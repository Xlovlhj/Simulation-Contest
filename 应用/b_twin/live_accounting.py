"""Retain loaded-but-not-attempted vehicles at a fixed SUMO cutoff."""
# AI-assisted: OpenAI Codex; retain actual use and human review.
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from .runner import digest


def reconcile(planned_ids, trips, running, pending):
    ids=[t.get('id') for t in trips]
    if len(set(ids))!=len(ids) or set(ids)!=set(planned_ids):
        raise ValueError('Tripinfo does not contain exactly the planned vehicles')
    arrived=sum(float(t.get('arrival'))>=0 for t in trips)
    departed=sum(float(t.get('depart'))>=0 for t in trips)
    not_departed=len(trips)-departed
    not_attempted=not_departed-pending
    if not_attempted<0:raise ValueError('Pending exceeds all undeparted trips')
    residual=len(planned_ids)-arrived-running-pending-not_attempted
    return {'planned':len(planned_ids),'arrived':arrived,'departed':departed,
        'running_at_cutoff':running,'pending_at_cutoff':pending,
        'loaded_not_yet_attempted_at_cutoff':not_attempted,
        'unfinished_total_at_cutoff':running+pending+not_attempted,
        'tripinfo_rows':len(trips),'not_departed_tripinfo':not_departed,
        'not_in_tripinfo_ids':[],'conservation_residual':residual,
        'accounting_includes_loaded_not_yet_attempted':True}


def finalize_accounting(directory):
    """Repair only the summary; preserve the first file and simulation outputs."""
    import pandas as pd
    directory=Path(directory)
    path=directory/'operational_metrics.json'
    original=directory/'operational_metrics_initial.json'
    if not original.exists():original.write_bytes(path.read_bytes())
    metrics=json.loads(original.read_text(encoding='utf-8'))
    agents=pd.read_csv(directory/'vehicles.csv')
    trips=ET.parse(directory/'tripinfo.xml').getroot().findall('tripinfo')
    state=json.loads((directory/'states_60s.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    result={**metrics,**reconcile(agents.vehicle_id.tolist(),trips,state['running'],state['pending']),
        'initial_summary_residual_before_loaded_correction':metrics['conservation_residual']}
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    evidence={'initial_summary_sha256':digest(original),'corrected_summary_sha256':digest(path),
        'review_source_sha256':digest(Path(__file__)),'tripinfo_sha256':digest(directory/'tripinfo.xml'),
        'reason':'Include loaded, scheduled vehicles not yet attempted in the last half-second; no simulation rerun or traffic change.'}
    (directory/'accounting_review.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    (directory/'accounting_review_source.py').write_bytes(Path(__file__).read_bytes())
    return result
