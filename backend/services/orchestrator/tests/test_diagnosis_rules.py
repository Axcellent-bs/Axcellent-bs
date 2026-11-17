"""에이전트 평가 진단 1차(규칙): 근거를 모으고 공통점을 찾아 진단하거나 근거 부족으로 둔다."""

from datetime import UTC, datetime

import pytest

from ax_contracts.schemas.diagnosis import DiagnoseAgentIssueInput
from ax_orchestrator import agent_diagnoses
from ax_orchestrator.agent_diagnoses import AgentDiagnoses
from ax_orchestrator.diagnosis_cards import CardsUnavailable
from ax_orchestrator.diagnosis_rules import collect_items, diagnose, find_commonalities
from ax_orchestrator.models import Incident, StepRun

NOW = "2026-11-17T03:00:00Z"


def _ev(name: str, *, attempts: int = 2, work: float = 41.0, verdict: str = "check") -> dict:
    return {
        "context_id": f"ctx_inc_{name}",
        "title": f"이슈 {name}",
        "verdict": verdict,
        "work_seconds": work,
        "attempts": attempts,
        "limit_seconds": 30.0,
        "reason": "다시 시도 1회" if attempts > 1 else None,
        "at": NOW,
    }


def _incident(name: str, *, event_type: str | None, reason_code: str | None = None) -> Incident:
    inc = Incident(
        context_id=f"ctx_inc_{name}",
        kind="incident",
        title=f"이슈 {name}",
        component="payment",
        event_type=event_type,
    )
    inc.steps["rollback"] = StepRun(
        step_id="rollback",
        skill="rollback_deployment",
        label="문제 배포 되돌리기",
        dept="infra",
        status="completed",
        attempts=2,
        reason_code=reason_code,
        note="재시도 뒤 성공",
    )
    return inc


def _card(name: str, *, event_type: str | None, action_code: str | None = None) -> dict:
    return {
        "card_id": f"card_{name}",
        "kind": "incident",
        "context_id": f"ctx_inc_{name}",
        "title": f"이슈 {name}",
        "extra": {
            "event_type": event_type,
            "resolution": {
                "outcome": "resolved",
                "action_code": action_code,
                "action": "배포 되돌림",
            },
        },
    }


def _row(*names: str, status: str = "received") -> dict:
    return {
        "request_id": "diag-1",
        "agent": "infra",
        "step_id": "rollback",
        "step_label": "문제 배포 되돌리기",
        "window": 3,
        "flagged": len(names),
        "criteria_version": 3,
        "evidence": [_ev(n) for n in names],
        "status": status,
        "received_at": NOW,
    }


def test_same_event_type_in_two_issues_is_a_cause_level_finding() -> None:
    incidents = {
        f"ctx_inc_{n}": _incident(n, event_type="payment:error_rate_5xx") for n in ("a", "b")
    }
    result = diagnose(_row("a", "b"), incidents, {})
    assert result["status"] == "diagnosed" and result["method"] == "rules"
    kinds = {f["kind"] for f in result["findings"]}
    assert {"event_type", "retry", "slow"} <= kinds
    assert "같은 종류의 이벤트(결제 서버 오류)에서 반복됐어요" in result["summary"]
    assert "인프라 AI의 '문제 배포 되돌리기' 단계가 최근 3건 중 2건" in result["summary"]
    # 문장이 가리킨 근거 번호는 실제 근거에 있다
    ids = {i["id"] for i in result["items"]}
    for finding in result["findings"]:
        assert set(finding["evidence_ids"]) <= ids and finding["evidence_ids"]


def test_only_time_and_retry_patterns_means_insufficient_and_no_cause_is_claimed() -> None:
    incidents = {
        "ctx_inc_a": _incident("a", event_type="payment:error_rate_5xx"),
        "ctx_inc_b": _incident("b", event_type="search:latency"),  # 서로 다른 유형
    }
    result = diagnose(_row("a", "b"), incidents, {})
    assert result["status"] == "insufficient"
    assert result["causal_count"] == 0
    assert "원인은 말하지 않아요" in result["summary"]
    assert any(f["kind"] == "retry" for f in result["findings"])  # 모양은 남긴다


def test_one_issue_cannot_make_a_common_finding_by_itself() -> None:
    incidents = {"ctx_inc_a": _incident("a", event_type="payment:error_rate_5xx")}
    cards = {
        "ctx_inc_a": _card("a", event_type="payment:error_rate_5xx", action_code="release_fault")
    }
    # 근거 이슈는 둘이지만 총괄·카드가 있는 이슈는 하나뿐이라 겹침이 아니다
    result = diagnose(_row("a", "b"), incidents, cards)
    assert result["status"] == "insufficient"
    assert any("기록이 없어" in n for n in result["notes"])


def test_archive_cards_add_action_code_findings() -> None:
    incidents = {n: _incident(n[-1], event_type=None) for n in ("ctx_inc_a", "ctx_inc_b")}
    cards = {
        f"ctx_inc_{n}": _card(n, event_type=None, action_code="rollback_deployment")
        for n in ("a", "b")
    }
    result = diagnose(_row("a", "b"), incidents, cards)
    assert result["status"] == "diagnosed"
    action = next(f for f in result["findings"] if f["kind"] == "action_code")
    assert action["value"] == "rollback_deployment" and len(action["contexts"]) == 2
    assert "같은 방식(이전 버전으로 되돌리기)으로 해결된 이슈가 반복됐어요" in result["summary"]


def test_same_reason_code_from_the_orchestrator_record_counts() -> None:
    incidents = {
        f"ctx_inc_{n}": _incident(n, event_type=None, reason_code="TIMEOUT") for n in ("a", "b")
    }
    result = diagnose(_row("a", "b"), incidents, {})
    assert result["status"] == "diagnosed"
    assert any(f["kind"] == "reason_code" and f["value"] == "TIMEOUT" for f in result["findings"])


def test_items_get_numbered_ids_and_unknown_issues_are_reported_missing() -> None:
    items, missing = collect_items(
        [_ev("a"), _ev("z")],
        "rollback",
        {"ctx_inc_a": _incident("a", event_type="x:y")},
        {"ctx_inc_a": _card("a", event_type="x:y")},
    )
    assert [i["id"] for i in items] == [f"E{n}" for n in range(1, len(items) + 1)]
    assert {i["kind"] for i in items} == {"evaluation", "orchestrator", "card"}
    assert missing == ["ctx_inc_z"]


def test_fast_or_unretried_evidence_does_not_make_retry_or_slow_findings() -> None:
    evidence = [_ev("a", attempts=1, work=5.0), _ev("b", attempts=1, work=6.0)]
    items, _ = collect_items(evidence, "rollback", {}, {})
    assert find_commonalities(items, evidence) == []


# ---- 진단 흐름 ---------------------------------------------------------------------------------
def _request(*names: str) -> DiagnoseAgentIssueInput:
    return DiagnoseAgentIssueInput.model_validate(
        {
            "agent": "infra",
            "step_id": "rollback",
            "step_label": "문제 배포 되돌리기",
            "window": 3,
            "flagged": len(names),
            "criteria_version": 3,
            "evidence": [_ev(n) for n in names],
            "requested_at": NOW,
        }
    )


@pytest.mark.asyncio
async def test_diagnose_saves_the_result_and_closes_the_request() -> None:
    diagnoses = AgentDiagnoses()
    out = diagnoses.receive(_request("a", "b"))
    incidents = {f"ctx_inc_{n}": _incident(n, event_type="payment:error_rate_5xx") for n in "ab"}
    seen: list[list[str]] = []

    async def fetch(ids: list[str], request_id: str) -> dict:
        seen.append(ids)
        return {f"ctx_inc_{n}": _card(n, event_type="payment:error_rate_5xx") for n in "ab"}

    diagnoses.cards_fetcher = fetch
    result = await diagnoses.diagnose(out.request_id, incidents)
    assert result is not None and result["status"] == "diagnosed"
    row = diagnoses.get(out.request_id)
    assert row["status"] == "diagnosed" and row["diagnosis"]["summary"] and row["diagnosed_at"]
    assert seen == [["ctx_inc_a", "ctx_inc_b"]]
    # 닫힌 요청은 다시 진단하지 않는다
    assert await diagnoses.diagnose(out.request_id, incidents) is None


@pytest.mark.asyncio
async def test_unreadable_archive_still_diagnoses_from_the_orchestrator_record() -> None:
    diagnoses = AgentDiagnoses()
    out = diagnoses.receive(_request("a", "b"))
    incidents = {f"ctx_inc_{n}": _incident(n, event_type="payment:error_rate_5xx") for n in "ab"}

    async def down(ids: list[str], request_id: str) -> dict:
        raise CardsUnavailable("꺼짐")

    diagnoses.cards_fetcher = down
    result = await diagnoses.diagnose(out.request_id, incidents)
    assert result is not None and result["status"] == "diagnosed"
    assert any("카드를 읽지 못해" in n for n in result["notes"])


@pytest.mark.asyncio
async def test_evidence_added_while_diagnosing_is_included_in_a_second_round() -> None:
    diagnoses = AgentDiagnoses()
    out = diagnoses.receive(_request("a", "b"))
    incidents = {f"ctx_inc_{n}": _incident(n, event_type="payment:error_rate_5xx") for n in "abc"}
    calls = 0

    async def fetch(ids: list[str], request_id: str) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:  # 진단하는 동안 새 근거가 들어온다
            assert diagnoses.receive(_request("c", "b")).status == "updated"
        return {}

    diagnoses.cards_fetcher = fetch
    await diagnoses.diagnose(out.request_id, incidents)
    assert calls == 2
    row = diagnoses.get(out.request_id)
    assert {i["context_id"] for i in row["diagnosis"]["items"]} == {
        "ctx_inc_a",
        "ctx_inc_b",
        "ctx_inc_c",
    }


@pytest.mark.asyncio
async def test_schedule_runs_only_when_auto_run_is_on() -> None:
    diagnoses = AgentDiagnoses()
    out = diagnoses.receive(_request("a", "b"))
    incidents = {f"ctx_inc_{n}": _incident(n, event_type="payment:error_rate_5xx") for n in "ab"}
    diagnoses.schedule(out.request_id, incidents)  # 꺼져 있으면 아무것도 안 한다
    assert diagnoses.get(out.request_id)["status"] == "received"
    diagnoses.auto_run = True
    diagnoses.schedule(out.request_id, incidents)
    for task in list(diagnoses._tasks):
        await task
    assert diagnoses.get(out.request_id)["status"] == "diagnosed"


@pytest.mark.asyncio
async def test_a_failing_diagnosis_leaves_the_request_received(monkeypatch) -> None:
    diagnoses = AgentDiagnoses()
    out = diagnoses.receive(_request("a", "b"))

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("규칙 오류")

    monkeypatch.setattr(agent_diagnoses, "diagnose_rows", boom)
    diagnoses.auto_run = True
    diagnoses.schedule(out.request_id, {})
    for task in list(diagnoses._tasks):
        await task
    assert diagnoses.get(out.request_id)["status"] == "received"


def test_stored_diagnosis_survives_a_restart(tmp_path) -> None:
    import asyncio

    from ax_orchestrator.store import IncidentStore

    store = IncidentStore(str(tmp_path / "orch.db"))
    first = AgentDiagnoses(store)
    out = first.receive(_request("a", "b"))
    incidents = {f"ctx_inc_{n}": _incident(n, event_type="payment:error_rate_5xx") for n in "ab"}
    asyncio.run(first.diagnose(out.request_id, incidents))
    store.close()

    again = AgentDiagnoses(IncidentStore(str(tmp_path / "orch.db")))
    again.restore()
    row = again.get(out.request_id)
    assert row["status"] == "diagnosed" and row["diagnosis"]["method"] == "rules"
    assert datetime.fromisoformat(row["diagnosed_at"]).tzinfo is UTC or row["diagnosed_at"]


def test_summary_and_evidence_lines_use_plain_words_without_english_codes() -> None:
    import re

    incidents = {
        f"ctx_inc_{n}": _incident(
            n, event_type="payment:error_rate_5xx", reason_code="restart_interrupted"
        )
        for n in ("a", "b")
    }
    cards = {
        f"ctx_inc_{n}": _card(n, action_code="release_fault", event_type="payment:error_rate_5xx")
        for n in ("a", "b")
    }
    result = diagnose(_row("a", "b"), incidents, cards)
    texts = [result["summary"], *(i["text"] for i in result["items"])]
    english = re.compile(r"[A-Za-z]{3,}")
    for text in texts:
        # 이슈 제목(title)은 총괄이 준 한국어 문장이라 코드가 없어야 한다
        assert not english.search(text), text
    assert "결제 서버 오류" in result["summary"]
    assert "장애 상태 해제" in result["summary"]
    assert "(E" not in result["summary"]  # 요약 문장에는 근거 번호를 늘어놓지 않는다
    assert all(i["id"].startswith("E") for i in result["items"])  # 번호는 근거 목록에서 쓴다


def test_unknown_codes_are_left_out_instead_of_shown_in_english() -> None:
    incidents = {
        f"ctx_inc_{n}": _incident(n, event_type="shipping:weird_metric") for n in ("a", "b")
    }
    result = diagnose(_row("a", "b"), incidents, {})
    assert "shipping" not in result["summary"] and "weird" not in result["summary"]
    assert "같은 종류의 이벤트에서 반복됐어요" in result["summary"]
