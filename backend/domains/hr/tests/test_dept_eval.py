"""부서 평가 판정: 기준값 읽기, 평가 대상 배정, 경계값 판정, 카드에 판정과 기준값 버전 저장."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas import AgentRequest, RecordCase
from hr_agent.cards.service import record_case
from hr_agent.case_memory.service import record_event_times
from hr_agent.dept_eval import CriteriaError, judge_step, judge_timings, load_criteria
from hr_agent.dept_eval.criteria import DEFAULT_PATH, parse_criteria
from hr_agent.dept_eval.stages import STAGES, Excluded, Stage, stage_of

CONTEXT = "ctx_inc_0001"


def _req(skill: str, inp: dict, key: str = "k") -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CONTEXT,
                "token": "mock:orchestrator",
                "deadline": datetime.now(UTC) + timedelta(seconds=30),
                "idempotency_key": f"{CONTEXT}:{skill}:{key}",
            },
            "skill": skill,
            "input": inp,
        }
    )


def _times(**kw) -> dict:
    return {"context_id": CONTEXT, **kw}


async def _incident(svc) -> str:
    record = RecordCase.model_validate(
        {
            "context_id": CONTEXT,
            "kind": "incident",
            "summary": "결제가 서버 오류로 자꾸 실패해요",
            "decision_reason": "원인을 확정하지 못해 사람에게 넘김",
            "alternatives_considered": ["즉시 롤백"],
            "source_refs": [f"inc:{CONTEXT}", "area:infra"],
            "author": "orchestrator",
        }
    )
    result = await record_case(svc, _req("record_case", record.model_dump(mode="json"), "rc"))
    assert result.output["outcome"] == "card_created", result.output
    return result.output["card_id"]


# 총괄 장애 대응 계획(scenario_a)의 단계 번호와 늦게 도는 단계. 이 표와 어긋나면 시험이 깨진다
ORCHESTRATOR_STEPS = {
    "diagnose",
    "similar_cases",
    "impact",
    "root_cause",
    "cause_check",
    "rollback",
    "related_prs",
    "link_incident",
    "notice",
    "publish_notice",
    "compensation",
    "recovery",
    "recovered_draft",
    "recovered_notice",
    "finance_record",
    "kpi",
    "record",
    "resolution",
    "late_compensation",
}


def _t(step_id: str = "diagnose", **kw) -> dict:
    base = {"step_id": step_id, "status": "completed", "attempts": 1, "work_seconds": 1.0}
    return {**base, **kw}


def _verdict(timing: dict, criteria=None) -> str:
    result = judge_step(timing, criteria or load_criteria())
    assert result is not None
    return result["verdict"]


# ---- 기준값 파일 ----------------------------------------------------------------------------


def test_default_criteria_file_is_version_1_with_one_rule() -> None:
    criteria = load_criteria()
    assert criteria.version == 1
    assert (criteria.default.normal_max_seconds, criteria.default.normal_max_attempts) == (30, 1)
    assert criteria.groups == {}  # 모든 단계가 같은 기준이다


def test_changing_only_the_file_values_changes_the_verdict(tmp_path: Path) -> None:
    source = DEFAULT_PATH.read_text(encoding="utf-8")
    loose = tmp_path / "criteria.toml"
    loose.write_text(
        source.replace("version = 1", "version = 2").replace(
            "normal_max_seconds = 30", "normal_max_seconds = 60"
        ),
        encoding="utf-8",
    )
    timing = _t(work_seconds=45.0)
    assert _verdict(timing) == "check"
    assert _verdict(timing, load_criteria(loose)) == "normal"
    assert load_criteria(loose).version == 2


def test_a_group_rule_overrides_only_that_group() -> None:
    criteria = parse_criteria(
        {
            "version": 3,
            "default": {"normal_max_seconds": 30, "normal_max_attempts": 1},
            "group": {"LLM 분석": {"normal_max_seconds": 50}},
        }
    )
    assert _verdict(_t("root_cause", work_seconds=40.0), criteria) == "normal"
    assert _verdict(_t("impact", work_seconds=40.0), criteria) == "check"
    assert criteria.rule_for("LLM 분석").normal_max_attempts == 1  # 안 적은 값은 기본값


@pytest.mark.parametrize(
    "data",
    [
        {"default": {"normal_max_seconds": 30, "normal_max_attempts": 1}},  # version 없음
        {"version": 1},  # default 없음
        {"version": 1, "default": {"normal_max_seconds": 0, "normal_max_attempts": 1}},
        {"version": 1, "default": {"normal_max_seconds": 30, "normal_max_attempts": 0}},
        {"version": 1, "default": {"normal_max_seconds": "30", "normal_max_attempts": 1}},
    ],
)
def test_bad_criteria_values_are_errors_not_silent_defaults(data: dict) -> None:
    with pytest.raises(CriteriaError):
        parse_criteria(data)


def test_a_missing_criteria_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CriteriaError):
        load_criteria(tmp_path / "nope.toml")


# ---- 평가 대상 배정표 -------------------------------------------------------------------------


def test_every_orchestrator_step_is_either_assigned_or_excluded() -> None:
    assert set(STAGES) == ORCHESTRATOR_STEPS


def test_planning_check_and_recovery_verification_are_excluded() -> None:
    assert isinstance(STAGES["cause_check"], Excluded)
    assert isinstance(STAGES["recovery"], Excluded)
    assert stage_of("cause_check") is None and stage_of("recovery") is None


def test_dev_steps_are_counted_as_dev_even_though_the_plan_says_planning() -> None:
    for step_id in ("related_prs", "link_incident"):
        stage = STAGES[step_id]
        assert isinstance(stage, Stage) and stage.dept == "dev"


def test_unknown_steps_are_not_judged() -> None:
    assert judge_step(_t("cost"), load_criteria()) is None  # 다른 계획의 단계


# ---- 판정 경계값 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("work", "attempts", "expected"),
    [
        (0.0, 1, "normal"),
        (29.999, 1, "normal"),
        (30.0, 1, "normal"),  # 30초 정확히는 이하라 정상
        (30.001, 1, "check"),  # 30초를 조금이라도 넘으면 확인 필요
        (5.0, 2, "check"),  # 시간은 짧아도 재시도가 있었으면 확인 필요
        (95.0, 3, "check"),
    ],
)
def test_normal_only_when_within_30_seconds_and_no_retry(
    work: float, attempts: int, expected: str
) -> None:
    assert _verdict(_t(work_seconds=work, attempts=attempts)) == expected


def test_the_attempt_count_and_limit_are_kept_with_the_verdict() -> None:
    out = judge_step(_t("rollback", work_seconds=40.0, attempts=2), load_criteria())
    assert out is not None
    assert (out["attempts"], out["limit_seconds"], out["dept"], out["group"]) == (
        2,
        30.0,
        "infra",
        "되돌리기 교차검증",
    )


def test_waits_are_not_in_work_seconds_so_a_long_approval_wait_stays_normal() -> None:
    timing = _t("rollback", work_seconds=3.5, approval_wait_seconds=34.9, human_wait_seconds=None)
    assert _verdict(timing) == "normal"


# ---- 시간이 아닌 상태가 먼저 ---------------------------------------------------------------------


@pytest.mark.parametrize("status", ["failed", "rejected_by_human"])
def test_failed_or_rejected_steps_are_blocked_not_slow(status: str) -> None:
    assert _verdict(_t(status=status, work_seconds=1.0)) == "blocked"
    assert _verdict(_t(status=status, work_seconds=None)) == "blocked"


@pytest.mark.parametrize("status", ["transfer_queued", "waiting_human"])
def test_handing_a_step_to_a_person_is_neither_a_failure_nor_a_delay(status: str) -> None:
    # 원인을 확정하지 못해 사람에게 넘기는 건 설계된 동작이다. 실패·막힘이 아니다
    assert _verdict(_t("rollback", status=status, work_seconds=None)) == "handed_off"
    assert _verdict(_t("rollback", status=status, work_seconds=40.0, attempts=3)) == "handed_off"


def test_an_expired_approval_is_its_own_verdict() -> None:
    timing = _t("rollback", status="rejected_by_human", reason_code="review_timeout")
    assert _verdict(timing) == "approval_expired"


def test_skipped_or_untimed_steps_have_no_record_instead_of_delay() -> None:
    assert _verdict(_t("publish_notice", status="skipped", work_seconds=None)) == "no_record"
    assert _verdict(_t("publish_notice", status="completed", work_seconds=None)) == "no_record"


@pytest.mark.parametrize("status", ["pending", "running"])
def test_unfinished_steps_are_put_off(status: str) -> None:
    assert _verdict(_t(status=status, work_seconds=None)) == "in_progress"


def test_a_human_inbox_step_with_almost_no_work_time_is_normal() -> None:
    # 사람 할 일함으로 넘긴 단계는 대기가 거의 전부라 일한 시간이 0에 가깝다. 부서 잘못이 아니다
    timing = _t("notice", work_seconds=0.0, human_wait_seconds=600.0)
    assert _verdict(timing) == "normal"


def test_judge_timings_carries_the_version_and_drops_excluded_steps() -> None:
    out = judge_timings(
        [_t("diagnose"), _t("cause_check"), _t("recovery"), _t("cost")], load_criteria()
    )
    assert out["criteria_version"] == 1
    assert [item["step_id"] for item in out["items"]] == ["diagnose"]


# ---- 카드에 저장 ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judgments_are_saved_on_the_card_with_the_criteria_version(svc) -> None:
    card_id = await _incident(svc)
    rows = [
        _t("diagnose", dept="infra", work_seconds=1.5),
        _t("rollback", dept="infra", work_seconds=40.0, attempts=2),
        _t("recovery", dept="infra", work_seconds=120.0),
    ]
    result = await record_event_times(
        svc, _req("record_event_times", _times(step_timings=rows), key="a")
    )
    assert result.output["outcome"] == "recorded"
    saved = (await svc.memory.get_card(card_id)).extra["step_judgments"]
    assert saved["criteria_version"] == 1
    verdicts = {item["step_id"]: item["verdict"] for item in saved["items"]}
    assert verdicts == {"diagnose": "normal", "rollback": "check"}  # 복구 검증은 빠진다


@pytest.mark.asyncio
async def test_judgments_follow_the_last_step_timings(svc) -> None:
    card_id = await _incident(svc)
    first = [_t("rollback", dept="infra", work_seconds=40.0, attempts=2)]
    await record_event_times(svc, _req("record_event_times", _times(step_timings=first), key="a"))
    later = [_t("rollback", dept="infra", work_seconds=3.0, attempts=1)]
    await record_event_times(svc, _req("record_event_times", _times(step_timings=later), key="b"))
    items = (await svc.memory.get_card(card_id)).extra["step_judgments"]["items"]
    assert [i["verdict"] for i in items] == ["normal"]


@pytest.mark.asyncio
async def test_times_without_step_timings_make_no_judgments(svc) -> None:
    card_id = await _incident(svc)
    result = await record_event_times(
        svc,
        _req("record_event_times", _times(detected_at=svc.clock().isoformat()), key="a"),
    )
    assert result.output["outcome"] == "recorded"
    assert "step_judgments" not in (await svc.memory.get_card(card_id)).extra


@pytest.mark.asyncio
async def test_an_unreadable_criteria_file_does_not_block_recording_times(
    svc, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken():
        raise CriteriaError("깨진 파일")

    monkeypatch.setattr("hr_agent.case_memory.service.load_criteria", broken)
    card_id = await _incident(svc)
    rows = [_t("diagnose", dept="infra", work_seconds=1.5)]
    result = await record_event_times(
        svc, _req("record_event_times", _times(step_timings=rows), key="a")
    )
    assert result.output["outcome"] == "recorded"
    extra = (await svc.memory.get_card(card_id)).extra
    assert extra["step_timings"] and "step_judgments" not in extra
    assert CONTEXT  # 시간은 그대로 저장되고 판정만 건너뛴다
