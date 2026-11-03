"""조직 변동(발령) 기록: 초안 → 인사 담당자 확정 → 확정만 팀 인원에 센다 (재무 팀 예산용)."""

from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import TeamHeadcountOutput
from hr_agent.main import app
from hr_agent.org import changes as org_changes
from hr_agent.org.ledger import Change, LedgerError, counts_from_month, roster_at, validate

ORG = """
assignments:
  planning: {unit: 기획팀}
  dev: {unit: 개발팀}
  finance: {unit: 재무/정산}
"""
_keys = count(1)


def _body(skill: str, inp: dict, token: str = "mock:dashboard-api", key: str | None = None) -> dict:
    return {
        "meta": {
            "context_id": "ctx_org_0001",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key or f"k{next(_keys)}",
        },
        "skill": skill,
        "input": inp,
    }


@pytest.fixture
def client(svc, tmp_path: Path, monkeypatch) -> TestClient:
    path = tmp_path / "org.yaml"
    path.write_text(ORG, encoding="utf-8")
    monkeypatch.setenv("AX_ORG_CONFIG", str(path))
    return TestClient(app)


def draft(client, member, kind, frm, to, date, key=None) -> dict:
    inp = {"member_id": member, "kind": kind, "effective_date": date}
    if frm:
        inp["from_team"] = frm
    if to:
        inp["to_team"] = to
    return client.post("/a2a", json=_body("record_org_change", inp, key=key)).json()


def decide(client, change_id, decision="approve", token="mock:dashboard-api", key=None) -> dict:
    inp = {"change_id": change_id, "decision": decision}
    return client.post("/a2a", json=_body("decide_org_change", inp, token, key)).json()


def complete(client, monkeypatch, members: int, token: str = "mock:dashboard-api") -> dict:
    """전 직원 등록 완료 표시. 시험에서는 전체 직원 수를 `members`로 바꿔 쓴다."""
    monkeypatch.setattr(org_changes, "EXPECTED_MEMBER_COUNT", members)
    body = _body("set_org_roster_complete", {}, token)
    return client.post("/a2a", json=body).json()


def confirm(client, member, kind, frm, to, date) -> str:
    change_id = draft(client, member, kind, frm, to, date)["output"]["change"]["change_id"]
    assert decide(client, change_id)["outcome"] == "confirmed"
    return change_id


def headcount(client, month="2026-11") -> dict:
    res = client.post(
        "/a2a", json=_body("team_headcount", {"month": month}, token="mock:finance")
    ).json()
    return res


def rows(res: dict) -> dict[str, int]:
    out = TeamHeadcountOutput.model_validate(res["output"])
    return {r.team: r.headcount for r in out.rows}


# ---- 초안은 인원에 세지 않는다 ------------------------------------------------------


def test_draft_is_saved_without_approval_and_not_counted(client) -> None:
    res = draft(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    assert (res["status"], res["outcome"]) == ("completed", "drafted")
    change = res["output"]["change"]
    assert change["status"] == "draft" and change["counts_from_month"] == "2026-11"
    # 확정 기록이 없으면 가상 명부(예시) 그대로다
    hc = headcount(client)
    assert TeamHeadcountOutput.model_validate(hc["output"]).example is True


def test_confirmed_records_count_but_stay_example_until_baseline_is_complete(client) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    confirm(client, "emp_b", "join", None, "기획팀", "2026-11-01")
    confirm(client, "emp_c", "join", None, "개발팀", "2026-11-01")
    res = headcount(client)
    out = TeamHeadcountOutput.model_validate(res["output"])
    assert out.example is True
    assert rows(res) == {"기획팀": 2, "개발팀": 1, "재무/정산": 0}
    assert out.basis == "확정된 발령 기록 3건 기준, 2026-11-01 소속 (전 직원 등록 전)"


def test_marking_the_roster_complete_makes_the_count_real(client, monkeypatch) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    confirm(client, "emp_b", "join", None, "기획팀", "2026-11-01")
    confirm(client, "emp_c", "join", None, "개발팀", "2026-11-01")
    complete(client, monkeypatch, 3)
    res = headcount(client)
    out = TeamHeadcountOutput.model_validate(res["output"])
    assert out.example is False
    assert rows(res) == {"기획팀": 2, "개발팀": 1, "재무/정산": 0}
    assert out.basis == "확정된 발령 기록 3건 기준, 2026-11-01 소속"


def test_draft_and_rejected_changes_are_not_counted(client, monkeypatch) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    pending = draft(client, "emp_b", "join", None, "기획팀", "2026-11-01")["output"]["change"]
    rejected = draft(client, "emp_c", "join", None, "개발팀", "2026-11-01")["output"]["change"]
    assert decide(client, rejected["change_id"], "reject")["outcome"] == "rejected"
    complete(client, monkeypatch, 1)
    assert rows(headcount(client)) == {"기획팀": 1, "개발팀": 0, "재무/정산": 0}
    assert pending["status"] == "draft"


# ---- 월초 기준 ---------------------------------------------------------------------


def test_first_of_month_counts_that_month_otherwise_next_month(client, monkeypatch) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-10-01")
    confirm(client, "emp_a", "transfer", "기획팀", "개발팀", "2026-11-01")  # 11월부터
    confirm(client, "emp_a", "transfer", "개발팀", "재무/정산", "2026-12-15")  # 1월부터
    complete(client, monkeypatch, 1)
    assert rows(headcount(client, "2026-10"))["기획팀"] == 1
    assert rows(headcount(client, "2026-11"))["개발팀"] == 1
    assert rows(headcount(client, "2026-12"))["개발팀"] == 1
    assert rows(headcount(client, "2027-01"))["재무/정산"] == 1


def test_counts_from_month_rolls_over_the_year_end() -> None:
    assert counts_from_month("2026-12-31") == "2027-01"
    assert counts_from_month("2026-12-01") == "2026-12"
    assert counts_from_month("2026-02-28") == "2026-03"


def test_leave_removes_the_member_from_that_month(client, monkeypatch) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-10-01")
    confirm(client, "emp_b", "join", None, "개발팀", "2026-10-01")
    confirm(client, "emp_a", "leave", "기획팀", None, "2026-11-01")
    complete(client, monkeypatch, 1)
    assert rows(headcount(client, "2026-10"))["기획팀"] == 1
    assert rows(headcount(client, "2026-11"))["기획팀"] == 0


def test_month_before_the_first_record_fails_instead_of_zero(client, monkeypatch) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    complete(client, monkeypatch, 1)
    res = headcount(client, "2026-09")
    assert res["status"] == "failed" and res["outcome"] == "NO_HEADCOUNT_RECORD"
    assert "rows" not in res["output"]


# ---- 이름·ID 비노출 ----------------------------------------------------------------


def test_headcount_output_and_audit_never_carry_member_ids(svc, client) -> None:
    confirm(client, "emp_secret_77", "join", None, "기획팀", "2026-11-01")
    out = headcount(client)["output"]
    assert set(out) == {"month", "rows", "basis", "collected_at", "example"}
    assert "emp_secret_77" not in str(out)
    assert "emp_secret_77" not in str(svc.audit.list())


# ---- 중복 --------------------------------------------------------------------------


def test_same_request_twice_saves_one_draft(svc, client) -> None:
    first = draft(client, "emp_a", "join", None, "기획팀", "2026-11-01", key="same")
    second = draft(client, "emp_a", "join", None, "기획팀", "2026-11-01", key="same")
    assert second["outcome"] == "duplicate"
    assert second["output"]["change"]["change_id"] == first["output"]["change"]["change_id"]
    assert len(svc.store.all("SELECT * FROM org_changes")) == 1


def test_same_change_with_a_new_key_is_not_saved_again(svc, client) -> None:
    draft(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    again = draft(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    assert again["outcome"] == "duplicate"
    assert len(svc.store.all("SELECT * FROM org_changes")) == 1


def test_deciding_twice_does_not_change_anything(client, monkeypatch) -> None:
    change_id = draft(client, "emp_a", "join", None, "기획팀", "2026-11-01")["output"]["change"][
        "change_id"
    ]
    assert decide(client, change_id)["outcome"] == "confirmed"
    assert decide(client, change_id)["outcome"] == "duplicate"
    complete(client, monkeypatch, 1)
    assert rows(headcount(client))["기획팀"] == 1


def test_a_decided_change_cannot_be_decided_the_other_way(client, monkeypatch) -> None:
    change_id = confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    res = decide(client, change_id, "reject")
    assert res["outcome"] == "NOT_DRAFT"
    complete(client, monkeypatch, 1)
    assert rows(headcount(client))["기획팀"] == 1


# ---- 승인 -------------------------------------------------------------------------


def test_only_the_dashboard_may_decide_or_record(client) -> None:
    change_id = draft(client, "emp_a", "join", None, "기획팀", "2026-11-01")["output"]["change"][
        "change_id"
    ]
    for token in ("mock:orchestrator", "mock:finance", "mock:stranger"):
        denied = decide(client, change_id, token=token)
        assert (denied["status"], denied["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
        inp = {
            "member_id": "x",
            "kind": "join",
            "to_team": "기획팀",
            "effective_date": "2026-11-01",
        }
        res = client.post("/a2a", json=_body("record_org_change", inp, token=token)).json()
        assert (res["status"], res["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
    assert rows(headcount(client)) != {}  # 아직 확정 없음: 가상 명부 그대로


def test_a_change_that_conflicts_with_confirmed_records_is_not_confirmed(client) -> None:
    unknown = draft(client, "emp_ghost", "transfer", "기획팀", "개발팀", "2026-11-01")
    res = decide(client, unknown["output"]["change"]["change_id"])
    assert res["outcome"] == "LEDGER_CONFLICT"  # 명부에 없는 직원

    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    wrong_team = draft(client, "emp_a", "transfer", "개발팀", "재무/정산", "2026-12-01")
    assert decide(client, wrong_team["output"]["change"]["change_id"])["outcome"] == (
        "LEDGER_CONFLICT"
    )  # 지금 그 팀에 없다
    twice = draft(client, "emp_a", "join", None, "개발팀", "2026-12-01")
    assert decide(client, twice["output"]["change"]["change_id"])["outcome"] == "LEDGER_CONFLICT"


def test_unknown_change_and_unknown_team_are_refused(client) -> None:
    assert decide(client, "orgchg_none")["outcome"] == "UNKNOWN_CHANGE"
    res = draft(client, "emp_a", "join", None, "없는팀", "2026-11-01")
    assert res["outcome"] == "UNKNOWN_TEAM"


@pytest.mark.parametrize(
    "inp",
    [
        {"kind": "join", "from_team": "기획팀", "to_team": "개발팀"},
        {"kind": "transfer", "from_team": "기획팀", "to_team": "기획팀"},
        {"kind": "leave", "to_team": "기획팀"},
        {"kind": "transfer", "to_team": "기획팀"},
    ],
)
def test_teams_must_match_the_kind(client, inp) -> None:
    body = _body("record_org_change", {"member_id": "emp_a", "effective_date": "2026-11-01", **inp})
    res = client.post("/a2a", json=body).json()
    assert (res["status"], res["outcome"]) == ("rejected", "INVALID_INPUT")


@pytest.mark.parametrize("date", ["2026-13-01", "2026-11-32", "20261101", "2026-11", ""])
def test_bad_dates_are_rejected(client, date) -> None:
    inp = {"member_id": "emp_a", "kind": "join", "to_team": "기획팀", "effective_date": date}
    res = client.post("/a2a", json=_body("record_org_change", inp)).json()
    assert (res["status"], res["outcome"]) == ("rejected", "INVALID_INPUT")


def test_list_shows_status_and_filters(client) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    draft(client, "emp_b", "join", None, "개발팀", "2026-11-01")

    def listing(inp):
        body = _body("list_org_changes", inp)
        return client.post("/a2a", json=body).json()["output"]["changes"]

    assert {c["status"] for c in listing({})} == {"confirmed", "draft"}
    assert [c["status"] for c in listing({"status": "draft"})] == ["draft"]


# ---- 계산 규칙(순수 함수) ----------------------------------------------------------


def test_ledger_applies_by_month_then_receipt_order() -> None:
    a = Change("c1", "m", "join", None, "A", "2026-10")
    b = Change("c2", "m", "transfer", "A", "B", "2026-11")
    assert roster_at([b, a], "2026-10") == {"m": "A"}
    assert roster_at([b, a], "2026-11") == {"m": "B"}
    with pytest.raises(LedgerError):
        validate([Change("c3", "m", "transfer", "X", "B", "2026-11"), a])


# ---- 전 직원 등록 완료 --------------------------------------------------------------


def test_records_without_roster_complete_are_marked_example(client) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    out = TeamHeadcountOutput.model_validate(headcount(client)["output"])
    assert out.example is True  # 일부만 등록된 인원은 재무가 확정하지 못한다
    assert "전 직원 등록 전" in (out.basis or "")


def test_complete_needs_the_whole_roster_registered(client) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    res = client.post("/a2a", json=_body("set_org_roster_complete", {})).json()
    assert res["outcome"] == "ROSTER_COUNT_MISMATCH"  # 전체 직원 10명 중 1명뿐
    assert TeamHeadcountOutput.model_validate(headcount(client)["output"]).example is True


def test_ten_registered_members_can_be_marked_complete(client) -> None:
    for n in range(1, 11):
        team = ["기획팀", "개발팀", "재무/정산"][n % 3]
        confirm(client, f"emp_{n:02d}", "join", None, team, "2026-11-01")
    res = client.post("/a2a", json=_body("set_org_roster_complete", {})).json()
    assert res["outcome"] == "roster_complete"
    again = client.post("/a2a", json=_body("set_org_roster_complete", {})).json()
    assert again["outcome"] == "duplicate"
    out = TeamHeadcountOutput.model_validate(headcount(client)["output"])
    assert out.example is False and sum(r.headcount for r in out.rows) == 10


def test_complete_can_be_reopened(client, monkeypatch) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    complete(client, monkeypatch, 1)
    assert TeamHeadcountOutput.model_validate(headcount(client)["output"]).example is False
    body = _body("set_org_roster_complete", {"complete": False})
    assert client.post("/a2a", json=body).json()["outcome"] == "roster_reopened"
    assert TeamHeadcountOutput.model_validate(headcount(client)["output"]).example is True


def test_only_the_dashboard_may_mark_the_roster_complete(client, monkeypatch) -> None:
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    for token in ("mock:orchestrator", "mock:finance", "mock:stranger"):
        res = complete(client, monkeypatch, 1, token)
        assert (res["status"], res["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
    assert TeamHeadcountOutput.model_validate(headcount(client)["output"]).example is True


def test_list_reports_the_roster_state(client, monkeypatch) -> None:
    def state():
        body = _body("list_org_changes", {})
        out = client.post("/a2a", json=body).json()["output"]
        return out["registered_members"], out["expected_members"], out["roster_complete"]

    assert state() == (0, 10, False)
    confirm(client, "emp_a", "join", None, "기획팀", "2026-11-01")
    draft(client, "emp_b", "join", None, "개발팀", "2026-11-01")  # 초안은 세지 않는다
    assert state() == (1, 10, False)
    complete(client, monkeypatch, 1)
    assert state() == (1, 1, True)


def test_dashboard_may_ask_for_the_headcount_preview(client) -> None:
    res = headcount(client)
    assert res["status"] == "completed"
    body = _body("team_headcount", {"month": "2026-11"}, token="mock:dashboard-api")
    assert client.post("/a2a", json=body).json()["status"] == "completed"
