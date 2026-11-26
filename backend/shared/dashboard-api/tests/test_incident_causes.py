"""장애 상세의 확정 원인 (SPEC-30 R1·R3): 저장·읽기, 회사 분리, 입력 검사.

- 쓰기는 로그인 세션만(누가 저장했는지 남긴다). 공용 토큰은 403, 토큰 없음은 401
- 다시 저장하면 지금 것이 바뀌고 앞의 것은 history로 남는다
- 같은 장애 번호라도 회사마다 따로: 다른 회사는 읽지도 덮지도 못한다
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_agent_template.testing import reset_state  # noqa: F401  (autouse 픽스처)
from ax_dashboard_api import incident_causes
from ax_dashboard_api.main import app

URL = "/api/dashboard/incidents/{}/cause"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("AX_SESSION_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.delenv(incident_causes.DB_ENV, raising=False)
    incident_causes.reset_store()
    c = TestClient(app)
    c.headers.pop("authorization", None)  # conftest 기본 사람 토큰을 지우고 시험마다 정한다
    yield c
    incident_causes.reset_store()


def _session(company: str, name: str = "재현", role: str = "member") -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id=f"acc_{company}_{name}",
        name=name,
        role=role,
        company_id=company,
        company_role=role if role in ("admin", "member") else None,
    )
    return {"authorization": f"Bearer {token}"}


def test_empty_until_someone_confirms(client: TestClient) -> None:
    res = client.get(URL.format(7), headers=_session("catbooks"))
    assert res.status_code == 200
    assert res.json()["data"] == {"incident_id": 7, "confirmed": None, "history": []}


def test_save_then_read_records_who_and_when(client: TestClient) -> None:
    who = _session("catbooks", "예진")
    body = {"text": "  디스크가 가득 차 결제 로그를 못 썼다  ", "from_rank": 2}
    res = client.post(URL.format(7), json=body, headers=who)
    assert res.status_code == 200
    got = res.json()["data"]["confirmed"]
    assert got["text"] == "디스크가 가득 차 결제 로그를 못 썼다"
    assert got["from_rank"] == 2
    assert got["saved_by"] == "예진"
    assert got["saved_at"]
    assert "account_id" not in got

    again = client.get(URL.format(7), headers=_session("catbooks", "다른사람"))
    assert again.json()["data"]["confirmed"] == got


def test_resave_replaces_and_keeps_history(client: TestClient) -> None:
    who = _session("catbooks")
    client.post(URL.format(7), json={"text": "첫 판단", "from_rank": 1}, headers=who)
    res = client.post(URL.format(7), json={"text": "다시 보니 설정 누락"}, headers=who)
    data = res.json()["data"]
    assert data["confirmed"]["text"] == "다시 보니 설정 누락"
    assert data["confirmed"]["from_rank"] is None
    assert [h["text"] for h in data["history"]] == ["첫 판단"]
    assert data["history"][0]["replaced_at"] == data["confirmed"]["saved_at"]


def test_other_incident_is_separate(client: TestClient) -> None:
    who = _session("catbooks")
    client.post(URL.format(7), json={"text": "7번 원인"}, headers=who)
    assert client.get(URL.format(8), headers=who).json()["data"]["confirmed"] is None


def test_other_company_cannot_read_or_overwrite(client: TestClient) -> None:
    cat, nubi = _session("catbooks", "캣"), _session("nubitour", "누비")
    client.post(URL.format(7), json={"text": "캣북스 원인"}, headers=cat)

    # 다른 회사는 같은 번호를 읽어도 비어 있다
    assert client.get(URL.format(7), headers=nubi).json()["data"]["confirmed"] is None

    # 다른 회사가 같은 번호로 저장해도 자기 회사 기록에만 남는다
    client.post(URL.format(7), json={"text": "누비투어 원인"}, headers=nubi)
    mine = client.get(URL.format(7), headers=cat).json()["data"]
    assert mine["confirmed"]["text"] == "캣북스 원인"
    assert mine["history"] == []
    theirs = client.get(URL.format(7), headers=nubi).json()["data"]
    assert theirs["confirmed"]["text"] == "누비투어 원인"


def test_write_needs_a_login_session(client: TestClient) -> None:
    body = {"text": "원인"}
    assert client.post(URL.format(7), json=body).status_code == 401
    # 공용·목 사람 토큰은 누가 쓰는지 몰라 저장하지 않는다
    shared = {"authorization": "Bearer human:shared"}
    assert client.post(URL.format(7), json=body, headers=shared).status_code == 403
    assert client.get(URL.format(7), headers=shared).json()["data"]["confirmed"] is None
    # 에이전트 토큰은 읽기도 안 된다
    agent = {"authorization": "Bearer mock:orchestrator"}
    assert client.get(URL.format(7), headers=agent).status_code == 403


def test_read_needs_a_human_token(client: TestClient) -> None:
    assert client.get(URL.format(7)).status_code == 401


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"text": "   "}, "CAUSE_EMPTY"),
        ({"text": "가" * (incident_causes.TEXT_MAX + 1)}, "CAUSE_TOO_LONG"),
    ],
)
def test_text_is_checked(client: TestClient, body: dict, code: str) -> None:
    res = client.post(URL.format(7), json=body, headers=_session("catbooks"))
    assert res.status_code == 422
    assert res.json()["error"]["code"] == code
    saved = client.get(URL.format(7), headers=_session("catbooks")).json()["data"]
    assert saved["confirmed"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": "원인", "from_rank": 0},
        {"text": "원인", "from_rank": incident_causes.RANK_MAX + 1},
        {"text": "원인", "who": "다른 사람"},  # 저장한 사람은 화면이 정하지 않는다
        {"text": "가" * (incident_causes.TEXT_MAX * 2 + 1)},
    ],
)
def test_bad_body_is_422(client: TestClient, body: dict) -> None:
    res = client.post(URL.format(7), json=body, headers=_session("catbooks"))
    assert res.status_code == 422


@pytest.mark.parametrize("incident", ["0", "-1", "abc", str(2**31)])
def test_bad_incident_id_is_422(client: TestClient, incident: str) -> None:
    who = _session("catbooks")
    assert client.get(URL.format(incident), headers=who).status_code == 422
    assert client.post(URL.format(incident), json={"text": "원인"}, headers=who).status_code == 422


def test_saved_file_survives_restart(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(incident_causes.DB_ENV, str(tmp_path / "incident_causes.db"))
    who = _session("catbooks")
    client.post(URL.format(7), json={"text": "파일에 남는 원인"}, headers=who)
    incident_causes.reset_store()  # 다시 켠 것처럼
    got = client.get(URL.format(7), headers=who).json()["data"]["confirmed"]
    assert got["text"] == "파일에 남는 원인"


# --- 인프라 AI가 읽는 창구 (읽기 전용, 에이전트 토큰) ---

READ = "/api/dashboard/incident-causes"


def _infra(company: str | None = "catbooks", token: str = "mock:infra") -> dict[str, str]:
    headers = {"authorization": f"Bearer {token}"}
    if company is not None:
        headers["X-AX-Company"] = company
    return headers


def _save(
    client: TestClient, company: str, incident_id: int, text: str, name: str = "재현"
) -> None:
    res = client.post(URL.format(incident_id), json={"text": text}, headers=_session(company, name))
    assert res.status_code == 200, res.text


def test_infra_agent_reads_only_current_confirmed_causes_of_its_company(client: TestClient) -> None:
    _save(client, "catbooks", 7, "첫 원인")
    _save(client, "catbooks", 7, "고친 원인", name="태윤")
    _save(client, "nubitour", 7, "다른 회사 원인")
    _save(client, "nubitour", 8, "다른 회사만 있음")
    res = client.get(READ, params={"ids": "7,8,9"}, headers=_infra("catbooks"))
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert set(data) == {"7"}  # 8은 다른 회사, 9는 없음
    assert data["7"]["text"] == "고친 원인" and data["7"]["saved_by"] == "태윤"
    assert set(data["7"]) == {"text", "from_rank", "saved_by", "saved_at"}  # history·계정 ID 없음
    other = client.get(READ, params={"ids": "7,8"}, headers=_infra("nubitour")).json()["data"]
    assert {k: v["text"] for k, v in other.items()} == {
        "7": "다른 회사 원인",
        "8": "다른 회사만 있음",
    }


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"authorization": "Bearer mock:finance", "X-AX-Company": "catbooks"}, 403),
        ({"authorization": "Bearer nope", "X-AX-Company": "catbooks"}, 403),
        (_infra(None), 400),  # 회사 머리글 없음: 캣북스로 떨어지지 않는다
        (_infra("Bad Company!"), 400),
    ],
)
def test_read_window_refuses_wrong_caller_or_missing_company(
    client: TestClient, headers: dict[str, str], status: int
) -> None:
    _save(client, "catbooks", 7, "원인")
    res = client.get(READ, params={"ids": "7"}, headers=headers)
    assert res.status_code == status, res.text


def test_read_window_refuses_human_tokens(client: TestClient) -> None:
    headers = _session("catbooks") | {"X-AX-Company": "catbooks"}
    assert client.get(READ, params={"ids": "7"}, headers=headers).status_code == 403
    shared = {"authorization": "Bearer human:jaehun", "X-AX-Company": "catbooks"}
    assert client.get(READ, params={"ids": "7"}, headers=shared).status_code == 403


@pytest.mark.parametrize(
    "ids", ["", "a", "0", "1,,2", "-3", ",".join(str(i) for i in range(1, 22))]
)
def test_read_window_checks_ids(client: TestClient, ids: str) -> None:
    res = client.get(READ, params={"ids": ids}, headers=_infra())
    assert res.status_code == 422, res.text


def test_read_window_cannot_write_and_human_route_still_needs_a_human(client: TestClient) -> None:
    for method in ("post", "put", "delete", "patch"):
        res = getattr(client, method)(READ, headers=_infra())
        assert res.status_code in (404, 405), (method, res.status_code)
    agent_on_human = client.get(URL.format(7), headers=_infra())  # 사람 라우터는 약해지지 않았다
    assert agent_on_human.status_code == 403
    assert client.post(URL.format(7), json={"text": "x"}, headers=_infra()).status_code == 403
    assert client.get(URL.format(7)).status_code == 401
