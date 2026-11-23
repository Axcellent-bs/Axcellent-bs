"""여러 회사 1-5 ①: 회사 키워드로 만드는 data 저장소에 회사 칸 (11/23).

표마다: 두 회사(catbooks·nubi)로 쓰면 서로 안 보인다 · 옛 DB(칸 없음)를 열면 기존 줄이 catbooks ·
두 번 열어도 같다(멱등). 회사는 skill 요청이면 meta.tenant_id, 아니면 지금 회사(tenant.current()).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import TrendEvidence, TrendVerdict
from data_agent import company_scope as cs
from data_agent import keyword_pool as kp
from data_agent.today_board import TodayBoard

DAY = date(2026, 11, 23)
AT = datetime(2026, 11, 23, 1, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AX_TENANT", "AX_COMPANY_CONNECTION_FILE", "AX_COMPANIES_DIR",
                 "AX_COMPANY_FOLDERS", "AX_IG_RADAR_AUTO_FILE"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)


def _old(path: Path, *sql: str) -> None:
    """칸 없는 옛 DB 픽스처."""
    conn = sqlite3.connect(path)
    for s in sql:
        conn.execute(s)
    conn.commit()
    conn.close()


def _keys(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return cs.key_columns(conn, table)
    finally:
        conn.close()


def _rows(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------- 회사 정하기


def test_scope_id_aliases_and_unknown_values_become_catbooks() -> None:
    assert cs.scope_id("catbooks") == "catbooks"
    assert cs.scope_id("shop-demo") == "catbooks"  # 별칭은 읽기만, 쓰기는 정식 id
    assert cs.scope_id("nubi") == "nubi"
    for bad in (None, "", "Nubi!", "../x", 3):
        assert cs.scope_id(bad) == "catbooks"  # type: ignore[arg-type]


def test_active_is_the_request_company_then_the_current_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cs.active() == "catbooks"  # 연결 전
    monkeypatch.setenv("AX_TENANT", "nubi")  # 스스로 도는 루틴: 지금 회사
    assert cs.current_id() == cs.active() == "nubi"
    with cs.using("shop-demo"):
        assert cs.active() == "catbooks"  # 요청의 회사가 먼저
    assert cs.active() == "nubi"


def test_scoped_handler_uses_the_request_meta_tenant() -> None:
    seen: list[str] = []

    async def handler(request: AgentRequest) -> AgentResult:
        seen.append(cs.active())
        await asyncio.sleep(0)
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED)

    run = cs.scoped(handler)

    def req(tenant: str) -> AgentRequest:
        meta = RequestMeta(context_id="ctx_t", token="x", tenant_id=tenant,
                           deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
        return AgentRequest(meta=meta, skill="x", input={})

    async def both() -> None:
        await asyncio.gather(run(req("nubi")), run(req("shop-demo")))

    asyncio.run(both())
    assert sorted(seen) == ["catbooks", "nubi"]
    assert cs.active() == "catbooks"  # 요청이 끝나면 되돌린다


def test_the_server_handlers_are_scoped_but_handlers_stay_the_same_objects() -> None:
    from data_agent import handlers

    wrapped = handlers.scoped_handlers()
    assert set(wrapped) == set(handlers.HANDLERS)
    assert wrapped["basket_pairs"] is not handlers.HANDLERS["basket_pairs"]
    assert wrapped["basket_pairs"].__wrapped__ is handlers.HANDLERS["basket_pairs"]


def test_rebuild_is_one_transaction_keeps_rows_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    _old(path, "CREATE TABLE t (day TEXT PRIMARY KEY, v TEXT)",
         "INSERT INTO t VALUES ('2026-11-01', 'a')")  # fmt: skip
    create = ("CREATE TABLE IF NOT EXISTS {table} (day TEXT NOT NULL, v TEXT,"
              " tenant_id TEXT NOT NULL DEFAULT 'catbooks', PRIMARY KEY (tenant_id, day))")
    conn = sqlite3.connect(path)
    assert cs.rebuild_with_tenant_key(conn, "t", create) is True
    assert cs.rebuild_with_tenant_key(conn, "t", create) is False  # 두 번째는 건너뜀
    assert cs.key_columns(conn, "t") == ["tenant_id", "day"]
    assert conn.execute("SELECT day, v, tenant_id FROM t").fetchall() == [
        ("2026-11-01", "a", "catbooks")]
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert names == ["t"]  # 임시 표가 남지 않는다
    conn.close()


def test_rebuild_failure_rolls_back_and_keeps_the_old_table(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    _old(path, "CREATE TABLE t (day TEXT PRIMARY KEY, v TEXT)",
         "INSERT INTO t VALUES ('2026-11-01', 'a')")  # fmt: skip
    bad = "CREATE TABLE {table} (other TEXT, tenant_id TEXT, PRIMARY KEY (tenant_id, other))"
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.OperationalError):
        cs.rebuild_with_tenant_key(conn, "t", bad)  # 칸이 안 맞아 복사 실패
    assert conn.execute("SELECT day, v FROM t").fetchall() == [("2026-11-01", "a")]
    assert cs.key_columns(conn, "t") == ["day"]
    conn.close()


# ---------------------------------------------------------------- keyword_pool


def _pool(word: str) -> kp.PoolRow:
    return kp.PoolRow(word, 100, 200, False, False, "높음", "related", "교육·책")


def test_keyword_pool_is_per_company(tmp_path: Path) -> None:
    path = tmp_path / "keyword_pool.db"
    cat, nubi = kp.PoolStore(path, "catbooks"), kp.PoolStore(path, "nubi")
    cat.save(DAY, [_pool("고양이 책")], "ok", None, 1, 1, AT)
    assert not nubi.done(DAY) and cat.done(DAY)
    nubi.save(DAY, [_pool("해외여행"), _pool("고양이 책")], "ok", None, 2, 1, AT)
    cat.save_candidates(DAY, [("고양이 책", ["pool"])])
    nubi.save_candidates(DAY, [("해외여행", ["pool"])])
    cat.save_judgment(DAY, "고양이 책", "rising", 2.0, 1.0, [(DAY, 3.0)], AT)
    nubi.save_judgment(DAY, "해외여행", "rising", 3.0, 1.0, [(DAY, 3.0)], AT)
    assert [r.keyword for r in kp.latest(path, "catbooks")[1]] == ["고양이 책"]
    assert {r.keyword for r in kp.latest(path, "nubi")[1]} == {"해외여행", "고양이 책"}
    assert [r.keyword for r in kp.by_day(path, DAY, DAY, "catbooks")[DAY]] == ["고양이 책"]
    assert cat.candidates(DAY) == [("고양이 책", ["pool"])]
    assert [j["keyword"] for j in cat.judgments(DAY, DAY)] == ["고양이 책"]
    assert [j["keyword"] for j in nubi.judgments(DAY, DAY)] == ["해외여행"]
    with cs.using("nubi"):  # 넘긴 값이 없으면 지금 요청의 회사
        assert kp.PoolStore(path).company_id == "nubi"
        assert {r.keyword for r in kp.latest(path)[1]} == {"해외여행", "고양이 책"}
    for s in (cat, nubi):
        s.close()


def test_old_keyword_pool_db_becomes_catbooks_and_reopening_is_the_same(tmp_path: Path) -> None:
    path = tmp_path / "keyword_pool.db"
    _old(
        path,
        "CREATE TABLE pool (day TEXT NOT NULL, keyword TEXT NOT NULL, pc INTEGER, mobile INTEGER,"
        " pc_below_10 INTEGER NOT NULL, mobile_below_10 INTEGER NOT NULL, competition TEXT,"
        " source TEXT NOT NULL, category TEXT, PRIMARY KEY (day, keyword))",
        "CREATE TABLE runs (day TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT,"
        " hints INTEGER NOT NULL, calls INTEGER NOT NULL, at TEXT NOT NULL)",
        "CREATE TABLE rising_candidates (day TEXT NOT NULL, position INTEGER NOT NULL,"
        " keyword TEXT NOT NULL, sources TEXT NOT NULL, PRIMARY KEY (day, position))",
        "CREATE TABLE rising_judgments (day TEXT NOT NULL, keyword TEXT NOT NULL,"
        " status TEXT NOT NULL, ratio REAL, baseline REAL, points_json TEXT NOT NULL,"
        " judged_at TEXT NOT NULL, score REAL, reason TEXT, PRIMARY KEY (day, keyword))",
        "INSERT INTO pool VALUES ('2026-11-23', '고양이 책', 1, 2, 0, 0, NULL, 'related', NULL)",
        "INSERT INTO runs VALUES ('2026-11-23', 'ok', NULL, 1, 1, '2026-11-23T01:00:00+00:00')",
        "INSERT INTO rising_candidates VALUES ('2026-11-23', 0, '고양이 책', '[\"pool\"]')",
        "INSERT INTO rising_judgments VALUES ('2026-11-23', '고양이 책', 'rising', 2, 1, '[]',"
        " '2026-11-23T01:00:00+00:00', NULL, NULL)",
    )
    assert [r.keyword for r in kp.latest(path, "catbooks")[1]] == ["고양이 책"]  # 옮기기 전 읽기
    assert kp.latest(path, "nubi") == (None, [])
    for _ in range(2):
        store = kp.PoolStore(path, "catbooks")
        assert store.done(DAY) and store.candidates(DAY) == [("고양이 책", ["pool"])]
        store.close()
    assert _keys(path, "pool") == ["tenant_id", "day", "keyword"]
    assert _keys(path, "runs") == ["tenant_id", "day"]
    assert _keys(path, "rising_candidates") == ["tenant_id", "day", "position"]
    assert _keys(path, "rising_judgments") == ["tenant_id", "day", "keyword"]
    assert _rows(path, "SELECT keyword, tenant_id FROM pool") == [("고양이 책", "catbooks")]
    nubi = kp.PoolStore(path, "nubi")
    assert not nubi.done(DAY) and nubi.candidates(DAY) == []
    nubi.close()


# ---------------------------------------------------------------- 오늘 보드


def _verdict(name: str) -> TrendVerdict:
    return TrendVerdict(group_name=name, status="rising", ratio=3.0,
                        evidence=TrendEvidence(score=5))  # fmt: skip


def test_today_board_is_per_company_in_memory_and_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "today_board.db"
    board = TodayBoard(today=lambda: DAY, path=path)
    with cs.using("nubi"):
        board.record_verdict(["해외여행"], _verdict("해외여행"))
        board.record_pair("여행", "travel")
        nubi_stamp = board.stamp()
    with cs.using("catbooks"):
        assert board.rising_names() == [] and board.pairs() == []
        assert board.verdict(["해외여행"]) is None
        board.record_verdict(["고양이 책"], _verdict("고양이 책"))
        assert board.stamp() != nubi_stamp
    again = TodayBoard(today=lambda: DAY, path=path)  # 다시 켜도 회사마다 그대로
    with cs.using("nubi"):
        assert again.rising_names() == ["해외여행"] and again.pairs() == [("여행", "travel")]
    assert again.rising_names() == ["고양이 책"]  # 요청 밖 = 지금 회사(연결 전이면 캣북스)
    assert _keys(path, "board") == ["tenant_id", "day", "kind", "key"]


def test_old_today_board_db_becomes_catbooks(tmp_path: Path) -> None:
    path = tmp_path / "today_board.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE board (day TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,"
                 " value TEXT NOT NULL, PRIMARY KEY (day, kind, key))")  # fmt: skip
    conn.execute("INSERT INTO board VALUES (?, 'verdict', '고양이책', ?)",
                 (DAY.isoformat(), _verdict("고양이 책").model_dump_json()))  # fmt: skip
    conn.commit()
    conn.close()
    for _ in range(2):
        board = TodayBoard(today=lambda: DAY, path=path)
        assert board.rising_names() == ["고양이 책"]
        with cs.using("nubi"):
            assert board.rising_names() == []
    assert _rows(path, "SELECT key, tenant_id FROM board") == [("고양이책", "catbooks")]


def test_nubi_keywords_do_not_show_on_the_catbooks_board(tmp_path: Path) -> None:
    """화면 경로: 누비투어가 낸 급상승 판정이 캣북스 trend_popular 배지에 붙지 않는다."""
    spec = importlib.util.spec_from_file_location(
        "trend_popular_fakes_cs", Path(__file__).with_name("test_trend_popular.py"))
    assert spec is not None and spec.loader is not None
    fakes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fakes)
    board = TodayBoard()
    with cs.using("nubi"):
        board.record_verdict(["t1"], _verdict("t1"))
        assert {i.keyword: i for i in fakes._run(fakes._env(tmp_path / "n"), board=board).items}[  # noqa: SLF001
            "t1"].rising_now is True
    with cs.using("catbooks"):
        out = fakes._run(fakes._env(tmp_path / "c"), board=board)  # noqa: SLF001
    assert all(i.rising_now is None for i in out.items)


# ---------------------------------------------------------------- domain_profile


def test_domain_profile_store_and_hash_are_per_company(tmp_path: Path) -> None:
    from ax_contracts.schemas.data import DomainProfileOutput
    from data_agent.skills import domain_profile as dp
    from data_agent.skills.product_link import CatalogRead

    catalog = CatalogRead(products=[], categories={})
    tenant = dp.TenantProfile(about="같은 소개")
    assert dp.profile_hash(tenant, catalog, "catbooks") != dp.profile_hash(tenant, catalog, "nubi")
    with cs.using("nubi"):
        assert dp.profile_hash(tenant, catalog) == dp.profile_hash(tenant, catalog, "nubi")
    path = tmp_path / "domain_profile.db"
    cat, nubi = dp.ProfileStore(path, "catbooks"), dp.ProfileStore(path, "nubi")
    cat.put("h1", DomainProfileOutput(industry="출판"), AT)
    assert nubi.get("h1") is None and cat.get("h1").industry == "출판"  # type: ignore[union-attr]
    cat.close()
    nubi.close()


def test_old_domain_profile_db_becomes_catbooks(tmp_path: Path) -> None:
    from ax_contracts.schemas.data import DomainProfileOutput
    from data_agent.skills import domain_profile as dp

    path = tmp_path / "domain_profile.db"
    _old(path, "CREATE TABLE profiles (hash TEXT PRIMARY KEY, created_at TEXT NOT NULL,"
               " output TEXT NOT NULL)")  # fmt: skip
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO profiles VALUES ('h1', ?, ?)",
                 (AT.isoformat(), DomainProfileOutput(industry="출판").model_dump_json()))
    conn.commit()
    conn.close()
    for _ in range(2):
        store = dp.ProfileStore(path, "catbooks")
        assert store.get("h1").industry == "출판"  # type: ignore[union-attr]
        store.close()
    assert dp.ProfileStore(path, "nubi").get("h1") is None
    assert _rows(path, "SELECT hash, tenant_id FROM profiles") == [("h1", "catbooks")]


# ---------------------------------------------------------------- domain_subtopics


def test_subtopic_searches_and_axes_are_per_company(tmp_path: Path) -> None:
    from data_agent.skills import domain_subtopics as ds

    path = tmp_path / "subtopics.db"
    cat, nubi = ds.SubtopicStore(path, "catbooks"), ds.SubtopicStore(path, "nubi")
    cat.put("고양이", AT, [ds.Short("v1", "고양이 책", ("책",), 10)])
    nubi.put("고양이", AT, [ds.Short("v2", "고양이 여행", ("여행",), 20)])
    assert [s.video_id for s in cat.get("고양이")[1]] == ["v1"]  # type: ignore[index]
    assert [s.video_id for s in nubi.get("고양이")[1]] == ["v2"]  # type: ignore[index]
    assert [s.video_id for s in cat.fresh_shorts(AT - timedelta(days=1))] == ["v1"]
    day = AT.astimezone(ds.KST).date().isoformat()
    assert cat.searched_on(day) == 1 and nubi.searched_on(day) == 1
    cat.save_axes(["장르"], {"소설": "장르"})
    assert cat.axes(["장르"], ["소설"]) == {"소설": "장르"} and nubi.axes(["장르"], ["소설"]) == {}
    cat.close()
    nubi.close()


def test_old_subtopics_db_becomes_catbooks(tmp_path: Path) -> None:
    from data_agent.skills import domain_subtopics as ds

    path = tmp_path / "subtopics.db"
    shorts = json.dumps([{"id": "v1", "title": "t", "tags": [], "views": 1}])
    _old(path,
         "CREATE TABLE searches (seed TEXT PRIMARY KEY, searched_at TEXT NOT NULL,"
         " day TEXT NOT NULL, shorts TEXT NOT NULL)",
         "CREATE TABLE axes (axes TEXT NOT NULL, name TEXT NOT NULL, axis TEXT NOT NULL,"
         " PRIMARY KEY (axes, name))",
         f"INSERT INTO searches VALUES ('고양이', '{AT.isoformat()}', '2026-11-23', '{shorts}')",
         "INSERT INTO axes VALUES ('[\"장르\"]', '소설', '장르')")  # fmt: skip
    for _ in range(2):
        store = ds.SubtopicStore(path, "catbooks")
        assert store.get("고양이") is not None
        assert store.axes(["장르"], ["소설"]) == {"소설": "장르"}
        store.close()
    assert ds.SubtopicStore(path, "nubi").get("고양이") is None
    assert _keys(path, "searches") == ["tenant_id", "seed"]
    assert _keys(path, "axes") == ["tenant_id", "axes", "name"]


# ---------------------------------------------------------------- source_plan · source_probe


def test_source_plans_and_probes_are_per_company(tmp_path: Path) -> None:
    from ax_contracts.schemas.data import DomainProfileOutput, SourcePlanOutput
    from data_agent.skills import data_sources as dsrc
    from data_agent.skills import source_plan as sp

    profile = DomainProfileOutput(industry="출판")
    config = sp.PlanConfig()
    assert sp.plan_id(profile, [], config, "catbooks") != sp.plan_id(profile, [], config, "nubi")
    path = tmp_path / "source_plan.db"
    cat, nubi = sp.PlanStore(path, "catbooks"), sp.PlanStore(path, "nubi")
    cat.put(SourcePlanOutput(plan_id="plan_c"), AT)
    assert cat.latest().plan_id == "plan_c"  # type: ignore[union-attr]
    assert nubi.latest() is None and nubi.get("plan_c") is None
    probes = tmp_path / "source_probe.db"
    pc, pn = dsrc.ProbeStore(probes, "catbooks"), dsrc.ProbeStore(probes, "nubi")
    pc.put("naver_shopping", True, "ok", AT)
    pn.put("naver_shopping", False, "키 없음", AT)
    assert pc.get("naver_shopping")[0] is True and pn.get("naver_shopping")[0] is False  # type: ignore[index]
    for s in (cat, nubi, pc, pn):
        s.close()


def test_old_source_plan_and_probe_dbs_become_catbooks(tmp_path: Path) -> None:
    from ax_contracts.schemas.data import SourcePlanOutput
    from data_agent.skills import data_sources as dsrc
    from data_agent.skills import source_plan as sp

    plans = tmp_path / "source_plan.db"
    _old(plans, "CREATE TABLE plans (plan_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,"
                " output TEXT NOT NULL)")  # fmt: skip
    conn = sqlite3.connect(plans)
    conn.execute("INSERT INTO plans VALUES ('plan_c', ?, ?)",
                 (AT.isoformat(), SourcePlanOutput(plan_id="plan_c").model_dump_json()))
    conn.commit()
    conn.close()
    probes = tmp_path / "source_probe.db"
    _old(probes, "CREATE TABLE probes (source_id TEXT PRIMARY KEY, ok INTEGER NOT NULL,"
                 " reason TEXT NOT NULL, at TEXT NOT NULL)",
         f"INSERT INTO probes VALUES ('naver_shopping', 1, 'ok', '{AT.isoformat()}')")
    for _ in range(2):
        assert sp.PlanStore(plans, "catbooks").get("plan_c") is not None
        assert dsrc.ProbeStore(probes, "catbooks").get("naver_shopping") is not None
    assert sp.PlanStore(plans, "nubi").get("plan_c") is None
    assert dsrc.ProbeStore(probes, "nubi").get("naver_shopping") is None
    assert _keys(probes, "probes") == ["tenant_id", "source_id"]


# ---------------------------------------------------------------- topic_categories


def test_topic_categories_key_has_the_company(tmp_path: Path) -> None:
    from data_agent.skills.topic_category import CategoryCache, CategoryResult

    path = tmp_path / "topic_categories.db"
    cache = CategoryCache(path)
    with cs.using("nubi"):
        cache.put("고양이", CategoryResult("생활·취미", None, 1, is_person=False))
    with cs.using("catbooks"):
        assert cache.get("고양이") is None  # 같은 문장이라도 회사가 다르면 다른 키
        cache.put("고양이", CategoryResult("교육·책", None, 1, is_person=False))
    fresh = CategoryCache(path)  # 메모리 말고 저장 파일에서
    with cs.using("nubi"):
        assert fresh.get("고양이").category == "생활·취미"  # type: ignore[union-attr]
    assert fresh.get("고양이").category == "교육·책"  # type: ignore[union-attr]
    assert sorted(_rows(path, "SELECT key, tenant_id FROM topic_categories")) == [
        ("catbooks|고양이", "catbooks"), ("nubi|고양이", "nubi")]
    with cs.using("nubi"):
        fresh.clear()  # 그 회사 것만 지운다
    assert _rows(path, "SELECT tenant_id FROM topic_categories") == [("catbooks",)]


def test_old_topic_categories_db_becomes_catbooks_once(tmp_path: Path) -> None:
    from data_agent.skills.topic_category import CATEGORY_VERSION, CategoryCache

    path = tmp_path / "topic_categories.db"
    _old(path,
         "CREATE TABLE topic_categories (key TEXT PRIMARY KEY, keyword TEXT NOT NULL,"
         " category TEXT NOT NULL, reason TEXT, real_entity INTEGER, entity_names TEXT NOT NULL,"
         " evidence INTEGER NOT NULL, classified_at TEXT NOT NULL, is_person INTEGER,"
         " version INTEGER)",
         "INSERT INTO topic_categories VALUES ('고양이', '고양이', '교육·책', NULL, 0, '[]', 1,"
         f" '{datetime.now(UTC).isoformat()}', 0, {CATEGORY_VERSION})")  # fmt: skip
    for _ in range(2):  # 두 번 열어도 키를 두 번 바꾸지 않는다
        cache = CategoryCache(path)
        assert cache.get("고양이").category == "교육·책"  # type: ignore[union-attr]
        with cs.using("nubi"):
            assert cache.get("고양이") is None
    assert _rows(path, "SELECT key, tenant_id FROM topic_categories") == [
        ("catbooks|고양이", "catbooks")]


# ---------------------------------------------------------------- company_accounts · 파일


def test_company_accounts_already_has_a_tenant_key(tmp_path: Path) -> None:
    from data_agent.skills import company_accounts as ca

    path = tmp_path / "company_accounts.db"
    ca.AccountStore(path).close()
    assert _keys(path, "company_accounts") == ["tenant", "profile_hash"]  # #1564 그대로


def test_radar_auto_file_moves_into_the_company_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_agent_template import tenant

    conn = tmp_path / "company_connection.json"
    old = tmp_path / tenant.IG_RADAR_AUTO_FILE
    old.write_text(json.dumps({"tenant": "catbooks", "accounts": ["a1"]}), encoding="utf-8")
    monkeypatch.setenv("AX_COMPANY_CONNECTION_FILE", str(conn))
    assert tenant.ig_radar_auto_file() == old  # 회사 폴더를 안 켰으면 예전 자리
    monkeypatch.setenv("AX_COMPANIES_DIR", str(tmp_path / "companies"))
    new = tenant.ig_radar_auto_file()
    assert new == tmp_path / "companies" / "catbooks" / tenant.IG_RADAR_AUTO_FILE
    assert new.is_file() and not old.exists()  # type: ignore[union-attr]
    assert (tmp_path / (tenant.IG_RADAR_AUTO_FILE + ".moved")).is_file()  # 옛 것은 .moved
    assert tenant.ig_radar_auto_accounts() == ["a1"]
    monkeypatch.setenv("AX_COMPANY_FOLDERS", "off")  # 되돌리기: 옛 자리로 되살린다
    assert tenant.ig_radar_auto_file() == old and old.is_file()
