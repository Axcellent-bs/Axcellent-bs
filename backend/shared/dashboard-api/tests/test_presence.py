"""근무 상태·휴가 화면 경로: 스위치 기본 꺼짐, 상태는 모두에게, 상세·기록은 사람 토큰만."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import hr_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard"
NOW = "2026-11-09T09:00:00Z"
LEAVE = {
    "leave_id": "lv_1",
    "member_id": "emp_01",
    "start_date": "2026-11-09",
    "end_date": "2026-11-10",
    "recorded_by": "tester",
    "recorded_at": NOW,
}


class Fake:
    def __init__(self) -> None:
        self.sent: list[AgentRequest] = []
        self.outputs: dict[str, dict[str, Any]] = {}
        self.overrides: dict[str, dict[str, Any]] = {}

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "hr"
        self.sent.append(req)
        fields = {"status": TaskStatus.COMPLETED, "output": self.outputs.get(req.skill, {})}
        return AgentResult(meta=req.meta, **{**fields, **self.overrides.get(req.skill, {})})

    def last(self, skill: str) -> AgentRequest:
        return [r for r in self.sent if r.skill == skill][-1]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    fake = Fake()
    fake.outputs["get_presence"] = {
        "entries": [
            {"area": "payment", "role": "owner", "member_id": "emp_01", "status": "on_leave"}
        ],
        "leaves": [LEAVE],
        "collected_at": NOW,
    }
    fake.outputs["record_leave"] = {"leave": LEAVE}
    monkeypatch.setattr(hr_client, "SEND", fake)
    return fake


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_PRESENCE_ENABLED", "1")


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


def _anonymous(client: TestClient) -> TestClient:
    client.headers.pop("authorization", None)
    return client


def test_스위치를_켜지_않으면_읽기도_기록도_닫혀_있다(
    client: TestClient, fake: Fake, monkeypatch
) -> None:
    monkeypatch.delenv("DASHBOARD_PRESENCE_ENABLED", raising=False)
    for value in (None, "0", "true", ""):
        if value is not None:
            monkeypatch.setenv("DASHBOARD_PRESENCE_ENABLED", value)
        read = client.get(BASE + "/presence")
        write = client.post(
            BASE + "/presence/leaves",
            json={"member_id": "emp_01", "start_date": "2026-11-09", "end_date": "2026-11-09"},
        )
        assert (read.status_code, write.status_code) == (404, 404)
        assert read.json()["error"]["code"] == "PRESENCE_DISABLED"
    assert fake.sent == []


def test_사람_토큰이_없으면_상태만_받고_상세는_요청하지_않는다(
    client: TestClient, fake: Fake, on: None
) -> None:
    fake.outputs["get_presence"]["leaves"] = []
    data = _anonymous(client).get(BASE + "/presence").json()["data"]
    assert data["entries"][0]["status"] == "on_leave"
    assert fake.last("get_presence").input == {"include_details": False}


def test_사람_토큰이_있으면_상세를_요청한다(client: TestClient, fake: Fake, on: None) -> None:
    data = client.get(BASE + "/presence", params={"area": "payment"}).json()["data"]
    assert data["leaves"][0]["recorded_by"] == "tester"
    assert fake.last("get_presence").input == {"include_details": True, "area": "payment"}


def test_에이전트_토큰이나_틀린_토큰은_상세를_받지_못한다(
    client: TestClient, fake: Fake, on: None
) -> None:
    for token in ("Bearer mock:finance", "Bearer wrong"):
        client.headers["authorization"] = token
        assert client.get(BASE + "/presence").status_code == 200
        assert fake.last("get_presence").input["include_details"] is False


def test_기록은_사람_토큰이_필요하고_입력자는_토큰의_이름이다(
    client: TestClient, fake: Fake, on: None
) -> None:
    body = {"member_id": "emp_01", "start_date": "2026-11-09", "end_date": "2026-11-10"}
    assert _anonymous(client).post(BASE + "/presence/leaves", json=body).status_code == 401
    client.headers["authorization"] = "Bearer mock:finance"
    assert client.post(BASE + "/presence/leaves", json=body).status_code == 403
    assert fake.sent == []

    client.headers["authorization"] = "Bearer test-human-token"
    res = client.post(BASE + "/presence/leaves", json=body)
    assert res.status_code == 200 and res.json()["data"]["leave"]["leave_id"] == "lv_1"
    sent = fake.last("record_leave")
    assert sent.input == body | {"recorded_by": "tester"}
    assert sent.meta.idempotency_key and sent.meta.idempotency_key.endswith(
        ":record_leave:leave:emp_01:2026-11-09:2026-11-10"
    )


def test_본문에_적은_입력자나_사유는_받지_않는다(client: TestClient, fake: Fake, on: None) -> None:
    base = {"member_id": "emp_01", "start_date": "2026-11-09", "end_date": "2026-11-10"}
    for extra in ({"recorded_by": "someone"}, {"reason": "병가"}, {"kind": "annual"}):
        assert client.post(BASE + "/presence/leaves", json=base | extra).status_code == 422
    for bad in ({"start_date": "11/09"}, {"end_date": "2026-13-01"}, {"member_id": ""}):
        assert client.post(BASE + "/presence/leaves", json=base | bad).status_code == 422
    assert fake.sent == []


def test_없는_사람은_404_같은_기간_기록은_그대로_성공(
    client: TestClient, fake: Fake, on: None
) -> None:
    body = {"member_id": "emp_99", "start_date": "2026-11-09", "end_date": "2026-11-10"}
    fake.overrides["record_leave"] = {"outcome": "UNKNOWN_MEMBER", "output": {"message": "없다"}}
    assert client.post(BASE + "/presence/leaves", json=body).status_code == 404
    fake.overrides["record_leave"] = {"outcome": "duplicate"}
    assert (
        client.post(BASE + "/presence/leaves", json=body | {"member_id": "emp_01"}).status_code
        == 200
    )


def test_목_모드에는_근무_상태가_없다(client: TestClient, on: None, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    assert client.get(BASE + "/presence").status_code == 501


# ---- 취소 (cancel_leave) ---------------------------------------------------------------
CANCELLED = LEAVE | {"cancelled_by": "tester", "cancelled_at": NOW}


def test_취소는_사람_토큰이_필요하고_취소한_사람은_토큰의_이름이다(
    client: TestClient, fake: Fake, on: None
) -> None:
    fake.outputs["cancel_leave"] = {"leave": CANCELLED}
    url = BASE + "/presence/leaves/lv_1/cancel"
    assert _anonymous(client).post(url).status_code == 401
    client.headers["authorization"] = "Bearer mock:finance"
    assert client.post(url).status_code == 403
    assert fake.sent == []

    client.headers["authorization"] = "Bearer test-human-token"
    res = client.post(url)
    assert res.status_code == 200
    assert res.json()["data"]["leave"]["cancelled_by"] == "tester"
    sent = fake.last("cancel_leave")
    assert sent.input == {"leave_id": "lv_1", "cancelled_by": "tester"}
    assert sent.meta.idempotency_key and sent.meta.idempotency_key.endswith(
        ":cancel_leave:leave-cancel:lv_1"
    )


def test_없는_휴가는_404_이미_취소된_기록은_성공(client: TestClient, fake: Fake, on: None) -> None:
    url = BASE + "/presence/leaves/lv_nope/cancel"
    fake.overrides["cancel_leave"] = {"outcome": "UNKNOWN_LEAVE", "output": {"message": "없다"}}
    assert client.post(url).status_code == 404
    fake.overrides["cancel_leave"] = {"outcome": "duplicate"}
    fake.outputs["cancel_leave"] = {"leave": CANCELLED}
    assert client.post(BASE + "/presence/leaves/lv_1/cancel").status_code == 200


def test_취소도_스위치가_꺼져_있으면_닫혀_있다(client: TestClient, fake: Fake, monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_PRESENCE_ENABLED", raising=False)
    res = client.post(BASE + "/presence/leaves/lv_1/cancel")
    assert (res.status_code, res.json()["error"]["code"]) == (404, "PRESENCE_DISABLED")
    assert fake.sent == []


def test_취소는_본문을_받지_않고_너무_긴_ID는_거절한다(
    client: TestClient, fake: Fake, on: None
) -> None:
    fake.outputs["cancel_leave"] = {"leave": CANCELLED}
    assert client.post(BASE + "/presence/leaves/" + "x" * 65 + "/cancel").status_code == 422
    assert fake.sent == []
