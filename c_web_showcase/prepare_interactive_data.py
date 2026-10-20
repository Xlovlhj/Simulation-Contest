#!/usr/bin/env python3
"""Read-only export of parameters and fixed experiment summaries, not trajectories."""
import hashlib
import csv
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "CCSF2605207fj"


def main():
    sources = []

    def read(name):
        content = (SOURCE / name).read_bytes()
        sources.append({"path": f"CCSF2605207fj/{name}", "sha256": hashlib.sha256(content).hexdigest()})
        return json.loads(content)

    parameters = {
        "reference": read("configs/reference.json"),
        "calibrated": read("configs/calibrated.json"),
        "curves": read("data/processed/component_curves.json"),
        "protocol": read("configs/control_protocol.json"),
        "optimizedRule": read("configs/optimized_rule.json"),
        "scope": "Current-state algebraic/thermal-balance illustration. No cycle integration or new optimization.",
    }
    for name in ("src/bev/model.py", "src/bev/control.py"):
        content = (SOURCE / name).read_bytes()
        sources.append({"path": f"CCSF2605207fj/{name}", "sha256": hashlib.sha256(content).hexdigest()})
    parameters["sources"] = sources
    summaries = json.loads((HERE / "data/showcase.json").read_text())["summaries"]
    # A small, static baseline evidence figure for the detail window, not a player.
    member = "results/timeseries/scenario_A_reference.csv"
    with tarfile.open(SOURCE / "payload/trajectories.tar.xz") as archive:
        content = archive.extractfile(member).read()
    manifest = json.loads((SOURCE / "payload/trajectories.json").read_text())
    expected = next(row["sha256"] for row in manifest["files"] if row["path"] == member)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Static baseline source differs from original trajectory manifest")
    raw = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    columns = ["time_s", "soc", "battery_c", "cabin_c"]
    rows = [[float(row[key]) for key in columns] for row in raw]
    mandatory = {0, len(rows) - 1}
    for j in range(1, len(columns)):
        mandatory.add(min(range(len(rows)), key=lambda i: rows[i][j]))
        mandatory.add(max(range(len(rows)), key=lambda i: rows[i][j]))
    indices = mandatory | {round(i * (len(rows) - 1) / 98) for i in range(99)}
    while len(indices) > 99:
        ordered = sorted(indices)
        removable = [
            (ordered[i + 1] - ordered[i - 1], ordered[i])
            for i in range(1, len(ordered) - 1) if ordered[i] not in mandatory
        ]
        indices.remove(min(removable)[1])
    q1 = summaries["q1"]
    summaries["q1StaticCurve"] = {
        "fields": columns,
        "rows": [[0.0, q1["soc_initial"], q1["battery_initial_c"], q1["cabin_initial_c"]]]
                + [rows[i] for i in sorted(indices)],
        "source": f"CCSF2605207fj/payload/trajectories.tar.xz!/{member}",
        "source_sha256": expected,
        "raw_rows": len(raw),
        "export_rows": 100,
        "note": "Original 3600-row reference trace, 99 selected rows preserving each plotted global extremum, plus the source summary initial state. Static figure only.",
    }
    result = {
        "scope": "Frozen experiments from paper/support materials; UI choices never recalculate these results.",
        "summaries": summaries,
        "sources": [
            {"path": f"CCSF2605207fj/{name}",
             "sha256": hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()}
            for name in ("results/tables/scenario_A_summary.json",
                         "results/tables/control_evaluation.json",
                         "results/tables/durability_comparisons.csv",
                         "records/revision_20261019/results.json",
                         "results/tables/calibration_metrics.csv",
                         "results/tables/parameter_intervals.csv",
                         "results/tables/scenario_A_sensitivity.csv")
        ],
    }
    for filename, payload in (("research-parameters.json", parameters), ("research-results.json", result)):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        (HERE / "data" / filename).write_text(encoded, encoding="utf-8")
        print(f"{filename}: {len(encoded.encode()):,} bytes")


if __name__ == "__main__":
    main()
