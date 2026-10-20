# 来源与修改说明

本地项目基于 [CarClear](https://github.com/bdeeps/carclear) 修改。

- 在线原作：https://glassbox.how/carclear/
- 上游源码：https://github.com/bdeeps/carclear
- 采用提交：`b60fa6e87277d65cb6b8bdc6bf82133bead202f6`（2026-10-20取得）
- 原代码版权所有者：Deepankar Biswas，MIT许可；原文见根目录 `LICENSE`。
- 原说明、图片与视频采用CC BY 4.0，范围见 `LICENSE-CONTENT.md`。原作署名：“How does a car work?” by Glassbox (glassbox.how/e/carclear), CC BY 4.0。
- 内容许可链接：https://creativecommons.org/licenses/by/4.0/
- Three.js与字体各自的许可证保留在 `vendor/three/`、`fonts/`。

当前版本直接保留原 `index.html`、`js/app.js`、`css/app.css` 和 `js/ui.js` 的架构，修改中文内容并注册六个研究章节。复用原 `js/stage.js`、`js/kit.js`、`js/car.js`，将原 electric.js 的车辆几何提取到 `js/research/vehicle.js`，沿用原 anatomy.js 的拆解交互与 drive.js 的道路结构。新增电池观察、冷暖天气、研究静态计算和结果详情。原作示例EV物理参数不参与研究计算。此前轨迹仪表盘已退出主入口，备份仅保留为开发历史。

上游 `glassbox/`、`history.json`、原章节等文件只保留于本地下载历史；独立网页分支不附带原作展示视频、图片及未使用章节。当前页面所需的原代码、字体、三维库与许可均保留。

Glassbox名称与标志不作为本项目标志。车辆形状、部件布局及粒子运动属于结构示意；研究指标来自本工作区论文和支撑数据。开发使用了AI辅助，实质复核与正式竞赛提交另按实际情况记录。
