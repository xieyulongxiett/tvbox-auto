#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBox 源自动归集 + 失效检测 + 多仓生成

流程:
  1. 读取 sources.txt (@订阅行 = 别人的配置; 手动行 = 苹果CMS接口)
  2. 归集去重: 提取配置里的苹果CMS JSON直链接口; 多仓配置自动跟进一层子仓
  3. 逐个检测接口: 连通 -> 分类/数据有效 -> 抽一个视频验证播放地址可达
  4. 存活接口按速度排序生成 tvbox.json; 订阅配置及子仓存活的形成 store.json 多仓清单
  5. 结果写入 report.md; 失败计数写入 dead_log.json (连续 FAIL_LIMIT 次失败才剔除, 防误杀)
  6. --gitee 参数: 把结果同步到 Gitee 仓库 (需环境变量 GITEE_TOKEN / GITEE_REPO)

电视端两个入口:
  多仓入口 store.json (推荐): TVBox/影视仓加载后可一键切换各仓
  单仓入口 tvbox.json: 只含自检测的苹果CMS直链接口

用法:
  python check_sources.py [--sources 路径] [--gitee]
"""
import base64
import json
import os
import re
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
REPORT = os.path.join(HERE, 'report.md')
DEADLOG = os.path.join(HERE, 'dead_log.json')

TIMEOUT_API = 10      # 接口请求超时(秒)
TIMEOUT_PLAY = 8      # 播放地址抽测超时(秒)
FAIL_LIMIT = 2        # 连续失败次数达到该值才从配置剔除
MAX_WORKERS = 8
FLAGS = ["qq", "iqiyi", "qiyi", "youku", "sohu", "letv", "mgtj", "bilibili"]
UA = {'User-Agent': 'Mozilla/5.0'}


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


def http_get(url, timeout, max_bytes=None):
    url = norm_url(url)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes) if max_bytes else r.read()
        return r.getcode(), data


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
    # 第1层: 连通 + 分类/列表数据有效
    j = fetch_json(join_api(api, 'ac=list'), TIMEOUT_API)
    classes = j.get('class') or []
    vods = j.get('list') or []
    if not classes and not vods:
        raise RuntimeError('接口返回空数据')
    # 第2层: 拿一个真实视频样本
    if not vods:
        j2 = fetch_json(join_api(api, 'ac=videolist&pg=1'), TIMEOUT_API)
        vods = j2.get('list') or []
        if not vods:
            raise RuntimeError('无可用视频数据')
    sample = vods[0]
    # 第3层: 播放地址抽测
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


def collect():
    """归集: 返回 (接口池pool, 多仓清单stores).
    @订阅行: 提取苹果CMS直链接口入池; 配置本身与其子仓(存活)记入 stores.
    多仓配置(storeHouse/urls)自动跟进一层子仓."""
    pool = {}
    stores = []
    seen_store = set()
    SUB_LIMIT = 25  # 每个订阅最多跟进的子仓数量

    def add(api, name, origin):
        api = api.strip()
        if not api.startswith('http'):
            return
        key = re.sub(r'^https?://', '', api).lower()
        if key not in pool:
            pool[key] = {'api': api, 'name': name, 'origin': origin}

    def add_store(name, url):
        if url in seen_store:
            return
        seen_store.add(url)
        stores.append({'name': name or urllib.parse.urlparse(url).netloc,
                       'url': url})

    def extract_apis(cfg, origin):
        n = 0
        for s in (cfg.get('sites') or []):
            if s.get('type') != 3:
                continue
            api = str(s.get('api') or '')
            # 只要苹果CMS型接口: http开头且不是drpy js脚本
            if api.startswith('http') and not api.split('?')[0].rstrip('/').endswith('.js'):
                add(api, str(s.get('name') or ''), origin)
                n += 1
        return n

    def sub_entries(cfg):
        out = []  # [(名称, 地址)]
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
        log('缺少 sources.txt, 接口池为空')
        return pool, stores
    with open(SOURCES, encoding='utf-8-sig') as f:
        lines = [ln.strip() for ln in f]
    for line in lines:
        if not line or line.startswith('#'):
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
            add_store(alias, rest)
            n = extract_apis(cfg, '订阅')
            subs = sub_entries(cfg)[:SUB_LIMIT]
            nsub = 0
            for nm, s2 in subs:
                try:
                    cfg2 = fetch_json(s2, 12)
                except Exception as e:
                    log('[聚合跳过] %s -> %s' % (s2, str(e)[:60]))
                    continue
                add_store(nm, s2)
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


def write_store(stores, alive_count, repo):
    """生成多仓清单 store.json; 电视端入口, 一键切换各仓"""
    entries = []
    if repo and alive_count > 0:
        own = 'https://raw.githubusercontent.com/%s/main/tvbox.json' % repo
        entries.append({'sourceName': '自动优选·直链检测',
                        'sourceUrl': tv_url(own)})
    for s in stores:
        entries.append({'sourceName': (s['name'] or '未命名')[:20],
                        'sourceUrl': tv_url(s['url'])})
    cfg = {'storeHouse': entries}
    with open(STORE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log('store.json 已生成: %d 个仓' % len(entries))


def write_report(items, alive, suspect, deadlist, stores, now):
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    lines = ['# TVBox 源检测报告', '', '检测时间: %s (北京时间)' % now, '',
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
    lines.append('归集总数 %d | 存活 %d | 疑似 %d | 剔除 %d' % (
        len(items), len(alive), len(suspect), len(deadlist)))
    if stores:
        lines += ['', '## 多仓清单 (store.json, 电视端一键换仓)', '',
                  '| 仓 | 地址 |', '|---|---|']
        for s in stores:
            lines.append('| %s | `%s` |' % (
                (s['name'] or '未命名')[:20], s['url']))
        lines += ['', '共 %d 个仓' % len(stores)]
    if repo:
        raw = 'https://raw.githubusercontent.com/%s/main/tvbox.json' % repo
        raws = 'https://raw.githubusercontent.com/%s/main/store.json' % repo
        lines += ['', '电视端多仓入口 (gh-proxy 加速, 推荐):', '', '```',
                  'https://gh-proxy.com/' + raws, '```',
                  '', '电视端单仓入口 (仅自检测直链):', '', '```',
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
    for fp in ['tvbox.json', 'store.json', 'dead_log.json', 'report.md']:
        push_file_gitee(repo, fp, token)


def main():
    global SOURCES
    sources = SOURCES
    if '--sources' in sys.argv:
        sources = sys.argv[sys.argv.index('--sources') + 1]
    SOURCES = sources

    pool, stores = collect()
    items = list(pool.values())
    log('归集接口共 %d 个, 仓 %d 个, 开始检测(并发%d)...' % (
        len(items), len(stores), MAX_WORKERS))
    results = {}
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

    dead = {}
    if os.path.exists(DEADLOG):
        try:
            with open(DEADLOG, encoding='utf-8') as f:
                dead = json.load(f)
        except Exception:
            dead = {}

    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime('%Y-%m-%d %H:%M')
    alive, suspect, deadlist = [], [], []
    for it in items:
        api = it['api']
        r = results.get(api, {'ok': False, 'err': '未检测'})
        if r['ok']:
            dead[api] = {'fails': 0, 'last_ok': now}
            alive.append(dict(it, ms=r['ms']))
        else:
            fails = int(dead.get(api, {}).get('fails', 0)) + 1
            dead[api] = {'fails': fails, 'last_fail': now, 'reason': r['err']}
            rec = dict(it, fails=fails, reason=r['err'])
            if fails >= FAIL_LIMIT:
                deadlist.append(rec)
            else:
                suspect.append(rec)
    with open(DEADLOG, 'w', encoding='utf-8') as f:
        json.dump(dead, f, ensure_ascii=False, indent=1)

    alive.sort(key=lambda x: x['ms'])
    sites = []
    for i, it in enumerate(alive, 1):
        name = it['name'] or urllib.parse.urlparse(it['api']).netloc
        sites.append({'key': 'site%d' % i, 'name': name, 'type': 3,
                      'api': it['api'], 'searchable': 1,
                      'quickSearch': 1, 'filterable': 1})
    cfg = {'wallpaper': '', 'sites': sites, 'flags': FLAGS, 'ads': []}
    with open(TVBOX, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log('tvbox.json 已生成: %d 个存活接口' % len(sites))

    write_store(stores, len(sites), os.environ.get('GITHUB_REPOSITORY', ''))
    write_report(items, alive, suspect, deadlist, stores, now)
    log('report.md 已生成')

    if '--gitee' in sys.argv:
        sync_gitee()


if __name__ == '__main__':
    main()
