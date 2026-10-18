"""阶段二可复现入口、结果说明和上下文恢复记录。AI辅助见records。"""
from __future__ import annotations

import importlib.metadata
import platform
from datetime import datetime, timedelta, timezone

import pandas as pd

from .calibration import confidence_analysis, evaluate_calibration, fit_parameters, plot_calibration
from .cycles import load_cltc
from .diagnostics import run_training_profile
from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json
from .scenarios import run_scenario_a
from .workflow import run_checks


def write_manifest(*,stage: str="CLTC_and_calibration_v2_with_explicit_limitations") -> None:
    files = (sorted((PROJECT_ROOT/"src").rglob("*.py")) + sorted((PROJECT_ROOT/"configs").glob("*.json"))
             + sorted((PROJECT_ROOT/"checks").glob("*.py")) + sorted((PROJECT_ROOT/"scripts").glob("*.py"))
             + [PROJECT_ROOT/"run.py",PROJECT_ROOT/"reproduce.py"] + sorted(PROJECT_ROOT.glob("requirements*.txt"))
             + [p for p in sorted((PROJECT_ROOT/"portal").rglob("*")) if p.is_file()]
             + [p for p in sorted((PROJECT_ROOT/"paper").rglob("*")) if p.is_file()])
    records = [p.name for p in sorted((PROJECT_ROOT/"records").glob("*.json")) if p.name!="run_manifest.json"]
    external = sorted(p for p in (PROJECT_ROOT/"data/external").iterdir() if p.is_file())
    artifacts = [PROJECT_ROOT/"results/tables/calibration_linearization.npz"]
    dependencies = {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "scipy", "matplotlib", "openpyxl", "Markdown"]}
    optional_dependencies = {}
    for name in ["imageio-ffmpeg", "playwright", "PyMuPDF", "Pillow", "python-docx"]:
        try:
            optional_dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    documentation = [PROJECT_ROOT/"README.md", PROJECT_ROOT/"C题开发日志.md", PROJECT_ROOT/"AGENTS.md"]
    documentation += sorted((PROJECT_ROOT/"docs").rglob("*.md")) + sorted((PROJECT_ROOT/"records").glob("*.md")) + sorted((PROJECT_ROOT/"results").glob("*.md"))
    manifest = {"date_beijing": datetime.now(timezone(timedelta(hours=8))).date().isoformat(), "date_source": "runtime_UTC_plus_08",
                "stage": stage, "python": platform.python_version(),
                "dependencies": dependencies, "optional_dependencies": optional_dependencies,
                "code_and_config": {p.relative_to(PROJECT_ROOT).as_posix(): sha256(p) for p in files},
                "documentation": {p.relative_to(PROJECT_ROOT).as_posix(): sha256(p) for p in documentation},
                "external_files": {p.relative_to(PROJECT_ROOT).as_posix(): sha256(p) for p in external},
                "experiment_records": {f"records/{name}": sha256(PROJECT_ROOT/"records"/name) for name in records},
                "fit_artifacts": {p.relative_to(PROJECT_ROOT).as_posix(): sha256(p) for p in artifacts}}
    write_json(PROJECT_ROOT/"records/run_manifest.json", manifest)
    (PROJECT_ROOT/"records/requirements-lock.txt").write_text(
        "\n".join(f"{name}=={version}" for name, version in manifest["dependencies"].items())+"\n", encoding="utf-8")


def write_stage_two_report(scenario: dict, evaluation: dict, uncertainty: dict, checks: dict, profile: dict) -> dict:
    out = scenario["summary"]
    groups = evaluation["group_metrics"]
    roles = {"train": "训练575/596", "validation": "验证588", "test_retrospective": "回顾检验600"}
    variants = {"reference": "原参考", "nuisance_only": "仅修正容量/电压", "joint_calibrated": "联合校准v2"}
    metric_rows = "\n".join(f"| {roles[r['role']]} | {variants[r['variant']]} | {r['soc_rmse_macro_pp']:.3f} | {r['energy_mape_pct']:.3f}% |"
                            for r in groups)
    parameter_rows = "\n".join(f"| {r['parameter']} | {r['estimate']:.6g} | [{r['ci_low']:.6g}, {r['ci_high']:.6g}] |"
                               for r in uncertainty["primary_intervals"])
    voltage = pd.DataFrame(evaluation["trip_metrics"])
    voltage_rows = "\n".join(f"| {tid} | {voltage[(voltage.trip_id==tid)&(voltage.variant=='reference')].iloc[0].voltage_rmse_v:.3f} | "
                              f"{voltage[(voltage.trip_id==tid)&(voltage.variant=='joint_calibrated')].iloc[0].voltage_rmse_v:.3f} |"
                              for tid in [575, 596, 588, 600])
    corr = uncertainty["correlations_by_block_length"]["40.0"][0][1]
    profile_rows = "\n".join(f"| {r['fixed_Cr']:.6g} | {r['drag_coefficient']:.6g} | {r['training_cost']:.6f} |" for r in profile["rows"])
    report = f"""# C题阶段二实际进展

日期：2026年10月17日（北京时间，会话日期）。复现命令：`python run.py all`；单独重现本阶段：`python run.py stage2`。本报告为中间开发结果，尚非最终论文。

## 已完成与当前判断

完成公开CLTC-P的来源归档、两来源逐点核对、25℃必选基准仿真、七参数双扰动幅度灵敏度、三参数及两个共享干扰参数辨识、条件性区间和分路程前向评估。**能耗/SOC已有改善，但电压跨SOC迁移和滚阻可辨识性未通过充分验证，校准模型仍为暂定。**

## 25℃ CLTC基准

采用参考160Ah及原参考参数，初始SOC80%，电池/座舱25℃，其余规则见`configs/reference.json`。

| 指标 | 实际值 |
|---|---:|
| 距离 | {out['distance_km']:.6f} km |
| 电池端净耗电 | {out['net_energy_kwh']:.6f} kWh |
| 百公里电耗 | {out['consumption_kwh_100km']:.6f} kWh/100km |
| 终点SOC | {out['soc_final']*100:.6f}% |
| 最高电池温度 | {out['battery_max_c']:.6f}℃ |
| DC收支残差 | {out['dc_balance_residual_kwh']:.3e} kWh |
| 步长0.5→0.25s能耗相对差 | {scenario['convergence']['energy_relative_difference']*100:.6f}% |

CLTC来自公开学术仓库，固定提交版本与SHA256，另一仓库的重合速度点最大差约4.45×10⁻⁹m/s；第一来源点编号1—1800换为0—1799s，末尾1800s零速经第二来源核对。**这是公开复现序列的交叉核验，未直接比对官方标准原文件，也不是组委会勘误。** 附件3与该序列不同，继续作为场景E。

七参数在±5%和±10%下能耗排序一致：质量、滚阻、空阻、低压附件、COP；内阻与热容在本工况的端能耗响应为零。电池冷却没有开启，端口负载不因内阻变化而改变；内阻仍影响损耗、SOC和温度。内部损耗与DC支路分别列示，不能重复相加；占比分母为净端电能，回收支路为负，牵引占比可能超过100%。

80Ah的能量等效口径和暂定校准参数下的CLTC结果另存为对照，不据此判定附件中的容量/能量哪项错误。25℃超出实测环境范围，校准模型的该场景属于迁移仿真。

## 辨识方法与评价口径

575/596用于训练；588用于验证。统一前向模型从首个SOC和假定等于环境的初始温度开始，只输入车速、环境及已知的实测空调/加热负载，**不逐时读取真实SOC、电流、电压来制造预测**。因此这是“已知辅助负载”的条件预测，不声称仅凭车速/环境即可达到同一精度。

原始非等间隔观测保留；输入以1s重采样并做sigma=1s的高斯车速平滑，各路程里程变化绝对值均不足0.05%。拟合步长1s，评价步长0.5s；长缺口仍按插值处理，缺口内无真实观测。SOC拟合残差按时间支撑加权；表中RMSE为各路程样本RMSE的宏平均，单位为百分点。能耗MAPE为各路程净端电能APE的平均，单路程时就是该路程APE。

参考OCV/容量与训练记录不相容，因此增加共享有效容量Q和OCV常偏移作为干扰参数，并保留“只校准这两项”的对照。Q是当前模型条件下的有效参数，不是电池额定容量。

v1只拟合SOC、电压与10s平均功率，训练能耗MAPE反而从16.39%升至22.60%；失败实验已归档至`records/experiments/calibration_v1/`。v2基于**这一训练失败**增加5%相对尺度的总电能残差，保持原预处理、参数边界与路程划分。每个目标按通道/路程归一，采用有界最小二乘与两个初值，仅按训练目标选解。未据600结果选v2设置。

**600已在v1被评估，因此v2结果称回顾性检验，不能继续宣称“从未见过的最终盲测”。** 原v1首次冻结/测试记录和v2重新冻结/回顾评估均保留。现有四路程已无完全未查看的实测测试路程；后续可增加数据，或在固定方法下做明确标注的整路程交叉验证，不能重命名数据来恢复盲测资格。

| 数据 | 模型 | SOC RMSE/百分点（宏平均） | 能耗MAPE |
|---|---|---:|---:|
{metric_rows}

与只修正容量/电压相比，联合辨识明显改善能耗，但并未在所有路程上进一步改善SOC；不能把全部SOC提升都归因于Cd/Cr/Rref。

## 参数区间与可辨识性

| 参数 | v2估计 | 95%局部线性块wild近似区间 |
|---|---:|---|
{parameter_rows}

1000次Rademacher乘子抽样，主块长40s，另比较20/80s；同一路程同一时间块的SOC、电压、功率和总能量贡献共用乘子。使用训练残差与五参数雅可比局部线性化，没有做1000次非线性重新拟合，没有把搜索范围当置信区间，也没有把越界抽样裁回范围。只有两个训练路程，不保证跨车辆、跨天气的总体覆盖率。

Cd与Cr的块抽样相关系数约{corr:.3f}；Cr接近0.003搜索下界，近似区间穿过0且约半数线性抽样越过搜索下界，**不能把该Cr区间解读为物理可信区间，也不能把最优Cr视为已可靠确定**。Cd与Rref提供条件性区间，但仍受固定OCV/温度模型及再生阈值非光滑性影响。

另外固定7个Cr取值、仅用575/596重新优化其他四参数，得到训练剖面（`python run.py profile`可完整重算）。Cr增大时Cd补偿下降，显示耦合；最小成本仍在现冻结值附近，没有改写冻结参数。该复合目标的残差相关、含人为通道尺度，不能套用未经论证的卡方阈值将剖面直接换成95%区间。

| 固定Cr | 重新优化的Cd | 训练目标cost |
|---|---:|---:|
{profile_rows}

## 不应隐藏的电压迁移问题

| 路程 | 原参考电压RMSE/V | 联合校准v2电压RMSE/V |
|---|---:|---:|
{voltage_rows}

训练路程实测SOC位于87.68%—100%，588为72.32%—82.80%，600为48.29%—56.71%；参考OCV曲线加一个常偏移不足以跨区间迁移。验证/回顾路程的电压误差上升，说明“能耗更准”不能推导为“整套电池模型都更准”。没有实测温度、初始温度未知、坡度/风速未知也会影响辨识。温度输出只能声称机理仿真与物理核查通过，不能声称实测验证。

## 已运行检查与下一步

{checks['tests_run']}项检查全部通过，包括100%SOC起步、OCV修正的功率一致性、未来观测防泄漏、已知三参数合成恢复、CLTC来源核对及聚合能量残差拆分。CLTC和四路程的步长减半检查已输出；合成恢复只验证估计程序可在理想条件恢复已知值，不证明实测参数真实。

`python run.py refit`已实际完整重算训练辨识，五个参数与冻结结果差异均为0，输出在独立目录，原冻结文件不变。下一阶段优先诊断OCV曲线迁移及Cr边界，做训练内的非线性重采样/通道权重稳定性和速度激励分析；在参数不确定性被明确保留的情况下建立公平温度/舒适性约束、规则基准和优化规则/MPC。随后做高低温迁移、模型不确定性鲁棒性、MP4编码链路和动态展示。尚无节能优化结论、正式论文或提交视频。

## 图表入口

- [CLTC综合轨迹](figures/scenario_A_overview.png)
- [CLTC母线能量分解](figures/scenario_A_energy.png)
- [七参数双幅度灵敏度](figures/scenario_A_sensitivity.png)
- [四路程SOC/电压预测](figures/calibration_predictions.png)
- [条件性参数区间](figures/parameter_intervals.png)
- [训练内Cr剖面](figures/Cr_training_profile.png)
- [完整指标CSV](tables/calibration_metrics.csv)
- [CLTC来源核验JSON](tables/cltc_verification.json)
"""
    (PROJECT_ROOT/"results/阶段二进展.md").write_text(report, encoding="utf-8")
    previous_path = PROJECT_ROOT/"records/latest_state.json"
    previous = read_json(previous_path) if previous_path.exists() else {}
    state = {**previous, "date_beijing": "2026-10-17", "status": "stage2_reproduced_calibration_provisional",
             "completed": ["C-specific source archive and immutable hash audit", "four-trip preprocessing",
                           "shared electromechanical and two-node thermal model", "reference E and public CLTC-P scenario A",
                           "seven-parameter sensitivity at two amplitudes", "three-parameter plus shared nuisance calibration v2",
                           "conditional linearized block-wild uncertainty and nonlinear Cr training profile", "frozen validation and retrospective trip600 evaluation",
                           f"{checks['tests_run']} physical/identification/control checks and step convergence"],
             "not_completed": ["OCV cross-SOC transfer adequacy and nonlinear uncertainty near Cr boundary",
                               "fully untouched new measurement test", "constrained control optimization and robustness",
                               "final paper and submission video"],
             "scenario_A_reference_consumption_kwh_100km": out["consumption_kwh_100km"],
             "calibration_version": "v2_provisional", "test600_is_untouched": False,
             "verification": checks, "calibration_metrics": groups,
             "next": ["Diagnose training OCV fit range and near-bound Cr; retain uncertainty and voltage failure",
                      "Implement fair comfort/safety constraints and control comparison using one shared plant",
                      "Robust temperature transfer and MP4 demonstration"],
             "important": ["Public CLTC-P cross-repository verification is not direct official-file certification",
                           "Trip600 v2 is retrospective after v1 was viewed", "Cr near bound and voltage transfer inadequate",
                           "No measured battery/cabin temperature", "30kWh vs160Ah capacity conflict unresolved"],
             "report": "results/阶段二进展.md"}
    write_json(previous_path, state)
    write_manifest()
    return state


def run_stage_two(*, checks: dict | None = None) -> dict:
    verify_sources()
    # 先核验程序与来源，再对冻结参数执行确定性评估；正常复现不联网。
    checks = checks if checks is not None else run_checks()
    scenario = run_scenario_a()
    fit_parameters()
    uncertainty = confidence_analysis()
    profile = run_training_profile(recompute=False)
    evaluation = evaluate_calibration()
    plot_calibration()
    verify_sources()
    load_cltc()
    return write_stage_two_report(scenario, evaluation, uncertainty, checks, profile)
