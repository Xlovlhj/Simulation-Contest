"""Build a preliminary SUMO network from supplemental OSM data.

Generated TLS phases and inferred OSM attributes are modeling inputs, not
observed government signal timing. Official GIS is exported separately.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))


def main():
    exe = shutil.which("netconvert") or str(SUMO_HOME / "bin/netconvert.exe")
    osm = ROOT / "raw/osm/kowloon_de.osm.xml"
    restriction = ROOT / "raw/osm/restrictions.osm.xml"
    if restriction.exists():
        tree = ET.parse(osm)
        root = tree.getroot()
        keys = {(n.tag, n.get("id")) for n in root}
        for node in ET.parse(restriction).getroot():
            if node.tag in {"node", "way", "relation"} and (node.tag, node.get("id")) not in keys:
                root.append(node)
        osm = ROOT / "raw/osm/kowloon_merged.osm.xml"
        tree.write(osm, encoding="utf-8", xml_declaration=True)
    out = ROOT / "processed"
    out.mkdir(exist_ok=True)
    cmd = [exe, "--osm-files", str(osm), "--output-file", str(out / "kowloon.net.xml"),
           "--lefthand", "true", "--geometry.remove", "true", "--ramps.guess", "true",
           "--junctions.join", "true", "--tls.guess-signals", "true",
           "--tls.discard-simple", "true", "--tls.join", "true", "--no-turnarounds", "true",
           "--remove-edges.isolated", "true", "--keep-edges.by-vclass", "passenger",
           "--keep-edges.in-geo-boundary", "114.164,22.300,114.189,22.326",
           "--output.original-names", "true", "--output.street-names", "true",
           "--log", str(out / "netconvert.log"), "--error-log", str(out / "netconvert_warnings.log")]
    (out / "network_build.json").write_text(json.dumps({"command": cmd,
        "purpose": "Preliminary runnable network; GIS/topology/lane/TLS verification still required",
        "left_hand_traffic": True, "actual_signal_timing": False}, indent=2), encoding="utf-8")
    result = subprocess.run(cmd, capture_output=True)
    print(result.stdout.decode("utf-8", errors="replace")[-1500:])
    print(result.stderr.decode("utf-8", errors="replace")[-1500:])
    result.check_returncode()


if __name__ == "__main__":
    main()
