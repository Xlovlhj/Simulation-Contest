"""Quantify missing spatial constraints in the existing static route estimator.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
Equal detector signatures do not imply equal dynamic traffic performance.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .paths import APP, INPUTS, REFINED
from .runner import digest


def main():
    output = APP / 'results/stage14_identifiability'
    output.mkdir(exist_ok=True)
    mapping = pd.read_csv(REFINED / 'detector_mapping.csv', dtype={'sumo_edge': str})
    core = set(json.loads((REFINED / 'corridor_definition.json').read_text(encoding='utf-8'))['core_edges'])
    mapping = mapping[mapping.sumo_edge.isin(core) & ~mapping.requires_review]
    results = []
    for case in ['ordinary', 'peak']:
        source = INPUTS / f'{case}_inferred_routes.csv'
        routes = pd.read_csv(source)
        sets = [set(edges.split()) for edges in routes.edges]
        a = np.array([[int(edge in route) for route in sets] for edge in mapping.sumo_edge], dtype=float)
        rank = int(np.linalg.matrix_rank(a))
        signatures = [''.join(str(int(v)) for v in a[:, i]) for i in range(len(routes))]
        table = routes[['route_id', 'estimated_vph']].copy()
        table['detector_signature'] = signatures
        table['source_edge'] = routes.edges.str.split().str[0]
        table['destination_edge'] = routes.edges.str.split().str[-1]
        table.to_csv(output / f'{case}_route_signatures.csv', index=False, encoding='utf-8-sig')
        witness = None
        for _, group in table.groupby('detector_signature'):
            if len(group) < 2 or not group.estimated_vph.gt(1e-4).any():
                continue
            i = group[group.estimated_vph.gt(1e-4)].index[0]
            j = next(k for k in group.index if k != i)
            original = routes.estimated_vph.to_numpy(dtype=float)
            changed = original.copy()
            moved = min(50., original[i] * .5)
            changed[i] -= moved
            changed[j] += moved
            assert (changed >= 0).all()
            residual = float(np.max(abs(a @ original - a @ changed)))
            assert residual < 1e-8 and np.isclose(original.sum(), changed.sum())
            witness = {'from_route': routes.iloc[i].route_id, 'to_route': routes.iloc[j].route_id,
                'transferred_vph': moved, 'maximum_station_rate_change_vph': residual,
                'meaning': 'Two different nonnegative route-rate vectors give identical STATIC detector predictions; not two verified dynamic simulations'}
            table['alternative_vph'] = changed
            table.to_csv(output / f'{case}_nonunique_witness.csv', index=False, encoding='utf-8-sig')
            break
        result = {'case': case, 'route_variables': len(routes), 'trusted_core_stations': len(mapping),
            'incidence_rank': rank, 'linear_nullity': len(routes) - rank,
            'distinct_station_signatures': len(set(signatures)),
            'routes_without_station_signature': int((a.sum(axis=0) == 0).sum()),
            'nonnegative_nonunique_witness': witness,
            'route_source_sha256': digest(source)}
        results.append(result)
    report = {'cases': results, 'mapping_sha256': digest(REFINED / 'detector_mapping.csv'),
        'script_sha256': digest(Path(__file__)),
        'scope': 'Maximum static spatial information with all trusted core stations; actual missing windows may weaken it further. Linear nullity is not the dimension of every nonnegative feasible set.',
        'implication': 'Regularization selects a prior-dependent estimate. More dates at the same locations do not independently observe missing turns, capacity or initial queues.'}
    (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False, indent=2))
