# C题主Demo说明

更新日期：2026-10-22。队伍编号：CSF2605207。

**[查看／下载4K主Demo](../CCSF2605207fz.mp4)** · [SRT字幕](../CCSF2605207fz.srt)

主Demo已完成，用三维车辆和交互网页展示电动车节能研究：先讲研究目标和主要结论，再展示能耗模型、实测校准、控制策略与完整过程对比。视频文件保留赛题命名 `CCSF2605207fz.mp4`。

## 视频规格

| 项目 | 内容 |
|---|---|
| 时长 | 175秒，即2分55秒 |
| 分辨率与帧率 | 3840×2160，30fps，共5250帧 |
| 文件大小 | 35,466,973字节，约35.47MB |
| 格式 | MP4；H.264 High／yuv420p视频，AAC立体声48kHz音频 |
| 字幕 | 25条中文字幕，已烧录到画面；另附SRT |
| 配乐 | Kevin MacLeod《Carefree》，无旁白 |

成片已完成完整解码检查和7个关键时刻的画面检查。主Demo对应文件的SHA-256为：

```text
07a564b1da82a3be0097c79e67d4dea612ae527daf36de36b657c3acccc992f4
```

文件满足所给官方规则的MP4、3分钟内、至少1280×720和300MB以内要求。GitHub是项目展示入口，发布到这里不代表已向比赛系统提交。

## 展示内容与时间安排

| 时间 | 展示内容 |
|---|---|
| 00:00—00:12 | 研究目标与主要节能结论 |
| 00:12—00:30 | 车辆拆解、模型模块与车速对动力需求的影响 |
| 00:30—00:56 | 实际运行Q1模型，展示本次里程、电耗、电量、温度与能量分项 |
| 00:56—01:08 | 七参数比较，突出质量、滚动阻力和风阻 |
| 01:08—01:38 | 实测校准、验证误差、条件近似区间、原Rint与RC补充结果 |
| 01:38—02:03 | 回收能量流、72组方案选择与每5秒温控更新 |
| 02:03—02:15 | 低温雪景与高温效果，说明取暖和制冷负担 |
| 02:15—02:45 | 公平条件、完整行程、基准与优化、三温度对比 |
| 02:45—02:55 | 结论与实车验证边界；空白顶栏显示音乐署名 |

录制时实际运行了一次Q1原Python模型，完成3600个积分步，6项结果与归档一致。校准及策略部分展示已有研究结果。完整过程采用相同条件下的90分钟驾驶加120分钟停车温控，25℃、40℃、−10℃的模型节能率分别为11.83%、9.85%、0.70%；预调温未计，实车效果仍需验证。

## 最终材料与源码

main只收录最终论文、支撑附件、成果视频及必要展示说明。正式文件为[论文](../CCSF2605207.pdf)、[支撑附件](../CCSF2605207fj.zip)、[4K视频](../CCSF2605207fz.mp4)。视频来自2026-10-21本地录制版本。

完整研究工程保留在[ybs-c](https://github.com/Xlovlhj/Simulation-Contest/tree/ybs-c)，网页源码保留在[xqw-c-web](https://github.com/Xlovlhj/Simulation-Contest/tree/xqw-c-web)。网页分支是2026-10-20快照，后续本地录制新增的Q1入口和4K工具尚未同步到该分支；最终附件与各开发分支分别保留自己的版本记录。

main所有文件直接存储于Git，下载本页材料不需要Git LFS。文件大小及SHA-256见根目录`DELIVERY_MANIFEST.json`。

## 音乐来源与署名

配乐为 **《Carefree》— Kevin MacLeod**，原录音取自[作者官网](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1400037)，按[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)使用。

> Music: “Carefree” — Kevin MacLeod (incompetech.com).<br>
> Licensed under Creative Commons Attribution 4.0: https://creativecommons.org/licenses/by/4.0/<br>
> Source: https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1400037<br>
> Changes: excerpt, volume adjustment, fade in and fade out.

使用原曲0—175秒，调整音量，开头0.7秒淡入、最后6秒淡出，保持原速和音高。音乐署名同时保留在165—175秒的画面顶栏和MP4元数据中。上述许可适用于配乐，其他材料沿用各自许可。

## 网页画面来源与改编

视频录制的网页基于 **CarClear** 改编：

- [在线原作](https://glassbox.how/carclear/)；[源代码](https://github.com/bdeeps/carclear)。
- 采用的上游提交：`b60fa6e87277d65cb6b8bdc6bf82133bead202f6`，2026-10-20取得。
- 原代码版权所有者：**Deepankar Biswas**，采用[MIT许可](https://github.com/bdeeps/carclear/blob/b60fa6e87277d65cb6b8bdc6bf82133bead202f6/LICENSE)。
- 原内容署名：**“How does a car work?” by Glassbox (glassbox.how/e/carclear)**，采用[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；范围见上游[LICENSE-CONTENT.md](https://github.com/bdeeps/carclear/blob/b60fa6e87277d65cb6b8bdc6bf82133bead202f6/LICENSE-CONTENT.md)。

改编保留原三栏布局、三维车辆、拆解和道路场景，替换为中文研究内容，加入电池观察、冷热天气、论文结果窗口及演示操作；录制版进一步加入Q1运行入口、字幕、配乐和4K绘制。原作示例车辆参数不用于本研究计算，车辆几何与粒子效果用于结构示意。

Three.js及字体各自的许可证随网页源码保留；完整网页来源说明见[xqw-c-web的THIRD_PARTY_NOTICES.md](https://github.com/Xlovlhj/Simulation-Contest/blob/xqw-c-web/c_web_showcase/THIRD_PARTY_NOTICES.md)。Glassbox名称与标志不作为本项目标志。
