"""Run the diagnoser against the fault library and store the scores.

python -m app.eval --fake                 keyword baseline, no API key needed
python -m app.eval                        real model (needs ANTHROPIC_API_KEY)
python -m app.eval --scenario oom-killed  one or more scenarios only
"""

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.eval.runner import run_eval
from app.eval.scenarios import load_scenarios
from app.main import build_model
from app.models import EvalRun


def format_report(run: EvalRun) -> str:
    width = max(len(r.scenario_id) for r in run.results)
    lines = [f"run {run.id}  model={run.model}", ""]
    for result in run.results:
        mark = "PASS" if result.passed else "FAIL"
        predicted = result.predicted_category or f"error: {result.error}"
        lines.append(
            f"{mark}  {result.scenario_id:<{width}}  expected={result.expected_category}"
            f"  got={predicted}  {result.latency_ms}ms"
        )
    lines += ["", f"top-1 root cause match: {run.passed}/{run.total}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.eval", description=__doc__.split("\n")[0])
    parser.add_argument("--fake", action="store_true", help="use the keyword baseline model")
    parser.add_argument("--faults-dir", type=Path, default=Path("../faults"))
    parser.add_argument("--scenario", action="append", help="scenario id; repeatable")
    parser.add_argument("--database-url", help="overrides DATABASE_URL")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.fake:
        settings = settings.model_copy(update={"llm_mode": "fake"})
    scenarios = load_scenarios(args.faults_dir, args.scenario)
    if not scenarios:
        print(f"no scenarios found under {args.faults_dir}", file=sys.stderr)
        return 2

    engine = make_engine(args.database_url or settings.database_url)
    with make_session_factory(engine)() as session:
        run = run_eval(scenarios, build_model(settings), session, settings.log_tail_lines)
        print(format_report(run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
