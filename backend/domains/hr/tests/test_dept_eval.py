"""부서 평가 판정: 기준값 읽기, 평가 대상 배정, 경계값 판정, 카드에 판정과 기준값 버전 저장."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas import AgentRequest, JudgeStepTimingsOutput
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
    "end_notice",
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


def test_default_criteria_file_is_version_3_with_one_rule() -> None:
    criteria = load_criteria()
    assert criteria.version == 3
    assert criteria.default.max_silence_seconds == 300.0  # 응답 끊김 기준
    assert (criteria.default.normal_max_seconds, criteria.default.normal_max_attempts) == (30, 1)
    assert criteria.default.use_promised is True  # 단계의 기준 시간을 기준으로 삼는다
    assert criteria.groups == {}  # 모든 단계가 같은 기준이다


def test_changing_only_the_file_values_changes_the_verdict(tmp_path: Path) -> None:
    source = DEFAULT_PATH.read_text(encoding="utf-8")
    loose = tmp_path / "criteria.toml"
    loose.write_text(
        source.replace("version = 3", "version = 4").replace(
            "normal_max_seconds = 30", "normal_max_seconds = 60"
        ),
        encoding="utf-8",
    )
    timing = _t(work_seconds=45.0)
    assert _verdict(timing) == "check"
    assert _verdict(timing, load_criteria(loose)) == "normal"
    assert load_criteria(loose).version == 4


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
    assert isinstance(STAGES["end_notice"], Excluded)  # 복구 신호 뒤에 도는 사후 정리
    assert stage_of("cause_check") is None and stage_of("recovery") is None
    assert stage_of("end_notice") is None


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
    assert out["criteria_version"] == 3
    assert [item["step_id"] for item in out["items"]] == ["diagnose"]


# ---- 단계별 기준 시간(기준값 v2) ----------------------------------------------------------------


def test_the_promised_time_is_the_limit_when_the_step_has_one() -> None:
    """짧은 일은 더 엄하게, 긴 일은 더 넉넉하게 잰다. 30초 하나로 재지 않는다."""
    assert _verdict(_t("diagnose", work_seconds=12.0, promised_seconds=10.0)) == "check"
    assert _verdict(_t("diagnose", work_seconds=10.0, promised_seconds=10.0)) == "normal"
    assert _verdict(_t("impact", work_seconds=45.0, promised_seconds=60.0)) == "normal"
    assert _verdict(_t("impact", work_seconds=61.0, promised_seconds=60.0)) == "check"


def test_a_step_without_a_promise_keeps_the_default_30_seconds() -> None:
    assert _verdict(_t("impact", work_seconds=29.0)) == "normal"
    assert _verdict(_t("impact", work_seconds=31.0)) == "check"
    assert _verdict(_t("impact", work_seconds=31.0, promised_seconds=None)) == "check"


@pytest.mark.parametrize("bad", [0, -5, 0.0])
def test_a_nonsense_promise_is_ignored_not_trusted(bad: float) -> None:
    out = judge_step(_t("impact", work_seconds=40.0, promised_seconds=bad), load_criteria())
    assert out is not None
    assert (out["verdict"], out["limit_seconds"], out["promised"]) == ("check", 30.0, False)


def test_a_retry_is_still_a_check_even_within_the_promised_time() -> None:
    assert _verdict(_t("impact", work_seconds=40.0, attempts=2, promised_seconds=60.0)) == "check"


def test_the_limit_and_where_it_came_from_are_kept_with_the_verdict() -> None:
    kept = judge_step(_t("impact", work_seconds=45.0, promised_seconds=60.0), load_criteria())
    assert kept is not None
    assert (kept["limit_seconds"], kept["promised"]) == (60.0, True)
    plain = judge_step(_t("impact", work_seconds=45.0), load_criteria())
    assert plain is not None
    assert (plain["limit_seconds"], plain["promised"]) == (30.0, False)


def test_version_1_style_criteria_ignore_the_promise(tmp_path: Path) -> None:
    """use_promised를 끄면 옛 방식(모든 단계 30초)으로 판정한다."""
    source = DEFAULT_PATH.read_text(encoding="utf-8")
    old = tmp_path / "criteria.toml"
    old.write_text(source.replace("use_promised = true", "use_promised = false"), encoding="utf-8")
    timing = _t("impact", work_seconds=45.0, promised_seconds=60.0)
    assert _verdict(timing) == "normal"
    assert _verdict(timing, load_criteria(old)) == "check"


def test_use_promised_must_be_true_or_false() -> None:
    with pytest.raises(CriteriaError):
        parse_criteria(
            {
                "version": 2,
                "default": {
                    "normal_max_seconds": 30,
                    "normal_max_attempts": 1,
                    "use_promised": "yes",
                },
            }
        )


# ---- 긴 작업의 응답 끊김(기준값 v3) ---------------------------------------------------------


def test_a_long_task_with_steady_notices_is_not_silent_however_long_it_took() -> None:
    """총 시간이 길어도 응답 사이가 기준(300초)보다 짧았으면 끊김이 아니다."""
    timing = _t("impact", work_seconds=7200.0, promised_seconds=9000.0, max_silence_seconds=120.0)
    assert _verdict(timing) == "normal"


def test_a_gap_longer_than_the_limit_is_silent_even_when_the_task_finished_on_time() -> None:
    timing = _t("impact", work_seconds=600.0, promised_seconds=900.0, max_silence_seconds=301.0)
    assert _verdict(timing) == "silent"
    ok = _t("impact", work_seconds=600.0, promised_seconds=900.0, max_silence_seconds=300.0)
    assert _verdict(ok) == "normal"  # 기준 정확히는 이하


def test_the_orchestrator_cutting_a_silent_step_is_silent_not_a_plain_failure() -> None:
    cut = _t("impact", status="failed", reason_code="no_response", max_silence_seconds=301.0)
    assert _verdict(cut) == "silent"
    plain = _t("impact", status="failed")
    assert _verdict(plain) == "blocked"  # 끊김이 아니라 그냥 실패


def test_a_running_long_task_that_went_silent_is_flagged_before_it_ends() -> None:
    running = _t("impact", status="waiting_async", work_seconds=None, max_silence_seconds=400.0)
    assert _verdict(running) == "silent"
    quiet = _t("impact", status="waiting_async", work_seconds=None, max_silence_seconds=30.0)
    assert _verdict(quiet) == "in_progress"


def test_a_step_without_long_task_data_is_judged_by_time_alone() -> None:
    assert _verdict(_t("impact", work_seconds=10.0)) == "normal"
    assert _verdict(_t("impact", work_seconds=10.0, max_silence_seconds=None)) == "normal"


def test_the_silence_is_kept_with_the_verdict() -> None:
    out = judge_step(_t("impact", work_seconds=5.0, max_silence_seconds=42.0), load_criteria())
    assert out is not None and out["max_silence_seconds"] == 42.0


def test_the_silence_limit_must_be_a_positive_number() -> None:
    for bad in (0, -1, "300", True):
        with pytest.raises(CriteriaError):
            parse_criteria(
                {
                    "version": 3,
                    "default": {
                        "normal_max_seconds": 30,
                        "normal_max_attempts": 1,
                        "max_silence_seconds": bad,
                    },
                }
            )


# ---- 판정 skill (아카이브가 이슈 시각을 기록할 때 부른다) ---------------------------------------


@pytest.mark.asyncio
async def test_the_judge_skill_returns_the_verdicts_with_the_criteria_version(svc) -> None:
    from hr_agent.dept_eval.skill import judge_step_timings

    rows = [
        _t("diagnose", dept="infra", work_seconds=1.5),
        _t("rollback", dept="infra", work_seconds=40.0, attempts=2),
        _t("recovery", dept="infra", work_seconds=120.0),
    ]
    result = await judge_step_timings(svc, _req("judge_step_timings", {"step_timings": rows}))
    assert result.status.value == "completed" and result.outcome == "judged"
    out = JudgeStepTimingsOutput.model_validate(result.output)
    assert out.criteria_version == 3
    assert {i["step_id"]: i["verdict"] for i in out.items} == {
        "diagnose": "normal",
        "rollback": "check",
    }  # 복구 검증은 빠진다


@pytest.mark.asyncio
async def test_the_judge_skill_carries_the_promise_into_the_verdict(svc) -> None:
    from hr_agent.dept_eval.skill import judge_step_timings

    rows = [_t("impact", dept="data", work_seconds=45.0, promised_seconds=60.0)]
    result = await judge_step_timings(svc, _req("judge_step_timings", {"step_timings": rows}))
    (item,) = JudgeStepTimingsOutput.model_validate(result.output).items
    assert (item["verdict"], item["limit_seconds"], item["promised"]) == ("normal", 60.0, True)


@pytest.mark.asyncio
async def test_an_unreadable_criteria_file_is_a_failure_not_a_guess(
    svc, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hr_agent.dept_eval import skill

    def broken():
        raise CriteriaError("깨진 파일")

    monkeypatch.setattr(skill, "load_criteria", broken)
    rows = [_t("diagnose", dept="infra", work_seconds=1.5)]
    result = await skill.judge_step_timings(svc, _req("judge_step_timings", {"step_timings": rows}))
    assert result.status.value == "failed" and result.outcome == "CRITERIA_UNREADABLE"


@pytest.mark.asyncio
async def test_bad_input_is_rejected_by_the_judge_skill(svc) -> None:
    from hr_agent.dept_eval.skill import judge_step_timings

    result = await judge_step_timings(svc, _req("judge_step_timings", {"step_timings": "x"}))
    assert result.status.value == "rejected"
