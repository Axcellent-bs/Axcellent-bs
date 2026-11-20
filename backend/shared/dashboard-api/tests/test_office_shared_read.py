"""단톡방(전체 대화방·이슈 방)에 올라온 대화는 로그인한 누구나 원문을 읽는다 (11/20).

- 중계 부서 8곳의 상세(GET /<부서>-chat/{id}): 단톡방 줄이 있으면 건 사람 이름으로 부서에 묻고
  shared·owner를 붙인다. 1:1 대화·숨긴 줄·예전 줄(이름 없음)은 지금처럼 본인만
- 이어 쓰기·끝내기는 요청한 사람 그대로라 부서의 본인 검사(forbidden 403)가 그대로 막는다
- 단톡방에 처음 올릴 때 그 대화가 올리는 사람 것인지 부서로 확인한다(남의 1:1 대화 → 403)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_dashboard_api import (
    chat_read,
    data_chat,
    dev_chat,
    finance_chat,
    infra_chat,
    marketing_client,
    office_room,
    orchestrator_chat,
    planning_chat,
    support_client,
)
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.main import app

OWNER, OTHER = "재현", "태윤"
TOKENS = {OWNER: "tok_owner", OTHER: "tok_other"}
AGENTS = ["marketing", "planning", "finance", "orchestrator", "data", "infra", "dev", "support"]
ROOM = "/api/dashboard/office-room"


def _h(who: str) -> dict[str, str]:
    return {"authorization": f"Bearer {TOKENS[who]}"}


class Department:
    """부서 대화 서버 자리: 대화 하나(owner 소유). 열람·쓰기 규칙은 부서와 같다(본인만)."""

    def __init__(self) -> None:
        self.asked: list[str] = []  # 상세를 물은 who

    async def get_thread(self, agent: str, thread_id: str, who: str, auth: Any) -> dict:
        self.asked.append(who)
        if thread_id != "th_1":
            raise ApiError(404, "NOT_FOUND", "없는 대화")
        if who == OWNER:
            return {"id": thread_id, "who": OWNER, "restricted": False, "messages": [{"t": "원문"}]}
        return {"id": thread_id, "who": OWNER, "restricted": True, "messages": []}

    async def write(self, *args: Any, **kwargs: Any) -> dict:
        payload = next(a for a in args if isinstance(a, dict))
        who = payload.get("who") or payload.get("viewer")
        if who != OWNER:
            raise ApiError(403, "FORBIDDEN", "다른 사람의 대화에는 쓸 수 없다")
        return {"thread": {"id": payload["thread_id"], "status": "closed"},
                "id": payload["thread_id"]}  # fmt: skip


@pytest.fixture
def dept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Department:
    d = Department()
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", ",".join(f"{n}:{t}" for n, t in TOKENS.items()))
    monkeypatch.setenv(office_room.DB_ENV, str(tmp_path / "office.db"))
    office_room.reset_store()
    monkeypatch.setattr(chat_read, "get_thread", d.get_thread)
    for mod in (data_chat, dev_chat, finance_chat, infra_chat, orchestrator_chat, planning_chat):
        monkeypatch.setattr(mod, "call", d.write)
    monkeypatch.setattr(marketing_client, "chat_call", d.write)
    monkeypatch.setattr(support_client, "call", d.write)
    yield d
    office_room.reset_store()


def _post_room(client: TestClient, agent: str, who: str = OWNER, thread: str = "th_1") -> Any:
    body = {"agent": agent, "thread_id": thread, "from_id": "acc", "from_name": who}
    return client.post(ROOM, json=body, headers=_h(who))


@pytest.mark.parametrize("agent", AGENTS)
def test_room_thread_is_readable_by_others_but_not_writable(agent: str, dept: Department) -> None:
    client = TestClient(app)
    assert _post_room(client, agent).status_code == 201
    got = client.get(f"/api/dashboard/{agent}-chat/th_1", headers=_h(OTHER)).json()["data"]
    assert got["restricted"] is False and got["messages"] == [{"t": "원문"}]
    assert got["shared"] is True and got["owner"] == OWNER
    assert dept.asked[-1] == OWNER  # 건 사람 이름으로 부서에 물었다
    # 다른 계정의 끝내기는 부서의 본인 검사 그대로 403
    res = client.post(f"/api/dashboard/{agent}-chat/th_1/close", headers=_h(OTHER))
    assert res.status_code == 403


@pytest.mark.parametrize("agent", AGENTS)
def test_one_to_one_thread_stays_private(agent: str, dept: Department) -> None:
    client = TestClient(app)
    got = client.get(f"/api/dashboard/{agent}-chat/th_1", headers=_h(OTHER)).json()["data"]
    assert got["restricted"] is True and got["messages"] == []
    assert got["shared"] is False and got["owner"] is None
    mine = client.get(f"/api/dashboard/{agent}-chat/th_1", headers=_h(OWNER)).json()["data"]
    assert mine["restricted"] is False and mine["shared"] is False


@pytest.mark.parametrize("agent", AGENTS)
def test_hidden_room_row_is_not_shared(agent: str, dept: Department) -> None:
    client = TestClient(app)
    assert _post_room(client, agent).status_code == 201
    office_room.get_store().hide("all", [(agent, "th_1")], "acc", office_room._now())  # noqa: SLF001
    got = client.get(f"/api/dashboard/{agent}-chat/th_1", headers=_h(OTHER)).json()["data"]
    assert got["restricted"] is True and got["shared"] is False


@pytest.mark.parametrize("agent", AGENTS)
def test_posting_someone_elses_thread_is_refused(agent: str, dept: Department) -> None:
    client = TestClient(app)
    res = _post_room(client, agent, who=OTHER)  # 남의 1:1 대화 번호를 단톡방에 올림
    assert res.status_code == 403 and res.json()["error"]["code"] == "NOT_THREAD_OWNER"
    assert office_room.get_store().get(agent, "th_1") is None  # 줄을 넣지 않음
    missing = _post_room(client, agent, thread="th_none")
    assert missing.status_code == 403


def test_department_down_is_503_and_no_row(
    dept: Department, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(*args: Any, **kwargs: Any) -> dict:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "응답 없음")

    monkeypatch.setattr(chat_read, "get_thread", down)
    res = _post_room(TestClient(app), "data")
    assert res.status_code == 503 and office_room.get_store().get("data", "th_1") is None


def test_hr_safety_archive_are_not_checked_here(dept: Department) -> None:
    """HTTP로 직접 받는 3곳은 부서 서버가 room_id로 정한다 (여기서는 확인하지 않고 줄만 넣는다)."""
    for agent in ("hr", "safety", "archive"):
        assert _post_room(TestClient(app), agent, thread=f"{agent}-1").status_code == 201
    assert dept.asked == []


def test_owner_who_is_saved_once_and_read_who(dept: Department) -> None:
    client = TestClient(app)
    _post_room(client, "data")
    _post_room(client, "data", who=OTHER)  # 이미 있는 줄: 마지막 시각만 (건 사람 그대로)
    row = office_room.get_store().get("data", "th_1")
    assert row is not None and row["owner_who"] == OWNER
    assert office_room.read_who("data", "th_1", OTHER) == OWNER
    assert office_room.read_who("data", "th_2", OTHER) == OTHER  # 1:1이면 그대로


def test_old_db_gets_owner_who_column_and_old_rows_stay_private(
    dept: Department, tmp_path: Path
) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE office_threads (agent TEXT NOT NULL, thread_id TEXT NOT NULL,"
                 " from_id TEXT NOT NULL, from_name TEXT NOT NULL, created_at TEXT NOT NULL,"
                 " updated_at TEXT NOT NULL, PRIMARY KEY (agent, thread_id))")  # fmt: skip
    conn.execute("INSERT INTO office_threads VALUES ('data','th_old','acc','재현',"
                 "'2026-11-19T00:00:00+00:00','2026-11-19T00:00:00+00:00')")  # fmt: skip
    conn.commit()
    conn.close()
    store = office_room.OfficeRoomStore(str(path))
    office_room.OfficeRoomStore(str(path))  # 두 번째 열기도 안전
    row = store.get("data", "th_old")
    assert row is not None and row["owner_who"] is None  # 예전 줄은 채우지 않는다
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(office_threads)")}
    assert "owner_who" in cols
