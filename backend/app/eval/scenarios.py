from pathlib import Path

import yaml
from pydantic import BaseModel

from app.diagnosis.evidence import Target, TargetKind
from app.diagnosis.schema import RootCause


class ScenarioTarget(BaseModel):
    kind: TargetKind
    name: str


class Scenario(BaseModel):
    id: str
    title: str
    description: str
    namespace: str
    target: ScenarioTarget
    expected_category: RootCause
    # Strings the collected evidence must contain for the fault to be diagnosable at all.
    expected_signals: list[str]
    directory: Path

    @property
    def capture_dir(self) -> Path:
        return self.directory / "captured"

    @property
    def diagnosis_target(self) -> Target:
        return Target(kind=self.target.kind, namespace=self.namespace, name=self.target.name)


def load_scenarios(faults_dir: Path, only: list[str] | None = None) -> list[Scenario]:
    scenarios = []
    for path in sorted(faults_dir.glob("*/scenario.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        scenarios.append(Scenario(**data, directory=path.parent))
    if only:
        unknown = set(only) - {s.id for s in scenarios}
        if unknown:
            raise ValueError(f"unknown scenario ids: {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.id in only]
    return scenarios
