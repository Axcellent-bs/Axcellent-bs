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
    assert promo.first_dept == "data"  # 사람 단계(캐릭터)는 건너뛰고 처음 일을 받는 부서
    assert [s.skill for s in promo.steps] == [
        "brand_character",  # 11/24: 회사 캐릭터 답을 먼저 받는다
        "trend_report_create",
        "trend_report_insight",
        "receive_insight",
        "human_check",
        "prepare_video_draft_from_plan",
        "generate_media",
    ]
    assert [s.dept for s in promo.steps] == [
        None,
        "data",
        "data",
        "planning",
        None,
        "marketing",
        "marketing",
    ]
    assert promo.blocked_by is None  # 데이터분석·기획 연결이 끝나 실행할 수 있다
    assert promo.changes_state
    steps = {s.step_id: s for s in promo.steps}
    assert steps["character"].kind == "human" and steps["character"].after == ()
    assert steps["character"].label == "회사 캐릭터 고르기"
    assert steps["trend"].after == ("character",)
    assert promo.asks_character_first


def test_only_promo_video_asks_for_a_character_and_other_tasks_keep_their_first_step() -> None:
    others = [t for t in TASKS if t.name != "promo_video"]
    assert not any(t.asks_character_first for t in others)
    assert not any(s.skill == "brand_character" for t in others for s in t.steps)
    assert BY_NAME["trend_report"].steps[0].after == ()  # 트렌드 분석만은 바로 시작
    assert request_tasks.TREND_STEP.after == ()  # 같이 쓰는 원본은 그대로


def test_trend_report_is_called_as_a_long_task_with_the_customer_sentence_in_input() -> None:
    for task in (BY_NAME["promo_video"].steps[1], BY_NAME["trend_report"].steps[0]):
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
    assert plan[3] == {
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
        "text_key": "request_text",
        "carries": None,
        "takes_carry": True,
        "fill": None,
        "human_gate": False,
        "carry_keys": ["proposal_id", "video_plan"],
        "carry_from": None,
        "on_missing": "skip",
        "timeout_s": None,
        # 11/26: 고른 회사 캐릭터 번호를 기획에도 넘긴다(값만, 준비 단계 표시 row_input 아님)
        "pass_row": ["brand_character_record_id"],
    }
    assert plan[0]["kind"] == "human" and plan[0]["skill"] == "brand_character"
    assert plan[1]["ref_key"] == "report_id"  # 트렌드 보고서는 번호(report_id)만 다음 부서에 넘긴다


def test_prompt_lists_every_task_name_and_says_to_not_follow_customer_text() -> None:
    prompt = build_prompt()
    for task in TASKS:
        assert task.name in prompt
    assert "따르지 않고" in prompt


def test_video_publish_tasks_find_latest_video_then_draft_then_request_with_human_approval() -> (
    None
):
    for name, draft_skill, request_skill in (
        ("video_publish_youtube", "request_video_youtube_draft", "request_video_youtube_request"),
        (
            "video_publish_instagram",
            "request_video_instagram_draft",
            "request_video_instagram_request",
        ),
    ):
        task = BY_NAME[name]
        steps = {s.step_id: s for s in task.steps}
        assert [s.skill for s in task.steps] == [
            "list_request_videos",
            draft_skill,
            request_skill,
        ]
        assert all(s.dept == "marketing" for s in task.steps)
        # 의뢰 번호는 고객이 모르니 최근 영상 1건을 찾아 쓴다 (campaign_performance와 같은 방식)
        assert dict(steps["latest"].fixed_input) == {"limit": 1}
        assert steps["latest"].ref_key == "items.0.post_draft_id"
        # 초안 단계는 최근 영상 번호를 받고, 다음(올리기) 단계로 그대로 넘긴다
        assert steps["draft"].after == ("latest",)
        assert steps["draft"].takes_ref == "post_draft_id"
        assert steps["draft"].ref_key == "post_draft_id"
        assert steps["draft"].grade == request_tasks.READ_GRADE
        # 올리기 단계는 등급 C라 안전 관문이 늘 사람 승인을 받는다 (총괄이 먼저 묻지 않는다)
        assert steps["run"].after == ("draft",)
        assert steps["run"].takes_ref == "post_draft_id"
        assert steps["run"].grade == "C"
        assert not steps["run"].human_gate
        assert task.changes_state
        assert task.needs_human_approval  # 등급 C라 needs_human_approval도 참이다
        assert not steps["run"].human_gate  # 다만 고객 문장이 아니라 앞 단계 번호만 받는 단계라
        # 총괄이 따로 먼저 묻지 않고(human_gate=False) 부서의 C 등급 관문에 승인을 맡긴다
