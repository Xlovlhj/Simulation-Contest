# 上海瑞金医院外围交通仿真项目

本项目对应 [Simulation-Contest 的 xqw-b 分支](https://github.com/Xlovlhj/Simulation-Contest/tree/xqw-b)。Git上传范围与本地保留项见 [GITHUB_SYNC.md](GITHUB_SYNC.md)；C题成果在独立的ybs-c分支。

> **最新：V2 模型修订及论文同步已完成。**阅读 [新版论文PDF](submission/B_paper.pdf)、[可编辑Word](submission/B_paper.docx)，支撑材料为 [B_support_v2.zip](submission/B_support_v2.zip)。论文19页，采用内部SUMO反演、来源审计和44组治理结果；跨日预测仍弱，旧治理收益没有延续。用 `python3 project_v2.py` 查看结果，方法见 [REVISION_V2.md](REVISION_V2.md)。下文原网页、旧论文输出目录、视频及22组结果保留为V1历史记录；视频尚未同步。

本项目把百度地图的两段真实观测、一个 SUMO 交通模型，以及路径引导和应急协同策略放进同一套可查看、可复算的程序。研究范围是上海瑞金医院总院外围的部分地面走廊；医院接驳点采用路口研究设定，未核验院内急救入口。

## 直接查看

在项目根目录执行：

```text
python3 project.py
```

浏览器打开 `http://127.0.0.1:8765`。页面包含观测数据、22 组真实 SUMO 输出的动态回放、误差评价、策略对照和论文下载。按 Ctrl+C 结束服务。此入口只读取冻结结果，不发起百度请求。也可以直接打开 `outputs/app/index.html`，无需安装 SUMO 或输入地图密钥。

界面中的车辆运动来自实际输出的 FCD 轨迹；选择不同情景后，可以播放或拖动时间轴。数据页的“查看原始观测回放”展示冻结会话中的百度快照。两种时间轴分别标注：采集时刻是真实请求／接收时间，仿真时刻是设定情景的模拟秒。

## 已完成的工作与主要发现

- 两天合计 100 次路况、164 次路线响应；按同 OD、同五分钟桶去重后得到 160 条有效路线样本，分为训练 48 条、同日探索 16 条、跨日留出 96 条。采集器已于 2026-10-18 13:36:33 自动停止。
- 地图模型使用 7 个定位路口、6 个需求 OD、8 条完整候选路径，形成 307 条外部有向几何分段和 310 个允许转向。分段数量不等于真实街道或路口数量。
- 完成日常基准、特殊拥堵治理 A、应急协同 B 及需求／事件敏感性共 22 组实验。全部车辆到达，无碰撞、传送、丢弃；系统时间包括源点控制等待、SUMO 入网等待和在网旅行时间。
- 共享 ETA 等效模型的跨日 MAE 为 217.73 秒，路径训练中位数基准为 200.46 秒。这个结果说明当前短窗口模型跨时段迁移较弱，不能声称真实交通模型已标定成功。
- 在较高需求情景中，B 相对 A 将保障车辆用时从 292 秒降至 232 秒，普通车辆总系统时间减少 3340 车·秒；但有 30 辆普通车耗时增加。其他三个预设情景的普通车辆总系统时间增加 995—1000 车·秒，不能宣称 B 普遍有效。
- 112 项测试通过；在独立工作区从零重算全部 22 组实验与 ETA 评价，数值一致。相应证据位于 `outputs/quality/`。

车道、信号、需求量及应急管控权限缺少现场真值，因此仿真治理效果属于明确假设下的条件性结果。百度 ETA 是平台估计，不能代替现场车辆轨迹。观测层误差评价与微观仿真工程核查分别开展，不能互相替代。

## 交付文件

| 文件 | 用途 |
|---|---|
| `outputs/paper/paper.pdf`、`paper.docx` | 匿名论文及可编辑源文件 |
| `outputs/paper/ai_usage.pdf`、`ai_usage.docx` | 实际 AI 使用说明 |
| `outputs/video/B_demo.mp4` | 实际浏览器界面与仿真轨迹演示 |
| `submission/B_support.zip` | 筛选后的代码、必要数据、结果和离线页面 |
| `submission/SUBMISSION_CHECKLIST.md` | 队伍号、字体、承诺书和上传检查 |
| `outputs/final_experiments/summary.json` | 22 组实验的完整数值汇总 |
| `outputs/validation/summary.json` | 时间模型参数及各窗口误差 |
| `outputs/quality/fairness_audit.md` | 逐车收益和损失核查 |
| `outputs/quality/reproduction_audit.json` | 从零复算记录 |
| `PROJECT_PLAN.md`、`CHANGELOG.md` | 总体计划和追加式进展记录 |

提交包按 20 MB 上限筛选，不含密钥、环境、缓存、视频、承诺书和可重新生成的大型逐步 XML。包内 `SUPPORT_MANIFEST.json` 列出每个文件的大小及 SHA-256；原工作区保留完整原始仿真输出。论文和视频在官方系统分开提交。

## 检查与复算

```text
python3 project.py check
python3 project.py reproduce
```

`check` 在完整工作区检查来源、仿真产物、论文、视频和支撑包；在解压后的支撑包检查清单中每个文件的完整性。

`reproduce` 在本工作区新建 `.cache/reproduction_时间戳/`，重新冻结数据、运行全部 22 组 SUMO 实验、拟合训练层并评价保留窗口，再与已保存的数值比较。它不覆盖正式结果、不重新请求百度数据、不用既有结果冒充新运行。重算需要约数百 MB 工作空间和本地 TCP 端口供 TraCI 通信。

当前工作区已具备独立 `.venv`。在新的 Linux 环境中复算时，先用 Python 3.11 或更高版本建立本地环境并安装固定依赖：

```text
python3 -m venv .venv
.venv/bin/python -m pip --cache-dir .cache/pip install -r requirements-project.txt
python3 project.py reproduce
```

安装步骤需要联网下载依赖，仿真与评价过程离线。当前环境实际使用 Python 3.13、SUMO 1.28.0；跨平台二进制安装兼容性需按所在机器核实。依赖仅装入本目录，不修改系统或原有 Conda。

复算后的完整输出在终端报告的新目录中。若需要从该新目录重新生成可视化，使用其中的 `scripts/build_project.py`；解压包已带可直接查看的页面，不需要先重建页面。正式工作区的 `python3 project.py build` 读取完整运行 XML 重新生成页面；精简支撑包中的 build 会检查并复用已冻结的页面；从零计算仍使用 reproduce。

其他入口：

```text
python3 project.py status
python3 project.py build
python3 project.py package
python3 project.py video
```

`status` 仅查采集状态。`video` 需要本机已有 Google Chrome 和工作区中的固定录制依赖。论文生成入口为 `scripts/build_paper.py`，PDF 导出另需要 LibreOffice 与中文字体；这不是查看或复算的依赖。

## 提交前由队员完成

队员需要按赛方 AI 规定独立完成必要核心工作，并实质复核模型、代码、结果和实际 AI 使用范围；不得把尚未进行的人工审核写成已完成。按队伍号改名、用符合规范的宋体／黑体环境核对最终排版，签署独立承诺书，再由队员在官方系统完成提交。论文 DOCX 已指定宋体／黑体，但当前机器导出的 PDF 使用替代字体，具体字体见论文构建记录。

截止时间以赛题包为据：论文、承诺书及支撑材料为 **2026-10-20 20:00**，视频为 **2026-10-21 20:00**，均为北京时间。官方材料中视频命名及 AI 规则年份存在差异，提交时应核对系统和最新正式说明。
