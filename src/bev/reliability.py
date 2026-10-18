"""仅训练数据的中心化非线性块wild与激励诊断；不替换原冻结。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.sparse import csr_matrix

from .calibration import CalibrationProblem, protocol as calibration_protocol
from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json


def protocol() -> dict:
    return read_json(PROJECT_ROOT/"configs/reliability_protocol.json")


def require_training_only(cfg: dict, extension: dict) -> None:
    if cfg["fit_trip_ids"]!=extension["fit_trip_ids"] or set(extension["fit_trip_ids"])!={575,596}:
        raise ValueError("可靠性实验只允许预先冻结的训练575/596")
    if set(extension["fit_trip_ids"]) & set(cfg["validation_trip_ids"]+cfg["test_trip_ids"]):
        raise ValueError("训练与评价路程交叉")


def block_residual_matrix(residual: np.ndarray, parts: list[dict], length_s: float) -> tuple[csr_matrix, list[tuple]]:
    """每行残差对时间块的贡献；总电能行分散到对应功率窗。"""
    if length_s<=0 or sum(len(p["time_s"]) for p in parts)!=len(residual):
        raise ValueError("时间块或残差元数据无效")
    keys=sorted({(p["trip_id"],int(t//length_s)) for p in parts for t in p.get("contribution_time_s",p["time_s"])})
    index={key:i for i,key in enumerate(keys)}
    rows,columns,values=[],[],[]
    cursor=0
    for part in parts:
        if part["channel"]=="net_energy":
            if len(part["time_s"])!=1 or len(part["contribution_time_s"])!=len(part["contribution_residual"]):
                raise ValueError("总能量残差贡献维度不一致")
            if not np.isclose(sum(part["contribution_residual"]),residual[cursor],atol=1e-10,rtol=1e-10):
                raise ValueError("总能量残差与功率窗贡献不一致")
            for t,value in zip(part["contribution_time_s"],part["contribution_residual"]):
                rows.append(cursor)
                columns.append(index[(part["trip_id"],int(t//length_s))])
                values.append(value)
        else:
            for offset,t in enumerate(part["time_s"]):
                rows.append(cursor+offset)
                columns.append(index[(part["trip_id"],int(t//length_s))])
                values.append(residual[cursor+offset])
        cursor+=len(part["time_s"])
    matrix=csr_matrix((values,(rows,columns)),shape=(len(residual),len(keys)))
    np.testing.assert_allclose(matrix@np.ones(len(keys)),residual,atol=1e-10,rtol=1e-10)
    return matrix,keys


def centered_residual(current: np.ndarray, center: np.ndarray, perturbation: np.ndarray) -> np.ndarray:
    # 以预测中心生成伪响应；不能直接用r(theta)+扰动，后者没有正确中心化。
    return current-center+perturbation


def column_information(design: np.ndarray) -> dict:
    norms=np.linalg.norm(design,axis=0)
    if np.any(norms<=0):
        return {"rank":int(np.linalg.matrix_rank(design)),"column_cosine":None,"normalized_condition":None}
    normalized=design/norms
    singular=np.linalg.svd(normalized,compute_uv=False)
    rank=int(np.linalg.matrix_rank(normalized))
    return {"rank":rank,"column_cosine":float(normalized[:,0]@normalized[:,1]),
            "normalized_condition":float(singular[0]/singular[-1]) if rank==design.shape[1] else None,
            "column_norms":norms.tolist(),"singular_values":singular.tolist()}


def roadload_design(speed_m_s: np.ndarray, dt_s: np.ndarray, p: dict) -> np.ndarray:
    return np.sqrt(dt_s)[:,None]*np.column_stack([
        p["mass_kg"]*p["gravity_m_s2"]*speed_m_s,
        .5*p["air_density_kg_m3"]*p["frontal_area_m2"]*speed_m_s**3])


def excitation_diagnostics(problem: CalibrationProblem, frozen_jac: np.ndarray, ext: dict) -> dict:
    rows=[]
    designs=[]
    for trip in problem.trips:
        cycle=trip.cycle
        dt=np.diff(cycle.time_s)
        v=(cycle.speed_m_s.to_numpy()[:-1]+cycle.speed_m_s.to_numpy()[1:])/2
        acceleration=np.diff(cycle.speed_m_s)/dt
        bins=np.array(ext["speed_bin_edges_kmh"])
        fractions=np.histogram(v*3.6,bins=bins,weights=dt)[0]/dt.sum()
        design=roadload_design(v,dt,problem.base)
        designs.append(design)
        obs=trip.observation
        gaps=np.diff(obs.time_s)
        rows.append({"trip_id":trip.trip_id,"duration_s":float(dt.sum()),"soc_min":float(obs.soc.min()),"soc_max":float(obs.soc.max()),
                     "maximum_conditioned_speed_kmh":float(v.max()*3.6),"speed_bin_time_fractions":fractions.tolist(),
                     "quasi_steady_time_fraction":float(np.sum(dt*((v*3.6>=ext["quasi_steady_min_speed_kmh"]) & (abs(acceleration)<=ext["quasi_steady_max_abs_acceleration_m_s2"]))) /dt.sum()),
                     "observation_gap_over_5s_fraction":float(gaps[gaps>5].sum()/gaps.sum()),
                     "ideal_roadload_information":column_information(design)})
    # 实际复合残差的两列，先投影掉另外三个参数的局部补偿方向。
    target=frozen_jac[:,:2]
    nuisance=frozen_jac[:,2:]
    partial=target-nuisance@(np.linalg.pinv(nuisance,rcond=1e-10)@target)
    return {"train_only":True,"trip_ids":[t.trip_id for t in problem.trips],"trips":rows,
            "speed_bin_edges_kmh":ext["speed_bin_edges_kmh"],
            "pooled_ideal_roadload_information":column_information(np.vstack(designs)),
            "actual_Cd_Cr_column_information":column_information(target),
            "actual_Cd_Cr_after_nuisance_projection":column_information(partial),
            "ideal_column_order":["rolling_coefficient","drag_coefficient"],
            "actual_column_order":["drag_coefficient","rolling_coefficient"],
            "limits":"Speed distribution and local Jacobian geometry only; ideal road-load columns omit drive nonlinearities and are not measured force observations. Full column rank is not practical identifiability."}


def identity() -> dict:
    paths=["configs/reliability_protocol.json","configs/calibration_protocol.json","configs/calibrated.json",
           "configs/reference.json","configs/split.json","records/calibration_freeze.json","records/control_freeze.json",
           "records/durability_freeze.json","records/source_manifest.json","results/tables/calibration_linearization.npz",
           "src/bev/model.py","src/bev/calibration.py","src/bev/reliability.py","checks/test_reliability.py",
           "data/processed/component_curves.json"]
    return {name:sha256(PROJECT_ROOT/name) for name in paths}


def summarize_draws(rows: list[dict], cfg: dict, ext: dict, center: np.ndarray) -> list[dict]:
    lower,upper=np.array(cfg["lower_bounds"]),np.array(cfg["upper_bounds"])
    span=upper-lower
    summary=[]
    for length in ext["block_lengths_s"]:
        requested=[r for r in rows if r["block_s"]==length]
        good=[r for r in requested if r["accepted"]]
        if not good:
            continue
        values=np.array([[r[name] for name in cfg["parameters"]] for r in good])
        quantiles=np.quantile(values,[.025,.5,.975],axis=0)
        midpoint=len(values)//2
        halves=[np.quantile(v,[.025,.975],axis=0) for v in [values[:midpoint],values[midpoint:]] if len(v)>1]
        half_difference=np.max(abs(halves[0]-halves[1]),axis=0) if len(halves)==2 else np.full(len(center),np.nan)
        for i,name in enumerate(cfg["parameters"]):
            summary.append({"parameter":name,"block_s":length,"frozen_estimate":float(center[i]),
                            "percentile025":float(quantiles[0,i]),"median":float(quantiles[1,i]),"percentile975":float(quantiles[2,i]),
                            "requested":len(requested),"accepted":len(good),"failed":len(requested)-len(good),
                            "at_lower_bound_fraction":float(np.mean(values[:,i]-lower[i]<=ext["boundary_tolerance_of_span"]*span[i])),
                            "at_upper_bound_fraction":float(np.mean(upper[i]-values[:,i]<=ext["boundary_tolerance_of_span"]*span[i])),
                            "near_lower_bound_fraction":float(np.mean(values[:,i]-lower[i]<=ext["near_boundary_fraction_of_span"]*span[i])),
                            "max_half_sample_tail_difference":float(half_difference[i]) if np.isfinite(half_difference[i]) else None,
                            "method":"centered_nonlinear_constrained_block_wild_percentile_range"})
    return summary


def run_reliability() -> dict:
    verify_sources()
    cfg,ext=calibration_protocol(),protocol()
    require_training_only(cfg,ext)
    frozen=read_json(PROJECT_ROOT/"records/calibration_freeze.json")
    if frozen["protocol_sha256"]!=sha256(PROJECT_ROOT/"configs/calibration_protocol.json") or frozen["calibrated_sha256"]!=sha256(PROJECT_ROOT/"configs/calibrated.json"):
        raise ValueError("原校准冻结不一致")
    fingerprint=identity()
    freeze_path=PROJECT_ROOT/"records/reliability_freeze.json"
    if freeze_path.exists():
        if read_json(freeze_path)["fingerprint"]!=fingerprint:
            raise ValueError("可靠性实验代码或协议变化；须保留原实验并显式建立新版本")
    else:
        write_json(freeze_path,{"status":"protocol_fixed_before_runs","date_beijing":datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                                "fingerprint":fingerprint,"train_only":True,"point_estimates_replaced":False})
    target=PROJECT_ROOT/"results/tables/nonlinear_reliability.json"
    if target.exists():
        cached=read_json(target)
        if cached["freeze_sha256"]==sha256(freeze_path):
            return cached
    problem=CalibrationProblem(cfg)
    center=np.array([frozen["parameters"][name] for name in cfg["parameters"]])
    lower,upper=np.array(cfg["lower_bounds"]),np.array(cfg["upper_bounds"])
    span=upper-lower
    frozen_residual=problem.residual(center)
    metadata=problem.metadata
    linear=np.load(PROJECT_ROOT/"results/tables/calibration_linearization.npz")
    np.testing.assert_allclose(frozen_residual,linear["residual"],atol=1e-10,rtol=1e-10)
    np.testing.assert_allclose(center,linear["estimate"],atol=1e-12,rtol=0)
    inverse=np.linalg.pinv(linear["jacobian"]*span,rcond=1e-10)
    excitation=excitation_diagnostics(problem,linear["jacobian"],ext)
    checkpoint_path=PROJECT_ROOT/"records/reliability_checkpoint.json"
    rows=[]
    if checkpoint_path.exists():
        checkpoint=read_json(checkpoint_path)
        if checkpoint["freeze_sha256"]!=sha256(freeze_path):
            raise ValueError("可靠性断点与冻结不一致")
        rows=checkpoint["rows"]
    completed={(r["block_s"],r["replicate"]) for r in rows}
    signs_artifact={}
    started=time.perf_counter()
    for length in ext["block_lengths_s"]:
        count=ext["repetitions_by_block_s"][f"{length:g}"]
        matrix,keys=block_residual_matrix(frozen_residual,metadata,length)
        rng=np.random.default_rng(np.random.SeedSequence([ext["seed"],int(length)]))
        signs=rng.choice([-1.,1.],size=(count,len(keys)))
        signs_artifact[f"signs_{length:g}s"]=signs.astype(np.int8)
        for replicate,sign in enumerate(signs):
            if (length,replicate) in completed:
                continue
            perturb=np.asarray(matrix@sign)
            z0=np.clip((center-lower)/span-inverse@perturb,1e-8,1-1e-8)
            infeasible_start=bool(np.max(abs(problem.residual(lower+z0*span)))>=9999.)
            if infeasible_start:
                z0=(center-lower)/span
            def objective(z):
                return centered_residual(problem.residual(lower+z*span),frozen_residual,perturb)
            starts=[z0]
            audited=replicate % ext["multistart_every"]==0
            if audited:
                alternate=np.array(ext["alternate_start_fraction"])
                if np.max(abs(problem.residual(lower+alternate*span)))>=9999.:
                    alternate=(center-lower)/span
                starts.append(alternate)
            fits=[least_squares(objective,start,bounds=(np.zeros(len(center)),np.ones(len(center))),x_scale="jac",
                                max_nfev=ext["max_nfev"],ftol=ext["ftol"],xtol=ext["xtol"],gtol=ext["gtol"]) for start in starts]
            good=[fit for fit in fits if fit.success and np.isfinite(fit.cost) and np.max(abs(problem.residual(lower+fit.x*span)))<9999.]
            fit=min(good or fits,key=lambda fit:fit.cost)
            estimate=lower+fit.x*span
            row={"block_s":length,"replicate":replicate,"accepted":bool(len(good)>0),"optimizer_success":bool(fit.success),
                 "optimizer_status":int(fit.status),"cost":float(fit.cost),"optimality":float(fit.optimality),"nfev":int(fit.nfev),
                 "blocks":len(keys),"infeasible_warm_start_fallback":infeasible_start,"multistart_audit":audited,
                 "start_costs":[float(value.cost) for value in fits],"start_success":[bool(value.success) for value in fits],
                 "multistart_cost_gap":float(abs(fits[0].cost-fits[1].cost)) if audited else None,
                 **dict(zip(cfg["parameters"],map(float,estimate)))}
            rows.append(row)
            if (replicate+1)%5==0 or replicate==0:
                write_json(checkpoint_path,{"freeze_sha256":sha256(freeze_path),"rows":rows})
                print(f"Nonlinear wild {length:g}s: {replicate+1}/{count}; accepted {sum(r['accepted'] for r in rows)} of {len(rows)}",flush=True)
    summary=summarize_draws(rows,cfg,ext,center)
    correlations={}
    for length in ext["block_lengths_s"]:
        values=np.array([[r[name] for name in cfg["parameters"]] for r in rows if r["accepted"] and r["block_s"]==length])
        correlations[f"{length:g}"]=np.corrcoef(values.T).tolist() if len(values)>1 else None
    np.savez_compressed(PROJECT_ROOT/"results/tables/nonlinear_wild_signs.npz",**signs_artifact)
    result={"freeze_sha256":sha256(freeze_path),"train_only":True,"trip_ids":ext["fit_trip_ids"],"point_estimates_replaced":False,
            "rows":rows,"summary":summary,"correlations":correlations,"excitation":excitation,"runtime_this_invocation_s":time.perf_counter()-started,
            "requested":sum(ext["repetitions_by_block_s"].values()),"accepted":sum(r["accepted"] for r in rows),
            "multistart_audits":sum(r["multistart_audit"] for r in rows),
            "frozen_residual_reproduced":True,"limits":ext["limits"]}
    if identity()!=fingerprint:
        raise ValueError("计算中冻结输入变化，结果未作为完成实验保存")
    pd.DataFrame(rows).to_csv(PROJECT_ROOT/"results/tables/nonlinear_wild_draws.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(summary).to_csv(PROJECT_ROOT/"results/tables/nonlinear_wild_ranges.csv",index=False,encoding="utf-8-sig")
    write_json(target,result)
    write_json(checkpoint_path,{"freeze_sha256":sha256(freeze_path),"rows":rows,"completed":True})
    verify_sources()
    return result
