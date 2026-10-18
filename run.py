"""项目命令入口；AI辅助来源和人工复核情况见records/ai_usage.md。"""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from bev.data import audit_data
from bev.workflow import plot_audit, run_all, run_checks, run_reference


def main() -> None:
    parser = argparse.ArgumentParser(description="C题：仿真、校准、控制与离线项目工作台")
    parser.add_argument("command", nargs="?", default="all", choices=["all", "audit", "reference", "checks", "cltc", "calibrate", "evaluate", "stage2", "refit", "profile", "adequacy", "control", "stage3", "stage4", "durability", "stage5", "reliability", "paper", "stage6", "final", "package", "improve", "video", "portal"])
    args = parser.parse_args()
    if args.command == "all":
        result = run_all()
        print(json.dumps({"status": result["status"], "records": result["audit_records"],
                          "reference_E_kwh_100km": result["reference_E_consumption_kwh_100km"],
                          "scenario_A_kwh_100km": result["scenario_A_reference_consumption_kwh_100km"],
                          "checks": result["verification"]["tests_run"], "results": result["report"]}, ensure_ascii=False, indent=2))
    elif args.command == "audit":
        result = audit_data()
        plot_audit()
        print(json.dumps({"records": result["records"], "trips": result["trips"]}, ensure_ascii=False))
    elif args.command == "reference":
        audit_data()
        print(json.dumps(run_reference(), ensure_ascii=False, indent=2))
    elif args.command == "checks":
        audit_data()
        print(json.dumps(run_checks(), ensure_ascii=False, indent=2))
    elif args.command == "cltc":
        from bev.scenarios import run_scenario_a
        print(json.dumps(run_scenario_a(), ensure_ascii=False, indent=2))
    elif args.command == "calibrate":
        from bev.calibration import fit_parameters, confidence_analysis
        print(json.dumps({"fit": fit_parameters(), "uncertainty": confidence_analysis()}, ensure_ascii=False, indent=2))
    elif args.command == "evaluate":
        from bev.calibration import evaluate_calibration, plot_calibration
        result = evaluate_calibration()
        plot_calibration()
        print(json.dumps(result["group_metrics"], ensure_ascii=False, indent=2))
    elif args.command == "stage2":
        from bev.progress import run_stage_two
        result = run_stage_two()
        print(json.dumps({"status": result["status"], "results": result["report"]}, ensure_ascii=False, indent=2))
    elif args.command == "refit":
        from bev.calibration import reproduce_fit
        print(json.dumps(reproduce_fit(), ensure_ascii=False, indent=2))
    elif args.command == "profile":
        from bev.diagnostics import run_training_profile
        print(json.dumps(run_training_profile(), ensure_ascii=False, indent=2))
    elif args.command == "adequacy":
        from bev.adequacy import run_adequacy
        print(json.dumps(run_adequacy(),ensure_ascii=False,indent=2))
    elif args.command in ["control","stage3"]:
        from bev.stage3 import run_stage_three
        result=run_stage_three()
        print(json.dumps({"status":result["status"],"report":result["report"]},ensure_ascii=False,indent=2))
    elif args.command in ["stage4","durability"]:
        from bev.stage4 import run_stage_four
        result=run_stage_four()
        print(json.dumps({"status":result["status"],"report":result["report"],"durability":result["durability"]},ensure_ascii=False,indent=2))
    elif args.command in ["stage5","reliability"]:
        from bev.stage5 import run_stage_five
        result=run_stage_five()
        print(json.dumps({"status":result["status"],"report":result["report"],"reliability":result["reliability"]},ensure_ascii=False,indent=2))
    elif args.command in ["paper","stage6"]:
        from bev.stage6 import run_stage_six
        result=run_stage_six()
        print(json.dumps({"status":result["status"],"report":result["report"],"paper":result["paper"]},ensure_ascii=False,indent=2))
    elif args.command == "final":
        from bev.delivery import build_documents
        print(json.dumps(build_documents(),ensure_ascii=False,indent=2))
    elif args.command == "package":
        from bev.portal import build_portal
        build_portal()
        from bev.delivery import build_packages
        print(json.dumps(build_packages(),ensure_ascii=False,indent=2))
    elif args.command == "improve":
        from bev.remediation import run_remediation
        from bev.remediation_bounds import run as run_bounds
        from bev.remediation_report import publish
        run_remediation()
        run_bounds()
        summary=publish()
        print(json.dumps({k:summary[k] for k in ['status','two_hour_parking','sensor_exact_model','step_checks','Cr_reliability_solved']},ensure_ascii=False,indent=2))
    elif args.command == "video":
        from bev.video import render_development_video
        print(json.dumps(render_development_video(),ensure_ascii=False,indent=2))
    # 新增或修改docs/*.md后也可单独运行portal，不必重算仿真。
    from bev.portal import build_portal
    portal = build_portal()
    from bev.io import PROJECT_ROOT, read_json
    from bev.progress import write_manifest
    previous_manifest = PROJECT_ROOT / "records/run_manifest.json"
    stage = read_json(previous_manifest).get("stage", "project_workspace") if previous_manifest.exists() else "project_workspace"
    write_manifest(stage=stage)
    print(json.dumps({"project_portal":portal},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
