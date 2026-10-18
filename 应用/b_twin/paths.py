"""Resolve local project assets without depending on the working directory."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期未提供，提交前按实际使用记录补齐并人工核查。
from __future__ import annotations

import os
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
PROJECT = APP.parent
DATA = PROJECT / "B题数据工程"
REFINED = DATA / "refined"
INPUTS = REFINED / "all_counts"
SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
sys.path[:0] = [str(DATA / ".deps"), str(SUMO_HOME / "tools")]


def sumo_exe(name="sumo"):
    candidate = SUMO_HOME / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if not candidate.exists():
        raise FileNotFoundError(f"SUMO executable missing: {candidate}. Set SUMO_HOME.")
    return str(candidate)
