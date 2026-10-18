#!/usr/bin/env python3
"""Render the V2 manuscript without changing V1 or frozen scientific outputs.

AI assistance: OpenAI Codex, 2026-10-18. Actual model snapshot is not recorded.
Run: python3 scripts/build_paper_v2.py (uses the existing workspace environment).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if sys.prefix != str(ROOT / '.venv'):
    interpreter = ROOT / '.venv/bin/python'
    if interpreter.exists():
        os.execv(str(interpreter), [str(interpreter), '-B', str(Path(__file__).resolve()), *sys.argv[1:]])

import build_paper as layout

SOURCE = ROOT / 'manuscript/v2'
OUT = ROOT / 'outputs/paper_v2'
TITLE = '观测约束下的街区交通数字孪生标定与治理适用边界研究'
FIGURES = {
    'NETWORK_FIGURE': ('network_v2.png', '图1 百度GIS子图、候选路径与完整扰动路段。AI辅助绘制；空间位置来自实际路线，事件为仿真设计。'),
    'VALIDATION_FIGURE': ('calibration_v2.png', '图2 直接SUMO时间与平台训练中位及时间留出误差。AI辅助统计；平台ETA不是逐车实测。'),
    'GOVERNANCE_FIGURE': ('governance_v2.png', '图3 通过训练筛选的四组B相对A耗时变化。AI辅助统计；正值表示增加，单位按各面板标注。'),
    'TIMESERIES_FIGURE': ('timeseries_v2.png', '图4 30秒需求档较强事件的实际仿真时序。AI辅助统计FCD与源端队列；横轴为模拟秒。'),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def install_reference_links(refs):
    """Keep long signed-free public URLs clickable without overflowing the page."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from urllib.parse import urlsplit
    original = layout.paragraph
    by_id = {str(r['id']): r for r in refs}

    def paragraph(document, text, heading=0, centered=False):
        if text.startswith('关键词：'):
            text = '关键词：交通数字孪生；仿真反演；参数来源；路径引导；源端协调'
        p = original(document, text, heading, centered)
        match = re.match(r'^\[(\d+)\]', text)
        ref = by_id.get(match.group(1)) if match else None
        if ref and ref.get('url'):
            url = ref['url']
            link = OxmlElement('w:hyperlink')
            link.set(qn('r:id'), p.part.relate_to(url, RT.HYPERLINK, is_external=True))
            run = OxmlElement('w:r')
            props = OxmlElement('w:rPr')
            fonts = OxmlElement('w:rFonts')
            fonts.set(qn('w:ascii'), 'Times New Roman')
            fonts.set(qn('w:hAnsi'), 'Times New Roman')
            fonts.set(qn('w:eastAsia'), '宋体')
            props.append(fonts)
            size = OxmlElement('w:sz'); size.set(qn('w:val'), '24'); props.append(size)
            run.append(props)
            value = OxmlElement('w:t'); value.text = ' 在线原文：' + urlsplit(url).netloc
            value.set(qn('xml:space'), 'preserve'); run.append(value)
            link.append(run); p._p.append(link)
        return p
    layout.paragraph = paragraph


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    runtime = OUT / '.runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    os.environ['XDG_RUNTIME_DIR'] = str(runtime)
    os.environ['SAL_USE_VCLPLUGIN'] = 'svp'
    source = (SOURCE / 'paper_core.md').read_text(encoding='utf-8')
    abstract = (SOURCE / 'abstract.md').read_text(encoding='utf-8').strip()
    refs = read_json(SOURCE / 'references.json')
    for marker, (filename, caption) in FIGURES.items():
        figure = OUT / 'figures' / filename
        if not figure.is_file():
            raise ValueError('缺少真实数据生成图：' + filename)
        source = source.replace('{{' + marker + '}}', f'![{caption}]({figure.relative_to(ROOT)})')
    source = source.replace('{{REFERENCES}}', '\n\n'.join(f"[{r['id']}] {r['text']}" for r in refs))
    if re.search(r'\{\{[^}]+\}\}', source):
        raise ValueError('稿源仍含未填占位符')

    # Validate the precise frozen inputs used by the paper, without rerunning science.
    checked = {}
    for relative in ('outputs/governance_v2/manifest.json', 'outputs/identification_v2/calibration/summary_manifest.json'):
        manifest_path = ROOT / relative
        manifest = read_json(manifest_path)
        for field in ('input_hashes', 'source_sha256'):
            for name, expected in manifest.get(field, {}).items():
                path = ROOT / name
                if sha(path) != expected:
                    raise ValueError('冻结来源变化：' + name)
                checked[name] = expected
        for name, expected in manifest.get('artifacts', {}).items():
            path = manifest_path.parent / name
            if sha(path) != expected:
                raise ValueError('冻结结果变化：' + str(path.relative_to(ROOT)))
            checked[str(path.relative_to(ROOT))] = expected
    for path in (SOURCE / 'paper_core.md', SOURCE / 'abstract.md', SOURCE / 'references.json',
                 SOURCE / 'ai_usage.md', Path(__file__), ROOT / 'scripts/build_paper.py',
                 ROOT / 'scripts/build_paper_figures_v2.py', OUT / 'figures/manifest.json'):
        checked[str(path.relative_to(ROOT))] = sha(path)
    (SOURCE / 'paper_source.md').write_text(source + '\n', encoding='utf-8')
    layout.OUT, layout.SOURCE, layout.TITLE = OUT, SOURCE, TITLE
    install_reference_links(refs)
    layout.create_paper(source, abstract, OUT / 'paper.docx')
    pdf = layout.convert_pdf(OUT / 'paper.docx')
    headings = re.findall(r'^# (.+)$', source, re.M)
    toc = layout.locate_headings(layout.pdf_pages(pdf), headings)
    layout.create_paper(source, abstract, OUT / 'paper.docx', toc)
    pdf = layout.convert_pdf(OUT / 'paper.docx')
    pages = layout.pdf_pages(pdf)
    if layout.locate_headings(pages, headings) != toc:
        raise ValueError('目录页码未稳定')
    if '目 录' not in pages[1] and '目录' not in re.sub(r'\s+', '', pages[1]):
        raise ValueError('摘要超过一页，须调整排版')
    ai = (SOURCE / 'ai_usage.md').read_text(encoding='utf-8')
    layout.create_ai_document(ai, OUT / 'ai_usage.docx')
    ai_pdf = layout.convert_pdf(OUT / 'ai_usage.docx')
    if any(p.stat().st_size > 20_000_000 for p in (pdf, ai_pdf)):
        raise ValueError('PDF超过20 MB')
    (OUT / 'paper_extracted.txt').write_text('\f'.join(pages), encoding='utf-8')
    fonts = subprocess.run(['pdffonts', str(pdf)], capture_output=True, text=True, check=True).stdout
    manifest = {
        'version': 'V2', 'generated_at': datetime.now(timezone.utc).isoformat(),
        'title': TITLE, 'paper_pages': len(pages), 'ai_pages': len(layout.pdf_pages(ai_pdf)),
        'heading_pages': toc, 'human_review_status': 'not_recorded_complete',
        'font_status': 'DOCX指定宋体/黑体；PDF使用本机已有Noto等替代字体，正式提交前需在合法规定字体环境重导出。',
        'actual_pdf_fonts': fonts, 'source_sha256': checked,
        'artifacts': {str(p.relative_to(OUT)): {'sha256': sha(p), 'bytes': p.stat().st_size}
                      for p in (OUT / 'paper.docx', pdf, OUT / 'ai_usage.docx', ai_pdf,
                                OUT / 'paper_extracted.txt', *sorted((OUT / 'figures').glob('*.png')))},
    }
    (OUT / 'build_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'paper': str(pdf.relative_to(ROOT)), 'pages': len(pages), 'ai_pages': manifest['ai_pages'],
                      'frozen_inputs_checked': len(checked), 'font_review_required': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
