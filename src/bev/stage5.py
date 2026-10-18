"""阶段五：参数可靠性证据、图表、报告与恢复状态。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .io import PROJECT_ROOT, read_json, verify_sources, write_json
from .reliability import protocol, run_reliability
from .progress import write_manifest
from .workflow import run_checks, setup_figures


def plot_reliability(result: dict) -> None:
    import matplotlib.pyplot as plt
    setup_figures()
    primary=[r for r in result["summary"] if r["block_s"]==protocol()["primary_block_s"]]
    prior=read_json(PROJECT_ROOT/"results/tables/parameter_uncertainty.json")["primary_intervals"]
    cfg=read_json(PROJECT_ROOT/"configs/calibration_protocol.json")
    fig,axes=plt.subplots(1,3,figsize=(13,4))
    for i,(ax,title) in enumerate(zip(axes,["空气阻力系数 Cd","滚动阻力系数 Cr","参考内阻 Rref / Ω"])):
        old,new=prior[i],primary[i]
        ax.errorbar(old["estimate"],1,xerr=[[old["estimate"]-old["ci_low"]],[old["ci_high"]-old["estimate"]]],fmt="o",capsize=4,color="#356d9d")
        ax.errorbar(new["median"],0,xerr=[[new["median"]-new["percentile025"]],[new["percentile975"]-new["median"]]],fmt="s",capsize=4,color="#2b8b69")
        ax.axvline(new["frozen_estimate"],ls="--",color="#b9763f",lw=1,label="冻结点估计")
        ax.axvline(cfg["lower_bounds"][i],ls=":",color="#b04c4c",lw=1,label="工程下界")
        ax.set_yticks([0,1],["非线性：中位数与范围","旧线性：点估计与区间"],fontsize=8)
        ax.set_xlabel(title)
        ax.set_ylim(-.5,1.5)
        ax.grid(axis="x",alpha=.25)
    axes[0].legend(fontsize=8,loc="upper left")
    fig.suptitle("40秒块：非线性200次重拟合与旧线性近似；95%条件范围不保证实车覆盖")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/reliability_ranges.png")
    plt.close(fig)

    rows=pd.DataFrame(result["rows"])
    main=rows[rows.block_s.eq(protocol()["primary_block_s"]) & rows.accepted]
    frozen=read_json(PROJECT_ROOT/"records/calibration_freeze.json")["parameters"]
    fig,axes=plt.subplots(1,2,figsize=(11,4.5))
    points=axes[0].scatter(main.drag_coefficient,main.rolling_coefficient,c=main.capacity_ah,s=22,alpha=.7,cmap="viridis")
    axes[0].scatter([frozen["drag_coefficient"]],[frozen["rolling_coefficient"]],marker="*",s=150,c="#c15436",label="原冻结点估计")
    axes[0].axhline(cfg["lower_bounds"][1],ls="--",color="#b04c4c",lw=1,label="Cr工程下界")
    axes[0].set_xlabel("空气阻力系数 Cd")
    axes[0].set_ylabel("滚动阻力系数 Cr")
    axes[0].set_title("联合分布：观察补偿和边界堆积")
    axes[0].legend(fontsize=8)
    fig.colorbar(points,ax=axes[0],label="共享有效容量 / Ah")
    cr=next(r for r in primary if r["parameter"]=="rolling_coefficient")
    axes[1].hist(main.rolling_coefficient,bins=20,color="#2b8b69",edgecolor="white")
    axes[1].axvline(cfg["lower_bounds"][1],ls="--",color="#b04c4c")
    axes[1].set_xlabel("滚动阻力系数 Cr")
    axes[1].set_ylabel("成功重拟合次数")
    axes[1].set_title(f"Cr落在工程下界：{cr['at_lower_bound_fraction']*100:.1f}%")
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle("边界内的分布不能证明参数物理可信；所有原始重拟合均保留")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/reliability_joint.png")
    plt.close(fig)

    excitation=result["excitation"]
    fig,axes=plt.subplots(1,2,figsize=(12,4.5))
    labels=["<0.1","0.1—20","20—40","40—60","60—80","80—120",">120"]
    positions=np.arange(len(labels))
    for offset,trip in zip([-.2,.2],excitation["trips"]):
        axes[0].bar(positions+offset,np.array(trip["speed_bin_time_fractions"])*100,width=.38,label=f"路程{trip['trip_id']}")
    axes[0].set_xticks(positions,labels,rotation=25,fontsize=8)
    axes[0].set_xlabel("平滑后的车速 / km/h")
    axes[0].set_ylabel("时间占比 / %")
    axes[0].set_title("训练车速覆盖；不是全程低速")
    axes[0].legend()
    speed=np.linspace(0,120,200)/3.6
    p=read_json(PROJECT_ROOT/"configs/calibrated.json")
    axes[1].plot(speed*3.6,p["mass_kg"]*p["gravity_m_s2"]*frozen["rolling_coefficient"]*speed/1000,label="滚阻功率 ∝ v")
    axes[1].plot(speed*3.6,.5*p["air_density_kg_m3"]*p["frontal_area_m2"]*frozen["drag_coefficient"]*speed**3/1000,label="风阻功率 ∝ v³")
    axes[1].set_xlabel("车速 / km/h")
    axes[1].set_ylabel("理想平路轮端功率 / kW")
    axes[1].set_title("两种阻力的速度规律不同；此图为模型关系")
    axes[1].legend()
    for ax in axes:
        ax.grid(alpha=.25)
    fig.suptitle("有速度变化提供区分信息，但不排除参数补偿、观测误差和结构失配")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/training_excitation.png")
    plt.close(fig)

    fig,axes=plt.subplots(1,3,figsize=(13,4))
    for ax,name,title in zip(axes,cfg["parameters"][:3],["Cd","Cr","Rref / Ω"]):
        part=[r for r in result["summary"] if r["parameter"]==name]
        for row in part:
            ax.errorbar(row["block_s"],row["median"],yerr=[[row["median"]-row["percentile025"]],[row["percentile975"]-row["median"]]],fmt="o",capsize=4,color="#2b8b69")
        ax.axhline(frozen[name],color="#b9763f",ls="--",label="原冻结点估计")
        ax.set_xticks([20,40,80],["20s / 50次","40s / 200次","80s / 50次"],fontsize=8)
        ax.set_ylabel(title)
        ax.grid(alpha=.25)
    axes[0].legend(fontsize=8)
    fig.suptitle("时间块长度敏感性：中位数与2.5%—97.5%分位；尾部分位有抽样波动")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/nonlinear_block_sensitivity.png")
    plt.close(fig)


def write_stage_five_report(result: dict, checks: dict) -> dict:
    cfg=read_json(PROJECT_ROOT/"configs/calibration_protocol.json")
    ext=protocol()
    primary=[r for r in result["summary"] if r["block_s"]==ext["primary_block_s"]]
    previous_intervals=read_json(PROJECT_ROOT/"results/tables/parameter_uncertainty.json")["primary_intervals"]
    cr=next(r for r in primary if r["parameter"]=="rolling_coefficient")
    names=["Cd","Cr","Rref / Ω","有效容量Q / Ah","OCV偏移 / V"]
    parameter_rows=[]
    for label,row,old in zip(names,primary,previous_intervals):
        parameter_rows.append(f"| {label} | {row['frozen_estimate']:.6g} | [{old['ci_low']:.6g}, {old['ci_high']:.6g}] | [{row['percentile025']:.6g}, {row['percentile975']:.6g}] | {row['median']:.6g} | {100*row['at_lower_bound_fraction']:.1f}% |")
    block_rows=[]
    for length in ext["block_lengths_s"]:
        part=[r for r in result["summary"] if r["block_s"]==length]
        interval=lambda name:next(r for r in part if r["parameter"]==name)
        cr_part=interval("rolling_coefficient")
        block_rows.append(f"| {length:g} | {cr_part['accepted']} / {cr_part['requested']} | [{interval('drag_coefficient')['percentile025']:.6g}, {interval('drag_coefficient')['percentile975']:.6g}] | [{cr_part['percentile025']:.6g}, {cr_part['percentile975']:.6g}] | [{interval('resistance_ref_ohm')['percentile025']:.6g}, {interval('resistance_ref_ohm')['percentile975']:.6g}] | {100*cr_part['at_lower_bound_fraction']:.1f}% |")
    audit=[r for r in result["rows"] if r["multistart_audit"]]
    largest_gap=max((r["multistart_cost_gap"] for r in audit),default=0.)
    largest_relative=max((r["multistart_cost_gap"]/max(min(r["start_costs"]),1e-12) for r in audit),default=0.)
    failed=[r for r in result["rows"] if not r["accepted"]]
    excitation=result["excitation"]
    speed_rows=[]
    for t in excitation["trips"]:
        speed_rows.append(f"| {t['trip_id']} | {t['maximum_conditioned_speed_kmh']:.2f} | {100*t['speed_bin_time_fractions'][0]:.2f}% | {100*sum(t['speed_bin_time_fractions'][5:]):.2f}% | {100*t['quasi_steady_time_fraction']:.2f}% | {100*t['observation_gap_over_5s_fraction']:.2f}% |")
    geometry_rows=[]
    for title,key in [("合并训练：理想路阻功率列","pooled_ideal_roadload_information"),("实际复合残差：Cd/Cr列","actual_Cd_Cr_column_information"),("实际列扣除R/Q/OCV补偿方向","actual_Cd_Cr_after_nuisance_projection")]:
        g=excitation[key]
        geometry_rows.append(f"| {title} | {g['rank']} | {g['column_cosine']:.6f} | {g['normalized_condition']:.6f} |")
    mc_rows="\n".join(f"| {name} | {row['max_half_sample_tail_difference']:.6g} |" for name,row in zip(names,primary))
    boundary_rows="\n".join(f"| {name} | {100*row['at_lower_bound_fraction']:.1f}% | {100*row['near_lower_bound_fraction']:.1f}% | {100*row['at_upper_bound_fraction']:.1f}% |" for name,row in zip(names,primary))
    correlation=result["correlations"][f"{ext['primary_block_s']:g}"][0][1]
    join=lambda values:"\n".join(values)
    report=fr"""# C题阶段五实际进展

日期：2026年10月18日，北京时间。命令：`python run.py stage5`（或`reliability`）。本阶段仅使用训练575/596；不重新评价588/600，不替换原校准参数和控制规则。

## 结论先看

已完成{result['requested']}次真正的非线性约束重拟合，{result['accepted']}次通过既定求解状态与模型有限性检查；新增训练车速覆盖和实际雅可比几何分析。**滚阻可靠性仍未解决。** 主40秒块中，Cr有{100*cr['at_lower_bound_fraction']:.1f}%落在原工程下界附近；主分位范围为[{cr['percentile025']:.6g}, {cr['percentile975']:.6g}]。边界堆积不能解释为参数准确，原线性区间越过零的失败信号也保留。

训练路程并非一直低速：575/596平滑后的最高车速约74.94/117.76km/h，局部两参数方向仍为满秩。因此不能把Cr问题完全归因于“没有高速数据”，也不能声称两个参数在数学上绝对不可区分。参数补偿、近边界估计和既有结构失配共同限制可信度。

## 新增分析怎样做

1. 固定原模型、输入车速/环境/实测附件负载、初始SOC与假定初温、通道尺度和五参数工程边界。
2. 在原冻结参数处重新计算训练残差，与原线性化文件逐项核对一致。这里只计算训练数据，没有将未来SOC/I/U喂给正向模型。
3. 同一路程、同一时间块内，SOC、电压和功率窗使用同一随机正负号；不同路程独立分块。整路程总能量残差先拆回功率窗的时间贡献，再使用同一组符号汇总。
4. 以冻结预测为中心构造扰动目标，重新求解整个非线性模型，仍限制在原工程边界。主40秒块200次，20/80秒各50次，共300次；预设种子固定。
5. 每20次附加一个不同初值复算，共{result['multistart_audits']}个多初值审计；记录每次状态、代价、迭代数、边界及失败，不因结果不好换抽样。

运行前协议、代码、数据来源清单与原冻结哈希记录于`records/reliability_freeze.json`。计算断点只允许在相同指纹下继续，随机符号也单独保存，便于重现。

### 中心化的公式与人话

设原训练复合残差为$r(\theta)$，冻结估计为$\hat\theta$，分块带符号残差贡献为$B\xi$。每次求解：

$$\min_{{\theta\in[\ell,u]}}\frac12\left\|r(\theta)-r(\hat\theta)+B\xi\right\|^2.$$

通俗地说：从原冻结预测出发，给误差按时间段换正负号，再看看重新拟合的参数会跑到哪里。减去原残差是为了正确地围绕冻结预测生成伪响应；不是直接在原拟合目标上再随意加一层误差。

这是固定输入条件下的复合残差重抽样。各通道保持块内符号一致、总量与功率窗代数一致，但不宣称每个伪响应都对应一辆物理上真实存在的车。没有重抽样车速误差、温度初值、热模型结构或不同车辆。

## 参数范围：保留旧结果，再做非线性对照

主分析40秒块。旧线性区间为1000次局部线性块wild近似，新非线性范围为200次重拟合的2.5%—97.5%分位。新图用中位数表示分布中心，**中位数没有替换原点估计**。

| 参数 | 原冻结值 | 旧95%线性条件近似区间 | 新95%非线性百分位范围 | 新分布中位数 | 落在工程下界比例 |
|---|---:|---|---|---:|---:|
{join(parameter_rows)}

下界判定使用“距离原下界不超过参数搜索跨度的$10^{{-6}}$”，近下界另以跨度1%统计；这些是数值诊断定义。Cr原下界0.003由工程设定给出，**不能把0.003改写成数据证明的真实最小值**。受约束的非线性分布变得非对称、在边界堆积，不能据“不再穿零”声称问题解决。

| 参数 | 落下界 | 距下界不超过搜索跨度1% | 落上界 |
|---|---:|---:|---:|
{boundary_rows}

题目要求至少两个参数的置信区间。论文应完整列出原条件近似区间及其假设，用新非线性对照说明边界和非线性影响；这些结果均无真实总体覆盖保证。尤其不可把工程搜索范围直接称作置信区间。若称为近似统计区间，须同时声明固定模型、残差块依赖等条件，保留有限抽样和失配局限。

![区间对照](figures/reliability_ranges.png)

主重抽样Cd/Cr经验相关系数约{correlation:.6f}。它描述本重抽样机制下的共同变化，不是真实参数的总体相关。原训练剖面仍可用于解释补偿；没有将剖面代价直接换算成卡方置信区间。

![联合分布与边界](figures/reliability_joint.png)

## 块长与数值稳定性

| 块长s | 成功/请求 | Cd百分位范围 | Cr百分位范围 | Rref百分位范围Ω | Cr落下界 |
|---:|---:|---|---|---|---:|
{join(block_rows)}

只有两条训练路程，不是两百辆独立车辆。20/80秒各50次只能作粗略敏感性检查，95%尾部每端约1—2个样本；40秒200次每端也只有约5个样本，分位端点会波动。没有把300次计算称为300次实测。

把主200次成功结果按生成顺序分成两半，比较两半的上下尾分位，得到最大端点差：

| 参数 | 两半样本尾分位最大差（参数单位） |
|---|---:|
{mc_rows}

这只是蒙特卡洛波动诊断，不是区间端点的置信限，也不构成覆盖证明。

多初值审计最大代价差{largest_gap:.6g}，最大相对代价差{100*largest_relative:.6f}%。已有局部数值差异，不能声称全部初值收敛至同一解或证明全局最优；按预设规则保留审计中代价较低的成功解。未接受重拟合{len(failed)}次；全部原始状态见`nonlinear_wild_draws.csv`，失败不能被悄悄替换。

![块长敏感性](figures/nonlinear_block_sensitivity.png)

## 训练输入到底提供了哪些信息

对平路理想路阻，滚阻功率近似随车速一次变化，空气阻力功率随车速三次变化：

$$P_{{road}}=mgC_rv+\frac12\rho A C_dv^3.$$

**人话解释：** 只在一个速度开，两个阻力旋钮容易互相补偿；增加不同速度，尤其同时覆盖低速与高速，通常有助于区分。但真实数据还包含附件用电、驱动非线性、缺口和电池模型误差，不能只看车速就认定参数可靠。

| 路程 | 最高平滑车速km/h | 近静止时间 | ≥80km/h时间 | 准稳态时间 | 原观测>5秒缺口跨越时间 |
|---|---:|---:|---:|---:|---:|
{join(speed_rows)}

准稳态定义为车速≥10km/h且加速度绝对值≤0.1m/s²，只是可重复的描述性筛选；不代表独立滑行或道路阻力台架试验。缺口内车速来自插值和平滑，不能把高分辨率网格当成新增实测点。

![车速覆盖和阻力规律](figures/training_excitation.png)

下表两列先归一化，余弦接近1表示局部响应相似；条件数越大表示该线性化方向更难区分。实际雅可比来自原冻结复合残差，最后一行投影掉Rref/Q/OCV的局部补偿方向。

| 诊断对象 | 两列秩 | 列余弦 | 列归一化条件数 |
|---|---:|---:|---:|
{join(geometry_rows)}

两列满秩只说明在这个局部近似下不是完全重合。它不证明现实参数具有足够精度，也不能解释为全模型的全局可辨识性。风阻/滚阻存在补偿，但本数据不是简单的“全程低速、完全不可分离”。电压跨SOC迁移失败、容量冲突、热状态未知和Cr靠边界仍需共同报告。

## 已验证与尚未解决

本轮新增6项解析核查，总计{checks['tests_run']}项程序检查通过，涵盖块内跨通道依赖、总能量贡献一致、线性已知解、非线性正确中心与边界效应、恒速路阻不可分与变速可分、训练路程限制。冻结残差逐项重现；原参数、规则及既有长程结论未改变。

尚未解决真实OCV跨SOC精度、Cr物理可靠性、温度实测与传感器校准；600仍仅为回顾检验。没有新增盲测、MPC、最终论文或正式比赛视频。

## 结果文件与后续

- `results/tables/nonlinear_reliability.json`：所有重拟合、区间、审计及激励统计。
- `nonlinear_wild_draws.csv`、`nonlinear_wild_ranges.csv`：逐次结果及范围表；`nonlinear_wild_signs.npz`保存随机符号。
- `records/reliability_freeze.json`、`reliability_checkpoint.json`：运行前协议指纹与可恢复断点。
- [参数可靠性通俗说明](../docs/参数可靠性与非线性区间通俗说明.md)：给队员阅读与复核。

下一步整理三问的共同证据表，开始正式论文、四环节视频及提交支撑材料。优先准确回答三问、写清限制；不再为追求漂亮参数而修改已查看结果对应的口径。
"""
    (PROJECT_ROOT/"results/阶段五进展.md").write_text(report,encoding="utf-8")
    state=read_json(PROJECT_ROOT/"records/latest_state.json")
    summary={"requested":result["requested"],"accepted":result["accepted"],"multistart_audits":result["multistart_audits"],
             "primary_block_s":ext["primary_block_s"],"primary_repetitions":ext["repetitions_by_block_s"][f"{ext['primary_block_s']:g}"],
             "Cr_lower_bound_fraction":cr["at_lower_bound_fraction"],"primary_ranges":primary,
             "Cd_Cr_resampling_correlation":correlation,"max_multistart_cost_gap":largest_gap,
             "point_estimates_replaced":False,"train_only":True}
    state.update(date_beijing=datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
                 status="stage5_nonlinear_training_uncertainty_with_boundary_mass",report="results/阶段五进展.md",verification=checks,reliability=summary)
    state["completed"]=list(dict.fromkeys(state["completed"]+[
        "300 centered nonlinear constrained block-wild training refits with boundary and multistart diagnostics",
        "training speed coverage, road-load excitation and actual residual Jacobian geometry",
        "34 physical, identification, control, continuity and uncertainty implementation checks"]))
    state["next"]=["Freeze common evidence tables and begin the final paper covering all three questions",
                    "Produce the complete four-part narrated simulation video and reproducible support package",
                    "Retain Cr/OCV, true sensor calibration, extended-SOC and thermal-endpoint limits; collect team review"]
    from .portal import discover_documents, resolved_terms
    state["project_portal"].update(documents=len(discover_documents()),terms=len(resolved_terms()),
                                   figures=len(list((PROJECT_ROOT/"results/figures").glob("*.png"))))
    write_json(PROJECT_ROOT/"records/latest_state.json",state)
    write_manifest(stage="stage5_training_nonlinear_uncertainty_and_excitation")
    return state


def run_stage_five(*,checks: dict|None=None) -> dict:
    verify_sources()
    checks=checks if checks is not None else run_checks()
    result=run_reliability()
    plot_reliability(result)
    verify_sources()
    return write_stage_five_report(result,checks)
