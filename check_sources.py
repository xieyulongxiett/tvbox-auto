#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TVBox 源自动归集 + 失效检测 + 仓体检 + 高清直播生成

流程:
  1. sources.txt:
       @订阅行  = 别人的点播配置(进仓体检, 子仓跟到孙仓)
       手动行   = 苹果CMS直链接口(进接口检测)
       L|上游行 = 直播源m3u(进直播检测)
       D|目录仓 = 从目录仓 resources.json 自动发现新点播仓
  2. 接口检测: 连通 -> 分类/数据有效 -> 抽一个视频验证播放地址可达
     -> 再测一次搜索(ac=detail&wd=); 搜不到的接口不标 searchable
   3. 仓体检: 解析每个仓的全部站点, 逐个测依赖(jar/脚本/接口/网页)可达,
      算健康分; 低于 STORE_MIN_HEALTH 的仓一轮即踢(下轮达标自动回来);
      **v12 新增通道画像**: 纯文本统计子资源(jar/外挂js/接口)落在 GitHub 系
      (含 gh-proxy 等加速代理)的比例, 超过 STORE_GH_MAX 直接不收 —— 这类仓
      "能加载, 点进去一直转圈", 健康分再高也不能上电视;
      电视清单 = 我的直播 + 最多 STORE_TOP_N 个仓, 按 GitHub依赖升序 >
      分数降序 > jar率降序 取前 N; 仓死了自动跌出、别的仓自动补位
  4. 直播检测: 按频道白名单(央视/卫视/中国香港/湖南)从上游m3u归集候选,
     候选按上游轮转取样, 逐条拉流实测分辨率(带UA/Referer), >=720p 才收;
     每频道按画质排序保留前 LIVE_KEEP 条 -> live.txt(主力: TVBox txt 格式,
     一行一个台, 多线路用 "#" 分隔, 电视上"一台一次"+播放时可切线路) /
     live.json(OSC多线路, 备查) / live.m3u(每台1条, 旧播放器兼容),
     并作为"我的直播"仓进 store.json;
     某台一条实测线都没有时, 收"索引可达但测不出分辨率"的兜底线(标未实测)
  5. 熔断: 本轮接口数或频道数比上一版腰斩 -> 不覆盖 tvbox/store/live,
     只更新 report 并报警(防"全网抖动那天把能用的配置冲成空")
  6. 结果写入 report.md; 失败计数写入 dead_log.json (防误杀)
  7. --gitee 参数: 把结果同步到 Gitee 仓库 (需 GITEE_TOKEN / GITEE_REPO)

产物:
  tvbox.json          单仓入口(精选直链接口 + 直播 + ads 广告域名)
  store.json          多仓入口(我的直播 + 最多3个"点进去真能用"的仓)
  live.txt            直播主力源(TVBox txt: 台名,线路1#线路2#线路3)
  live.json / live.m3u 直播备用(OSC多线路 / 通用m3u, 每台1条)
  multi/mN.json       导航清理版多仓(v12 起不再进 store 清单, 仅产物备查)
  ads_pool.json       广告域名采集清单(供人工过目后决定是否启用)
  report.md           本轮体检报告(GH依赖 / jar率 两列直接标出会转圈的仓)
  dead_log.json       连续失败计数(防误杀)

电视端入口(地址永远不变):
  多仓入口 store.json: 我的直播 + 最多3个外部仓(GitHub依赖低的优先)
  单仓入口 tvbox.json: 精选直链接口 + 直播 + ads 拦截

v3 (2026-10-26): 导航仓重建清理版(multi/mN.json) —— 只装体检合格的子仓,
  死子仓不再拖累整仓(电视加载导航不再弹"拉取配置失败");
  香港台维持 v2 砍法(凤凰中文/凤凰资讯/RTHK32); 检测频率维持每天一次
v4 (2026-10-26): 电视清单一票否决制 —— 完美仓(100%存活)优先凑前3,
   不足时>=90分仓补位; 清理版多仓按最低子仓分算1个仓同台竞争(同为完美
   时多仓优先, 自带每日重建容错); 删除"自动优选·直链检测"条目
v5 (2026-10-27): 直播广告治理 + 每台单线路 ——
   ① 运营源直接拒收(用户拍板): livekey/streamid/txiptv/bfgd/tsfile 等
      URL特征 + 索引广告指纹(DISCONTINUITY拼接/15s+长段/首片超长起播贴片)
      —— 2026-10-27 家中实看每台插广告实锤, 换血后运营线放量所致
      (注: 长段/拼接判定 v6 已放宽为相对阈值, 见下⑦)
   ② live.m3u 每台只输出排序第1的最佳线路(部分播放器不合并同名多行,
      显示成"每台3个重复频道"); 备线全保留在 live.json(OSC多线路)
v6 (2026-10-27): 代码评审修复(12处) ——
   ① 凤凰卫视资讯台正则补"卫视"二字(原模式凤凰资讯永远匹配不上,
      文档声称保留的台其实从没进过清单);
   ② 直播候选按上游轮转取样(原来取插入序前16条 = 全是排第一的Guovin旧线,
      vbskycn 活池的线路连实测机会都没有);
   ③ 点播广告关键词加词边界(原 ad[s]? 会命中 load/download/broadcast/
      shadow 里的"ad", 误判整仓插广告 → 直接剔除);
   ④ 惩罚分降到门槛余量以内(0.15/0.12 > 余量0.10, "降分"实际等于"剔除");
   ⑤ parse_m3u 支持 #EXTVLCOPT / #KODIPROP 的 UA 与 Referer;
   ⑥ ffprobe/ffmpeg 真正带上 UA/Referer + rw_timeout(原来收下ua却不用);
   ⑦ 索引广告指纹改相对判定(单次DISCONTINUITY多为重连、均匀20s慢切片不误杀);
   ⑧ 依赖探测失败重试一次(一天只跑一轮, 抖动不该判死);
   ⑨ 推荐质量检测并入线程池(原来在主线程串行, 每仓6个请求白等);
   ⑩ FLAGS 的 mgtj 纠正为 mgtv(芒果);
   ⑪ dead_log.json 每轮按当前接口池裁剪(否则只增不减, 天天提交把仓库喂胖);
   ⑫ --sources 缺参数不再 IndexError

v7 (2026-10-27): 工程质量修复(结构防御 + 去重 + 可测试) ——
   ① 不可信配置零防御 → 补齐: 新增 as_list/as_dict/as_str + fetch_cfg,
      仓配置的 sites/storeHouse/urls/spider/ext 任意畸形(sites 是 dict、
      元素是字符串、storeHouse 是字符串)只跳过该项, 不再 AttributeError
      让整轮归集崩掉(崩了当天不出新配置, 电视端一直吃旧配置);
   ② 阶段级兜底: 归集/体检/直播任一阶段异常都用空数据继续出产物,
      顶层再兜一层, 异常打堆栈 + 非零退出(半成品不会被 commit);
   ③ 重复代码收口: cms_query(ac=list→videolist 取列表, 原4处)、
      first_play_url(播放地址正则, 原4处)、ts_domain_spread(ts域名分散,
      原2处逐字相同);
   ④ 用显式 kind('store'/'nav') 取代"score is None 即导航仓"的隐含约定;
      顺带修掉"无站点的空仓被当导航仓保留"的漏洞(现在判 0 分剔除);
   ⑤ 代理/raw 域名集中为常量(原来 gh-proxy 等硬编码 14 处);
   ⑥ norm_store_url / store_fingerprint 从内层闭包提到模块级(纯函数可测);
   ⑦ dead_log 的接口与仓两份记录都按当轮池裁剪, 写文件前键排序(减少无谓 diff);
   ⑧ 清理: import hashlib 归位到顶部; write_live 去掉从未使用的 repo 参数;
      collect_live 里与版本号撞名的局部变量 v6 改名 ipv6_cnt; type 兼容 "3";
   ⑨ 新增 tests/test_core.py: 20+ 项纯函数断言(频道匹配/广告指纹/m3u解析/
      轮转取样/结构防御/惩罚分), 本地 1 秒跑完, 不必推 GitHub 等 10 分钟

v8 (2026-10-27): 直播链路补强 ——
   ① (已撤回) 曾补 7 个卫视; 用户明确"白名单是确定好的, 不要主动增加",
      已全部还原, 频道数回到 59。白名单范围属于用户决策, 不由脚本扩;
   ② "索引合法但测不出分辨率"的线原来是静默丢弃(日志都没有)。Actions
      runner 在境外, 国内线路常"能开索引、拉不动分片", 这类线家里可能能看。
      新增兜底层: 只有该台一条实测线都没有时才收(标 method=unverified),
      绝不挤占实测通过的线, report 里标"未实测"并统计条数;
   ③ 直播探测连接异常重试一次(点播依赖 test_dep 早有重试, 这里却一次就否,
      标准不一致)。只重试连接异常, 403/404 这类确定性失败不重试。
      兜底判定抽成 pick_live_urls() 便于单测;

v9 (2026-10-27): 对齐"真正在电视机上发生的事情"(依据均为实测, 非推测) ——
   问题: 之前所有检测都在测"源活着吗", 而用户在电视上遇到的是"没节目单/
   搜不到片/某天突然全空"。这一版补的是后半段:
   ① EPG 台名对齐: 实测 51zmt 的 e.xml(100频道) —— EPG 里叫 "CCTV1",
      我们输出 "CCTV-1 综合", 归一化后 cctv1综合 ≠ cctv1, 19 个央视台
      **精确匹配全部失败**, 电视上一直没有节目单。现按台映射 tvg-id/
      tvg-name(海南卫视在 EPG 里还是旧名"旅游卫视", 凤凰→凤凰中文/资讯)。
      54 个台对得上, 5 个 EPG 里确实没有的(湖南经视等)不写 tvg, 避免错配;
      **频道范围不变, 仍是 59 个**;
   ② 搜索纳入检测: searchable 一直是硬写的 1, 从没验证过。实测抓到
      "首页能读 20 条、ac=detail&wd= 却报错"的接口, 这种进了清单就是
      "能打开仓却什么都搜不到"。现在 check_one 多测一次搜索, 搜不到
      (连得上但返回空)的不标 searchable; 网络异常算"不确定", 不误判;
   ③ 熔断: 原先无条件覆盖产物 —— 全网抖动那天会写出空 store.json 并
      commit, 电视第二天拉不到任何仓(这是唯一能让设备变砖的路径)。
      现在本轮接口数或频道数比上一版**腰斩**就保留旧文件, 只更新 report
      并在顶部报警; 熔断时不累加失败计数(本轮结果不可信);
   ④ 大仓容错: 健康分=存活/总数, 完美线 100%。200 站的仓死 1 个=99.5%,
      永远够不到完美线, 竞争不过 3 站全活的小仓 —— 而大仓才是内容最多的。
      现在 >=30 站的仓允许死 1 个; 完美组内按内容量(站点数)优先;
   ⑤ 直播码率: 1080p 但只有 800kbps 的"假高清"按分辨率排序会排第一,
      画面反而不如 720p@3M。现在解析 master 的 BANDWIDTH, 每千像素
      低于 LIVE_MIN_BPP 的降到备用层; 取不到码率的线不受影响(不猜测);
   ⑥ 点播站点插广告由"整仓剔除"改"扣分"(1站扣4分, 最多扣8分):
      个别站点插广告不等于整仓不能用, 与 v6 把降分改回真降分同源。
      测试 28 → 36 项

v10 (2026-10-27): 按用户提的六条要求逐条补 ——
   用户原话: 台不要多(央视1-16 / 31省卫视 / 香港 / 湖南), 中文, 清晰度高,
   不能插广告, 点播仓和线路不要太多, 全部自动更新。
   ① ads 广告域名拦截(最大的缺口): tvbox.json 的 'ads' 一直是写死的空数组,
      而上游每个仓都带了自己维护的广告域名黑名单 —— 解析了配置却从不取。
      现在在 fetch_cfg 里顺手采集, 只取**多仓共识**(>=ADS_MIN_VOTES 个不同
      仓都列出)才可信, 单仓列出的可能有误伤(把图床当广告)只进清单。
      按用户要求"先看清单再启用": ADS_ENABLE 默认 False, 清单落在
      ads_pool.json + report 第四节, 确认后改 True 才写进配置;
   ② 自己那份配置进 store: 用户用 store.json 入口, 而 ads 只在"播放器直接
      加载的那份配置"里生效, 3 个外部仓的 ads 由别人决定。现在 store.json
      第一项就是"我的影视"(=tvbox.json), 广告拦截才真能生效。外部仓
      3→2(STORE_TOP_N), 入口总数仍是 3 个;
   ③ tvbox.json 站点上限 MAX_SITES=16: 原先是"存活几个写几个"(几十个),
      电视上翻不完。截断时搜索不通的先淘汰 —— 能浏览但搜不到片的接口
      留着只占位置;
   ④ live.m3u 每台写满 3 条(v5 曾只写第1条): 用户拍板"每台3个源",
      主线路卡住当天还有得换, 不必等第二天更新。代价是少数魔改播放器
      会把一台显示成多个同名频道;
   ⑤ 台单维持 59 个不动(用户确认"共59个"): 央视1-17+4K+5+ / 32卫视
      (31省+深圳) / 香港3 / 湖南5。只加质量门槛, 不增不减;
   ⑥ 测试 36 → 39 项

v12 (2026-10-27): 两件事 —— 直播换 txt, 点播换选源标准
   [一] 直播源格式(只在电视端实测暴露的问题)
   用户电视端是影视仓 V5.0.31, 实测结论(截图+文件双重确认):
   ① v10 的 live.m3u 每台写 3 条 -> 频道列表里一台变成 3 个同名台
      (线上文件实测 146 条 EXTINF / 52 个独立台名 / 49 个台各重复3次);
      根因: m3u 语法没有"一个台多条线路"这种结构, 同名多行只能是多个台;
   ② live.json(OSC 风格 {"lives":[{group,channels:[{name,urls}]}]}) 在
      影视仓上不认, 填进去加载失败 —— 这个格式本就不是通用标准;
   ③ 正解是 TVBox 的 txt 格式(type 0): "台名,地址1#地址2#地址3",
      播出列表只显示一个台, 播放时按线路切换。新增 live.txt 作主力源;
      m3u 退回每台 1 条(LIVE_M3U_KEEP)只做旧播放器兼容, json 保留备查;
   ④ store.json 的"我的直播"改指 live.txt(原来指 live.json, 点不开);
   ⑤ 生成 txt 时对台名逗号、地址 "#" 做兜底清理, 防上游数据把行切错

   [二] 点播选源标准(用户连续两轮质疑后查实的根因, 推翻了此前所有假设)
   用户电视实测 "第1步(gh-proxy/高天流云)能进仓但一直转圈, 第2步(七牛云/
   无名聚合)几秒就进去"。同一台电脑同时段实测通道速度:
     gh-proxy.com 拉 65KB drpy2.min.js = 3.13s(21KB/s); 2.68MB jar 约130s
     七牛云        拉 3.94MB jar        = 0.47s(8.4MB/s)  —— 快 400 倍
   决定性差异不是 jar 好坏(两个 jar 都实测 md5 匹配), 而是**子资源依赖模式**:
     高天流云 298 站: 227 站 api=./lib/drpy2.min.js + 287 站 ext=./js/*.js,
       相对路径全落在 raw.githubusercontent -> 每开一个站再发 1~2 次请求,
       实测子资源 514 个, **100% 在 GitHub 系** -> 必然转圈;
     无名聚合 104 站: 全部 csp_ 打包进 jar, 子资源仅 3 个, **0% 在 GitHub**
       -> 下 1 次 jar 后全本地, 秒开;
     挺好分享 46 站: jar 93%, GitHub 依赖 0%, jar 与配置同域(ztha.top)
       -> 秒开。这正是用户说"只有挺好分享能用"的原因。
   ① 新增 cdn_profile(): 纯文本统计子资源(GitHub 依赖率 / jar 打包率),
      **零网络请求** —— 云端"能不能访问"不可靠(gitee 对脚本请求回 423、
      git.acwing.com 返回登录页、裸 UA 被大量站点 403), 用它当判据会误杀;
      而"地址落在哪个域名"写死在配置里, 100% 可判定;
   ② 新增硬门槛 STORE_GH_MAX=30%: 超过直接不进清单, 健康分再高也不收。
      v10 只看健康分, 于是高天流云(298站/99% GH依赖)拿满分排第一位 ——
      分数骗人, 那正是"能进仓、点开转圈"的来源;
   ③ STORE_TOP_N 2 -> 3(用户拍板"3个就够, 但必须能看");
      排序改为 GitHub依赖升序 > 分数降序 > jar率降序;
   ④ 空仓(sites=0)不挂进清单: v10 无条件挂 tvbox.json 作"我的影视",
      而 sources.txt 里直链接口一条都没配 -> 恒为空壳 -> 电视上一个红点 +
      "接口无效 No value for uri"。现在 mine_sites>0 才挂;
   ⑤ 多仓整棵展开成独立子仓各自体检(SUB_LIMIT 25->40, 总量 40->100):
      挺好分享多仓 GYCK.json 一家 32 个子仓, 原来 25 会漏 7 个, 总量 40
      会被它一个吃光。现在"一行多仓 = 几十个候选", 只留合格的进清单;
   ⑥ 导航清理版(multi/)停止参与 store 竞争: 功能与"多仓展开自动筛"重复,
      且文件在自己仓库(gh-proxy 通道), 光加载就要几秒, 展开后又是一堆仓;
   ⑦ 极端兜底: 全部仓都超标时放宽取最接近的并在报告标警告 ——
      空清单等于点播全废, 比慢一点更糟;
   ⑧ report.md 仓体检表新增 `GH依赖` / `jar率` 两列, 超标的直接标 ✘转圈

v13 (2026-10-27): 直播深探分级 —— 把"没验证就交出去"这条补上
  起因: v12 首次上线后核对 report.md, 133 条直播线路 **100% 标"未实测"**。
  追下去是 runner 上 ffprobe 根本不存在(ubuntu-latest 镜像已不预装),
  apt 又装不上(240 秒超时被掐), imageio-ffmpeg 只给 ffmpeg 不给 ffprobe。
  后果不是"画质差一点", 而是**这一轮 52 个台 133 条线, 谁死谁活、是不是
  720p, 一条都没验过**, 全靠索引里那个地址蒙对 —— 电视上放不出来才知道。
  ① workflow: Ensure ffmpeg 改为先取 BtbN 静态构建(GitHub Release 144MB,
     runner 在内网几秒下完, 自带 ffmpeg + ffprobe), 失败才退回 apt(120秒),
     两条路都不通仍降级出配置; 新增 Probe selftest 一步, 拿一条已知线真测
     一次分辨率, 日志里出现 "1920,1080" 才算这轮深探是真的;
  ② 浅探/深探两级(方案C, 用户拍板"只实测每台第一条, 备用线只探活"):
     候选上限是 52 台 × 16 = 832 条, 每条 ffprobe 最长 12 秒, 全量深探能把
     job 拖到超时。改成第一轮全量只拉索引判活(零 ffprobe), 第二轮每台只对
     浅探排最前的 LIVE_DEEP_PER_CH(=2) 条测真画质;
  ③ pick_live_urls 放开补位: 实测线不足 LIVE_KEEP 时用探活通过的线补满,
     否则每台会退化成 2 条线; 补位只在不够数时发生, 从不挤掉已实测的线,
     并把补了几条如实写进 report(标"未实测补位");
  ④ PROBE_AVAILABLE: 探测工具真不可用时直接跳过深探, 不再空排一轮队;
  ⑤ 测试 48 → 51 项(深探配额 / 浅探不碰 ffprobe / 未实测补位)

v13 补丁 (2026-10-28): 点播"接口无效"的真凶是多仓格式, 不是源
  实锤链: 3 个入选仓的配置/jar 从电脑端全部秒开且 MD5 一致(v10 与 v12 的
  store.json 条目一模一样, v10 时代直接填地址能看) → 同一个仓(挺好分享
  thdjk.json)在 GYCK.json 的 {"urls":[{"url","name"}]} 格式里电视能选开,
  在本仓 {"storeHouse":[{"sourceName","sourceUrl"}]} 格式里列表能显示、
  选中即"接口无效" → 影视仓 V5.0.31 对 storeHouse 只展示不兼容。
  ⑥ store.json 与 multi/m*.json 产出从 storeHouse 改为 urls 格式
     (饭太硬/肥猫/GYCK 通用主流写法), 仓的内容与排序逻辑零改动

v14 (2026-10-28): 直播起播广告 —— 不是"删不掉", 是判据看错了地方
  用户报"很多台要先播广告才是正片", 并判断"这不是解决不了, 是代码没写对"。
  复核线上 143 条线路后, 这句话被三组数据证实:
  ① AD_URL_PATS 命中率 **0/143** —— 规则写的是 livekey= / bfgd / txiptv
     这类参数特征, 实际线路是裸 IP + /live/cctv1hd.m3u8 或 /gslb/dsdqbv/…,
     一条都不含。写了拒收, 实际从没拦下过一条;
  ② 91.6%(131/143)的线路是**裸 IP**, 官方 CDN 只有 2 条; 且 107.150.60.122
     一个 IP 承载 **33 个不同台**, 63.141.230.178 承载 25 个, 38.75.136.137
     承载 24 个 —— 正规 CDN 绝不这样部署, 这是"IPTV 线路分发面板", 靠起播
     15 秒贴片变现, 起播广告就是它加的;
  ③ 静态判据卡在数学边界: 要求 分片>=15s **且** >2.5x中位数, 而直播切片
     普遍 6 秒 —— 2.5x6=15, 判断退化成 "15 > 15" = False, **15 秒广告
     必然漏判**(10s/12s 起播贴片同样漏)。
  改动(用户拍板"干净优先, 宁少勿滥" + "加起播实测"):
  ① 新增 LIVE_AD_HOST_CH=5: 裸 IP 且承载>=5 个不同频道 -> 判广告分发站,
     整站线路剔除。只对**裸 IP**生效 —— 域名型 host 扛多个台可能是省级 IPTV
     平台或广电公开 CDN(实测河北广电 event.pull.hebtv.com 就扛 20 个台),
     一起判会整片误杀;
  ② 新增起播实测: 每台前 LIVE_START_CHECK(=4) 条候选, 间隔数秒抓两次索引,
     比对 MEDIA-SEQUENCE —— 真直播滑窗必前移, 不前进 / 出现 EXT-X-ENDLIST
     或 PLAYLIST-TYPE:VOD 就是"预置一段片子循环播"的广告垫片, 直接不收。
     这才是"测不出起播广告"的真正原因: 抓一次索引时滑窗早已滑过去, 起播
     那一刻根本不在文本里。两阶段抓取中间**全局只等一次**, 不是每条各等一次;
  ③ 静态判据改相对倍数: 分片 >2.0x中位数 且 >=12s; 首片 >1.8x中位数 且
     >=9s。绝对秒数只做下限(防 1~2 秒切片被倍数判据误伤);
  ④ 熔断加 cand_ch: 主动剔除会让出线台数从 52 腰斩到 21, 拿淘汰后的数去比
     每轮都会被误判成"上游挂了"从而拒绝写文件 —— 改用"剔除前的候选台数"
     判上游抖动, 并保留"全被剔干净(0 个)"的兜底拦截;
  ⑤ report 新增「广告治理」小节: 哪个 IP 被判分发站、剔除多少条、哪些台
     因"宁少勿滥"被清空, 全部写清楚 —— 否则用户只会看到"台少了";
  ⑥ 测试 51 → 56 项(判据边界回归 / 裸IP识别 / 分发站判定 / 两次快照比对 /
     熔断区分主动剔除)

v15 (2026-10-28): 按"机器在哪"分流 —— 治"干净源进不来"的根 -
  用户在电视上实测: v14 之后广告照样很长(30 秒到几分钟), 不是 15 秒贴片。
  查下去发现问题不在判据严不严, 而在**源池被广告站占满, 干净源一条进不来**:
  ① 上游 Guovin 池 1616 条里, 那 5 台境外 VPS 占 153 条, 官方域名只有 5 条;
  ② 干净的源其实是有的 —— iptv-org 的 cn.m3u 里 39.134.115.163(中国移动,
     22 个台)、183.207.248.71(21 个台)、112.25.48.68(14 个台) 全是省网 IPTV,
     一条广告没有;
  ③ 但它们**全部被剔除了**: 检测跑在 GitHub 的境外机器上, 大陆运营商地址
     连不上, 一律判死链。能连上的只有境外 VPS —— 于是"干净的进不来,
     有广告的出不去", 台数越剔越少, 广告一条没少。
  关键认识: "一个 IP 扛几十个台"本身分不出好坏。中国移动的省网 IPTV 一台
  扛 22 个台是标准部署; 美国 Nocix 的 VPS 扛 30 个台才是卖线路的面板。
  同一个数字, 两种结论, 唯一的区分依据是**这台机器在哪**。v14 不分归属
  一律砍, 等于把干净源和广告站一起枪毙。
  改动:
  ① 新增 IP 归属查询 annotate_geo(): 只查裸 IP, 域名直接判 DOMAIN。
     查询链 ipwho.is -> ipinfo.io, 失败记 '?' 且**不写缓存**(下轮重试,
     免得一次抖动把干净 IP 永久钉成未知)。缓存落 ip_geo.json,
     LIVE_GEO_TTL_DAYS=30 天有效, 随仓库提交, 第二轮起基本零请求;
  ② pick_ad_hosts 加归属: 裸 IP 且扛>=5 台 且 **归属不是 CN** 才判广告站。
     大陆运营商源豁免, 并在日志与 report 里单独列出"豁免了哪几个";
  ③ 大陆盲源补位: 浅探失败时, 大陆/域名源不再直接丢, 而是收进 cn_blind。
     出线实测线不足 LIVE_KEEP 时用它补(每台最多 LIVE_CN_BLIND_KEEP=3 条),
     已有实测线时一条不挤。理由: 境外机器连不上大陆源是**地理问题**,
     不是源坏了; 宁可给一条"可能不通但一定没广告"的线, 也不给一条
     "一定通但先播 30 秒广告"的线 —— 不通还能切下一条, 广告只能干等;
  ④ report「广告治理」升级: 增列"归属豁免"和"大陆盲源补位",
     让用户看懂"为什么某个 IP 扛 22 个台却没被砍";
  ⑤ 测试 56 → 61 项(归属判据 / 未知归属 / 干净源识别 / 盲源补位不挤实测线)

用法:
  python check_sources.py [--sources 路径] [--gitee]
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
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
# v12: TVBox txt 直播格式(type 0) —— 唯一能同时做到"一台只显示一次"和
# "播放时可切多条线路"的格式。m3u 无多线路语法(同名多行=多个台),
# json(OSC) 在影视仓 V5.0.31 上实测不认, 故以 txt 为主力源。
LIVE_TXT = os.path.join(HERE, 'live.txt')
REPORT = os.path.join(HERE, 'report.md')
DEADLOG = os.path.join(HERE, 'dead_log.json')
ADS_POOL = os.path.join(HERE, 'ads_pool.json')   # v10: 广告域名清单(供人工过目)
GEO_CACHE = os.path.join(HERE, 'ip_geo.json')     # v15: IP归属缓存(跨轮复用)

# ---- v7: 代理与 raw 域名集中定义 ----
# 原来 gh-proxy.com 在 http_get / tv_url / norm_store_url / write_report 里
# 各写一遍(共 14 处), 换镜像域名要改遍全文件; 现在只改这里
RAW_HOST = 'raw.githubusercontent.com'
GH_PROXY = 'https://gh-proxy.com/'
GH_PROXY_ALT = 'https://ghproxy.net/'
GH_MIRRORS = (GH_PROXY, GH_PROXY_ALT)
PROXY_PREFIXES = (GH_PROXY, GH_PROXY_ALT, 'https://ghfast.top/',
                  'https://mirror.ghproxy.com/', 'https://cdn.jsdelivr.net/gh/')

TIMEOUT_API = 10      # 接口请求超时(秒)
TIMEOUT_PLAY = 8      # 播放地址抽测超时(秒)
TIMEOUT_DEP = 8       # 仓依赖检测超时(秒)
TIMEOUT_LIVE = 8      # 直播流拉取超时(秒)
TIMEOUT_PROBE = 12    # ffmpeg/ffprobe 画质探测超时(秒)
FAIL_LIMIT = 2        # 接口连续失败次数达到该值才剔除
STORE_MIN_HEALTH = 0.9   # 补位仓门槛: 90分(凑不齐完美仓时用它补位)
STORE_PERFECT = 1.0      # v4: 完美仓线=100%(一票否决: 死1个地址整仓出局)
STORE_FAIL_LIMIT = 1     # 一轮即踢(每轮从sources重新归集, 下轮达标自动回来)
STORE_TOP_N = 3          # v12: 外部点播仓留3个(用户实测拍板"3个就够了, 但必须能看")
                         # v10 曾砍到2, 结果清单里只剩两个重依赖仓 + 一个空壳,
                         # 电视上 4 个源有 3 个打不开 —— 少不等于精, 能看才算

# ---- v12: 子资源通道门槛(纯文本判定, 零网络请求) ----
# 病根: 仓"能不能加载"和"点进去能不能用"是两件事。配置 json 通常几十KB,
# 走 gh-proxy 也能在 1~3 秒拉到, 所以仓看着是"活的"; 但仓里的真实内容
# (spider jar / 站点级 js / lib/*.js) 也要下载, 那些动辄几MB、几十上百个,
# 全压在同一个 21KB/s 的通道上 —— 于是"能进仓, 一直转圈"。
# 实测(2026-10-27, 同一台电脑同一时段):
#   gh-proxy.com 拉 65KB 的 drpy2.min.js = 3.13s(21KB/s), 拉 2.68MB jar 约130s
#   七牛云 拉 3.94MB jar = 0.47s(8.4MB/s) —— 快 400 倍
# 三个真实样本的指标完全分离, 中间没有过渡区:
#   挺好分享 46站  jar率93%  GitHub依赖  0%  -> 电视秒开
#   小马     62站  jar率65%  GitHub依赖  6%  -> 云端0.36s
#   高天流云 298站 jar率24%  GitHub依赖 99%  -> 电视转圈
STORE_GH_MAX = 0.30      # GitHub 系子资源占比上限: 超过即不入选(站点再多也不收)
GH_HOSTS = ('raw.githubusercontent.com', 'raw.github.com', 'github.com',
            'githubusercontent.com', 'github.io', 'githubassets.com')
# 各类 GitHub 加速代理: 它们转发的内容仍然来自 GitHub, 速度同样受限,
# 且会按 IP/频率拦截(gitee 实测返回 423), 所以一律按 GitHub 系计
GH_PROXY_HINTS = ('gh-proxy.com', 'ghproxy', 'ghfast', 'moeyy.xyz',
                  'ghapi.', 'kkgithub.com', 'hub.fastgit', 'gitclone.com')
MAX_WORKERS = 12
# v10: tvbox.json 的站点数上限。原先是"存活几个写几个", 几十个站在电视上
# 翻不完; 用户要求"线路不要太多"。搜索不通的排到后面, 优先被截断的是它们。
MAX_SITES = 16
FLAGS = ["qq", "iqiyi", "qiyi", "youku", "sohu", "letv", "mgtv", "bilibili"]

# ---- v2: 点播画质抽测(点播也要高清) ----
VOD_QUALITY_SAMPLE = 2   # 每仓抽几个CMS接口实测影片分辨率
VOD_HD_MIN_H = 720       # 高清线: 高度<此值则降分
VOD_QUALITY_PENALTY = 0.06  # v6: 0.15->0.06 画质不达标的降分幅度
# 注意: STORE_MIN_HEALTH=0.90 => 存活余量只有0.10, 惩罚必须<0.10,
# 否则"降分"实质等于"一票否决剔除"(v5 的 0.15/0.12 就是这么误杀的)

# ---- v2: 首页推荐质量检测(首页推荐内容质量优化) ----
REC_QUALITY_PENALTY = 0.05  # v6: 0.12->0.05 首页空/海报挂 的降分幅度

# ---- v9: 点播站点插广告改为扣分(原来是一票否决整仓剔除) ----
# 一个站点插广告不代表整个仓不能用; 直接剔除会让内容最多的仓因为个别
# 站点出局 —— 与 v6 把"降分"改回"真降分"是同一个道理
AD_HITS_PENALTY = 0.04       # 每个插广告站点扣分
AD_HITS_PENALTY_MAX = 0.08   # 最多扣这么多(3个站点以上扣满, 跌破门槛才剔除)

# ---- v10: 播放器级广告域名拦截(ads 字段) ----
# 病根: tvbox.json 的 'ads' 一直是写死的空数组。上游每个仓的配置里都带了
# 自己维护的广告域名黑名单, 我们解析了配置却从不取这个字段 —— 等于白拿不用。
# ads 是 TVBox 播放器级的域名拦截, 拦的是网页播放页里的弹窗/贴片域名,
# 与"选哪个站"(已在做)是两层, 补上这层才能真少看到广告。
# 但上游可能乱写(把图床域名塞进去会连海报一起拦掉), 所以只取**多仓共识**:
# 至少 ADS_MIN_VOTES 个不同仓都列了才认为可信, 单仓列出的只进清单待人工确认。
ADS_ENABLE = False       # 用户要求"先看清单再启用"; 看完确认后改 True
ADS_MIN_VOTES = 2        # 至少几个仓同时列出才算共识
ADS_MAX = 200            # 写进配置的条数上限(防某个仓库塞几千条拖慢播放器)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/126.0.0.0 Safari/537.36'}

# ---- 直播参数 ----
LIVE_MAX_CAND = 16    # 每频道最多实测候选地址数(v2: 8->16 吃满大池)
LIVE_KEEP = 3         # 每频道保留主线路数(高清层)
# v13(方案C): 深探配额。2026-10-27 那轮 report 里 133 条线路 100% 标"未实测"
# —— runner 上 ffprobe 缺席, 画质和存活实际一条都没验过, 全靠索引里的地址
# 蒙对。修好 ffprobe 之后也不能全量深探: 候选上限 52 台 × 16 = 832 条, 每条
# ffprobe 最长 TIMEOUT_PROBE=12 秒, 最坏情况能把 job 拖到超时。所以分两级:
#   第一轮 浅探: 全量候选, 只发 HTTP 拉索引(判活 + 广告指纹 + master 声明), 秒级
#   第二轮 深探: 每台只对浅探排在最前的 LIVE_DEEP_PER_CH 条跑 ffprobe 测画质
# 用户拍板方案C: "只实测每台第一条, 备用线只探活"。给 2 而不是 1, 是因为
# 排第一的线可能画质不达标(测出 480p), 留一条余量让这台仍能拿到实测高清线。
LIVE_DEEP_PER_CH = 2
# v12: m3u 每台只写 1 条。v10 写满3条换来的是"电视上一台显示3个同名频道",
# 而 m3u 没有多线路语法, 两者不可兼得 —— 多线路改由 live.txt 承担。
LIVE_M3U_KEEP = 1
LIVE_BACKUP_KEEP = 3  # v2: 备用层最多保留数(清晰度放宽 540p)
LIVE_MIN_W, LIVE_MIN_H = 1280, 720   # 主线路高清底线: 720p(须 ffmpeg 实测确认)
LIVE_BACKUP_MIN_H = 540   # v2: 备用线路门槛(540p), 保证有线路可换
# v9: 码率下限(每千像素的 kbps)。1080p 只有 800kbps 时画面糊得不如 720p@3M,
# 但按分辨率排序会把它排第一。只在 master playlist 声明了 BANDWIDTH 时判断,
# 测不到码率的线不受影响(不猜测)。
LIVE_MIN_BPP = 0.9
LIVE_NO_IPV6 = True   # 用户家无 IPv6: 含 IPv6 地址的线路一律不要

# ---- v5: 直播运营源拒收(广告治理, 用户拍板"直接拒收") ----
# 运营型线路特征: "免费送线路换贴片广告"(换台/起播插15-30秒广告);
# 2026-10-27 家中实看每台插广告实锤 —— 换血后 livekey/bfgd/txiptv 线放量
AD_URL_PATS = ('livekey=', 'streamid=', 'key=txiptv', 'authid=',
               '/bfgd/', '/tsfile/live/')

# ---- v14: 广告治理重做(用户拍板"干净优先, 宁少勿滥" + "加起播实测") ----
# 起因: 用户报"很多台要先播广告才播正片"。复核线上 143 条线路后实测到三条:
#
# ① AD_URL_PATS 命中率 **0/143**。规则写的是 livekey= / bfgd 这类参数特征,
#    而实际线路是裸 IP + /live/cctv1hd.m3u8 或 /gslb/dsdqbv/... —— 一条不含。
#    等于"写了拒收, 实际一条都没拦过"。
# ② 91.6% 的线路是裸 IP(131/143), 官方 CDN 仅 2 条。且 107.150.60.122 一个 IP
#    承载 33 个不同台、63.141.230.178 承载 25 个、38.75.136.137 承载 24 个。
#    一个 IP 跑三十几个央视+卫视, 这不是 CDN 部署, 是"IPTV 线路分发面板" ——
#    靠起播 15 秒贴片变现, 起播广告就是它加的。这条判据最强且零成本。
# ③ 旧静态判据卡在数学边界: 要求 分片>=15s **且** >2.5x中位数。6 秒切片时
#    2.5x6=15, 判断退化成 "15 > 15" = False —— 15 秒广告在 6 秒切片下**必然漏判**
#    (10s/12s 起播贴片同样漏)。改为相对倍数 + 下限, 见下。
#
# 另: 起播贴片发生在"按下播放那一刻", 抓一次索引看不见(那时滑窗早已滑过去)。
# 故新增起播实测: 间隔 LIVE_START_GAP 秒抓两次索引, 真直播的 MEDIA-SEQUENCE
# 必然前进; 不前进 / 出现 ENDLIST / PLAYLIST-TYPE:VOD = 循环垫片的伪直播。
LIVE_AD_HOST_CH = 5     # 裸IP承载>=5个不同频道 -> 判广告分发站, 整站线路剔除
# ---- v15: 归属地判据(治"干净源进不来") ----
# v14 的判据是"裸 IP 且扛 >=5 台 -> 剔除", 方向对但不完整: 中国移动/联通/电信的
# 省网 IPTV 源本来就是"一台服务器扛几十个台"(实测 39.134.115.163 扛 22 个、
# 183.207.248.71 扛 21 个), 它们一条广告都没有, 却被一起判死。
# 真正该看的是"这台机器在哪": 美国 Kansas City 的 Nocix、GTHost、WholeSale
# 这类廉价 VPS 扛 30 个台 = 线路分发面板; 中国的运营商 IP 扛 30 个台 = 正常。
# 更要命的是第二层: GitHub 的检测机器在境外, 国内运营商源全连不上, 被当死链丢光,
# 结果清单里只剩境外广告站 —— 台数越剔越少, 广告一条没少。
LIVE_GEO_TRUST = ('CN', 'DOMAIN')   # 大陆 IP 与域名型: 可免实测入清单
LIVE_GEO_TTL_DAYS = 30              # 归属缓存有效期(天), 过期重查
LIVE_GEO_MAX = 140                  # 单轮最多查多少个 host, 超出的沿用缓存/判未知
LIVE_CN_BLIND_KEEP = 3              # 每台最多补几条"境外测不出"的大陆源
LIVE_START_CHECK = 4    # 每台做"起播快照"的候选数(浅探后按快/高清排序取前N)
LIVE_START_GAP = 6      # 两次快照间隔(秒); 全局只等这一轮, 不是每条各等一次
# 静态贴片判据: 相对倍数为主, 绝对秒数只做下限(防 1~2 秒切片被倍数判据误伤)
AD_SEG_RATIO = 2.0      # 某分片 > 中位数 2.0 倍
AD_SEG_MIN = 12         # 且不短于 12 秒
AD_FIRST_RATIO = 1.8    # 首片 > 中位数 1.8 倍
AD_FIRST_MIN = 9        # 且不短于 9 秒

# ---- 目录仓发现(自动找点播源) ----
# D| 开头的行: 目录仓的 resources.json 地址, 自动提取其中 tvbox_config 类的
# 配置地址作为新仓候选, 体检合格即进清单 —— 点播源自动发现池
DIR_CATEGORIES = ('tvbox_config',)   # 目录仓里哪些分类算点播配置
DIR_LIMIT = 30                        # 每个目录仓最多发现新仓数
LIVE_EPG = 'http://epg.51zmt.top:8000/e.xml'

# ---- 点播深测参数 ----
SUB_DEPTH = 2          # 多仓子仓递归跟进深度(验到最后一级)
# v12: 40 -> 100。挺好分享多仓 GYCK.json 一家就有 32 个子仓, 原来 40 的
# 总量上限会被它一个吃光, 后面 sources.txt 里的单仓就再没名额了 ——
# 表现是"加了多仓反而把别的仓挤掉"。
SUB_TOTAL_LIMIT = 100
CMS_DEEP_LIMIT = 15    # 每仓最多深测的CMS接口数(实际调用验证内容)

# ffmpeg 探测工具定位(云端apt安装; 本地尝试 imageio_ffmpeg)
FFPROBE_BIN = shutil.which('ffprobe')
FFMPEG_BIN = shutil.which('ffmpeg')
if not (FFPROBE_BIN or FFMPEG_BIN):
    try:
        import imageio_ffmpeg
        FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

# v13: 探测工具是否真的可用 —— 只有 imageio-ffmpeg(有 ffmpeg 无 ffprobe)时
# ffprobe_res 走 ffmpeg 分支, 也算可用; 两个都没有时深探纯属空转(每条线都要
# 走一遍函数才发现没工具), 直接跳过, 免得白排一轮队
PROBE_AVAILABLE = bool(FFPROBE_BIN or FFMPEG_BIN)

# v6: 老版本 ffprobe 不认 -headers/-rw_timeout 这类输入选项, 一旦报
# "Unrecognized option" 就永久降级为裸命令, 避免所有探测一起失败
_PROBE_EXTRA_OK = True


def log(m):
    print(m, flush=True)


# ---------------- v7: 不可信配置的结构防御 ----------------
# 防御原来只写在"网络不可达"上, 但仓配置是第三方不可信 JSON:
# sites 写成 dict / 元素是字符串 / storeHouse 是字符串, 就会在归集阶段
# AttributeError 整轮崩掉(当天不出新配置, 电视端一直吃旧配置)。
# 以下三个归一化函数 + 逐处 isinstance 判断, 让任何畸形结构都只损失一个仓。

def as_list(v):
    """把不可信字段归一成 list: list原样; dict取values; 单值/字符串包一层"""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return list(v.values())
    if v is None or v == '':
        return []
    return [v]


def as_dict(v):
    """不可信字段归一成 dict: 非 dict 一律给空 dict(调用方 .get 不再崩)"""
    return v if isinstance(v, dict) else {}


def as_str(v):
    """不可信字段归一成 str: None->'', 数字/布尔转字符串"""
    if isinstance(v, str):
        return v
    if v is None:
        return ''
    return str(v)


def store_kind(cfg):
    """仓类型(显式标注, 取代原来'score is None 就代表导航仓'的隐含约定):
      store = 有站点的真仓, 参与健康分竞争
      nav   = 只有子仓列表的多仓导航入口, 不打分
    既没站点也没子仓的按 store 处理 —— 交给打分判死(空仓不该当导航留着)"""
    cfg = as_dict(cfg)
    if as_list(cfg.get('sites')):
        return 'store'
    if as_list(cfg.get('storeHouse')) or as_list(cfg.get('urls')):
        return 'nav'
    return 'store'


def split_alias(s):
    """'URL | 备注' -> (URL, 备注); 没有竖线则备注为空.
    v7: sources.txt 的 @ / L| / D| 三种行都是这个格式, 原来各写一遍"""
    rest = as_str(s).strip()
    alias = ''
    if '|' in rest:
        u, alias = rest.split('|', 1)
        rest, alias = u.strip(), alias.strip()
    return rest, alias


def norm_store_url(u):
    """仓地址归一化: 剥代理前缀、去参数、去尾斜杠、host小写
    v7: 从 collect() 内层闭包提到模块级 —— 纯函数, 测试才测得到"""
    u = as_str(u).strip()
    low = u.lower()
    for pref in PROXY_PREFIXES:
        if low.startswith(pref):
            u = u[len(pref):]
            low = u.lower()
            if RAW_HOST not in u[:60] and not re.match(r'^https?://', u, re.I):
                u = 'https://' + u
            low = u.lower()
    try:
        p = urllib.parse.urlsplit(u)
    except Exception:
        return low.rstrip('/')
    host = (p.hostname or '').lower()
    path = (p.path or '').rstrip('/')
    return (host + path) if path else host


def store_fingerprint(cfg):
    """仓内容指纹: 前5个站点的api/key排序后md5 —— 拦截换马甲的克隆仓
    v7: 同样从闭包提到模块级, 并加结构防御"""
    sites = as_list(as_dict(cfg).get('sites'))
    keys = []
    for s in sites[:5]:
        if not isinstance(s, dict):
            continue
        k = as_str(s.get('api')) or as_str(s.get('key'))
        if k:
            keys.append(norm_store_url(k) if k.startswith('http') else k)
    if not keys:
        return None
    keys.sort()
    return hashlib.md5('|'.join(keys).encode('utf-8')).hexdigest()


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
    """GET; raw 域名失败时自动走镜像重试(v7: 镜像列表统一取自 GH_MIRRORS)"""
    tries = [url]
    if re.match(r'^https?://%s/' % re.escape(RAW_HOST), url, re.I):
        tries += [m + url for m in GH_MIRRORS]
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


# ---- v10: 广告域名采集 ----
# 拉配置的入口只有 fetch_cfg 一处, 在这里顺手记一笔就不会漏(含子仓/孙仓)。
# 键=归一化域名, 值=列过它的仓(用 host 当投票者), 同一仓的子仓只算一票。
_ADS_VOTES = {}
_ADS_RAW = {}


def norm_ad_dom(x):
    """ads 项可能是域名/URL/带通配的写法, 归一成 host 便于去重与统计"""
    s = str(x).strip().lower()
    s = re.sub(r'^[a-z]+://', '', s)
    s = s.split('/')[0].split('?')[0].split('#')[0]
    s = s.split(':')[0]
    s = s.lstrip('*.')
    return s if '.' in s else ''


def note_ads(cfg, src):
    a = cfg.get('ads')
    if isinstance(a, str):
        a = [a]
    if not isinstance(a, list):
        return
    voter = urllib.parse.urlparse(src).netloc.lower() or src[:40]
    for x in a:
        d = norm_ad_dom(x)
        if not d:
            continue
        _ADS_VOTES.setdefault(d, set()).add(voter)
        _ADS_RAW.setdefault(d, set()).add(str(x).strip())


def ads_consensus(min_votes=ADS_MIN_VOTES):
    """返回 (共识域名, 单仓域名), 均按票数降序。
    多仓共识才算可信 —— 单仓乱写图床域名会连海报一起拦掉的教训在上游很常见"""
    con = sorted(((len(v), k) for k, v in _ADS_VOTES.items() if len(v) >= min_votes),
                 key=lambda x: (-x[0], x[1]))
    one = sorted(k for k, v in _ADS_VOTES.items() if len(v) < min_votes)
    return [k for _, k in con], one


def fetch_cfg(url, timeout):
    """取一个仓配置: 必须是 JSON 对象 —— 数组/字符串/数字一律当失败
    v7: 防止 json 顶层结构异常(如返回 list)一路传到 .get() 才崩"""
    j = fetch_json(url, timeout)
    if not isinstance(j, dict):
        raise RuntimeError('配置不是JSON对象(%s)' % type(j).__name__)
    note_ads(j, url)      # v10: 顺手采广告域名, 供 ads 白名单用
    return j


def tv_url(u):
    """电视端可达地址: raw 域名需加代理前缀(电视盒子直连 raw 基本拉不动)"""
    ul = as_str(u)
    if ul.startswith('https://' + RAW_HOST) or ul.startswith('http://' + RAW_HOST):
        return GH_PROXY + ul
    return ul


def join_api(base, params):
    sep = '&' if '?' in base else '?'
    return base + sep + params


PLAY_URL_RE = re.compile(r'\$(https?://[^#\s]+)')


def first_play_url(vod, m3u8_only=False):
    """从 vod_play_url 取第一个播放地址, 无则 None
    v7: 这段正则原来在 check_one / deep_check_cms_vod / vod_quality_probe
    里各写一遍(共4处), 抽成一处"""
    play = as_str(as_dict(vod).get('vod_play_url'))
    for m in PLAY_URL_RE.finditer(play):
        u = m.group(1)
        if m3u8_only and '.m3u8' not in u.split('?')[0]:
            continue
        return u
    return None


def cms_query(api, timeout=TIMEOUT_API):
    """查一个苹果CMS接口: 返回 (有分类, 视频列表[dict]).
    先 ac=list, 无内容再 ac=videolist&pg=1(部分接口 list 为空).
    v7: 结构非预期(list 是 dict / 元素是字符串)一律当空, 不再 AttributeError"""
    j = fetch_json(join_api(api, 'ac=list'), timeout)
    if not isinstance(j, dict):
        raise RuntimeError('接口返回非JSON对象')
    has_cls = bool(as_list(j.get('class')))
    vods = [v for v in as_list(j.get('list')) if isinstance(v, dict)]
    if not vods:
        j2 = fetch_json(join_api(api, 'ac=videolist&pg=1'), timeout)
        if isinstance(j2, dict):
            vods = [v for v in as_list(j2.get('list')) if isinstance(v, dict)]
    return has_cls, vods


SEARCH_PROBE_KW = '爱情'   # 通用词: 任何有片库的接口都该搜出结果


def cms_search_ok(api, timeout=TIMEOUT_API):
    """v9: 搜索可用性 —— 用户绝大多数时间在搜索, 而 searchable 一直是硬写的 1。
    2026-10-27 实测: 确实存在"首页能读 20 条、ac=detail&wd= 直接报错"的接口,
    这种接口进了清单, 电视上能打开仓却什么都搜不到。
    返回 True/False; 网络异常返回 None(结果不确定, 不当作坏)"""
    try:
        j = fetch_json(join_api(
            api, 'ac=detail&wd=' + urllib.parse.quote(SEARCH_PROBE_KW)),
            timeout)
    except Exception:
        return None
    if not isinstance(j, dict):
        return None
    return bool([v for v in as_list(j.get('list')) if isinstance(v, dict)])


def check_one(api):
    """检测单个接口, 返回 (总耗时毫秒, 搜索是否可用)
    搜索单独计时, 不计入接口本身的延迟"""
    t0 = time.time()
    has_cls, vods = cms_query(api)
    if not has_cls and not vods:
        raise RuntimeError('接口返回空数据')
    if not vods:
        raise RuntimeError('无可用视频数据')
    purl = first_play_url(vods[0])
    if not purl:
        raise RuntimeError('未取到播放地址')
    try:
        st, _ = http_get(purl, TIMEOUT_PLAY, max_bytes=2048)
        if st != 200:
            raise RuntimeError('播放地址 HTTP %s' % st)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError('播放地址不可达: %s' % e)
    ms = int((time.time() - t0) * 1000)
    return ms, cms_search_ok(api)


# ---------------- 仓体检 ----------------

API_URL_HINTS_LOCAL = ('api', 'provide', 'vod', '.js', '.json', '.php',
                       'collect', 'mxpro')


def extract_api_like_urls(obj, depth=0):
    """递归提取 ext(dict/list/JSON字符串)里疑似接口的http URL
    —— 验到最后一级: 内嵌配置里的接口地址也要测"""
    urls = set()
    if depth > 3 or obj is None:
        return urls
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith('http'):
            u = s.split(';')[0]
            low = u.lower()
            if any(k in low for k in API_URL_HINTS_LOCAL):
                urls.add(u)
        elif s.startswith('{') or s.startswith('['):
            try:
                urls |= extract_api_like_urls(json.loads(s), depth + 1)
            except Exception:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            urls |= extract_api_like_urls(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            urls |= extract_api_like_urls(v, depth + 1)
    return urls


AD_KEYWORDS = re.compile(
    r'(?:^|[/_.\-])(?:ads?|adv|advert\w*|guanggao|guangg|promo\w*'
    r'|banner|tuijian|preload|creative)(?:[/_.\-]|$)', re.I)
# v6: 必须要求词边界. 旧的 r'ad[s]?[-_.]?' 会命中 load / download /
# broadcast / shadow / head 里的 "ad" —— 一个CDN路径带 load 就把整仓
# 判成"插广告"并直接剔除, 误杀代价太大


def ts_domain_spread(txt):
    """ts 片段域名分散度: 返回 (是否分散, 主域名占比).
    正常单CDN的ts都在同一域名下; 拼广告会把片段打到别的域名。
    v7: vod_has_ads 与 live_has_ads 里这段统计原来逐字重复两份, 抽成一处"""
    ts_urls = [u for u in re.findall(r'^(https?://\S+)$', txt, re.M)
               if '.ts' in u.split('?')[0]]
    if len(ts_urls) < 4:
        return False, 1.0
    domains = {}
    for u in ts_urls:
        try:
            d = urllib.parse.urlsplit(u).netloc
        except Exception:
            d = '?'
        domains[d] = domains.get(d, 0) + 1
    main = max(domains.values())
    ratio = main / len(ts_urls)
    return (len(domains) > 1 and ratio < 0.85), ratio


def vod_has_ads(play_url):
    """点播广告检测: 抽测真实播放地址.
    m3u8: ts片段域名分散或片段名带广告词 -> 判插广告;
    其他类型(js/flv等)无法静态判定 -> 放行."""
    try:
        st, data = http_get(play_url, TIMEOUT_PLAY, max_bytes=65536)
        if st != 200:
            return None  # 无法判定(不因网络问题错杀)
        txt = data.decode('utf-8', 'ignore')
        if '#EXTM3U' not in txt[:200]:
            return None
        if not re.search(r'^https?://\S+\.ts', txt, re.M):
            return None   # 相对路径片段: 单一来源, 无域名可判
        spread, _ = ts_domain_spread(txt)
        if spread:
            return True   # 片段域名分散: 疑似广告插入
        ts = [u for u in re.findall(r'^(https?://\S+)$', txt, re.M)
              if '.ts' in u.split('?')[0]]
        if AD_KEYWORDS.search(' '.join(ts[:20])):
            return True   # 片段名带广告特征
        return False
    except Exception:
        return None


def deep_check_cms_vod(api_url):
    """CMS深测升级: 验证有真实内容 + 抽真实视频测广告.
    返回 (有内容, 无广告); 广告无法判定时按无广告处理"""
    try:
        has_cls, vods = cms_query(api_url)
    except Exception:
        return False, True
    if not (has_cls or vods):
        return False, True   # 没内容
    # 抽前几个视频测广告
    for v in vods[:3]:
        u = first_play_url(v)
        if not u:
            continue
        if vod_has_ads(u) is True:
            return True, False  # 有内容但插广告
    return True, True


def site_deps(cfg, base_url=''):
    """解析仓内每个站点: 返回 (全局jar, [{site,deps,cms}]).
    cms = 苹果CMS接口(深测); deps = 其他依赖URL;
    相对路径jar(./x.jar)拼基目录转绝对地址 — 验到最后一级
    v7: cfg / sites / 单站点 全部按不可信结构处理 —— sites 是 dict 或元素
    是字符串时只跳过该项, 不再让整轮归集崩掉"""
    cfg = as_dict(cfg)
    global_jar = as_str(cfg.get('spider'))
    jar_clean = global_jar.split(';')[0]
    if jar_clean.startswith('http'):
        global_jar = jar_clean
    elif jar_clean and base_url and ('.jar' in jar_clean or
                                    jar_clean.startswith(('./', '../', '/'))):
        global_jar = urllib.parse.urljoin(base_url, jar_clean)
    else:
        global_jar = ''
    per = []
    for s in as_list(cfg.get('sites')):
        if not isinstance(s, dict):
            continue          # v7: 元素是字符串/数字 → 跳过, 不计入分母
        deps = set()
        api = as_str(s.get('api'))
        jar = as_str(s.get('jar'))
        ext = s.get('ext')
        cms = None
        if api.startswith('http'):
            u = api.split(';')[0]
            # v7: type 可能是 "3"(字符串), 不能只认 int 3
            if as_str(s.get('type')).strip() == '3' and \
                    not u.split('?')[0].rstrip('/').endswith('.js'):
                cms = u   # CMS接口: 走深测(实际调用验证内容)
            else:
                deps.add(u)
        if jar.startswith('http'):
            deps.add(jar.split(';')[0])
        if isinstance(ext, str) and ext.startswith('http'):
            u = ext.split(';')[0]
            low = u.lower()
            if any(k in low for k in API_URL_HINTS_LOCAL):
                deps.add(u)
        elif ext is not None:
            deps |= extract_api_like_urls(ext)
        # csp_/相对路径/py脚本站点依赖全局jar
        if not api.startswith('http') and global_jar:
            deps.add(global_jar)
        per.append({'site': s, 'deps': deps, 'cms': cms})
    return global_jar, per


# ---- v12: 子资源通道画像 ----
# 目的: 在"上电视之前"就知道这个仓点进去会不会转圈。
# 只用配置文本判断, 一次网络请求都不发 —— 这点很关键:
# 云端"能不能访问"并不可靠(gitee 对脚本请求返回 423 反爬、git.acwing.com
# 返回登录页 HTML、裸 UA 被大量站点 403), 用它当判据会误杀好仓;
# 而"子资源地址落在哪个域名"是配置里写死的, 100% 可判定, 也不会因为
# 当天网络抖动把好仓筛掉。
def is_gh_url(u):
    """地址是否落在 GitHub 系(含各类加速代理)"""
    low = as_str(u).lower()
    if not low:
        return False
    if any(h in low for h in GH_HOSTS):
        return True
    return any(h in low for h in GH_PROXY_HINTS)


def _abs_res(u, base_url):
    """把子资源地址转绝对: 去掉 ;md5; 后缀, 相对路径按配置所在目录展开"""
    u = as_str(u).split(';')[0].strip()
    if not u:
        return ''
    if u.startswith('http'):
        return u
    if u.startswith(('./', '../')) or (not u.startswith('/') and '.' in u):
        if base_url:
            try:
                return urllib.parse.urljoin(base_url, u)
            except Exception:
                return ''
    return ''


def cdn_profile(cfg, base_url=''):
    """子资源通道画像. 返回 dict(sites, jar_packed, res_total, gh_cnt, gh_rate)
      sites      : 站点数(空仓 = 0, 这种仓挂进清单就是电视上一个红点)
      jar_packed : csp_ 站点数(代码打包在 jar 里, 只需下 1 次)
      res_total  : 需要单独下载的子资源数(jar / 外挂 js / 接口)
      gh_cnt     : 其中落在 GitHub 系的个数
      gh_rate    : gh_cnt / res_total, 无资源时为 0.0
    站点数优先看它自己, 不看声明 —— 有些仓 sites 是空数组但蜘蛛能拉满页面,
    那种属于"网页源", 不在本函数判断范围内, 仍按 sites=0 处理。
    """
    cfg = as_dict(cfg)
    sites = [s for s in as_list(cfg.get('sites')) if isinstance(s, dict)]
    resources = []
    jar_packed = 0

    r = _abs_res(cfg.get('spider'), base_url)
    if r:
        resources.append(r)

    for s in sites:
        api = as_str(s.get('api'))
        if api.startswith('csp_'):
            jar_packed += 1          # 打包在 jar 里, 不计入独立资源
        elif api.startswith(('http', './', '../')):
            r = _abs_res(api, base_url)
            if r:
                resources.append(r)
        for k in ('jar', 'ext'):
            v = s.get(k)
            if isinstance(v, str):
                r = _abs_res(v, base_url)
                if r and ('.js' in r.lower() or '.jar' in r.lower()
                          or '/api' in r.lower() or k == 'jar'):
                    resources.append(r)

    gh_cnt = sum(1 for u in resources if is_gh_url(u))
    total = len(resources)
    return {'sites': len(sites), 'jar_packed': jar_packed,
            'res_total': total, 'gh_cnt': gh_cnt,
            'gh_rate': (gh_cnt / total) if total else 0.0}


def test_dep(url):
    """v6: 失败重试一次. 一天只跑一轮 + 完美仓一票否决(0.10余量),
    一次网络抖动就能让整仓当天出局, 代价太大"""
    for i in range(2):
        try:
            st, _ = http_get(url, TIMEOUT_DEP, max_bytes=2048)
            if st == 200:
                return True
        except Exception:
            pass
        if i == 0:
            time.sleep(0.4)
    return False


def vod_quality_probe(api_url):
    """v2: 点播画质抽测 —— 从CMS接口取一部真实影片的m3u8, ffmpeg实测分辨率.
    返回 (高度, None); 失败/测不出返回 (0, 原因)"""
    try:
        _, vods = cms_query(api_url)
    except Exception as e:
        return 0, str(e)[:40]
    for v in vods[:3]:
        u = first_play_url(v, m3u8_only=True)
        if not u:
            continue
        w, h = ffprobe_res(u)
        if h:
            return h, None
    return 0, '无可测m3u8'


def recommend_quality_probe(cms_apis):
    """v2: 首页推荐质量检测 —— 抽首页接口看内容与海报.
    返回 (推荐条数, 海报挂率); 失败返回 (0,1)"""
    if not cms_apis:
        return 0, 1
    api = cms_apis[0]
    try:
        _, vods = cms_query(api)
    except Exception:
        return 0, 1
    if not vods:
        return 0, 1
    pics = [as_str(v.get('vod_pic')) for v in vods[:5]]
    pic_urls = [p for p in pics if p.startswith('http')]
    if not pic_urls:
        return len(vods), 1
    ok_cnt = 0
    for p in pic_urls:
        try:
            st, _ = http_get(p, 6, max_bytes=64)
            if st == 200:
                ok_cnt += 1
        except Exception:
            pass
    return len(vods), 1 - ok_cnt / len(pic_urls)


def assess_stores(stores):
    """给每个仓算健康分(含CMS接口深测: 实际调用验证内容)
    v7: 单个仓的结构异常只跳过它自己, 不再让整轮体检崩掉"""
    all_deps = set()
    parsed = []
    for st in stores:
        kind = 'store'
        gjar, per = '', []
        prof = {'sites': 0, 'jar_packed': 0, 'res_total': 0,
                'gh_cnt': 0, 'gh_rate': 1.0}
        try:
            cfg = as_dict(st.get('cfg'))
            kind = store_kind(cfg)
            gjar, per = site_deps(cfg, as_str(st.get('url')))
            # v12: 通道画像(纯文本, 零请求) —— 判定这个仓上电视会不会转圈
            prof = cdn_profile(cfg, as_str(st.get('url')))
        except Exception as e:
            log('[仓体检跳过] %s -> %s: %s' % (
                as_str(st.get('name'))[:16], type(e).__name__, e))
        st['kind'] = kind
        st['gh_rate'] = prof['gh_rate']
        st['gh_cnt'] = prof['gh_cnt']
        st['res_total'] = prof['res_total']
        st['sites_n'] = prof['sites']
        st['jar_rate'] = ((prof['jar_packed'] / prof['sites'])
                          if prof['sites'] else 0.0)
        parsed.append((st, gjar, per))
        for e in per:
            all_deps.update(e['deps'])
    log('仓体检: %d 个仓, 去重后依赖 %d 个, 并发检测...' % (
        len(stores), len(all_deps)))
    dep_ok = {}
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        futs = {ex.submit(test_dep, d): d for d in all_deps}
        for fu in as_completed(futs):
            d = futs[fu]
            dep_ok[d] = fu.result()

    # CMS接口深测: 实际调用验证真实内容 + 抽真实视频测广告
    cms_apis = []
    for st, gjar, per in parsed:
        cnt = 0
        for e in per:
            if e['cms'] and cnt < CMS_DEEP_LIMIT:
                if e['cms'] not in cms_apis:
                    cms_apis.append(e['cms'])
                cnt += 1
    cms_ok = {}
    cms_ad = {}
    if cms_apis:
        log('CMS深测: %d 个接口验证内容+抽视频测广告...' % len(cms_apis))
        with ThreadPoolExecutor(MAX_WORKERS) as ex:
            futs = {ex.submit(deep_check_cms_vod, a): a for a in cms_apis}
            for fu in as_completed(futs):
                a = futs[fu]
                has_content, no_ads = fu.result()
                cms_ok[a] = has_content
                if not no_ads:
                    cms_ad[a] = True

    # v2: 画质抽测 + 推荐质量检测(只对有CMS接口的仓做, 每仓限抽样数)
    quality_res = {}
    rec_res = {}
    q_tasks = []
    for st, gjar, per in parsed:
        cms_list = [e['cms'] for e in per if e['cms']][:VOD_QUALITY_SAMPLE]
        if cms_list:
            q_tasks.append((as_str(st.get('url')), cms_list))
    if q_tasks:
        log('画质抽测: %d 个仓 (抽影片实测分辨率, <720p 降分; 检首页推荐质量)...' % len(q_tasks))
        with ThreadPoolExecutor(MAX_WORKERS) as ex:
            # v6: 推荐质量检测原来在主线程串行跑(每仓 1+5 个请求, 6s 超时),
            # 40 个仓就是几分钟白等; 现在一并丢进线程池
            q_futs = {ex.submit(vod_quality_probe, cl[0]): surl
                      for surl, cl in q_tasks}
            r_futs = {ex.submit(recommend_quality_probe, cl): surl
                      for surl, cl in q_tasks}
            for fu in as_completed(q_futs):
                try:
                    quality_res[q_futs[fu]] = fu.result()
                except Exception:
                    pass
            for fu in as_completed(r_futs):
                try:
                    rec_res[r_futs[fu]] = fu.result()
                except Exception:
                    pass

    out = []
    for st, gjar, per in parsed:
        surl = as_str(st.get('url'))
        kind = st.get('kind') or 'store'
        alive_cnt, total, unknown, deep, ad_hits = 0, 0, 0, 0, 0
        for e in per:
            if e['cms']:
                total += 1
                deep += 1
                if cms_ok.get(e['cms']):
                    alive_cnt += 1
                if cms_ad.get(e['cms']):
                    ad_hits += 1
                continue
            deps = e['deps']
            if not deps:
                unknown += 1
                continue
            total += 1
            if all(dep_ok.get(d) for d in deps):
                alive_cnt += 1
        if total:
            score = alive_cnt / total
        else:
            # v7: 导航仓不打分; 无站点的"空仓"给 0 分判死
            # (旧版 score=None 顺手把它当导航仓保留, 是个隐性漏洞)
            score = None if kind == 'nav' else 0.0
        # v2: 画质抽测降分 —— 抽的影片实测 <720p, 该仓降 VOD_QUALITY_PENALTY
        q_hit, q_note = quality_res.get(surl, (None, ''))
        if q_hit is not None and score is not None and 0 < q_hit < VOD_HD_MIN_H:
            score = max(0.0, score - VOD_QUALITY_PENALTY)
            q_note = '抽测影片%d(<720p)' % q_hit
        # v2: 推荐质量降分 —— 首页空/海报挂率高的仓降分
        rec_n, rec_bad = rec_res.get(surl, (None, None))
        rec_note = ''
        if rec_n is not None and score is not None and rec_bad is not None:
            if rec_n == 0:
                score = max(0.0, score - REC_QUALITY_PENALTY)
                rec_note = '首页无推荐'
            elif rec_bad > 0.5:
                score = max(0.0, score - REC_QUALITY_PENALTY / 2)
                rec_note = '海报挂率%d%%' % int(rec_bad * 100)
        jar_ok = dep_ok.get(gjar) if gjar else None
        out.append(dict(st, kind=kind, score=score, alive=alive_cnt,
                        total=total, unknown=unknown, deep=deep,
                        ad_hits=ad_hits, jar_ok=jar_ok,
                        quality_h=q_hit if q_hit is not None else '',
                        rec_note=rec_note or q_note))
        pct = ('%d%%' % (score * 100)) if score is not None else '入口'
        ad_txt = ', 广告%d' % ad_hits if ad_hits else ''
        extra = ''
        if q_note:
            extra += ', 画质降分'
        if rec_note:
            extra += ', 推荐降分'
        log('[仓体检] %-16s 分数%s (存活%d/%d含深测%d, 未测%d%s%s)' % (
            as_str(st.get('name'))[:16], pct, alive_cnt, total, deep,
            unknown, ad_txt, extra))
    return out


# ---------------- 直播: 频道白名单 ----------------

LIVE_GROUPS = ['央视', '卫视', '中国香港', '湖南']

# ---- v9: EPG 台名对齐(不新增任何频道, 只让已有台名对得上) ----
# 病根: live.m3u 只声明了 x-tvg-url, 但台名是我们自己起的("CCTV-1 综合"),
# 而 EPG 里叫 "CCTV1" —— 归一化后 cctv1综合 与 cctv1 永不相等。
# 2026-10-27 实测 51zmt 的 e.xml(100个频道): 19 个央视台精确匹配全部失败,
# 电视上这些台一个节目单都没有。这里补 tvg-id/tvg-name 做精确对齐。
EPG_ALIAS = {
    '海南卫视': '旅游卫视',          # EPG 里仍用旧名
    '凤凰卫视中文台': '凤凰中文',
    '凤凰卫视资讯台': '凤凰资讯',
    'CCTV-4K 超高清': 'CCTV4K',
    'CCTV-5+ 体育赛事': 'CCTV5+',
}
# EPG 里确实没有的台: 不输出 tvg 属性 —— 写错名字反而可能被播放器
# 模糊匹配到别的台, 显示成别的台的节目单, 比没有更糟
EPG_NONE = ('RTHK 港台电视32', '金鹰纪实', '湖南经视',
            '湖南都市', '湖南国际')


def epg_name(ch_name):
    """台名 -> EPG 中的 display-name; 无对应返回 None"""
    if ch_name in EPG_NONE:
        return None
    if ch_name in EPG_ALIAS:
        return EPG_ALIAS[ch_name]
    m = re.match(r'^CCTV-(\d+) ', ch_name)
    if m:
        return 'CCTV' + m.group(1)
    return ch_name


def build_whitelist():
    wl = []

    def add(name, group, patterns):
        wl.append({'name': name, 'group': group,
                   'epg': epg_name(name),
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

    # v2: 香港台 B 方案 —— 只要中文台(普通话):
    #   保留: 凤凰中文/凤凰资讯/RTHK32(普通话台)
    #   移除: 翡翠台(粤语)/明珠台(英语)/HOY(粤语)/RTHK31(粤语)
    hks = [
        ('凤凰卫视中文台', r'凤凰卫视?中文|phoenixchinese'),
        # v6: 补"卫视" —— 归一化后是"凤凰卫视资讯台", 旧模式"凤凰资讯"隔着
        # "卫视"永远匹配不上, 文档声称保留的资讯台从没进过清单
        ('凤凰卫视资讯台', r'凤凰(卫视)?资讯|phoenixinfo'),
        ('RTHK 港台电视32', r'rthk\w*32|港台(电视|電視)?32'),
    ]
    for name, pat in hks:
        add(name, '中国香港', [pat])

    huns = [
        ('金鹰纪实', r'金鹰纪实'),
        ('金鹰卡通', r'金鹰卡通|金鹰动漫'),
        ('湖南经视', r'湖南经视'),
        ('湖南都市', r'湖南都市'),
        ('湖南国际', r'湖南国际|hunaninternational'),  # v2: 新增
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


def parse_m3u(text):
    """解析m3u, 返回 [{name,ua,ref,group,url}]
    v6: 支持 #EXTVLCOPT / #KODIPROP 里的 UA 与 Referer —— iptv-org、Guovin
    这类大池大量线路靠这两个头才肯发流, 不认就一直测不出分辨率被丢弃"""
    items = []
    cur = None
    opt = {}

    def _set_opt(key, val):
        k = key.strip().lower()
        v = val.strip().strip('"')
        if k in ('http-user-agent', 'user-agent', 'http-useragent'):
            opt['ua'] = v
        elif k in ('http-referrer', 'http-referer', 'referer', 'referrer'):
            opt['ref'] = v

    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        up = ln.upper()
        if up.startswith('#EXTINF'):
            head, _, name = ln.partition(',')
            name = name.strip()
            ua = re.search(r'http-user-agent="([^"]+)"', head, re.I)
            grp = re.search(r'group-title="([^"]+)"', head, re.I)
            cur = {'name': name,
                   'ua': ua.group(1) if ua else None,
                   'group': grp.group(1) if grp else ''}
            opt = {}
        elif up.startswith('#EXTVLCOPT:'):
            k, _, v = ln[11:].partition('=')
            _set_opt(k, v)
        elif up.startswith('#KODIPROP:'):
            k, _, v = ln[10:].partition('=')
            if 'useragent' in k.lower().replace('-', '').replace('_', ''):
                opt['ua'] = v.strip().strip('"')
            else:
                _set_opt(k.split(':')[-1], v)
        elif ln.startswith('#'):
            continue
        elif cur is not None:
            cur['ua'] = cur.get('ua') or opt.get('ua')
            cur['ref'] = opt.get('ref')
            cur['url'] = ln
            items.append(cur)
            cur = None
            opt = {}
    return items


def _ffprobe_cmd(extra, url):
    cmd = [FFPROBE_BIN, '-v', 'error']
    if extra:
        cmd += ['-rw_timeout', str(TIMEOUT_PROBE * 1000 * 1000)]
        hdr = []
        if extra.get('ref'):
            hdr.append('Referer: %s' % extra['ref'])
        if extra.get('ua'):
            hdr.append('User-Agent: %s' % extra['ua'])
        if hdr:
            cmd += ['-headers', ''.join(h + '\r\n' for h in hdr)]
    cmd += ['-select_streams', 'v:0', '-show_entries',
            'stream=width,height', '-of', 'csv=p=0', url]
    return cmd


def ffprobe_res(url, ua=None, ref=None):
    """用 ffprobe/ffmpeg 探测真实分辨率: (w,h) 或 (0,0)
    v6: 真的把 UA/Referer 传给探测器. v5 收下 ua 却在函数里从没用过,
    导致"必须伪装UA才发流"的线路一律测不出分辨率 → 被当成低质线丢弃"""
    global _PROBE_EXTRA_OK
    if FFPROBE_BIN:
        want_extra = bool(_PROBE_EXTRA_OK and (ua or ref))
        for with_extra in ([True, False] if want_extra else [False]):
            cmd = _ffprobe_cmd({'ua': ua, 'ref': ref} if with_extra else None,
                               url)
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=TIMEOUT_PROBE)
            except subprocess.TimeoutExpired:
                return 0, 0      # 超时就是线慢/死, 不再重试
            except Exception:
                continue
            if with_extra and 'Unrecognized option' in (p.stderr or ''):
                _PROBE_EXTRA_OK = False   # 老版本: 永久降级后重试
                continue
            m = re.search(r'(\d{2,4})\s*,\s*(\d{2,4})', p.stdout or '')
            if m:
                return int(m.group(1)), int(m.group(2))
        return 0, 0
    if FFMPEG_BIN:
        cmd = [FFMPEG_BIN, '-hide_banner']
        if _PROBE_EXTRA_OK:
            hdr = []
            if ref:
                hdr.append('Referer: %s' % ref)
            if ua:
                hdr.append('User-Agent: %s' % ua)
            if hdr:
                cmd += ['-headers', ''.join(h + '\r\n' for h in hdr)]
        cmd += ['-i', url, '-t', '0', '-f', 'null', '-']
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


def is_ipv6_url(u):
    """判断URL是否IPv6地址(用户家无IPv6)"""
    try:
        host = urllib.parse.urlsplit(u).hostname or ''
    except Exception:
        return False
    return ':' in host


def is_ad_url(u):
    """v5: 运营源URL特征 —— livekey/streamid/txiptv/bfgd/tsfile等参数或路径.
    这类"免费线路"以起播/换台贴片广告变现(2026-10-27家中实看实锤),
    用户拍板直接拒收: 不实测不占候选配额, 收录阶段即剔除"""
    ul = u.lower()
    return any(p in ul for p in AD_URL_PATS)


def live_index_ads(txt):
    """v14: m3u8索引广告指纹 —— 相对倍数 + 绝对下限(修 v6 的边界 bug):
    ① DISCONTINUITY 出现 >=2 次: 流内多处拼接 = 插广告强信号;
       单次多为编码器重连或节目切换(官方源也常见), 放行;
    ② 某分片 >2.0x中位数 且 >=12s: 拼入广告段会突兀变长;
       均匀20s的慢切片中位数同为20s, 2.0x=40s 不触发;
    ③ 首片 >1.8x中位数 且 >=9s: 起播贴片特征.
    v6 旧判据是 ">=15s 且 >2.5x中位数", 6 秒切片时退化成 "15>15" 恒 False,
    15 秒广告与 10/12 秒起播贴片**全部漏判** —— v14 改成倍数主导。
    注: 只取当前窗口(通常3-6段), 判定不了的情况宁可放行, 次日会再测一遍。
    起播贴片真正的判据在 judge_preroll()(两次快照比对), 这里只是静态兜底"""
    if txt.count('DISCONTINUITY') >= 2:
        return True
    durs = [float(m) for m in re.findall(r'#EXTINF:([\d.]+)', txt)]
    if len(durs) >= 3:
        med = sorted(durs)[len(durs) // 2]
        if med > 0:
            if any(d >= AD_SEG_MIN and d > AD_SEG_RATIO * med for d in durs):
                return True
            if (durs[0] >= AD_FIRST_MIN
                    and durs[0] > AD_FIRST_RATIO * med):
                return True
    return False


def is_bare_ip(host):
    """v14: 裸 IP(无域名)判定 —— 广告分发站的强特征之一。
    正规 CDN 用域名+多节点; 卖线路的面板通常是 1~2 个 VPS 的裸 IP 扛几十个台"""
    if not host:
        return False
    return bool(re.match(r'^\d{1,3}(?:\.\d{1,3}){3}$', host))


def host_channel_map(pairs):
    """v14: {host: {频道名}} —— 统计每个 host 承载了多少个**不同**频道。
    pairs 是 (频道名, url) 的序列。返回 {host: set(频道名)}。
    纯文本计算, 不发请求; 用于识别"一个 IP 跑几十个台"的分发面板。"""
    m = {}
    for name, u in pairs:
        try:
            host = urllib.parse.urlsplit(u).hostname or ''
        except Exception:
            continue
        if host:
            m.setdefault(host, set()).add(name)
    return m


# ---------------- v15: IP 归属地 ----------------
# 为什么必须查归属: "扛多少个台"本身分不出好人坏人。中国移动/联通的省网 IPTV
# 一台扛 20 多个台是标准部署方式(实测 39.134.115.163 扛 22 个), 而美国 Nocix
# 的 VPS 扛 30 个台就是卖线路的面板。同一个数字, 两种结论, 唯一的区分依据是
# "这台机器在哪"。不查归属就只剩"见一个杀一个", 会把干净源一起杀光。
def load_geo_cache():
    """读归属缓存。结构 {host: {'cc': 'CN', 'ts': 1699999999}}"""
    try:
        with open(GEO_CACHE, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_geo_cache(cache):
    try:
        with open(GEO_CACHE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=0, sort_keys=True)
    except Exception as e:
        log('[归属缓存] 写入失败(不影响本轮): %s' % e)


def geo_lookup(host):
    """查一个 IP 的归属国代码。失败依次降级两个接口, 都失败返回 '?'。
    只查裸 IP; 域名直接返回 'DOMAIN'(域名型另有判据, 不靠归属)。"""
    if not is_bare_ip(host):
        return 'DOMAIN'
    try:
        code, data = http_get('https://ipwho.is/%s' % host, 10)
        j = json.loads(data.decode('utf-8', 'ignore'))
        if isinstance(j, dict) and j.get('success') is not False:
            cc = as_str(j.get('country_code'))
            if cc:
                return cc
    except Exception:
        pass
    try:
        code, data = http_get('https://ipinfo.io/%s/json' % host, 10)
        j = json.loads(data.decode('utf-8', 'ignore'))
        if isinstance(j, dict):
            cc = as_str(j.get('country'))
            if cc:
                return cc
    except Exception:
        pass
    return '?'


def annotate_geo(hosts):
    """给一批 host 打归属标记, 返回 {host: cc}。
    带 30 天缓存: 每轮只查新出现的 IP, 老 IP 直接用上轮结果。
    缓存落在 ip_geo.json 并随仓库提交, 所以第二轮起基本零请求。
    查询失败记 '?' 但**不写缓存** —— 下轮还要重试, 免得一次网络抖动把某个
    干净 IP 永久钉成未知。"""
    cache = load_geo_cache()
    now = int(time.time())
    ttl = LIVE_GEO_TTL_DAYS * 86400
    out = {}
    todo = []
    for h in hosts:
        if not is_bare_ip(h):
            out[h] = 'DOMAIN'
            continue
        rec = cache.get(h)
        if isinstance(rec, dict) and rec.get('cc') and rec.get('cc') != '?':
            if now - int(rec.get('ts') or 0) < ttl:
                out[h] = rec['cc']
                continue
        todo.append(h)
    todo.sort()
    if len(todo) > LIVE_GEO_MAX:
        log('[归属] 待查 %d 个, 本轮只查前 %d 个(其余沿用旧值/判未知)'
            % (len(todo), LIVE_GEO_MAX))
        todo = todo[:LIVE_GEO_MAX]
    if todo:
        log('[归属] 查询 %d 个新 host 的归属地...' % len(todo))
        with ThreadPoolExecutor(min(8, MAX_WORKERS)) as ex:
            futs = {ex.submit(geo_lookup, h): h for h in todo}
            for fu in as_completed(futs):
                h = futs[fu]
                try:
                    cc = fu.result()
                except Exception:
                    cc = '?'
                out[h] = cc
                if cc and cc != '?':
                    cache[h] = {'cc': cc, 'ts': now}
                time.sleep(0.05)
        save_geo_cache(cache)
    n_cn = sum(1 for c in out.values() if c == 'CN')
    n_unk = sum(1 for c in out.values() if c == '?')
    log('[归属] 大陆 %d / 域名 %d / 境外 %d / 未知 %d'
        % (n_cn, sum(1 for c in out.values() if c == 'DOMAIN'),
           len(out) - n_cn - n_unk - sum(1 for c in out.values()
                                         if c == 'DOMAIN'), n_unk))
    return out


def is_clean_src(url, geo):
    """v15: 这条线是否属于"可信任的干净源"(大陆 IP 或域名型)。
    用于两个地方: ① 广告站判据里豁免大陆运营商源 ② 境外测不出时不判死。"""
    try:
        h = urllib.parse.urlsplit(url).hostname or ''
    except Exception:
        return False
    return (geo or {}).get(h) in LIVE_GEO_TRUST


def pick_ad_hosts(host_map, geo=None, min_ch=None):
    """v15: 从 host->频道集合 里挑出广告分发站。
    判据: **裸 IP** 且 承载 >= LIVE_AD_HOST_CH 个不同频道 且 **归属不是大陆**。

    为什么必须加"归属"这一条（v14 漏掉的）:
      只看"扛几个台"分不出好坏。中国移动/联通的省网 IPTV 源一台扛 20 多个台
      是标准部署(实测 39.134.115.163 扛 22 个、183.207.248.71 扛 21 个),
      一条广告都没有; 而美国 Nocix 的 VPS 扛 30 个台就是卖线路的面板。
      v14 不分归属一律砍, 等于"连干净源一起枪毙", 越砍台越少、广告一条没少。

    为什么仍要"裸 IP"这一条: 域名型 host 承载多个台可能是正规 CDN
    (省级 IPTV 平台、广电公开 CDN 都是一域名多台), 误杀代价太高。

    geo 为 None 时退化成 v14 行为(全砍), 保持旧测试与旧调用可用。"""
    lim = LIVE_AD_HOST_CH if min_ch is None else min_ch
    out = set()
    for h, chs in host_map.items():
        if not is_bare_ip(h) or len(chs) < lim:
            continue
        if (geo or {}).get(h) == 'CN':
            continue          # 大陆运营商源: 扛多是常态, 不是广告站
        out.add(h)
    return out


def snapshot_playlist(txt):
    """v14: 把一份 m3u8 解析成可比对的快照。
    只取判定"是不是真直播"需要的字段, 不做任何网络请求。
    返回 None 表示这不是可解析的 m3u8。"""
    if not txt or '#EXTM3U' not in txt:
        return None
    durs = [float(m) for m in re.findall(r'#EXTINF:([\d.]+)', txt)]
    m = re.search(r'#EXT-X-MEDIA-SEQUENCE:(\d+)', txt)
    seq = int(m.group(1)) if m else None
    m = re.search(r'#EXT-X-TARGETDURATION:(\d+)', txt)
    target = int(m.group(1)) if m else 0
    # 分片地址(去掉协议/主机差异, 只留 path+query 便于比对)
    segs = [ln.strip() for ln in txt.splitlines()
            if ln.strip() and not ln.lstrip().startswith('#')]
    return {
        'seq': seq,
        'segs': segs,
        'n': len(durs),
        'durs': durs,
        'target': target,
        # master 清单里的"分片"其实是各档 variant 地址, 天生固定不变,
        # 不能拿它比"有没有前进" —— 必须单独标出来
        'master': '#EXT-X-STREAM-INF' in txt,
        'endlist': '#EXT-X-ENDLIST' in txt,
        'vod': bool(re.search(r'#EXT-X-PLAYLIST-TYPE:\s*VOD', txt, re.I)),
        'disc': txt.count('DISCONTINUITY'),
    }


def judge_preroll(a, b):
    """v14: 起播贴片/伪直播判定 —— 比对同一地址间隔数秒的两次快照。
    返回 (is_fake, reason)。is_fake=True 表示"这条线起播要过广告"。

    判据(命中任一即假):
      ① 出现 EXT-X-ENDLIST 或 PLAYLIST-TYPE:VOD —— 真直播绝不会标记"播完了",
         这是"预置一段片子循环播"的广告垫片最典型形态;
      ② 两次 MEDIA-SEQUENCE 都没前进 —— 直播滑窗每几秒必前移, 不动就是循环;
      ③ 没有 MEDIA-SEQUENCE 时, 退化为"两次分片列表完全相同"(同样说明没在往前走)。

    master 清单直接放行: 它列的是 variant 地址, 本来就不变, 不是判据。
    a/b 任一为 None(拉不到/不是m3u8)时判不了, 返回 (False, '') 放行 ——
    宁可放行次日再测, 也不拿"测不出"当"有问题"去误杀。"""
    if not a or not b:
        return False, ''
    if a.get('master') or b.get('master'):
        return False, ''
    if a['endlist'] or a['vod']:
        return True, 'VOD/ENDLIST(预置循环)'
    if a['seq'] is not None and b['seq'] is not None:
        if b['seq'] <= a['seq']:
            return True, '索引不前进(seq %d->%d)' % (a['seq'], b['seq'])
        return False, ''
    if a['segs'] and a['segs'] == b['segs']:
        return True, '分片列表不变(非直播)'
    return False, ''


def fetch_snapshot(url, ua=None, ref=None):
    """v14: 抓一次 m3u8 并解析成快照(起播实测专用)。
    与 probe_live 分开是有意的: 这里只要"两次抓取的差异", 不判活、不判画质、
    不做广告判据 —— 复用 probe_live 会白白多跑一遍 ffprobe, 也让判据混在一起。"""
    headers = dict(UA)
    if ua:
        headers['User-Agent'] = ua
    if ref:
        headers['Referer'] = ref
    try:
        st, data = http_get(url, TIMEOUT_LIVE, max_bytes=65536,
                            headers=headers)
        if st != 200:
            return None
        return snapshot_playlist(data.decode('utf-8', 'ignore'))
    except Exception:
        return None


def live_has_ads(txt):
    """启发式广告检测: m3u8媒体清单里ts片段域名分散(主域名占比低)疑插广告
    只能测静态广告, 播放中动态插入的测不出
    v7: 统计逻辑与 vod_has_ads 共用 ts_domain_spread()"""
    spread, _ = ts_domain_spread(txt)
    return spread


def probe_live(url, ua=None, ref=None, deep=True):
    """直播线路体检: 返回 dict(ok,w,h,method,ms)
    清晰度以 ffmpeg 实测为准; master声明仅在ffprobe失败时佐证;
    名字标签一律不作为画质依据(宁缺勿滥)
    v6: 补 ref(Referer) —— 部分源校验来源, 不带就 403
    v13: deep=False 时**不跑 ffprobe**, 只做 HTTP 拉索引(判活 + 广告指纹 +
    master 声明)。第一轮全量浅探用它(832 条候选也就几秒), 第二轮才对每台
    前 LIVE_DEEP_PER_CH 条用 deep=True 测真实画质。"""
    t0 = time.time()
    headers = dict(UA)
    if ua:
        headers['User-Agent'] = ua
    if ref:
        headers['Referer'] = ref
    # v8: 连接异常重试一次再判死 —— 直播线是所有检测里最容易抖动的
    # (点播依赖 test_dep 早有重试, 这里却是一次就否, 标准不一致)。
    # 只重试"连接异常", 403/404 这类确定性失败不重试, 不浪费时间。
    st, data = 0, b''
    for attempt in range(2):
        try:
            st, data = http_get(url, TIMEOUT_LIVE, max_bytes=65536,
                                headers=headers)
            break
        except Exception:
            if attempt:
                return {'ok': False, 'w': 0, 'h': 0, 'method': '', 'ms': 0}
            time.sleep(0.4)
    ms = int((time.time() - t0) * 1000)
    if st != 200:
        return {'ok': False, 'w': 0, 'h': 0, 'method': '', 'ms': ms}
    head = data[:200].decode('utf-8', 'ignore')
    if '#EXTM3U' in head:
        txt = data.decode('utf-8', 'ignore')
        base = {'ok': True, 'w': 0, 'h': 0, 'method': '', 'ms': ms,
                'hls': True}   # v8: 标记"索引合法", 供兜底层识别
        # 广告启发式: ts域名分散拒收; v5: 索引广告指纹(拼接/长段/首片贴片)拒收
        if live_has_ads(txt) or live_index_ads(txt):
            return {'ok': False, 'w': 0, 'h': 0, 'method': 'ads', 'ms': ms}
        master_res = None
        master_kbps = 0
        if '#EXT-X-STREAM-INF' in txt:
            # v9: 同时取 BANDWIDTH —— 只有 master 会声明码率, 媒体清单没有
            for m in re.finditer(r'#EXT-X-STREAM-INF:([^\r\n]+)', txt):
                attrs = m.group(1)
                r = re.search(r'RESOLUTION=(\d+)x(\d+)', attrs)
                if not r:
                    continue
                w, h = int(r.group(1)), int(r.group(2))
                b = re.search(r'BANDWIDTH=(\d+)', attrs)
                kbps = (int(b.group(1)) // 1000) if b else 0
                if master_res is None or w * h > master_res[0] * master_res[1]:
                    master_res, master_kbps = (w, h), kbps
        base['kbps'] = master_kbps
        # ffmpeg 实测复核(优先); master声明仅佐证
        # v13: 浅探不测画质 —— 只有 master 声明就先用声明值, 连声明都没有就
        # 退回 base(w=0), 由第二轮深探来定; 这样第一轮零 ffprobe 开销
        if deep:
            w, h = ffprobe_res(url, ua, ref)
            if w:
                return dict(base, w=w, h=h, method='ffmpeg')
        if master_res:
            return dict(base, w=master_res[0], h=master_res[1],
                        method='master')
        return base
    # 非m3u8文本(可能是flv/ts/php转发的流)
    # v13: 这类只能靠 ffprobe 判画质, 浅探阶段一律先记"可达但未知"
    if not deep:
        return {'ok': True, 'w': 0, 'h': 0, 'method': '', 'ms': ms}
    w, h = ffprobe_res(url, ua, ref)
    if w:
        return {'ok': True, 'w': w, 'h': h, 'method': 'ffmpeg', 'ms': ms}
    return {'ok': True, 'w': 0, 'h': 0, 'method': '', 'ms': ms}


def rr_pick(urls_dict, n):
    """v6: 按上游轮转取候选 —— 防止排第一个的上游(Guovin, 8月后停更)
    用一堆旧死线把 LIVE_MAX_CAND 名额吃满, 后面 vbskycn 活池的线
    一条都进不了实测. 轮转后每个上游都有份, 死线自然被淘汰出局"""
    by = {}
    for u, m in urls_dict.items():
        by.setdefault(m.get('src'), []).append((u, m))
    keys = list(by.keys())
    out = []
    i = 0
    while len(out) < n and keys:
        k = keys[i % len(keys)]
        if by[k]:
            out.append(by[k].pop(0))
        elif not any(by[v] for v in keys):
            break
        i += 1
    return out


def pick_live_urls(hd, backup, unver):
    """选定该频道最终线路, 返回 (urls, n_unver)。
    主力 hd(>=720p) 优先; 不足 LIVE_KEEP 用 backup(>=540p) 补齐;
    v13: 仍不足时用 unver(探活通过但本轮没深测画质)补满 —— 方案C 每台只深测
    前 2 条, 第 3 条线天生没有实测结果; 旧逻辑"有实测线就绝不碰 unver"会让
    所有台都退化成 2 条线。补位**只在不够数时发生**, 绝不挤掉任何已实测的线,
    且把补了几条报回去, 让 report 明确标出哪些是"未实测补位"。
    一条实测线都没有时, 仍然全用 unver 兜底(此时 n_unver = 实际条数)。"""
    urls = hd[:LIVE_KEEP]
    if len(urls) < LIVE_KEEP:
        urls += backup[:LIVE_KEEP - len(urls)]
    n_unver = 0
    if len(urls) < LIVE_KEEP and unver:
        need = LIVE_KEEP - len(urls)
        picked = sorted(unver, key=lambda x: x['ms'])[:need]
        urls += picked
        n_unver = len(picked)
    return urls, n_unver


def collect_live(wl):
    """从上游m3u归集白名单频道并逐线实测.
    返回 (channels, upstream_stats)"""
    upstreams = []
    if os.path.exists(SOURCES):
        with open(SOURCES, encoding='utf-8-sig') as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith('L|'):
                    u, alias = split_alias(ln[2:])
                    upstreams.append((u, alias))

    cands = {}   # 频道名 -> {'entry':wl, 'urls':{url:{'ua','ref','raw','src'}}}
    up_stats = []
    for up_idx, (u, alias) in enumerate(upstreams):
        try:
            st, data = http_get(u, 25)
            txt = data.decode('utf-8-sig', 'ignore')
            items = parse_m3u(txt)
            matched = 0
            ipv6_cnt = 0      # v7: 原来叫 v6, 与版本号 v6 撞名
            ads = 0
            for it in items:
                e = match_channel(it['name'], wl)
                if not e:
                    continue
                matched += 1
                if LIVE_NO_IPV6 and is_ipv6_url(it['url']):
                    ipv6_cnt += 1
                    continue
                # v5: 运营源直接拒收(不实测不占配额) —— 贴片广告线
                if is_ad_url(it['url']):
                    ads += 1
                    continue
                cands.setdefault(e['name'], {'entry': e, 'urls': {}})
                if it['url'] not in cands[e['name']]['urls']:
                    cands[e['name']]['urls'][it['url']] = {
                        'ua': it.get('ua'), 'ref': it.get('ref'),
                        'raw': it['name'], 'src': up_idx}
            up_stats.append({'url': u, 'name': alias, 'ok': True,
                             'total': len(items), 'matched': matched})
            extra = ''
            if ads:
                extra += ' 拒运营源%d条' % ads
            if ipv6_cnt:
                extra += ' 跳IPv6 %d条' % ipv6_cnt
            log('[直播上游] %s %d条, 命中%d%s' % (
                alias or u, len(items), matched, extra))
        except Exception as e:
            up_stats.append({'url': u, 'name': alias, 'ok': False,
                             'err': str(e)[:60]})
            log('[直播上游失败] %s -> %s' % (alias or u, str(e)[:60]))

    # ---- v14: 广告分发站剔除(用户拍板"干净优先, 宁少勿滥") ----
    # 判据: 裸 IP 且承载 >= LIVE_AD_HOST_CH 个不同频道。
    # 线上实测 143 条里 131 条是裸 IP, 而 107.150.60.122 一个 IP 扛 33 个台、
    # 63.141.230.178 扛 25 个、38.75.136.137 扛 24 个 —— 正规 CDN 不会这么部署,
    # 这是"IPTV 线路分发面板", 靠起播贴片变现, 起播广告就是它加的。
    # 代价(已与用户确认): 52 个台里约 31 个只有这类线路, 会被清空 —— 宁可少。
    pairs = [(k, u) for k, c in cands.items() for u in c['urls']]
    host_map = host_channel_map(pairs)
    # v15: 先查归属。判"是不是广告站"不能只看"扛几个台" —— 大陆运营商的
    # 省网 IPTV 一台扛 20 多个台是标准部署, 美国 Nocix 的 VPS 扛 30 个台
    # 才是卖线路的面板。同一个数字, 两种结论, 唯一的区分依据是机器在哪。
    geo = annotate_geo(set(host_map.keys()))
    ad_hosts = pick_ad_hosts(host_map, geo)
    # 被归属判据"救下来"的大陆高承载源 —— 必须报出来, 否则用户看不出
    # "为什么这轮没把某个扛 20 台的 IP 砍掉"
    saved_cn = sorted([(h, len(host_map[h])) for h in host_map
                       if is_bare_ip(h) and len(host_map[h]) >= LIVE_AD_HOST_CH
                       and geo.get(h) == 'CN'], key=lambda x: -x[1])
    if saved_cn:
        log('[归属豁免] %d 个大陆运营商源扛>=%d个台, 按"正常部署"保留: %s'
            % (len(saved_cn), LIVE_AD_HOST_CH,
               ', '.join('%s(%d台)' % (h, n) for h, n in saved_cn[:6])))
    ad_stats = {'hosts': [], 'dropped': [], 'n_line': 0,
                # v14: 剔除前的候选台数 —— 熔断用它判"上游是不是挂了",
                # 而不是用淘汰后的出线数(那会被主动剔除误判成腰斩)
                'n_chan_cand': len(cands)}
    if ad_hosts:
        ad_stats['hosts'] = sorted(
            [(h, len(host_map[h])) for h in ad_hosts], key=lambda x: -x[1])
        for key in list(cands.keys()):
            keep = {u: m for u, m in cands[key]['urls'].items()
                    if (urllib.parse.urlsplit(u).hostname
                        or '') not in ad_hosts}
            ad_stats['n_line'] += len(cands[key]['urls']) - len(keep)
            if keep:
                cands[key]['urls'] = keep
            else:
                ad_stats['dropped'].append(key)
                cands.pop(key)
        log('[直播广告站] 剔除 %d 个裸IP分发站(单IP扛>=%d个台), 共 %d 条线路: %s'
            % (len(ad_hosts), LIVE_AD_HOST_CH, ad_stats['n_line'],
               ', '.join('%s(%d台)' % (h, n) for h, n in ad_stats['hosts'][:6])))
        if ad_stats['dropped']:
            log('[直播广告站] 只剩这类线路、因"宁少勿滥"清空的台 %d 个: %s'
                % (len(ad_stats['dropped']), ', '.join(ad_stats['dropped'])))

    tasks = []
    for key, c in cands.items():
        for u, meta in rr_pick(c['urls'], LIVE_MAX_CAND):
            tasks.append((key, u, meta.get('ua'), meta.get('raw'),
                          meta.get('ref')))
    log('直播候选线路共 %d 条(白名单频道 %d 个), 第一轮浅探(只判活)...' % (
        len(tasks), len(cands)))
    probe_res = {}
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        futs = {ex.submit(probe_live, u, ua, ref, False):
                (key, u, raw, ua, ref)
                for key, u, ua, raw, ref in tasks}
        for fu in as_completed(futs):
            key, u, raw, ua, ref = futs[fu]
            r = fu.result()
            # v15: 标上"是不是大陆/域名源"。境外机器连不上大陆源是地理问题,
            # 不是死链 —— 后面要靠这个标记把它们捡回来, 不白丢。
            r['cn'] = is_clean_src(u, geo)
            probe_res[(key, u)] = {'r': r, 'raw': raw,
                                   'ua': ua, 'ref': ref}

    # ---- v14: 起播实测(间隔抓两次索引, 判"伪直播/起播贴片") ----
    # 为什么必须实测: 起播贴片发生在"按下播放那一刻", 而抓一次索引时滑窗
    # (通常 3-6 段)早已滑过去, 静态文本里根本看不见 —— 这就是 v5~v13 一直
    # 说"测不出"的真正原因, 不是测不出, 是判据看错了地方。
    # 只对每台排前 LIVE_START_CHECK 条做: 每台最终只用 3 条, 全量做既慢又无意义。
    # 两阶段抓取, 中间**全局只等一次**; 若按每条各等一次, 200 条就是 200 倍等待。
    start_cands = []
    for key in cands:
        rows = [(u, d) for (k2, u), d in probe_res.items()
                if k2 == key and d['r'].get('ok')]
        rows.sort(key=lambda x: (-((x[1]['r'].get('w') or 0) *
                                   (x[1]['r'].get('h') or 0)),
                                 x[1]['r'].get('ms') or 0))
        for u, d in rows[:LIVE_START_CHECK]:
            start_cands.append((key, u, d['ua'], d['ref']))

    preroll_flags = {}     # (key,u) -> 判为伪直播的原因
    if start_cands:
        def _snap_all():
            out = {}
            with ThreadPoolExecutor(MAX_WORKERS) as ex:
                futs = {ex.submit(fetch_snapshot, u, ua, ref): (key, u)
                        for key, u, ua, ref in start_cands}
                for fu in as_completed(futs):
                    out[futs[fu]] = fu.result()
            return out

        snap_a = _snap_all()
        # 等待时长要够让滑窗真的前移: 慢切片(target=20s)等 10 秒也可能不动,
        # 所以取 max(基础间隔, 最大target+2), 上限 20 秒, 避免拖长整轮
        mx = max([s['target'] for s in snap_a.values() if s] or [0])
        gap = min(20, max(LIVE_START_GAP, mx + 2))
        log('直播起播实测: %d 条候选, 间隔 %d 秒抓第二次索引...'
            % (len(start_cands), gap))
        time.sleep(gap)
        snap_b = _snap_all()
        for ck in snap_a:
            fake, why = judge_preroll(snap_a[ck], snap_b[ck])
            if fake:
                preroll_flags[ck] = why
        log('[直播起播广告] 判为伪直播/贴片 %d 条%s' % (
            len(preroll_flags),
            (': ' + '; '.join('%s(%s)' % (k, v)
                              for (k, _), v in list(preroll_flags.items())[:5]))
            if preroll_flags else ''))

    # ---- v13 第二轮: 只给每台排在前面的几条测真画质(方案C) ----
    # 排序依据只能是浅探拿得到的信息: master 声明的分辨率(有就信, 它写在
    # 索引里是本台的自我声明) > 探活延迟(快线优先)。死线(ok=False)不占名额,
    # 否则名额会被"能连上但 404"的线白白吃掉。
    deep_cands = []
    for key in cands:
        rows = [(u, d) for (k2, u), d in probe_res.items()
                if k2 == key and d['r'].get('ok')
                and (k2, u) not in preroll_flags]
        rows.sort(key=lambda x: (-((x[1]['r'].get('w') or 0) *
                                   (x[1]['r'].get('h') or 0)),
                                 x[1]['r'].get('ms') or 0))
        for u, d in rows[:LIVE_DEEP_PER_CH]:
            deep_cands.append((key, u, d['ua'], d['raw'], d['ref']))
    if not PROBE_AVAILABLE:
        log('[直播] 无 ffprobe/ffmpeg, 跳过深探 —— 本轮线路全部按"未实测"记账')
    elif deep_cands:
        log('直播第二轮深探(ffprobe 实测画质) %d 条 / 候选 %d 条' % (
            len(deep_cands), len(tasks)))
        with ThreadPoolExecutor(MAX_WORKERS) as ex:
            futs2 = {ex.submit(probe_live, u, ua, ref, True): (key, u, raw)
                     for key, u, ua, raw, ref in deep_cands}
            for fu in as_completed(futs2):
                key, u, raw = futs2[fu]
                prev = probe_res.get((key, u)) or {}
                probe_res[(key, u)] = {'r': fu.result(), 'raw': raw,
                                       'ua': prev.get('ua'),
                                       'ref': prev.get('ref')}

    channels = []
    preroll_dropped = []    # v14: 因起播广告被清空的台
    n_cn_blind = 0          # v15: 靠"大陆盲源"补位的线路总条数
    cn_blind_chans = []     # v15: 只有靠盲源才出线的台(境外测不出, 需家里验证)
    for key, c in cands.items():
        hd = []        # v2: 高清层(>=720p) —— 主力线路
        backup = []    # v2: 备用层(>=540p) —— 换线保底, 有得换好过没得换
        unver = []     # v8: 索引合法但测不出分辨率(见下方说明)
        cn_blind = []  # v15: 大陆/域名源, 境外机器连不上 —— 地理问题, 不是死链
        n_preroll = 0  # v14: 本台被起播实测剔掉的条数
        for (k2, u), d in probe_res.items():
            r, raw = d['r'], d['raw']
            if k2 != key:
                continue
            if not r['ok']:
                # v15: 这一行是整轮的关键。以前浅探失败一律 continue, 而大陆
                # 运营商源在 GitHub 的境外机器上必然失败 —— 于是央视那 19 个台
                # 的干净源一条都进不来, 清单里只剩境外广告站。现在把大陆源
                # 单独收起来, 出线不够时补位, 让"家里能看但境外测不出"的源
                # 有机会出现在电视上。
                if r.get('cn'):
                    cn_blind.append({'url': u, 'w': 0, 'h': 0,
                                     'method': 'cn-blind', 'ms': 9999})
                continue
            # v14: 起播实测判为伪直播/贴片的线一律不收(用户拍板"宁少勿滥")
            if (k2, u) in preroll_flags:
                n_preroll += 1
                continue
            w, h, method = r['w'], r['h'], r['method']
            # 清晰度必须实测: 实测不出分辨率的线路一律不收(名字标签不作依据)
            if not w:
                if r.get('hls'):
                    unver.append({'url': u, 'w': 0, 'h': 0,
                                  'method': 'unverified', 'ms': r['ms']})
                continue
            kbps = r.get('kbps') or 0
            entry = {'url': u, 'w': w, 'h': h, 'method': method,
                     'ms': r['ms'], 'kbps': kbps}
            if w >= LIVE_MIN_W and h >= LIVE_MIN_H:
                # v9: 分辨率够但码率虚低 -> 降到备用层, 不让假高清占主位
                if kbps and kbps / (w * h / 1000.0) < LIVE_MIN_BPP:
                    entry['thin'] = True
                    backup.append(entry)
                else:
                    hd.append(entry)
            elif h >= LIVE_BACKUP_MIN_H and w >= 960:
                backup.append(entry)
        # v2 排序: 稳定优先 —— 延迟分档(快者优先) > 分辨率 > 实测方式
        # 旧版"分辨率优先"会把高画质但高延迟的烂线排第一, 导致 CCTV-1 老卡
        def stab_key(x):
            ms_tier = 0 if x['ms'] < 1500 else (1 if x['ms'] < 3000 else 2)
            return (ms_tier, -(x['w'] * x['h']), -(x.get('kbps') or 0),
                    0 if x['method'] == 'ffmpeg' else 1, x['ms'])
        hd.sort(key=stab_key)
        backup.sort(key=stab_key)
        # v8: 兜底判定抽到 pick_live_urls(), 便于单测
        # v15: 实测线不足 LIVE_KEEP 时, 用大陆盲源补位(最多 LIVE_CN_BLIND_KEEP)。
        # 为什么允许补: 宁可给用户一条"可能不通但一定没广告"的线(不通就切下一条),
        # 也不给他一条"一定通但先播 30 秒广告"的线。
        fill = cn_blind[:LIVE_CN_BLIND_KEEP] if len(hd) + len(backup) < LIVE_KEEP else []
        urls, n_unver = pick_live_urls(hd, backup, unver + fill)
        n_cn_used = sum(1 for x in urls if x.get('method') == 'cn-blind')
        if n_cn_used:
            n_cn_blind += n_cn_used
            cn_blind_chans.append(key)
        if n_unver:
            log('[直播兜底] %s 无实测线, 收 %d 条未实测(仅索引可达)'
                % (key, n_unver))
        if not urls:
            if n_preroll:
                preroll_dropped.append((key, n_preroll))
            continue
        channels.append({'name': key, 'group': c['entry']['group'],
                         'epg': c['entry'].get('epg'),
                         'urls': urls,
                         'n_cand': len(c['urls']),
                         'n_hd': len(hd), 'n_backup': len(backup),
                         'n_unver': n_unver, 'n_cn': n_cn_used})

    order = {g: i for i, g in enumerate(LIVE_GROUPS)}
    channels.sort(key=lambda c: (order.get(c['group'], 9), c['name']))
    if preroll_dropped:
        log('[直播起播广告] 因起播实测被清空的台 %d 个: %s'
            % (len(preroll_dropped),
               ', '.join('%s(%d条)' % (k, n) for k, n in preroll_dropped)))
    ad_stats['preroll'] = preroll_flags
    ad_stats['preroll_dropped'] = preroll_dropped
    ad_stats['cn_blind'] = n_cn_blind
    ad_stats['cn_blind_chans'] = cn_blind_chans
    ad_stats['saved_cn'] = saved_cn
    ad_stats['geo'] = geo
    if n_cn_blind:
        log('[大陆盲源] 境外测不出、按"地理原因"保留 %d 条, 涉及 %d 个台: %s'
            % (n_cn_blind, len(cn_blind_chans), ', '.join(cn_blind_chans[:10])))
    return channels, up_stats, ad_stats


def write_ads_pool(con, one):
    """v10: 广告域名清单 —— 只写文件不启用。
    用户要求"先看清单再启用", 所以脚本每轮把采集结果落成 ads_pool.json,
    report 里也列一份。确认没有误伤(比如把图床域名当成广告)后,
    把 ADS_ENABLE 改成 True, 下一轮就会写进 tvbox.json 的 ads 字段。"""
    data = {'enabled': ADS_ENABLE,
            'min_votes': ADS_MIN_VOTES,
            'consensus': con,          # >=ADS_MIN_VOTES 个仓同时列出, 启用后会写进配置
            'single': one,             # 只有1个仓列出, 可能有误伤, 默认不启用
            'note': ('consensus = 至少 %d 个不同仓同时列出的域名(启用后生效); '
                     'single = 只有1个仓列出, 误伤风险高, 不启用'
                     % ADS_MIN_VOTES)}
    with open(ADS_POOL, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log('ads_pool.json: 共识 %d / 单仓 %d (当前%s)' % (
        len(con), len(one), '已启用' if ADS_ENABLE else '仅采集未启用'))


def write_live(channels):
    """生成 live.txt(TVBox txt 多线路) / live.json(OSC) / live.m3u; 返回 lives 结构
    v7: 去掉从未使用过的 repo 参数(签名骗人比缺参数更糟)
    v12: 主力源改为 live.txt —— 用户在影视仓 V5.0.31 实测:
         · live.m3u 每台3条 -> 频道列表里一台显示成3个同名台(146条/52台)
         · live.json(OSC)   -> 影视仓直接不认, 加载失败
         · txt 的 "台名,url1#url2#url3" 是唯一同时满足"一台一次"+"可切线路"
           且各分支都支持的写法, 故作为主推; m3u/json 保留给别的播放器用"""
    lives = []
    for g in LIVE_GROUPS:
        chans = [c for c in channels if c['group'] == g]
        if chans:
            lives.append({'group': g, 'channels': [
                {'name': c['name'], 'urls': [u['url'] for u in c['urls']]}
                for c in chans]})
    with open(LIVE_JSON, 'w', encoding='utf-8') as f:
        json.dump({'lives': lives}, f, ensure_ascii=False, indent=2)

    # ---- v12: live.txt (TVBox type 0) ----
    # 格式: 空行分隔分组, 分组行是 "组名,#genre#", 台行是 "台名,地址1#地址2#地址3"
    # 台名/地址里的逗号与 # 会被解析器切错, 建库时统一兜一层(实测现有数据干净,
    # 但上游池随时可能冒出带逗号的台名, 这里不留给线上翻车的机会)
    tl = []
    for g in LIVE_GROUPS:
        chans = [x for x in channels if x['group'] == g]
        if not chans:
            continue
        if tl:
            tl.append('')          # 分组之间留空行, 纯为可读性(解析器会跳过)
        tl.append('%s,#genre#' % g)
        for c in chans:
            urls = [u['url'] for u in c['urls'] if '#' not in u['url']]
            if not urls:
                continue
            tl.append('%s,%s' % (c['name'].replace(',', ' '), '#'.join(urls)))
    with open(LIVE_TXT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(tl) + '\n')

    lines = ['#EXTM3U x-tvg-url="%s"' % LIVE_EPG]
    # v12: 每台只写 LIVE_M3U_KEEP 条(默认1)。m3u 无法表达"一个台多条线路",
    # 写多了就是多个同名频道; 换线需求由 live.txt 承接。
    for g in LIVE_GROUPS:
        for c in [x for x in channels if x['group'] == g]:
            for u in c['urls'][:LIVE_M3U_KEEP]:
                # v9: 带上 tvg-id/tvg-name, 播放器才能把台名对到 EPG 的 channel
                epg = c.get('epg')
                attr = (' tvg-id="%s" tvg-name="%s"' % (epg, epg)) if epg else ''
                lines.append('#EXTINF:-1%s group-title="%s",%s' % (
                    attr, g, c['name']))
                lines.append(u['url'])
    with open(LIVE_M3U, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    log('live.txt: %d 个频道, 共 %d 条线路(每台一台多条); '
        'live.m3u: 每台 %d 条' % (
            len(channels), sum(len(c['urls']) for c in channels),
            LIVE_M3U_KEEP))
    return lives


# ---------------- 归集(点播) ----------------

def collect():
    """归集: 返回 (接口池pool, 仓列表stores含cfg, 导航父子表nav_children)
    v2: 双层去重 —— URL归一化(剥代理前缀/参数/尾斜杠) + 内容指纹(站点api集合哈希)
    不同多仓指向同一子仓时只收一次(先到先得, 记日志)
    v3: nav_children = {导航仓url: [(子仓名, 子仓url)]} 供清理版重建
    v7: 结构防御 —— sites/storeHouse/urls 任意畸形只跳过该项, 不让整轮崩"""
    pool = {}
    stores = []
    nav_children = {}
    seen_store = set()
    seen_fingerprint = {}
    # v12: 25 -> 40。挺好分享多仓一家就 32 个子仓, 25 会漏掉最后 7 个
    SUB_LIMIT = 40
    # v7: norm_store_url / store_fingerprint 已提到模块级(纯函数, 可单测)

    def add(api, name, origin):
        api = as_str(api).strip()
        if not api.startswith('http'):
            return
        key = re.sub(r'^https?://', '', api).lower()
        if key not in pool:
            pool[key] = {'api': api, 'name': as_str(name), 'origin': origin}

    def add_store(name, url, cfg):
        norm = norm_store_url(url)
        if norm in seen_store:
            return False
        fp = store_fingerprint(cfg) if isinstance(cfg, dict) else None
        if fp and fp in seen_fingerprint:
            log('[重复仓跳过] %s 与已收录的 %s 内容相同' % (
                as_str(name or url)[:30], seen_fingerprint[fp][:30]))
            return False
        seen_store.add(norm)
        if fp:
            seen_fingerprint[fp] = (name or url)
        stores.append({'name': name or urllib.parse.urlparse(url).netloc,
                       'url': url, 'cfg': cfg,
                       'kind': store_kind(cfg)})
        return True

    def extract_apis(cfg, origin):
        n = 0
        for s in as_list(as_dict(cfg).get('sites')):
            if not isinstance(s, dict):
                continue
            if as_str(s.get('type')).strip() != '3':
                continue
            api = as_str(s.get('api'))
            if api.startswith('http') and not api.split('?')[0].rstrip('/').endswith('.js'):
                add(api, as_str(s.get('name')), origin)
                n += 1
        return n

    def sub_entries(cfg):
        """子仓列表: storeHouse(对象数组) + urls(数组/字符串混用).
        v7: 字段若是 dict/字符串也不再崩(原 (sh or {}).get 遇字符串直接炸)"""
        cfg = as_dict(cfg)
        out = []
        for sh in as_list(cfg.get('storeHouse')):
            d = as_dict(sh)
            u = as_str(d.get('sourceUrl'))
            if u.startswith('http'):
                out.append((as_str(d.get('sourceName')), u))
        for u in as_list(cfg.get('urls')):
            if isinstance(u, dict):
                uu = as_str(u.get('url'))
                if uu.startswith('http'):
                    out.append((as_str(u.get('name')), uu))
            elif isinstance(u, str) and u.startswith('http'):
                out.append(('', u))
        return out

    def follow_store(url, alias, depth=0):
        """递归跟进一个仓: 自己进stores, 若是多仓则继续跟子仓(验到最后一级)
        v2: 重复仓(同URL马甲/同内容)直接不跟, 避免同一子仓被多个多仓重复拉入
        v3: 记录导航仓→子仓关系, 供清理版重建"""
        norm = norm_store_url(url)
        if norm in seen_store or len(stores) >= SUB_TOTAL_LIMIT:
            return 0
        try:
            cfg = fetch_cfg(url, 12)
        except Exception as e:
            log('[聚合跳过] %s -> %s' % (url, str(e)[:60]))
            return 0
        if not add_store(alias, url, cfg):
            return 0
        n = extract_apis(cfg, '聚合')
        if depth >= SUB_DEPTH:
            return n
        subs = sub_entries(cfg)[:SUB_LIMIT]
        if subs:
            nav_children[url] = subs   # v3: 记录父子关系
        for nm, s2 in subs:
            try:
                n += follow_store(s2, nm, depth + 1)
            except Exception as e:      # v7: 单个子仓再烂也不影响其它子仓
                log('[子仓跳过] %s -> %s' % (s2, str(e)[:50]))
        return n

    if not os.path.exists(SOURCES):
        log('缺少 sources.txt')
        return pool, stores, nav_children
    with open(SOURCES, encoding='utf-8-sig') as f:
        lines = [ln.strip() for ln in f]
    dir_lines = [ln for ln in lines if ln.startswith('D|')]
    for line in lines:
        if not line or line.startswith('#') or line.startswith('L|') \
                or line.startswith('D|'):
            continue
        if line.startswith('@'):
            rest, alias = split_alias(line[1:])
            try:
                cfg = fetch_cfg(rest, 15)
            except Exception as e:
                log('[订阅失败] %s -> %s' % (rest, e))
                continue
            if not add_store(alias, rest, cfg):
                continue   # v2: 与已收录仓重复, 子仓也无需再跟
            n = extract_apis(cfg, '订阅')
            subs = sub_entries(cfg)[:SUB_LIMIT]
            if subs:
                nav_children[rest] = subs   # v3: 记录父子关系
            nsub = 0
            for nm, s2 in subs:
                try:
                    nsub += follow_store(s2, nm, 1)
                except Exception as e:
                    log('[子仓跳过] %s -> %s' % (s2, str(e)[:50]))
            log('[订阅] %s 直提%d接口, 跟进%d子仓及孙仓(接口+%d)' % (
                rest, n, len(subs), nsub))
        else:
            if '|' in line:
                u, alias = line.rsplit('|', 1)
                add(u.strip(), alias.strip(), '手动')
            else:
                add(line, '', '手动')

    # ---- 目录仓发现: 自动从网上找点播源(无需人工) ----
    for dline in dir_lines:
        rest, alias = split_alias(dline[2:])
        try:
            j = fetch_cfg(rest, 20)
        except Exception as e:
            log('[目录仓失败] %s -> %s' % (rest, e))
            continue
        resources = [x for x in as_list(j.get('resources')) if isinstance(x, dict)]
        found = 0
        for item in resources:
            cat = as_str(item.get('category'))
            if cat not in DIR_CATEGORIES:
                continue
            u = as_str(item.get('url'))
            nm = as_str(item.get('name'))
            if not u.startswith('http'):
                continue
            if norm_store_url(u) in seen_store:
                continue
            try:
                cfg = fetch_cfg(u, 12)
            except Exception as e:
                log('[发现跳过] %s %s -> %s' % (nm, u, str(e)[:50]))
                continue
            try:
                add_store(nm, u, cfg)
                extract_apis(cfg, '发现')
            except Exception as e:
                log('[发现跳过] %s -> %s' % (u, str(e)[:50]))
                continue
            found += 1
            if found >= DIR_LIMIT:
                break
        log('[目录仓] %s 发现%d个新仓(共%d资源)' % (
            alias or rest, found, len(resources)))
    return pool, stores, nav_children


# ---- v9: 熔断 ----
# 防的是"全网抖动那天把能用的配置覆盖成空的"—— 无人值守系统里, 这是唯一
# 能让电视变砖的路径。用户拍板: 腰斩(跌到一半)才拦, 正常波动不干预。
CIRCUIT_RATIO = 0.5
CIRCUIT_MIN_API = 6    # 基线太小(刚起步)时比例没有意义, 不拦
CIRCUIT_MIN_CH = 10


def load_prev_stats():
    """读上一版 tvbox.json 得到 (接口数, 频道数) 作为熔断基线。
    读不到(首次运行/文件缺失)返回 (0,0): 没有基线就不拦, 不能因为
    "第一次跑"就把正常结果也拦下来"""
    try:
        with open(TVBOX, encoding='utf-8') as f:
            cfg = as_dict(json.load(f))
    except Exception:
        return 0, 0
    n_api = len(as_list(cfg.get('sites')))
    n_ch = sum(len(as_list(as_dict(g).get('channels')))
               for g in as_list(cfg.get('lives')))
    return n_api, n_ch


def circuit_broken(prev_api, prev_ch, new_api, new_ch, cand_ch=None):
    """本轮比上一版腰斩则熔断, 返回 (是否熔断, 原因)

    v14: cand_ch = 本轮"归集到的候选台数"(剔除广告站之前)。给了它就用它判
    频道维度 —— 因为 v14 主动剔除广告分发站会让出线台数从 52 腰斩到 21,
    那是策略不是故障。拿淘汰后的数去比, 每轮都会误判成"上游挂了"从而拒绝
    写文件, 改动等于完全没生效。候选数没掉, 说明上游是好的。
    不给 cand_ch 时行为与旧版完全一致(老测试照旧通过)。"""
    if prev_api >= CIRCUIT_MIN_API and new_api < prev_api * CIRCUIT_RATIO:
        return True, '接口数腰斩 %d -> %d' % (prev_api, new_api)
    if cand_ch is not None:
        # 上游抖动 -> 连候选都归集不到; 主动淘汰 -> 候选不变、只有出线变少
        if prev_ch >= CIRCUIT_MIN_CH and cand_ch < prev_ch * CIRCUIT_RATIO:
            return True, '候选频道数腰斩 %d -> %d' % (prev_ch, cand_ch)
        # 兜底: 全部被剔干净也不能真的写一份空直播出去
        if new_ch == 0 and prev_ch >= CIRCUIT_MIN_CH:
            return True, '直播台全部被剔除(0 个)'
        return False, ''
    if prev_ch >= CIRCUIT_MIN_CH and new_ch < prev_ch * CIRCUIT_RATIO:
        return True, '频道数腰斩 %d -> %d' % (prev_ch, new_ch)
    return False, ''


def load_deadlog():
    if not os.path.exists(DEADLOG):
        return {}, {}
    try:
        with open(DEADLOG, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}, {}
    if not isinstance(data, dict):      # v7: 文件被人手改坏也不崩
        return {}, {}
    if 'apis' in data or 'stores' in data:
        return as_dict(data.get('apis')), as_dict(data.get('stores'))
    return data, {}


def save_deadlog(apis, stores):
    # v7: 键排序后再写 —— 否则插入顺序一变就是一次无意义的 diff
    with open(DEADLOG, 'w', encoding='utf-8') as f:
        json.dump({'apis': dict(sorted(apis.items())),
                   'stores': dict(sorted(stores.items()))}, f,
                  ensure_ascii=False, indent=1)


def write_multi_files(scored, nav_children):
    """v3: 导航仓重建清理版(multi/mN.json) —— 只装体检合格的子仓.
    病根: 电视加载别人家的导航文件时会拉它全部子仓, 死一个(如摸鱼儿)
    就弹"拉取配置失败"; 清理版把死子仓天然剔除, 电视加载我们的文件.
    v4: 清理版分数=所装子仓的最低分(一票否决精神), 作为1个仓进前3竞争.
    返回 [(显示名, 文件名, 最低子仓分)] 供 store.json 引用"""
    multi_dir = os.path.join(HERE, 'multi')
    shutil.rmtree(multi_dir, ignore_errors=True)
    os.makedirs(multi_dir, exist_ok=True)
    info = {as_str(s.get('url')): s for s in scored}   # url -> 体检结果
    out = []
    idx = 0
    for nav_url, children in nav_children.items():
        nav = info.get(nav_url)
        if not nav or nav.get('state') != '保留':
            continue   # 导航仓本身不在保留状态则不重建
        kept = []
        subs = []
        for nm, cu in children:
            cs = info.get(cu)
            if not cs:
                continue   # 子仓没跟进来(拉取失败) —— 正是要跳过的
            # v7: 嵌套导航仓(kind=nav)不进清理版, 避免问题复现
            if cs.get('state') == '保留' and cs.get('kind') != 'nav' \
                    and cs.get('score') is not None \
                    and cs['score'] >= STORE_MIN_HEALTH:
                kept.append({'sourceName': as_str(nm or cs.get('name') or '未命名')[:16],
                             'sourceUrl': tv_url(cu)})
                subs.append(cs['score'])
        if len(kept) < 2:
            log('[导航清理] %s 合格子仓仅%d个, 不生成' % (
                as_str(nav.get('name') or nav_url)[:20], len(kept)))
            continue
        idx += 1
        fn = 'multi/m%d.json' % idx
        # v13: 与 store.json 同步改 urls 格式(storeHouse 电视上选不开)
        with open(os.path.join(HERE, fn), 'w', encoding='utf-8') as f:
            json.dump({'urls': [{'url': k['sourceUrl'], 'name': k['sourceName']}
                                for k in kept]},
                      f, ensure_ascii=False, indent=2)
        mscore = min(subs) if subs else 0   # v4: 清理版分数=最低子仓分
        disp = '%s·清理版%d仓' % (as_str(nav.get('name') or '多仓')[:14], len(kept))
        out.append((disp, fn, mscore, len(kept)))
        log('[导航清理] %s -> %s (合格%d/%d, 最低子仓%d分)' % (
            disp, fn, len(kept), len(children), round(mscore * 100)))
    return out


# ---- v9: 大仓容错 ----
# 病根: 健康分=存活站点数/总站点数, 而完美线是 100%。200 个站点的仓死 1 个
# 就是 99.5%, 永远够不到完美线, 竞争不过 3 个站全活的小仓 —— 结果清单里
# 剩下的都是"小而全"的仓, 内容最少。大仓恰恰是内容最多的那些。
# 用户拍板保留一票否决精神, 只对大仓放开 1 个名额。
BIG_STORE_SITES = 30       # 站点数达到此值算大仓
BIG_STORE_TOLERANCE = 1    # 大仓允许的死站数(小仓仍然一个都不能死)


def is_perfect_store(s):
    """完美仓判定: 小仓必须 100%, 大仓允许死 1 个"""
    score = s.get('score')
    if score is None:
        return False
    if score >= STORE_PERFECT:
        return True
    total = s.get('total') or 0
    alive = s.get('alive') or 0
    return total >= BIG_STORE_SITES and (total - alive) <= BIG_STORE_TOLERANCE


def write_store(scored, repo, live_ok, multi_entries=None, mine_sites=0):
    """v12: 电视点播清单 = 我的直播 + 最多 STORE_TOP_N 个"点进去真能用"的仓

    与 v10 的根本差别: 入选标准从"站点存活数"换成"子资源通道".
    v10 只看健康分, 于是高天流云(298站, 99% 子资源压在 gh-proxy 上)拿了
    100 分排在第一位, 而它在家里电视上点进去就是无限转圈 —— 分数骗人。
    v12 三条硬门槛:
      ① gh_rate > STORE_GH_MAX    -> 不收(再健康也是转圈)
      ② sites_n == 0              -> 不收(挂上去就是电视上一个红点)
      ③ score < STORE_MIN_HEALTH  -> 不收(站点大半打不开)
    然后按 gh_rate 升序(直连优先) > 分数降序 > 内容量降序 取前 STORE_TOP_N。
    某天某个仓死了, 它自然跌出前三, 后面的自动补位 —— 这就是"源失效后
    别的源自动加进来"的实现方式, 不需要人工干预。
    """
    entries = []
    # v12: "我的影视"(tvbox.json) 只在真有站点时才挂。
    # v10 无条件挂, 而 sources.txt 里直链接口一条都没配 -> sites 恒为 0,
    # 电视上就是一个带红点的空壳, 点它必报"接口无效 No value for uri"。
    if repo and mine_sites > 0:
        mine = 'https://%s/%s/main/tvbox.json' % (RAW_HOST, repo)
        entries.append({'sourceName': '我的影视',
                        'sourceUrl': tv_url(mine)})
    elif repo:
        log('我的影视: tvbox.json 站点数为 0, 不挂进清单(挂了就是电视上一个红点)')
    if repo and live_ok:
        # v12: 指向 live.txt —— live.json(OSC) 在影视仓 V5.0.31 上不认
        own = 'https://%s/%s/main/live.txt' % (RAW_HOST, repo)
        entries.append({'sourceName': '我的直播',
                        'sourceUrl': tv_url(own)})

    # ---- 候选池: 只收体检保留的真仓; 导航入口不参与(子仓已独立参选) ----
    cands = []
    cut_gh = cut_empty = 0
    for s in scored:
        if s.get('kind') == 'nav' or s.get('score') is None:
            continue
        if s.get('state') == '剔除':
            continue
        gh = s.get('gh_rate')
        gh = 1.0 if gh is None else gh
        if (s.get('sites_n') or 0) <= 0:
            cut_empty += 1
            continue
        if gh > STORE_GH_MAX:
            cut_gh += 1
            log('[通道淘汰] %s: GitHub系子资源 %d/%d = %d%% > 上限%d%%, '
                '上电视会转圈, 不收' % (
                    as_str(s.get('name'))[:20], s.get('gh_cnt') or 0,
                    s.get('res_total') or 0, round(gh * 100),
                    round(STORE_GH_MAX * 100)))
            continue
        cands.append({'s': s, 'gh': gh,
                      'score': s.get('score') or 0,
                      'total': s.get('sites_n') or 0,
                      'jar': s.get('jar_rate') or 0,
                      # v9 的大仓容错继续沿用: >=30站的仓允许死1个仍算完美
                      'perfect': is_perfect_store(s)})

    # 极端情况兜底: 严格筛完一个都不剩时, 放宽到"最不坏的那几个", 并在
    # 报告里标警告 —— 空清单等于点播全废, 比慢一点更糟
    relaxed = False
    if not cands:
        pool2 = [s for s in scored
                 if s.get('kind') != 'nav' and s.get('score') is not None
                 and s.get('state') != '剔除' and (s.get('sites_n') or 0) > 0]
        if pool2:
            relaxed = True
            log('[警告] 没有任何仓满足 GitHub 依赖 <= %d%%, 本轮放宽取最接近的; '
                '请在 sources.txt 里补直连型仓' % round(STORE_GH_MAX * 100))
            for s in pool2:
                cands.append({'s': s, 'gh': s.get('gh_rate') or 1.0,
                              'score': s.get('score') or 0,
                              'total': s.get('sites_n') or 0,
                              'jar': s.get('jar_rate') or 0,
                              'perfect': is_perfect_store(s)})

    # 直连优先 > 完美仓 > 组内(v9: 完美看内容量, 其余看分数) > jar率
    # gh 放第一位: 0% 与 99% 的差别是"秒开"和"转圈", 比分数差几分重要得多
    # perfect 组按 total 降序是 v9 的大仓容错: 200 站死 1 个仍是 99.5%,
    # 不该被 3 站全活的小仓挤掉 —— 大仓才是内容最多的那个
    cands.sort(key=lambda c: (c['gh'],
                              0 if c['perfect'] else 1,
                              -c['total'] if c['perfect'] else 0,
                              -c['score'], -c['jar'], -c['total']))
    chosen = cands[:STORE_TOP_N]
    for c in chosen:
        s = c['s']
        name = '%s %d分' % (as_str(s.get('name') or '未命名')[:16],
                            round(c['score'] * 100))
        if c['gh'] > 0:
            name += '·G%d' % round(c['gh'] * 100)   # 带 GitHub 依赖的标出来
        entries.append({'sourceName': name,
                        'sourceUrl': tv_url(as_str(s.get('url')))})

    # v13: 多仓格式 storeHouse -> urls。实锤过程(2026-10-28):
    #   同一个仓(挺好分享 thdjk.json), 在 GYCK.json 的 {"urls":[{"url","name"}]}
    #   格式里电视能选开; 在本仓 {"storeHouse":[{"sourceName","sourceUrl"}]}
    #   格式里列表能显示、选中就"接口无效"。影视仓 V5.0.31 对 storeHouse
    #   只展示不兼容, urls 是饭太硬/肥猫/GYCK 通用的主流写法。
    cfg = {'urls': [{'url': e['sourceUrl'], 'name': e['sourceName']}
                    for e in entries]}
    with open(STORE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log('store.json 已生成: %d 个入口(直播%d + 点播仓%d; 候选%d, '
        '通道淘汰%d, 空仓淘汰%d%s)' % (
            len(entries), 1 if (repo and live_ok) else 0, len(chosen),
            len(cands), cut_gh, cut_empty, ', 已放宽' if relaxed else ''))
    for c in chosen:
        log('  [入选] %-20s 站点%3d  GitHub依赖%3d%%  jar打包%3d%%  %d分' % (
            as_str(c['s'].get('name'))[:20], c['total'],
            round(c['gh'] * 100), round(c['jar'] * 100),
            round(c['score'] * 100)))


def write_report(items, alive, suspect, deadlist, scored, channels,
                 up_stats, now, trip='', ad_stats=None):
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    lines = ['# TVBox 源检测报告', '', '检测时间: %s (北京时间)' % now, '']
    if trip:
        lines += ['> ⚠️ **本轮触发熔断：%s**' % trip,
                  '>',
                  '> `tvbox.json` / `store.json` / `live.json` / `live.m3u` '
                  '**保持上一版未变**（没有覆盖成空配置）。',
                  '> 下面的数据只代表本轮检测结果，**不代表电视当前正在用的配置**。',
                  '> 若连续两天熔断，请手动跑一次 `workflow_dispatch` 或在'
                  ' `sources.txt` 里换上游。', '']
    lines += ['## 一、直链接口检测 (tvbox.json)', '',
              '| 状态 | 名称 | 接口 | 延迟 | 说明 |', '|---|---|---|---|---|']

    def row(tag, it, ms, note):
        name = as_str(it.get('name') or '未命名')[:24]
        # v9: 搜索不通的接口单独标出来 —— 这类接口在电视上能打开仓但一搜就空
        if it.get('search') is False:
            note = ('**搜索不可用**' if not note else note + ' · **搜索不可用**')
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
    # v10: 上表是"检测结果", tvbox.json 只取前 MAX_SITES —— 差多少要说清楚,
    # 否则看报告的人会以为漏写了
    if len(alive) > MAX_SITES:
        lines += ['', '> `tvbox.json` 实际只收录前 %d 个站点'
                      '（排序：搜索可用优先，其次延迟）。本轮截断 %d 个，'
                      '完整存活清单见上表。' % (MAX_SITES, len(alive) - MAX_SITES)]

    lines += ['', '## 二、仓体检 (store.json, 按健康分排序)', '',
               '健康分 = 站点存活占比; 含CMS接口深测(实际调用验证有真实数据'
               '+抽真实视频测广告); ext内嵌接口递归拆测; 子仓跟到孙仓级', '',
               '门槛: 低于 %d%% 或抽测发现插广告的仓本轮剔除(下轮达标自动回来);'
               ' 多仓入口不参与分数' % (STORE_MIN_HEALTH * 100), '',
               'v2: 抽测影片<720p降6分(点播画质); 首页无推荐/海报挂降分(推荐质量);'
               ' v6: 惩罚分<门槛余量(0.10), 只影响排名不再等于剔除;',
               '同内容克隆仓(不同多仓指向同一子仓)自动去重只收一次;',
               '**v12 新增两列, 它们决定点进去会不会转圈**:',
               '- `GH依赖` = 子资源(jar/外挂js/接口)里落在 GitHub 系(含 gh-proxy'
               ' 等各类加速代理)的比例。**超过 %d%% 直接不进清单** ——'
               ' 配置 json 几十KB, 走代理也能几秒拉到, 所以"仓能打开";'
               ' 但仓里的真实内容要另外下载, 全压在同一个慢通道上就是无限转圈。'
               '实测 gh-proxy 21KB/s, 七牛云 8.4MB/s, 差 400 倍' % round(STORE_GH_MAX * 100),
               '- `jar率` = 站点里 csp_ 打包型的占比。打包型只需下 1 个 jar,'
               ' 之后全在本地跑; 外挂 js 型每开一个站都要再发 1~2 次请求',
               'v12: 电视清单 = 我的直播 + 最多 %d 个仓, 按 GH依赖升序 >'
               ' 分数降序 > jar率降序 取前 %d; 某仓死亡自动跌出、后面自动补位'
               % (STORE_TOP_N, STORE_TOP_N), '',
               '| 状态 | 仓 | 分数 | 站点存活 | GH依赖 | jar率 | jar | 广告 | 画质/推荐 | 说明 |',
               '|---|---|---|---|---|---|---|---|---|---|']
    for s in scored:
        st = s.get('state') or ''
        score = s.get('score')
        if s.get('kind') == 'nav':
            pct = '入口'
        elif score is not None:
            pct = '%d%%' % (score * 100)
        else:
            pct = '—'
        site = '%d/%d' % (s.get('alive', 0), s.get('total', 0))
        if s.get('deep'):
            site += ' (深测%d)' % s['deep']
        if s.get('unknown'):
            site += ' (+%d未测)' % s['unknown']
        jar = s.get('jar_ok')
        jar_txt = '—' if jar is None else ('正常' if jar else '失效')
        ad = s.get('ad_hits') or 0
        ad_txt = ('发现%d' % ad) if ad else '未发现'
        qr = s.get('quality_h') or ''
        recn = s.get('rec_note') or ''
        qr_txt = ('%dp' % qr) if isinstance(qr, int) and qr else ''
        if recn:
            qr_txt = (qr_txt + ' ' if qr_txt else '') + recn
        if not qr_txt:
            qr_txt = '—'
        # v12: 通道画像两列 —— 判定"上电视会不会转圈"的直接依据
        gh = s.get('gh_rate')
        if gh is None:
            gh_txt = '—'
        elif s.get('res_total'):
            gh_txt = '%d%% (%d/%d)' % (round(gh * 100), s.get('gh_cnt') or 0,
                                       s.get('res_total') or 0)
        else:
            gh_txt = '—'
        if gh is not None and gh > STORE_GH_MAX:
            gh_txt += ' ✘转圈'
        jr = s.get('jar_rate')
        jr_txt = ('%d%%' % round(jr * 100)) if jr is not None else '—'
        lines.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            st, as_str(s.get('name') or '未命名')[:18], pct, site, gh_txt, jr_txt,
            jar_txt, ad_txt, qr_txt, s.get('note') or ''))
    kept = [s for s in scored if s.get('state') == '保留']
    lines.append('')
    lines.append('仓总数 %d | 保留 %d | 剔除 %d | 电视清单前%d(GitHub依赖升序优先, '
                 '>%d%% 直接不收)' % (
                     len(scored), len(kept), len(scored) - len(kept),
                     STORE_TOP_N, round(STORE_GH_MAX * 100)))

    lines += ['', '## 三、高清直播 (live.json, 频道白名单)', '',
               '收录标准: 主线路 ffmpeg拉流实测 >= 720p;'
               '备用线路 >= 540p(保证有线路可换);',
               '排序: 稳定优先(延迟分档) > 分辨率 —— 高画质但高延迟的烂线不再排第一;',
               '拒收: IPv6线路 / 运营源(livekey·txiptv·bfgd等贴片广告线) /'
               '实测不出分辨率(例外: 该台一条实测线都没有时, 收索引可达的'
               '兜底线, 表中标"未实测") /'
               '索引广告指纹(DISCONTINUITY>=2次拼接·分片>2倍中位数的异常长段'
               '·首片>1.8倍中位数的起播贴片·ts域名分散);',
               'v14(广告治理, 用户拍板"干净优先, 宁少勿滥"):',
               ' ① **广告分发站整站剔除** —— 裸IP且单IP扛 >=%d 个不同频道;'
               '    实测线上 107.150.60.122 一个IP扛33个台, 属"IPTV线路分发'
               '面板", 起播贴片就是它加的;' % LIVE_AD_HOST_CH,
               ' ② **起播实测** —— 每台前 %d 条候选间隔数秒抓两次索引:'
               '    MEDIA-SEQUENCE 不前进 / 出现 ENDLIST·PLAYLIST-TYPE:VOD'
               ' = 预置循环的伪直播, 直接不收(静态文本看不见起播那一刻,'
               '这才是"测不出广告"的真正原因);' % LIVE_START_CHECK,
               ' ③ 静态判据改**相对倍数**(旧版">=15s 且 >2.5x中位数"在 6 秒'
               '切片下退化成"15>15"恒假, 15秒广告必然漏判);',
               '代价: 只剩广告站线路的台会被清空 —— 宁可少, 不要打开就广告。',
               'v12: 主力源是 live.txt(TVBox txt 格式) —— 一行一个台, 多线路用'
               ' "#" 分隔, 电视上"一台只显示一次"+播放时可切线路;'
               ' live.m3u 每台只留 1 条(旧播放器兼容), live.json 保留备查。',
               'v13: 两级探测 —— 每台全部候选先浅探(只拉索引判活, 零 ffprobe),'
               ' 再对排最前的 %d 条用 ffprobe 实测画质; 因此"最佳画质"列是实测值,'
               ' 带 "(+N条未实测)" 表示还有 N 条备用线只做了探活。'
               % LIVE_DEEP_PER_CH, '']
    # ---- v14: 广告治理小节(用户最关心"为什么台变少了", 必须写在频道表前面) ----
    if ad_stats:
        hosts = ad_stats.get('hosts') or []
        drop_ch = ad_stats.get('dropped') or []
        pre = ad_stats.get('preroll') or {}
        pre_ch = ad_stats.get('preroll_dropped') or []
        lines += ['### 广告治理 (v15)', '']
        lines.append('**① 广告分发站剔除**: %d 个**境外**裸IP单IP扛>=%d个台, '
                     '共剔除 %d 条线路' % (
                         len(hosts), LIVE_AD_HOST_CH,
                         ad_stats.get('n_line', 0)))
        if hosts:
            lines += ['', '| 分发站IP | 承载频道数 |', '|---|---|']
            for h, n in hosts:
                lines.append('| `%s` | %d |' % (h, n))
            lines.append('')
        else:
            lines += ['', '本轮没发现(单IP扛<%d个台)' % LIVE_AD_HOST_CH, '']
        # v15: 被归属判据救下来的大陆高承载源 —— 不报出来的话,
        # 用户看到"某个 IP 扛 22 个台却没被砍"会以为程序漏了
        saved = ad_stats.get('saved_cn') or []
        if saved:
            lines.append('**①-b 归属豁免**: %d 个**大陆**裸IP扛>=%d个台, '
                         '按"运营商正常部署"保留(不算广告站): %s'
                         % (len(saved), LIVE_AD_HOST_CH,
                            '、'.join('`%s`(%d台)' % (h, n) for h, n in saved[:8])))
            lines.append('')
        cnb = ad_stats.get('cn_blind') or 0
        cnb_ch = ad_stats.get('cn_blind_chans') or []
        lines.append('**② 大陆盲源补位**: %d 条%s' % (
            cnb,
            ('（涉及 %d 个台: %s）' % (len(cnb_ch), '、'.join(cnb_ch[:10])))
            if cnb_ch else ''))
        lines.append('')
        lines.append('> 这些线在 GitHub 的境外机器上连不上(地理原因, 不是死链), '
                     '所以标"未实测"。它们在**你家的宽带上**很可能能播, '
                     '而且没有起播广告 —— 播放器会自动在几条线之间切换。')
        lines.append('')
        lines.append('**③ 起播实测**: 判为伪直播/贴片的线路 %d 条%s' % (
            len(pre),
            ('（%s）' % ', '.join(sorted(set(pre.values()))[:3])) if pre else ''))
        if drop_ch or pre_ch:
            lines.append('')
            lines.append('**④ 因"宁少勿滥"被清空的台**: %d 个' % (
                len(drop_ch) + len(pre_ch)))
            if drop_ch:
                lines.append('- 只有广告分发站线路: %s' % '、'.join(drop_ch))
            if pre_ch:
                lines.append('- 只有伪直播/贴片线路: %s' % '、'.join(
                    '%s(%d条)' % (k, n) for k, n in pre_ch))
        lines.append('')
        lines.append('> 这些台不是"源坏了"，是原来那条线打开就要先看广告。'
                     '想补回来只能加更干净的直播上游(见 sources.txt 的 L| 行)。')
    lines += ['', '| 频道 | 分组 | 线路数 | 高清/备线 | 最佳画质 |',
              '|---|---|---|---|---|']
    for c in channels:
        best = max(c['urls'], key=lambda u: u['w'] * u['h'])
        # v13: 判断依据从"有没有未实测线"改成"最好的那条线是否实测过" ——
        # 方案C 每台只深测前 2 条, 第 3 条备用线天生没有实测结果; 旧写法会让
        # 几乎每一台都显示"未实测", 把实测到的画质数字直接盖掉。
        if not best['w']:
            res = '未实测'
        elif best['h'] >= 2000:
            res = '4K'
        else:
            res = '%dp' % best['h']
        if best.get('kbps'):
            res += '/%dk' % best['kbps']
        # v13: 浅探阶段"没轮到深探"的线只有 master 自报的分辨率(它可能虚标
        # 1080p 实推 720p), 必须和 ffprobe 实测值区分开, 否则报告骗人
        if best.get('method') == 'master':
            res += '(声明)'
        if c.get('n_unver') and best['w']:
            res += ' (+%d条未实测)' % c['n_unver']
        hb = '%d/%d' % (c.get('n_hd', 0), c.get('n_backup', 0))
        lines.append('| %s | %s | %d | %s | %s |' % (
            c['name'], c['group'], len(c['urls']), hb, res))
    lines.append('')
    n_unver_ch = sum(1 for c in channels if c.get('n_unver'))
    n_all_unver = sum(1 for c in channels
                      if c.get('n_unver')
                      and not any(u['w'] for u in c['urls']))
    tail = ''
    if n_all_unver:
        tail += '；%d 个台整台无实测线(仅索引可达, 可能打不开)' % n_all_unver
    if n_unver_ch - n_all_unver:
        tail += '；%d 个台主线路已实测, 另有备用线只做了探活' % (
            n_unver_ch - n_all_unver)
    lines.append('直播频道 %d 个 / 线路 %d 条%s' % (
        len(channels), sum(len(c['urls']) for c in channels), tail))
    # v2: 直播上游健康表(上游挂了看得见, 手动补新上游有依据)
    lines += ['', '### 直播上游健康表', '',
               '| 状态 | 上游 | 命中白名单 | 总条数 | 备注 |',
               '|---|---|---|---|---|']
    for u in up_stats:
        if u.get('ok'):
            lines.append('| ✓ | %s | %d | %d | — |' % (
                as_str(u.get('name') or u['url'])[:36], u.get('matched', 0),
                u.get('total', 0)))
        else:
            lines.append('| ✗ | %s | - | - | %s |' % (
                as_str(u.get('name') or u['url'])[:36],
                as_str(u.get('err') or '失败')))
    lines.append('')
    lines.append('EPG 节目单: `%s`' % LIVE_EPG)

    if repo:
        raw = 'https://%s/%s/main/tvbox.json' % (RAW_HOST, repo)
        raws = 'https://%s/%s/main/store.json' % (RAW_HOST, repo)
        lines += ['', '电视端多仓入口 (gh-proxy 加速, 推荐):', '', '```',
                  GH_PROXY + raws, '```',
                  '', '电视端单仓入口 (直链+直播):', '', '```',
                  GH_PROXY + raw, '```']

    # ---- v10: 广告域名清单 ----
    # 只采不启, 清单在这里和 ads_pool.json 各出一份, 供人工过目后再开
    ads_con, ads_one = ads_consensus()

    def _fmt(lst, n=60):
        s = ', '.join(lst[:n])
        if len(lst) > n:
            s += '  …另有 %d 个' % (len(lst) - n)
        return s

    lines += ['', '## 四、广告域名清单 (ads_pool.json)', '',
              '当前状态: ' + ('**已启用** —— 已写进 tvbox.json 的 ads, '
                            '播放器会拦掉这些域名的请求'
                            if ADS_ENABLE else
                            '**仅采集未启用** —— tvbox.json 的 ads 仍为空。'
                            '确认下面没有误伤(把图床/封面域名当成广告)后, '
                            '把 `ADS_ENABLE` 改成 `True`, 下一轮自动生效'),
              '',
              '判定规则: 至少 %d 个不同仓同时列出的域名才算共识。'
              '单仓列出的不启用 —— 上游常有把正常图床写进 ads 的, 一启用连海报都加载不出来'
              % ADS_MIN_VOTES, '']
    if ads_con:
        lines += ['### 共识域名 (%d 个)' % len(ads_con), '', '```',
                  _fmt(ads_con), '```']
    else:
        lines.append('共识域名: 无。本轮没采到 —— 可能是上游仓全部拉取失败, '
                     '或各仓配置里本来就没写 ads 字段。')
    if ads_one:
        lines += ['', '### 单仓列出 (%d 个, 默认不启用)' % len(ads_one), '',
                  '```', _fmt(ads_one), '```']
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
    for fp in ['tvbox.json', 'store.json', 'live.txt', 'live.json', 'live.m3u',
               'dead_log.json', 'report.md']:
        push_file_gitee(repo, fp, token)
    # v3: 导航清理版文件也同步
    multi_dir = os.path.join(HERE, 'multi')
    if os.path.isdir(multi_dir):
        for fn in sorted(os.listdir(multi_dir)):
            push_file_gitee(repo, 'multi/' + fn, token)


def main():
    global SOURCES
    sources = SOURCES
    if '--sources' in sys.argv:
        i = sys.argv.index('--sources')
        if i + 1 < len(sys.argv):     # v6: 兼容参数写末尾, 不再 IndexError
            sources = sys.argv[i + 1]
    SOURCES = sources

    # ---- 点播归集 + 检测 ----
    # v7: 阶段级兜底 —— 归集/体检任一阶段炸了, 也继续出 live/store,
    # 不让"某天某个畸形仓"把当天所有产物一起带走
    try:
        pool, stores, nav_children = collect()
    except Exception as e:
        log('[阶段失败] 归集异常, 用空池继续: %s: %s' % (type(e).__name__, e))
        traceback.print_exc()
        pool, stores, nav_children = {}, [], {}
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
                    ms, s_ok = fu.result()
                    results[it['api']] = {'ok': True, 'ms': ms, 'search': s_ok}
                    tag = '' if s_ok is not False else '  [搜索不可用]'
                    log('[存活] %5dms  %s%s' % (ms, it['api'], tag))
                except Exception as e:
                    results[it['api']] = {'ok': False, 'err': str(e)[:80]}
                    log('[失败] %s -> %s' % (it['api'], e))

    scored = []
    if stores:
        try:
            scored = assess_stores(stores)
        except Exception as e:
            log('[阶段失败] 仓体检异常, 跳过点播仓: %s: %s' % (type(e).__name__, e))
            traceback.print_exc()

    # ---- 直播归集 + 实测 ----
    ad_stats = {}
    try:
        wl = build_whitelist()
        channels, up_stats, ad_stats = collect_live(wl)
    except Exception as e:
        log('[阶段失败] 直播归集异常, 本轮不带直播: %s: %s' % (type(e).__name__, e))
        traceback.print_exc()
        channels, up_stats = [], []
    # 注意: live.json 不在这里写 —— 要先过熔断, 否则会先把好文件覆盖掉

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
            alive.append(dict(it, ms=r['ms'], search=r.get('search')))
        else:
            fails = int(dead_apis.get(api, {}).get('fails', 0)) + 1
            dead_apis[api] = {'fails': fails, 'last_fail': now, 'reason': r['err']}
            rec = dict(it, fails=fails, reason=r['err'])
            if fails >= FAIL_LIMIT:
                deadlist.append(rec)
            else:
                suspect.append(rec)

    for s in scored:
        url = as_str(s.get('url'))
        score = s.get('score')
        if s.get('kind') == 'nav':
            # 多仓导航入口: 不打分也不参与剔除(子仓已各自独立体检)
            s['state'] = '保留'
            s['note'] = '多仓入口(导航), 子仓已独立体检'
            dead_stores.pop(url, None)
            continue
        if s.get('ad_hits'):
            # v9: 扣分而非一票否决 —— 3 个站点以上扣满 8 分, 跌破 90 才剔除
            cut = min(AD_HITS_PENALTY * s['ad_hits'], AD_HITS_PENALTY_MAX)
            if score is not None:
                score = max(0.0, score - cut)
                s['score'] = score
            s['note'] = '抽测 %d 个站点插广告, 扣 %d 分' % (
                s['ad_hits'], round(cut * 100))
        if score is not None and score >= STORE_MIN_HEALTH:
            s['state'] = '保留'
            dead_stores.pop(url, None)
        else:
            # 一轮即踢: 每轮从sources重新归集, 下轮达标自动回来
            s['state'] = '剔除'
            s['note'] = '健康分 %d%% 低于门槛 %d%%, 本轮剔除' % (
                (score or 0) * 100, STORE_MIN_HEALTH * 100)
            dead_stores[url] = {'last_fail': now, 'score': round(score or 0, 2)}
    # v6: 死计数按当前接口池裁剪 —— 旧接口早从 sources 删了还留在文件里,
    # 只增不减, 每天 commit 一次把仓库喂胖; v7: 仓的失败记录一并裁剪
    if items:
        cur_apis = {it['api'] for it in items}
        dead_apis = {k: v for k, v in dead_apis.items() if k in cur_apis}
    if scored:
        cur_stores = {as_str(s.get('url')) for s in scored}
        dead_stores = {k: v for k, v in dead_stores.items() if k in cur_stores}

    # ---- v9: 熔断 —— 本轮结果腰斩就不覆盖产物 ----
    # 放在 save_deadlog 之前: 熔断说明本轮检测不可信, 不该把失败次数累加进去
    prev_api, prev_ch = load_prev_stats()
    tripped, why = circuit_broken(prev_api, prev_ch, len(alive), len(channels),
                                  cand_ch=ad_stats.get('n_chan_cand'))
    if tripped:
        log('[熔断] %s' % why)
        log('       保留上一版 tvbox/store/live 不动, 本轮只更新 report')
        write_report(items, alive, suspect, deadlist, scored, channels,
                     up_stats, now, trip=why, ad_stats=ad_stats)
        return
    save_deadlog(dead_apis, dead_stores)
    lives = write_live(channels)

    # ---- 生成配置 ----
    # v10: 搜索可用的排前面 —— 截断到 MAX_SITES 时, 先被淘汰的是"能浏览但
    # 搜不到片"的接口(用户要的是搜得到、放得出, 不是站点数量多)
    alive.sort(key=lambda x: (1 if x.get('search') is False else 0, x['ms']))
    # v9: 搜索不通的接口不标 searchable —— 能浏览但不能搜, 标了反而更糟
    # (播放器把它当可搜索站点, 每次搜索都白等一轮超时)
    no_search = 0
    sites = []
    for i, it in enumerate(alive[:MAX_SITES], 1):
        name = as_str(it.get('name')) or urllib.parse.urlparse(it['api']).netloc
        srch = 0 if it.get('search') is False else 1
        if not srch:
            no_search += 1
        sites.append({'key': 'site%d' % i, 'name': name, 'type': 3,
                      'api': it['api'], 'searchable': srch,
                      'quickSearch': srch, 'filterable': 1})
    # v10: ads 播放器级域名拦截 —— 只采不启, 清单另存 ads_pool.json 供人工过目
    ads_con, ads_one = ads_consensus()
    write_ads_pool(ads_con, ads_one)
    cfg = {'wallpaper': '', 'sites': sites, 'flags': FLAGS,
           'ads': ads_con[:ADS_MAX] if ADS_ENABLE else [], 'lives': lives}
    with open(TVBOX, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log('tvbox.json 已生成: %d 个站点(存活 %d, 截断 %d; 其中搜索不可用 %d), '
        '%d 组直播, ads 拦截 %d 条' % (
            len(sites), len(alive), max(0, len(alive) - MAX_SITES), no_search,
            len(lives), len(cfg['ads'])))

    # v12: 清理版不再生成, 顺手删掉仓库里的旧 multi/(否则旧文件会一直留在
    # 仓库里, 而 store.json 已经不再引用它们, 留着只会让人以为还在生效)
    shutil.rmtree(os.path.join(HERE, 'multi'), ignore_errors=True)

    kept = [s for s in scored if s.get('state') != '剔除']
    # v7: 真仓在前、按分数降序; 导航入口只进报告不进 store.json
    kept.sort(key=lambda s: (1 if s.get('kind') != 'nav' else 0,
                             s.get('score') or 0), reverse=True)
    # v12: 导航清理版(multi/mN.json)停止参与 store 竞争。
    # 它当初是为"剔掉死子仓"而生, 现在多仓会整个展开成独立子仓各自体检,
    # 功能重复; 而且清理版文件存在自己仓库里(gh-proxy 通道), 光加载就要几秒,
    # 展开后又是一堆仓 —— 与"3个能看的"背道而驰。
    # 函数保留不删(产物备查), 只是不再进清单。
    write_store(kept, os.environ.get('GITHUB_REPOSITORY', ''),
                bool(channels), None, mine_sites=len(sites))
    write_report(items, alive, suspect, deadlist, scored, channels,
                 up_stats, now, ad_stats=ad_stats)
    log('report.md 已生成')

    if '--gitee' in sys.argv:
        sync_gitee()


if __name__ == '__main__':
    # v7: 顶层兜底 —— 打出完整堆栈再以非零码退出.
    # 非零退出会让 workflow 那一步失败, 从而不会把半成品 commit 上去
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
