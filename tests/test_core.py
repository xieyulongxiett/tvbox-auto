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
    tmp = tempfile.mkdtemp()
    olds = (cs.LIVE_JSON, cs.LIVE_M3U)
    try:
        cs.LIVE_JSON = os.path.join(tmp, 'live.json')
        cs.LIVE_M3U = os.path.join(tmp, 'live.m3u')
        lives = cs.write_live(_channels())
        assert [g['group'] for g in lives] == ['央视', '中国香港']
        assert lives[0]['channels'][0]['urls'][0] == 'http://a/1a.m3u8'
        m3u = open(cs.LIVE_M3U, encoding='utf-8').read()
        # v10: 每台写满3条备源(用户拍板"每台3个源") —— CCTV-1 有2条就写2条,
        # 凤凰只有1条就写1条; 顺序保持主线路在前
        assert m3u.count('#EXTINF') == 3
        assert m3u.count('CCTV-1 综合') == 2      # 同名多行 = 同一台的多个源
        assert 'CCTV-1 综合' in m3u and '凤凰卫视资讯台' in m3u
        assert 'http://a/1b.m3u8' in m3u          # 备线现在也进 m3u
        assert m3u.index('http://a/1a.m3u8') < m3u.index('http://a/1b.m3u8')
        # 每行 EXTINF 后面必须紧跟一个 URL(别写成 2 个台名挤在一起共用一个地址)
        assert len([l for l in m3u.splitlines() if l.startswith('http')]) == 3
        j = json.load(open(cs.LIVE_JSON, encoding='utf-8'))
        assert j['lives'][0]['channels'][0]['urls'] == [
            'http://a/1a.m3u8', 'http://a/1b.m3u8']
    finally:
        cs.LIVE_JSON, cs.LIVE_M3U = olds
        shutil.rmtree(tmp, ignore_errors=True)


def _scored():
    return [
        {'url': 'http://s0/nav.json', 'name': '多仓导航', 'kind': 'nav',
         'score': None, 'state': '保留', 'alive': 0, 'total': 0},
        {'url': 'http://s1/a.json', 'name': '完美仓A', 'kind': 'store',
         'score': 1.0, 'state': '保留', 'alive': 10, 'total': 10},
        {'url': 'http://s2/b.json', 'name': '降分仓B', 'kind': 'store',
         'score': 0.94, 'state': '保留', 'alive': 9, 'total': 9},
        {'url': 'http://s5/e.json', 'name': '完美仓E', 'kind': 'store',
         'score': 1.0, 'state': '保留', 'alive': 8, 'total': 8},
        {'url': 'http://s3/c.json', 'name': '补位仓C', 'kind': 'store',
         'score': 0.92, 'state': '保留', 'alive': 11, 'total': 12},
        {'url': 'http://s4/d.json', 'name': '死仓D', 'kind': 'store',
         'score': 0.5, 'state': '剔除', 'alive': 5, 'total': 10},
    ]


def test_write_store_selection():
    tmp = tempfile.mkdtemp()
    old = cs.STORE
    try:
        cs.STORE = os.path.join(tmp, 'store.json')
        kept = [s for s in _scored() if s['state'] != '剔除']
        cs.write_store(kept, 'owner/repo', True, [])
        cfg = json.load(open(cs.STORE, encoding='utf-8'))
        names = [e['sourceName'] for e in cfg['storeHouse']]
        # v10: 自己那份配置也进 store(ads 只在播放器直接加载的配置里生效)
        assert names[0] == '我的影视', names
        assert names[1] == '我的直播', names
        assert '完美仓A 100分' in names
        assert '完美仓E 100分' in names          # 两个完美仓都进
        # v10: 外部仓只留2个(自己占1个位置, 合计仍3个入口) —— 降分仓挤不进
        assert '降分仓B 94分' not in names
        assert '补位仓C 92分' not in names
        assert '死仓D 50分' not in names
        assert all('多仓导航' not in n for n in names)
        # 电视端地址必须带代理前缀(裸 raw 在盒子上拉不动)
        assert cfg['storeHouse'][0]['sourceUrl'].startswith(cs.GH_PROXY)
        assert cfg['storeHouse'][1]['sourceUrl'].startswith(cs.GH_PROXY)
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
        urls = [e['sourceUrl'] for e in cfg['storeHouse']]
        assert all(u.startswith('http') for u in urls), urls
        assert all('//main' not in u for u in urls), urls
        assert all('我的直播' not in e['sourceName'] for e in cfg['storeHouse'])
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
    """v9: 同样完美时, 内容多(站点多)的仓排前面"""
    tmp = tempfile.mkdtemp()
    old = cs.STORE
    try:
        cs.STORE = os.path.join(tmp, 'store.json')
        scored = [
            {'url': 'http://s/small.json', 'name': '小仓', 'kind': 'store',
             'score': 1.0, 'alive': 3, 'total': 3, 'state': '保留'},
            {'url': 'http://s/big.json', 'name': '大仓', 'kind': 'store',
             'score': 0.995, 'alive': 199, 'total': 200, 'state': '保留'},
        ]
        cs.write_store(scored, '', False, [])
        cfg = json.load(open(cs.STORE, encoding='utf-8'))
        names = [e['sourceName'] for e in cfg['storeHouse']]
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


# ---------------- 独立运行 ----------------

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
