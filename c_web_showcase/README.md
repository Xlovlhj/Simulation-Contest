# 电动车节能研究：C题成果展示

保留CarClear原来的深色三栏、中央大三维场景、右侧文字和滑条。内容按论文三问逐页深入：第一页先给研究目标与主要结论，后续展示建模、实测校准、策略选择和结果验证。车体、电池、道路、雪景和热浪用于说明研究过程。

原网页：[CarClear](https://glassbox.how/carclear/)；原源码：[bdeeps/carclear](https://github.com/bdeeps/carclear)。采用提交与许可见 `SOURCE_VERSION.json`、`THIRD_PARTY_NOTICES.md`。

## 启动

需要 **Python 3.10或更新版本**和支持WebGL的浏览器。页面所需数据、Three.js与字体已附带，普通浏览无需安装Python依赖，也无需原论文支撑包。

在项目根目录运行：

```text
python3 c_web_showcase/run.py
```

打开 **http://127.0.0.1:8765/**。程序会尝试自动打开浏览器；终端按Ctrl+C停止。

只启动服务：`python3 c_web_showcase/run.py --no-open`。端口占用可用 `python3 c_web_showcase/run.py --port 8766 --no-open`。请通过HTTP访问，不直接双击HTML。

## 从哪里开始体验

1. **研究了什么**：展示三问之间的关系，先给完整过程三温度节能结论；拆解和部件窗口解释模型模块。
2. **模型怎么算电**：从车速需求讲到电池用电、剩余电量和温度；展示25℃基准与七参数影响，保留道路受力交互。
3. **用实测校准**：直接比较验证路程的能耗和电量误差，解释参数反推和数据划分；独立电池与充放电交互保留。
4. **怎样选节能策略**：说明72组规则、5秒反馈和共同约束；电驱与回收场景解释策略作用。
5. **冷热环境下的取舍**：温度滑条带动雪景与热浪，同时解释取暖、冷却和回收之间的关系。
6. **节能结论**：三温度柱图对照；比较单循环、同24℃目标、90分钟驾驶加120分钟停车，查阅依据与适用条件。

页面用于交互演示，已移除各章测试题与答题评分。

拖动中央场景旋转，滚轮缩放；右下角重置视角。整体布局沿用原 `index.html`、`js/app.js` 和 `css/app.css`，没有全局轨迹播放器或时间轴。

## 研究计算与视觉示意

- 滑条调用原模型对应的静态动力学、电池Rint及瞬时热功率公式，计算当前选定状态。
- “节能结论”读取已有实验摘要，明确温度、初态和核算范围，不用滑条插值制造新的实验节能率。
- 前几章不是完整CLTC/长程求解。车辆布局、24个显示模块、展开电芯、雪和热浪用于理解原理，不能当成实车测量。
- 参考模型与校准模型分别标明。电池“充电”展示给定功率下的电学方向，不模拟完整充电机和BMS协议。

小型参数与摘要在 `data/research-parameters.json`、`data/research-results.json`。首次启动直接使用这些文件。只有维护者更新原研究数据时，才需要把原 `CCSF2605207fj/` 支撑材料与 `CCSF2605207.pdf` 放在项目根目录，依次运行：

```text
python3 c_web_showcase/prepare_data.py
python3 c_web_showcase/prepare_interactive_data.py
```

`data/physics-validation.json`记录与原Python模型静态公式的对比。`prepare_data.py`生成的旧格式 `showcase.json` 是第二步导出的中间输入，当前页面不读取它，网页分支也不附带它。

`compute.py`及Q1复算API是可选维护功能，需要原支撑材料和已有NumPy、pandas。启动时显示“可选Q1原模型复算：unavailable”仅表示该功能不可用，六页展示仍正常；程序不会自动安装依赖。

## 代码位置

| 文件 | 作用 |
|---|---|
| `index.html`、`js/app.js`、`css/app.css` | 原CarClear页面与章节调度；主要改中文和研究内容 |
| `css/research.css` | 小范围中文排版和窗口样式补充 |
| `js/chapters/index.js` | 注册六个研究章节 |
| `js/chapters/research-*.js` | 每页研究问题、做法、成果，以及原风格场景和控件 |
| `js/research/vehicle.js` | 从原electric场景提取的车辆几何与能流 |
| `js/research/weather.js` | 原道路运动和新增雪、热浪、冷暖光线 |
| `js/research/physics.js` | 有来源的当前状态计算 |
| `js/research/results.js` | 只读实验摘要 |
| `interaction_checks.py` | 新架构浏览器检查，需既有Chrome及工作区websocket依赖 |
| `browser_session.py` | 当前检查使用的Chrome辅助类，不包含旧仪表盘检查入口 |
| `data/README.md` | 当前两个展示数据文件的结构、单位与来源说明 |

本地开发过程的原站截图、临时浏览器和旧报告保存在 `.runtime/`，该目录不上传。网页分支的 `checks/` 保存精选验证记录，根目录 `docs/preview.png` 提供页面预览。

本次成果内容检查见 `.runtime/research_content_check/verification.json` 与 `followup_verification.json`：覆盖六页、新研究窗口、桌面及手机排版；后者记录标题缩短后的定向复查。前一份中一条同目标说明的文字匹配误判已在补查中更正，原报告保留。

旧仪表盘和原作展示视频只保留于本地开发历史，不随网页分支上传。正式视频仍需按修订方案录制、配音与验收。

完整方案：[C题网页与视频设计方案](../C题网页与视频设计方案.md)；资料缺口：[C题网页待确认事项](../C题网页待确认事项.md)。所有依赖与产物均留在工作区；不改论文或正式支撑包。
