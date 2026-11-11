"""공통 로그 한 줄의 skill 등급(`grade`): 로그 값, 없으면 Agent Card 값.

화면이 조회 줄을 거를 때 쓴다.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ax_contracts.data_access.common_log import CommonLogResult, LogRecord
from ax_dashboard_api import communications
from ax_dashboard_api.main import app

URL = "/api/dashboard/communications"


@pytest.fixture(autouse=True)
def _real(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    communications.set_reader(None)
    yield
    communications.set_reader(None)


def _rec(n: int, skill: str | None, **kw: object) -> LogRecord:
    return LogRecord.model_validate(
        {
            "log_id": f"log_{n}",
            "context_id": "ctx_team_budget_2026-11",
            "occurred_at": datetime(2026, 11, 11, 8, n, tzinfo=UTC),
            "source": "org",
            "kind": "result_sent",
            "skill": skill,
            "task_status": "completed",
            "requester": "finance",
            **kw,
        }
    )


def _lines(*records: LogRecord) -> list[dict]:
    communications.set_reader(lambda q: CommonLogResult(records=list(records)))
    return TestClient(app).get(URL).json()["data"]


def test_cards_give_grades_for_read_and_write_skills() -> None:
    grades = communications.skill_grades()
    # 화면에서 탭만 열어도 뜨던 줄 (인사 팀 인원, 기억 카드 검색)은 조회
    assert grades["team_headcount"] == "read"
    assert grades["card_search"] == "read"
    # 상태를 바꾸는 일은 조회가 아니다
    assert grades["issue_coupon"] != "read"
    assert set(grades.values()) <= {"read", "A", "B", "C"}


def test_line_carries_the_card_grade() -> None:
    lines = _lines(_rec(1, "team_headcount"), _rec(2, "issue_coupon", source="coupon"))
    assert [(x["skill"], x["grade"]) for x in lines] == [
        ("team_headcount", "read"),
        ("issue_coupon", communications.skill_grades()["issue_coupon"]),
    ]


def test_logged_grade_wins_over_the_card() -> None:
    (line,) = _lines(_rec(1, "team_headcount", kind="execute_decided", grade="C"))
    assert line["grade"] == "C"


def test_unknown_skill_or_no_skill_has_no_grade() -> None:
    lines = _lines(_rec(1, "no_such_skill"), _rec(2, None))
    assert [x["grade"] for x in lines] == [None, None]
