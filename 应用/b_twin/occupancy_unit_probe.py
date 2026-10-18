"""Measure local API units against one car of known length, independently of control.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
"""
import argparse
from pathlib import Path
import json
import subprocess
import xml.etree.ElementTree as ET
from .paths import sumo_exe
from .runner import digest
import sumolib
import traci


def probe(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = subprocess.run([sumo_exe('netgenerate'), '--grid', '--grid.number', '2', '--grid.length', '100',
        '--default.lanenumber', '1', '--output-file', str(output / 'net.xml')], capture_output=True, check=True)
    (output / 'build.log').write_bytes(result.stdout + result.stderr)
    net = sumolib.net.readNet(str(output / 'net.xml'))
    edge = next(e for e in net.getEdges() if e.allows('passenger'))
    lane = edge.getLanes()[0]
    (output / 'routes.xml').write_text(f'<routes><vType id="p" length="5" minGap="2.5"/>'
        f'<route id="r" edges="{edge.getID()}"/><vehicle id="one" type="p" route="r" depart="0" departPos="50" departSpeed="0">'
        f'<stop lane="{lane.getID()}" endPos="50" until="100"/></vehicle></routes>', encoding='utf-8')
    (output / 'detector.add.xml').write_text(f'<additional><inductionLoop id="loop" lane="{lane.getID()}" pos="48" freq="10" file="e1.xml"/></additional>', encoding='utf-8')
    traci.start([sumo_exe(), '-n', str(output / 'net.xml'), '-r', str(output / 'routes.xml'),
        '-a', str(output / 'detector.add.xml'), '--no-step-log', 'true'], label='occupancy_unit_probe')
    c = traci.getConnection('occupancy_unit_probe')
    try:
        c.simulationStep(10)
        expected = c.vehicle.getLength('one') / c.lane.getLength(lane.getID())
        data = {'sumo_version': c.getVersion(), 'vehicles': c.lane.getLastStepVehicleNumber(lane.getID()),
            'vehicle_length_m': c.vehicle.getLength('one'), 'lane_length_m': c.lane.getLength(lane.getID()),
            'geometric_occupied_fraction': expected, 'edge_api_raw': c.edge.getLastStepOccupancy(edge.getID()),
            'lane_api_raw': c.lane.getLastStepOccupancy(lane.getID()),
            'e1_api_percent': c.inductionloop.getLastStepOccupancy('loop')}
        assert data['vehicles'] == 1
        assert abs(data['edge_api_raw'] - expected) < 1e-8
        assert abs(data['lane_api_raw'] - expected) < 1e-8
        assert abs(data['e1_api_percent'] - 100) < 1e-8
    finally:
        c.close()
    data['e1_xml_percent'] = float(ET.parse(output / 'e1.xml').getroot().find('interval').get('occupancy'))
    assert data['e1_xml_percent'] == 100
    data.update(edge_to_percent_factor=100, e1_xml_to_percent_factor=1,
        scope='Local SUMO 1.27.1 unit check; spatial road occupancy and temporal point occupancy are different measures.',
        source_sha256=digest(Path(__file__)))
    (output / 'verification.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True, type=Path)
    print(json.dumps(probe(p.parse_args().output), ensure_ascii=False, indent=2))
