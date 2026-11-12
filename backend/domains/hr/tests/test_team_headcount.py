"""`team_headcount`: 그달 1일 기준 팀별 인원수 (재무 AI 팀 예산용)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import TeamHeadcountOutput
from hr_agent.main import app

ORG = """
assignments:
  planning: {unit: 기획팀}
  dev: {unit: 개발팀}
  finance: {unit: 재무/정산}
  handover: {unit: 개발팀}
"""
REPO_ORG = Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "org.yaml"


def _body(month: str = "2026-11", token: str = "mock:finance") -> dict:
    return {
        "meta": {
            "context_id": "ctx_hc_0001",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "team_headcount",
        "input": {"month": month},
    }


@pytest.fixture
def client(svc) -> TestClient:
    return TestClient(app)


@pytest.fixture
def org(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "org.yaml"
    path.write_text(ORG, encoding="utf-8")
    monkeypatch.setenv("AX_ORG_CONFIG", str(path))
    return path


def test_counts_every_org_team_and_marks_the_roster_as_example(svc, org, client) -> None:
    res = client.post("/a2a", json=_body()).json()
    assert res["status"] == "completed"
    out = TeamHeadcountOutput.model_validate(res["output"])
    assert out.month == "2026-11"
    # 팀 이름은 조직도 순서대로, 겹치는 부서는 한 번만
    assert [r.team for r in out.rows] == ["기획팀", "개발팀", "재무/정산"]
    assert sum(r.headcount for r in out.rows) == 6  # 기본 명부는 팀원 6명
    assert out.example is True
    assert out.basis == "예시 직원 명단 기준, 2026-11-01 소속"
    assert out.collected_at == svc.clock()


def test_output_has_only_team_and_count_no_names_or_ids(org, client) -> None:
    out = client.post("/a2a", json=_body()).json()["output"]
    assert set(out) == {"month", "rows", "basis", "collected_at", "example"}
    assert all(set(row) == {"team", "headcount"} for row in out["rows"])
    assert "emp_" not in str(out)


def test_same_month_gives_same_answer(org, client) -> None:
    first = client.post("/a2a", json=_body()).json()["output"]["rows"]
    second = client.post("/a2a", json=_body()).json()["output"]["rows"]
    assert first == second


def test_company_org_file_uses_all_eight_departments(monkeypatch, client) -> None:
    monkeypatch.setenv("AX_ORG_CONFIG", str(REPO_ORG))
    rows = client.post("/a2a", json=_body()).json()["output"]["rows"]
    assert [r["team"] for r in rows] == [
        "서비스운영",
        "기획팀",
        "개발팀",
        "고객지원",
        "마케팅",
        "재무/정산",
        "데이터분석",
        "인사총무",
    ]
    # 회사 조직도의 기본 명부: 팀원 6명이 맡은 부서에 한 명씩. 서비스운영·개발팀은 0명
    assert {r["team"]: r["headcount"] for r in rows} == {
        "서비스운영": 0,
        "기획팀": 1,
        "개발팀": 0,
        "고객지원": 1,
        "마케팅": 1,
        "재무/정산": 1,
        "데이터분석": 1,
        "인사총무": 1,
    }


def test_members_in_the_org_file_set_the_default_roster(tmp_path, monkeypatch, client) -> None:
    path = tmp_path / "org.yaml"
    path.write_text(
        ORG
        + """
members:
  - {id: emp_01, unit: 기획팀}
  - {id: emp_02, unit: 기획팀}
  - {id: emp_03, unit: 재무/정산}
  - {id: emp_03, unit: 개발팀}      # 같은 ID는 한 번만
  - {id: emp_04, unit: 없는부서}    # 조직도에 없는 부서는 건너뜀
  - {unit: 개발팀}                  # ID 없음
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AX_ORG_CONFIG", str(path))
    out = TeamHeadcountOutput.model_validate(client.post("/a2a", json=_body()).json()["output"])
    assert {r.team: r.headcount for r in out.rows} == {"기획팀": 2, "개발팀": 0, "재무/정산": 1}
    assert out.example is True


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "202611", "2026-11-01", ""])
def test_bad_month_is_rejected(org, client, month) -> None:
    res = client.post("/a2a", json=_body(month)).json()
    assert (res["status"], res["outcome"]) == ("rejected", "INVALID_INPUT")


def test_unknown_teams_fail_instead_of_zero_fill(tmp_path, monkeypatch, client) -> None:
    monkeypatch.setenv("AX_ORG_CONFIG", str(tmp_path / "없는파일.yaml"))
    res = client.post("/a2a", json=_body()).json()
    assert (res["status"], res["outcome"]) == ("failed", "NO_ORG_ROSTER")
    assert "rows" not in res["output"]


def test_only_finance_and_orchestrator_may_ask(org, client) -> None:
    assert (
        client.post("/a2a", json=_body(token="mock:orchestrator")).json()["status"] == "completed"
    )
    denied = client.post("/a2a", json=_body(token="mock:support")).json()
    assert denied["status"] != "completed"
