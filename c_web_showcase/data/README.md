# 当前网页数据

六页展示只请求下面两个JSON，均已随网页提供。首次启动无需重新导出或安装科学计算库。

| 文件 | 用途 |
|---|---|
| `research-parameters.json` | 参考/校准参数、部件曲线、控制协议与选定规则，供滑条计算当前状态 |
| `research-results.json` | 已完成实验的摘要与Q1静态曲线，供正文、柱图和依据窗口展示 |
| `physics-validation.json` | 当前状态公式与原Python模型的历史对比记录 |

参数文件的 `reference` 与 `calibrated` 分开使用，`curves` 保存电池等部件关系，`protocol` 和 `optimizedRule` 保存策略设定。`sources` 记录原支撑材料的相对路径和SHA-256。

结果文件的 `summaries` 包含：

- `q1`、`q1StaticCurve`：25℃、30分钟CLTC参考基准及100点静态曲线。
- `sensitivity`：7个参数、±5%和±10%两种调整幅度的比较。
- `calibration`、`calibrationGroups`、`intervals`：逐路程/分组误差与参数区间。
- `candidates`、`controlProtocol`：72组已完成的候选比较及规则协议。
- `control`：单循环，采用结束时热状态修正后的节能率。
- `sameTarget`：相同24℃座舱目标的单循环核查，沿用选定回收与冷却规则。
- `long`：90分钟驾驶加120分钟共同停车温控，采用实际总耗电计算节能率。

功率单位W，页面通常换算为kW；累计电量单位kWh，即“度电”；SOC原值为0—1，页面换算为百分比；SOC误差的单位为**百分点**。`energy_ape_pct`为单路程绝对百分比误差，`energy_mape_pct`为组内平均值。

Q1使用160Ah参考模型、初始电量80%；策略对照使用约74.4145Ah校准模型、初始电量94%、车内初温24℃。两者分别用于不同实验，不直接拼成节能对照。三温度节能率来自模型实验；滑条不会生成新的完整行程结果，也不会修改已记录结论。

`sources`中的路径指向生成这些摘要时的原始研究资料，不是网页请求地址。完整论文与支撑材料不属于网页运行依赖，也未随独立网页分支上传。来源论文摘要见 `../SOURCE_VERSION.json`。

更新数据时，先按项目README准备原始论文与支撑目录，再运行 `prepare_data.py` 和 `prepare_interactive_data.py`。前者生成的 `showcase.json`是导出中间文件，当前页面不加载它。
