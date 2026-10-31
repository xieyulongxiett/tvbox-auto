# TVBox 源检测报告

检测时间: 2026-10-31 08:21 (北京时间)

## 一、直链接口检测 (tvbox.json)

| 状态 | 名称 | 接口 | 延迟 | 说明 |
|---|---|---|---|---|
| 剔除 | 📢接口软件永远免费 | `https://cdn.jsdmirror.com/gh/ouhaibo1980/tvbox@main/py/py_douban.py` | - | 连续失败 4 次: HTTP Error 400: Bad Request |
| 剔除 | 🧿我要┃吃瓜 | `https://rose.tvdog.top/51chigua.py` | - | 连续失败 25 次: 配置无法解析: https://rose.tvdog.top/51chigua.py?ac=list |

接口归集 2 | 存活 0 | 疑似 0 | 剔除 2

## 二、仓体检 (store.json, 按健康分排序)

健康分 = 站点存活占比; 含CMS接口深测(实际调用验证有真实数据+抽真实视频测广告); ext内嵌接口递归拆测; 子仓跟到孙仓级

门槛: 低于 85% 或抽测发现插广告的仓本轮剔除(下轮达标自动回来); 多仓入口不参与分数

v2: 抽测影片<720p降6分(点播画质); 首页无推荐/海报挂降分(推荐质量); v6: 惩罚分<门槛余量(0.10), 只影响排名不再等于剔除;
同内容克隆仓(不同多仓指向同一子仓)自动去重只收一次;
**v12 新增两列, 它们决定点进去会不会转圈**:
- `GH依赖` = 子资源(jar/外挂js/接口)里落在 GitHub 系(含 gh-proxy 等各类加速代理)的比例。**超过 90% 直接不进清单** —— 配置 json 几十KB, 走代理也能几秒拉到, 所以"仓能打开"; 但仓里的真实内容要另外下载, 全压在同一个慢通道上就是无限转圈。实测 gh-proxy 21KB/s, 七牛云 8.4MB/s, 差 400 倍
- `jar率` = 站点里 csp_ 打包型的占比。打包型只需下 1 个 jar, 之后全在本地跑; 外挂 js 型每开一个站都要再发 1~2 次请求
v12: 电视清单 = 我的直播 + 最多 8 个仓, 按 GH依赖升序 > 分数降序 > jar率降序 取前 8; 某仓死亡自动跌出、后面自动补位

| 状态 | 仓 | 分数 | 站点存活 | GH依赖 | jar率 | jar | 广告 | 画质/推荐 | 说明 |
|---|---|---|---|---|---|---|---|---|---|
| 保留 | 挺好分享多仓 | 入口 | 0/0 | — | 0% | — | 未发现 | — | 多仓入口(导航), 子仓已独立体检; 不收进清单 —— 多仓套多仓电视上不生效(点进去 0 站点) |
| 保留 | 📛挺好分享 | 100% | 46/46 | 0% (0/7) | 93% | 正常 | 未发现 | — |  |
| 保留 | 🚀小马线路 | 98% | 61/62 | 6% (2/34) | 65% | 正常 | 未发现 | — |  |
| 剔除 | 🚀俊佬在线 | 0% | 0/24 | 0% (0/7) | 92% | 失效 | 未发现 | — | 健康分 0% 低于门槛 85%, 本轮剔除 |
| 保留 | 🚀高天流云 | 100% | 298/298 | 100% (514/514) ✘转圈 | 24% | 正常 | 未发现 | — |  |
| 保留 | 无名聚合 | 100% | 102/102 | 0% (0/3) | 100% | 正常 | 未发现 | — |  |
| 保留 | qist大而全 | 97% | 84/86 | 92% (76/83) ✘转圈 | 49% | 正常 | 未发现 | — |  |
| 保留 | Lightconer单仓聚合 | 85% | 469/516 (深测1) | 70% (305/437) | 78% | 正常 | 未发现 | 首页无推荐 |  |
| 保留 | 小盒子单仓 | 94% | 51/54 | 0% (0/19) | 93% | 正常 | 未发现 | — |  |
| 剔除 | 小盒子4K | 13% | 7/53 | 5% (3/57) | 72% | 失效 | 未发现 | — | 健康分 13% 低于门槛 85%, 本轮剔除 |
| 保留 | 王二小 | 100% | 99/99 | 0% (0/1) | 100% | 正常 | 未发现 | — |  |
| 保留 | 肥猫 | 100% | 39/39 | 0% (0/5) | 97% | 正常 | 未发现 | — |  |
| 保留 | 老刘备 | 92% | 217/234 | 0% (0/321) | 71% | 正常 | 未发现 | — |  |
| 保留 | 创享影视 | 100% | 12/12 | 0% (0/12) | 0% | — | 未发现 | — |  |
| 剔除 | 伦理接口 | 71% | 33/43 (深测1) | 0% (0/44) | 67% | 正常 | 未发现 | 首页无推荐 | 健康分 71% 低于门槛 85%, 本轮剔除 |
| 保留 | 小盒子多仓 | 入口 | 0/0 | — | 0% | — | 未发现 | — | 多仓入口(导航), 子仓已独立体检; 不收进清单 —— 多仓套多仓电视上不生效(点进去 0 站点) |
| 保留 | 拾光多仓 | 入口 | 0/0 | — | 0% | — | 未发现 | — | 多仓入口(导航), 子仓已独立体检; 不收进清单 —— 多仓套多仓电视上不生效(点进去 0 站点) |

仓总数 17 | 保留 14 | 剔除 3 | 电视清单前8(GitHub依赖升序优先, >90% 直接不收)

### 未进入体检表的源 (1)

这些源在归集阶段就被丢弃了, 所以上面的体检表里看不到它们 —— **不是被打分淘汰, 是没能进来**:

| 源 | 地址 | 原因 |
|---|---|---|
| 饭太硬 | `http://www.饭太硬.net/tv` | 拉取/解析失败: RuntimeError: 配置无法解析: http://www.饭太硬.net/tv |


## 三、高清直播 (live.json, 频道白名单)

收录标准: 主线路 ffmpeg拉流实测 >= 720p;备用线路 >= 540p(保证有线路可换);
排序: 稳定优先(延迟分档) > 分辨率 —— 高画质但高延迟的烂线不再排第一;
拒收: IPv6线路 / 运营源(livekey·txiptv·bfgd等贴片广告线) /实测不出分辨率(例外: 该台一条实测线都没有时, 收索引可达的兜底线, 表中标"未实测") /索引广告指纹(DISCONTINUITY>=2次拼接·分片>2倍中位数的异常长段·首片>1.8倍中位数的起播贴片·ts域名分散);
v14(广告治理, 用户拍板"干净优先, 宁少勿滥"):
 ① **广告分发站整站剔除** —— 裸IP且单IP扛 >=5 个不同频道;    实测线上 107.150.60.122 一个IP扛33个台, 属"IPTV线路分发面板", 起播贴片就是它加的;
 ② **起播实测** —— 每台前 4 条候选间隔数秒抓两次索引:    MEDIA-SEQUENCE 不前进 / 出现 ENDLIST·PLAYLIST-TYPE:VOD = 预置循环的伪直播, 直接不收(静态文本看不见起播那一刻,这才是"测不出广告"的真正原因);
 ③ 静态判据改**相对倍数**(旧版">=15s 且 >2.5x中位数"在 6 秒切片下退化成"15>15"恒假, 15秒广告必然漏判);
代价: 只剩广告站线路的台会被清空 —— 宁可少, 不要打开就广告。
v12: 主力源是 live.txt(TVBox txt 格式) —— 一行一个台, 多线路用 "#" 分隔, 电视上"一台只显示一次"+播放时可切线路; live.m3u 每台只留 1 条(旧播放器兼容), live.json 保留备查。
v13: 两级探测 —— 每台全部候选先浅探(只拉索引判活, 零 ffprobe), 再对排最前的 2 条用 ffprobe 实测画质; 因此"最佳画质"列是实测值, 带 "(+N条未实测)" 表示还有 N 条备用线只做了探活。


| 频道 | 分组 | 线路数 | 高清/备线 | 最佳画质 |
|---|---|---|---|---|

直播频道 0 个 / 线路 0 条

### 直播上游健康表

| 状态 | 上游 | 命中白名单 | 总条数 | 备注 |
|---|---|---|---|---|

EPG 节目单: `http://epg.51zmt.top:8000/e.xml`

电视端多仓入口 (gh-proxy 加速, 推荐):

```
https://gh-proxy.com/https://raw.githubusercontent.com/xieyulongxiett/tvbox-auto/main/store.json
```

电视端单仓入口 (直链+直播):

```
https://gh-proxy.com/https://raw.githubusercontent.com/xieyulongxiett/tvbox-auto/main/tvbox.json
```

## 四、广告域名清单 (ads_pool.json)

当前状态: **仅采集未启用** —— tvbox.json 的 ads 仍为空。确认下面没有误伤(把图床/封面域名当成广告)后, 把 `ADS_ENABLE` 改成 `True`, 下一轮自动生效

判定规则: 至少 2 个不同仓同时列出的域名才算共识。单仓列出的不启用 —— 上游常有把正常图床写进 ads 的, 一启用连海报都加载不出来

### 共识域名 (2 个)

```
static-mozai.4gtv.tv, mozai.4gtv.tv
```

### 单仓列出 (63 个, 默认不启用)

```
ae01.alicdn.com, c.cnzz.com, cdn.bootcss.com, cdnjs.hnfenxun.com, cms.qdwght.com, cnzz.hhttm.top, cnzz.mmstat.com, ehwe.yhdtns.com, f5w.as12df.top, hm.baidu.com, img.mosflower.cn, img.xunzhuo123.com, iqiyi.hbuioo.com, itvba.xyz, k.jinxiuzhilv.com, k.wudejia.com, lf1-cdn-tos.bytegoofy.com, mav.mavuz.com, mc.usihnbcq.cn, mg.g1mm3d.cn, mimg.0c1q0l.cn, mscs.svaeuzh.cn, ppl.xunzhuo123.com, pv.vipwm.cc, q11.cnzz.com, qiu.xixiqiu.com, s13.cnzz.com, s19.cnzz.com, s23.cnzz.com, s3t3d2y8.afcdn.net, s4.cnzz.com, s9.cnzz.com, s96.cnzz.com, sp.chemacid.cn, stj.v1vo.top, thy.dacedsfa.cn, tips.gamevvip.com, tj.tianwenhu.com, tp.vinuxhome.com, um.cavuc.com, vip.ffzyad.com, vivovip.mmszxc.top, wan.51img1.com, wofwk.aoidf3.com, www.92424.cn, www.baihuillq.com, www.google-analytics.com, www.googletagmanager.com, www.jixunkyy.cn, xc.hubeijieshikj.cn, xdn.cqqc3.com, xg.gars57.cn, xg.huataisangao.cn, xg.whsbpw.cn, xomk.jiangjunmh.top, z1.cnzz.com, z12.cnzz.com, z2.cnzz.com, z3.cnzz.com, z5.cnzz.com  …另有 3 个
```
