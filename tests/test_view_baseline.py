# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；测试设计与研究使用待队员复核。
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import view_baseline as viewer


class BaselineViewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".baseline-view-test-", dir=viewer.ROOT)
        self.root = Path(self.temporary.name)
        files = {
            "network.net.xml": '<net><edge id="e"><lane id="e_0" shape="0,0 100,0" /></edge></net>',
            "fcd.xml": '<fcd-export><timestep time="0"><vehicle id="v0" x="5" y="0" angle="90" speed="0" /></timestep><timestep time="5" /></fcd-export>',
            "summary.xml": '<summary><step time="0" running="1" arrived="0" waiting="0" /><step time="5" running="0" arrived="1" waiting="0" /></summary>',
            "route_manifest.json": '{"routes": [{"id":"r0", "source_id":"south_to_north"}]}',
            "metrics.json": json.dumps({"model_status": "uncalibrated_technical_baseline", "planned": 1, "arrived": 1, "per_route": {"r0": {"planned": 1, "arrived": 1}}}),
        }
        for name, content in files.items():
            (self.root / name).write_text(content, encoding="utf-8")
        manifest = {"status": "completed", "run_id": "test", "config": {"lane_count": 1, "ak": "SECRET_SENTINEL"}, "artifacts": {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest() for name in files}}
        (self.root / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_frames_coordinates_and_missing_physical_mapping(self):
        data = viewer.read_run(self.root)
        self.assertEqual(data["frame_count"], 2)
        self.assertEqual(data["frame_intervals_s"], [5])
        self.assertEqual(data["frames"][0]["vehicles"][0]["x"], 5)
        self.assertEqual(data["frames"][0]["vehicles"][0]["speed_m_s"], 0)
        self.assertEqual(data["frames"][1]["vehicles"], [])
        self.assertIsNone(data["physical_time_mapping"])
        self.assertEqual(data["distinct_vehicle_ids_in_fcd"], 1)
        self.assertEqual(data["per_route"][0]["label"], "南端至北端")
        self.assertNotIn("SECRET_SENTINEL", json.dumps(data))

    def test_changed_output_is_rejected(self):
        with (self.root / "fcd.xml").open("a") as stream:
            stream.write("\n")
        with self.assertRaises(ValueError):
            viewer.read_run(self.root)

    def test_internal_lane_geometry_and_vehicle_snapshot_are_preserved(self):
        changes = {
            "network.net.xml": '<net><edge id="e"><lane id="e_0" shape="0,0 100,0" /></edge><edge id=":junction_0" function="internal"><lane id=":junction_0_0" shape="100,0 101,1 102,2" /></edge></net>',
            "fcd.xml": '<fcd-export><timestep time="0"><vehicle id="v0" lane=":junction_0_0" x="101" y="1" angle="45" speed="2" /></timestep><timestep time="5" /></fcd-export>',
        }
        manifest_path = self.root / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for name, content in changes.items():
            (self.root / name).write_text(content, encoding="utf-8")
            manifest["artifacts"][name] = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        data = viewer.read_run(self.root)
        self.assertEqual(len(data["lanes"]), 2)
        self.assertEqual(data["lanes"][1], {"id": ":junction_0_0", "points": [[100, 0], [101, 1], [102, 2]]})
        vehicle = data["frames"][0]["vehicles"][0]
        self.assertEqual((vehicle["x"], vehicle["y"], vehicle["angle"], vehicle["speed_m_s"]), (101, 1, 45, 2))

    def test_unfinished_run_is_rejected(self):
        path = self.root / "run_manifest.json"
        manifest = json.loads(path.read_text())
        manifest["status"] = "running"
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            viewer.read_run(self.root)


if __name__ == "__main__":
    unittest.main()
