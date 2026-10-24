 #!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBox 源自动归集 + 失效检测 + 仓体检 + 高清直播生成

流程:
  1. sources.txt:
       @订阅行  = 别人的点播配置(进仓体检)
       手动行   = 苹果CMS直链接口(进接口检测)
       L|上游行 = 直播源m3u(进直播检测)
  2. 接口检测: 连通 -> 分类/数据有效 -> 抽一个视频验证播放地址可达
  3. 仓体检: 解析每个仓的全部站点, 逐个测依赖(jar/脚本/接口/网页)可达,
     算健康分; 低于 STORE_MIN_HEALTH 且连续 STORE_FAIL_LIMIT 轮的仓剔除,
     存活仓按分数排序写入 store.json (名称带分数)
  4. 直播检测: 按频道白名单(央视/卫视/中国香港/湖南)从上游m3u归集候选,
     逐条拉流实测分辨率, >=720p 才收; 每频道按画质排序保留前 LIVE_KEEP 条,
     生成 live.json / live.m3u, 并作为"我的直播"仓进 store.json
  5. 结果写入 report.md; 失败计数写入 dead_log.json (防误杀)
  6. --gitee 参数: 把结果同步到 Gitee 仓库 (需 GITEE_TOKEN / GITEE_REPO)

电视端入口(地址永远不变):
  多仓入口 store.json: 点播仓(体检排序) + 我的直播仓
  单仓入口 tvbox.json: 自检测直链接口 + 直播

用法:
  python check_sources.py [--sources 路径] [--gitee]
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, 'sources.txt')
TVBOX = os.path.join(HERE, 'tvbox.json')
STORE = os.path.join(HERE, 'store.json')
LIVE_JSON = os.path.join(HERE, 'live.json')
LIVE_M3U = os.path.join(HERE, 'live.m3u')
REPORT = os.path.join(HERE, 'report.md')
DEADLOG = os.path.join(HERE, 'dead_log.json')

TIMEOUT_API = 10      # 接口请求超时(秒)
TIMEOUT_PLAY = 8      # 播放地址抽测超时(秒)
TIMEOUT_DEP = 8       # 仓依赖检测超时(秒)
TIMEOUT_LIVE = 8      # 直播流拉取超时(秒)
TIMEOUT_PROBE = 12    # ffmpeg/ffprobe 画质探测超时(秒)
FAIL_LIMIT = 2        # 接口连续失败次数达到该值才剔除
STORE_MIN_HEALTH = 0.4   # 仓健康分低于该比例开始计失败
STORE_FAIL_LIMIT = 2     # 仓连续低于健康线轮数, 达到即从清单剔除
MAX_WORKERS = 12
FLAGS = ["qq", "iqiyi", "qiyi", "youku", "sohu", "letv", "mgtj", "bilibili"]
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/126.0.0.0 Safari/537.36'}

# ---- 直播参数 ----
LIVE_MAX_CAND = 8     # 每频道最多实测候选地址数
LIVE_KEEP = 3         # 每频道保留线路数
LIVE_MIN_W, LIVE_MIN_H = 1280, 720   # 高清底线: 720p
LIVE_EPG = 'http://epg.51zmt.top:8000/e.xml'

# ffmpeg 探测工具定位(云端apt安装; 本地尝试 imageio_ffmpeg)
FFPROBE_BIN = shutil.which('ffprobe')
FFMPEG_BIN = shutil.which('ffmpeg')
if not (FFPROBE_BIN or FFMPEG_BIN):
    try:
        import imageio_ffmpeg
        FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass


def log(m):
    print(m, flush=True)


def norm_url(u):
    """中文域名转 punycode、路径中文转百分号编码, 供 urllib 使用"""
    try:
        p = urllib.parse.urlsplit(u)
    except Exception:
        return u
    host = p.hostname or ''
    try:
        if host and not host.isascii():
            host = host.encode('idna').decode()
    except Exception:
        pass
    netloc = host
    if p.port:
        netloc = '%s:%d' % (host, p.port)
    try:
        path = urllib.parse.quote(p.path or '/', safe='/:')
        query = urllib.parse.quote(p.query, safe='=&?%+')
    except Exception:
        path, query = p.path, p.query
    return urllib.parse.urlunsplit((p.scheme, netloc, path, query, p.fragment))


def http_get(url, timeout, max_bytes=None, headers=None):
    """GET; raw.githubusercontent 失败时自动走 gh-proxy 镜像重试"""
    tries = [url]
    if re.match(r'^https?://raw\.githubusercontent\.com/', url):
        tries += ['https://gh-proxy.com/' + url, 'https://ghproxy.net/' + url]
    last = None
    for u in tries:
        try:
            req = urllib.request.Request(norm_url(u), headers=headers or UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(max_bytes) if max_bytes else r.read()
                return r.getcode(), data
        except Exception as e:
            last = e
    raise last


def fetch_json(url, timeout):
    st, data = http_get(url, timeout)
    if st != 200:
        raise RuntimeError('HTTP %s' % st)
    # 伪装成图片的配置: JPEG 尾部附加 "干扰头**base64(JSON)"
    if data[:2] == b'\xff\xd8':
        idx = data.rfind(b'\xff\xd9')
        if idx >= 0:
            tail = data[idx + 2:].decode('utf-8', 'ignore').strip()
            if '**' in tail:
                tail = tail.split('**')[-1]
            tail = re.sub(r'\s+', '', tail)
            try:
                data = base64.b64decode(tail + '=' * (-len(tail) % 4))
            except Exception:
                pass
    txt = data.decode('utf-8-sig', 'ignore')
    try:
        return json.loads(txt)
    except Exception:
        pass
    # TVBox圈常用宽松JSON: 带//或/* */注释、字符串内换行等, 剥离后再解析
    stripped = re.sub(r'^\s*//.*$', '', txt, flags=re.M)
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.S)
    stripped = stripped.replace('\r', '').replace('\n', ' ').replace('\t', ' ')
    return json.loads(stripped)


def tv_url(u):
    """电视端可达地址: raw.githubusercontent 需加 gh-proxy 前缀"""
    if u.startswith('https://raw.githubusercontent.com') or \
            u.startswith('http://raw.githubusercontent.com'):
        return 'https://gh-proxy.com/' + u
    return u


def join_api(base, params):
    sep = '&' if '?' in base else '?'
    return base + sep + params


def check_one(api):
    """检测单个接口, 返回总耗时毫秒; 失败抛异常(带原因)"""
    t0 = time.time()
    j = fetch_json(join_api(api, 'ac=list'), TIMEOUT_API)
    classes = j.get('class') or []
    vods = j.get('list') or []
    if not classes and not vods:
        raise RuntimeError('接口返回空数据')
    if not vods:
        j2 = fetch_json(join_api(api, 'ac=videolist&pg=1'), TIMEOUT_API)
        vods = j2.get('list') or []
        if not vods:
            raise RuntimeError('无可用视频数据')
    sample = vods[0]
    play = str(sample.get('vod_play_url') or '')
    m = re.search(r'\$(https?://[^#\s]+)', play)
    if not m:
        raise RuntimeError('未取到播放地址')
    purl = m.group(1)
    try:
        st, _ = http_get(purl, TIMEOUT_PLAY, max_bytes=2048)
        if st != 200:
            raise RuntimeError('播放地址 HTTP %s' % st)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError('播放地址不可达: %s' % e)
    return int((time.time() - t0) * 1000)


# ---------------- 仓体检 ----------------

def site_deps(cfg):
    """解析仓内每个站点的依赖URL. 返回 (全局jar, [(站点, 依赖URL集合)])"""
    global_jar = str(cfg.get('spider') or '')
    if global_jar.startswith('http'):
        global_jar = global_jar.split(';')[0]
    else:
        global_jar = ''
    per = []
    for s in (cfg.get('sites') or []):
        deps = set()
        api = str(s.get('api') or '')
        jar = str(s.get('jar') or '')
        ext = s.get('ext')
        if api.startswith('http'):
            deps.add(api.split(';')[0])
        if jar.startswith('http'):
            deps.add(jar.split(';')[0])
        if isinstance(ext, str) and ext.startswith('http'):
            deps.add(ext.split(';')[0])
        # csp_/相对路径/py脚本站点依赖全局jar
        if not api.startswith('http') and global_jar:
            deps.add(global_jar)
        per.append((s, deps))
    return global_jar, per


def test_dep(url):
    try:
        st, _ = http_get(url, TIMEOUT_DEP, max_bytes=2048)
        return st == 200
    except Exception:
        return False


def assess_stores(stores):
    """给每个仓算健康分"""
    all_deps = set()
    parsed = []
    for st in stores:
        cfg = st.get('cfg') or {}
        gjar, per = site_deps(cfg)
        parsed.append((st, gjar, per))
        for _, deps in per:
            all_deps.update(deps)
    log('仓体检: %d 个仓, 去重后依赖共 %d 个, 并发检测...' % (
        len(stores), len(all_deps)))
    dep_ok = {}
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        futs = {ex.submit(test_dep, d): d for d in all_deps}
        for fu in as_completed(futs):
            d = futs[fu]
            dep_ok[d] = fu.result()

    out = []
    for st, gjar, per in parsed:
        alive_cnt, total, unknown = 0, 0, 0
        for _, deps in per:
            if not deps:
                unknown += 1
                continue
            total += 1
            if all(dep_ok.get(d) for d in deps):
                alive_cnt += 1
        score = (alive_cnt / total) if total else None
        jar_ok = dep_ok.get(gjar) if gjar else None
        out.append(dict(st, score=score, alive=alive_cnt, total=total,
                        unknown=unknown, jar_ok=jar_ok))
        pct = ('%d%%' % (score * 100)) if score is not None else '未测'
        log('[仓体检] %-16s 分数%s (站点存活%d/%d, 未测%d)' % (
            (st.get('name') or '')[:16], pct, alive_cnt, total, unknown))
    return out


# ---------------- 直播: 频道白名单 ----------------

LIVE_GROUPS = ['央视', '卫视', '中国香港', '湖南']


def build_whitelist():
    wl = []

    def add(name, group, patterns):
        wl.append({'name': name, 'group': group,
                   'patterns': [re.compile(p, re.I) for p in patterns]})

    # 央视: 4K/5+ 先行, 避免被 CCTV4/CCTV5 误吞
    add('CCTV-4K 超高清', '央视', [r'cctv4k'])
    add('CCTV-5+ 体育赛事', '央视', [r'cctv5\+', r'cctv5＋', r'体育赛事'])
    cctv_names = {
        1: '综合', 2: '财经', 3: '综艺', 4: '中文国际', 5: '体育', 6: '电影',
        7: '国防军事', 8: '电视剧', 9: '纪录', 10: '科教', 11: '戏曲',
        12: '社会与法', 13: '新闻', 14: '少儿', 15: '音乐',
        16: '奥林匹克', 17: '农业农村',
    }
    for i in range(1, 18):
        add('CCTV-%d %s' % (i, cctv_names[i]), '央视',
            [r'cctv0?%d(?![\dk])' % i])

    sats = [
        ('湖南卫视', r'湖南卫视|hunantv'),
        ('东方卫视', r'东方卫视|dragontv|上海卫视'),
        ('浙江卫视', r'浙江卫视|zhejiangtv'),
        ('江苏卫视', r'江苏卫视|jiangsutv'),
        ('北京卫视', r'北京卫视|beijingtv|brtv(?![a-z])'),
        ('广东卫视', r'广东卫视|guangdongtv'),
        ('深圳卫视', r'深圳卫视|shenzhentv'),
        ('四川卫视', r'四川卫视|sichuantv'),
        ('重庆卫视', r'重庆卫视|chongqingtv'),
        ('湖北卫视', r'湖北卫视|hubeitv'),
        ('山东卫视', r'山东卫视|shandongtv'),
        ('河南卫视', r'河南卫视|henantv'),
        ('安徽卫视', r'安徽卫视|anhuitv'),
        ('江西卫视', r'江西卫视|jiangxitv'),
        ('贵州卫视', r'贵州卫视|guizhoutv'),
        ('云南卫视', r'云南卫视|yunnantv'),
        ('广西卫视', r'广西卫视|guangxitv'),
        ('辽宁卫视', r'辽宁卫视|liaoningtv'),
        ('黑龙江卫视', r'黑龙江卫视|heilongjiangtv'),
        ('吉林卫视', r'吉林卫视|jilintv'),
        ('天津卫视', r'天津卫视|tianjintv'),
        ('河北卫视', r'河北卫视|hebeitv'),
        ('山西卫视', r'山西卫视|shanxitv'),
        ('陕西卫视', r'陕西卫视|shaanxitv'),
        ('甘肃卫视', r'甘肃卫视|gansutv'),
        ('宁夏卫视', r'宁夏卫视|ningxiatv'),
        ('青海卫视', r'青海卫视|qinghaitv'),
        ('新疆卫视', r'新疆卫视|xinjiangtv'),
        ('内蒙古卫视', r'内蒙古卫视|neimenggutv|innermongolia'),
        ('西藏卫视', r'西藏卫视|xizangtv|tibet'),
        ('海南卫视', r'海南卫视|hainantv'),
        ('东南卫视', r'东南卫视|dongnantv'),
    ]
    for name, pat in sats:
        add(name, '卫视', [pat])

    hks = [
        ('翡翠台', r'翡翠台?|(?<![a-z])jade(?![a-z])'),
        ('明珠台', r'明珠台?|(?<![a-z])pearl(?![a-z])'),
        ('凤凰卫视中文台', r'凤凰卫视?中文|phoenixchinese'),
        ('凤凰卫视资讯台', r'凤凰资讯|phoenixinfonews'),
        ('HOY TV', r'hoytv|hoy(?![a-z])'),
        ('RTHK 港台电视31', r'rthk\w*31|港台(电视|電視)?31'),
    ]
    for name, pat in hks:
        add(name, '中国香港', [pat])

    huns = [
        ('金鹰纪实', r'金鹰纪实'),
        ('金鹰卡通', r'金鹰卡通|金鹰动漫'),
        ('湖南经视', r'湖南经视'),
        ('湖南都市', r'湖南都市'),
    ]
    for name, pat in huns:
        add(name, '湖南', [pat])
    return wl


def norm_ch_name(s):
    s = s.lower()
    s = s.replace('（', '(').replace('）', ')').replace('－', '-')
    s = re.sub(r'[\s\-_]+', '', s)   # 去空格/横杠/下划线, 保留+(CCTV5+)
    return s


def match_channel(raw_name, wl):
    n = norm_ch_name(raw_name)
    for e in wl:
        for p in e['patterns']:
            if p.search(n):
                return e
    return None


def res_hint(name):
    """从频道名里的画质标签推断分辨率: (w,h) 或 None"""
    m = re.search(r'\((\d{3,4})[pi]\)', name)
    if m:
        h = int(m.group(1))
        if h >= 2000:
            return (3840, 2160)
        return (int(h * 16 / 9), h)
    if re.search(r'4k|uhd|2160', name, re.I):
        return (3840, 2160)
    if re.search(r'蓝光|超高清|1080', name, re.I):
        return (1920, 1080)
    if re.search(r'超清', name):
        return (1920, 1080)
    if re.search(r'高清|720', name, re.I):
        return (1280, 720)
    return None


def parse_m3u(text):
    """解析m3u, 返回 [{name,ua,group,url}]"""
    items = []
    cur = None
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith('#EXTINF'):
            head, _, name = ln.partition(',')
            name = name.strip()
            ua = re.search(r'http-user-agent="([^"]+)"', head)
            grp = re.search(r'group-title="([^"]+)"', head)
            cur = {'name': name,
                   'ua': ua.group(1) if ua else None,
                   'group': grp.group(1) if grp else ''}
        elif ln.startswith('#'):
            continue
        elif cur is not None:
            cur['url'] = ln
            items.append(cur)
            cur = None
    return items


def ffprobe_res(url, ua=None):
    """用 ffprobe/ffmpeg 探测真实分辨率: (w,h) 或 (0,0)"""
    if FFPROBE_BIN:
        cmd = [FFPROBE_BIN, '-v', 'error', '-select_streams', 'v:0',
               '-show_entries', 'stream=width,height', '-of', 'csv=p=0', url]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=TIMEOUT_PROBE).stdout.strip()
            m = re.search(r'(\d{2,4})\s*,\s*(\d{2,4})', out)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return 0, 0
    if FFMPEG_BIN:
        cmd = [FFMPEG_BIN, '-hide_banner', '-i', url, '-t', '0',
               '-f', 'null', '-']
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=TIMEOUT_PROBE)
            best = (0, 0)
            for m in re.finditer(r'(\d{3,4})x(\d{3,4})', p.stderr):
                w, h = int(m.group(1)), int(m.group(2))
                if w * h > best[0] * best[1] and w <= 4096 and h <= 2160:
                    best = (w, h)
            return best
        except Exception:
            pass
    return 0, 0


def probe_live(url, ua=None):
    """直播线路体检: 返回 dict(ok,w,h,method,ms)"""
    t0 = time.time()
    headers = dict(UA)
    if ua:
        headers['User-Agent'] = ua
    try:
        st, data = http_get(url, TIMEOUT_LIVE, max_bytes=65536, headers=headers)
    except Exception:
        return {'ok': False, 'w': 0, 'h': 0, 'method': '', 'ms': 0}
    ms = int((time.time() - t0) * 1000)
    if st != 200:
        return {'ok': False, 'w': 0, 'h': 0, 'method': '', 'ms': ms}
    head = data[:200].decode('utf-8', 'ignore')
    if '#EXTM3U' in head:
        txt = data.decode('utf-8', 'ignore')
        if '#EXT-X-STREAM-INF' in txt:
            best = (0, 0)
            for m in re.finditer(r'RESOLUTION=(\d+)x(\d+)', txt):
                w, h = int(m.group(1)), int(m.group(2))
                if w * h > best[0] * best[1]:
                    best = (w, h)
            if best[0]:
                return {'ok': True, 'w': best[0], 'h': best[1],
                        'method': 'master', 'ms': ms}
        w, h = ffprobe_res(url, ua)
        if w:
            return {'ok': True, 'w': w, 'h': h, 'method': 'ffmpeg', 'ms': ms}
        return {'ok': True, 'w': 0, 'h': 0, 'method': '', 'ms': ms}
    # 非m3u8文本(可能是flv/ts/php转发的流)
    w, h = ffprobe_res(url, ua)
    if w:
        return {'ok': True, 'w': w, 'h': h, 'method': 'ffmpeg', 'ms': ms}
    return {'ok': True, 'w': 0, 'h': 0, 'method': '', 'ms': ms}


def collect_live(wl):
    """从上游m3u归集白名单频道并逐线实测.
    返回 (channels, upstream_stats)"""
    upstreams = []
    if os.path.exists(SOURCES):
        with open(SOURCES, encoding='utf-8-sig') as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith('L|'):
                    rest = ln[2:].strip()
                    alias = ''
                    if '|' in rest:
                        u, alias = rest.split('|', 1)
                        rest, alias = u.strip(), alias.strip()
                    upstreams.append((rest, alias))

    cands = {}   # 频道名 -> {'entry':wl, 'urls':{url:{'ua':..,'raw':..}}}
    up_stats = []
    for u, alias in upstreams:
        try:
            st, data = http_get(u, 25)
            txt = data.decode('utf-8-sig', 'ignore')
            items = parse_m3u(txt)
            matched = 0
            for it in items:
                e = match_channel(it['name'], wl)
                if not e:
                    continue
                matched += 1
                cands.setdefault(e['name'], {'entry': e, 'urls': {}})
                if it['url'] not in cands[e['name']]['urls']:
                    cands[e['name']]['urls'][it['url']] = {
                        'ua': it.get('ua'), 'raw': it['name']}
            up_stats.append({'url': u, 'name': alias, 'ok': True,
                             'total': len(items), 'matched': matched})
            log('[直播上游] %s %d条, 命中白名单 %d' % (
                alias or u, len(items), matched))
        except Exception as e:
            up_stats.append({'url': u, 'name': alias, 'ok': False,
                             'err': str(e)[:60]})
            log('[直播上游失败] %s -> %s' % (alias or u, str(e)[:60]))

    tasks = []
    for key, c in cands.items():
        urls = list(c['urls'].items())[:LIVE_MAX_CAND]
        for u, meta in urls:
            tasks.append((key, u, meta.get('ua'), meta.get('raw')))
    log('直播候选线路共 %d 条(白名单频道 %d 个), 并发实测...' % (
        len(tasks), len(cands)))
    probe_res = {}
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        futs = {ex.submit(probe_live, u, ua): (key, u, raw)
                for key, u, ua, raw in tasks}
        for fu in as_completed(futs):
            key, u, raw = futs[fu]
            probe_res[(key, u)] = (fu.result(), raw)

    channels = []
    for key, c in cands.items():
        passing = []
        for (k2, u), (r, raw) in probe_res.items():
            if k2 != key or not r['ok']:
                continue
            w, h, method = r['w'], r['h'], r['method']
            if not w:  # 实测不出 -> 用上游名字里的画质标签兜底
                hint = res_hint(raw or '')
                if hint and hint[1] >= LIVE_MIN_H:
                    w, h, method = hint[0], hint[1], 'hint'
                else:
                    continue
            if w >= LIVE_MIN_W and h >= LIVE_MIN_H:
                passing.append({'url': u, 'w': w, 'h': h,
                                'method': method, 'ms': r['ms']})
        if not passing:
            continue
        # 实测优先于名字标签; 再按分辨率降序、耗时升序
        passing.sort(key=lambda x: (x['method'] in ('master', 'ffmpeg'),
                                    x['w'] * x['h'], -x['ms']), reverse=True)
        channels.append({'name': key, 'group': c['entry']['group'],
                         'urls': passing[:LIVE_KEEP],
                         'n_cand': len(c['urls'])})

    order = {g: i for i, g in enumerate(LIVE_GROUPS)}
    channels.sort(key=lambda c: (order.get(c['group'], 9), c['name']))
    return channels, up_stats


def write_live(channels, repo):
    """生成 live.json(OSC风格) 与 live.m3u; 返回 lives 结构"""
    lives = []
    for g in LIVE_GROUPS:
        chans = [c for c in channels if c['group'] == g]
        if chans:
            lives.append({'group': g, 'channels': [
                {'name': c['name'], 'urls': [u['url'] for u in c['urls']]}
                for c in chans]})
    with open(LIVE_JSON, 'w', encoding='utf-8') as f:
        json.dump({'lives': lives}, f, ensure_ascii=False, indent=2)
    lines = ['#EXTM3U x-tvg-url="%s"' % LIVE_EPG]
    for g in LIVE_GROUPS:
        for c in [x for x in channels if x['group'] == g]:
            for u in c['urls']:
                lines.append('#EXTINF:-1 group-title="%s",%s' % (g, c['name']))
                lines.append(u['url'])
    with open(LIVE_M3U, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    log('live.json / live.m3u 已生成: %d 个频道, 共 %d 条线路' % (
        len(channels), sum(len(c['urls']) for c in channels)))
    return lives


# ---------------- 归集(点播) ----------------

def collect():
    """归集: 返回 (接口池pool, 仓列表stores含cfg)"""
    pool = {}
    stores = []
    seen_store = set()
    SUB_LIMIT = 25

    def add(api, name, origin):
        api = api.strip()
        if not api.startswith('http'):
            return
        key = re.sub(r'^https?://', '', api).lower()
        if key not in pool:
            pool[key] = {'api': api, 'name': name, 'origin': origin}

    def add_store(name, url, cfg):
        if url in seen_store:
            return
        seen_store.add(url)
        stores.append({'name': name or urllib.parse.urlparse(url).netloc,
                       'url': url, 'cfg': cfg})

    def extract_apis(cfg, origin):
        n = 0
        for s in (cfg.get('sites') or []):
            if s.get('type') != 3:
                continue
            api = str(s.get('api') or '')
            if api.startswith('http') and not api.split('?')[0].rstrip('/').endswith('.js'):
                add(api, str(s.get('name') or ''), origin)
                n += 1
        return n

    def sub_entries(cfg):
        out = []
        for sh in (cfg.get('storeHouse') or []):
            u = str((sh or {}).get('sourceUrl') or '')
            n = str((sh or {}).get('sourceName') or '')
            if u.startswith('http'):
                out.append((n, u))
        for u in (cfg.get('urls') or []):
            if isinstance(u, dict):
                uu = str(u.get('url') or '')
                nn = str(u.get('name') or '')
                if uu.startswith('http'):
                    out.append((nn, uu))
            elif isinstance(u, str) and u.startswith('http'):
                out.append(('', u))
        return out

    if not os.path.exists(SOURCES):
        log('缺少 sources.txt')
        return pool, stores
    with open(SOURCES, encoding='utf-8-sig') as f:
        lines = [ln.strip() for ln in f]
    for line in lines:
        if not line or line.startswith('#') or line.startswith('L|'):
            continue
        if line.startswith('@'):
            rest = line[1:].strip()
            alias = ''
            if '|' in rest:
                parts = rest.split('|', 1)
                rest, alias = parts[0].strip(), parts[1].strip()
            try:
                cfg = fetch_json(rest, 15)
            except Exception as e:
                log('[订阅失败] %s -> %s' % (rest, e))
                continue
            add_store(alias, rest, cfg)
            n = extract_apis(cfg, '订阅')
            subs = sub_entries(cfg)[:SUB_LIMIT]
            nsub = 0
            for nm, s2 in subs:
                try:
                    cfg2 = fetch_json(s2, 12)
                except Exception as e:
                    log('[聚合跳过] %s -> %s' % (s2, str(e)[:60]))
                    continue
                add_store(nm, s2, cfg2)
                nsub += extract_apis(cfg2, '聚合')
            log('[订阅] %s 直提%d接口, 跟进%d子仓(接口+%d)' % (
                rest, n, len(subs), nsub))
        else:
            if '|' in line:
                u, alias = line.rsplit('|', 1)
                add(u.strip(), alias.strip(), '手动')
            else:
                add(line, '', '手动')
    return pool, stores


def load_deadlog():
    if not os.path.exists(DEADLOG):
        return {}, {}
    try:
        with open(DEADLOG, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}, {}
    if 'apis' in data or 'stores' in data:
        return data.get('apis') or {}, data.get('stores') or {}
    return data, {}


def save_deadlog(apis, stores):
    with open(DEADLOG, 'w', encoding='utf-8') as f:
        json.dump({'apis': apis, 'stores': stores}, f,
                  ensure_ascii=False, indent=1)


def write_store(scored, alive_count, repo, live_ok):
    """生成多仓清单 store.json: 我的直播 + 按健康分排序的点播仓"""
    entries = []
    if repo and live_ok:
        own = 'https://raw.githubusercontent.com/%s/main/live.json' % repo
        entries.append({'sourceName': '我的直播',
                        'sourceUrl': tv_url(own)})
    if repo and alive_count > 0:
        own = 'https://raw.githubusercontent.com/%s/main/tvbox.json' % repo
        entries.append({'sourceName': '自动优选·直链检测',
                        'sourceUrl': tv_url(own)})
    for s in scored:
        name = (s.get('name') or '未命名')[:16]
        if s.get('score') is not None:
            name = '%s %d分' % (name, round(s['score'] * 100))
        entries.append({'sourceName': name, 'sourceUrl': tv_url(s['url'])})
    cfg = {'storeHouse': entries}
    with open(STORE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log('store.json 已生成: %d 个仓(含直播仓)' % len(entries))


def write_report(items, alive, suspect, deadlist, scored, channels,
                 up_stats, now):
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    lines = ['# TVBox 源检测报告', '', '检测时间: %s (北京时间)' % now, '']
    lines += ['## 一、直链接口检测 (tvbox.json)', '',
              '| 状态 | 名称 | 接口 | 延迟 | 说明 |', '|---|---|---|---|---|']

    def row(tag, it, ms, note):
        name = (it.get('name') or '未命名')[:24]
        lines.append('| %s | %s | `%s` | %s | %s |' % (
            tag, name, it['api'], ('%dms' % ms) if ms else '-', note))

    for it in alive:
        row('存活', it, it['ms'], '')
    for it in suspect:
        row('疑似', it, None, '连续失败 %d 次: %s' % (it['fails'], it['reason']))
    for it in deadlist:
        row('剔除', it, None, '连续失败 %d 次: %s' % (it['fails'], it['reason']))
    lines.append('')
    lines.append('接口归集 %d | 存活 %d | 疑似 %d | 剔除 %d' % (
        len(items), len(alive), len(suspect), len(deadlist)))

    lines += ['', '## 二、仓体检 (store.json, 按健康分排序)', '',
              '健康分 = 仓内站点依赖(jar/脚本/接口/网页)可达占比;'
              '低于 %d%% 连续 %d 轮的仓剔除出清单' % (
                  STORE_MIN_HEALTH * 100, STORE_FAIL_LIMIT), '',
              '| 状态 | 仓 | 分数 | 站点存活 | jar | 说明 |',
              '|---|---|---|---|---|---|']
    for s in scored:
        st = s.get('state') or ''
        score = s.get('score')
        pct = ('%d%%' % (score * 100)) if score is not None else '未测'
        site = '%d/%d' % (s.get('alive', 0), s.get('total', 0))
        if s.get('unknown'):
            site += ' (+%d未测)' % s['unknown']
        jar = s.get('jar_ok')
        jar_txt = '—' if jar is None else ('正常' if jar else '失效')
        lines.append('| %s | %s | %s | %s | %s | %s |' % (
            st, (s.get('name') or '未命名')[:18], pct, site, jar_txt,
            s.get('note') or ''))
    kept = [s for s in scored if s.get('state') == '保留']
    lines.append('')
    lines.append('仓总数 %d | 保留 %d | 剔除 %d' % (
        len(scored), len(kept), len(scored) - len(kept)))

    lines += ['', '## 三、高清直播 (live.json, 频道白名单)', '',
              '收录标准: 实测分辨率 >= 720p(高清); 每频道保留画质最好的前%d条线路'
              % LIVE_KEEP, '',
              '| 频道 | 分组 | 线路数 | 最佳画质 |', '|---|---|---|---|']
    for c in channels:
        best = max(c['urls'], key=lambda u: u['w'] * u['h'])
        res = '%dp' % best['h'] if best['h'] < 2000 else '4K'
        lines.append('| %s | %s | %d | %s |' % (
            c['name'], c['group'], len(c['urls']), res))
    lines.append('')
    lines.append('直播频道 %d 个 / 线路 %d 条; 上游: %s' % (
        len(channels), sum(len(c['urls']) for c in channels),
        '、'.join('%s%s' % (u['name'] or u['url'],
                            '✓' if u['ok'] else '✗') for u in up_stats)))
    lines.append('')
    lines.append('EPG 节目单: `%s`' % LIVE_EPG)

    if repo:
        raw = 'https://raw.githubusercontent.com/%s/main/tvbox.json' % repo
        raws = 'https://raw.githubusercontent.com/%s/main/store.json' % repo
        lines += ['', '电视端多仓入口 (gh-proxy 加速, 推荐):', '', '```',
                  'https://gh-proxy.com/' + raws, '```',
                  '', '电视端单仓入口 (直链+直播):', '', '```',
                  'https://gh-proxy.com/' + raw, '```']
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def push_file_gitee(repo, filepath, token):
    path = '/repos/%s/contents/%s' % (repo, urllib.parse.quote(filepath, safe=''))
    with open(os.path.join(HERE, filepath), 'rb') as f:
        content = base64.b64encode(f.read()).decode()
    sha = None
    try:
        st, data = http_get('https://gitee.com/api/v5%s?access_token=%s' % (path, token), 15)
        if st == 200:
            sha = json.loads(data.decode('utf-8', 'ignore')).get('sha')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log('[Gitee] 查询失败 %s: HTTP %s' % (filepath, e.code))
            return
    except Exception as e:
        log('[Gitee] 查询失败 %s: %s' % (filepath, e))
        return
    payload = {'access_token': token, 'content': content,
               'message': 'auto update %s' % filepath}
    if sha:
        payload['sha'] = sha
    req = urllib.request.Request(
        'https://gitee.com/api/v5' + path,
        data=urllib.parse.urlencode(payload).encode(),
        method='PUT' if sha else 'POST', headers=UA)
    try:
        with urllib.request.urlopen(req, 15) as r:
            log('[Gitee] 已同步 %s (HTTP %s)' % (filepath, r.getcode()))
    except Exception as e:
        log('[Gitee] 同步失败 %s: %s' % (filepath, e))


def sync_gitee():
    token = os.environ.get('GITEE_TOKEN', '')
    repo = os.environ.get('GITEE_REPO', '')
    if not token or not repo:
        log('未配置 GITEE_TOKEN / GITEE_REPO, 跳过 Gitee 同步')
        return
    for fp in ['tvbox.json', 'store.json', 'live.json', 'live.m3u',
               'dead_log.json', 'report.md']:
        push_file_gitee(repo, fp, token)


def main():
    global SOURCES
    sources = SOURCES
    if '--sources' in sys.argv:
        sources = sys.argv[sys.argv.index('--sources') + 1]
    SOURCES = sources

    # ---- 点播归集 + 检测 ----
    pool, stores = collect()
    items = list(pool.values())
    log('归集接口共 %d 个, 仓 %d 个' % (len(items), len(stores)))

    results = {}
    if items:
        log('开始接口检测(并发%d)...' % MAX_WORKERS)
        with ThreadPoolExecutor(MAX_WORKERS) as ex:
            futs = {ex.submit(check_one, it['api']): it for it in items}
            for fu in as_completed(futs):
                it = futs[fu]
                try:
                    ms = fu.result()
                    results[it['api']] = {'ok': True, 'ms': ms}
                    log('[存活] %5dms  %s' % (ms, it['api']))
                except Exception as e:
                    results[it['api']] = {'ok': False, 'err': str(e)[:80]}
                    log('[失败] %s -> %s' % (it['api'], e))

    scored = []
    if stores:
        scored = assess_stores(stores)

    # ---- 直播归集 + 实测 ----
    wl = build_whitelist()
    channels, up_stats = collect_live(wl)
    lives = write_live(channels, os.environ.get('GITHUB_REPOSITORY', ''))

    # ---- 失败计数(防误杀) ----
    dead_apis, dead_stores = load_deadlog()
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime('%Y-%m-%d %H:%M')

    alive, suspect, deadlist = [], [], []
    for it in items:
        api = it['api']
        r = results.get(api, {'ok': False, 'err': '未检测'})
        if r['ok']:
            dead_apis[api] = {'fails': 0, 'last_ok': now}
            alive.append(dict(it, ms=r['ms']))
        else:
            fails = int(dead_apis.get(api, {}).get('fails', 0)) + 1
            dead_apis[api] = {'fails': fails, 'last_fail': now, 'reason': r['err']}
            rec = dict(it, fails=fails, reason=r['err'])
            if fails >= FAIL_LIMIT:
                deadlist.append(rec)
            else:
                suspect.append(rec)

    for s in scored:
        url = s['url']
        score = s.get('score')
        if score is None:
            s['state'] = '保留'
            s['note'] = '依赖不可直测, 保留观察'
            dead_stores.pop(url, None)
            continue
        if score >= STORE_MIN_HEALTH:
            s['state'] = '保留'
            dead_stores.pop(url, None)
        else:
            fails = int(dead_stores.get(url, {}).get('fails', 0)) + 1
            dead_stores[url] = {'fails': fails, 'last_fail': now,
                                'score': round(score, 2)}
            if fails >= STORE_FAIL_LIMIT:
                s['state'] = '剔除'
                s['note'] = '健康分 %d%%, 连续 %d 轮低于 %d%%' % (
                    score * 100, fails, STORE_MIN_HEALTH * 100)
            else:
                s['state'] = '保留'
                s['note'] = '健康分 %d%% 偏低, 观察(第%d轮)' % (
                    score * 100, fails)
    save_deadlog(dead_apis, dead_stores)

    # ---- 生成配置 ----
    alive.sort(key=lambda x: x['ms'])
    sites = []
    for i, it in enumerate(alive, 1):
        name = it['name'] or urllib.parse.urlparse(it['api']).netloc
        sites.append({'key': 'site%d' % i, 'name': name, 'type': 3,
                      'api': it['api'], 'searchable': 1,
                      'quickSearch': 1, 'filterable': 1})
    cfg = {'wallpaper': '', 'sites': sites, 'flags': FLAGS, 'ads': [],
           'lives': lives}
    with open(TVBOX, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log('tvbox.json 已生成: %d 个存活接口, %d 组直播' % (
        len(sites), len(lives)))

    kept = [s for s in scored if s.get('state') != '剔除']
    kept.sort(key=lambda s: s['score'] if s.get('score') is not None else 0.6,
              reverse=True)
    write_store(kept, len(sites), os.environ.get('GITHUB_REPOSITORY', ''),
                bool(channels))
    write_report(items, alive, suspect, deadlist, scored, channels,
                 up_stats, now)
    log('report.md 已生成')

    if '--gitee' in sys.argv:
        sync_gitee()


if __name__ == '__main__':
    main()

