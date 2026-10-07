import time

from sqlalchemy.orm import Session

from app.diagnosis.evidence import EvidenceCollector, TargetNotFound
from app.diagnosis.llm import DiagnosisModel, ModelError
from app.diagnosis.redact import RedactionError
from app.diagnosis.schema import DiagnosisParseError
from app.diagnosis.service import Diagnoser
from app.eval.scenarios import Scenario
from app.kube.fake import load_capture
from app.models import EvalResult, EvalRun, utcnow

_SCENARIO_ERRORS = (ModelError, DiagnosisParseError, RedactionError, TargetNotFound)


def evaluate_scenario(scenario: Scenario, model: DiagnosisModel, log_tail_lines: int) -> EvalResult:
    """Diagnose one scenario from its captured evidence and score the top hypothesis."""
    diagnoser = Diagnoser(
        EvidenceCollector(load_capture(scenario.capture_dir), log_tail_lines), model
    )
    result = EvalResult(scenario_id=scenario.id, expected_category=scenario.expected_category.value)
    started = time.monotonic()
    try:
        diagnosis = diagnoser.diagnose(scenario.diagnosis_target).diagnosis
    except _SCENARIO_ERRORS as exc:
        # A scenario the model fails on counts as a miss; it must not abort the run.
        result.error = f"{type(exc).__name__}: {exc}"
        result.passed = False
    else:
        result.predicted_category = diagnosis.top.category.value
        result.passed = diagnosis.top.category == scenario.expected_category
        result.diagnosis = diagnosis.model_dump(mode="json")
    result.latency_ms = int((time.monotonic() - started) * 1000)
    return result


def run_eval(
    scenarios: list[Scenario], model: DiagnosisModel, session: Session, log_tail_lines: int = 80
) -> EvalRun:
    run = EvalRun(model=model.name, started_at=utcnow())
    session.add(run)
    for scenario in scenarios:
        run.results.append(evaluate_scenario(scenario, model, log_tail_lines))
    run.total = len(run.results)
    run.passed = sum(1 for r in run.results if r.passed)
    run.finished_at = utcnow()
    session.commit()
    return run
