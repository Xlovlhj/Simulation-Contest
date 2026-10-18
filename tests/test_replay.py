# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；测试设计与研究使用待队员复核。
"""Offline replay correctness and boundary tests; fixtures stay in the workspace."""
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from scripts import replay_baidu as replay


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.script_count = 0
        self.external = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.script_count += 1
        if attrs.get("src") or attrs.get("href", "").startswith("http"):
            self.external.append(attrs)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".replay-test-", dir=replay.ROOT)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.assessments = self.root / "assessments.json"
        self.assessments.write_text('{"records": []}', encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def batch(self, name, area="shanghai_ruijin", cycle=None):
        folder = self.raw / name
        folder.mkdir()
        request = {"endpoint": "/directionlite/v1/driving", "response_file": "route.json", "requested_at": "2026-10-17T14:00:00+08:00", "received_at": "2026-10-17T14:00:01+08:00", "params": {"timestamp": "1792216800", "ak": "NEVER_EXPORT_SENTINEL"}}
        if cycle is not None:
            request["cycle_id"] = cycle
        manifest = {"study_area": {"id": area}, "coordinate_system": "bd09ll", "requests": [request], "probe_bounds": {"west": 121.46, "east": 121.48, "south": 31.21, "north": 31.23}}
        payload = {"status": 0, "secret": "NEVER_EXPORT_SENTINEL", "result": {"routes": [{"distance": 2000, "duration": 600, "steps": [{"path": "121.47,31.22;121.471,31.221;121.49,31.24"}]}]}}
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "route.json").write_text(json.dumps(payload), encoding="utf-8")
        return folder, manifest, request

    def test_filters_rejected_and_other_city(self):
        self.batch("accepted")
        self.batch("rejected")
        self.batch("historical")
        self.batch("beijing", "beijing_haidian")
        self.assessments.write_text(json.dumps({"records": [{"probe_directory": "rejected", "assessment": "rejected_location_semantics"}, {"probe_directory": "historical", "assessment": "historical_capability_probe"}]}), encoding="utf-8")
        snapshots, excluded = replay.read_snapshots(self.raw, self.assessments, "shanghai_ruijin")
        self.assertEqual([s["batch"] for s in snapshots], ["accepted"])
        self.assertEqual(len(excluded), 3)
        route = snapshots[0]["routes"][0]
        self.assertEqual(route["points_outside_query_bounds"], 1)
        self.assertEqual(route["source_data_time"], "")
        self.assertEqual(route["eta_implied_speed_kmh"], 12)

    def test_invalid_coordinates_break_paths(self):
        parts, count = replay.parse_path("121.47,31.22;NaN,31.22;121.471,31.221;500,31;121.472,31.222")
        self.assertEqual(count, 2)
        self.assertEqual([len(p) for p in parts], [1, 1, 1])
        self.assertAlmostEqual(replay.haversine([0, 0], [0, 1]), 111195.08, places=1)

    def test_no_missing_value_to_zero_and_no_secrets_exported(self):
        folder, manifest, _ = self.batch("valid")
        traffic = {"status": 0, "road_traffic": [{"road_name": "UNKNOW"}, {"road_name": "瑞金二路", "congestion_sections": [{"speed": 0, "status": 4}]}]}
        (folder / "traffic.json").write_text(json.dumps(traffic), encoding="utf-8")
        manifest["requests"].append({"endpoint": "/traffic/v1/bound", "response_file": "traffic.json"})
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
        snapshots, excluded = replay.read_snapshots(self.raw, self.assessments, "shanghai_ruijin")
        self.assertIsNone(snapshots[0]["traffic"][0]["speed_kmh"])
        self.assertEqual(snapshots[0]["traffic"][0]["road_name"], "")
        self.assertEqual(snapshots[0]["traffic"][1]["speed_kmh"], 0)
        out, processed = self.root / "out", self.root / "processed"
        replay.write_outputs(snapshots, excluded, out, processed)
        for p in list(out.rglob("*")) + list(processed.rglob("*")):
            if p.is_file():
                self.assertNotIn("NEVER_EXPORT_SENTINEL", p.read_text(encoding="utf-8-sig"))
        traffic_csv = next(processed.rglob("traffic.csv"))
        with traffic_csv.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["speed_kmh"], "")
        self.assertEqual(rows[1]["speed_kmh"], "0.0")
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()})

    def test_collection_jsonl_cycles_and_partial_tail(self):
        folder, manifest, request = self.batch("collection", cycle=1)
        manifest.pop("requests")
        manifest["requests_index"] = "requests.jsonl"
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        request2 = dict(request, cycle_id=2, requested_at="2026-10-17T14:02:00+08:00", route_pair_id="northbound")
        (folder / "requests.jsonl").write_text(json.dumps(request) + "\n" + json.dumps(request2) + '\n{"unfinished":', encoding="utf-8")
        snapshots, _ = replay.read_snapshots(self.raw, self.assessments, "shanghai_ruijin")
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[1]["routes"][0]["label"], "northbound")
        self.assertIn("unreadable_request_record", {i["code"] for i in snapshots[0]["issues"]})

    def test_html_script_escape_and_csv_formula_escape(self):
        malicious = '</script><script>alert(1)</script>&\u2028'
        serialized = replay.safe_json({"value": malicious})
        self.assertNotIn("<", serialized)
        self.assertEqual(json.loads(serialized)["value"], malicious)
        parser = ScriptParser()
        parser.feed(replay.PAGE.replace("__DATA__", serialized))
        self.assertEqual(parser.script_count, 2)
        self.assertFalse(parser.external)
        self.assertEqual(replay.csv_value("=1+1"), "'=1+1")
        self.assertEqual(replay.csv_value(-1), -1)

    def test_junction_reference_and_model_bounds_are_distinct(self):
        folder, manifest, request = self.batch("survey")
        manifest["purpose"] = "junction_and_route_survey"
        manifest["requests"].insert(0, {"endpoint": "/geocoding/v3/", "response_file": "node.json", "label": "RJ_YJ", "params": {"address": "瑞金二路与永嘉路交叉口"}})
        (folder / "node.json").write_text(json.dumps({"status": 0, "result": {"location": {"lng": 121.472, "lat": 31.217}, "level": "交叉路口", "confidence": 80}}), encoding="utf-8")
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        context = self.root / "context.json"
        context.write_text(json.dumps({"study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll", "model_bounds": {"west": 121.45, "east": 121.50, "south": 31.20, "north": 31.25}, "routes": [
            {"id": "natural", "use_for_baseline": True, "accepted_surface": True, "points": [[121.47, 31.22], [121.48, 31.23]], "duration_s": 100},
            {"id": "forced_waypoint", "use_for_baseline": False, "accepted_surface": True, "points": [[121.47, 31.22], [121.48, 31.23]]}]}), encoding="utf-8")
        snapshots, _ = replay.read_snapshots(self.raw, self.assessments, "shanghai_ruijin", context)
        s = snapshots[0]
        self.assertEqual(len(s["nodes"]), 1)
        self.assertEqual(len(s["reference_nodes"]), 1)
        self.assertIn("急救入口未验证", s["nodes"][0]["note"])
        self.assertEqual(s["routes"][0]["points_outside_query_bounds"], 1)
        self.assertEqual(s["routes"][0]["points_outside_model_bounds"], 0)
        self.assertEqual(len(s["reference_routes"]), 1)
        self.assertEqual(s["reference_routes"][0]["id"], "natural")
        self.assertNotIn("duration_s", s["reference_routes"][0])

    def test_paths_and_non_bd09_are_rejected(self):
        folder, manifest, request = self.batch("valid")
        self.assertIsNone(replay.response_path(folder, "../route.json"))
        self.assertIsNone(replay.response_path(folder, "../../config/baidu.local.json"))
        with self.assertRaises(ValueError):
            replay.inside_workspace(Path("/tmp/replay-out"))
        with self.assertRaises(ValueError):
            replay.inside_workspace(replay.ROOT / "config/baidu.local.json")
        manifest["coordinate_system"] = "wgs84"
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        snapshots, excluded = replay.read_snapshots(self.raw, self.assessments, "shanghai_ruijin")
        self.assertFalse(snapshots)
        self.assertEqual(len(excluded), 1)


if __name__ == "__main__":
    unittest.main()
