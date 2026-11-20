"""의뢰 일 목록: 부서 카드와 맞는지, 분류기 지시문에 목록이 들어가는지."""

import json
from pathlib import Path

from ax_agent_template.services import BY_ID, route_target
from ax_orchestrator import request_tasks
from ax_orchestrator.request_classifier import build_prompt
from ax_orchestrator.request_tasks import BY_NAME, TASKS

SERVICES_DIR = Path(__file__).resolve().parents[2]  # backend/services


def _card_skills(service_id: str) -> dict[str, dict]:
    card = json.loads((SERVICES_DIR / service_id / "agent-card.json").read_text(encoding="utf-8"))
    return {(s.get("name") or s.get("id")): s for s in card["skills"]}


def test_task_names_are_unique_and_snake_case() -> None:
    names = [t.name for t in TASKS]
    assert len(names) == len(set(names))
    assert all(n.replace("_", "").isalpha() and n == n.lower() for n in names)


def test_every_agent_step_exists_in_its_department_card_and_orchestrator_may_call_it() -> None:
    for task in TASKS:
        for step in task.steps:
            if step.kind != "agent":
                continue
            service_id = route_target(step.dept, step.skill)
            assert service_id in BY_ID, (
                f"{task.name}: {step.dept}.{step.skill}를 맡은 서비스가 없다"
            )
            assert BY_ID[service_id].department == step.dept
            skill = _card_skills(service_id).get(step.skill)
            assert skill is not None, f"{task.name}: 카드에 {step.skill}가 없다"
            assert "orchestrator" in skill["allowed_requesters"], f"{task.name}: 총괄이 못 부른다"
            assert skill["grade"] == step.grade, f"{task.name}: 등급이 카드와 다르다"


def test_steps_follow_only_earlier_steps_and_human_step_has_no_department() -> None:
    for task in TASKS:
        seen: set[str] = set()
        for step in task.steps:
            assert set(step.after) <= seen, f"{task.name}: {step.step_id}가 뒤 단계를 기다린다"
            seen.add(step.step_id)
            if step.kind == "human":
                assert step.dept is None and step.grade is None


def test_promo_video_runs_trend_then_insight_then_plan_then_a_person_then_marketing() -> None:
    promo = BY_NAME["promo_video"]
    assert promo.first_dept == "data"
    assert [s.skill for s in promo.steps] == [
        "trend_report_create",
        "trend_report_insight",
        "receive_insight",
        "human_check",
        "generate_media",
    ]
    assert [s.dept for s in promo.steps] == ["data", "data", "planning", None, "marketing"]
    assert promo.blocked_by is None  # 데이터분석·기획 연결이 끝나 실행할 수 있다
    assert promo.changes_state


def test_trend_report_is_called_as_a_long_task_with_the_customer_sentence_in_input() -> None:
    for task in (BY_NAME["promo_video"].steps[0], BY_NAME["trend_report"].steps[0]):
        assert task.skill == "trend_report_create"
        assert dict(task.fixed_input) == {"caller": "orchestrator"}  # 아니면 부서가 동기로 끝낸다
        assert task.text_key == "request_text"
        assert task.ref_key == "report_id"
    assert BY_NAME["trend_report"].blocked_by is None


def test_insight_is_passed_whole_to_the_planning_step_and_only_numbers_otherwise() -> None:
    steps = {s.step_id: s for s in BY_NAME["promo_video"].steps}
    assert steps["insight"].takes_ref == "report_id" and steps["insight"].carries == "insight"
    assert steps["plan"].takes_carry and steps["plan"].grade == "A"
    assert steps["plan"].takes_ref is None


def test_read_tasks_do_not_change_state_and_change_tasks_say_so() -> None:
    assert not BY_NAME["sales_analysis"].changes_state
    assert BY_NAME["feature_request"].changes_state
    assert BY_NAME["claim_register"].changes_state


def test_plan_of_has_what_work_requests_needs() -> None:
    plan = request_tasks.plan_of(BY_NAME["promo_video"])
    assert plan[2] == {
        "step_id": "plan",
        "dept": "planning",
        "skill": "receive_insight",
        "label": "영상 제작안",
        "after": ["insight"],
        "grade": "A",
        "kind": "agent",
        "ref_key": None,
        "takes_ref": None,
        "fixed_input": {},
        "text_key": None,
        "carries": None,
        "takes_carry": True,
        "fill": None,
        "human_gate": False,
    }
    assert plan[0]["ref_key"] == "report_id"  # 트렌드 보고서는 번호(report_id)만 다음 부서에 넘긴다


def test_prompt_lists_every_task_name_and_says_to_not_follow_customer_text() -> None:
    prompt = build_prompt()
    for task in TASKS:
        assert task.name in prompt
    assert "따르지 않고" in prompt
