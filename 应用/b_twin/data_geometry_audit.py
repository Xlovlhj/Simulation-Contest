"""Geographic feasibility checks; no provisional mapping is promoted to truth.

AI-assisted: OpenAI Codex; actual use and review must be disclosed.
"""
from __future__ import annotations

import gzip
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
from .paths import APP, DATA
from .runner import digest
from .road_emergency import definition
import sumolib

OUTPUT = APP / 'results/stage14_geometry_audit'


def nearest(point, shape):
    best = (math.inf, None)
    for a, b in zip(shape, shape[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        if not length:
            continue
        t = max(0., min(1., ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length))
        d = math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)
        if d < best[0]:
            best = d, math.degrees(math.atan2(dx, dy)) % 360
    return best


def angle(a, b):
    return abs((a - b + 180) % 360 - 180)


def interpolate(shape, fraction):
    lengths = [math.dist(a, b) for a, b in zip(shape, shape[1:])]
    target = sum(lengths) * fraction
    for (a, b), length in zip(zip(shape, shape[1:]), lengths):
        if target <= length and length:
            return (a[0] + (b[0] - a[0]) * target / length, a[1] + (b[1] - a[1]) * target / length)
        target -= length
    return shape[-1]


def main():
    OUTPUT.mkdir(exist_ok=True)
    core_path = APP / 'models/boundary_v3/corridor.net.xml'
    extended_path = APP / 'models/emergency_v1/corridor.net.xml'
    networks = {'98': sumolib.net.readNet(str(core_path)), '125': sumolib.net.readNet(str(extended_path))}
    net = networks['125']
    urban = sumolib.net.readNet(str(DATA / 'refined/urban.net.xml'))
    with gzip.open(DATA / 'processed/official_centerline.geojson.gz', 'rt', encoding='utf-8') as f:
        features = json.load(f)['features']
    official = []
    for f in features:
        xy = [net.convertLonLat2XY(*p[:2]) for p in f['geometry']['coordinates']]
        official.append((str(f['properties']['ROUTE_ID']), f['properties'], xy))
    by_id = {rid: (attrs, shape) for rid, attrs, shape in official}
    first = pd.read_csv(DATA / 'raw/aivas_20261018_source_audit/first_capture_normalized.csv', dtype={'segment': str})
    rows = []
    for r in first.itertuples():
        attrs, shape = by_id[r.segment]
        mid = interpolate(shape, .5)
        _, bearing = nearest(mid, shape)
        for network, n in networks.items():
            ranked = []
            for edge in n.getEdges():
                distance, direction = nearest(mid, edge.getShape())
                delta = angle(bearing, direction)
                if attrs['TRAVEL_DIRECTION'] == '1':
                    delta = min(delta, abs(180 - delta))
                matched = attrs['STREET_ENAME'].lower() in edge.getName().lower()
                samples = [interpolate(shape, x / 10) for x in range(11)]
                close = sum(nearest(p, edge.getShape())[0] <= 15 for p in samples) / 11
                ranked.append((distance + (0 if matched else 100) + delta * .3, edge, distance, delta, matched, close))
            _, edge, distance, delta, matched, close = min(ranked, key=lambda x: x[0])
            candidate = matched and delta <= 30 and close >= .8
            rows.append({'camera': r.camera, 'official_segment': r.segment, 'official_name': attrs['STREET_ENAME'],
                'official_elevation': attrs['ELEVATION'], 'travel_direction_code': attrs['TRAVEL_DIRECTION'],
                'network_edges': network, 'nearest_candidate_edge': edge.getID(), 'sumo_name': edge.getName(),
                'midpoint_distance_m': distance, 'local_direction_difference_deg': delta,
                'sample_fraction_within_15m': close, 'street_name_matches': matched,
                'geometric_candidate_only': candidate, 'ready_for_assimilation': False,
                'remaining_check': 'Exact video lane coverage and grade not independently confirmed; current data do not backfill past dates'})
    aivas = pd.DataFrame(rows)
    aivas.to_csv(OUTPUT / 'aivas_network_coverage.csv', index=False, encoding='utf-8-sig')
    mappings = pd.read_csv(DATA / 'refined/detector_mapping.csv', dtype={'sumo_edge': str})
    locations = pd.read_csv(DATA / 'processed/detector_locations.csv').set_index('AID_ID_Number')
    original = pd.read_csv(DATA / 'processed/detector_official_mapping.csv', dtype={'ROUTE_ID': str}).set_index('detector_id')
    reviews = []
    for r in mappings[mappings.requires_review].itertuples():
        loc = locations.loc[r.detector_id]
        point = urban.convertLonLat2XY(loc.Longitude, loc.Latitude)
        selected = urban.getEdge(r.sumo_edge)
        distance, heading = nearest(point, selected.getShape())
        attrs, shape = by_id[str(original.loc[r.detector_id, 'ROUTE_ID'])]
        offdist, offhead = nearest(point, shape)
        reviews.append({'detector_id': r.detector_id, 'selected_edge': r.sumo_edge, 'street': r.sumo_name,
            'source_description': loc.Road_EN, 'source_rotation': int(loc.Rotation),
            'sumo_local_heading_deg': heading, 'official_local_heading_deg': offhead,
            'sumo_official_local_angle_deg': angle(heading, offhead),
            'detector_to_sumo_m': distance, 'detector_to_official_m': offdist,
            'lane_count_sumo': r.sumo_lanes, 'lane_labels_observed': r.observed_lane_labels,
            'score_gap': r.score_gap, 'grade_conflict': r.grade_conflict,
            'requires_review_retained': True,
            'disposition': 'Geometry cross-checked; detector rotation semantics, exact lane coverage or competing parallel road still unresolved; not used in primary metrics'})
    pd.DataFrame(reviews).to_csv(OUTPUT / 'nine_mapping_reviews.csv', index=False, encoding='utf-8-sig')
    endpoints = []
    for label, d in definition()['coordinates'].items():
        point = net.convertLonLat2XY(d['longitude'], d['latitude'])
        ranked = []
        for rid, attrs, shape in official:
            dist, heading = nearest(point, shape)
            match = attrs['STREET_ENAME'].lower() in d['name'].lower()
            ranked.append((dist + (0 if match else 100), rid, attrs, dist))
        for rank, (_, rid, attrs, dist) in enumerate(sorted(ranked, key=lambda x: x[0])[:3], 1):
            endpoints.append({'endpoint': label, **d, 'rank': rank, 'official_route_id': rid,
                'official_street': attrs['STREET_ENAME'], 'official_elevation': attrs['ELEVATION'],
                'point_to_centerline_m': dist, 'lane_access_or_parking_verified': False})
    pd.DataFrame(endpoints).to_csv(OUTPUT / 'road_task_official_gis_check.csv', index=False, encoding='utf-8-sig')
    source_paths = [core_path, extended_path, DATA / 'processed/official_centerline.geojson.gz',
        DATA / 'refined/detector_mapping.csv', DATA / 'processed/detector_locations.csv',
        DATA / 'raw/aivas_20261018_source_audit/first_capture_normalized.csv', Path(__file__)]
    result = {'aivas_segments': int(first.segment.nunique()), 'networks_checked': [98, 125],
        'geometric_candidates_by_network': aivas.groupby('network_edges').geometric_candidate_only.sum().astype(int).to_dict(),
        'ready_for_assimilation': 0, 'review_mappings_rechecked': len(reviews), 'review_flags_cleared': 0,
        'road_task_endpoints_checked': 2,
        'source_sha256': {p.relative_to(APP.parent).as_posix(): digest(p) for p in source_paths},
        'limitations': 'Geometric candidates do not establish video coverage, capacity, exact detector lanes, stopping permission or actual emergency access'}
    save = OUTPUT / 'verification.json'
    save.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False, indent=2))
