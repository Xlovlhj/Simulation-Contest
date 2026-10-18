# S1 路线 ETA 状态层：冻结与时间留出评价

结论范围：建立了可复现的路线 ETA 预测与留出评价；SUMO 物理机制未经过现场流量、信号或逐车数据标定。

公式：共享模型 T_hat = alpha * T_SUMO,OD + beta；非负最小二乘只使用 17 日训练窗。默认状态为每 OD 训练中位数，跨日结果不用于选模型。

alpha=1.86595000, beta=293.69065481 s。

|分区|样本数|SUMO 原时耗 MAE(s)|共享 ETA 层 MAE(s)|OD 中位数 MAE(s)|
|---|---:|---:|---:|---:|
|training|48|418.3333|62.1226|57.6042|
|same_day_exploratory|16|467.1875|105.0003|119.7500|
|cross_day_holdout|96|200.6042|217.7292|200.4583|

共享模型比中位数更差的结果完整保留。等效路线残差 medianETA−SUMOtime 只表示两层间未解释时耗，不能解释为测得的排队时间。

## 证据边界

- Validation target is provider-estimated route ETA; no observed vehicle trajectories, counts or measured signal plans are available.
- The ETA observation layer is fitted; SUMO lane count, demand, speed and priority rules remain uncalibrated assumptions.
- Only four fixed routes and two short weekend windows are observed. Cross-day holdout also changes time of day; day and time-of-day effects cannot be separated.
- Repeated five-minute estimates are serially dependent; the 96 cross-day rows are not 96 independent traffic experiments.
- Both dates were already collected when this final protocol was documented. No holdout response enters the fitting objective or selection rule.
- The original 30-minute same-day validation was incomplete. This report explicitly labels the available 20 minutes exploratory.
- The source observation timestamp is absent; request/receive times bound API access, not the age of underlying traffic observations.
- No accuracy pass threshold is asserted, and a worse-than-median affine result is retained.
