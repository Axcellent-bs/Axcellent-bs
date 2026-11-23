"""총괄 저장소의 회사 칸 (여러 회사 1-6): 옛 DB는 캣북스로 채우고, 회사끼리 서로 안 보인다."""

from __future__ import annotations

import sqlite3

import pytest

from ax_orchestrator.chat.threads import ChatStore
from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.store import IncidentStore

# 칸을 붙이기 전(1단계 이전)의 표 모양
_OLD_SCHEMA = """
CREATE TABLE incidents (context_id TEXT PRIMARY KEY, status TEXT NOT NULL,
    opened_at TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE seen_events (event_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL);
CREATE TABLE finance_opinions (opinion_id TEXT PRIMARY KEY, received_at TEXT NOT NULL,
    body TEXT NOT NULL);
CREATE TABLE agent_diagnoses (request_id TEXT PRIMARY KEY, received_at TEXT NOT NULL,
    body TEXT NOT NULL);
CREATE TABLE work_requests (request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
    body TEXT NOT NULL);
"""
_TABLES = ("incidents", "seen_events", "finance_opinions", "agent_diagnoses", "work_requests")


def _columns(path: str, table: str) -> set[str]:
    with sqlite3.connect(path) as db:
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def test_old_store_gets_the_column_and_old_rows_become_catbooks(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    with sqlite3.connect(path) as db:
        db.executescript(_OLD_SCHEMA)
        db.execute("INSERT INTO seen_events VALUES ('evt-old', '2026-11-01T00:00:00')")
        db.execute(
            "INSERT INTO finance_opinions VALUES ('op-old', '2026-11-01T00:00:00',"
            ' \'{"opinion": {"a": 1}}\')'
        )
        db.execute(
            "INSERT INTO work_requests VALUES ('wr-old', '2026-11-01T00:00:00',"
            ' \'{"request_id": "wr-old"}\')'
        )
    store = IncidentStore(path)  # 열 때 칸을 붙인다
    for table in _TABLES:
        assert "tenant_id" in _columns(path, table)
    assert store.seen_ids() == {"evt-old"}
    assert [r["request_id"] for r in store.load_requests()] == ["wr-old"]
    assert len(store.load_opinions()) == 1
    # 같은 파일을 두 번 열어도 안전하다(멱등)
    assert IncidentStore(path).seen_ids() == {"evt-old"}
    # 다른 회사는 옛 줄을 보지 못한다
    other = IncidentStore(path, company_id="nubitour")
    assert other.seen_ids() == set()
    assert other.load_requests() == []
    assert other.load_opinions() == []


def test_alias_and_bad_company_id_fall_back_to_catbooks(tmp_path) -> None:
    assert IncidentStore(str(tmp_path / "a.db"), company_id="shop-demo").company_id == "catbooks"
    assert IncidentStore(str(tmp_path / "b.db"), company_id="Bad Id!").company_id == "catbooks"
    assert IncidentStore(str(tmp_path / "c.db")).company_id == "catbooks"


@pytest.mark.asyncio
async def test_two_companies_in_one_file_do_not_see_each_other(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    shop = IncidentStore(path)
    nubi = IncidentStore(path, company_id="nubitour")

    shop.save(new_incident())
    assert len(shop.load_all()) == 1
    assert nubi.load_all() == []

    assert shop.mark_seen("evt-1") is True
    assert nubi.seen_ids() == set()
    assert shop.seen_ids() == {"evt-1"}

    shop.save_request("wr-1", "2026-11-01T00:00:00", {"request_id": "wr-1", "v": 1})
    nubi.save_request("wr-1", "2026-11-01T00:00:00", {"request_id": "wr-1", "v": 2})  # 덮지 않는다
    assert shop.load_requests()[0]["v"] == 1
    nubi.drop_request("wr-1")  # 남의 줄은 못 지운다
    assert len(shop.load_requests()) == 1

    shop.save_diagnosis("dg-1", "2026-11-01T00:00:00", {"request_id": "dg-1", "v": 1})
    nubi.save_diagnosis("dg-1", "2026-11-02T00:00:00", {"request_id": "dg-1", "v": 2})
    assert shop.load_diagnoses()[0]["v"] == 1
    nubi.drop_diagnosis("dg-1")
    assert len(shop.load_diagnoses()) == 1

    shop.save_opinion("op-1", "2026-11-01T00:00:00", {"opinion": {"v": 1}})
    nubi.update_opinion("op-1", {"opinion": {"v": 2}})  # 남의 줄은 못 고친다
    nubi.drop_opinion("op-1")
    assert shop.load_opinions()[0]["opinion"]["v"] == 1


def test_chat_threads_are_scoped_by_company(tmp_path) -> None:
    path = str(tmp_path / "orchestrator_chat.db")
    shop = ChatStore(path)
    nubi = ChatStore(path, company_id="nubitour")
    thread = shop.create("캣북스 대화", "준하")
    assert shop.get(thread["id"]) is not None
    assert nubi.get(thread["id"]) is None
    assert nubi.list_all() == []
    assert nubi.begin_turn(thread["id"], "안녕")[0] == "missing"  # 남의 대화에는 못 쓴다
    assert nubi.remove(thread["id"], "누비") is False
    assert [t["id"] for t in shop.list_all()] == [thread["id"]]


def test_old_chat_db_gets_the_column_and_keeps_old_threads(tmp_path) -> None:
    path = str(tmp_path / "orchestrator_chat.db")
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE orchestrator_chat_threads (id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
            " title TEXT NOT NULL, status TEXT NOT NULL, who TEXT, summary TEXT NOT NULL"
            " DEFAULT '', messages TEXT NOT NULL, created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL, removed_at TEXT, removed_by TEXT)"
        )
        db.execute(
            "INSERT INTO orchestrator_chat_threads VALUES ('orchchat-1', 1, '옛 대화', 'closed',"
            " NULL, '', '[]', '2026-11-01T00:00:00', '2026-11-01T00:00:00', NULL, NULL)"
        )
    shop = ChatStore(path)
    assert "tenant_id" in _columns(path, "orchestrator_chat_threads")
    assert [t["id"] for t in shop.list_all()] == ["orchchat-1"]
    assert ChatStore(path, company_id="nubitour").list_all() == []
