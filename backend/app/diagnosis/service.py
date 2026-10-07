import logging
import time

from pydantic import BaseModel

from app.diagnosis.evidence import (
    Evidence,
    EvidenceCollector,
    RedactedEvidence,
    Target,
    redact_evidence,
)
from app.diagnosis.llm import DiagnosisModel
from app.diagnosis.prompt import SYSTEM_PROMPT, build_user_prompt
from app.diagnosis.schema import Diagnosis, DiagnosisParseError, parse_diagnosis
from app.metrics import DIAGNOSES, DIAGNOSIS_SECONDS

logger = logging.getLogger(__name__)

PARSE_ATTEMPTS = 2


class DiagnosisResult(BaseModel):
    target: Target
    model: str
    diagnosis: Diagnosis
    evidence: Evidence


class Diagnoser:
    def __init__(self, collector: EvidenceCollector, model: DiagnosisModel) -> None:
        self._collector = collector
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model.name

    def collect(self, target: Target) -> RedactedEvidence:
        return redact_evidence(self._collector.collect(target))

    def diagnose(self, target: Target) -> DiagnosisResult:
        started = time.monotonic()
        try:
            evidence = self.collect(target)
            diagnosis = self._ask(build_user_prompt(evidence))
        except Exception as exc:
            DIAGNOSES.labels(type(exc).__name__).inc()
            raise
        DIAGNOSES.labels("ok").inc()
        DIAGNOSIS_SECONDS.observe(time.monotonic() - started)
        return DiagnosisResult(
            target=target, model=self._model.name, diagnosis=diagnosis, evidence=evidence
        )

    def _ask(self, prompt: str) -> Diagnosis:
        for attempt in range(1, PARSE_ATTEMPTS + 1):
            raw = self._model.complete(SYSTEM_PROMPT, prompt)
            try:
                return parse_diagnosis(raw)
            except DiagnosisParseError as exc:
                logger.warning("unusable model output (attempt %d): %s", attempt, exc)
                if attempt == PARSE_ATTEMPTS:
                    raise
        raise AssertionError("unreachable")
