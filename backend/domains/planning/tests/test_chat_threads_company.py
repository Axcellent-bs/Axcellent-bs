"""기획 AI 대화는 회사별로 나뉜다 (11/24 회사 격리: 다른 회사에 같은 이름 계정이 있어도 안 섞임).

- 회사가 안 걸린 요청(캣북스 세션·공용 토큰)은 캣북스 대화만 본다 (남의 회사 대화가 보이지 않게)
- 정리 루틴(오래 쉰 대화 끝내기·켤 때 되살리기)만 모든 회사를 본다
- 옛 DB의 대화는 캣북스로 남는다
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from ax_agent_template import company_scope
from planning_agent.chat.threads import INTERRUPTED, ChatStore


def test_threads_are_kept_per_request_company() -> None:
    store = ChatStore()
    cat = store.create("캣북스 질문", "예진")  # 회사 안 걸림 = 캣북스
    with company_scope.using("nubi"):
        nubi = store.create("누비 질문", "예진")  # 이름이 같아도
        assert [t["id"] for t in store.list_all()] == [nubi["id"]]
        assert store.get(cat["id"]) is None
        assert store.begin_turn(cat["id"], "끼어들기") == ("missing", None)
        assert store.close(cat["id"]) is None
        assert store.hide(cat["id"], "예진") is False  # 남의 회사 대화는 못 숨긴다
    for scope in (None, "catbooks", "shop-demo"):
        with company_scope.using(scope) if scope else _nothing():
            assert [t["id"] for t in store.list_all()] == [cat["id"]], scope
            assert store.get(nubi["id"]) is None


def test_routines_reach_every_company() -> None:
    now = [datetime(2026, 11, 24, 9, tzinfo=UTC)]
    store = ChatStore(clock=lambda: now[0])
    with company_scope.using("nubi"):
        idle = store.create("쉬는 대화", "예진")
        busy = store.create("답하는 중", "예진")
        assert store.begin_turn(busy["id"], "질문")[0] == "ok"
    assert store.recover_orphans() == [busy["id"]]  # 켤 때 되살리기
    now[0] += timedelta(minutes=31)
    assert set(store.expire_idle()) == {idle["id"], busy["id"]}
    with company_scope.using("nubi"):
        got = store.get(busy["id"])
        assert got is not None and got["status"] == "closed"
        assert got["messages"][-1]["text"] == INTERRUPTED


def test_old_database_rows_become_catbooks(tmp_path) -> None:  # noqa: ANN001
    path = str(tmp_path / "planning_chat.db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE planning_chat_threads (id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
        " title TEXT NOT NULL, status TEXT NOT NULL, who TEXT, summary TEXT NOT NULL DEFAULT '',"
        " messages TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    db.execute("INSERT INTO planning_chat_threads VALUES ('planchat-1', 1, '옛 대화', 'talking',"
               " '예진', '', '[]', '2026-11-20', '2026-11-20')")  # fmt: skip
    db.commit()
    db.close()
    store = ChatStore(path)
    assert [t["id"] for t in store.list_all()] == ["planchat-1"]
    with company_scope.using("nubi"):
        assert store.list_all() == []


class _nothing:  # noqa: N801 - 회사를 걸지 않는 자리
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
