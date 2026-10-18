"""路径、JSON与文件校验。AI辅助：Codex；可核实版本信息见records/ai_usage.md。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def attachment(number: int, root: Path = PROJECT_ROOT) -> Path:
    matches = list((root / "data/raw").glob(f"附件{number}*.xlsx"))
    if len(matches) != 1:
        raise ValueError(f"附件{number}应恰有一个文件，实际{len(matches)}")
    return matches[0]


def verify_sources(root: Path = PROJECT_ROOT) -> list[dict]:
    """首次建立清单，之后严格复核；同时验证仍存在的外层官方原件。"""
    manifest_path = root / "records/source_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        for item in manifest["files"]:
            p = root / item["project_path"]
            if not p.exists() or sha256(p) != item["sha256"]:
                raise ValueError(f"源文件缺失或已修改：{item['project_path']}")
        return manifest["files"]
    official = root.parent / "2026年第二届全国大学生仿真建模应用挑战赛赛题"
    files = sorted((root / "data/raw").glob("*.xlsx")) + sorted((root / "赛题资料").rglob("*.*"))
    items = []
    for p in files:
        candidates = list(official.rglob(p.name)) if official.exists() else []
        digest = sha256(p)
        if len(candidates) == 1 and sha256(candidates[0]) != digest:
            raise ValueError(f"复制件与官方原件不一致：{p.name}")
        items.append({"project_path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
                      "sha256": digest, "matches_original": len(candidates) == 1})
    write_json(manifest_path, {"date_beijing": "2026-10-17", "files": items})
    return items
