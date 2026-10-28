#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_sources.py 核心纯函数回归测试(不联网, 本地 1 秒内跑完)

为什么要有这个文件:
  以前改一个正则, 唯一的验证方式是"推到 GitHub 跑 10 分钟看 Actions 日志",
  而且看的是最终产物 —— 中间哪一步判错完全看不出来. 下面的函数全都不碰网络,
  改完立刻验证.

跑法:
  python tests/test_core.py        # 不依赖 pytest
  pytest -q tests/                 # 装了 pytest 也可以
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location(
        'check_sources', os.path.join(ROOT, 'check_sources.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cs = _load()


# ---------------- 频道匹配 ----------------

def test_match_channel_basic():
    wl = cs.build_whitelist()
    cases = [
        ('CCTV-1 综合', 'CCTV-1 综合'),
        ('CCTV1', 'CCTV-1 综合'),
        ('CCTV-11 戏曲', 'CCTV-11 戏曲'),
        ('CCTV-5 体育', 'CCTV-5 体育'),
        ('CCTV5+ 体育赛事', 'CCTV-5+ 体育赛事'),
        ('CCTV-4K 超高清', 'CCTV-4K 超高清'),
        ('CCTV-17 农业农村', 'CCTV-17 农业农村'),
        ('湖南卫视', '湖南卫视'),
        ('湖南经视', '湖南经视'),
        ('东方卫视', '东方卫视'),
    ]
    for raw, want in cases:
        got = cs.match_channel(raw, wl)
        assert got and got['name'] == want, '%s -> %s' % (
            raw, got and got['name'])


def test_phoenix_info_matches():
    """v6 修复: '凤凰卫视资讯台' 归一化后中间有'卫视', 旧正则匹配不上"""
    wl = cs.build_whitelist()
    for raw in ('凤凰卫视资讯台', '凤凰资讯台', 'Phoenix Info News'):
        got = cs.match_channel(raw, wl)
        assert got and got['name'] == '凤凰卫视资讯台', raw
    got = cs.match_channel('凤凰卫视中文台', wl)
    assert got and got['name'] == '凤凰卫视中文台'
    # 粤语/英语台刻意不收
    assert cs.match_channel('翡翠台', wl) is None
    assert cs.match_channel('明珠台', wl) is None


def test_cctv_no_cross_match():
    """CCTV-4 不能被 CCTV-4K 的条目吞掉, CCTV-5 不能被 CCTV-5+ 吞掉"""
    wl = cs.build_whitelist()
    assert cs.match_channel('CCTV-4 中文国际', wl)['name'] == 'CCTV-4 中文国际'
    assert cs.match_channel('CCTV-5 体育', wl)['name'] == 'CCTV-5 体育'


# ---------------- 广告判定 ----------------

def test_ad_keywords_word_boundary():
    """v6 修复: 无词边界的 ad[s]? 会命中 load/download/broadcast/shadow"""
    ok = ['https://c.com/hls/load/seg1.ts', 'https://c.com/vod/download/1.ts',
          'https://c.com/broadcast/2.ts', 'https://c.com/shadow/3.ts',
          'https://c.com/head/x.ts', 'https://c.com/vod/2024/0001.ts']
    bad = ['https://c.com/hls/ad/1.ts', 'https://c.com/advert_01.ts',
           'https://c.com/x-ad-2.ts', 'https://c.com/tuijian/1.ts',
           'https://c.com/promo/x.ts']
    for u in ok:
        assert not cs.AD_KEYWORDS.search(u), '误报: ' + u
    for u in bad:
        assert cs.AD_KEYWORDS.search(u), '漏报: ' + u


def test_live_index_ads():
    head = '#EXTM3U\n'
    cases = [
        # 正常切片
        ('均匀6s', '#EXTINF:6.0,\na.ts\n#EXTINF:6.0,\nb.ts\n#EXTINF:6.0,\nc.ts\n', False),
        # 均匀慢切片(部分地方台) —— 旧版一律拒收
        ('均匀20s', '#EXTINF:20.0,\na.ts\n#EXTINF:20.0,\nb.ts\n#EXTINF:20.0,\nc.ts\n', False),
        # 单次 DISCONTINUITY = 编码器重连/节目切换
        ('单次重连', '#EXTINF:6.0,\na.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:6.0,\nb.ts\n#EXTINF:6.0,\nc.ts\n', False),
        # 多处拼接 = 插广告强信号
        ('两次拼接', '#EXTINF:6.0,\na.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:6.0,\nb.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:6.0,\nc.ts\n', True),
        # 起播贴片: 首片超长
        ('起播贴片', '#EXTINF:20.0,\nad.ts\n#EXTINF:3.0,\na.ts\n#EXTINF:3.0,\nb.ts\n', True),
        # 中插长段
        ('中插长段', '#EXTINF:6.0,\na.ts\n#EXTINF:6.0,\nb.ts\n#EXTINF:18.0,\nx.ts\n#EXTINF:6.0,\nc.ts\n', True),
    ]
    for name, body, want in cases:
        got = cs.live_index_ads(head + body)
        assert got == want, '%s: got %s want %s' % (name, got, want)


def test_is_ad_url():
    bad = ['http://x/a.m3u8?livekey=abc', 'http://x/streamid=1.m3u8',
           'http://x/a?key=txiptv', 'http://x/bfgd/a.m3u8',
           'http://x/tsfile/live/a.m3u8', 'http://x/a?authid=9']
    ok = ['http://asp.miguvideo.com/x.m3u8', 'http://hebtv.cn/live/a.m3u8',
          'http://live.264788.xyz/a.m3u8?live=1']
    for u in bad:
        assert cs.is_ad_url(u), '应拒收: ' + u
    for u in ok:
        assert not cs.is_ad_url(u), '应放行: ' + u


def test_ts_domain_spread():
    def idx(urls):
        return '#EXTM3U\n' + ''.join(
            '#EXTINF:6.0,\n%s\n' % u for u in urls)
    same = idx(['http://cdn.a.com/%d.ts' % i for i in range(6)])
    spread, ratio = cs.ts_domain_spread(same)
    assert not spread and ratio == 1.0
    mixed = idx(['http://cdn.a.com/%d.ts' % i for i in range(3)] +
                ['http://ad.b.com/%d.ts' % i for i in range(3)])
    spread, ratio = cs.ts_domain_spread(mixed)
    assert spread and abs(ratio - 0.5) < 1e-9
    # 片段太少时不做判断
    assert cs.ts_domain_spread(idx(['http://a.com/1.ts']))[0] is False


def test_live_has_ads_uses_spread():
    """v7: live_has_ads 与 vod_has_ads 共用 ts_domain_spread"""
    def idx(urls):
        return '#EXTM3U\n' + ''.join(
            '#EXTINF:6.0,\n%s\n' % u for u in urls)
    assert cs.live_has_ads(idx(['http://a.com/%d.ts' % i for i in range(6)])) is False
    assert cs.live_has_ads(idx(['http://a.com/%d.ts' % i for i in range(3)] +
                               ['http://b.com/%d.ts' % i for i in range(3)])) is True
    assert cs.live_has_ads('#EXTM3U\n') is False


def test_split_alias():
    assert cs.split_alias('http://a/x.json | 我的仓') == ('http://a/x.json', '我的仓')
    assert cs.split_alias('http://a/x.json') == ('http://a/x.json', '')
    assert cs.split_alias('  http://a/x.json |  ') == ('http://a/x.json', '')
    assert cs.split_alias(None) == ('', '')
    assert cs.split_alias('http://a/x|y.json|备注') == ('http://a/x', 'y.json|备注')


# ---------------- m3u 解析 ----------------

def test_parse_m3u_headers():
    text = ('#EXTM3U\n'
            '#EXTINF:-1 tvg-name="CCTV-1",CCTV-1 综合\n'
            '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (iPhone)\n'
            'http://a.com/1.m3u8\n'
            '#EXTINF:-1,CCTV-2 财经\n'
            '#EXTVLCOPT:http-referer=http://a.com/\n'
            '#EXTVLCOPT:http-user-agent=Roku\n'
            'http://a.com/2.m3u8\n'
            '#EXTINF:-1,CCTV-3 综艺\n'
            '#KODIPROP:inputstream.adaptive.manifest_type=hls\n'
            '#KODIPROP:http-user-agent=okhttp/3.12\n'
            'http://a.com/3.m3u8\n')
    its = cs.parse_m3u(text)
    assert len(its) == 3, len(its)
    assert its[0]['ua'] == 'Mozilla/5.0 (iPhone)'
    assert its[0].get('ref') is None
    assert its[1]['ua'] == 'Roku'
    assert its[1]['ref'] == 'http://a.com/'
    assert its[2]['ua'] == 'okhttp/3.12'
    assert [it['url'] for it in its] == [
        'http://a.com/1.m3u8', 'http://a.com/2.m3u8', 'http://a.com/3.m3u8']


# ---------------- 候选取样 ----------------

def test_rr_pick_no_starvation():
    """v6 修复: 排第一的上游(旧死线多)不能吃满名额"""
    ud = {}
    for i in range(20):
        ud['http://guovin/%d' % i] = {'src': 0}
    for i in range(4):
        ud['http://vbskycn/%d' % i] = {'src': 1}
    for i in range(3):
        ud['http://fanmingming/%d' % i] = {'src': 2}
    picked = [u for u, _ in cs.rr_pick(ud, cs.LIVE_MAX_CAND)]
    assert len(picked) == cs.LIVE_MAX_CAND
    assert sum(1 for u in picked if '/vbskycn/' in u) == 4
    assert sum(1 for u in picked if '/fanmingming/' in u) == 3
    assert sum(1 for u in picked if '/guovin/' in u) == 9
    # 少于名额时全给
    assert len(cs.rr_pick({'http://a/1': {'src': 0}}, 16)) == 1


def test_rr_pick_handles_missing_src():
    ud = {'http://a/1': {}, 'http://b/2': {'src': None}}
    out = cs.rr_pick(ud, 5)
    assert len(out) == 2


# ---------------- 分数账 ----------------

def test_penalties_keep_store_alive():
    """惩罚分必须小于门槛余量, 否则'降分'就是'剔除'"""
    room = 1.0 - cs.STORE_MIN_HEALTH
    assert cs.VOD_QUALITY_PENALTY < room, '画质降分会把好仓打下门槛'
    assert cs.REC_QUALITY_PENALTY < room, '推荐降分会把好仓打下门槛'
    assert 1.0 - cs.VOD_QUALITY_PENALTY >= cs.STORE_MIN_HEALTH
    assert 1.0 - cs.REC_QUALITY_PENALTY >= cs.STORE_MIN_HEALTH


def test_flags():
    assert 'mgtv' in cs.FLAGS      # 芒果
    assert 'mgtj' not in cs.FLAGS  # 旧版拼错


# ---------------- 结构防御(v7 的核心) ----------------

def test_as_helpers():
    assert cs.as_list(None) == []
    assert cs.as_list('x') == ['x']
    assert cs.as_list({'a': 1, 'b': 2}) == [1, 2]
    assert cs.as_list([1, 2]) == [1, 2]
    assert cs.as_dict('x') == {}
    assert cs.as_dict(None) == {}
    assert cs.as_dict({'a': 1}) == {'a': 1}
    assert cs.as_str(None) == ''
    assert cs.as_str(555) == '555'
    assert cs.as_str('a') == 'a'


def test_site_deps_survives_garbage():
    """v7: 这些畸形结构以前会让整轮归集 AttributeError 崩掉"""
    garbage = [
        {'sites': {'a': 1}},                                  # sites 是 dict
        {'sites': ['http://x/a']},                            # 元素是字符串
        {'spider': 123, 'sites': []},                         # spider 是数字
        {'storeHouse': ['http://x']},                         # storeHouse 元素是字符串
        {'sites': [{'type': 3, 'api': 'http://x/api.php', 'ext': 42}]},
        {'sites': [{'api': None, 'jar': None, 'type': None}]},
        {},                                                   # 空配置
    ]
    for cfg in garbage:
        gjar, per = cs.site_deps(cfg, 'http://b/c.json')
        assert isinstance(per, list)
        assert isinstance(gjar, str)
    # sites 是 dict 时不该把键名当站点
    assert cs.site_deps({'sites': {'a': 1}}) == ('', [])


def test_site_deps_type_as_string():
    """type 写成 "3"(字符串)也要认成 CMS 接口"""
    _, per = cs.site_deps({'sites': [{'type': '3', 'api': 'http://x/api.php'}]})
    assert per and per[0]['cms'] == 'http://x/api.php'
    _, per = cs.site_deps({'sites': [{'type': 3, 'api': 'http://x/a.js'}]})
    assert per and per[0]['cms'] is None and 'http://x/a.js' in per[0]['deps']


def test_store_kind():
    assert cs.store_kind({'sites': [{'api': 'http://x'}]}) == 'store'
    assert cs.store_kind({'storeHouse': [{'sourceUrl': 'http://x'}]}) == 'nav'
    assert cs.store_kind({'urls': ['http://x']}) == 'nav'
    # 既没站点也没子仓: 按真仓处理, 交给打分判死(旧版会当导航仓保留)
    assert cs.store_kind({}) == 'store'


def test_first_play_url():
    assert cs.first_play_url({'vod_play_url':
                              '第1集$http://a/1.m3u8#第2集$http://a/2.m3u8'}) \
        == 'http://a/1.m3u8'
    assert cs.first_play_url({'vod_play_url': '第1集$http://a/1.mp4#x$http://a/2.m3u8'},
                             m3u8_only=True) == 'http://a/2.m3u8'
    assert cs.first_play_url({}) is None
    assert cs.first_play_url({'vod_play_url': '没有地址'}) is None
    assert cs.first_play_url({'vod_play_url': 123}) is None


def test_norm_store_url():
    cases = [
        ('https://gh-proxy.com/https://raw.githubusercontent.com/a/b/main/c.json',
         'raw.githubusercontent.com/a/b/main/c.json'),
        ('https://ghproxy.net/https://raw.githubusercontent.com/a/b/main/c.json',
         'raw.githubusercontent.com/a/b/main/c.json'),
        ('https://raw.githubusercontent.com/a/b/main/c.json?t=1',
         'raw.githubusercontent.com/a/b/main/c.json'),
        ('https://raw.githubusercontent.com/a/b/main/c.json/',
         'raw.githubusercontent.com/a/b/main/c.json'),
        ('HTTPS://Example.COM/a/b/', 'example.com/a/b'),
        (None, ''),
    ]
    for raw, want in cases:
        got = cs.norm_store_url(raw)
        assert got == want, '%r -> %r want %r' % (raw, got, want)


def test_store_fingerprint():
    a = {'sites': [{'api': 'http://x/1.php'}, {'api': 'http://x/2.php'}]}
    b = {'sites': [{'api': 'http://x/2.php'}, {'api': 'http://x/1.php'}]}
    assert cs.store_fingerprint(a) == cs.store_fingerprint(b)
    assert cs.store_fingerprint({'sites': []}) is None
    assert cs.store_fingerprint({'sites': {'a': 1}}) is None
    c = {'sites': [{'api': 'http://x/3.php'}]}
    assert cs.store_fingerprint(c) != cs.store_fingerprint(a)


def test_load_deadlog_survives_garbage():
    tmp = tempfile.mkdtemp()
    old = cs.DEADLOG
    try:
        cs.DEADLOG = os.path.join(tmp, 'dead_log.json')
        for content in ('[]', '"x"', 'null', '{bad json', ''):
            with open(cs.DEADLOG, 'w', encoding='utf-8') as f:
                f.write(content)
            a, s = cs.load_deadlog()
            assert a == {} and s == {}, content
        with open(cs.DEADLOG, 'w', encoding='utf-8') as f:
            json.dump({'apis': {'http://x': {'fails': 1}}, 'stores': {}}, f)
        a, s = cs.load_deadlog()
        assert 'http://x' in a
    finally:
        cs.DEADLOG = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- 产物生成(写临时目录) ----------------

def _channels():
    return [
        {'name': 'CCTV-1 综合', 'group': '央视', 'n_hd': 2, 'n_backup': 0,
         'urls': [{'url': 'http://a/1a.m3u8', 'w': 1920, 'h': 1080,
                   'method': 'ffmpeg', 'ms': 800},
                  {'url': 'http://a/1b.m3u8', 'w': 1280, 'h': 720,
                   'method': 'ffmpeg', 'ms': 1500}]},
        {'name': '凤凰卫视资讯台', 'group': '中国香港', 'n_hd': 1, 'n_backup': 0,
         'urls': [{'url': 'http://b/p.m3u8', 'w': 1280, 'h': 720,
                   'method': 'ffmpeg', 'ms': 2000}]},
    ]


def test_write_live():
    """v12: 主力源 live.txt(一台一行/多线路 # 分隔); m3u 退回每台 1 条
    这是"电视上一台显示成3个"的根治点 —— m3u 写几条就是几个台。"""
    tmp = tempfile.mkdtemp()
    olds = (cs.LIVE_JSON, cs.LIVE_M3U, cs.LIVE_TXT)
    try:
        cs.LIVE_JSON = os.path.join(tmp, 'live.json')
        cs.LIVE_M3U = os.path.join(tmp, 'live.m3u')
        cs.LIVE_TXT = os.path.join(tmp, 'live.txt')
        lives = cs.write_live(_channels())
        assert [g['group'] for g in lives] == ['央视', '中国香港']
        assert lives[0]['channels'][0]['urls'][0] == 'http://a/1a.m3u8'

        # ---- live.txt: 电视上真正在用那份 ----
        txt = open(cs.LIVE_TXT, encoding='utf-8').read()
        assert '央视,#genre#' in txt, txt
        assert '中国香港,#genre#' in txt, txt
        # 一台一行, 主线路在前, 备线用 # 串在同一行
        assert 'CCTV-1 综合,http://a/1a.m3u8#http://a/1b.m3u8' in txt, txt
        assert '凤凰卫视资讯台,http://b/p.m3u8' in txt, txt
        # 核心: 每个台名只出现一次
        assert txt.count('CCTV-1 综合') == 1, txt
        assert txt.count('凤凰卫视资讯台') == 1, txt

        # ---- live.m3u: 每台 1 条(写多了就是一个台变三个) ----
        m3u = open(cs.LIVE_M3U, encoding='utf-8').read()
        assert m3u.count('#EXTINF') == 2, m3u
        assert m3u.count('CCTV-1 综合') == 1, m3u
        assert m3u.count('凤凰卫视资讯台') == 1, m3u
        assert 'http://a/1a.m3u8' in m3u          # 主线路保留
        assert 'http://a/1b.m3u8' not in m3u      # 备线不进 m3u, 交给 txt
        assert len([l for l in m3u.splitlines() if l.startswith('http')]) == 2

        # ---- live.json: 备线全量保留(给认这个格式的播放器) ----
        j = json.load(open(cs.LIVE_JSON, encoding='utf-8'))
        assert j['lives'][0]['channels'][0]['urls'] == [
            'http://a/1a.m3u8', 'http://a/1b.m3u8']
    finally:
        cs.LIVE_JSON, cs.LIVE_M3U, cs.LIVE_TXT = olds
        shutil.rmtree(tmp, ignore_errors=True)


def test_live_txt_escape():
    """v12: 台名里的逗号会把 txt 行切错, 建库时必须兜住
    (上游池随时可能冒出 "CCTV-5 体育,高清" 这种名字)"""
    tmp = tempfile.mkdtemp()
    olds = (cs.LIVE_JSON, cs.LIVE_M3U, cs.LIVE_TXT)
    try:
        cs.LIVE_JSON = os.path.join(tmp, 'live.json')
        cs.LIVE_M3U = os.path.join(tmp, 'live.m3u')
        cs.LIVE_TXT = os.path.join(tmp, 'live.txt')
        cs.write_live([
            {'name': 'CCTV-5,体育', 'group': '央视', 'n_hd': 1,
             'urls': [{'url': 'http://a/x.m3u8', 'w': 1280, 'h': 720,
                       'method': 'ffmpeg', 'ms': 100}]},
        ])
        txt = open(cs.LIVE_TXT, encoding='utf-8').read()
        # 逗号被替换成空格, 一行仍然是 "台名,地址" 两段
        assert 'CCTV-5 体育,http://a/x.m3u8' in txt, txt
        line = [l for l in txt.splitlines() if l.startswith('CCTV')][0]
        assert line.count(',') == 1, line
    finally:
        cs.LIVE_JSON, cs.LIVE_M3U, cs.LIVE_TXT = olds
        shutil.rmtree(tmp, ignore_errors=True)


def _scored():
    """v12: 每条要有通道画像字段(gh_rate/sites_n/jar_rate) ——
    write_store 现在按"GitHub依赖升序 > 分数 > jar率 > 站点数"排序,
    并且 sites_n<=0 或 gh_rate>STORE_GH_MAX 的一律不收。
    没这些字段的仓会被当成空仓淘汰, 所以测试数据必须带齐。"""
    return [
        {'url': 'http://s0/nav.json', 'name': '多仓导航', 'kind': 'nav',
         'score': None, 'state': '保留', 'alive': 0, 'total': 0,
         'gh_rate': 0.0, 'sites_n': 0, 'jar_rate': 0.0},
        {'url': 'http://s1/a.json', 'name': '完美仓A', 'kind': 'store',
         'score': 1.0, 'state': '保留', 'alive': 10, 'total': 10,
         'gh_rate': 0.0, 'sites_n': 46, 'jar_rate': 0.93},
        {'url': 'http://s2/b.json', 'name': '降分仓B', 'kind': 'store',
         'score': 0.94, 'state': '保留', 'alive': 9, 'total': 9,
         'gh_rate': 0.0, 'sites_n': 9, 'jar_rate': 0.5},
        {'url': 'http://s5/e.json', 'name': '完美仓E', 'kind': 'store',
         'score': 1.0, 'state': '保留', 'alive': 8, 'total': 8,
         'gh_rate': 0.0, 'sites_n': 8, 'jar_rate': 0.6},
        {'url': 'http://s3/c.json', 'name': '补位仓C', 'kind': 'store',
         'score': 0.92, 'state': '保留', 'alive': 11, 'total': 12,
         'gh_rate': 0.0, 'sites_n': 12, 'jar_rate': 0.4},
        {'url': 'http://s4/d.json', 'name': '死仓D', 'kind': 'store',
         'score': 0.5, 'state': '剔除', 'alive': 5, 'total': 10,
         'gh_rate': 0.0, 'sites_n': 10, 'jar_rate': 0.3},
        # v12 核心: 298 站全活的"高分仓", 但 99% 子资源在 GitHub 上
        # -> 电视上点进去就是无限转圈, 必须被拒
        {'url': 'http://s6/f.json', 'name': '转圈仓F', 'kind': 'store',
         'score': 1.0, 'state': '保留', 'alive': 298, 'total': 298,
         'gh_rate': 0.99, 'sites_n': 298, 'jar_rate': 0.24},
    ]


def test_write_store_selection():
    """v12: 清单 = 我的直播 + 最多3个"点进去真能用"的仓
    与 v10 的差别: 我的影视只在有站点时挂; 通道太差的仓一票否决"""
    tmp = tempfile.mkdtemp()
    old = cs.STORE
    try:
        cs.STORE = os.path.join(tmp, 'store.json')
        kept = [s for s in _scored() if s['state'] != '剔除']
        # mine_sites 默认 0 -> 不挂"我的影视"(否则电视上是个红点)
        cs.write_store(kept, 'owner/repo', True, [])
        cfg = json.load(open(cs.STORE, encoding='utf-8'))
        # v13: urls 格式(GYCK.json 同款), storeHouse 在影视仓上选不开
        assert 'urls' in cfg and 'storeHouse' not in cfg, list(cfg.keys())
        names = [e['name'] for e in cfg['urls']]
        assert names[0] == '我的直播', names
        assert '我的影视' not in names, 'tvbox.json 无站点时不该挂(红点废源)'
        # 三个直连仓按 分数 > 站点数 排进去
        assert '完美仓A 100分' in names, names
        assert '完美仓E 100分' in names, names
        assert '降分仓B 94分' in names, names
        # 第4个直连仓被 STORE_TOP_N=3 截断
        assert '补位仓C 92分' not in names, names
        assert '死仓D 50分' not in names, names
        assert all('多仓导航' not in n for n in names)
        # v12 核心断言: 99% GitHub 依赖的仓再高分也不能进
        assert all('转圈仓F' not in n for n in names), names
        assert sum(1 for n in names if '分' in n) == 3, names
        # 电视端地址必须带代理前缀(裸 raw 在盒子上拉不动)
        assert cfg['urls'][0]['url'].startswith(cs.GH_PROXY)
    finally:
        cs.STORE = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_store_rejects_gh_heavy():
    """v12: 只有 GH 依赖超标的仓时, 走放宽兜底(不能输出空清单)"""
    tmp = tempfile.mkdtemp()
    old = cs.STORE
    try:
        cs.STORE = os.path.join(tmp, 'store.json')
        bad = [s for s in _scored() if s['name'] == '转圈仓F']
        cs.write_store(bad, 'owner/repo', True, [])
        cfg = json.load(open(cs.STORE, encoding='utf-8'))
        names = [e['name'] for e in cfg['urls']]
        # 放宽时也要挂上(空清单等于点播全废), 但名字里带 G99 标记
        assert any('转圈仓F' in n for n in names), names
        assert any('G99' in n for n in names), names
    finally:
        cs.STORE = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_store_mine_only_when_sites():
    """v12: tvbox.json 有站点才挂"我的影视" —— 空壳挂上去就是红点"""
    tmp = tempfile.mkdtemp()
    old = cs.STORE
    try:
        cs.STORE = os.path.join(tmp, 'store.json')
        kept = [s for s in _scored() if s['state'] != '剔除']
        cs.write_store(kept, 'owner/repo', True, None, mine_sites=12)
        names = [e['name'] for e in
                 json.load(open(cs.STORE, encoding='utf-8'))['urls']]
        assert '我的影视' in names, names
        cs.write_store(kept, 'owner/repo', True, None, mine_sites=0)
        names = [e['name'] for e in
                 json.load(open(cs.STORE, encoding='utf-8'))['urls']]
        assert '我的影视' not in names, names
    finally:
        cs.STORE = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_store_without_repo():
    """本地跑(没有 GITHUB_REPOSITORY)时不该产出坏地址"""
    tmp = tempfile.mkdtemp()
    old = cs.STORE
    try:
        cs.STORE = os.path.join(tmp, 'store.json')
        cs.write_store(_scored(), '', False, [('清理版', 'multi/m1.json', 1.0)])
        cfg = json.load(open(cs.STORE, encoding='utf-8'))
        urls = [e['url'] for e in cfg['urls']]
        assert all(u.startswith('http') for u in urls), urls
        assert all('//main' not in u for u in urls), urls
        assert all('我的直播' not in e['name'] for e in cfg['urls'])
    finally:
        cs.STORE = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- v9: EPG 对齐 / 搜索 / 熔断 / 大仓容错 / 码率 ----------------

def test_epg_mapping():
    """v9: 台名要能对上 EPG —— 白名单范围不变(59 个), 只补 tvg 名字。
    病根是 CCTV-1 综合 与 EPG 的 CCTV1 归一化后永不相等, 19 个央视台
    因此在电视上一直没有节目单"""
    wl = cs.build_whitelist()
    assert len(wl) == 59, '白名单范围不该被改动, 实际 %d' % len(wl)
    m = {e['name']: e['epg'] for e in wl}
    assert m['CCTV-1 综合'] == 'CCTV1'
    assert m['CCTV-16 奥林匹克'] == 'CCTV16'
    assert m['CCTV-5+ 体育赛事'] == 'CCTV5+'
    assert m['CCTV-4K 超高清'] == 'CCTV4K'
    assert m['湖南卫视'] == '湖南卫视'
    assert m['海南卫视'] == '旅游卫视'      # EPG 里仍是旧名
    assert m['凤凰卫视中文台'] == '凤凰中文'
    assert m['凤凰卫视资讯台'] == '凤凰资讯'
    # EPG 里确实没有的台: 必须 None(宁可没节目单, 也不能错配到别的台)
    for n in ['RTHK 港台电视32', '金鹰纪实', '湖南经视']:
        assert m[n] is None, n
    assert sum(1 for v in m.values() if v is None) == 5


def test_live_m3u_tvg_attr():
    """v9: live.m3u 要带 tvg-id/tvg-name; 没有 EPG 的台不能硬写"""
    tmp = tempfile.mkdtemp()
    old_m3u, old_json = cs.LIVE_M3U, cs.LIVE_JSON
    try:
        cs.LIVE_M3U = os.path.join(tmp, 'live.m3u')
        cs.LIVE_JSON = os.path.join(tmp, 'live.json')
        chans = [
            {'name': 'CCTV-1 综合', 'group': '央视', 'epg': 'CCTV1',
             'urls': [{'url': 'http://a/1.m3u8'}], 'n_hd': 1, 'n_backup': 0,
             'n_unver': 0, 'n_cand': 1},
            {'name': '湖南经视', 'group': '湖南', 'epg': None,
             'urls': [{'url': 'http://b/2.m3u8'}], 'n_hd': 1, 'n_backup': 0,
             'n_unver': 0, 'n_cand': 1},
        ]
        cs.write_live(chans)
        txt = open(cs.LIVE_M3U, encoding='utf-8').read()
        assert 'tvg-id="CCTV1" tvg-name="CCTV1"' in txt, txt
        line = [l for l in txt.split('\n') if '湖南经视' in l][0]
        assert 'tvg-' not in line, line
        assert 'x-tvg-url' in txt
    finally:
        cs.LIVE_M3U, cs.LIVE_JSON = old_m3u, old_json
        shutil.rmtree(tmp, ignore_errors=True)


def test_cms_search_ok():
    """v9: 搜索可用性 —— 首页能读但搜索报错的接口不该标 searchable"""
    real = cs.fetch_json
    try:
        cs.fetch_json = lambda *a, **k: {'list': [{'vod_id': 1}]}
        assert cs.cms_search_ok('http://x/api.php') is True
        cs.fetch_json = lambda *a, **k: {'list': []}
        assert cs.cms_search_ok('http://x/api.php') is False

        def boom(*a, **k):
            raise RuntimeError('search broken')

        cs.fetch_json = boom
        assert cs.cms_search_ok('http://x/api.php') is None   # 不确定, 不算坏
        cs.fetch_json = lambda *a, **k: ['not', 'dict']
        assert cs.cms_search_ok('http://x/api.php') is None
    finally:
        cs.fetch_json = real


def test_circuit_breaker():
    """v9: 熔断 —— 腰斩才拦, 正常波动不拦, 没有基线不拦"""
    assert cs.circuit_broken(100, 50, 40, 48)[0] is True
    assert cs.circuit_broken(100, 50, 80, 20)[0] is True
    assert cs.circuit_broken(100, 50, 70, 40)[0] is False
    assert cs.circuit_broken(100, 50, 120, 60)[0] is False
    # 刚好一半: 不拦("低于"一半才拦)
    assert cs.circuit_broken(100, 50, 50, 25)[0] is False
    # 没有基线(首次运行)
    assert cs.circuit_broken(0, 0, 0, 0)[0] is False
    assert cs.circuit_broken(0, 0, 3, 2)[0] is False
    # 基线太小
    assert cs.circuit_broken(5, 8, 0, 0)[0] is False
    ok, why = cs.circuit_broken(100, 50, 30, 50)
    assert ok and '接口数' in why, why


# ---------------- v12: 子资源通道画像 ----------------
# 这一组守的是"点进去会不会转圈"—— 2026-10-27 查实的根因:
# 配置 json 几十KB走代理也能拉到(所以"仓能打开"), 但仓里的 jar/外挂js
# 动辄几MB、几十上百个, 全压在 21KB/s 的 gh-proxy 上就永远转圈。

def test_is_gh_url():
    """各类 GitHub 加速代理都按 GitHub 系计(转发内容仍来自 GitHub, 速度同样受限)"""
    assert cs.is_gh_url('https://raw.githubusercontent.com/a/b')
    assert cs.is_gh_url(
        'https://gh-proxy.com/https://raw.githubusercontent.com/a/b')
    assert cs.is_gh_url('https://ghproxy.cn/https://raw.githubusercontent.com/a')
    assert cs.is_gh_url('https://ghfast.top/https://raw.githubusercontent.com/a')
    assert cs.is_gh_url(
        'https://github.moeyy.xyz/https://raw.githubusercontent.com/a/b')
    # 直连域名不算
    assert not cs.is_gh_url('https://ztha.top/TVBox/ztha.jar')
    assert not cs.is_gh_url('https://qiniu.gongxueyun.com/upload/x.jar')
    assert not cs.is_gh_url('https://6800.kstore.vip/fish.json')
    assert not cs.is_gh_url('https://git.acwing.com/a/b/-/raw/main/x.json')
    assert not cs.is_gh_url('')
    assert not cs.is_gh_url(None)


def test_cdn_profile_direct_store():
    """直连仓: 配置与 jar 同域 -> GH依赖 0%, 全部 csp_ -> jar率 100%"""
    cfg = {'spider': './ztha.jar;md5;097bfd13', 'sites': [
        {'api': 'csp_A'}, {'api': 'csp_B'}, {'api': 'csp_C'}]}
    p = cs.cdn_profile(cfg, 'https://ztha.top/TVBox/thdjk.json')
    assert p['sites'] == 3
    assert p['jar_packed'] == 3
    assert p['gh_rate'] == 0.0, p
    assert p['gh_cnt'] == 0


def test_cdn_profile_inherits_github():
    """同一份配置, 放在 GitHub 上 -> 相对路径也算 GitHub 系"""
    cfg = {'spider': './jar/pg.jar;md5;abc', 'sites': [{'api': 'csp_A'}]}
    p = cs.cdn_profile(cfg, 'https://raw.githubusercontent.com/u/r/master/x.json')
    assert p['gh_rate'] == 1.0, p
    p = cs.cdn_profile(
        cfg, 'https://gh-proxy.com/https://raw.githubusercontent.com/u/r/x.json')
    assert p['gh_rate'] == 1.0, p


def test_cdn_profile_detects_turn_circle():
    """外挂 js 全压在 GitHub 上的仓 -> 必须超门槛(实地表现就是一直转圈)"""
    sites = [{'api': './lib/drpy2.min.js', 'ext': './js/s%d.js' % i}
             for i in range(10)]
    cfg = {'spider': './jar/pg.jar', 'sites': sites}
    p = cs.cdn_profile(
        cfg, 'https://gh-proxy.com/https://raw.githubusercontent.com/'
             'gaotianliuyun/gao/master/js.json')
    assert p['res_total'] > 10, p          # 每个站 2 个外挂资源
    assert p['gh_rate'] > cs.STORE_GH_MAX, p
    assert p['gh_cnt'] == p['res_total'], p


def test_cdn_profile_mixed():
    """混合仓: 一半直连一半 GitHub -> 比例落在中间"""
    cfg = {'spider': 'https://qiniu.a.com/j.jar', 'sites': [
        {'api': 'http://cms1.com/api.php/provide/vod/'},
        {'api': 'http://cms2.com/api.php/provide/vod/'},
        {'api': 'https://raw.githubusercontent.com/x/y/z.js'},
    ]}
    p = cs.cdn_profile(cfg, 'https://a.com/cfg.json')
    assert p['gh_cnt'] == 1, p
    assert p['res_total'] == 4, p
    assert abs(p['gh_rate'] - 0.25) < 1e-6, p


def test_cdn_profile_malformed():
    """畸形结构不能崩(仓配置是外部数据, 什么都可能有)"""
    assert cs.cdn_profile({})['sites'] == 0
    assert cs.cdn_profile({'sites': {'a': 1}})['sites'] == 0
    assert cs.cdn_profile({'sites': ['x', 1, None]})['sites'] == 0
    assert cs.cdn_profile(None)['gh_rate'] == 0.0
    assert cs.cdn_profile({'spider': 123, 'sites': []})['sites'] == 0


def test_is_perfect_store():
    """v9: 大仓容错 —— 小仓仍要 100%, 大仓允许死 1 个"""
    assert cs.is_perfect_store({'score': 1.0, 'total': 3, 'alive': 3}) is True
    assert cs.is_perfect_store({'score': 2 / 3, 'total': 3, 'alive': 2}) is False
    assert cs.is_perfect_store(
        {'score': 199 / 200, 'total': 200, 'alive': 199}) is True
    assert cs.is_perfect_store(
        {'score': 198 / 200, 'total': 200, 'alive': 198}) is False
    assert cs.is_perfect_store(
        {'score': 29 / 30, 'total': 30, 'alive': 29}) is True
    assert cs.is_perfect_store(
        {'score': 28 / 30, 'total': 30, 'alive': 28}) is False
    assert cs.is_perfect_store({'score': None, 'total': 0}) is False


def test_store_prefers_big_store():
    """v9: 同样完美时, 内容多(站点多)的仓排前面 (v12 保留, 排在 GH 依赖之后)"""
    tmp = tempfile.mkdtemp()
    old = cs.STORE
    try:
        cs.STORE = os.path.join(tmp, 'store.json')
        scored = [
            {'url': 'http://s/small.json', 'name': '小仓', 'kind': 'store',
             'score': 1.0, 'alive': 3, 'total': 3, 'state': '保留',
             'gh_rate': 0.0, 'sites_n': 3, 'jar_rate': 1.0},
            {'url': 'http://s/big.json', 'name': '大仓', 'kind': 'store',
             'score': 0.995, 'alive': 199, 'total': 200, 'state': '保留',
             'gh_rate': 0.0, 'sites_n': 200, 'jar_rate': 0.6},
        ]
        cs.write_store(scored, '', False, [])
        cfg = json.load(open(cs.STORE, encoding='utf-8'))
        names = [e['name'] for e in cfg['urls']]
        assert names[0].startswith('大仓'), names
    finally:
        cs.STORE = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_live_kbps_demote():
    """v9: 1080p 但码率虚低的线降到备用层, 不占主位"""
    w, h = 1920, 1080
    assert 2000 / (w * h / 1000.0) >= cs.LIVE_MIN_BPP     # 正常
    assert 800 / (w * h / 1000.0) < cs.LIVE_MIN_BPP       # 假高清
    assert 1500 / (1280 * 720 / 1000.0) >= cs.LIVE_MIN_BPP


def test_probe_live_kbps_parse():
    """v9: master playlist 的 BANDWIDTH 要能解析出来"""
    real_get, real_probe = cs.http_get, cs.ffprobe_res
    try:
        master = (b'#EXTM3U\n'
                  b'#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=1920x1080\n'
                  b'high.m3u8\n'
                  b'#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720\n'
                  b'mid.m3u8\n')
        cs.http_get = lambda *a, **k: (200, master)
        cs.ffprobe_res = lambda *a, **k: (0, 0)   # 强制走 master 分支
        r = cs.probe_live('http://x/m.m3u8')
        assert r['ok'] and r['method'] == 'master', r
        assert r['w'] == 1920 and r['h'] == 1080, r
        assert r['kbps'] == 800, r
        # 媒体清单没有 BANDWIDTH -> kbps=0, 不做判断
        media = (b'#EXTM3U\n#EXTINF:6.0,\na.ts\n#EXTINF:6.0,\nb.ts\n'
                 b'#EXTINF:6.0,\nc.ts\n')
        cs.http_get = lambda *a, **k: (200, media)
        cs.ffprobe_res = lambda *a, **k: (1280, 720)
        r2 = cs.probe_live('http://x/1.m3u8')
        assert r2['kbps'] == 0, r2
    finally:
        cs.http_get, cs.ffprobe_res = real_get, real_probe


def test_ad_hits_is_penalty_not_veto():
    """v9: 站点插广告改成扣分, 不再整仓剔除"""
    assert round(1.0 - cs.AD_HITS_PENALTY * 1, 2) >= cs.STORE_MIN_HEALTH
    assert round(1.0 - cs.AD_HITS_PENALTY * 2, 2) >= cs.STORE_MIN_HEALTH
    # 扣分有上限, 不会无限扣
    assert cs.AD_HITS_PENALTY * 3 > cs.AD_HITS_PENALTY_MAX
    assert round(1.0 - cs.AD_HITS_PENALTY_MAX, 2) >= cs.STORE_MIN_HEALTH


# ---------------- v8: 兜底 / 重试 ----------------

def test_pick_live_urls():
    """v8 兜底层: 有实测线时绝不混入未实测线; 完全没有时才兜底"""
    HD = [{'url': 'h%d' % i, 'w': 1920, 'h': 1080, 'ms': 100} for i in range(4)]
    BK = [{'url': 'b%d' % i, 'w': 1280, 'h': 576, 'ms': 200} for i in range(4)]
    UV = [{'url': 'u%d' % i, 'w': 0, 'h': 0, 'ms': 300 + i} for i in range(4)]

    # 1) 高清足够: 只取高清, 不碰兜底
    urls, n_uv = cs.pick_live_urls(HD, BK, UV)
    assert [u['url'] for u in urls] == ['h0', 'h1', 'h2'], urls
    assert n_uv == 0

    # 2) 高清不足: 备用补位, 仍不碰兜底
    urls, n_uv = cs.pick_live_urls(HD[:1], BK, UV)
    assert [u['url'] for u in urls] == ['h0', 'b0', 'b1'], urls
    assert n_uv == 0

    # 3) 一条实测线都没有: 兜底生效, 按延迟排序
    urls, n_uv = cs.pick_live_urls([], [], UV)
    assert [u['url'] for u in urls] == ['u0', 'u1', 'u2'], urls
    assert n_uv == 3

    # 4) 什么都没有: 空, 频道消失
    urls, n_uv = cs.pick_live_urls([], [], [])
    assert urls == [] and n_uv == 0


def test_probe_live_retry_and_hls_flag():
    """v8: 连接异常重试一次; 403 不重试; 只有 m3u8 才带 hls 标记"""
    real_get, real_probe = cs.http_get, cs.ffprobe_res
    M3U8 = (b'#EXTM3U\n#EXTINF:6.0,\na.ts\n#EXTINF:6.0,\nb.ts\n'
            b'#EXTINF:6.0,\nc.ts\n')
    try:
        # 1) 首连抖动 -> 重试后成功
        state = {'n': 0}

        def flaky(url, timeout, max_bytes=None, headers=None):
            state['n'] += 1
            if state['n'] == 1:
                raise TimeoutError('抖动')
            return 200, M3U8

        cs.http_get = flaky
        cs.ffprobe_res = lambda *a, **k: (1280, 720)
        r = cs.probe_live('http://x/1.m3u8')
        assert state['n'] == 2 and r['ok'] and r['w'] == 1280, (state, r)
        assert r.get('hls') is True

        # 2) 两次都异常 -> 判死
        def dead(url, timeout, max_bytes=None, headers=None):
            raise TimeoutError('死')

        cs.http_get = dead
        assert cs.probe_live('http://x/2.m3u8')['ok'] is False

        # 3) 403 是确定性失败, 不该重试
        state['n'] = 0

        def forbidden(url, timeout, max_bytes=None, headers=None):
            state['n'] += 1
            return 403, b''

        cs.http_get = forbidden
        assert cs.probe_live('http://x/3.m3u8')['ok'] is False
        assert state['n'] == 1, state

        # 4) m3u8 但测不出分辨率 -> hls 标记在(进兜底层)
        cs.http_get = lambda *a, **k: (200, M3U8)
        cs.ffprobe_res = lambda *a, **k: (0, 0)
        r = cs.probe_live('http://x/4.m3u8')
        assert r['ok'] and r['w'] == 0 and r.get('hls') is True, r

        # 5) 返回 HTML 的假流 -> 没有 hls 标记(不进兜底层)
        cs.http_get = lambda *a, **k: (200, b'<html>404</html>')
        r = cs.probe_live('http://x/5.m3u8')
        assert r['ok'] and r['w'] == 0 and not r.get('hls'), r

        # 6) 广告线照旧拒收
        AD = (b'#EXTM3U\n#EXTINF:6.0,\na.ts\n#EXT-X-DISCONTINUITY\n'
              b'#EXTINF:6.0,\nb.ts\n#EXT-X-DISCONTINUITY\n'
              b'#EXTINF:6.0,\nc.ts\n')
        cs.http_get = lambda *a, **k: (200, AD)
        r = cs.probe_live('http://x/6.m3u8')
        assert r['ok'] is False and r['method'] == 'ads', r
    finally:
        cs.http_get, cs.ffprobe_res = real_get, real_probe


# ---------------- v10: 广告域名采集 ----------------

def test_norm_ad_dom():
    """ads 项写法五花八门: 裸域名/URL/通配/带端口, 都要归一成 host"""
    assert cs.norm_ad_dom('http://ad.aaa.com/x.js') == 'ad.aaa.com'
    assert cs.norm_ad_dom('*.track.cc') == 'track.cc'
    assert cs.norm_ad_dom('  Ad.AAA.com:8080/p  ') == 'ad.aaa.com'
    assert cs.norm_ad_dom('not-a-domain') == ''
    assert cs.norm_ad_dom('') == ''


def test_ads_consensus():
    """多仓共识才算可信: 单仓乱写图床域名会连海报一起拦掉"""
    saved = (dict(cs._ADS_VOTES), dict(cs._ADS_RAW))
    try:
        cs._ADS_VOTES.clear()
        cs._ADS_RAW.clear()
        cs.note_ads({'ads': ['http://ad.aaa.com/x.js', '*.track.cc']},
                    'http://s1/a.json')
        cs.note_ads({'ads': ['ad.aaa.com']}, 'http://s2/b.json')
        cs.note_ads({'ads': ['img.cdn-poster.com']}, 'http://s3/c.json')
        con, one = cs.ads_consensus(2)
        assert con == ['ad.aaa.com'], con
        # track.cc 只有 s1 列过(通配写法也要能归一), 属于单仓待确认
        assert one == ['img.cdn-poster.com', 'track.cc'], one
        # 同一个仓的不同子仓只算一票, 自己刷票不能凑成共识
        cs.note_ads({'ads': ['x.cdn.com']}, 'http://s1/a.json')
        cs.note_ads({'ads': ['x.cdn.com']}, 'http://s1/sub.json')
        con2, _ = cs.ads_consensus(2)
        assert 'x.cdn.com' not in con2, con2
        # 非 list/str 的 ads(上游写坏)不能让整轮崩
        cs.note_ads({'ads': {'a': 1}}, 'http://s4/d.json')
        cs.note_ads({'ads': None}, 'http://s5/e.json')
    finally:
        cs._ADS_VOTES.clear()
        cs._ADS_VOTES.update(saved[0])
        cs._ADS_RAW.clear()
        cs._ADS_RAW.update(saved[1])


def test_write_ads_pool():
    """清单必须落盘且带启用状态 —— 用户靠这个文件决定要不要开"""
    tmp = tempfile.mkdtemp()
    old = cs.ADS_POOL
    try:
        cs.ADS_POOL = os.path.join(tmp, 'ads_pool.json')
        cs.write_ads_pool(['ad.aaa.com'], ['img.x.com'])
        j = json.load(open(cs.ADS_POOL, encoding='utf-8'))
        assert j['enabled'] is False           # 默认不启用
        assert j['consensus'] == ['ad.aaa.com']
        assert j['single'] == ['img.x.com']
    finally:
        cs.ADS_POOL = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- v13: 方案C 直播深探分级 ----------------

def test_live_deep_quota():
    """v13: 深探配额是正整数, 且要明显小于候选上限 ——
    等于候选上限就退化成全量深探, 832 条 × 12 秒能把 job 拖死"""
    assert isinstance(cs.LIVE_DEEP_PER_CH, int) and cs.LIVE_DEEP_PER_CH >= 1
    assert cs.LIVE_DEEP_PER_CH * 4 <= cs.LIVE_MAX_CAND, \
        '深探配额 %d 相对候选上限 %d 过大' % (cs.LIVE_DEEP_PER_CH,
                                            cs.LIVE_MAX_CAND)


def test_probe_live_shallow_skips_ffprobe():
    """v13: 浅探(deep=False)一次 ffprobe 都不许调 ——
    第一轮全量候选靠它, 漏调一次就会在白名单规模上乘以 12 秒超时"""
    real_get, real_probe = cs.http_get, cs.ffprobe_res
    calls = {'n': 0}

    def counter(*a, **k):
        calls['n'] += 1
        return (1920, 1080)
    try:
        cs.ffprobe_res = counter
        master = (b'#EXTM3U\n'
                  b'#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720\n'
                  b'mid.m3u8\n')
        cs.http_get = lambda *a, **k: (200, master)
        r = cs.probe_live('http://x/m.m3u8', deep=False)
        assert calls['n'] == 0, '浅探不该调 ffprobe, 实际调了 %d 次' % calls['n']
        assert r['ok'] and r['method'] == 'master', r
        assert (r['w'], r['h']) == (1280, 720), r

        # 媒体清单: 没有 master 声明, 浅探只能给出"可达但画质未知"
        media = b'#EXTM3U\n#EXTINF:6.0,\na.ts\n#EXTINF:6.0,\nb.ts\n'
        cs.http_get = lambda *a, **k: (200, media)
        r2 = cs.probe_live('http://x/1.m3u8', deep=False)
        assert r2['ok'] and r2['w'] == 0 and r2.get('hls'), r2
        # 非 m3u8 文本(php 转发): 浅探也算可达, 但不给画质结论
        cs.http_get = lambda *a, **k: (200, b'not a playlist')
        r3 = cs.probe_live('http://x/2.php', deep=False)
        assert r3['ok'] and r3['w'] == 0, r3
        assert calls['n'] == 0

        # 对照组: deep=True 必须真去测, 且实测优先于 master 声明
        cs.http_get = lambda *a, **k: (200, master)
        r4 = cs.probe_live('http://x/m.m3u8', deep=True)
        assert calls['n'] == 1, calls
        assert r4['method'] == 'ffmpeg' and r4['w'] == 1920, r4
    finally:
        cs.http_get, cs.ffprobe_res = real_get, real_probe


def test_pick_live_urls_fill_unverified():
    """v13: 实测线不足 LIVE_KEEP 时用未实测线补位 ——
    方案C 每台只深测前几条, 不补的话所有台都退化成 2 条线。
    补位只在不够数时发生, 绝不挤掉已实测的线, 且要如实报数"""
    HD = [{'url': 'h0', 'w': 1920, 'h': 1080, 'ms': 100}]
    BK = [{'url': 'b0', 'w': 1280, 'h': 576, 'ms': 200}]
    UV = [{'url': 'u%d' % i, 'w': 0, 'h': 0, 'ms': 300 + i} for i in range(4)]

    urls, n_uv = cs.pick_live_urls(HD, BK, UV)
    assert [u['url'] for u in urls] == ['h0', 'b0', 'u0'], urls
    assert n_uv == 1, n_uv            # 只补了 1 条

    # 实测线够数: 一条未实测都不许混进来
    urls2, n_uv2 = cs.pick_live_urls(HD * 3, BK, UV)
    assert len(urls2) == cs.LIVE_KEEP and n_uv2 == 0, (urls2, n_uv2)

    # 补位优先取探活最快的, 不够几条就补几条
    UV2 = [{'url': 'slow', 'w': 0, 'h': 0, 'ms': 9000},
           {'url': 'fast', 'w': 0, 'h': 0, 'ms': 50}]
    urls3, n3 = cs.pick_live_urls([], BK[:1], UV2)
    assert [u['url'] for u in urls3] == ['b0', 'fast', 'slow'], urls3
    assert n3 == 2, n3


# ---------------- v14: 广告治理 ----------------

def _idx(durs):
    return '#EXTM3U\n' + ''.join(
        '#EXTINF:%s,\nseg%d.ts\n' % (d, i) for i, d in enumerate(durs))


def test_ad_seg_ratio_boundary():
    """v14 核心回归: 旧判据 '>=15s 且 >2.5x中位数' 在 6 秒切片下退化成
    '15 > 15' = False, 15 秒广告必然漏判。新判据必须是倍数主导。
    这组用例就是拿旧代码的真实漏判场景钉死它。"""
    # 6 秒切片 + 15 秒广告: 旧代码漏判, 新版必须抓到
    assert cs.live_index_ads(_idx([15, 6, 6, 6, 6])), '6s切片+15s广告必须命中'
    # 6 秒切片 + 20 秒广告: 两版都该抓到
    assert cs.live_index_ads(_idx([20, 6, 6, 6, 6]))
    # 起播贴片 12 秒(旧版 10s 阈值 + 3 倍中位数也漏)
    assert cs.live_index_ads(_idx([12, 6, 6, 6, 6])), '12s起播贴片必须命中'
    # 中插 15 秒
    assert cs.live_index_ads(_idx([6, 6, 15, 6, 6]))

    # 反例: 不许误杀
    assert not cs.live_index_ads(_idx([6, 6, 6, 6, 6]))        # 均匀 6s
    assert not cs.live_index_ads(_idx([20, 20, 20]))            # 均匀 20s 慢切片
    assert not cs.live_index_ads(_idx([4, 4, 4, 4]))            # 均匀 4s
    # 1~2 秒切片: 倍数判据会命中但绝对下限(12s/9s)必须挡住
    assert not cs.live_index_ads(_idx([4, 1, 1, 1, 1])), '短切片不许靠倍数误杀'
    # 单次 DISCONTINUITY 是重连, 不是广告
    once = '#EXTM3U\n#EXTINF:6,\na.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:6,\nb.ts\n#EXTINF:6,\nc.ts\n'
    assert not cs.live_index_ads(once)
    # 两处拼接才是强信号
    twice = ('#EXTM3U\n#EXTINF:6,\na.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:6,\nb.ts\n'
             '#EXT-X-DISCONTINUITY\n#EXTINF:6,\nc.ts\n')
    assert cs.live_index_ads(twice)


def test_is_bare_ip():
    assert cs.is_bare_ip('107.150.60.122')
    assert cs.is_bare_ip('38.75.136.137')
    assert not cs.is_bare_ip('ali-m-l.cztv.com')
    assert not cs.is_bare_ip('www.hebtv.com')
    assert not cs.is_bare_ip('')
    assert not cs.is_bare_ip(None)


def test_pick_ad_hosts():
    """v14: 广告分发站判据 —— 裸IP + 扛>=N个不同频道。
    关键: 域名型 host 扛多台**不许**判(省级 IPTV 平台 / 广电 CDN 就是这样),
    否则会把正规源整片误杀。"""
    # 线上真实形态: 一个裸IP扛多个台
    pairs = []
    for i in range(10):
        pairs.append(('CCTV-%d' % i, 'http://107.150.60.122/live/cctv%dhd.m3u8' % i))
    for i in range(6):
        pairs.append(('卫视%d' % i, 'http://63.141.230.178:82/gslb/x%d.m3u8' % i))
    # 一个正规 CDN 域名扛很多台(河北广电) —— 不该被判
    for i in range(20):
        pairs.append(('河北%d' % i, 'https://event.pull.hebtv.com/live/l%d.m3u8' % i))
    # 裸IP只扛2个台(未达阈值) —— 不该被判
    pairs.append(('单台A', 'http://9.9.9.9/live/a.m3u8'))
    pairs.append(('单台B', 'http://9.9.9.9/live/b.m3u8'))

    hm = cs.host_channel_map(pairs)
    assert len(hm['107.150.60.122']) == 10
    assert len(hm['event.pull.hebtv.com']) == 20

    bad = cs.pick_ad_hosts(hm)
    assert '107.150.60.122' in bad, '裸IP扛10个台必须判为分发站'
    assert '63.141.230.178' in bad, '裸IP扛6个台必须判为分发站'
    assert 'event.pull.hebtv.com' not in bad, '域名扛20台不许误杀'
    assert '9.9.9.9' not in bad, '裸IP只扛2个台不许判'
    assert len(bad) == 2, bad

    # 阈值可调(便于测试与将来放宽)
    assert '9.9.9.9' in cs.pick_ad_hosts(hm, min_ch=2)


def test_snapshot_and_preroll():
    """v14: 起播实测 —— 两次快照比对判真直播。"""
    live1 = ('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:100\n#EXT-X-TARGETDURATION:6\n'
             '#EXTINF:6,\na1.ts\n#EXTINF:6,\na2.ts\n#EXTINF:6,\na3.ts\n')
    live2 = ('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:102\n#EXT-X-TARGETDURATION:6\n'
             '#EXTINF:6,\na3.ts\n#EXTINF:6,\na4.ts\n#EXTINF:6,\na5.ts\n')
    s1, s2 = cs.snapshot_playlist(live1), cs.snapshot_playlist(live2)
    assert s1['seq'] == 100 and s2['seq'] == 102
    assert s1['target'] == 6
    # 序列前进 = 真直播
    assert cs.judge_preroll(s1, s2) == (False, '')

    # 序列不动 = 伪直播(广告垫片循环)
    fake = cs.snapshot_playlist(live1)
    ok, why = cs.judge_preroll(s1, fake)
    assert ok and '不前进' in why, (ok, why)

    # ENDLIST = 预置循环, 真直播绝不会有
    ended = cs.snapshot_playlist(live1 + '#EXT-X-ENDLIST\n')
    ok2, why2 = cs.judge_preroll(ended, ended)
    assert ok2 and 'ENDLIST' in why2 or '预置' in why2, (ok2, why2)

    # VOD 类型同理
    vod = cs.snapshot_playlist(
        '#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:6,\na.ts\n')
    assert cs.judge_preroll(vod, vod)[0]

    # master 清单: variant 地址天生不变, 不许拿它判"没前进"
    m1 = cs.snapshot_playlist(
        '#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080\nhd.m3u8\n')
    m2 = cs.snapshot_playlist(
        '#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080\nhd.m3u8\n')
    assert m1['master'] is True
    assert cs.judge_preroll(m1, m2) == (False, ''), 'master 不许误判'

    # 没有 MEDIA-SEQUENCE 时: 分片列表完全相同 -> 判伪直播
    noseq1 = cs.snapshot_playlist('#EXTM3U\n#EXTINF:6,\nx.ts\n#EXTINF:6,\ny.ts\n')
    noseq2 = cs.snapshot_playlist('#EXTM3U\n#EXTINF:6,\nx.ts\n#EXTINF:6,\ny.ts\n')
    assert noseq1['seq'] is None
    assert cs.judge_preroll(noseq1, noseq2)[0]

    # 拉不到(快照为 None)一律放行 —— 不许把"测不出"当"有问题"
    assert cs.judge_preroll(None, None) == (False, '')
    assert cs.judge_preroll(s1, None) == (False, '')
    # 不是 m3u8
    assert cs.snapshot_playlist('<html>404</html>') is None
    assert cs.snapshot_playlist('') is None


def test_circuit_breaker_v14_ad_purge():
    """v14: 熔断必须分清"上游抖动"和"主动剔除广告站"。
    线上真实场景: 上一版出线 52 个台, v14 剔除广告分发站后只剩 21 个。
    如果拿淘汰后的数去比, 21 < 52*0.5=26 会误判成"上游挂了", 于是拒绝写
    live 文件 —— 改完等于完全没生效。这是必须钉死的坑。"""
    # 主动剔除: 候选台数没掉(59 >= 52), 只是出线变少 -> 不许拦
    ok, why = cs.circuit_broken(8, 52, 8, 21, cand_ch=59)
    assert not ok, '主动剔除不该熔断, 但报了: %s' % why

    # 上游真的挂了: 候选台数从 52 掉到 10 -> 必须拦
    ok2, why2 = cs.circuit_broken(8, 52, 8, 10, cand_ch=10)
    assert ok2 and '候选' in why2, (ok2, why2)

    # 全部被剔干净: 候选正常但出线 0 -> 也要拦(不然会写出空直播)
    ok3, why3 = cs.circuit_broken(8, 52, 8, 0, cand_ch=59)
    assert ok3 and '剔除' in why3, (ok3, why3)

    # 接口数腰斩仍然独立生效
    ok4, why4 = cs.circuit_broken(100, 52, 30, 21, cand_ch=59)
    assert ok4 and '接口数' in why4, (ok4, why4)

    # 老调用方式(不传 cand_ch)行为不变
    assert cs.circuit_broken(100, 50, 80, 20)[0] is True
    assert cs.circuit_broken(100, 50, 70, 40)[0] is False


# ---------------- v15: 归属地判据 ----------------

def test_pick_ad_hosts_geo():
    """v15 核心: 同样"一个IP扛6个台", 境外的判广告站, 大陆的不能判。
    这是 v14 漏掉的关键 —— 中国移动的省网 IPTV 一台扛 20 多个台是标准部署,
    不分归属一律砍, 等于把干净源和广告站一起枪毙。"""
    hm = {'107.150.60.122': set('abcdefg'),      # 美国 Nocix, 扛 7 台
          '39.134.115.163': set('abcdefg'),      # 中国移动, 扛 7 台
          '1.2.3.4': {'a', 'b'},                 # 美国, 只扛 2 台
          'cdn.example.com': set('abcdefghij')}  # 域名型, 扛 10 台
    geo = {'107.150.60.122': 'US', '39.134.115.163': 'CN',
           '1.2.3.4': 'US', 'cdn.example.com': 'DOMAIN'}
    got = cs.pick_ad_hosts(hm, geo)
    assert got == {'107.150.60.122'}, got     # 只砍境外高承载
    # 大陆高承载必须豁免
    assert '39.134.115.163' not in got
    # 域名型不论扛多少都不砍(可能是省级 IPTV 平台)
    assert 'cdn.example.com' not in got
    # 扛得太少不砍
    assert '1.2.3.4' not in got
    # geo=None 时退化成 v14 行为, 保持旧行为可用
    old = cs.pick_ad_hosts(hm)
    assert old == {'107.150.60.122', '39.134.115.163'}, old


def test_pick_ad_hosts_unknown_country():
    """归属查不到('?')时按"非大陆"处理 —— 宁可在广告上保守,
    因为一条广告线的代价是用户每次打开都等 30 秒, 而少一条线只是少一个备选。"""
    hm = {'9.9.9.9': set('abcde')}
    assert cs.pick_ad_hosts(hm, {'9.9.9.9': '?'}) == {'9.9.9.9'}
    assert cs.pick_ad_hosts(hm, {}) == {'9.9.9.9'}


def test_is_clean_src():
    geo = {'39.134.115.163': 'CN', '107.150.60.122': 'US',
           'hlsal-ldvt.qing.mgtv.com': 'DOMAIN', '1.2.3.4': '?'}
    assert cs.is_clean_src('http://39.134.115.163/live/x.m3u8', geo) is True
    assert cs.is_clean_src('http://107.150.60.122/live/x.m3u8', geo) is False
    assert cs.is_clean_src('http://hlsal-ldvt.qing.mgtv.com/a.m3u8', geo) is True
    assert cs.is_clean_src('http://1.2.3.4/x.m3u8', geo) is False
    assert cs.is_clean_src('http://unknown.host/x.m3u8', geo) is False


def test_annotate_geo_domain_no_network():
    """域名型不该触发任何网络请求 —— 传纯域名进去必须秒回, 且不写缓存。"""
    got = cs.annotate_geo({'a.example.com', 'b.example.com'})
    assert got == {'a.example.com': 'DOMAIN', 'b.example.com': 'DOMAIN'}, got


def test_cn_blind_fill():
    """v15: 大陆盲源补位 —— 实测线不足 3 条时用盲源补, 有实测线时一条不挤。"""
    def e(u, h, ms):
        return {'url': u, 'w': 1920 if h else 0, 'h': h,
                'method': 'ffmpeg', 'ms': ms}
    blind = [{'url': 'http://cn1/x.m3u8', 'w': 0, 'h': 0,
              'method': 'cn-blind', 'ms': 9999},
             {'url': 'http://cn2/x.m3u8', 'w': 0, 'h': 0,
              'method': 'cn-blind', 'ms': 9999},
             {'url': 'http://cn3/x.m3u8', 'w': 0, 'h': 0,
              'method': 'cn-blind', 'ms': 9999}]
    # 一条实测线都没有 -> 盲源全用上(总比空着强)
    urls, _ = cs.pick_live_urls([], [], list(blind))
    assert len(urls) == 3, urls
    assert all(x['method'] == 'cn-blind' for x in urls)
    # 已有 2 条实测线 -> 只补 1 条, 不挤掉实测线
    hd = [e('http://hd1.m3u8', 1080, 800)]
    bk = [e('http://bk1.m3u8', 576, 900)]
    urls, _ = cs.pick_live_urls(hd, bk, list(blind))
    assert len(urls) == 3
    assert urls[0]['url'] == 'http://hd1.m3u8'      # 实测高清仍在第一位
    assert sum(1 for x in urls if x['method'] == 'cn-blind') == 1
    # 实测线已满 3 条 -> 盲源一条都不进
    urls, _ = cs.pick_live_urls([e('a', 1080, 1), e('b', 1080, 2),
                                e('c', 1080, 3)], [], list(blind))
    assert all(x['method'] != 'cn-blind' for x in urls)


# ---------------- 独立运行 ----------------

def test_isp_class():
    """v16: 运营商识别。用户家是中国移动 —— 移动源要顶到前面, 联通/电信的
    省网 IPTV 跨网基本连不上要沉底。只判"是不是 CN"分不出这个差别。"""
    assert cs.isp_class('China Mobile Communications Corporation') == 'CMCC'
    assert cs.isp_class('AS9808 China Mobile') == 'CMCC'
    assert cs.isp_class('CHINA MOBILE') == 'CMCC'
    assert cs.isp_class('China Telecom (Group)') == 'CT'
    assert cs.isp_class('CHINANET-BACKBONE') == 'CT'
    assert cs.isp_class('China Unicom IP network') == 'CUCC'
    assert cs.isp_class('CHINA169-BACKBONE') == 'CUCC'
    assert cs.isp_class('CERNET') == 'EDU'
    assert cs.isp_class('Nocix LLC') == ''
    assert cs.isp_class('') == ''
    assert cs.isp_class(None) == ''


def test_cn_affinity_static():
    """v16: 国内亲和度打分 —— 决定"每台第一条"是谁, 治卡顿的核心。
    下面这批 URL 全是线上 live.txt 里真实出现过的。"""
    geo = {'39.134.115.163': 'CN', '58.248.112.205': 'CN',
           '107.150.60.122': 'US', '9.9.9.9': '?'}
    isp = {'39.134.115.163': 'CMCC', '58.248.112.205': 'CUCC',
           '107.150.60.122': 'Nocix'}
    A = cs.cn_affinity
    # ① 单台腾讯云小机的 php 转发(线上有 10 个台拿它当第一条) —— 必须沉底
    assert A('http://101.35.240.114:88/live.php?id=CCTV1', geo,
             isp) == cs.AFF_PHP_RELAY
    # ② 广东联通 IPTV: 用户在湖南、家里移动宽带, 跨省又跨网 -> 排在移动源后
    assert A('http://58.248.112.205:8006/GD_CUCC/G_CCTV-11.m3u8?Authinfo=x',
             geo, isp) == cs.AFF_IPTV_OTHER
    # ③ 中国移动公网源: 用户本网, 给大陆最高档
    assert A('http://39.134.115.163/live/cctv1.m3u8', geo,
             isp) == cs.AFF_ISP_MATCH
    # ④ 国内 CDN(江西广电 GitV / 百视通 / 湖南华声在线)顶到最前
    for u in ('http://jxcbn.ws-cdn.gitv.tv/hls/CCTV1HD/index.m3u8',
              'http://bp-resource-dfl.bestv.cn/live/x.m3u8',
              'https://liveplay-srs.voc.com.cn/hls/tv/134.m3u8'):
        assert A(u, geo, isp) == cs.AFF_CN_CDN, u
    # ⑤ 境外 VPS 与 .xyz 廉价域名: 沉底
    assert A('http://107.150.60.122/live/cctv1.m3u8', geo,
             isp) == cs.AFF_OVERSEA
    assert A('https://zby.130519.xyz/PLTV/11/index.m3u8', geo,
             isp) == cs.AFF_CHEAP_DOMAIN
    # ⑥ 归属查不出: 不冤枉也不抬高
    assert A('http://9.9.9.9/live/a.m3u8', geo, isp) == cs.AFF_UNKNOWN


def test_cn_affinity_speed_override():
    """v16: 家里实测速度优先级最高 —— 快的压过任何静态分, 慢的一律沉底,
    但同为慢线时仍保留相对次序(好歹能挑个没那么烂的)。"""
    geo, isp = {}, {}
    A = cs.cn_affinity
    fast = A('http://1.1.1.1/a.m3u8', geo, isp, speed=3200)
    cdn = A('http://jxcbn.ws-cdn.gitv.tv/x.m3u8', geo, isp)
    assert fast > cdn, '实测 3.2Mbps 必须压过静态国内 CDN'
    assert A('http://2.2.2.2/a.m3u8', geo, isp, speed=120) < cs.AFF_PHP_RELAY
    assert A('http://2.2.2.2/a.m3u8', geo, isp, speed=0) < cs.AFF_PHP_RELAY
    assert (A('http://3.3.3.3/a.m3u8', geo, isp, speed=250)
            > A('http://2.2.2.2/a.m3u8', geo, isp, speed=100))


def test_load_speed_rank_never_crashes():
    """v16: 没测过速度时返回空 dict, 不能把主流程带崩。"""
    got = cs.load_speed_rank()
    assert isinstance(got, dict)
    assert all(isinstance(v, float) for v in got.values())


def test_first_line_must_be_domestic_fast():
    """v16 端到端验收: 同一个台的 5 条候选, 排序后第一条必须是国内最快的。
    电视默认播第一条, 而**卡顿不会触发自动换线** —— 第一条慢, 后面再快也白搭。
    注意境外机器测出的 ms 恰好是反的: 美国 VPS 180ms 最快, 国内源 700~900ms。"""
    def stab_key(x):
        ms_tier = 0 if x['ms'] < 1500 else (1 if x['ms'] < 3000 else 2)
        return (-x.get('aff', 0), ms_tier, -(x['w'] * x['h']),
                -(x.get('kbps') or 0), x['ms'])
    geo = {'39.134.115.163': 'CN', '58.248.112.205': 'CN',
           '107.150.60.122': 'US'}
    isp = {'39.134.115.163': 'CMCC', '58.248.112.205': 'CUCC'}
    ent = []
    for u, ms in (('http://107.150.60.122/live/cctv1hd.m3u8', 180),
                  ('http://101.35.240.114:88/live.php?id=CCTV1', 300),
                  ('http://58.248.112.205:8006/GD_CUCC/G_CCTV-1.m3u8'
                   '?Authinfo=x', 900),
                  ('http://39.134.115.163/live/cctv1.m3u8', 800),
                  ('http://jxcbn.ws-cdn.gitv.tv/hls/CCTV1HD/index.m3u8', 700)):
        ent.append({'url': u, 'w': 1920, 'h': 1080, 'method': 'ffmpeg',
                    'ms': ms, 'kbps': 3000,
                    'aff': cs.cn_affinity(u, geo, isp)})
    ent.sort(key=stab_key)
    assert 'gitv.tv' in ent[0]['url'], ent[0]['url']
    assert '39.134.115.163' in ent[1]['url'], ent[1]['url']
    # 沉底的两条: 境外 VPS(-4) 与 php 转发(-5)。php 更差 —— 一台小机扛 10 个台
    tail = [e['url'] for e in ent[-2:]]
    assert any('107.150.60.122' in u for u in tail), tail
    assert any('101.35.240.114' in u for u in tail), tail
    # 反向确认: 境外机器眼里最快的 180ms 那条, 绝不能出现在第一条
    assert ent[0]['ms'] == 700 and 'gitv.tv' in ent[0]['url']


def test_should_drop_by_speed():
    """v16: 空名台剔除。点 CCTV-2 跳到 CCTV-5 就是因为台打不开 —— 留个空名
    让用户点进去跳台, 比没有这个台更糟。但判定必须保守, 不能误杀好台。"""
    D = cs.should_drop_by_speed
    U = lambda *xs: [{'url': x} for x in xs]
    # ① 三条都测过、都不够用 -> 删
    assert D(U('a', 'b', 'c'), {'a': 0, 'b': 120, 'c': 250}) is True
    # ② 有一条能看 -> 不删
    assert D(U('a', 'b', 'c'), {'a': 0, 'b': 120, 'c': 900}) is False
    # ③ 有一条**没测过** -> 绝不删(数据不全, 不能下结论)
    assert D(U('a', 'b', 'c'), {'a': 0, 'b': 120}) is False
    # ④ 没测过速度(空表) -> 全部保留
    assert D(U('a', 'b'), {}) is False
    assert D(U('a', 'b'), None) is False
    # ⑤ 刚好卡在阈值上: LIVE_SPEED_OK 是"够用"的下限, 等于它不删
    assert D(U('a'), {'a': cs.LIVE_SPEED_OK}) is False
    assert D(U('a'), {'a': cs.LIVE_SPEED_OK - 1}) is True
    # ⑥ 空线路
    assert D([], {'a': 0}) is False


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith('test_') and callable(f)]
    bad = []
    for n, f in fns:
        try:
            f()
            print('  ok    %s' % n)
        except AssertionError as e:
            bad.append((n, str(e)))
            print('  FAIL  %s -> %s' % (n, e))
        except Exception as e:
            bad.append((n, '%s: %s' % (type(e).__name__, e)))
            print('  ERROR %s -> %s: %s' % (n, type(e).__name__, e))
    print('\n%d/%d 通过' % (len(fns) - len(bad), len(fns)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(_run_all())
