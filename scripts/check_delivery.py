#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18.
"""Audit final source hashes, simulation outputs, browser package and deliverables."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED_PARTS={'.venv','.cache','.git','.aws','.codex','.agents','__pycache__'}
REQUIRED_SUPPORT_FILES={
    'project.py','README.md','requirements-sumo.txt','requirements-project.txt',
    'scripts/check_delivery.py','scripts/build_project.py','scripts/reproduce_project.py',
    'scripts/finalize_data.py','scripts/prepare_calibration.py','scripts/validate_twin.py',
    'scripts/run_final_experiments.py','scripts/run_baseline.py','scripts/run_scenarios.py',
    'scripts/replay_baidu.py','scripts/audit_fairness.py','src/app_template.html',
    'config/final_experiments.json','config/final_validation.json','config/baseline.json',
    'data/processed/baidu/route_catalog_v1.json','data/processed/baidu/survey_routes.json',
    'data/processed/final/data_manifest.json','data/processed/final/samples.csv',
    'outputs/final_experiments/summary.json','outputs/validation/summary.json',
    'outputs/app/index.html','outputs/app/observations.html','outputs/app/app_manifest.json',
    'outputs/app/paper.pdf','outputs/app/paper.docx','outputs/app/ai_usage.pdf',
    'outputs/paper/ai_usage.pdf','outputs/quality/reproduction_audit.json',
    'outputs/quality/fairness_audit.json','outputs/quality/fairness_audit.csv',
    'outputs/quality/fairness_audit.md','submission/SUBMISSION_CHECKLIST.md',
}


def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def require(condition,message):
    if not condition:raise ValueError(message)


def safe_member(name):
    require(isinstance(name,str) and bool(name),'Invalid support member name')
    value=PurePosixPath(name)
    require(not value.is_absolute() and '\\' not in name and ':' not in name and
            '..' not in value.parts and str(value)==name,'Unsafe support member path: '+name)
    require(not name.endswith('.local.json') and not EXCLUDED_PARTS.intersection(value.parts),
            'Excluded content in support: '+name)
    return name


def manifest_items(manifest):
    require(isinstance(manifest,dict) and isinstance(manifest.get('files'),list),'Support manifest missing file list')
    items={}
    for item in manifest['files']:
        require(isinstance(item,dict),'Invalid support manifest entry')
        name=safe_member(item.get('path'))
        require(name!='SUPPORT_MANIFEST.json' and name not in items,'Duplicate/self-referencing manifest member: '+name)
        require(type(item.get('bytes')) is int and item['bytes']>=0,'Invalid manifest byte count: '+name)
        require(isinstance(item.get('sha256'),str) and re.fullmatch('[0-9a-f]{64}',item['sha256']) is not None,
                'Invalid manifest SHA-256: '+name)
        items[name]=item
    require(type(manifest.get('file_count')) is int and manifest['file_count']==len(items),'Manifest file count mismatch')
    require(REQUIRED_SUPPORT_FILES.issubset(items),'Required support files missing: '+', '.join(sorted(REQUIRED_SUPPORT_FILES-set(items))))
    return items


def verify_extracted_support(root):
    root=Path(root).resolve()
    manifest=read(root/'SUPPORT_MANIFEST.json')
    items=manifest_items(manifest)
    for name,item in items.items():
        target=(root/name).resolve()
        require(target.is_relative_to(root) and target.is_file() and not (root/name).is_symlink(),
                'Packaged file missing/outside: '+name)
        require(target.stat().st_size==item['bytes'] and digest(target)==item['sha256'],
                'Packaged file changed: '+name)
    return {'files':len(items),'required_files_checked':len(REQUIRED_SUPPORT_FILES)}


def verify_support_archive(archive,external_manifest,root):
    """Check exact membership and bytes against both internal and external seals."""
    archive,external_manifest,root=Path(archive),Path(external_manifest),Path(root).resolve()
    external=read(external_manifest)
    require(archive.stat().st_size<=20*1024*1024,'Support package exceeds 20MB')
    require(external.get('archive_bytes')==archive.stat().st_size and external.get('archive_sha256')==digest(archive),
            'Archive bytes/SHA-256 differ from external support_manifest.json')
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();names=[info.filename for info in infos]
        require(len(names)==len(set(names)),'Duplicate ZIP member names')
        for info in infos:
            safe_member(info.filename)
            require(not info.is_dir() and not stat.S_ISLNK(info.external_attr>>16),'Directory/symlink ZIP member rejected')
        require('SUPPORT_MANIFEST.json' in names,'ZIP has no SUPPORT_MANIFEST.json')
        internal=json.loads(z.read('SUPPORT_MANIFEST.json'))
        items=manifest_items(internal)
        require(set(names)==set(items)|{'SUPPORT_MANIFEST.json'},'ZIP members differ from declared manifest')
        require({k:v for k,v in external.items() if k not in ('archive_bytes','archive_sha256')}==internal,
                'Internal and external support manifests differ')
        for name,item in items.items():
            payload=z.read(name)
            require(len(payload)==item['bytes'] and hashlib.sha256(payload).hexdigest()==item['sha256'],
                    'ZIP member byte count/SHA-256 mismatch: '+name)
            current=(root/name).resolve()
            require(current.is_relative_to(root) and current.is_file() and not (root/name).is_symlink(),
                    'Current packaged source missing/outside: '+name)
            require(current.stat().st_size==item['bytes'] and digest(current)==item['sha256'],
                    'Archive differs from current delivery source: '+name)
        require(z.testzip() is None,'Archive CRC failed')
    return {'files':len(items),'required_files_checked':len(REQUIRED_SUPPORT_FILES),
            'archive_sha256':external['archive_sha256'],'exact_membership':True,
            'internal_external_manifests_identical':True,'current_source_bytes_identical':True}


def main():
    support=ROOT/'SUPPORT_MANIFEST.json'
    if support.exists():
        integrity=verify_extracted_support(ROOT)
        print(json.dumps({'status':'support_integrity_passed',**integrity,
            'note':'大型原始仿真XML有意不放入支撑包；python3 project.py reproduce 将从零重算并比较数值。'},ensure_ascii=False))
        return 0
    checked=0
    data=read(ROOT/'data/processed/final/data_manifest.json')
    for item in data['source_files']:
        require(digest(ROOT/item['path'])==item['sha256'],'Frozen source changed: '+item['path']);checked+=1
    require(digest(ROOT/'data/processed/final/samples.csv')==data['samples_sha256'],'Frozen samples changed')
    runs=read(ROOT/'outputs/final_experiments/summary.json')
    require(runs['status']=='completed' and len(runs['runs'])==22,'Expected 22 completed runs')
    for row in runs['runs']:
        folder=ROOT/row['folder'];manifest=read(folder/'run_manifest.json')
        require(digest(folder/'run_manifest.json')==row['run_manifest_sha256'],'Run manifest changed')
        require(row['physics_valid'] and row['arrived']==row['planned'] and row['unfinished_total']==0,'Run not physically complete')
        require(all(row[k]==0 for k in ('collisions','teleports','discarded')),'Invalid physical event')
        for name,expected in manifest['artifacts'].items():
            require(digest(folder/name)==expected,'Run output changed: '+str(folder/name));checked+=1
    for path in ('outputs/final_experiments/manifest.json','outputs/app/app_manifest.json'):
        p=ROOT/path;m=read(p)
        for name,expected in m['artifacts'].items():
            target=ROOT/name if name.startswith('outputs/') else p.parent/name
            require(digest(target)==expected,'Artifact changed: '+str(target));checked+=1
        for name,expected in m.get('inputs_sha256',{}).items():
            require(digest(ROOT/name)==expected,'Current source differs from frozen input: '+name)
    validation=read(ROOT/'outputs/validation/summary.json')
    for item in validation['artifacts'].values():
        require(digest(ROOT/item['path'])==item['sha256'],'Validation artifact changed');checked+=1
    require(validation['model_refitted_during_evaluation'] is False,'Holdout fitting detected')
    reproduction=read(ROOT/'outputs/quality/reproduction_audit.json')
    require(reproduction['status']=='passed' and reproduction['simulation_numeric_results_identical'],'Fresh reproduction did not pass')
    for p in (ROOT/'outputs/paper/paper.pdf',ROOT/'outputs/paper/ai_usage.pdf'):
        require(p.read_bytes().startswith(b'%PDF-') and p.stat().st_size<=20*1024*1024,'Invalid/oversized PDF: '+p.name)
    paper=read(ROOT/'outputs/paper/build_manifest.json')
    for name,expected in paper['source_sha256'].items():
        require(digest(ROOT/name)==expected,'Paper source changed: '+name);checked+=1
    for name,item in paper['artifacts'].items():
        p=ROOT/'outputs/paper'/name
        require(digest(p)==item['sha256'] and p.stat().st_size==item['bytes'],'Paper artifact changed: '+name);checked+=1
    smoke=read(ROOT/'outputs/quality/app_smoke_audit.json')
    require(smoke['status']=='passed','Actual application smoke test did not pass')
    for name,expected in smoke['source_sha256'].items():
        require(digest(ROOT/name)==expected,'Application changed after smoke test: '+name);checked+=1
    privacy=read(ROOT/'outputs/quality/document_privacy_audit.json')
    require(privacy['status']=='passed','Document privacy scan did not pass')
    for name,expected in privacy['documents'].items():
        require(digest(ROOT/name)==expected,'Document changed after privacy scan: '+name);checked+=1
    video=ROOT/'outputs/video/B_demo.mp4';vm=read(ROOT/'outputs/video/video_manifest.json')
    require(video.stat().st_size<=300*1024*1024 and digest(video)==vm['video_sha256'],'Video changed/oversized')
    require(vm['duration_s']<=180 and vm['width']>=1280 and vm['height']>=720,'Video format requirements not met')
    require(vm['app_sha256']==digest(ROOT/'outputs/app/index.html'),'Video does not match final app')
    for original,copy in (('outputs/paper/paper.pdf','submission/B_paper.pdf'),
                          ('outputs/paper/paper.docx','submission/B_paper.docx'),
                          ('outputs/paper/ai_usage.pdf','submission/B_ai_usage.pdf'),
                          ('outputs/video/B_demo.mp4','submission/B_demo.mp4')):
        require(digest(ROOT/original)==digest(ROOT/copy),'Submission copy differs: '+copy)
    import imageio_ffmpeg
    frames=imageio_ffmpeg.read_frames(str(video))
    metadata=next(frames);frames.close()
    require(metadata['size'][0]>=1280 and metadata['size'][1]>=720 and metadata['duration']<=180.1,'Actual video stream dimensions/duration invalid')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(video),'-f','null','-'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,check=True)
    archive=ROOT/'submission/B_support.zip'
    support_audit=verify_support_archive(archive,ROOT/'submission/support_manifest.json',ROOT)
    private=ROOT/'config/baidu.local.json';secrets=[]
    if private.exists():secrets=[v.encode() for v in read(private).values() if isinstance(v,str) and len(v)>16]
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None,'Archive CRC failed')
        for name in z.namelist():
            require(not name.endswith('.local.json') and not any(x in Path(name).parts for x in ('.venv','.cache','.git','.aws','.codex','.agents')),'Excluded content in support')
            payload=z.read(name);require(not any(secret in payload for secret in secrets),'Private value in package: '+name)
    report={'status':'passed','checked_at':datetime.now(timezone.utc).isoformat(),'verified_artifact_hashes':checked,
        'completed_simulation_runs':len(runs['runs']),'fresh_reproduction':'passed','video_actual_metadata':metadata,
        'support_integrity':support_audit,
        'support_bytes':archive.stat().st_size,'video_bytes':video.stat().st_size,
        'deliverables':{str(p.relative_to(ROOT)):{'sha256':digest(p),'bytes':p.stat().st_size} for p in (ROOT/'outputs/paper/paper.pdf',ROOT/'outputs/paper/paper.docx',ROOT/'outputs/paper/ai_usage.pdf',video,archive)},
        'manual_actions_remaining':['队员按赛方AI规定独立完成必要核心工作并实质复核','确认队伍号及正式命名','核对最终字体并签署独立承诺书','由用户在官方系统完成提交'],
        'scientific_scope':'Conditional hypothetical simulation effects; cross-day ETA error is reported and is not a claim of successful field calibration.'}
    (ROOT/'outputs/quality/delivery_check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'passed','artifact_hashes':checked,'runs':22,'video_duration_s':metadata['duration'],'support_bytes':archive.stat().st_size},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
