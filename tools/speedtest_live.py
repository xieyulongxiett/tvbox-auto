#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""家里网络实测直播线路速度 —— 生成"你家专属"的线路排序。

为什么要有这个脚本
------------------
线上那份 live.txt 的线路顺序, 是 GitHub Actions 那台**境外机器**排出来的:
它对美国 VPS 测出 200ms 觉得很快, 连广东联通 IPTV 连不上就判死链。而你家的
网络恰好完全相反 —— 于是"排在第一条的, 恰好是你访问最慢的那批源", 表现就是
"每个台都卡"。

check_sources.py 里已经加了静态亲和度兜底(国内 CDN 优先、移动源优先、境外
VPS 沉底), 但静态特征只能猜。**只有在你家网络下真跑一遍, 才知道哪条线真快**。
这个脚本就是干这个的。

怎么用
------
    python tools/speedtest_live.py                    # 自动拉线上最新 live_cand.txt
    python tools/speedtest_live.py D:/live.txt        # 测本地文件
    python tools/speedtest_live.py -w 16              # 提高并发(网快时用)

v18 注意: 默认拉的是 **live_cand.txt**(候选池), 不是 live.txt。
因为 live.txt 从 v18 起只保留家里实测 >=300kbps 的台(那天算下来只剩 13 个台),
测它等于只测已知的那几条线, 永远不会发现新台。候选池覆盖全部频道, 每台最多
6 条(2 条已知够用的复查 + 4 条没测过的找新线)。

产出(在当前目录)
    speed_rank.json    每条线路实测速度 {url: kbps}, 带 updated 时间戳
    live_sorted.txt    按你这里速度重排的 live.txt
    speed_report.md    逐台表格, 一眼看出哪些台够用、哪些台没救

跑完之后
--------
把 speed_rank.json 覆盖到仓库根目录(网页 Upload files 即可):
下一轮 Actions 只保留其中 >=300kbps 的线路, 电视那边刷新配置即可生效。
超过 14 天不更新, 仓库那边会当作"没测过"并自动退回不过滤(安全阀)。

判定口径
--------
720p 直播流畅约需 1.5Mbps(1500kbps)。分档:
    >=1500  流畅      >=300  能看(可能偶尔缓冲)
    >0      卡         =0    你这边连不上(境外/跨网/已失效)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/126.0.0.0 Safari/537.36'}
REMOTE = ('https://raw.githubusercontent.com/xieyulongxiett/'
          'tvbox-auto/main/live_cand.txt')
MIRRORS = ('https://gh-proxy.com/https://raw.githubusercontent.com/'
           'xieyulongxiett/tvbox-auto/main/live_cand.txt',
           'https://ghfast.top/https://raw.githubusercontent.com/'
           'xieyulongxiett/tvbox-auto/main/live_cand.txt',
           'https://raw.githubusercontent.com/xieyulongxiett/'
           'tvbox-auto/main/live.txt')
TIMEOUT = 8            # 单条线路最长等待(秒)
MAX_BYTES = 1200000    # 单条最多下载字节(够测速即可, 1.2MB)
MAX_TEXT = 400000      # 索引/播放列表最多读多少字节(v18 新增, 见 http_text)
MIN_OK = 300           # kbps, 低于此值判"会卡"
SMOOTH = 1500          # kbps, 高于此值判"流畅"


def http_text(url, timeout=15, max_bytes=MAX_TEXT):
    """拉文本(播放列表/索引)。

    v18: 加了字节与时长上限。老版本 `r.read()` 一把梭 —— 碰到 .flv/.ts/无后缀
    直链时会把**整个视频文件**当文本读完再解码。2026-10-28 那次实测的账:
    160 条用了 104 秒, 跑完 173 条却用了 1817 秒(最后 13 条耗了 28 分钟),
    根因就在这一行。"""
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    buf, got = [], 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while got < max_bytes:
            if time.time() - t0 > timeout:
                break
            b = r.read(65536)
            if not b:
                break
            buf.append(b)
            got += len(b)
    return b''.join(buf).decode('utf-8', 'ignore')


def download(url, timeout=TIMEOUT, max_bytes=MAX_BYTES):
    """下载一段, 返回 (字节数, 耗时秒)。超过 timeout 或 max_bytes 就停。"""
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    got = 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while got < max_bytes:
            if time.time() - t0 > timeout:
                break
            b = r.read(65536)
            if not b:
                break
            got += len(b)
    return got, max(0.001, time.time() - t0)


def split_m3u(text, base):
    """取出 m3u8 里的所有 URI 行(相对路径拼成绝对)。"""
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#'):
            continue
        if ln.startswith('http'):
            out.append(ln)
        else:
            out.append(urllib.parse.urljoin(base, ln))
    return out


def get_segments(url, depth=0):
    """取到真正的分片地址列表。master playlist 会自动下钻一层。"""
    txt = http_text(url, 12)
    if '#EXT-X-STREAM-INF' in txt and depth < 2:
        vs = split_m3u(txt, url)
        if not vs:
            return []
        return get_segments(vs[0], depth + 1)
    return split_m3u(txt, url)


def is_hls(url):
    """v18: 是不是 HLS 播放列表。flv/ts/mp4/无后缀直链不是 —— 以前一律当 m3u8
    解析(把二进制当文本切行), 结果全判 0 分, 10-28 那批 173 条里有 28 条被冤枉。"""
    low = url.lower().split('?')[0].split('#')[0]
    return low.endswith('.m3u8') or low.endswith('.m3u')


def measure(url):
    """测一条线路, 返回 (kbps, 备注)。"""
    # v18: 非 HLS 直链直接按字节流测速(下载一小段算码率), 不再当播放列表解析
    if not is_hls(url):
        try:
            got, el = download(url)
            if got < 2000:
                return 0.0, '样本过小(%dB)' % got
            return round(got * 8 / 1000.0 / el, 1), 'ok(直链)'
        except urllib.error.HTTPError as e:
            return 0.0, 'HTTP %s' % e.code
        except Exception as e:
            return 0.0, type(e).__name__
    try:
        segs = get_segments(url)
    except Exception as e:
        return 0.0, '索引失败 %s' % type(e).__name__
    if not segs:
        return 0.0, '无分片'
    # 直播取最新一片(末尾), 前面的可能已被滑窗清掉; 取不到再退回第一片
    cands = [segs[-1]]
    if len(segs) > 1:
        cands.append(segs[0])
    last = ''
    for s in cands:
        try:
            got, el = download(s)
            if got < 2000:
                last = '分片过小(%dB)' % got
                continue
            return round(got * 8 / 1000.0 / el, 1), 'ok'
        except urllib.error.HTTPError as e:
            last = 'HTTP %s' % e.code
        except Exception as e:
            last = type(e).__name__
    return 0.0, last or '失败'


def load_live(path):
    """读 live.txt, 返回 [(分组, 台名, [线路...])]。"""
    rows = []
    group = ''
    for ln in open(path, encoding='utf-8-sig'):
        ln = ln.strip()
        if not ln:
            continue
        # 分组行形如 "央视,#genre#" —— 它也含逗号, 不先剔掉就会被当成一个
        # 名叫"央视"、线路叫"genre"的频道(本机实测踩到过: 台数变成 62 而不是 58,
        # 报告里"播不了的台"第一个就是"央视")
        if ln.startswith('#') or '#genre#' in ln or ',' not in ln:
            # 分组行形如 "央视,#genre#": 取逗号前的部分。直接 replace('#genre#','')
            # 会把逗号留下, 写回时变成 "央视,,#genre#"(本机实测踩到过)
            group = ln.split(',')[0].replace('#', '').strip()
            continue
        name, urls = ln.split(',', 1)
        us = [u for u in urls.split('#') if u.strip()]
        if not us:
            continue
        rows.append((group, name.strip(), us))
    return rows


def fetch_remote(outdir):
    for u in (REMOTE,) + MIRRORS:
        try:
            print('拉取线上候选池 live_cand.txt: %s' % u)
            txt = http_text(u, 25)
            p = os.path.join(outdir, 'live_remote.txt')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(txt)
            return p
        except Exception as e:
            print('  失败(%s), 换下一个源' % type(e).__name__)
    return None


def grade(kbps):
    if kbps >= SMOOTH:
        return '流畅'
    if kbps >= MIN_OK:
        return '能看'
    if kbps > 0:
        return '卡'
    return '不通'


def main():
    ap = argparse.ArgumentParser(description='家里网络实测直播线路速度')
    ap.add_argument('live', nargs='?', help='live.txt 路径; 不给则自动拉线上最新')
    ap.add_argument('-o', '--out', default='.', help='输出目录')
    ap.add_argument('-w', '--workers', type=int, default=12, help='并发数')
    args = ap.parse_args()

    # 输出目录必须先建好: 拉线上 live.txt 时就要往这里落盘, 晚一步就是
    # FileNotFoundError(本机实测踩到过 —— 报错看起来像网络问题, 其实是目录不存在)
    outdir = args.out
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir, exist_ok=True)

    src = args.live
    if not src:
        src = fetch_remote(args.out)
        if not src:
            print('线上 live.txt 拉不下来。可以手动下载后把路径传给本脚本。')
            return 1
    if not os.path.exists(src):
        print('找不到文件: %s' % src)
        return 1

    rows = load_live(src)
    jobs = [(g, n, u) for g, n, us in rows for u in us]
    print('共 %d 个台 / %d 条线路, 并发 %d, 预计 1~3 分钟'
          % (len(rows), len(jobs), args.workers))
    print('测的是**你当前这台机器的网络** —— 要在家里宽带下跑才有意义。\n')

    res = {}
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(measure, u): u for _, _, u in jobs}
        for fu in as_completed(futs):
            u = futs[fu]
            try:
                res[u] = fu.result()
            except Exception as e:
                res[u] = (0.0, type(e).__name__)
            done += 1
            if done % 20 == 0 or done == len(jobs):
                print('  进度 %d/%d  (%.0fs)'
                      % (done, len(jobs), time.time() - t0))

    # 按速度重排每个台的线路
    out_rows = []
    stat = {'流畅': 0, '能看': 0, '卡': 0, '不通': 0}
    bad = []
    for g, n, us in rows:
        scored = sorted(us, key=lambda u: -res.get(u, (0.0, ''))[0])
        best = res.get(scored[0], (0.0, ''))[0] if scored else 0
        stat[grade(best)] += 1
        if best < MIN_OK:
            bad.append((n, best))
        out_rows.append((g, n, scored, best))

    # ① live_sorted.txt
    p1 = os.path.join(outdir, 'live_sorted.txt')
    with open(p1, 'w', encoding='utf-8') as f:
        cur = None
        for g, n, us, _ in out_rows:
            if g != cur:
                if cur is not None:
                    f.write('\n')
                f.write('%s,#genre#\n' % g)
                cur = g
            f.write('%s,%s\n' % (n, '#'.join(us)))
    # ② speed_rank.json
    p2 = os.path.join(outdir, 'speed_rank.json')
    # v18: 带时间戳。check_sources.py 读它会看 updated, 超过 14 天就当没测过 ——
    # 300 硬门槛下, 过期数据比没有更危险: 会把已经死掉的老线当成"实测够快"
    # 钉在清单第一位, 电视上就是那个台打不开。
    now_s = time.strftime('%Y-%m-%d %H:%M', time.localtime())
    with open(p2, 'w', encoding='utf-8') as f:
        json.dump({'updated': now_s,
                   'note': '家里实测: url -> kbps; check_sources.py 只收 >=%d 的线'
                           % MIN_OK,
                   'rank': {u: res[u][0] for u in res}},
                  f, ensure_ascii=False, indent=0)
    # ③ speed_report.md
    p3 = os.path.join(outdir, 'speed_report.md')
    with open(p3, 'w', encoding='utf-8') as f:
        f.write('# 家里实测直播线路速度\n\n')
        f.write('- 流畅(>=%dkbps) **%d** 台 / 能看(%d~%d) **%d** 台 / '
                '卡(< %d) **%d** 台 / 全不通 **%d** 台\n\n'
                % (SMOOTH, stat['流畅'], MIN_OK, SMOOTH, stat['能看'],
                   MIN_OK, stat['卡'], stat['不通']))
        f.write('| 频道 | 第一条 | 档位 | 三条线实测(kbps) |\n|---|---|---|---|\n')
        for g, n, us, best in out_rows:
            f.write('| %s | %.0f | %s | %s |\n'
                    % (n, best, grade(best),
                       ' / '.join('%.0f' % res.get(u, (0.0, ''))[0]
                                  for u in us)))
        if bad:
            f.write('\n## 三条线都不够用的台(%d)\n\n' % len(bad))
            for n, b in sorted(bad, key=lambda x: x[1]):
                f.write('- %s (最快 %.0f kbps)\n' % (n, b))

    print('\n=== 结果 ===')
    print('流畅 %d 台 / 能看 %d 台 / 卡 %d 台 / 全不通 %d 台'
          % (stat['流畅'], stat['能看'], stat['卡'], stat['不通']))
    print('已写出: %s' % p1)
    print('         %s' % p2)
    print('         %s' % p3)
    print('\n下一步: 把 speed_rank.json 覆盖到仓库根目录(网页 Upload files 即可),')
    print('下一轮 Actions 只保留其中 >=%dkbps 的线路, 电视刷新即可生效。' % MIN_OK)
    print('(超过 14 天不更新, 仓库那边会当作没测过, 自动退回"不过滤"的安全阀)')
    if bad:
        print('\n这些台三条线都不够用(%d 个): %s'
              % (len(bad), '、'.join(n for n, _ in bad[:12])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
