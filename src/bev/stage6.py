"""阶段六只整理冻结证据及审阅材料，不重新辨识或选择控制规则。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from .io import PROJECT_ROOT, read_json, write_json
from .paper import DRAFT, build_paper, render_review_pdf, verify_evidence


def publish_stage_six(build: dict, pdf: dict) -> dict:
    """同步实际产出与待办；队员独立工作和人工审阅仍需真实记录。"""
    verify_evidence()
    text=DRAFT.read_text(encoding="utf-8")
    tables=len(re.findall(r"\*\*表\d+",text))
    equations=len(re.findall(r"\\tag\{\d+\}",text))
    today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    visual=pdf.get("visual_review","pending")
    report=f"""# C题阶段六实际进展

日期：{today}，北京时间。命令：`python run.py paper`，也可用`stage6`。本轮只整理已冻结结果，没有新增校准或控制实验。

## 已完成

- [论文审阅初稿](../docs/论文/C题论文审阅初稿.md)：覆盖三问，{tables}张表、{equations}个编号公式、{pdf['figures']}张结果图。
- [PDF审阅版](paper/C题论文审阅初稿.pdf)：{pdf['pages']}页，{pdf['bytes']:,}字节；摘要首页、目录次页、连续居中页码已检查。
- [三问证据总表](../docs/论文/三问证据总表.md)：{build['metrics']}项关键数值有来源文件、选择条件、完整精度和限制；JSON/CSV位于`results/tables/paper_evidence.*`。
- [论文阅读与定稿说明](../docs/论文/论文审阅与定稿说明.md)：说明阅读顺序、原比赛格式和队员待完成事项。
- [正式视频分镜与讲解稿](../docs/正式视频分镜与讲解稿.md)：按175秒规划四环节，数字由相同证据生成；待试读、录音和制作。

## 已核查与仍待核查

原始9文件、校准/控制/连续行驶/参数可靠性冻结和结果关联核对通过。`records/paper_evidence_freeze.json`是计算后的证据快照，不是新实验预注册。原34项科研程序检查结果保留，本轮文档修改不重复执行无关实验。

交付核查另见`records/stage6_artifact_verification.json`；工作台离线浏览器核查见`results/portal_checks/browser_verification.json`。上述记录只对应各自实际核查的文件版本。

PDF离线渲染{pdf['formulas_rendered']}处公式，6张图片解码；无数学错误、表格横向溢出、近空白页或越界文字。目录页码和正文数字自动核对，未发生HTTP或意外本地资源请求。视觉审阅状态：`{visual}`；队员审阅仍待完成。检查详情见`records/paper_pdf_verification.json`。

从PDF实际文字核对正文约12pt、题目约16pt、一级标题约14pt；修复长公式触发的全篇缩放后，不再仅据CSS声明判断字号合格。表格及图注保持完整，附录单独起页。

已修复数学辅助朗读层参与可视排版造成的公式重复；同一修复同步到工作台。正文列6项实际来源，SciPy求解器文档已读取并缓存；未将未读论文写成已验证引用。

## 必须保留的限制

容量口径冲突、Cr主分布52%贴下界、OCV跨SOC电压迁移失败、600仅回顾性检验、热状态无实测、低SOC外推、±1℃严格终温失败及高温长程两小时恢复未齐，均进入论文和视频脚本。单循环理想热修正与实际停车总账分表，未混用比较条件。

## 距离正式交付还差什么

这是AI辅助审阅初稿。随题AI规定要求核心建模、实现与分析由队员独立完成；实际独立工作、人工修改和复核记录尚未提供。阅读或勾选术语不能替代独立完成。具体工具版本/发布日期未核实，不能虚填。

正式论文定稿、AI工具使用详情PDF、精简支撑包、签署承诺书和正式视频尚未完成，未上传。175秒是脚本设计时长，现有15秒无声开发样片不冒充正式成片。

下一步：队员核对核心工作和论文，开展针对性的技术审阅，制作正式四环节视频与可复现支撑材料。论文/承诺书/支撑材料截止10月20日20:00；视频截止10月21日20:00，北京时间。
"""
    (PROJECT_ROOT/"results/阶段六进展.md").write_text(report,encoding="utf-8")
    state_path=PROJECT_ROOT/"records/latest_state.json"
    state=read_json(state_path)
    state.update(date_beijing=today,status="stage6_evidence_and_paper_review_draft",report="results/阶段六进展.md")
    completed=["post-computation three-question evidence ledger with source hashes",
               "AI-assisted three-question paper review draft with offline HTML and checked PDF",
               "175-second four-part video storyboard and narration draft, not rendered final video"]
    for item in completed:
        if item not in state["completed"]:
            state["completed"].append(item)
    state["paper"]={"status":"AI_assisted_review_draft_not_final","markdown":build["markdown"],
                    "pdf":pdf["path"],"pdf_pages":pdf["pages"],"pdf_bytes":pdf["bytes"],"pdf_sha256":pdf["sha256"],
                    "evidence_metrics":build["metrics"],"numbered_tables":tables,"numbered_equations":equations,
                    "figures":pdf["figures"],"visual_review":visual,"team_review":"pending",
                    "team_independent_core_work":"not_provided","video_script":build["video_script"],
                    "evidence_is_post_computation_snapshot":True}
    state["next"]=["Collect actual team independent work, manual revisions and technical review of the draft",
                   "Create the complete four-part video and a reproducible support package",
                   "Complete truthful AI details, final paper checks and separately signed commitment; no upload yet"]
    from .portal import discover_documents, resolved_terms
    state["project_portal"].update(documents=len(discover_documents()),terms=len(resolved_terms()),
                                  figures=len(list((PROJECT_ROOT/"results/figures").glob("*.png"))))
    write_json(state_path,state)
    project_path=PROJECT_ROOT/"portal/project.json"
    project=read_json(project_path)
    project.update(stage_label="阶段六 · 论文与证据整理",current_title="三问审阅初稿已生成，开始核对与定稿。",
                   current_description="论文、证据索引与视频分镜已连通。先核对核心方法和关键结论，再完成正式视频、支撑包及真实AI记录。")
    project["current_tasks"]=[
        {"title":"先读论文说明","detail":"按阅读顺序核对三问，补充队员独立工作与实际修改。","link":"docs/论文/论文审阅与定稿说明.md"},
        {"title":"核对关键证据","detail":"25项数字可追溯；好结果与失败一起保留。","link":"docs/论文/三问证据总表.md"},
        {"title":"制作正式视频","detail":"175秒分镜已备，待试读、录音和完整动画制作。","link":"docs/正式视频分镜与讲解稿.md"}]
    delivery=project["stages"][-1]
    additions=["三问证据索引与来源核对","论文审阅初稿与PDF排版检查","正式视频分镜与讲解初稿"]
    existing={r["text"] for r in delivery["items"]}
    delivery["items"][1:1]=[{"text":name,"done":True} for name in additions if name not in existing]
    write_json(project_path,project)
    from .progress import write_manifest
    write_manifest(stage=state["status"])
    return state


def run_stage_six() -> dict:
    build=build_paper()
    pdf=render_review_pdf()
    return publish_stage_six(build,pdf)
