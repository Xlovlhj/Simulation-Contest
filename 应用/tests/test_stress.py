"""Stress contracts: exact agents, permissions, caps and isolated supply edits."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_twin.stress import shift_late_turns, change_supply, SCENARIOS, SEEDS, ARMS
from b_twin.boundary_supply import service_cycle


class StressContracts(unittest.TestCase):
    def sample(self, exits=8):
        routes = {'exit': ['origin', '1095134817', '45740606'],
                  'through': ['origin', '1095134817', 'downstream']}
        agents = pd.DataFrame([{'vehicle_id': f'v{i}', 'origin_edge': 'origin',
            'source_window': '08:00', 'route_id': 'exit' if i < exits else 'through',
            'original_destination_edge': '45740606' if i < exits else 'downstream',
            'depart': i * 4.3, 'sumo_vclass': 'passenger', 'speed_factor_assumed': .9 + i / 100}
            for i in range(20)])
        return agents, routes

    def test_turn_preserves_actual_agents_and_only_changes_the_fork(self):
        agents, routes = self.sample()
        shifted, rows = shift_late_turns(agents, routes, .15, 123, lambda e, c: True)
        self.assertEqual((shifted.route_id != agents.route_id).sum(), 3)
        self.assertEqual(rows[0]['through_after'] - rows[0]['through_before'], 3)
        for c in ['vehicle_id', 'depart', 'sumo_vclass', 'speed_factor_assumed', 'origin_edge', 'source_window']:
            self.assertTrue(shifted[c].equals(agents[c]))
        repeat, _ = shift_late_turns(agents, routes, .15, 123, lambda e, c: True)
        pd.testing.assert_frame_equal(repeat, shifted)

    def test_unavailable_donors_and_permissions_are_not_fabricated(self):
        agents, routes = self.sample(exits=1)
        shifted, rows = shift_late_turns(agents, routes, .15, 123, lambda e, c: True)
        self.assertEqual(rows[0]['actual_transfers'], 1)
        self.assertAlmostEqual(rows[0]['actual_shift'], .05)
        blocked, rows = shift_late_turns(agents, routes, .15, 123, lambda e, c: False)
        pd.testing.assert_frame_equal(blocked, agents)
        self.assertEqual(rows[0]['actual_transfers'], 0)

    def test_supply_edit_preserves_geometry_and_unrelated_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            root = ET.fromstring('<net><edge id="x"><lane id="x_0" speed="20" shape="0,0 1,1"/></edge>'
                '<tlLogic id="out" programID="boundary_supply_assumed"><phase duration="12" state="GG"/>'
                '<phase duration="3" state="yy"/><phase duration="45" state="rr"/></tlLogic>'
                '<tlLogic id="meter"><phase duration="30" state="G"/></tlLogic></net>')
            ET.ElementTree(root).write(directory / 'network.net.xml')
            meta = {'boundary_supply': dict(service_cycle(900, 2), node_id='out')}
            change_supply(directory, meta, 600)
            after = ET.parse(directory / 'network.net.xml').getroot()
            self.assertEqual(after.find('edge/lane').attrib, root.find('edge/lane').attrib)
            self.assertEqual(after.findall('tlLogic')[1].find('phase').attrib,
                             root.findall('tlLogic')[1].find('phase').attrib)
            self.assertAlmostEqual(sum(float(p.get('duration')) for p in after.find('tlLogic')), 60.)
            self.assertEqual(meta['residual_supply_vph_scenario'], 600)
            self.assertEqual([p.get('state') for p in after.find('tlLogic')], ['GG', 'yy', 'rr'])

    def test_plan_has_all_seven_conditions_and_eighty_four_runs(self):
        self.assertEqual(len(SCENARIOS) * len(SEEDS) * sum(map(len, ARMS.values())), 84)
        self.assertEqual(SEEDS, [20261029, 20261030, 20261031])
        for name, setting in SCENARIOS.items():
            changed = sum(setting[k] != SCENARIOS['reference'][k] for k in setting)
            self.assertEqual(changed, 0 if name == 'reference' else 1)


if __name__ == '__main__':
    unittest.main()
