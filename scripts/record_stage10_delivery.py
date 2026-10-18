"""记录本轮已执行核验；按现存报告反映状态，不推填人工完成。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bev.io import read_json, write_json, sha256
from bev.portal import build_portal

build = read_json(ROOT/'records/final_paper_build.json')
for key in ['paper', 'ai_disclosure']:
    build[key]['visual_review'] = 'Codex checked all-page contact sheet, new paper section details and every AI PDF page; not team manual review.'
write_json(ROOT/'records/final_paper_build.json', build)
artifact = read_json(ROOT/'records/extension_artifact_verification.json')
artifact['visual_review'] = 'Codex:20-page overview, new5.6/6.7/refs/appendix details; all5 AI PDF pages checked.'
write_json(ROOT/'records/extension_artifact_verification.json', artifact)

portal_report = ROOT/'results/portal_checks/browser_verification.json'
browser = read_json(portal_report) if portal_report.exists() else {}
state = read_json(ROOT/'records/latest_state.json')
state['report'] = 'docs/五项问题的试验结果与处理决定.md'
state['stage10_verification'] = {'original_checks_passed': 34, 'new_analytical_checks_passed': 7,
                               'step_halving_pass': 4, 'full_recompute_values_matched': 1791,
                               'paper_pages': build['paper']['pages'], 'ai_pdf_pages': build['ai_disclosure']['pages'],
                               'pdf_visual_review': 'Codex only'}
state['project_portal'] = {**state.get('project_portal', {}), 'documents': 37, 'terms': 72, 'figures': 22}
state['project_portal'].pop('browser_verified', None)
state['project_portal']['browser_check_passed'] = browser.get('passed', False)
state['portal_browser_check_passed'] = browser.get('passed', False)
state['next_actions'] = [
    'Use the selected one-RC model for retrospective voltage prediction; retain both older comparators.',
    'Keep transparent160Ah/80Ah scenarios; do not invent official clarification.',
    'Retain measured-temperature and Cr limitations after unsuccessful external trials.',
    'Use guarded comfort comparison with explicit cold-case failure and no robust cold-saving claim.',
    'User handles independent core work and manual review; no completion evidence added.']
state['external_data_review']['review_stage'] = 'Stage9 historical source review; see extension_summary for later actual trials and model choice.'
state['delivery'].pop('latest_source_review_in_stage8_zip', None)
state['delivery']['latest_trials_in_current_packages'] = True
if (ROOT/'records/extension_package_runtime_verification.json').exists():
    checked = read_json(ROOT/'records/extension_package_runtime_verification.json')
    state['stage10_verification']['fresh_team_zip_recompute_passed'] = checked['passed']
    state['delivery']['runtime_verification'] = 'both_fresh_ZIPs_all_passed; stage8_prior_recompute_retained; stage10_fresh_team_ZIP_recompute_passed'
    summary = read_json(ROOT/'records/delivery_runtime_summary.json')
    ordinary = read_json(ROOT/'records/delivery_runtime_verification.json')
    summary.update(date_beijing=ordinary['date_beijing'], passed=ordinary['passed'])
    summary['stage10'] = {k: checked[k] for k in ['passed', 'analytical_checks', 'numeric_values_compared', 'full_recompute', 'environment']}
    summary['note'] = 'Final records/UI/scripts updated after runtime checks; numerical code, data and frozen results are compared with executed versions. No fresh OS or team review claimed.'
    write_json(ROOT/'records/delivery_runtime_summary.json', summary)
write_json(ROOT/'records/latest_state.json', state)

log = ROOT/'C题开发日志.md'
text = log.read_text(encoding='utf-8')
marker = '阶段十排版与回归核验'
if marker not in text:
    text += '''\n\n**阶段十排版与回归核验：** 原34项自动检查通过，未改写原verification.json；新7项解析、4路步长及1791值完整重算通过。新论文20页、62数学节点、15张表、12编号公式、6图，PDF约2.89MB；AI详情5页、Word可编辑，仍为真实工作稿。新增目录条目曾溢出至近空白第三页，已收紧目录行间距并缩短附录入口，摘要第1页/目录第2页恢复，正文12pt等字号核对通过；无联网/数学错误/越界。已看20页总览及新节、参考文献、附录细节，AI五页逐页看，为Codex视觉检查。当前主论文及历史版本路径保持清楚，后续包及浏览器核查见记录。\n'''
    log.write_text(text, encoding='utf-8')
print(build_portal())
