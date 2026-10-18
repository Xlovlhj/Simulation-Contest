# CLTC-P公开序列来源与核验

记录日期：2026年10月17日（北京时间）。本文件说明项目采用的必选场景A输入如何取得；没有收到组委会关于附件3的勘误。

## 主来源

- 仓库：[MOTIVES-LAB/generalized-energy-consumption-evaluation-for-ev](https://github.com/MOTIVES-LAB/generalized-energy-consumption-evaluation-for-ev)。
- 固定提交：`557a4de432c057b4d7d97a2800e4a48d414e132c`。
- 固定提交的提交日期为2024-11-28（UTC），早于本次项目日期。
- 数据路径：`functions/cycle_data/CLTC_P.csv`，下载原件保留于`data/external/cltc_motives.csv`。
- 仓库README关联论文：Yuan, Xinmei, et al. *Data-driven evaluation of electric vehicle energy consumption for generalizing standard testing to real-world driving*. Patterns, 5(4), 2024. DOI：[10.1016/j.patter.2024.100950](https://doi.org/10.1016/j.patter.2024.100950)。论文书目信息来自仓库README；本次未完成论文全文核验。
- 同一固定提交的README、MIT LICENSE已缓存，保留作者与许可信息。仅使用数据，不下载或执行仓库模型程序。

## 交叉核对来源

- 仓库：[LigeZhao/EC-Window-for-Electric-Vehicles](https://github.com/LigeZhao/EC-Window-for-Electric-Vehicles)。
- 固定提交：`1624c1483b66920ccd5f2017a2391f1b0f8ede95`。
- 固定提交的提交日期为2026-06-19（UTC），早于本次项目日期；元数据存于`records/cltc_commit_dates.json`。
- 路径：`cycles/cltc.csv`，原件为`data/external/cltc_ec_window.csv`。
- README将该文件列为CLTC；关联研究题名为 *Climate-dependent thermal regulation by electrochromic glazing extends electric vehicle driving range across global cities*，README尚未给完整出版引用，不将其写成已正式发表论文。
- 固定版本README与MIT LICENSE已缓存。两仓库可能共享更早来源，不宣称它们构成两个独立实验。

## 实际核验

第一份CSV有1800个速度点，时间列为1—1800；第二份有1801点，时间为0—1800秒。将第一份时间整体减1、km/h除以3.6后，其0—1799秒速度与第二份对应点的最大差仅4.44445×10⁻⁹m/s（小数舍入）。第二份1800秒速度为0。

工程采用第一份速度及经第二份确认的末尾1800秒零速点，统一生成`data/processed/cltc_p.csv`，不改下载原件。时长1800秒，最高速度114km/h，梯形积分里程14.47975km。两个固定源文件的SHA256记录于`records/cltc_downloads.json`；每次加载都校验哈希、时间轴、逐点差及基础统计。

附件3的里程约14.466667km，虽然时长/峰值相近，但逐点速度不同（最大差114km/h），所以保留为场景E，不能改名为CLTC。

当前核验级别是**带学术出处、固定版本的公开CLTC-P复现序列，并经另一公开仓库逐点核对**；尚未直接比对官方标准原始表，也没有取得组委会确认。论文应如实写出来源、时间规范化与此限制。

正常仿真无需联网。若下载缓存丢失，可执行`python scripts/fetch_cltc.py`恢复固定版本的数据，仍须通过原SHA256；结果生成入口为`python run.py cltc`。完整来源核验JSON在`results/tables/cltc_verification.json`。
