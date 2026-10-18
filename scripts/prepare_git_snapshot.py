#!/usr/bin/env python3
"""Prepare and audit the public xqw-b snapshot without Git or network operations.

The two support manifests supply path allowlists, not current-content hashes.
Current workspace bytes are always read; old archives are never extracted over
the working tree. Only the fixed workspace-local checkout may receive copies.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "outputs/quality/git_upload_inventory.json"
CHECKOUT = ROOT / ".cache/github_xqw_b_checkout"
MANIFESTS = ("submission/support_manifest.json", "submission/support_manifest_v2.json")
EXCLUDED_PARTS = {
    ".git", ".venv", ".cache", ".aws", ".codex", ".agents", "__pycache__",
    "node_modules", ".runtime", "runtime", "logs", "chrome-profile", "chromium-profile",
}
LARGE_REFERENCE = "research/parameter_audit/downloads/ruijin_hospital_eia_public.pdf"
MAX_SMALL_REFERENCE = 25_000_000
TOKEN = re.compile(rb"(?<![A-Za-z0-9])(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
CREDENTIAL_URL = re.compile(rb"https?://[^\s<>\"']*[?&](?:ak|sk|sn|access_token|api_key)=[A-Za-z0-9_%.-]{12,}")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_name(name: str) -> str:
    p = PurePosixPath(name)
    if (p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name
            or str(p) != name or EXCLUDED_PARTS.intersection(p.parts)
            or name.endswith((".local.json", ".pyc", ".pyo"))
            or any(part.startswith((".env", "chrome-profile", "chromium-profile")) for part in p.parts)):
        raise ValueError("Excluded or invalid path: " + name)
    return name


def select_files() -> tuple[list[Path], list[dict]]:
    selected: set[Path] = set()
    manifest_sources = []
    for name in MANIFESTS:
        p = ROOT / name
        raw = p.read_bytes()
        contents = json.loads(raw)
        for item in contents["files"]:
            selected.add(ROOT / safe_name(item["path"]))
        manifest_sources.append({"path": name, "sha256": sha(raw),
                                 "use": "path allowlist only; original entry hashes are not reused"})
    for pattern in ("*.md", "*.py", "requirements*.txt"):
        selected.update(ROOT.glob(pattern))
    selected.add(ROOT / ".gitignore")
    selected.add(ROOT / ".gitattributes")
    selected.add(Path(__file__).resolve())
    for folder in ("src", "competition", "experiments", "research", "submission"):
        for p in (ROOT / folder).rglob("*"):
            if not p.is_file():
                continue
            name = p.relative_to(ROOT).as_posix()
            if name == LARGE_REFERENCE:
                continue
            safe_name(name)
            if folder in {"experiments", "research"} and p.stat().st_size > MAX_SMALL_REFERENCE:
                raise ValueError("Unexpected large reference requires an explicit selection: " + name)
            selected.add(p)
    for name in (
        "outputs/governance_v2/explain_route_choice.py",
        "outputs/quality/check_v2_view.py",
        "outputs/identification_v2/engine_smoke/base/metrics.json",
    ):
        selected.add(ROOT / name)
    validation = ROOT / "outputs/quality/git_upload_validation.json"
    if validation.is_file():
        selected.add(validation)
    selected.discard(INVENTORY)  # Its own hash cannot be included recursively.
    for p in selected:
        name = safe_name(p.relative_to(ROOT).as_posix())
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(ROOT):
            raise ValueError("Missing or unsafe selected file: " + name)
        if any((ROOT / parent).is_symlink() for parent in p.relative_to(ROOT).parents
               if str(parent) != "."):
            raise ValueError("Symlink parent is not permitted: " + name)
    return sorted(selected), manifest_sources


def local_secrets() -> list[bytes]:
    values: list[bytes] = []
    p = ROOT / "config/baidu.local.json"
    if not p.exists():
        return values

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in {"ak", "sk", "secret_key", "access_key"} and isinstance(value, str) and len(value) >= 12:
                    values.append(value.encode())
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(json.loads(p.read_text(encoding="utf-8")))
    return values


def scan_files(paths: list[Path]) -> tuple[dict, list[dict]]:
    secrets = local_secrets()
    counts: Counter = Counter()
    problems: list[dict] = []
    items = []
    if not shutil.which("pdftotext"):
        raise ValueError("pdftotext is required to audit embedded PDF text")

    def issue(name, kind):
        problems.append({"path": name, "issue": kind})

    def examine(raw: bytes, name: str, depth: int = 0):
        if any(value in raw for value in secrets):
            issue(name, "known local credential value")
        if TOKEN.search(raw):
            issue(name, "token or private-key pattern")
        if CREDENTIAL_URL.search(raw):
            issue(name, "credential-bearing URL pattern")
        if raw.startswith(b"%PDF-"):
            counts["pdf_documents"] += 1
            process = subprocess.run(["pdftotext", "-", "-"], input=raw, capture_output=True, timeout=60)
            if process.returncode:
                issue(name, "PDF text extraction failed")
            else:
                if any(value in process.stdout for value in secrets):
                    issue(name, "known local credential in PDF text")
                if TOKEN.search(process.stdout) or CREDENTIAL_URL.search(process.stdout):
                    issue(name, "credential pattern in PDF text")
        stream = io.BytesIO(raw)
        if zipfile.is_zipfile(stream):
            if depth >= 6:
                issue(name, "archive nesting exceeds audit limit")
                return
            counts["archives"] += 1
            with zipfile.ZipFile(stream) as archive:
                if archive.testzip() is not None:
                    issue(name, "archive CRC failure")
                names = archive.namelist()
                if len(names) != len(set(names)):
                    issue(name, "duplicate archive members")
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    member_name = name + "::" + member.filename
                    parts = PurePosixPath(member.filename).parts
                    if (member.filename.endswith(".local.json") or EXCLUDED_PARTS.intersection(parts)
                            or PurePosixPath(member.filename).is_absolute() or ".." in parts
                            or stat.S_ISLNK(member.external_attr >> 16)):
                        issue(member_name, "private or unsafe archive member")
                    counts["archive_members"] += 1
                    examine(archive.read(member), member_name, depth + 1)

    for p in paths:
        counts["selected_files"] += 1
        raw = p.read_bytes()
        name = p.relative_to(ROOT).as_posix()
        examine(raw, name)
        items.append({"path": name, "bytes": len(raw), "sha256": sha(raw)})
    if problems:
        # Only paths and categories are ever reported; credential bytes stay in memory.
        raise ValueError("Public upload audit failed: " + json.dumps(problems, ensure_ascii=False))
    return {"status": "passed", "known_local_secret_values_checked": len(secrets),
            **dict(counts), "credential_findings": 0,
            "scope": "Exact local AK/SK, token/private-key and credential URL patterns, nested archives and extracted PDF text; no general PII classification."}, items


def copy_snapshot(paths: list[Path], target: Path, inventory: dict) -> None:
    if target.resolve() != CHECKOUT.resolve() or not target.resolve().is_relative_to(ROOT):
        raise ValueError("Copy destination must be the fixed workspace .cache/github_xqw_b_checkout")
    if target.is_symlink() or (ROOT / ".cache").is_symlink():
        raise ValueError("Copy destination cannot be a symlink")
    target.mkdir(parents=True, exist_ok=True)
    by_name = {item["path"]: item for item in inventory["files"]}
    for p in paths:
        name = p.relative_to(ROOT).as_posix()
        raw = p.read_bytes()
        if sha(raw) != by_name[name]["sha256"]:
            raise ValueError("Source changed after audit; rerun preparation: " + name)
        dest = target / name
        if dest.is_symlink() or not dest.resolve().is_relative_to(target.resolve()):
            raise ValueError("Unsafe existing checkout destination: " + name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    dest = target / INVENTORY.relative_to(ROOT)
    if dest.is_symlink() or not dest.resolve().is_relative_to(target.resolve()):
        raise ValueError("Unsafe inventory destination")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(INVENTORY.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy-to", type=Path, help="Optional fixed workspace-local checkout destination")
    args = parser.parse_args()
    paths, sources = select_files()
    scan, items = scan_files(paths)
    inventory = {
        "purpose": "Current public project snapshot for Git branch xqw-b",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_sources": sources,
        "content_policy": "Read current workspace files. Support manifests provide names only; historical hashes are not asserted for revised current documents.",
        "file_count": len(items), "total_bytes": sum(item["bytes"] for item in items),
        "largest_file": max(items, key=lambda item: item["bytes"]),
        "files": items, "security_audit": scan,
        "additional_inventory_file": "outputs/quality/git_upload_inventory.json is copied separately and omitted from its own hash list to avoid self-reference.",
        "excluded": [
            "config/*.local.json and private configuration; credentials are used only in memory for the audit",
            "workspace tools/environments, cache, bytecode, browser profiles, logs and runtime state",
            "large SUMO output XML except source traces already explicitly listed by the V1/V2 support manifests",
            "unselected output directories and intermediate screenshots; full local experimental output remains in the workspace",
            "55,944,256-byte research/parameter_audit/downloads/ruijin_hospital_eia_public.pdf; source URL/hash, extracted text and evidence screenshots are retained",
        ],
        "copy_policy": "Only the fixed workspace .cache/github_xqw_b_checkout may receive copies; no Git, deletion, network or remote push is performed.",
    }
    INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.copy_to is not None:
        copy_snapshot(paths, args.copy_to, inventory)
    print(json.dumps({"status": "passed", "selected_files": len(items),
                      "selected_bytes": inventory["total_bytes"], "security_audit": scan,
                      "inventory": str(INVENTORY.relative_to(ROOT)),
                      "copied": args.copy_to is not None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
