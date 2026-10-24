#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBox 源自动归集 + 失效检测 + 仓体检 + 多仓生成

流程:
  1. 读取 sources.txt (@订阅行 = 别人的配置; 手动行 = 苹果CMS接口)
  2. 归集去重: 提取配置里的苹果CMS JSON直链接口; 多仓配置自动跟进一层子仓
  3. 接口检测: 连通 -> 分类/数据有效 -> 抽一个视频验证播放地址可达
  4. 仓体检: 解析每个仓的全部站点, 逐个测依赖(jar/脚本/接口/网页)可达,
     算健康分; 低于 STORE_MIN_HEALTH 且连续 STORE_FAIL_LIMIT 轮的仓剔除,
     存活仓按分数排序写入 store.json (名称带分数)
  5. 结果写入 report.md; 失败计数写入 dead_log.json (防误杀)
  6. --gitee 参数: 把结果同步到 Gitee 仓库 (需 GITEE_TOKEN / GITEE_REPO)

电视端两个入口:
  多仓入口 store.json (推荐): 按健康分排序的仓清单, 一键换仓
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
TIMEOUT_DEP = 8       # 仓依赖检测超时(秒)
FAIL_LIMIT = 2        # 接口连续失败次数达到该值才剔除
STORE_MIN_HEALTH = 0.4   # 仓健康分低于该比例开始计失败
STORE_FAIL_LIMIT = 2     # 仓连续低于健康线轮数, 达到即从清单剔除
MAX_WORKERS = 12
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


# ---------------- 仓体检 ----------------

def site_deps(cfg):
    """解析仓内每个站点的依赖URL.
    返回 (全局jar, [(站点, 依赖URL集合), ...])"""
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
    """单个依赖可达性: HTTP 200 即活"""
    try:
        st, _ = http_get(url, TIMEOUT_DEP, max_bytes=2048)
        return st == 200
    except Exception:
        return False


def assess_stores(stores):
    """给每个仓算健康分. stores元素含 name/url/cfg.
    返回带分数的列表, 每项: name,url,score,alive,total,unknown,jar_ok"""
    # 1. 汇总所有仓的全部依赖URL, 去重后并发测一遍
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


# ---------------- 归集 ----------------

def collect():
    """归集: 返回 (接口池pool, 仓列表stores含cfg).
    @订阅行: 提取苹果CMS直链接口入池; 配置本身与其子仓记入 stores.
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
    """兼容旧版(纯接口dict)与新版({"apis":..,"stores":..})"""
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


def write_store(scored, alive_count, repo):
    """生成多仓清单 store.json: 按健康分排序, 名称带分数"""
    entries = []
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
    log('store.json 已生成: %d 个仓(已按体检分排序)' % len(entries))


def write_report(items, alive, suspect, deadlist, scored, now):
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
        note = s.get('note') or ''
        lines.append('| %s | %s | %s | %s | %s | %s |' % (
            st, (s.get('name') or '未命名')[:18], pct, site, jar_txt, note))
    kept = [s for s in scored if s.get('state') == '保留']
    lines.append('')
    lines.append('仓总数 %d | 保留 %d | 剔除 %d' % (
        len(scored), len(kept), len(scored) - len(kept)))

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

    # ---- 归集 ----
    pool, stores = collect()
    items = list(pool.values())
    log('归集接口共 %d 个, 仓 %d 个' % (len(items), len(stores)))

    # ---- 接口检测 ----
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

    # ---- 仓体检 ----
    scored = []
    if stores:
        scored = assess_stores(stores)

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
    cfg = {'wallpaper': '', 'sites': sites, 'flags': FLAGS, 'ads': []}
    with open(TVBOX, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log('tvbox.json 已生成: %d 个存活接口' % len(sites))

    kept = [s for s in scored if s.get('state') != '剔除']
    # 排序: 有分数的按分数降序; 未测的排中间
    kept.sort(key=lambda s: s['score'] if s.get('score') is not None else 0.6,
              reverse=True)
    write_store(kept, len(sites), os.environ.get('GITHUB_REPOSITORY', ''))
    write_report(items, alive, suspect, deadlist, scored, now)
    log('report.md 已生成')

    if '--gitee' in sys.argv:
        sync_gitee()


if __name__ == '__main__':
    main()
