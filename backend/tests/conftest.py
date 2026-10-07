from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, make_engine, make_session_factory
from app.eval.scenarios import Scenario, load_scenarios
from app.kube.fake import FakeCluster, load_capture

BACKEND_DIR = Path(__file__).resolve().parents[1]
FAULTS_DIR = BACKEND_DIR.parent / "faults"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

SCENARIOS = load_scenarios(FAULTS_DIR)


def scenario(scenario_id: str) -> Scenario:
    return next(s for s in SCENARIOS if s.id == scenario_id)


def capture(scenario_id: str) -> FakeCluster:
    return load_capture(scenario(scenario_id).capture_dir)


@pytest.fixture
def leaky_cluster() -> FakeCluster:
    return load_capture(FIXTURES_DIR / "leaky")


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


class ScriptedModel:
    """DiagnosisModel that replays canned answers and records the prompts it was given."""

    name = "scripted"

    def __init__(self, *answers: str | Exception) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def diagnosis_json(category: str = "oom_killed", **overrides) -> str:
    import json

    hypothesis = {
        "rank": 1,
        "category": category,
        "title": "Container exceeds its memory limit",
        "confidence": 0.8,
        "explanation": "The container is killed when it passes the limit.",
        "evidence": ["lastState.terminated.reason=OOMKilled"],
        "next_command": "kubectl describe pod report-worker -n fault-oom-killed",
    }
    hypothesis.update(overrides)
    return json.dumps({"summary": "The worker runs out of memory.", "hypotheses": [hypothesis]})
