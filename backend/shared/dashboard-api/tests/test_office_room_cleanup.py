"""대화방 정리 (11/23): 장애·이슈 방 삭제(모두에게 숨김)·/clear(나만 지우기).

- 숨기기: 사람 토큰, ctx_inc_ 방만, 총괄에 같은 세션으로 물어 해결된 사건만. 진행 중 409, 총괄 404는
  404 그대로, 401·403·5xx·시간 초과는 503(막음). 같은 회사 것만 쓰고 읽는다
- /clear: 내 것만, 같은 회사 것만
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_agent_template.testing import reset_state  # noqa: F401  (autouse 픽스처)
from ax_dashboard_api import office_room, relay
from ax_dashboard_api.main import app

BASE = "/api/dashboard/office-room"
ROOM = "ctx_inc_0123456789"
HUMAN = {"authorization": "Bearer human:shared"}


class Orchestrator:
    """총괄 `GET /api/incidents/{id}` 자리.

    방마다 상태(또는 HTTP 코드)를 정하고 받은 머리를 적는다."""

    def __init__(self) -> None:
        self.answers: dict[str, object] = {ROOM: "resolved"}
        self.seen: list[dict[str, str | None]] = []
        self.timeout = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append({"url": str(request.url), "auth": request.headers.get("authorization")})
        if self.timeout:
            raise httpx.ReadTimeout("느림", request=request)
        room = request.url.path.rsplit("/", 1)[-1]
        answer = self.answers.get(room, 404)
        if isinstance(answer, int):
            return httpx.Response(answer, json={"detail": "x"})
        return httpx.Response(200, json={"data": {"context_id": room, "status": answer}})


@pytest.fixture
def orch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("AX_SESSION_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.delenv(office_room.DB_ENV, raising=False)
    office_room.reset_store()
    fake = Orchestrator()
    relay.use_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    yield fake
    relay.use_client(None)
    office_room.reset_store()


@pytest.fixture
def client(orch: Orchestrator) -> TestClient:
    c = TestClient(app)
    c.headers.pop("authorization", None)  # conftest 기본 사람 토큰을 지우고 시험마다 정한다
    return c


def _session(company: str, name: str = "재현") -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id=f"acc_{name}", name=name, role="admin", company_id=company, company_role="admin"
    )
    return {"authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- 숨기기


def test_hide_needs_a_human_token(client: TestClient, orch: Orchestrator) -> None:
    assert client.post(f"{BASE}/{ROOM}/hide").status_code == 401
    assert client.post(f"{BASE}/{ROOM}/unhide").status_code == 401
    assert client.get(f"{BASE}/hidden").status_code == 401
    assert orch.seen == []


def test_only_incident_rooms_can_be_hidden(client: TestClient, orch: Orchestrator) -> None:
    for room in ("all", "ctx_wr_abc", "direct:dev"):
        assert client.post(f"{BASE}/{room}/hide", headers=HUMAN).status_code == 422
    assert orch.seen == []


def test_resolved_room_is_hidden_for_everyone_and_can_be_restored(
    client: TestClient, orch: Orchestrator
) -> None:
    res = client.post(f"{BASE}/{ROOM}/hide", headers=HUMAN)
    assert res.status_code == 200 and res.json()["data"]["hidden_by"] == "shared"
    # 다른 사람(같은 회사)도 같은 숨김 목록을 본다
    other = {"authorization": "Bearer human:other"}
    rows = client.get(f"{BASE}/hidden", headers=other).json()["data"]
    assert [r["room_id"] for r in rows] == [ROOM]
    assert client.post(f"{BASE}/{ROOM}/unhide", headers=other).status_code == 200
    assert client.get(f"{BASE}/hidden", headers=HUMAN).json()["data"] == []
    assert client.post(f"{BASE}/{ROOM}/unhide", headers=HUMAN).status_code == 404
    # 되돌린 방을 다시 숨길 수 있다
    assert client.post(f"{BASE}/{ROOM}/hide", headers=other).status_code == 200


@pytest.mark.parametrize("status", ["open", "degraded"])
def test_running_incident_cannot_be_hidden(
    client: TestClient, orch: Orchestrator, status: str
) -> None:
    orch.answers[ROOM] = status
    res = client.post(f"{BASE}/{ROOM}/hide", headers=HUMAN)
    assert res.status_code == 409
    assert res.json()["error"]["message"] == "진행 중인 장애 방은 삭제할 수 없어요"
    assert client.get(f"{BASE}/hidden", headers=HUMAN).json()["data"] == []


def test_orchestrator_404_stays_404(client: TestClient, orch: Orchestrator) -> None:
    res = client.post(f"{BASE}/ctx_inc_unknown/hide", headers=HUMAN)
    assert res.status_code == 404


@pytest.mark.parametrize("answer", [401, 403, 500, 503, "timeout", "weird_status"])
def test_unchecked_status_blocks_hiding(
    client: TestClient, orch: Orchestrator, answer: object
) -> None:
    if answer == "timeout":
        orch.timeout = True
    else:
        orch.answers[ROOM] = answer
    res = client.post(f"{BASE}/{ROOM}/hide", headers=HUMAN)
    assert res.status_code == 503
    body = res.text
    assert "장애 상태를 확인하지 못해 삭제하지 않았어요" in body
    assert "human:shared" not in body  # 토큰은 응답에 넣지 않는다
    assert client.get(f"{BASE}/hidden", headers=HUMAN).json()["data"] == []


def test_orchestrator_is_asked_with_the_same_login(client: TestClient, orch: Orchestrator) -> None:
    session = _session("nubitour")
    assert client.post(f"{BASE}/{ROOM}/hide", headers=session).status_code == 200
    asked = orch.seen[-1]
    assert asked["url"].endswith(f"/api/incidents/{ROOM}")
    assert asked["auth"] == session["authorization"]  # 그 사람의 머리만 그대로


def test_other_company_does_not_see_or_restore_the_hidden_room(
    client: TestClient, orch: Orchestrator
) -> None:
    """누비투어 세션이 숨긴 방은 누비투어에만 숨김으로 남는다.

    캣북스 화면·되돌리기와 섞이지 않는다."""
    nubi, cat = _session("nubitour", "누비"), _session("catbooks", "캣")
    assert client.post(f"{BASE}/{ROOM}/hide", headers=nubi).status_code == 200
    shown = client.get(f"{BASE}/hidden", headers=nubi).json()["data"]
    assert [r["room_id"] for r in shown] == [ROOM]
    assert client.get(f"{BASE}/hidden", headers=cat).json()["data"] == []
    assert client.get(f"{BASE}/hidden", headers=HUMAN).json()["data"] == []  # 세션 없음 = 캣북스
    assert client.post(f"{BASE}/{ROOM}/unhide", headers=cat).status_code == 404
    # 총괄이 그 회사 사건이 아니라고(404) 하면 그 회사로는 숨기지 못한다
    orch.answers[ROOM] = 404
    assert client.post(f"{BASE}/{ROOM}/hide", headers=cat).status_code == 404
    assert client.get(f"{BASE}/hidden", headers=cat).json()["data"] == []


# ---------------------------------------------------------------- /clear


def test_clear_is_only_mine(client: TestClient, orch: Orchestrator) -> None:
    me, you = {"authorization": "Bearer human:me"}, {"authorization": "Bearer human:you"}
    assert client.post(f"{BASE}/all/clear").status_code == 401
    res = client.post(f"{BASE}/all/clear", headers=me)
    assert res.status_code == 200 and res.json()["data"]["cleared_at"]
    assert client.post(f"{BASE}/{ROOM}/clear", headers=me).status_code == 200
    mine = client.get(f"{BASE}/clears", headers=me).json()["data"]
    assert sorted(r["room_id"] for r in mine) == sorted(["all", ROOM])
    assert client.get(f"{BASE}/clears", headers=you).json()["data"] == []
    assert client.post(f"{BASE}/all/clear/undo", headers=me).status_code == 200
    assert [r["room_id"] for r in client.get(f"{BASE}/clears", headers=me).json()["data"]] == [ROOM]
    assert orch.seen == []  # /clear는 총괄을 부르지 않는다


def test_clear_is_kept_per_company(client: TestClient, orch: Orchestrator) -> None:
    nubi, cat = _session("nubitour", "같은이름"), _session("catbooks", "같은이름")
    assert client.post(f"{BASE}/all/clear", headers=nubi).status_code == 200
    assert client.get(f"{BASE}/clears", headers=cat).json()["data"] == []
    mine = client.get(f"{BASE}/clears", headers=nubi).json()["data"]
    assert [r["room_id"] for r in mine] == ["all"]


# ---------------------------------------------------------------- 11/24 대화 목록도 회사별


def test_chat_room_threads_are_kept_per_company(client: TestClient, orch: Orchestrator) -> None:
    """캣북스 세션에서 올린 대화는 누비투어 세션의 목록·방에 보이지 않는다(반대도 같다)."""
    body = {"agent": "hr", "thread_id": "th_cat", "from_id": "acc_a", "from_name": "재현"}
    assert (
        client.post(
            "/api/dashboard/office-room", json=body, headers=_session("catbooks")
        ).status_code
        == 201
    )
    nubi = {**body, "thread_id": "th_nubi", "from_id": "acc_n"}
    assert (
        client.post(
            "/api/dashboard/office-room", json=nubi, headers=_session("nubi", "누비")
        ).status_code
        == 201
    )

    def ids(company: str) -> set[str]:
        res = client.get("/api/dashboard/office-room", headers=_session(company, "보는사람"))
        return {r["thread_id"] for r in res.json()["data"]}

    assert ids("catbooks") == {"th_cat"}
    assert ids("nubi") == {"th_nubi"}
    # 다른 회사 대화를 내 회사로 다시 올리거나 그 방을 지울 수 없다(없는 것처럼 404)
    again = client.post("/api/dashboard/office-room", json=body, headers=_session("nubi", "누비"))
    assert again.status_code == 404
    # 누비투어 방에는 acc_a가 만든 줄이 없다(캣북스 줄은 안 셈) → 403, 캣북스 대화는 그대로
    res = client.post(
        "/api/dashboard/office-room/all/remove", json={"from_id": "acc_a"}, headers=_session("nubi")
    )
    assert res.status_code == 403
    assert ids("catbooks") == {"th_cat"}
