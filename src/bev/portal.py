"""从原文档和冻结结果构建离线项目工作台；不重新计算或修改物理结果。"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit

import markdown

from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json

OUTPUT_NAME = "C题项目管理.html"
PORTAL = PROJECT_ROOT / "portal"
PREFERRED = [
    ("docs/提交准备与文件清单.md", "提交 · 最终稿与文件"),
    ("docs/三问任务与成果速览.md", "三问 · 任务与成果"),
    ("docs/论文/最终稿核查与提交状态.md", "定稿 · 核查与状态"),
    ("docs/问题修复与结果说明.md", "修复 · 结果与未解项"),
    ("results/阶段八进展.md", "阶段八 · 问题修复"),
    ("docs/交付与复现说明.md", "交付 · 同学先读"),
    ("docs/论文/C题论文_技术定稿.md", "论文 · 技术定稿"),
    ("docs/论文/最终版修订说明.md", "论文 · 定稿说明"),
    ("docs/AI工具使用详情.md", "AI · 使用详情"),
    ("docs/参考文献与支撑材料核查.md", "材料 · 核查记录"),
    ("docs/论文/论文审阅与定稿说明.md", "论文 · 先读这里"),
    ("docs/论文/C题论文审阅初稿.md", "论文 · 审阅初稿"),
    ("docs/论文/三问证据总表.md", "论文 · 证据总表"),
    ("docs/正式视频分镜与讲解稿.md", "视频 · 分镜与讲解"),
    ("docs/C题项目执行方案_精简版.md", "执行计划 · 精简"),
    ("docs/C题项目执行方案.md", "执行计划 · 完整"),
    ("docs/C题建模原理说明.md", "建模原理"),
    ("docs/节能控制原理与使用说明.md", "控制原理"),
    ("docs/连续行驶与热恢复通俗说明.md", "热恢复与测温"),
    ("docs/参数可靠性与非线性区间通俗说明.md", "参数可靠性 · 人话版"),
    ("docs/C题-题意逐句翻译.md", "赛题解读"),
    ("docs/CLTC来源与核验.md", "CLTC来源"),
    ("docs/数据字典.md", "数据字典"),
    ("docs/项目工作台使用说明.md", "工作台使用说明"),
    ("results/阶段七进展.md", "阶段七 · 论文与交接"),
    ("results/阶段六进展.md", "阶段六 · 论文与证据"),
    ("results/阶段五进展.md", "阶段五 · 参数可靠性"),
    ("results/阶段四进展.md", "阶段四 · 连续与恢复"),
    ("results/阶段三进展.md", "阶段三 · 控制"),
    ("results/阶段二进展.md", "阶段二 · 校准"),
    ("results/阶段一进展.md", "阶段一 · 基准"),
    ("C题开发日志.md", "开发日志"),
    ("records/decisions.md", "关键决定"),
    ("records/assumptions.md", "假设与来源"),
    ("records/ai_usage.md", "AI工作记录"),
    ("README.md", "运行说明"),
]


def page_id(path: str) -> str:
    return "doc-" + hashlib.sha256(path.encode()).hexdigest()[:12]


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


class LinkRewriter(HTMLParser):
    """将文档链接改为内部页签，并嵌入图片；不依赖file://的fetch权限。"""
    def __init__(self, source: Path, paths: dict[str, str]):
        super().__init__(convert_charrefs=False)
        self.source, self.paths, self.output = source, paths, []

    def handle_starttag(self, tag, attrs):
        result = dict(attrs)
        attr = "href" if tag == "a" else "src" if tag == "img" else None
        if attr and result.get(attr):
            original = result[attr]
            parsed = urlsplit(original)
            if parsed.scheme in {"https", "http"}:
                if tag == "a":
                    result.update(target="_blank", rel="noopener noreferrer")
            elif not parsed.scheme and not original.startswith("#"):
                absolute = (self.source.parent / unquote(parsed.path)).resolve()
                if absolute.is_relative_to(PROJECT_ROOT):
                    rel = absolute.relative_to(PROJECT_ROOT).as_posix()
                    if tag == "img" and absolute.is_file():
                        result[attr] = data_url(absolute)
                    elif tag == "a" and (rel in self.paths or rel == OUTPUT_NAME):
                        destination = self.paths.get(rel, "home")
                        result[attr] = "#p=" + destination
                        result["data-page"] = destination
                        if parsed.fragment:
                            result["data-anchor"] = unquote(parsed.fragment)
                    else:
                        result[attr] = quote(rel, safe="/") + ("#"+parsed.fragment if parsed.fragment else "")
                        if tag == "a":
                            result.update(target="_blank", rel="noopener")
        if tag == "img":
            result["loading"] = "lazy"
        attrs_html = "".join(" "+key if value is None else f' {key}="{html.escape(value, quote=True)}"' for key, value in result.items())
        self.output.append(f"<{tag}{attrs_html}>")

    def handle_endtag(self, tag): self.output.append(f"</{tag}>")
    def handle_data(self, data): self.output.append(data)
    def handle_entityref(self, name): self.output.append(f"&{name};")
    def handle_charref(self, name): self.output.append(f"&#{name};")
    def handle_comment(self, data): self.output.append(f"<!--{data}-->")


def render_markdown(text: str, source: Path, paths: dict[str, str]) -> tuple[str, str]:
    # 先保护公式，以免Markdown把下标、反斜杠解释成普通标记。
    maths = []
    pattern = re.compile(r"(```[\s\S]*?```)|(\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|(?<!\$)\$(?!\$)[^\n$]+\$)")
    def protect(match):
        if match[1]:
            return match[0]
        value = match[0]
        display = value.startswith(("$$", "\\["))
        tex = value[2:-2] if value.startswith(("$$", "\\[", "\\(")) else value[1:-1]
        token = f"PORTALMATHTOKEN{len(maths)}END"
        if display:
            maths.append(f'<div class="math-display">\\[{html.escape(tex)}\\]</div>')
            return "\n\n"+token+"\n\n"
        maths.append(f'<span class="math-inline">\\({html.escape(tex)}\\)</span>')
        return token
    prepared = pattern.sub(protect, text)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"], extension_configs={"toc": {"permalink": False}})
    rendered = md.convert(prepared)
    for i, replacement in enumerate(maths):
        token = f"PORTALMATHTOKEN{i}END"
        rendered = rendered.replace("<p>"+token+"</p>", replacement).replace(token, replacement)
    rewrite = LinkRewriter(source, paths)
    rewrite.feed(rendered)
    return "".join(rewrite.output), md.toc


def discover_documents() -> list[tuple[Path, str, str]]:
    preferred = {path: title for path, title in PREFERRED}
    files = [PROJECT_ROOT / path for path, _ in PREFERRED if (PROJECT_ROOT / path).exists()]
    for folder in ["docs", "results", "records"]:
        candidates = (PROJECT_ROOT / folder).rglob("*.md") if folder == "docs" else (PROJECT_ROOT / folder).glob("*.md")
        files.extend(p for p in sorted(candidates) if p not in files)
    answer = []
    for path in files:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        group = "方案与原理" if rel.startswith("docs/") else "结果与证据" if rel.startswith("results/") else "项目记录"
        title = preferred.get(rel) or re.sub(r"^#\s*", "", path.read_text(encoding="utf-8").splitlines()[0]).strip() or path.stem
        answer.append((path, title, group))
    return answer


def resolved_terms() -> list[dict]:
    terms = read_json(PORTAL / "terms.json")
    reference = read_json(PROJECT_ROOT / "configs/reference.json")
    calibrated = read_json(PROJECT_ROOT / "configs/calibrated.json")
    rule = read_json(PROJECT_ROOT / "configs/optimized_rule.json")
    ids = set()
    for term in terms:
        if term["id"] in ids:
            raise ValueError("Duplicate term ID: "+term["id"])
        ids.add(term["id"])
        values = []
        def fmt(value):
            return f"{value:.6g}" if isinstance(value, (int, float)) else str(value)
        if "key" in term:
            key = term["key"]
            ref, fitted = reference.get(key), calibrated.get(key)
            if ref is not None:
                values.append({"label": "参考值" if ref != fitted else "采用值", "value": fmt(ref)})
            if fitted is not None and ref != fitted:
                values.append({"label": "暂定校准", "value": fmt(fitted)})
        if "rule_key" in term:
            values.append({"label": "优化名义值", "value": fmt(rule[term["rule_key"]])})
        if "value" in term:
            values.append({"label": "当前口径", "value": term["value"]})
        term["values"] = values
    return terms


def build_portal() -> dict:
    verify_sources()
    documents = discover_documents()
    latest = read_json(PROJECT_ROOT / "records/latest_state.json")
    paths = {path.relative_to(PROJECT_ROOT).as_posix(): page_id(path.relative_to(PROJECT_ROOT).as_posix()) for path, _, _ in documents}
    pages = []
    sources = [PORTAL / name for name in ["index.template.html", "app.css", "app.js", "project.json", "terms.json"]]
    for path, title, group in documents:
        text = path.read_text(encoding="utf-8")
        rendered, toc = render_markdown(text, path, paths)
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        pages.append({"id": paths[rel], "title": title, "group": group, "path": rel, "html": rendered, "toc": toc, "text": text,
                      "historical": rel.startswith("results/阶段") and rel!=latest.get("report")})
        sources.append(path)
    figures = []
    labels = {"control_comparison": "控制轨迹对比", "control_ablation": "回收与温控消融", "control_robustness": "扰动与迁移检验",
              "calibration_predictions": "四路程SOC与电压预测", "parameter_intervals": "条件性参数区间", "Cr_training_profile": "滚阻训练剖面",
              "scenario_A_overview": "场景A综合轨迹", "scenario_A_energy": "场景A能量分解", "scenario_A_sensitivity": "场景A灵敏度",
              "data_audit": "原始数据审计", "training_weight_stability": "训练权重稳定性", "reference_E_overview": "参考E综合轨迹",
              "reference_E_dc_energy": "参考E能量分解", "reference_E_sensitivity": "参考E灵敏度",
              "durability_duration": "连续驾驶时长对照", "durability_recovery": "实际恢复耗电与余热差",
              "durability_traces": "90分钟驾驶与两小时停车", "sensor_bias_terminal": "测温偏差与严格终温",
              "reliability_ranges": "非线性与线性参数范围", "reliability_joint": "参数联合分布与下界堆积",
              "training_excitation": "训练车速与阻力变化规律", "nonlinear_block_sensitivity": "非线性范围的块长对照"}
    for path in sorted((PROJECT_ROOT / "results/figures").glob("*.png"), key=lambda p: (0 if p.name.startswith(("reliability","training_excitation","nonlinear_block")) else 1 if p.name.startswith(("durability","sensor_bias")) else 2 if p.name.startswith("control") else 3, p.name)):
        figures.append({"title": labels.get(path.stem, path.stem), "image": data_url(path)})
        sources.append(path)
    video_path = PROJECT_ROOT / "results/videos/C题_控制对照_开发样片.mp4"
    video = data_url(video_path) if video_path.exists() else None
    if video:
        sources.append(video_path)
    resources = []
    for path in sorted((PROJECT_ROOT / "赛题资料").rglob("*")) + sorted((PROJECT_ROOT / "data/raw").glob("*.xlsx")):
        if path.is_file():
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            resources.append({"name": path.name, "path": quote(rel, safe="/"), "type": path.suffix.lower().lstrip("."), "bytes": path.stat().st_size})
    sources.extend(PROJECT_ROOT / name for name in ["records/latest_state.json", "configs/reference.json", "configs/calibrated.json", "configs/optimized_rule.json", "src/bev/portal.py"])
    vendor = read_json(PORTAL / "vendor/sources.json")
    for item in vendor:
        path = PROJECT_ROOT / item["path"]
        if sha256(path) != item["sha256"]:
            raise ValueError("Portal asset hash mismatch: "+item["path"])
        sources.append(path)
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    payload = {"project": read_json(PORTAL / "project.json"), "state": latest, "pages": pages, "paths": paths,
               "terms": resolved_terms(), "figures": figures, "video": video, "resources": resources, "built_at": now,
               "licenses": [{"name": "MathJax 3.2.2 · Apache-2.0", "text": (PORTAL / "vendor/mathjax-LICENSE.txt").read_text(encoding="utf-8")},
                            {"name": "Mermaid 9.4.3 · MIT", "text": (PORTAL / "vendor/mermaid-LICENSE.txt").read_text(encoding="utf-8")} ]}
    # JSON中的<转义，避免文档内容提前闭合script元素。
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (PORTAL / "index.template.html").read_text(encoding="utf-8")
    replacements = {"CSS": (PORTAL / "app.css").read_text(encoding="utf-8"), "DATA": serialized,
                    "APP": (PORTAL / "app.js").read_text(encoding="utf-8"),
                    "MATHJAX": (PORTAL / "vendor/mathjax-3.2.2-full.js").read_text(encoding="utf-8"),
                    "MERMAID": (PORTAL / "vendor/mermaid-9.4.3.js").read_text(encoding="utf-8")}
    output = re.sub(r"/\*PORTAL:(CSS|DATA|APP|MATHJAX|MERMAID)\*/", lambda m: replacements[m[1]], template)
    destination = PROJECT_ROOT / OUTPUT_NAME
    temporary = destination.with_suffix(".html.tmp")
    temporary.write_text(output, encoding="utf-8")
    temporary.replace(destination)
    metadata = {"path": OUTPUT_NAME, "built_at_beijing": now, "documents": len(pages), "terms": len(payload["terms"]),
                "figures": len(figures), "bytes": destination.stat().st_size, "sha256": sha256(destination),
                "source_hashes": {p.relative_to(PROJECT_ROOT).as_posix(): sha256(p) for p in sorted(set(sources))},
                "progress_method": "Manual declared work-item counts, not score or physical accuracy",
                "learning_storage": "Browser localStorage; export/import available; source files are not edited from browser"}
    write_json(PROJECT_ROOT / "records/portal_build.json", metadata)
    return {key: metadata[key] for key in ["path", "built_at_beijing", "documents", "terms", "figures", "bytes"]}
