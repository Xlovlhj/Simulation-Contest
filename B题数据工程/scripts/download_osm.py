"""Acquire supplementary OSM geometry and turn-restriction relations.

Independent requests may be retried; failures are logged and never replaced
with invented data. OSM © contributors, licensed under ODbL.
"""
import json
import requests
from download_sources import ROOT, download


def main():
    directory = ROOT / "raw/osm"
    directory.mkdir(parents=True, exist_ok=True)
    queries = [
        ("kowloon_de.osm.xml", "[out:xml][timeout:80];way[highway](22.300,114.164,22.326,114.189);(._;>;);out body;"),
        ("restrictions.osm.xml", '[out:xml][timeout:80];rel[restriction](22.300,114.164,22.326,114.189);(._;>;);out body;'),
    ]
    results = []
    for name, query in queries:
        for endpoint in ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]:
            result = download("raw/osm/" + name, endpoint, {"data": query})
            results.append(result)
            if result.get("success"):
                data = (directory / name).read_text(encoding="utf-8")
                if "<osm" in data and "<remark>" not in data:
                    break
                result.update(success=False, error="OSM error payload")
    (ROOT / "sources_osm.json").write_text(json.dumps({"downloads": results,
        "attribution": "© OpenStreetMap contributors, ODbL", "role": "Supplementary topology"},
        ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
