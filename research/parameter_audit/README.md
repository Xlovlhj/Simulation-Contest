# v1 参数来源审计与公开资料核查

核查日期：2026-10-18。审计对象为已经交付的 v1，新增结果不回写为旧参数原先已经存在的依据。完整机器可读登记见 `parameter_register.json` / `parameter_register.csv`；6 个公开配置的 222 个叶字段见 `config_field_inventory.json`；审计文件 SHA-256 见 `audited_inputs.json`。不读取私密配置。

## 判断

SUMO 的道路旅行时间应该作为直接校准对象。把其输出再变换为 a×t+b，能够改善一个统计预测器，却不会改变仿真中的排队、吞吐或任务车辆运动；因此旧仿射式不能替代物理模型标定。百度 ETA 是平台估计，比较时必须对齐完整路线、方向、时段和计时起止。

原 1 m/s 和 0.5 m/s 拥堵参数没有直接观测或文献数值依据。它们在 v1 中确实注明为假设，但要增强现实依据，应由同一训练路径的速度/时间统计约束新分级场景，并重新跑 SUMO。路况长段平均速度不能直接写到特意挑选的 68.735m 小段作为限速；平均速度和车辆速度上限也不是同一物理量。

48 项登记覆盖显式数值、隐含软件默认和会影响时间/容量的结构简化。任务发布时间、每车控制上限、仿真时长和种子属于实验/算法设计，必须说明并检验敏感性，但不应编造其“实测来源”。车道、信号、流量、车辆组成属于对现实的断言，未核验时必须保留估计或假设标签。

## 分类统计

| 分类 | 项数 |
|---|---:|
| design_variable | 21 |
| design_variable_and_api_constraint | 1 |
| engineering_transform | 1 |
| estimated_pending | 1 |
| local_observation | 4 |
| local_observation_and_design | 1 |
| locally_estimated | 1 |
| software_default | 7 |
| unverified_event_parameter | 1 |
| unverified_governance_authority | 1 |
| unverified_local_parameter | 4 |
| unverified_local_structure | 5 |

## 参数逐项清单

| ID | 参数与当前值 | 来源分类 | 处理建议 |
|---|---|---|---|
| P01 | 地理中心、医院门址：上海黄浦区瑞金二路197号 | local_observation | 保留门址来源；入口维持未核验 |
| P02 | 路线坐标、方向和顺序连接：12条调查；307有向分段、310允许连接 | local_observation | 保留有证据连接，不能推断未采集转向或全域完备 |
| P03 | 有效观测路线时间：160 ETA样本，4 OD | local_observation | 作为带误差的校准目标；不写成实测车辆时间 |
| P04 | 拥堵段速度/长度/方向：部分路况响应提供，其他缺失 | local_observation | 按道路/方向/几何匹配；采用训练区间统计，不将速度直接等同限速 |
| P05 | 投影方法、地球半径：局部等距圆柱近似；R=6371008.8m | engineering_transform | 核验路线长度与平台距离偏差；报告坐标误差，不当高精度地图 |
| P06 | 坐标保留六位及节点相等判定：round(lng/lat,6) | design_variable | 保留为算法容差，避免近邻未经证据自动合并 |
| P07 | 路网边界/采样框/四组OD坐标：config/collection.json数值 | local_observation_and_design | 保留来源、说明裁剪边界和选择偏差 |
| P08 | 每段车道数：1 | unverified_local_parameter | 不得声称真实单车道；作为有效单通道模型，外部地图/现场核验后再改 |
| P09 | 基础限速和车辆最大速度：30km/h=8.3333m/s | unverified_local_parameter | 用有效通行速度标定或资料核验；不能将低速平台平均值当法定限速 |
| P10 | 路口优先规则/没有真实信号：priority、所有edge.priority=1 | unverified_local_structure | 有相位资料则建真实灯；暂无时只能估计有效控制损失并做结构敏感性 |
| P11 | 真实交叉口与形状点分层：每个折线端点皆node；未显式区分真实路口 | unverified_local_structure | 审计度为2的几何点与真实交叉口，防止内部转弯约束产生人工延误 |
| P12 | 车长：5m | software_default | 可保留作外部默认先验；别称本地标定 |
| P13 | 静止车间净距：2.5m | software_default | 保留默认先验并说明车辆密度/容量依赖该值 |
| P14 | 加速度：2.6m/s² | software_default | 未由本地轨迹识别，避免称车辆极限物理能力 |
| P15 | 舒适减速度：4.5m/s² | software_default | 固定默认或以可比轨迹建立外部先验，不自由调参追ETA |
| P16 | 跟驰期望时间间距：tau=1s | software_default | 论文按期望时间间距解释；tau不小于仿真动作步长 |
| P17 | 驾驶随机性：sigma=0 | design_variable | 作为确定性诊断；若启用默认随机性需多种子重复 |
| P18 | 个体期望速度分布：speedFactor=1、speedDev=0 | design_variable | 明确同质简化，不当软件整体默认 |
| P19 | 跟驰/换道模型：默认Krauss；未显式carFollowModel | software_default | 后续显式写Krauss并锁版本，提高审计可见性 |
| P20 | 其他隐含vType/netconvert默认：emergencyDecel、width、junction参数等未覆盖项 | software_default | 登记依赖版本；若参数对机制敏感再逐项显式化 |
| P21 | 车辆组成：100% passenger；同一vType | unverified_local_structure | 以小客车等效流解释；无证据不能造公交/货车比例 |
| P22 | 行人/非机动车/公交停靠/停车干扰：未建模 | unverified_local_structure | 纳入有效通行能力/阻抗含义，不能声称已识别具体原因 |
| P23 | 六OD流量与时序：初版60s/辆，正式30或15s/辆/OD；等量同步 | unverified_local_parameter | 用ETA+可匹配速度反演低维需求；报告区间与多解，避免每OD每时段自由参数 |
| P24 | 转向与路线分担：固定默认路径；A决策服从率100% | design_variable | 保留作为策略完全执行实验；现实效果须服从率敏感性 |
| P25 | 初始交通状态：空网；无热启动队列 | unverified_local_parameter | 校准应加入预热或明确等效状态；测量窗口与模拟热身分开 |
| P26 | 入网位置/速度/车道：默认位置、departSpeed=0、departLane=0 | design_variable | 检验边界延误对短路网时间影响，保留入网等待账目 |
| P27 | 拥堵强度：1m/s、0.5m/s | unverified_event_parameter | 由本地训练速度/ETA分位数导出强弱情景，再用仿真反演相应容量/速度参数；不直接冒充实测限速 |
| P28 | 拥堵位置与影响长度：瑞金二路上游6分段，68.735091m | design_variable | 保持反事实事件位置说明；如声称回放需能匹配真实拥堵段 |
| P29 | 事件开始/公开/持续时间：t=600，持续600或900s | design_variable | 若研究反事实可保留设计值；真实拥堵持续时间只能由有匹配的连续观测给区间 |
| P30 | 保障任务数量/发布时间/起点：1辆；t=720；west_to_hospital_road | design_variable | 保留为任务设计；在多个发布时间评估稳健性，不冒充急救案例 |
| P31 | 医院目标与通行权限：RJ_YJ外围公共道路点；院内入口未核验 | unverified_local_structure | 始终称外围接驳任务；未核验前不称真实院内救援 |
| P32 | 任务车辆性能/权限：与普通车辆相同 | design_variable | 保留为机制隔离；不能说明真实救护车全部优势或权限 |
| P33 | 控制上限：45s/辆、180s任务窗口 | design_variable | 无需为人为决策变量伪造来源；由代价预算/多值敏感性或优化选择，并在留出情景评价 |
| P34 | 源端控制权限：可暂缓全部冲突普通请求 | unverified_governance_authority | 以可控出发点实验表述；无授权不声称可直接现实部署 |
| P35 | A路径代价：sum(length/current_speed_limit) | design_variable | 以校准后的动态旅行时间代价改进；保留无未来信息边界 |
| P36 | B冲突判定：chosen route intersects remaining task corridor | design_variable | 逐车公平性及因果检查；若改变控制先保持需求/事件相同 |
| P37 | 仿真时长/发车时长：1800s/1200s | design_variable | 检验未完成车辆，必要时延长清空时间，不漏计成本 |
| P38 | 步长/FCD周期：1s/5s | design_variable | 保持安全时间尺度；必要时0.5s收敛检查；FCD稀疏采样不等同求解步长 |
| P39 | 随机种子：20261017 | design_variable | 不宣称一个种子构成统计重复；确定性当前重复只证明复现 |
| P40 | 时钟锚点：2026-10-18 12:00+08:00 | design_variable | 反事实场景清楚标注；校准实测窗口另按真实采样时间对齐 |
| P41 | 采样与去重协议：120s路况、300s路线；5min首次成功 | design_variable | 保留观测非均匀/重复删除说明；不是赛方最低门槛 |
| P42 | 训练/评价窗口及样本门槛：60/20/120min；每OD12/4/24 | design_variable | 不按评价结果挑窗；新v2不能称旧留出从未看过 |
| P43 | 共享仿射ETA形式：alpha*x+beta、alpha>=0、beta>=0 | design_variable | 降级历史参照，主模型应直接校准SUMO/观测过程 |
| P44 | 仿射系数及每OD中位数：1.8659500028、293.6906548s；各OD中位数 | locally_estimated | 可复算但非交通流/信号参数；不得把截距解释成红灯等待 |
| P45 | 数据质量/网络硬门槛：HTTP200/status0、非空正值、同路径/框内、无碰撞/传送 | design_variable | 分清工程通过与现实标定通过，硬编码307/310/30只绑定v1 |
| P46 | 几何与地理筛选门槛：20m重复定位；地理坐标接受框；<2km矩形对角线 | design_variable_and_api_constraint | 保留人工阈值性质与API来源，不当路口实际尺寸 |
| P47 | 未显式测量的交通能力：从单车道/默认跟驰/priority间接形成 | estimated_pending | 若用c=s*g/C，只对有效容量或绿比作有先验范围估计；s=1500–2000只是外部先验 |
| P48 | 源端与入网等待计时：从原始请求时间计全部等待 | design_variable | 保持指标定义并报告未完成/个体受损 |

## 公开数据核查

这些资料分为真实观测、方法与软件默认、仿真数据。公开可访问目录不代表已得到可用原始数据。外地/异时段数据只能作外部先验或方法验证，不能替代瑞金路 2026-10-17/18 的现场流量。

| 数据/项目 | 获取与许可 | 空间时间、可用字段 | 适配限制 |
|---|---|---|---|
| 上海公共数据平台 | 主页可访问；未找到本研究路口直接可下载的现行配时/计数 | 交通指数等目录 | 城市/区域指数不能当四OD流量；本次未找到不等于不存在 |
| 随申行科研数据 | 产品页可读，面向签约用户的沙箱；未申请/购买 | 上海多维交通资源，更新周期按产品 | 缺具体授权、费用与研究区覆盖证明 |
| 道路智能交通设备运行数据集 | 114.14MB/2文件目录，动态页需登录，许可待核 | 倒计时、信号阶段 | 无具体路口覆盖/日期/列定义，未纳入计算 |
| 上海 GNSS / 无人机轨迹 | DOI 10.17632/yx7sy7frnh.1；CC BY 4.0；页已下载，轨迹未下载 | 国泓路—政芳路，2022-12-12，24Hz；Time、N_Lat、E_Lon或Time、x、y | 70次试验车过路口，不是瑞金路、不是全体车辆；无法用来估计本地总流量 |
| pNEUMA | EPFL下载界面；许可声明存在BY-NC/BY不一致，按具体版本核实，未下载 | 雅典2018年10–11月；约0.04s；车辆类型、位置、速度、加速度、时间 | 配时未直接提供；适合排队/轨迹算法验证，不能迁移绝对流量 |
| NGSIM | 官方元数据实际HTTP200；CC BY-SA 3.0；下载入口公开 | 美国2005–2006，10Hz；位置、速度、加速度、车长、车道、转向、头距；Peachtree还有信号资料 | 距离和速度单位须按站点文档；选择城市走廊，避免高速数据作街区先验；无现场完全精度保证 |
| INTERACTION | 官方脚本仓库可读，数据官网本轮不可达；脚本BSD，数据许可待核 | 多地交互轨迹和语义地图 | 本轮未取得正式轨迹或确认瑞金覆盖，不能直接用于参数估计 |
| 上海嘉定4×4数据 | Zenodo 1.6GB ZIP入口；未下载、许可字段待核 | 30天TESSNG仿真，15min流速/时间、小时OD | 仿真生成，不是真实观测，不能作为“本地实测标定成功”的证据 |
| demandify | 官方README已下载；MIT软件；未安装运行 | 遗传算法优化需求，匹配分边分时速度，可导入离线数据 | 方法适配需解决百度的稀疏/选择性观测与匹配，不能直接复制外部OD |

## 有来源的估计路线

1. 冻结数据用途；现有跨日留出结果已被查看，新一轮须称“历史留出复核/探索性验证”，不能再称完全未见测试。
2. 先对齐输入。只比较相同完整路径，区分本地平台ETA、部分拥堵段平均速度、仿真行驶/入网/控制时间；排除统计口径不相容的硬约束。
3. 保留少量可解释未知量，例如几组走廊的有效通行阻抗、整体需求倍率。车道、信号、需求、驾驶行为全部同时自由优化，容易出现多个完全不同交通状态都匹配同一ETA的情形。
4. 优化目标直接读取SUMO旅行时间和可比速度，反复改变输入重跑。需要正则项或外部先验限制，记录参数在边界及多解，不再通过输出后加常数掩盖内部失配。
5. 需求强度有无现场计数仍是核心限制。由ETA反演的需求只能称“与观测相容的有效需求”；若多个值误差相近，报告范围与相应治理效果，而非选一个值当实际流量。
6. 若建立有效信号容量，采用 c=s×g/C 的有来源关系。FHWA 给出通用饱和流率常见1500–2000辆/(小时·车道)，可将1800用于初始先验。它来自国外指南，不是当地估计；g/C与流量没有额外观测时不能同时唯一辨认。
7. 事件强度用训练观测的路线或同几何步骤分位数构成压力等级。持续时间、任务时刻和控制预算保持明确设计变量，敏感性评价包含无效和受损情景。
8. 冻结标定参数后重新比较固定/A/B，保持同需求、事件和车辆性能，继续计全部等待、未完成和个体损失。

## 数值范围建议的边界

- 车辆长度5m、minGap2.5m、accel2.6m/s²、decel4.5m/s²、tau1s有SUMO默认依据，作为外部默认先验可保留；这些数值没有被验证为上海分布。
- sigma=0和speedDev=0是确定性简化，不能称默认；若采用随机驾驶，应追加多随机种子并报告方差。
- 拥堵速度不另指定“通常1m/s”。优先采用本地训练数据分位数作为模型拟合目标；缺几何对应的长段不能硬映射短段。
- 没有本地配时就不宣称某个90s或120s周期是真实信号；程序生成周期仅作结构敏感性/先验。
- 45s/180s、600s/900s、720s不需要冒充来自论文的常数。这些是可由预算、策略优化或敏感性决定的设计变量。

## 来源索引

- **SUMO_VTYPE** [Eclipse SUMO vehicle types and defaults](https://sumo.dlr.de/docs/Definition_of_Vehicles%2C_Vehicle_Types%2C_and_Routes.html)。已下载HTML并SHA-256留档；软件文档；项目不是从文档取得当地实测参数。
- **SUMO_SAFETY** [SUMO Safety](https://sumo.dlr.de/docs/Simulation/Safety.html)。网页检索可达；软件文档。
- **SUMO_NETWORK** [SUMO PlainXML](https://sumo.dlr.de/docs/Networks/PlainXML.html)。网页可读；软件文档。
- **SUMO_TLS** [SUMO Traffic Lights](https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html)。网页可读；软件文档。
- **FHWA_CAL** [FHWA Traffic Analysis Toolbox III, 2019, Chapter 5](https://ops.fhwa.dot.gov/publications/fhwahop18036/chapter5.htm)。已下载HTML并SHA-256留档；美国官方方法指南，可作方法依据；不是上海数据。
- **FHWA_CAPACITY** [FHWA Traffic Signal Timing Manual, Chapter 3](https://ops.fhwa.dot.gov/publications/fhwahop08024/chapter3.htm)。已下载HTML并SHA-256留档；美国官方通用经验，迁移时需明确外部先验。
- **FHWA_TIMING** [FHWA Signal Timing on a Shoestring III](https://ops.fhwa.dot.gov/publications/signal_timing/03.htm)。网页可读；美国官方方法指南。
- **DEMANDIFY** [demandify official repository](https://github.com/aonurakman/demandify)。README实际下载；未安装运行；MIT软件许可；其TomTom/OSM数据分别受数据协议约束。
- **SUMO_CAL_TUTORIAL** [SUMO San Pablo Dam calibration tutorial](https://sumo.dlr.de/docs/Tutorials/Calibration/San_Pablo_Dam.html)。上轮已核验；本轮登记；SUMO文档/开源示例。
- **SH_PORTAL** [上海市公共数据开放平台](https://data.sh.gov.cn/view/)。主页可达；未找到瑞金周边逐路口流量/现行信号可直接下载文件；各数据集按开放条件及用户协议。
- **SH_MAAS** [随申行产学研数据开放](https://www.shmaas.cn/solution/openResearchData.html)。产品页可达；需要签约/沙箱，未申请未付费；向已签约用户开放；具体费用未公布、没有获得使用授权。
- **SH_SIGNAL** [道路智能交通设备运行数据集](https://www.nbsdc.cn/general/dataLinks/16666.11.nbsdc.XfLd2PNT)。搜索可见114.14MB/2文件；正文动态加载、提示登录，未取得文件；许可字段本次未读出，待提供方核实。
- **SH_GNSS** [A high-precision vehicle trajectory dataset in Shanghai](https://data.mendeley.com/datasets/yx7sy7frnh/1)。数据页HTTP200实际留档；页面文件树动态加载，本轮两个公开API候选URL404，未下载轨迹；CC BY 4.0，DOI 10.17632/yx7sy7frnh.1。
- **SH_GNSS_PAPER** [Zhao, Ma, Wang (2024), Data in Brief 57:110864](https://pmc.ncbi.nlm.nih.gov/articles/PMC11404071/)。PMC全文可读；出版商直访403；论文开放获取，数据许可另见Mendeley。
- **PNEUMA** [EPFL pNEUMA dataset](https://open-traffic.epfl.ch/index.php/downloads/)。下载选择界面可达，未批量下载；主页出现CC BY-NC 4.0与FAQ CC BY 4.0不一致；EPFL学校页支持BY-NC，正式下载需按具体版本条款。
- **PNEUMA_LICENSE** [EPFL pNEUMA Open Data](https://www.epfl.ch/schools/enac/about/open-science/open-research/neuma/)。网页可读；CC BY-NC 4.0。
- **NGSIM** [FHWA NGSIM Vehicle Trajectories and Supporting Data](https://data.transportation.gov/Automobiles/Next-Generation-Simulation-NGSIM-Vehicle-Trajectories/8ect-6jqj)。官方元数据API HTTP200、56656字节实际留档；CSV/JSON导出入口公开；未下载全量；官方元数据licenseId=CC_30_BY_SA，CC BY-SA 3.0。
- **NGSIM_SITE** [NGSIM Peachtree metadata documentation](https://data.transportation.gov/api/views/8ect-6jqj/files/8b5a833b-e286-467f-8c55-af45be37c8ab?download=true&filename=4-+Peachtree+Street+Metadata+Documentation.pdf)。搜索可读PDF字段；未下载全量轨迹；数据按官方元数据许可。
- **INTERACTION** [INTERACTION official repository](https://github.com/interaction-dataset/interaction-dataset)。脚本仓库可达；interaction-dataset.com本轮两次无法访问；未下载正式轨迹；脚本BSD-3-Clause；正式数据许可未核验，不能用脚本许可替代。
- **JIADING_SIM** [Grid and Graph-based Autonomous Transportation System State Awareness Open Dataset](https://zenodo.org/records/22141918)。记录页与1.6GB ZIP链接可见，未下载；页面许可字段未展示，正式使用前需核实。
- **BAIDU_TRAFFIC** [百度实时路况概览与矩形查询](https://lbsyun.baidu.com/docs/webapi?title=traffic/traffic-rectangleseek)。既有本地原始响应有来源hash，本轮未请求新API；百度接口使用条款；凭据不进入研究产物。
- **BAIDU_DIRECTION** [百度轻量驾车路线规划](https://lbsyun.baidu.com/docs/webapi?title=directionlite/guide/webservice-lwrouteplanapi/dirve)。既有160有效ETA样本冻结，本轮未请求新API；百度接口使用条款。

## 本轮完成范围

已执行公开资料检索和部分小型文档/元数据下载，未购买数据、未发起新百度采集、未安装外部项目。下载的原文件及SHA-256位于 `downloads/download_manifest.json`。不修改v1实验数值或原始资料；v2物理估计和事件分级由独立程序结果给出。

## 本地道路补核（2026-10-18追加）

- 黄浦公安2024年正式答复确认医院周边非机动车/配送停车干扰，给出单行描述，但没有2026信号或机动车计数。2011历史报道有局部单向双车道描述，不能据此直接改写当前全网车道。来源见 `HP_POLICE_2024` / `HP_POLICE_2011`。
- 公开OSM查询两端分别返回502/500，尚未获得lanes/maxspeed/traffic_signals标签；未将任何猜测的标签写入正式GIS。
- 黄浦政府公示环评的索引表82出现了瑞金二路、建德路、思南路的车流/速度资料。PDF实际下载、日期/计数/预测参数已核验；2025-09-09—10昼夜20分钟计数可作本地历史先验，详见 `local_eia_evidence.json` / `.md`。不把历史道路计数分解成当前六OD流量。

## v2事件依据交接

`scenario_basis_v2.json` / `.md`由观测分析程序独立生成：使用训练窗西侧医院路线的完整同几何276m步骤（模型278.665139m）作为作用区，中度/强度基于Q25/Q50与Q10/Q50速度倍率。该作用区共同必经约209.93m，不能选择性声称全部可绕行；持续时长仍是设计变量。旧1/.5mps和68m事件保留为历史工程案例。

## 新增历史需求量级情景的审核

若假定3条已建模OD穿过瑞金共同断面，2025昼间621辆/h对应等分发车间隔17.3913秒。采用18秒时每OD200辆/h、三OD合计600辆/h，比历史值低3.38%。18秒是方便的实验取整，不是最邻近整数或统计最优。小型车实际仅168/207=81.16%；若只以小型车计数作参照则504辆/h。故18秒情景可称历史量级参照，不能称已恢复当前OD或PCU流量；宽需求敏感性与车型/方向/测点/未覆盖OD边界必须保留。详见 `historical_demand_reference.json`。
