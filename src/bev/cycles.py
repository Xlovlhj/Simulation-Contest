"""公开CLTC-P复现序列的哈希、单位与跨来源逐点核验；AI辅助见记录。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.integrate import trapezoid

from .data import read_reference_cycle
from .io import PROJECT_ROOT, read_json, sha256, write_json


def load_cltc(ambient_c: float = 25.0) -> tuple[pd.DataFrame, dict]:
    catalog = read_json(PROJECT_ROOT / "records/cltc_downloads.json")
    if len(catalog["sources"]) != 2:
        raise ValueError("CLTC公开来源未完整归档")
    for source in catalog["sources"]:
        path = PROJECT_ROOT / source["local_path"]
        if sha256(path) != source["sha256"]:
            raise ValueError(f"CLTC原始文件已变更：{path.name}")
    motives = pd.read_csv(PROJECT_ROOT / "data/external/cltc_motives.csv")
    other = pd.read_csv(PROJECT_ROOT / "data/external/cltc_ec_window.csv")
    # 第一份1..1800为点编号，第二份0..1800为时间；不修改下载原件。
    tm = motives.Time_s.to_numpy(dtype=float)-1
    vm = motives.Speed_km_h.to_numpy(dtype=float)/3.6
    te = other.cycSecs.to_numpy(dtype=float)
    ve = other.cycMps.to_numpy(dtype=float)
    if not np.array_equal(tm, np.arange(1800)) or not np.array_equal(te, np.arange(1801)):
        raise ValueError("CLTC时间轴不符合已核验版本")
    difference = float(np.max(np.abs(vm-ve[:1800])))
    if difference > 1e-7 or ve[-1] != 0 or (ve<0).any():
        raise ValueError("两份公开CLTC速度序列无法相互核对")
    # 采用有MIT许可及论文关联的第一来源；最后1800s零速点由第二份核对。
    canonical_v = np.r_[vm, 0.0]
    distance = float(trapezoid(canonical_v, te)/1000)
    if abs(distance-14.47975)>1e-6 or abs(canonical_v.max()*3.6-114)>1e-6:
        raise ValueError("CLTC基础统计异常")
    cycle = pd.DataFrame({"time_s": te, "speed_m_s": canonical_v, "ambient_c": ambient_c})
    reference = read_reference_cycle()
    reference_difference = float(np.max(np.abs(reference.speed_m_s-canonical_v)))
    metadata = {"name": "CLTC-P public reproduction", "verification_level": "two_public_repository_sequences_cross_checked",
                "official_standard_file_directly_checked": False, "organizer_confirmation": False,
                "source_note": "Not an official erratum. Public sequence attributed to MOTIVES-LAB with a second repository cross-check; common ancestry is possible.",
                "duration_s": 1800, "points": 1801, "distance_km": distance, "max_speed_kmh": 114,
                "cross_source_max_difference_m_s": difference,
                "normalization": "MOTIVES time column 1..1800 shifted to 0..1799; terminal zero at1800 confirmed by second source; km/h divided by3.6.",
                "attachment3_is_identical": bool(reference_difference<1e-7),
                "attachment3_max_speed_difference_m_s": reference_difference,
                "sources": catalog["sources"]}
    return cycle, metadata


def verify_cltc() -> dict:
    cycle, metadata = load_cltc()
    cycle.to_csv(PROJECT_ROOT / "data/processed/cltc_p.csv", index=False, encoding="utf-8-sig")
    write_json(PROJECT_ROOT / "results/tables/cltc_verification.json", metadata)
    return metadata
