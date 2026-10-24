#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBox 源自动归集 + 失效检测

流程:
  1. 读取 sources.txt (接口池: 手动贴的接口 + @订阅的别人配置)
  2. 归集去重 (只收苹果CMS JSON 型接口, 不依赖 jar 的那种)
  3. 逐个检测: 接口连通 -> 分类/数据有效 -> 随机抽一个视频验证播放地址可达
  4. 存活接口按响应速度排序, 生成 tvbox.json
  5. 结果写入 report.md; 失败计数写入 dead_log.json (连续 FAIL_LIMIT 次失败才剔除, 防误杀)
  6. --gitee 参数: 把结果同步到 Gitee 仓库 (需环境变量 GITEE_TOKEN / GITEE_REPO)

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


def http_get(url, timeout, max_bytes=None):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes) if max_bytes else r.read()
        return r.getcode(), data


def fetch_json(url, timeout):
    st, data = http_get(url, timeout)
    if st != 200:
        raise RuntimeError('HTTP %s' % st)
    return json.loads(data.decode('utf-8', 'ignore'))


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
    """归集接口池: 手动行 + @订阅配置里提取的接口, 去重"""
    pool = {}

    def add(api, name, origin):
        api = api.strip()
        if not api.startswith('http'):
            return
        key = re.sub(r'^https?://', '', api).lower()
        if key not in pool:
            pool[key] = {'api': api, 'name': name, 'origin': origin}

    if not os.path.exists(SOURCES):
        log('缺少 sources.txt, 接口池为空')
        return pool
    with open(SOURCES, encoding='utf-8-sig') as f:
        lines = [ln.strip() for ln in f]
    for line in lines:
        if not line or line.startswith('#'):
            continue
        if line.startswith('@'):
            sub = line[1:].strip()
            try:
                cfg = fetch_json(sub, 15)
            except Exception as e:
                log('[订阅失败] %s -> %s' % (sub, e))
                continue
            n = 0
            for s in (cfg.get('sites') or []):
                if s.get('type') != 3:
                    continue
                api = str(s.get('api') or '')
                if not api.startswith('http'):
                    continue
                add(api, str(s.get('name') or ''), '订阅')
                n += 1
            log('[订阅] %s 提取 %d 个接口' % (sub, n))
        else:
            if '|' in line:
                u, alias = line.rsplit('|', 1)
                add(u.strip(), alias.strip(), '手动')
            else:
                add(line, '', '手动')
    return pool


def write_report(items, alive, suspect, deadlist, now):
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
    if repo:
        raw = 'https://raw.githubusercontent.com/%s/main/tvbox.json' % repo
        lines += ['', '电视端配置地址 (gh-proxy 加速):', '', '```',
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
    for fp in ['tvbox.json', 'dead_log.json', 'report.md']:
        push_file_gitee(repo, fp, token)


def main():
    global SOURCES
    sources = SOURCES
    if '--sources' in sys.argv:
        sources = sys.argv[sys.argv.index('--sources') + 1]
    SOURCES = sources

    pool = collect()
    items = list(pool.values())
    log('归集接口共 %d 个, 开始检测(并发%d)...' % (len(items), MAX_WORKERS))
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

    write_report(items, alive, suspect, deadlist, now)
    log('report.md 已生成')

    if '--gitee' in sys.argv:
        sync_gitee()


if __name__ == '__main__':
    main()
