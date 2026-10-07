import pytest
from sqlalchemy import select

from app.diagnosis.baseline import KeywordBaselineModel
from app.diagnosis.llm import ModelError
from app.diagnosis.schema import parse_diagnosis
from app.eval.__main__ import main
from app.eval.runner import evaluate_scenario, run_eval
from app.eval.scenarios import load_scenarios
from app.models import EvalResult, EvalRun
from tests.conftest import FAULTS_DIR, SCENARIOS, ScriptedModel, diagnosis_json, scenario


def test_every_scenario_is_complete_on_disk():
    for item in SCENARIOS:
        assert item.directory.name == item.id
        assert item.namespace == f"fault-{item.id}"
        for required in ("inject.yaml", "revert.sh", "captured/cluster.yaml"):
            assert (item.directory / required).is_file(), f"{item.id} is missing {required}"


def test_unknown_scenario_id_is_an_error():
    with pytest.raises(ValueError, match="no-such-fault"):
        load_scenarios(FAULTS_DIR, only=["no-such-fault"])


def test_top_hypothesis_is_scored_against_the_expected_root_cause():
    hit = evaluate_scenario(scenario("oom-killed"), ScriptedModel(diagnosis_json("oom_killed")), 80)
    miss = evaluate_scenario(
        scenario("oom-killed"), ScriptedModel(diagnosis_json("bad_command")), 80
    )

    assert (hit.passed, hit.predicted_category) == (True, "oom_killed")
    assert (miss.passed, miss.predicted_category) == (False, "bad_command")
    assert miss.expected_category == "oom_killed"


def test_only_the_top_ranked_hypothesis_counts():
    import json

    answer = json.loads(diagnosis_json("bad_command"))
    runner_up = {**answer["hypotheses"][0], "rank": 2, "category": "oom_killed"}
    answer["hypotheses"].append(runner_up)

    result = evaluate_scenario(scenario("oom-killed"), ScriptedModel(json.dumps(answer)), 80)
    assert result.passed is False


def test_run_is_stored_with_per_scenario_results(session_factory):
    scenarios = [scenario("oom-killed"), scenario("bad-image-tag")]
    with session_factory() as session:
        run = run_eval(scenarios, ScriptedModel(diagnosis_json("oom_killed")), session)
        run_id = run.id

    with session_factory() as session:
        stored = session.get(EvalRun, run_id)
        assert (stored.total, stored.passed, stored.model) == (2, 1, "scripted")
        assert stored.finished_at is not None
        assert {r.scenario_id: r.passed for r in stored.results} == {
            "bad-image-tag": False,
            "oom-killed": True,
        }


def test_model_failure_is_recorded_and_the_run_continues(session_factory):
    model = ScriptedModel(ModelError("rate limited", retryable=True), diagnosis_json("oom_killed"))
    scenarios = [scenario("bad-image-tag"), scenario("oom-killed")]
    with session_factory() as session:
        run = run_eval(scenarios, model, session)
        results = {r.scenario_id: r for r in run.results}

    assert results["bad-image-tag"].passed is False
    assert "ModelError: rate limited" in results["bad-image-tag"].error
    assert results["bad-image-tag"].predicted_category is None
    assert results["oom-killed"].passed is True


def test_unparseable_answer_counts_as_a_miss(session_factory):
    with session_factory() as session:
        run = run_eval([scenario("oom-killed")], ScriptedModel("no idea"), session)
        assert (run.total, run.passed) == (1, 0)
        assert "DiagnosisParseError" in run.results[0].error


@pytest.mark.parametrize("item", SCENARIOS, ids=lambda s: s.id)
def test_baseline_model_always_returns_a_valid_diagnosis(item):
    result = evaluate_scenario(item, KeywordBaselineModel(), 80)

    assert result.error is None
    assert parse_diagnosis_from(result).top.command_is_read_only


def parse_diagnosis_from(result: EvalResult):
    import json

    return parse_diagnosis(json.dumps(result.diagnosis))


def test_fake_mode_cli_runs_the_whole_library_and_persists_it(session_factory, tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 'test.db'}"

    exit_code = main(["--fake", "--faults-dir", str(FAULTS_DIR), "--database-url", url])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "model=keyword-baseline" in output
    assert "top-1 root cause match:" in output
    with session_factory() as session:
        run = session.scalars(select(EvalRun)).one()
        assert run.total == len(SCENARIOS) == len(run.results)


def test_cli_can_run_selected_scenarios(session_factory, tmp_path, capsys):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    args = ["--fake", "--faults-dir", str(FAULTS_DIR), "--database-url", url]

    assert main([*args, "--scenario", "oom-killed", "--scenario", "pvc-pending"]) == 0
    assert "/2" in capsys.readouterr().out
