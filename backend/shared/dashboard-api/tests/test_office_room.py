"""오피스 단톡방 대화 목록: 단톡방에서 시작한 대화는 모두가 읽는 목록에 오른다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.testing import reset_state  # noqa: F401  (autouse 픽스처)
from ax_dashboard_api import office_room
from ax_dashboard_api.main import app

BASE = "/api/dashboard/office-room"
HUMAN = {"authorization": "Bearer human:shared"}


def _body(**extra) -> dict:
    return {"agent": "dev", "thread_id": "th_1", "from_id": "acc_a", "from_name": "재현", **extra}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv(office_room.DB_ENV, raising=False)
    office_room.reset_store()
    yield
    office_room.reset_store()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_added_thread_is_listed_for_everyone(client: TestClient) -> None:
    res = client.post(BASE, json=_body(), headers=HUMAN)
    assert res.status_code == 201
    assert res.json()["data"]["thread_id"] == "th_1"
    # 다른 PC(토큰 없이 읽는 화면)도 같은 목록을 본다
    rows = client.get(BASE).json()["data"]
    assert [(r["agent"], r["thread_id"], r["from_id"], r["from_name"]) for r in rows] == [
        ("dev", "th_1", "acc_a", "재현")
    ]


def test_newest_first_and_same_thread_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(office_room, "_now", lambda: start)
    client.post(BASE, json=_body(), headers=HUMAN)
    monkeypatch.setattr(office_room, "_now", lambda: start + timedelta(minutes=1))
    other = _body(agent="data", thread_id="th_2", from_id="acc_b", from_name="준하")
    client.post(BASE, json=other, headers=HUMAN)
    rows = client.get(BASE).json()["data"]
    assert [r["thread_id"] for r in rows] == ["th_2", "th_1"]

    # 같은 대화에 이어 보내면 마지막 시각만 바뀌고 맨 앞으로 온다 (보낸 사람은 처음 그대로)
    later = start + timedelta(minutes=2)
    monkeypatch.setattr(office_room, "_now", lambda: later)
    again = client.post(BASE, json=_body(from_name="다른 이름"), headers=HUMAN).json()["data"]
    assert again["from_name"] == "재현"
    assert again["created_at"] == start.isoformat()
    assert again["updated_at"] == later.isoformat()
    rows = client.get(BASE).json()["data"]
    assert [r["thread_id"] for r in rows] == ["th_1", "th_2"]
    assert len(rows) == 2


def test_same_thread_id_in_other_agent_is_separate(client: TestClient) -> None:
    client.post(BASE, json=_body(), headers=HUMAN)
    client.post(BASE, json=_body(agent="hr"), headers=HUMAN)
    assert {r["agent"] for r in client.get(BASE).json()["data"]} == {"dev", "hr"}


def test_write_needs_human_token(client: TestClient) -> None:
    client.headers.pop("authorization", None)  # conftest가 붙이는 기본 사람 토큰을 뺀다
    assert client.post(BASE, json=_body()).status_code == 401
    agent = {"authorization": "Bearer mock:inquiry"}
    assert client.post(BASE, json=_body(), headers=agent).status_code == 403
    assert client.get(BASE).json()["data"] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"agent": "safety"},
        {"thread_id": ""},
        {"from_name": ""},
        {"from_name": "가" * 41},
        {"thread_id": "t" * 65},
    ],
)
def test_bad_body_is_rejected(client: TestClient, bad: dict) -> None:
    assert client.post(BASE, json=_body(**bad), headers=HUMAN).status_code in (400, 422)
    assert client.get(BASE).json()["data"] == []


def test_old_rows_drop_out_of_window(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(office_room, "_now", lambda: start)
    client.post(BASE, json=_body(), headers=HUMAN)
    monkeypatch.setattr(office_room, "_now", lambda: start + timedelta(hours=25))
    client.post(BASE, json=_body(thread_id="th_new"), headers=HUMAN)
    assert [r["thread_id"] for r in client.get(BASE).json()["data"]] == ["th_new"]
    rows = client.get(BASE, params={"hours": 48}).json()["data"]
    assert [r["thread_id"] for r in rows] == ["th_new", "th_1"]
    # 오래된 대화도 다시 말을 보내면 목록에 돌아온다
    monkeypatch.setattr(office_room, "_now", lambda: start + timedelta(hours=25, minutes=1))
    client.post(BASE, json=_body(), headers=HUMAN)
    assert [r["thread_id"] for r in client.get(BASE).json()["data"]] == ["th_1", "th_new"]
    assert client.get(BASE, params={"hours": 0}).status_code in (400, 422)
    assert client.get(BASE, params={"hours": 169}).status_code in (400, 422)


def test_file_store_survives_reopen(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(office_room.DB_ENV, str(tmp_path / "office_room.db"))
    client.post(BASE, json=_body(), headers=HUMAN)
    office_room.reset_store()
    assert [r["thread_id"] for r in client.get(BASE).json()["data"]] == ["th_1"]
