# V2：有来源的拥堵约束与拟合后治理对照

本报告使用直接 SUMO 运行时间，不在输出端乘加 ETA 拟合公式。基准运行约束来自训练路线时间的条件反演；交通量仍是多个固定需求剖面，不能视为唯一识别出的真实 OD 流量。

事件覆盖同几何的完整瑞金二路步骤：百度276米、模型278.665139米、19条边。中度/强度倍率分别来自训练有效速度Q25/Q50与Q10/Q50，乘在各边已拟合有效速度上。它们不是实测限速，也不证明存在对应事故。持续600秒是两个采样间隔的实验设计，300/900秒为可选设计尺度，本批次没有执行该时长敏感性。

扰动通过各外部道路边的可变速度约束实现；内部路口边保持拟合建网时的速度。模型因此模拟的是选定道路段的运行约束变化，不是把整条路线及所有路口耗时同比缩放。

前1200秒作为预热，1800秒发布并开始事件，1920秒发布任务，背景车辆释放至3000秒，观察到6000秒。发布事件前所有策略使用同样默认路线；A根据当时已知事件与各边有效速度比较完整路径，不预知恢复；B只暂缓任务发布后、尚未入网且与任务剩余路径重叠的普通请求。每车45秒、控制窗口180秒是算法约束，源端协调权限是假设。

背景OD错峰与反演一致。治理不额外加入反演时的18辆测量探针；S1在本批次重新运行，不能把反演probe ETA直接当作治理基准。完整事件段有约209.93米为医院方向共同必经路段，备选只能绕开上游部分，不保证A获得收益。

固定/A/B逐对校验车辆需求、事件与物理输入哈希，以及事件前FCD轨迹完全一致；A/B另校验任务发布前轨迹。全部源端控制等待及SUMO入网等待均从原始需求发布时间计入。若碰撞、异常传送、丢弃或未完成，保存原始结果并禁止宣称对照收益。

## 全部运行

| 条件/情景/策略 | 普通总系统时间(车·秒) | 事件后释放普通车总时间 | 普通源端等待 | 任务总时间(秒) | 未完成 | 物理有效 | 训练筛选通过 |
|---|---:|---:|---:|---:|---:|---|---|
| headway_60_none_S1_fixed | 173643 | 69460 | 0 | 无任务/未完成 | 0 | True | True |
| headway_60_step_q25_S2_fixed | 174571 | 70004 | 0 | 无任务/未完成 | 0 | True | True |
| headway_60_step_q25_S2_A | 174571 | 70004 | 0 | 无任务/未完成 | 0 | True | True |
| headway_60_step_q25_S3_fixed | 174571 | 70004 | 0 | 505.0 | 0 | True | True |
| headway_60_step_q25_S3_A | 174571 | 70004 | 0 | 505.0 | 0 | True | True |
| headway_60_step_q25_S3_B | 175268 | 70701 | 675 | 505.0 | 0 | True | True |
| headway_60_step_q10_S2_fixed | 175299 | 70391 | 0 | 无任务/未完成 | 0 | True | True |
| headway_60_step_q10_S2_A | 175299 | 70391 | 0 | 无任务/未完成 | 0 | True | True |
| headway_60_step_q10_S3_fixed | 175299 | 70391 | 0 | 533.0 | 0 | True | True |
| headway_60_step_q10_S3_A | 175299 | 70391 | 0 | 533.0 | 0 | True | True |
| headway_60_step_q10_S3_B | 175930 | 71022 | 675 | 533.0 | 0 | True | True |
| headway_30_none_S1_fixed | 365459 | 146039 | 0 | 无任务/未完成 | 0 | True | True |
| headway_30_step_q25_S2_fixed | 367262 | 147049 | 0 | 无任务/未完成 | 0 | True | True |
| headway_30_step_q25_S2_A | 367262 | 147049 | 0 | 无任务/未完成 | 0 | True | True |
| headway_30_step_q25_S3_fixed | 367441 | 147228 | 0 | 488.0 | 0 | True | True |
| headway_30_step_q25_S3_A | 367441 | 147228 | 0 | 488.0 | 0 | True | True |
| headway_30_step_q25_S3_B | 368878 | 148680 | 1233 | 518.0 | 0 | True | True |
| headway_30_step_q10_S2_fixed | 369164 | 148101 | 0 | 无任务/未完成 | 0 | True | True |
| headway_30_step_q10_S2_A | 369164 | 148101 | 0 | 无任务/未完成 | 0 | True | True |
| headway_30_step_q10_S3_fixed | 369378 | 148315 | 0 | 515.0 | 0 | True | True |
| headway_30_step_q10_S3_A | 369378 | 148315 | 0 | 515.0 | 0 | True | True |
| headway_30_step_q10_S3_B | 370874 | 149834 | 1233 | 540.0 | 0 | True | True |
| historical_scale_18_none_S1_fixed | 544711 | 217801 | 0 | 无任务/未完成 | 0 | True | False |
| historical_scale_18_step_q25_S2_fixed | 584648 | 255753 | 0 | 无任务/未完成 | 0 | True | False |
| historical_scale_18_step_q25_S2_A | 584648 | 255753 | 0 | 无任务/未完成 | 0 | True | False |
| historical_scale_18_step_q25_S3_fixed | 585769 | 256865 | 0 | 502.0 | 0 | True | False |
| historical_scale_18_step_q25_S3_A | 585769 | 256865 | 0 | 502.0 | 0 | True | False |
| historical_scale_18_step_q25_S3_B | 586033 | 256742 | 1989 | 502.0 | 0 | True | False |
| historical_scale_18_step_q10_S2_fixed | 585874 | 256169 | 0 | 无任务/未完成 | 0 | True | False |
| historical_scale_18_step_q10_S2_A | 585874 | 256169 | 0 | 无任务/未完成 | 0 | True | False |
| historical_scale_18_step_q10_S3_fixed | 586990 | 257264 | 0 | 519.0 | 0 | True | False |
| historical_scale_18_step_q10_S3_A | 586990 | 257264 | 0 | 519.0 | 0 | True | False |
| historical_scale_18_step_q10_S3_B | 588315 | 258215 | 1989 | 519.0 | 0 | True | False |
| headway_15_none_S1_fixed | 585987 | 234275 | 0 | 无任务/未完成 | 0 | True | False |
| headway_15_step_q25_S2_fixed | 608681 | 255768 | 0 | 无任务/未完成 | 0 | True | False |
| headway_15_step_q25_S2_A | 608681 | 255768 | 0 | 无任务/未完成 | 0 | True | False |
| headway_15_step_q25_S3_fixed | 605698 | 252556 | 0 | 339.0 | 0 | True | False |
| headway_15_step_q25_S3_A | 605698 | 252556 | 0 | 339.0 | 0 | True | False |
| headway_15_step_q25_S3_B | 615060 | 261717 | 2394 | 343.0 | 0 | True | False |
| headway_15_step_q10_S2_fixed | 609283 | 255715 | 0 | 无任务/未完成 | 0 | True | False |
| headway_15_step_q10_S2_A | 609283 | 255715 | 0 | 无任务/未完成 | 0 | True | False |
| headway_15_step_q10_S3_fixed | 609679 | 255850 | 0 | 389.0 | 0 | True | False |
| headway_15_step_q10_S3_A | 609679 | 255850 | 0 | 389.0 | 0 | True | False |
| headway_15_step_q10_S3_B | 615814 | 261290 | 2394 | 373.0 | 0 | True | False |

## 配对效果与诊断

差值为策略减参照；负值是减少，正值是增加。comparison_valid仅表示物理有效且完整；只有同时通过训练一致性筛选的eligible_for_fitted_scenario_conclusion=true可支持拟合条件下的情景结论。未通过训练筛选的需求剖面完整保留，数值仅作模型失配诊断。

| 需求/强度/情景 | 对比 | 普通总时间差(车·秒) | 事件后释放群体时间差 | 任务时间差(秒) | 物理完整 | 可用于拟合情景结论 |
|---|---|---:|---:|---:|---|---|
| headway_60/step_q25/S2 | A−fixed | 0.0 | 0.0 | None | True | True |
| headway_60/step_q25/S3 | A−fixed | 0.0 | 0.0 | 0.0 | True | True |
| headway_60/step_q25/S3 | B−fixed | 697.0 | 697.0 | 0.0 | True | True |
| headway_60/step_q25/S3 | B−A | 697.0 | 697.0 | 0.0 | True | True |
| headway_60/step_q10/S2 | A−fixed | 0.0 | 0.0 | None | True | True |
| headway_60/step_q10/S3 | A−fixed | 0.0 | 0.0 | 0.0 | True | True |
| headway_60/step_q10/S3 | B−fixed | 631.0 | 631.0 | 0.0 | True | True |
| headway_60/step_q10/S3 | B−A | 631.0 | 631.0 | 0.0 | True | True |
| headway_30/step_q25/S2 | A−fixed | 0.0 | 0.0 | None | True | True |
| headway_30/step_q25/S3 | A−fixed | 0.0 | 0.0 | 0.0 | True | True |
| headway_30/step_q25/S3 | B−fixed | 1437.0 | 1452.0 | 30.0 | True | True |
| headway_30/step_q25/S3 | B−A | 1437.0 | 1452.0 | 30.0 | True | True |
| headway_30/step_q10/S2 | A−fixed | 0.0 | 0.0 | None | True | True |
| headway_30/step_q10/S3 | A−fixed | 0.0 | 0.0 | 0.0 | True | True |
| headway_30/step_q10/S3 | B−fixed | 1496.0 | 1519.0 | 25.0 | True | True |
| headway_30/step_q10/S3 | B−A | 1496.0 | 1519.0 | 25.0 | True | True |
| historical_scale_18/step_q25/S2 | A−fixed | 0.0 | 0.0 | None | True | False |
| historical_scale_18/step_q25/S3 | A−fixed | 0.0 | 0.0 | 0.0 | True | False |
| historical_scale_18/step_q25/S3 | B−fixed | 264.0 | -123.0 | 0.0 | True | False |
| historical_scale_18/step_q25/S3 | B−A | 264.0 | -123.0 | 0.0 | True | False |
| historical_scale_18/step_q10/S2 | A−fixed | 0.0 | 0.0 | None | True | False |
| historical_scale_18/step_q10/S3 | A−fixed | 0.0 | 0.0 | 0.0 | True | False |
| historical_scale_18/step_q10/S3 | B−fixed | 1325.0 | 951.0 | 0.0 | True | False |
| historical_scale_18/step_q10/S3 | B−A | 1325.0 | 951.0 | 0.0 | True | False |
| headway_15/step_q25/S2 | A−fixed | 0.0 | 0.0 | None | True | False |
| headway_15/step_q25/S3 | A−fixed | 0.0 | 0.0 | 0.0 | True | False |
| headway_15/step_q25/S3 | B−fixed | 9362.0 | 9161.0 | 4.0 | True | False |
| headway_15/step_q25/S3 | B−A | 9362.0 | 9161.0 | 4.0 | True | False |
| headway_15/step_q10/S2 | A−fixed | 0.0 | 0.0 | None | True | False |
| headway_15/step_q10/S3 | A−fixed | 0.0 | 0.0 | 0.0 | True | False |
| headway_15/step_q10/S3 | B−fixed | 6135.0 | 5440.0 | -16.0 | True | False |
| headway_15/step_q10/S3 | B−A | 6135.0 | 5440.0 | -16.0 | True | False |

## 通过训练筛选的条件结论

本批次有 32 组物理完整的配对比较，其中 16 组同时通过训练一致性筛选。仅在这 16 组内统计：B对A共 4 组，保障任务缩短 0 组，普通交通总耗时增加 4 组。无改进或代价上升的结果与改善结果一并报告；不得将条件模拟解释为现场治理承诺。

## 未通过训练筛选的诊断范围

其余 16 组物理完整比较未通过训练一致性筛选，其收益或代价不能用于已拟合交通状态的治理结论。它们保留用于展示在更高/不同假设需求下的模型失配与敏感性。未完成或物理无效的运行另禁止收益比较。

## 个体代价

以下B−A比较保留每辆普通车原始释放时间；全体总耗时下降不代表每辆车受益。45秒仅限制主动源端等待，不是整个行程损失上限。

| 需求/强度 | 受益 | 受损 | 不变 | 最大增加(秒) | 物理完整 | 可用于拟合情景结论 |
|---|---:|---:|---:|---:|---|---|
| headway_60/step_q25 | 0 | 17 | 283 | 60.0 | True | True |
| headway_60/step_q10 | 0 | 17 | 283 | 49.0 | True | True |
| headway_30/step_q25 | 9 | 55 | 536 | 53.0 | True | True |
| headway_30/step_q10 | 10 | 57 | 533 | 49.0 | True | True |
| historical_scale_18/step_q25 | 78 | 165 | 757 | 54.0 | True | False |
| historical_scale_18/step_q10 | 79 | 165 | 756 | 54.0 | True | False |
| headway_15/step_q25 | 29 | 330 | 840 | 92.0 | True | False |
| headway_15/step_q10 | 79 | 291 | 829 | 59.0 | True | False |

复现：`python3 -B scripts/run_governance_v2.py --run`。已有完整目录逐文件校验后复用；新计算或不同协议请在outputs/governance_v2下显式指定独立子目录，保留现有证据。
