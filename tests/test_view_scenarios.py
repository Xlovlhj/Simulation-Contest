# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；测试设计与研究使用待队员复核。
import json
from pathlib import Path
import tempfile
import unittest

from scripts import view_scenarios as viewer


class ScenarioViewTests(unittest.TestCase):
    def test_verified_summary_rejects_changed_or_unhashed_data(self):
        with tempfile.TemporaryDirectory(prefix=".scenario-view-test-", dir=viewer.ROOT) as directory:
            root = Path(directory)
            path = root / "summary.json"
            path.write_text('[{"changed_assignments":0}]', encoding="utf-8")
            hashes = {path.name: viewer.sha256(path)}
            self.assertEqual(viewer.verified_json(root, path.name, hashes)[0]["changed_assignments"], 0)
            with self.assertRaises(ValueError):
                viewer.verified_json(root, path.name, {})
            path.write_text('[{"changed_assignments":20}]', encoding="utf-8")
            with self.assertRaises(ValueError):
                viewer.verified_json(root, path.name, hashes)

    def test_replay_replaces_per_route_demand_and_old_relative_link(self):
        data = {"scenario": {"demand_note": "固定6OD，每30秒；另1任务", "warning": "</script><script>unsafe()</script>"}}
        page = viewer.replay_page(data)
        self.assertNotIn("每条路线每", page)
        self.assertIn("data.scenario.demand_note", page)
        self.assertIn('href="../../index.html"', page)
        self.assertNotIn('href="../replay/index.html"', page)
        self.assertIn("B 协同机制未实现", page)
        self.assertNotIn("</script><script>unsafe()", page)

    def test_missing_and_zero_have_distinct_renderings(self):
        self.assertEqual(viewer.display_number(None), "—")
        self.assertEqual(viewer.display_number(0), "0")
        self.assertEqual(viewer.display_number(-4305), "-4,305")
        self.assertEqual(viewer.display_number("<tag>"), "&lt;tag&gt;")

    def test_overview_keeps_both_event_strengths_and_zero_result(self):
        rows = []
        pairs = []
        for prefix, speed, delta in (("primary_1mps", 1, 0), ("severe_0p5mps", .5, -4305)):
            for arm, time in (("fixed", 49071), ("guided", 49071 + delta)):
                rows.append({"case": f"{prefix}_S2_{arm}", "event_speed_m_s": speed,
                    "policy": viewer.policy_label(f"{prefix}_S2_{arm}"), "arrived": 240, "planned": 240,
                    "ordinary_system_time_s": time, "ordinary_departure_wait_s": 80,
                    "task_release_to_arrival_s": None, "changed_assignments": 0, "task_count": 0})
            pairs.append({"fixed": f"{prefix}_S2_fixed", "guided": f"{prefix}_S2_guided",
                "ordinary_system_time_delta_s": delta, "task_release_to_arrival_delta_s": None})
        page = viewer.overview_page({"batch_id": "test_batch", "manifest_sha256": "a" * 64, "rows": rows, "comparisons": pairs})
        self.assertIn("primary_1mps_S2_guided.html", page)
        self.assertIn("severe_0p5mps_S2_guided.html", page)
        self.assertIn("<td>0</td>", page)
        self.assertIn("<td>-4,305</td>", page)
        self.assertIn("<td>—</td>", page)


if __name__ == "__main__":
    unittest.main()
