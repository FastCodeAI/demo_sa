"""CLI entry point for demo_scheduler."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@click.group()
def main() -> None:
    """DEMO Pharma production + packaging scheduler."""


@main.command()
@click.option("--quarter", default=None, help="Override horizon.quarter (e.g. Q1).")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--output-dir", "output_dir", type=click.Path(path_type=Path), default=None)
@click.option("--time-limit", type=int, default=None, help="MILP time limit (seconds).")
@click.option("--cpsat-time-limit", type=float, default=10.0, help="CP-SAT time limit per week (seconds).")
@click.option("--skip-cpsat", is_flag=True, default=False, help="Skip Tier-3 sequencing (Phase-1 fallback).")
@click.option("--use-catalog", is_flag=True, default=False, help="Assemble the model from catalog/constraints/ (Phase 2b).")
def solve(
    quarter: str | None,
    config_path: Path | None,
    output_dir: Path | None,
    time_limit: int | None,
    cpsat_time_limit: float,
    skip_cpsat: bool,
    use_catalog: bool,
) -> None:
    """Solve the two-stage (MILP + CP-SAT) scheduler for the configured quarter."""
    from demo_scheduler.config.load import load_config
    from demo_scheduler.data.load_excel import load_orders
    from demo_scheduler.data.synthesize import synthesize
    from demo_scheduler.model.build import build_model
    from demo_scheduler.output.gantt import write_gantt
    from demo_scheduler.output.kpis import compute_kpis, format_kpis_text, write_kpis
    from demo_scheduler.output.plan import write_plan_csv, write_plan_json
    from demo_scheduler.solve.extract import extract_plan
    from demo_scheduler.solve.orchestrator import solve_orchestrated
    from demo_scheduler.solve.solver import solve as run_solve

    cfg = load_config(config_path)
    if quarter:
        cfg.horizon.quarter = quarter
    if time_limit:
        cfg.solver.time_limit_seconds = time_limit

    click.echo(f"Loading Packaging Ampoules.xlsx (horizon: {cfg.horizon.quarter}, include_backorder={cfg.horizon.include_backorder})…")
    raw = load_orders(
        quarter=cfg.horizon.quarter,
        include_backorder=cfg.horizon.include_backorder,
    )
    click.echo(f"  {len(raw.orders)} demand rows after horizon filter.")

    click.echo("Synthesising parameter tables…")
    params = synthesize(raw, cfg)
    click.echo(f"  |O|={len(params.orders)}  |S|={len(params.materials)}  |M|={len(params.machines)}  |F|={len(params.formats)}  |W|={len(params.weeks)}")
    click.echo(f"  Piramal orders: {len(params.piramal_orders)}   Org orders: {len(params.orgs_no_split)}")

    if use_catalog:
        from demo_scheduler.model.compile import build_from_catalog
        catalog_root = PROJECT_ROOT / "catalog"
        click.echo(f"Building Pyomo model from catalog ({catalog_root})…")
        model, cat_rows = build_from_catalog(params, catalog_root)
        enabled = [r.id for r in cat_rows if r.enabled]
        click.echo(f"  catalog rows enabled: {len(enabled)} ({', '.join(enabled)})")
    else:
        model = None  # built below by the legacy paths

    if skip_cpsat:
        if model is None:
            click.echo("Building Pyomo model (Tier-2 only)…")
            model = build_model(params)
        click.echo(f"Solving MILP (time limit = {cfg.solver.time_limit_seconds}s)…")
        milp_result = run_solve(model, cfg.solver.time_limit_seconds, cfg.solver.mip_gap)
        click.echo(f"  status={milp_result.status}  objective={milp_result.objective}  time={milp_result.solve_time_s:.1f}s")
        if milp_result.status not in {"optimal", "feasible", "time_limit"}:
            click.echo(f"!! Solver did not produce a usable solution ({milp_result.status}).")
            sys.exit(2)
        plan = extract_plan(model, params)
        report_status = milp_result.status
        report_obj = milp_result.objective
        report_time = milp_result.solve_time_s
        infeasible_weeks: list[int] = []
        cpsat_time = 0.0
        retries = 0
    else:
        click.echo(f"Running Tier-2 MILP → Tier-3 CP-SAT orchestrator (MILP limit={cfg.solver.time_limit_seconds}s, CP-SAT/week={cpsat_time_limit}s)…")
        plan, info = solve_orchestrated(
            params,
            time_limit_milp_s=cfg.solver.time_limit_seconds,
            mip_gap=cfg.solver.mip_gap,
            time_limit_cpsat_per_week_s=cpsat_time_limit,
            max_retries=2,
            catalog_root=(PROJECT_ROOT / "catalog") if use_catalog else None,
        )
        click.echo(f"  MILP   status={info.milp_result.status}  obj={info.milp_result.objective}  time={info.milp_result.solve_time_s:.1f}s")
        click.echo(f"  CP-SAT total time={info.sequence_result.total_solve_time_s:.1f}s   retries={info.retries}   infeasible weeks={info.infeasible_weeks or 'none'}")
        if info.milp_result.status not in {"optimal", "feasible", "time_limit"}:
            click.echo(f"!! Solver did not produce a usable solution ({info.milp_result.status}).")
            sys.exit(2)
        report_status = info.milp_result.status
        report_obj = info.milp_result.objective
        report_time = info.solve_time_s
        infeasible_weeks = info.infeasible_weeks
        cpsat_time = info.sequence_result.total_solve_time_s
        retries = info.retries

    out = output_dir or (PROJECT_ROOT / "outputs" / cfg.horizon.quarter.lower())
    out.mkdir(parents=True, exist_ok=True)
    write_plan_json(plan, out / "plan.json")
    write_plan_csv(plan, out / "plan.csv")
    write_gantt(plan, out / "gantt.png")

    from demo_scheduler.solve.solver import SolveResult
    composite = SolveResult(status=report_status, objective=report_obj, solve_time_s=report_time)
    kpis = compute_kpis(plan, composite)
    kpis["cpsat"] = {
        "total_time_s": cpsat_time,
        "retries": retries,
        "infeasible_weeks": infeasible_weeks,
        "n_intervals": len(plan.sequence),
    }
    write_kpis(kpis, out / "kpis.json")

    click.echo("")
    click.echo(format_kpis_text(kpis))
    click.echo("")
    if plan.sequence:
        click.echo(f"CP-SAT intervals:   {len(plan.sequence)} (minute-level)")
        if infeasible_weeks:
            click.echo(f"  ⚠ unsequenced weeks: {infeasible_weeks}")
    click.echo(f"Wrote: {out}/{{plan.json,plan.csv,gantt.png,kpis.json}}")


@main.command()
def analyse() -> None:
    """Run the legacy exploratory analyzer (writes outputs/)."""
    script = PROJECT_ROOT / "analyze_packaging_data.py"
    subprocess.run([sys.executable, str(script)], check=True)


@main.group()
def catalog() -> None:
    """Constraint catalog operations (Phase 2b)."""


@catalog.command("list")
def catalog_list() -> None:
    """List every constraint row in catalog/constraints/."""
    from demo_scheduler.catalog.load import load_catalog

    rows = load_catalog(PROJECT_ROOT / "catalog")
    click.echo(f"{'ID':<8} {'PATTERN':<22} {'LAYER':<12} {'SEV':<5} NAME")
    for r in rows:
        click.echo(
            f"{r.id:<8} {r.formal_expr.type.value:<22} "
            f"{r.solver_layer.value:<12} {r.severity.value:<5} {r.name}"
        )


@catalog.command("show")
@click.argument("rid")
def catalog_show(rid: str) -> None:
    """Print one constraint row in full."""
    from demo_scheduler.catalog.load import get_row, load_catalog

    rows = load_catalog(PROJECT_ROOT / "catalog")
    row = get_row(rows, rid)
    click.echo(row.model_dump_json(indent=2))


@catalog.command("validate")
def catalog_validate() -> None:
    """Load + pydantic-validate every catalog row."""
    from demo_scheduler.catalog.load import load_catalog

    try:
        rows = load_catalog(PROJECT_ROOT / "catalog")
    except Exception as e:
        click.echo(f"Validation FAILED: {e}", err=True)
        sys.exit(1)
    click.echo(f"OK — {len(rows)} catalog rows valid.")


@main.command()
@click.argument("text", nargs=-1, required=True)
@click.option("--persona", type=click.Choice(["sales", "production", "compliance"]), default=None)
@click.option("--use-mock", is_flag=True, default=False,
              help="Use a canned-response MockLLM (useful for demos / CI).")
@click.option("--llm", "llm_backend",
              type=click.Choice(["auto", "openai", "anthropic"]), default="auto",
              help="Which real LLM to use. 'auto' picks based on which API key is set.")
@click.option("--apply", is_flag=True, default=False,
              help="If the elicitation passes Verifier, write the patched row back to catalog/.")
def chat(text: tuple[str, ...], persona: str | None, use_mock: bool,
         llm_backend: str, apply: bool) -> None:
    """Send a natural-language message to the Supervisor agent.

    Examples:
      demo_scheduler chat "raise Piramal monthly band to 2.5M"
      demo_scheduler chat "why was W7 infeasible?"
      demo_scheduler chat "explain to Sales" --persona sales
    """
    import json as _json

    from demo_scheduler.agents.llm import MockLLM, default_llm
    from demo_scheduler.agents.supervisor import run_turn

    msg = " ".join(text)

    if use_mock:
        llm = MockLLM(responses=[_json.dumps({
            "id": "C-037",
            "parameter_changes": {"monthly_max": 2_500_000},
            "version_to": "2026-05-12.v2",
            "rationale": f"(mock) {msg}",
        })])
    else:
        prefer = None if llm_backend == "auto" else llm_backend
        llm = default_llm(prefer=prefer)
        click.echo(f"(using {type(llm).__name__} {getattr(llm, 'model', '')})")

    turn = run_turn(msg, llm, PROJECT_ROOT / "catalog", persona=persona)
    click.echo(f"intent: {turn.intent}")

    if turn.intent == "edit_constraint":
        elic = turn.output.get("elicitation")
        if elic and elic.patch:
            click.echo(f"patch: {elic.patch.id} -> {elic.patch.parameter_changes}")
            for o in elic.verify.outcomes:
                tick = "✓" if o.passed else "✗"
                click.echo(f"  [{tick}] {o.name}: {o.detail}")
            if elic.verify.approval_gates:
                click.echo("approval gates:")
                for g in elic.verify.approval_gates:
                    click.echo(f"  ⚠ {g}")
            if elic.verify.passed and not elic.verify.needs_approval and apply:
                _apply_patch_to_disk(elic.patch)
                click.echo("✓ patch applied to catalog/")
        elif elic and elic.error:
            click.echo(f"elicitation error: {elic.error}", err=True)
        else:
            click.echo("(no patch produced)")
    elif turn.intent == "explain":
        out = turn.output.get("explanation")
        if out:
            click.echo(out.text)
    elif turn.intent == "unknown":
        click.echo(turn.output.get("message", "unknown intent"))
    else:
        click.echo(_json.dumps(turn.output, default=str, indent=2))


def _apply_patch_to_disk(patch) -> None:
    """Write the patched parameters back into the catalog YAML."""
    import yaml as _yaml

    cat_dir = PROJECT_ROOT / "catalog" / "constraints"
    target = None
    for p in cat_dir.glob(f"{patch.id}-*.yaml"):
        target = p
        break
    if target is None:
        target = cat_dir / f"{patch.id}.yaml"
    if not target.exists():
        click.echo(f"  cannot find on-disk YAML for {patch.id}", err=True)
        return
    with target.open() as f:
        data = _yaml.safe_load(f)
    data.setdefault("parameters", {}).update(patch.parameter_changes)
    data["version"] = patch.version_to
    with target.open("w") as f:
        _yaml.safe_dump(data, f, sort_keys=False)


@main.command()
@click.argument("before_kpis", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("after_kpis", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--use-mock", is_flag=True, default=False)
@click.option("--llm", "llm_backend",
              type=click.Choice(["auto", "openai", "anthropic"]), default="auto")
def whatif(before_kpis: Path, after_kpis: Path, use_mock: bool, llm_backend: str) -> None:
    """Narrate the delta between two KPI JSON files."""
    from demo_scheduler.agents.llm import MockLLM, default_llm
    from demo_scheduler.agents.whatif import whatif as _whatif

    if use_mock:
        llm = MockLLM(responses=["• OTIF improved.\n• Idle hours rose modestly.\n• No new changeovers."])
    else:
        prefer = None if llm_backend == "auto" else llm_backend
        llm = default_llm(prefer=prefer)
    result = _whatif(before_kpis, after_kpis, llm)
    click.echo("DELTA:")
    for k, v in result.delta.items():
        click.echo(f"  {k:<20} {v}")
    click.echo()
    click.echo(result.narration)


@main.command("validate-config")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
def validate_config(config_path: Path | None) -> None:
    """Pydantic-validate a config YAML file."""
    from demo_scheduler.config.load import load_config

    try:
        cfg = load_config(config_path)
    except Exception as e:
        click.echo(f"Validation FAILED: {e}", err=True)
        sys.exit(1)
    click.echo(f"OK — config valid. Horizon: {cfg.horizon.quarter}, {len(cfg.machines)} machines.")


@main.command()
@click.option("--plan", "plan_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
def explain(plan_path: Path) -> None:
    """Print a KPI summary for a saved plan.json."""
    import json

    from demo_scheduler.output.kpis import format_kpis_text

    data = json.loads(plan_path.read_text())
    kpis_path = plan_path.with_name("kpis.json")
    if kpis_path.exists():
        kpis = json.loads(kpis_path.read_text())
        click.echo(format_kpis_text(kpis))
    else:
        click.echo(f"Plan has {len(data['assignments'])} orders across {len(data['machines'])} machines, {len(data['weeks'])} weeks.")


if __name__ == "__main__":
    main()
