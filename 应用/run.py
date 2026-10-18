"""Command-line entry point for the B project; run from any working directory."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期未提供，提交前按实际使用记录补齐并人工核查。
import argparse
import json

from b_twin.paths import PROJECT


def main():
    parser = argparse.ArgumentParser(description="B题交通孪生开发入口")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show project assets and current status")
    commands.add_parser("build-boundary", help="Build downstream extension diagnostic")
    commands.add_parser("build-feeders", help="Preserve upstream geometry at missing downstream inlets")
    commands.add_parser("build-continuation", help="Repair mainline routes ending at the last core detector")
    commands.add_parser("audit-topology", help="Crosscheck official one-way geometry and SUMO lane connections")
    commands.add_parser("collect-live", help="Collect one official snapshot into observation state")
    commands.add_parser("plan-live", help="Acquire a real snapshot and preview a future demand plan; no live SUMO")
    wallclock = commands.add_parser("wallclock-run", help="Pace SUMO with actual HTTP receipts; current traffic remains unvalidated")
    wallclock.add_argument("--run-id", required=True)
    wallclock.add_argument("--duration", type=float, default=960)
    wallclock.add_argument("--drop-after", type=float, help="Designed transport outage after this elapsed time")
    wallclock_report = commands.add_parser("report-wallclock", help="Audit actual HTTP receipts, paced timing and future-only demand")
    wallclock_report.add_argument("--prefix", default="stage08_main_v2")
    assimilate = commands.add_parser("assimilation-run", help="Causal archive replay with incremental TraCI demand updates")
    assimilate.add_argument("--case", choices=["ordinary", "peak"], required=True)
    assimilate.add_argument("--policy", choices=["prior", "rolling"], default="rolling")
    assimilate.add_argument("--run-id", required=True)
    assimilate.add_argument("--extra-delay", type=float, default=0.)
    assimilate.add_argument("--dropout", action="store_true")
    assimilation_suite = commands.add_parser("assimilation-suite", help="Frozen six-run observation-update engineering checks")
    assimilation_suite.add_argument("--prefix", default="stage06_main")
    assimilation_suite.add_argument("--jobs", type=int, default=2)
    assimilation_report = commands.add_parser("report-assimilation", help="Audit availability, future insertion and failure fallback")
    assimilation_report.add_argument("--prefix", default="stage06_main")
    commands.add_parser("report-stage01", help="Summarize the first paired boundary diagnostics")
    commands.add_parser("report-stage02", help="Summarize time-varying fixed-agent diagnostics")
    commands.add_parser("report-stage03", help="Review artificial sink repair and residual supply scenarios")
    commands.add_parser("project-hub", help="Refresh the offline project management HTML and document tabs")
    suite = commands.add_parser("governance-suite", help="Run five frozen paired seeds with free/fixed/feedback policies")
    suite.add_argument("--jobs", type=int, default=3)
    suite.add_argument("--prefix", default="stage04_main")
    governed_report = commands.add_parser("report-governance", help="Audit fixed exogenous governance comparisons and section storage")
    governed_report.add_argument("--prefix", default="stage04_main")
    commands.add_parser("build-emergency-access", help="Build a directed route to a documented public road near the hospital")
    emergency = commands.add_parser("emergency-run", help="Run a fixed designed emergency task and incident")
    emergency.add_argument("--policy", choices=["baseline", "yield", "combined"], required=True)
    emergency.add_argument("--seed", type=int, default=20261017)
    emergency.add_argument("--run-id", required=True)
    emergency.add_argument("--no-task", action="store_true", help="Topology/incident ablation; not a rescue comparison")
    emergency.add_argument("--no-event", action="store_true", help="No lane restriction; not a rescue comparison")
    emergency_suite = commands.add_parser("emergency-suite", help="Five paired seeds plus predeclared event/topology ablations")
    emergency_suite.add_argument("--jobs", type=int, default=3)
    emergency_suite.add_argument("--prefix", default="stage05_main")
    emergency_report = commands.add_parser("report-emergency", help="Audit rescue and ordinary-traffic effects")
    emergency_report.add_argument("--prefix", default="stage05_main")
    guarded = commands.add_parser("guarded-emergency-run", help="Route/space checked sparse yielding with unchanged safety modes")
    guarded.add_argument("--policy", choices=["baseline", "guarded", "guarded_green"], required=True)
    guarded.add_argument("--seed", type=int, default=20261023)
    guarded.add_argument("--run-id", required=True)
    guarded_suite = commands.add_parser("guarded-emergency-suite", help="Five new paired seeds for sparse emergency assistance")
    guarded_suite.add_argument("--prefix", default="stage07_main")
    guarded_suite.add_argument("--jobs", type=int, default=3)
    guarded_report = commands.add_parser("report-guarded-emergency", help="Audit sparse requests, actual lane observations and all-agent cost")
    guarded_report.add_argument("--prefix", default="stage07_main")
    stress = commands.add_parser("stress-run", help="Run a predeclared input stress condition")
    stress.add_argument("--family", choices=["governance", "emergency"], required=True)
    stress.add_argument("--policy", required=True)
    stress.add_argument("--scenario", required=True)
    stress.add_argument("--seed", type=int, required=True)
    stress.add_argument("--run-id", required=True)
    stress_suite = commands.add_parser("stress-suite", help="Frozen 84-run one-factor input screening")
    stress_suite.add_argument("--prefix", default="stage09_main")
    stress_suite.add_argument("--jobs", type=int, default=3)
    stress_report = commands.add_parser("report-stress", help="Audit all fixed-input stress pairs")
    stress_report.add_argument("--prefix", default="stage09_main")
    holdout_suite = commands.add_parser("holdout-suite", help="Frozen unseen-day open-loop and event-time tracking validation")
    holdout_suite.add_argument("--prefix", default="stage10_main")
    holdout_suite.add_argument("--jobs", type=int, default=3)
    holdout_report = commands.add_parser("report-holdout", help="Audit every cross-day plan, revision and result")
    holdout_report.add_argument("--prefix", default="stage10_main")
    dashboard = commands.add_parser("dashboard", help="Export an offline replay and observation review page")
    dashboard.add_argument("--run-id", required=True)
    run = commands.add_parser("run", help="Run an isolated ordinary/peak diagnostic")
    run.add_argument("--case", choices=["ordinary", "peak"], required=True)
    run.add_argument("--network", choices=["original", "extended", "feeders", "through"], default="original")
    run.add_argument("--backend", choices=["subprocess", "traci"], default="subprocess")
    run.add_argument("--seed", type=int, default=20261017)
    run.add_argument("--run-id")
    run.add_argument("--demand", choices=["legacy", "fixedmean", "time15"], default="time15")
    run.add_argument("--clearance", type=int, default=900, help="No new departures; excluded from observation errors")
    run.add_argument("--entry-ridge", type=float, default=0., help="Sensitivity penalty on unsupported inlet totals; time15 only")
    run.add_argument("--background-vph", type=float, default=0., help="Designed total feeder demand; NOT observed, feeders/through network only")
    run.add_argument("--supply-vph", type=float, default=0., help="Numerical residual exit service scenario; 0 disables, NOT a real signal")
    run.add_argument("--meter-policy", choices=["free", "fixed", "feedback"], default=None,
        help="Assumed feeder facilities; all arms identical except control, requires through and traci")
    args = parser.parse_args()
    if args.command == "status":
        current = json.loads((PROJECT / "管理中心/项目状态.json").read_text(encoding="utf-8"))
        report = {"project": str(PROJECT), "log": str(PROJECT / "开发日志.md"),
            "phase": current["current"], "next": current["next"], "latest_report": current["latest_report"],
            "data_exists": (PROJECT / "B题数据工程/refined/all_counts").exists()}
    elif args.command == "build-boundary":
        from b_twin.boundary import prepare_extended
        report = prepare_extended()
    elif args.command == "build-feeders":
        from b_twin.boundary import prepare_feeders
        report = prepare_feeders()
    elif args.command == "build-continuation":
        from b_twin.boundary import prepare_continuation
        report = prepare_continuation()
    elif args.command == "audit-topology":
        from b_twin.topology import audit_topology
        report = audit_topology()
    elif args.command == "collect-live":
        from b_twin.live import collect_once
        report = collect_once()
    elif args.command == "plan-live":
        from b_twin.assimilation import live_plan
        report = live_plan()
    elif args.command == "wallclock-run":
        from b_twin.wallclock import execute_wallclock
        report = execute_wallclock(args.run_id,args.duration,args.drop_after)
        from b_twin.live_accounting import finalize_accounting
        report.update(finalize_accounting(report['run_dir']))
        from b_twin.wallclock_dashboard import export_wallclock_dashboard
        report['replay_page'] = export_wallclock_dashboard(args.run_id)['dashboard']
    elif args.command == "report-wallclock":
        from b_twin.wallclock_report import report_wallclock
        report = report_wallclock(args.prefix)
    elif args.command == "assimilation-run":
        from b_twin.assimilation import execute_assimilation
        report = execute_assimilation(args.case, args.policy, args.run_id, args.extra_delay, args.dropout)
    elif args.command == "assimilation-suite":
        from b_twin.assimilation_suite import run_assimilation_suite
        report = run_assimilation_suite(args.prefix, args.jobs)
    elif args.command == "report-assimilation":
        from b_twin.assimilation_suite import report_assimilation
        report = report_assimilation(args.prefix)
    elif args.command == "report-stage01":
        from b_twin.diagnostics import report_stage01
        report = report_stage01()
    elif args.command == "report-stage02":
        from b_twin.diagnostics import report_stage02
        report = report_stage02()
    elif args.command == "report-stage03":
        from b_twin.stage03 import report_stage03
        report = report_stage03()
    elif args.command == "dashboard":
        from b_twin.paths import APP
        metadata = json.loads((APP / "results" / args.run_id / "run.json").read_text(encoding="utf-8")) if args.run_id.replace('_','').replace('-','').isalnum() else {}
        if metadata.get("wallclock_protocol"):
            from b_twin.wallclock_dashboard import export_wallclock_dashboard
            report = export_wallclock_dashboard(args.run_id)
        else:
            from b_twin.dashboard import export_dashboard
            report = export_dashboard(args.run_id)
    elif args.command == "project-hub":
        from b_twin.project_hub import build_project_hub
        report = build_project_hub()
    elif args.command == "governance-suite":
        from b_twin.governance_suite import run_suite
        report = run_suite(args.jobs, args.prefix)
    elif args.command == "report-governance":
        from b_twin.governance_report import report_governance
        report = report_governance(args.prefix)
    elif args.command == "build-emergency-access":
        from b_twin.emergency_access import build_emergency_access
        report = build_emergency_access()
    elif args.command == "emergency-run":
        from b_twin.emergency import execute_emergency
        report = execute_emergency(args.seed, args.policy, args.run_id, not args.no_task, not args.no_event)
    elif args.command == "emergency-suite":
        from b_twin.emergency_suite import run_emergency_suite
        report = run_emergency_suite(args.jobs, args.prefix)
    elif args.command == "report-emergency":
        from b_twin.emergency_report import report_emergency
        report = report_emergency(args.prefix)
    elif args.command == "guarded-emergency-run":
        from b_twin.emergency_guard import execute_guarded
        report = execute_guarded(args.seed,args.policy,args.run_id)
    elif args.command == "guarded-emergency-suite":
        from b_twin.emergency_guard_suite import run_guarded_suite
        report = run_guarded_suite(args.prefix,args.jobs)
    elif args.command == "report-guarded-emergency":
        from b_twin.emergency_guard_report import report_guarded
        report = report_guarded(args.prefix)
    elif args.command == "stress-run":
        from b_twin.stress import execute_stress
        report = execute_stress(args.family, args.policy, args.scenario, args.seed, args.run_id)
    elif args.command == "stress-suite":
        from b_twin.stress import run_stress_suite
        report = run_stress_suite(args.prefix, args.jobs)
    elif args.command == "report-stress":
        from b_twin.stress_report import report_stress
        report = report_stress(args.prefix)
    elif args.command == "holdout-suite":
        from b_twin.holdout import run_holdout
        report = run_holdout(args.prefix, args.jobs)
    elif args.command == "report-holdout":
        from b_twin.holdout_report import report_holdout
        report = report_holdout(args.prefix)
    else:
        from b_twin.runner import execute
        report = execute(args.case, args.network, args.seed, args.backend, args.run_id, args.demand, args.clearance, args.entry_ridge, args.background_vph, args.supply_vph, args.meter_policy)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
