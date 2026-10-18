"""Create a compact shareable regional development package and integrity list."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import zipfile

from download_sources import ROOT


def main():
    paths = []
    paths.extend(ROOT.glob("*.md"))
    paths.extend(ROOT.glob("*.json"))
    paths.append(ROOT / "requirements.txt")
    paths.extend((ROOT / "scripts").glob("*.py"))
    paths.extend(p for p in (ROOT / "processed").rglob("*") if p.is_file())
    # The patched OSM can be rebuilt from the source and refinement script.
    paths.extend(p for p in (ROOT / "refined").rglob("*")
                 if p.is_file() and p.name != "osm_with_assumed_defaults.xml")
    # Include small original snapshots/collection evidence, not whole-HK GIS.
    for directory in ["raw/hk_transport", "raw/live", "raw/vehicle_classes", "raw/journey_time"]:
        paths.extend(p for p in (ROOT / directory).glob("*") if p.is_file() and p.suffix != ".zip")
    paths.extend(p for p in (ROOT / "raw/osm").glob("*.json"))
    paths.extend(p for p in (ROOT / "raw").glob("*.json"))
    paths.append(ROOT / "raw/official_gis/revision_cutoff.csv")
    paths = sorted({p for p in paths if p.exists() and p.name != "package_manifest.json"})
    manifest = {"created_at": datetime.now().astimezone().isoformat(),
        "purpose": "Regional development data, not a validated baseline or complete competition submission",
        "files": [{"file": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size,
                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}
    m = ROOT / "package_manifest.json"
    m.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.append(m)
    target = ROOT / "B题开发数据包.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as archive:
        for p in paths:
            archive.write(p, p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(target) as archive:
        invalid = archive.testzip()
        if invalid:
            raise RuntimeError("ZIP CRC failed: " + invalid)
    print(json.dumps({"package": str(target), "files": len(paths), "bytes": target.stat().st_size,
                      "within_20_MB": target.stat().st_size <= 20_000_000}, ensure_ascii=False))


if __name__ == "__main__":
    main()
