"""여러 회사 1-5 ②: 회사 화면·보고서·대화·캐시·예산 저장소에 회사 칸 (11/23).

표마다: 두 회사(catbooks·nubi)로 쓰면 서로 안 보인다 · 옛 DB(칸 없음)를 열면 기존 줄이 catbooks ·
두 번 열어도 같다(멱등). 회사는 skill 요청이면 meta.tenant_id, 아니면 지금 회사(tenant.current()).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas.data import PromoConceptsOutput, TrendInsightBriefOutput
from data_agent import company_scope as cs

DAY = date(2026, 11, 23)
AT = datetime(2026, 11, 23, 1, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AX_TENANT", "AX_COMPANY_CONNECTION_FILE", "AX_COMPANIES_DIR"):
        monkeypatch.delenv(name, raising=False)


def _sql(path: Path, *statements: str) -> None:
    conn = sqlite3.connect(path)
    for s in statements:
        conn.execute(s)
    conn.commit()
    conn.close()


def _rows(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _keys(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return cs.key_columns(conn, table)
    finally:
        conn.close()


# ---------------------------------------------------------------- trend_reports


def _report(rid: str) -> dict:
    return {"report_id": rid, "status": "done", "sections": {}}


def test_trend_reports_are_per_company_and_reuse_stays_inside_the_company(tmp_path: Path) -> None:
    from ax_contracts.schemas.data import TrendReportInput
    from data_agent.trend_report_runner import brief_hash
    from data_agent.trend_report_store import TrendReportStore

    request = TrendReportInput(preset="eco_beauty")
    assert brief_hash(request, "catbooks") != brief_hash(request, "nubi")  # 같은 의뢰문이라도
    store = TrendReportStore(tmp_path / "trend_reports.db")
    with cs.using("catbooks"):
        store.create("tr_c", "h", DAY, {}, "human", None, _report("tr_c"), AT, chat_thread_id="t1")
    with cs.using("nubi"):
        store.create("tr_n", "h", DAY, {}, "human", None, _report("tr_n"), AT)
        assert store.same("h", DAY) == "tr_n"  # 같은 해시여도 그 회사 것만 다시 연다
        assert [r["report_id"] for r in store.recent(10)] == ["tr_n"]
        assert store.get("tr_c") is None and store.statuses("tr_c") is None
        assert store.chat_thread("tr_c") is None
        store.save_handoff("tr_c", {"outcome": "created"})  # 다른 회사 보고서는 못 고친다
    with cs.using("catbooks"):
        assert store.same("h", DAY) == "tr_c" and store.handoff("tr_c") is None
        assert [r["report_id"] for r in store.recent(10)] == ["tr_c"]
        assert store.chat_thread("tr_c") == "t1"
    assert store.get("tr_n", all_companies=True) is not None  # 재시작 정리는 모든 회사
    assert store.company_of("tr_n") == "nubi"
    assert sorted(store.interrupt_live(AT)) == []  # 끝난 것은 건드리지 않는다


def test_old_trend_reports_db_becomes_catbooks(tmp_path: Path) -> None:
    from data_agent.trend_report_store import TrendReportStore

    path = tmp_path / "trend_reports.db"
    TrendReportStore(path).close()
    conn = sqlite3.connect(path)  # 칸 없는 옛 줄로 되돌린 모양을 만든다
    conn.execute("ALTER TABLE reports DROP COLUMN tenant_id")
    conn.execute("INSERT INTO reports (id, brief_hash, day, brief, caller, status, sections,"
                 " result, created_at) VALUES ('tr_old', 'h', '2026-11-23', '{}', 'human', 'done',"
                 " '{}', '{\"report_id\": \"tr_old\"}', '2026-11-23T01:00:00+00:00')")  # fmt: skip
    conn.commit()
    conn.close()
    for _ in range(2):
        store = TrendReportStore(path)
        assert store.get("tr_old") is not None
        with cs.using("nubi"):
            assert store.get("tr_old") is None
        store.close()
    assert _rows(path, "SELECT id, tenant_id FROM reports") == [("tr_old", "catbooks")]


def test_a_nubi_request_is_not_listed_for_catbooks() -> None:
    """화면: 누비투어로 넣은 분석 의뢰가 캣북스 목록에 안 보인다 (같은 의뢰문이어도 따로 만든다)."""
    import asyncio

    from ax_contracts.schemas.data import TrendReportInput
    from data_agent.trend_report_runner import TrendReportService
    from data_agent.trend_report_store import TrendReportStore

    service = TrendReportService(TrendReportStore(), lambda: None,  # type: ignore[arg-type, return-value]
                                 publish=lambda e: None)  # fmt: skip

    async def make(company: str) -> str:
        with cs.using(company):
            got = await service.create(TrendReportInput(preset="eco_beauty"))
            return got.report_id

    async def both() -> tuple[str, str, bool]:
        nubi = await make("nubi")
        cat = await make("catbooks")
        for task in list(service._tasks.values()):  # noqa: SLF001 - 만드는 작업은 기다리지 않는다
            task.cancel()
        return nubi, cat, nubi != cat

    nubi, cat, different = asyncio.run(both())
    assert different  # 같은 의뢰문이라도 회사가 다르면 다시 쓰지 않는다
    with cs.using("catbooks"):
        assert [i.report_id for i in service.list(10)] == [cat]
    with cs.using("nubi"):
        assert [i.report_id for i in service.list(10)] == [nubi]


# ---------------------------------------------------------------- trend_responses


def test_response_cache_key_and_rows_are_per_company(tmp_path: Path) -> None:
    from data_agent.response_cache import ResponseCache

    cache = ResponseCache(tmp_path / "trend_responses.db")
    cat_key = cache.key("trend_popular", {}, "s", "r")
    with cs.using("nubi"):
        nubi_key = cache.key("trend_popular", {}, "s", "r")
        cache.put(nubi_key, ["ok", {"who": "nubi"}], "trend_popular")
        assert cache.latest("trend_popular") == ["ok", {"who": "nubi"}]
    assert cat_key != nubi_key  # 같은 요청이라도 회사가 다르면 다른 키
    assert cache.get(nubi_key) is None and cache.latest("trend_popular") is None
    cache.put(cat_key, ["ok", {"who": "cat"}], "trend_popular")
    assert cache.latest("trend_popular") == ["ok", {"who": "cat"}]
    cache.clear()  # 그 회사 것만
    with cs.using("nubi"):
        assert cache.latest("trend_popular") == ["ok", {"who": "nubi"}]


def test_old_response_cache_rows_become_catbooks(tmp_path: Path) -> None:
    from data_agent.response_cache import ResponseCache

    path = tmp_path / "trend_responses.db"
    _sql(path, "CREATE TABLE responses (key TEXT PRIMARY KEY, skill TEXT NOT NULL,"
               " stored_at TEXT NOT NULL, value TEXT NOT NULL)",
         "INSERT INTO responses VALUES ('k', 'trend_popular', '2026-11-23', '[\"ok\", {}]')")
    for _ in range(2):
        cache = ResponseCache(path)
        assert cache.get("k") == ["ok", {}]  # 캣북스 키는 예전 그대로
        with cs.using("nubi"):
            assert cache.get("k") is None
    assert _rows(path, "SELECT tenant_id FROM responses") == [("catbooks",)]


# ---------------------------------------------------------------- insight_brief


def test_briefs_versions_lock_and_jobs_are_per_company(tmp_path: Path) -> None:
    from data_agent.skills.trend_insight_brief import BriefStore

    path = tmp_path / "insight_brief.db"
    cat, nubi = BriefStore(path, "catbooks"), BriefStore(path, "nubi")
    assert cat.put(DAY, "r1", TrendInsightBriefOutput(summary="캣북스"), AT) == 1
    assert nubi.put(DAY, "r1", TrendInsightBriefOutput(summary="누비"), AT) == 1  # 판 번호도 따로
    assert cat.get(DAY, "r1").summary == "캣북스"  # type: ignore[union-attr]
    assert nubi.get(DAY, "r1").summary == "누비"  # type: ignore[union-attr]
    assert [v["version"] for v in cat.versions(DAY, "r1")] == [1]
    token = cat.claim_build("server", "auto", AT)
    assert token is not None and cat.building(AT) is not None
    assert nubi.building(AT) is None  # "만드는 중" 잠금은 회사마다 하나
    assert nubi.claim_build("server", "auto", AT) is not None
    assert cat.claim_build("server", "auto", AT) is None
    cat.job_start("job1", DAY, "r1", "재현", AT)
    assert cat.last_job(DAY, "r1") is not None and nubi.last_job(DAY, "r1") is None
    with cs.using("nubi"):  # 넘긴 값이 없으면 부를 때마다 지금 요청의 회사
        assert BriefStore(path).get(DAY, "r1").summary == "누비"  # type: ignore[union-attr]
    assert _keys(path, "briefs") == ["tenant_id", "day", "rules"]
    assert _keys(path, "brief_versions") == ["tenant_id", "day", "rules", "version"]
    assert _keys(path, "brief_lock") == ["tenant_id"]


def test_old_insight_brief_db_becomes_catbooks(tmp_path: Path) -> None:
    from data_agent.skills.trend_insight_brief import BriefStore

    path = tmp_path / "insight_brief.db"
    body = TrendInsightBriefOutput(summary="옛 브리프").model_dump_json().replace("'", "''")
    _sql(path,
         "CREATE TABLE briefs (day TEXT NOT NULL, rules TEXT NOT NULL, created_at TEXT NOT NULL,"
         " output TEXT NOT NULL, PRIMARY KEY (day, rules))",
         "CREATE TABLE brief_lock (id INTEGER PRIMARY KEY CHECK (id = 1), token TEXT NOT NULL,"
         " who TEXT NOT NULL, trigger TEXT NOT NULL, started_at TEXT NOT NULL,"
         " until TEXT NOT NULL)",
         f"INSERT INTO briefs VALUES ('2026-11-23', 'r1', '{AT.isoformat()}', '{body}')",
         "INSERT INTO brief_lock VALUES (1, 'tok', 'collector', 'auto',"
         f" '{AT.isoformat()}', '{(AT + timedelta(hours=1)).isoformat()}')")  # fmt: skip
    for _ in range(2):
        store = BriefStore(path, "catbooks")
        assert store.get(DAY, "r1").summary == "옛 브리프"  # type: ignore[union-attr]
        assert store.building(AT) is not None  # 옛 잠금은 캣북스 것
        assert BriefStore(path, "nubi").get(DAY, "r1") is None
        assert BriefStore(path, "nubi").building(AT) is None
    assert _keys(path, "briefs") == ["tenant_id", "day", "rules"]
    assert _rows(path, "SELECT tenant_id, token FROM brief_lock") == [("catbooks", "tok")]
    # 다시 추천이 옛 판을 1번 판으로 옮길 때도 캣북스로 (판 표가 생기기 전 저장분)
    store = BriefStore(path, "catbooks")
    assert store.put(DAY, "r1", TrendInsightBriefOutput(summary="새 판"), AT) == 2


# ---------------------------------------------------------------- brief_lines · explain_cache


def test_brief_lines_and_explanations_are_per_company(tmp_path: Path) -> None:
    from data_agent.explain import ExplainCache, Explanation
    from data_agent.line_store import LineStore, line_key

    lines = LineStore(tmp_path / "brief_lines.db")
    cat_key = line_key("brief", "ins_1", {"a": 1})
    with cs.using("nubi"):
        nubi_key = line_key("brief", "ins_1", {"a": 1})
        lines.put(nubi_key, "누비 문장")
    assert cat_key != nubi_key and lines.get(nubi_key) is None
    explain = ExplainCache(path=tmp_path / "explain_cache.db")
    with cs.using("nubi"):
        explain.put("같은 프롬프트", Explanation("누비 설명", False))
    assert explain.get("같은 프롬프트") is None
    explain.put("같은 프롬프트", Explanation("캣북스 설명", False))
    with cs.using("nubi"):
        assert explain.get("같은 프롬프트").summary == "누비 설명"  # type: ignore[union-attr]
    assert explain.get("같은 프롬프트").summary == "캣북스 설명"  # type: ignore[union-attr]
    memory = ExplainCache()  # 메모리도 회사별
    memory.put("p", Explanation("캣북스", False))
    with cs.using("nubi"):
        assert memory.get("p") is None


def test_old_brief_lines_db_becomes_catbooks(tmp_path: Path) -> None:
    from data_agent.line_store import LineStore

    path = tmp_path / "brief_lines.db"
    _sql(path, "CREATE TABLE lines (key TEXT PRIMARY KEY, line TEXT NOT NULL,"
               " used_at TEXT NOT NULL)",
         f"INSERT INTO lines VALUES ('k', '옛 문장', '{datetime.now(UTC).isoformat()}')")
    for _ in range(2):
        store = LineStore(path)
        assert store.get("k") == "옛 문장"
        with cs.using("nubi"):
            assert store.get("k") is None
    assert _rows(path, "SELECT tenant_id FROM lines") == [("catbooks",)]


# ---------------------------------------------------------------- data_chat


def test_data_chat_list_read_and_hide_only_the_company(tmp_path: Path) -> None:
    from data_agent.data_chat_store import DataChatStore

    store = DataChatStore(str(tmp_path / "data_chat.db"))
    cat = store.create("캣북스 질문", "재현")
    with cs.using("nubi"):
        nubi = store.create("누비 질문", "민수")
        assert [t["id"] for t in store.list()] == [nubi["id"]]
        assert store.get(cat["id"]) is None
        assert store.hide(cat["id"], "민수") is False  # 다른 회사 대화는 못 지운다
        assert store.begin_turn(cat["id"], "끼어들기")[0] == "missing"
    assert [t["id"] for t in store.list()] == [cat["id"]]
    assert store.hide(cat["id"], "재현") is True
    assert len(store.list(all_companies=True)) == 1  # 누비 대화는 그대로


def test_old_data_chat_db_becomes_catbooks(tmp_path: Path) -> None:
    from data_agent.data_chat_store import DataChatStore

    path = tmp_path / "data_chat.db"
    _sql(path, "CREATE TABLE data_chat_threads (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
               " id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, status TEXT NOT NULL, who TEXT,"
               " summary TEXT NOT NULL DEFAULT '', messages TEXT NOT NULL,"
               " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
         "INSERT INTO data_chat_threads (id, title, status, who, messages, created_at,"
         f" updated_at) VALUES ('datachat-1', '옛 대화', 'answering', '재현', '[]',"
         f" '{AT.isoformat()}', '{AT.isoformat()}')")  # fmt: skip
    for _ in range(2):  # 다시 켤 때 답을 만들던 대화를 푼다 (회사와 상관없이)
        store = DataChatStore(str(path))
        assert store.get("datachat-1")["status"] == "talking"  # type: ignore[index]
        with cs.using("nubi"):
            assert store.list() == []
        store.shutdown()
    assert _rows(path, "SELECT tenant_id FROM data_chat_threads") == [("catbooks",)]


# ---------------------------------------------------------------- promo · sends · budget · videos


def test_promo_runs_are_per_company(tmp_path: Path) -> None:
    from data_agent.skills.promo_concepts import PromoStore

    store = PromoStore(tmp_path / "promo_concepts.db")
    with cs.using("nubi"):
        store.save(DAY, PromoConceptsOutput(run_id="run_n"))
    assert store.latest(DAY) is None
    store.save(DAY, PromoConceptsOutput(run_id="run_c"))
    assert store.latest(DAY).run_id == "run_c"  # type: ignore[union-attr]
    with cs.using("nubi"):
        assert store.latest(DAY).run_id == "run_n"  # type: ignore[union-attr]


def test_campaign_sends_are_per_company_and_the_meta_has_the_company(tmp_path: Path) -> None:
    from data_agent import campaign_update as cu

    path = tmp_path / "campaign_sends.db"
    _sql(path, "CREATE TABLE sends (campaign_id TEXT PRIMARY KEY, state TEXT NOT NULL,"
               " proposal_id TEXT, direction TEXT, sent_at TEXT, failures INTEGER NOT NULL,"
               " fail_day TEXT, reason TEXT, context_id TEXT, updated_at TEXT NOT NULL)",
         "INSERT INTO sends VALUES ('cmp_old', 'sent', 'PROP-1', '늘었음', NULL, 0, NULL, NULL,"
         " NULL, '2026-11-23')")  # fmt: skip
    for _ in range(2):
        store = cu.SendStore(path)
        assert store.get("cmp_old").state == "sent"  # type: ignore[union-attr]
        with cs.using("nubi"):
            assert store.get("cmp_old") is None
            store.save(cu.SendRow("cmp_n", state="failed"))
        assert store.get("cmp_n") is None
        store.close()
    assert sorted(_rows(path, "SELECT campaign_id, tenant_id FROM sends")) == [
        ("cmp_n", "nubi"), ("cmp_old", "catbooks")]
    import inspect

    assert "TENANT_ID_DEFAULT" not in inspect.getsource(cu)  # 기본값으로 새 요청을 만들지 않는다


def test_sent_request_results_are_per_company(tmp_path: Path) -> None:
    """의뢰 영상(#1568) 보낸 결과도 그 회사 것만 다음 인사이트 "지난번 결과"에 들어간다."""
    from data_agent import campaign_update as cu

    store = cu.SendStore(tmp_path / "campaign_sends.db")
    with cs.using("nubi"):
        store.save(cu.SendRow("wr_nubi000001", state="sent", direction="늘었음", sent_at=AT))
        assert [c for c, _, _ in store.sent_with_prefix("wr_")] == ["wr_nubi000001"]
    assert store.sent_with_prefix("wr_") == []
    store.close()


def test_meta_tenant_keeps_the_old_value_for_catbooks() -> None:
    """다른 부서·이벤트에 넣는 값: 캣북스는 예전 기본값(공통 로그가 그 값으로 거른다),
    다른 회사는 그 id."""
    from ax_contracts.schemas.common import TENANT_ID_DEFAULT
    from ax_contracts.schemas.data import TrendReport, TrendReportInput
    from data_agent.skills import trend_report_brief as tb
    from data_agent.trend_report_runner import ready_event

    assert cs.meta_tenant() == TENANT_ID_DEFAULT
    brief = tb.input_brief(TrendReportInput(preset="eco_beauty"))
    report = TrendReport(report_id="tr_x", status="done", created_at=AT, caller="human",
                         brief=brief)  # fmt: skip
    assert ready_event(report, AT).tenant_id == TENANT_ID_DEFAULT
    with cs.using("nubi"):
        assert cs.meta_tenant() == "nubi"
        assert ready_event(report, AT).tenant_id == "nubi"


def test_llm_budget_counts_per_company_with_the_same_cap(tmp_path: Path) -> None:
    from data_agent.llm_budget import Budget

    path = tmp_path / "data_llm_budget.db"
    _sql(path, "CREATE TABLE usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL,"
               " tokens INTEGER NOT NULL)",
         "CREATE TABLE usage_by (day TEXT NOT NULL, skill TEXT NOT NULL, task TEXT NOT NULL,"
         " calls INTEGER NOT NULL, tokens INTEGER NOT NULL, PRIMARY KEY (day, skill, task))",
         f"INSERT INTO usage VALUES ('{DAY.isoformat()}', 5, 500)",
         f"INSERT INTO usage_by VALUES ('{DAY.isoformat()}', 'brief', 'line', 5, 500)")
    env = {"DATA_LLM_DAILY_CALLS": "10", "DATA_LLM_DAILY_TOKENS": "100000"}
    for _ in range(2):
        budget = Budget(path)
        assert budget.usage(env, DAY).calls == 5  # 옛 줄은 캣북스
        with cs.using("nubi"):
            assert budget.usage(env, DAY).calls == 0
    budget = Budget(path)
    with cs.using("nubi"):
        budget.add(3, 30, DAY, skill="chat", task="answer")
        nubi = budget.usage(env, DAY)
        assert (nubi.calls, nubi.call_cap) == (3, budget.usage(env, DAY).call_cap)
        assert budget.by_task(DAY) == [("chat", "answer", 3, 30)]
    assert budget.usage(env, DAY).calls == 5 and budget.by_task(DAY) == [("brief", "line", 5, 500)]
    assert _keys(path, "usage") == ["tenant_id", "day"]
    assert _keys(path, "usage_by") == ["tenant_id", "day", "skill", "task"]
    memory = Budget()  # 메모리도 회사별
    memory.add(2, 20, DAY)
    with cs.using("nubi"):
        assert memory.usage(env, DAY).calls == 0


def test_registered_videos_are_per_company_but_measuring_is_shared(tmp_path: Path) -> None:
    from data_agent.skills import video_performance as vp

    def video(key: str, campaign: str) -> vp.Video:
        return vp.Video(key=key, platform="youtube", video_id=key.split(":")[1],
                        video_url=f"https://youtu.be/{key.split(':')[1]}",
                        campaign_id=campaign,
                        insight_id=None, proposal_id=None, test=False, published_at=AT,
                        title=None, registered_at=AT, category=None,
                        shorts_format=None)  # fmt: skip

    store = vp.VideoStore(tmp_path / "video_performance.db")
    store.upsert(video("youtube:aaaaaaaaaaa", "cmp_c"))
    with cs.using("nubi"):
        store.upsert(video("youtube:bbbbbbbbbbb", "cmp_n"))
        assert [v.campaign_id for v in store.videos()] == ["cmp_n"]
        assert store.find(campaign_id="cmp_c") is None
    assert [v.campaign_id for v in store.videos()] == ["cmp_c"]
    assert len(store.videos(all_companies=True)) == 2  # 측정(수집기)은 모든 회사 영상을 한 번에


def test_event_proposals_and_idea_reports_already_have_a_company_key(tmp_path: Path) -> None:
    from data_agent.event_proposal_store import EventProposalStore
    from data_agent.idea_reports import IdeaReportStore

    EventProposalStore(tmp_path / "event_proposals.db").close()
    assert _keys(tmp_path / "event_proposals.db", "event_proposals") == [
        "tenant_id", "proposal_id"]
    IdeaReportStore(str(tmp_path / "idea_reports.db")).close()
    assert "tenant_id" in [r[1] for r in sqlite3.connect(tmp_path / "idea_reports.db").execute(
        "PRAGMA table_info(idea_reports)")]


def test_data_chat_material_reads_only_the_company(tmp_path: Path) -> None:
    """데이터 대화가 바로 여는 응답 캐시·보고서 표도 그 회사 줄만 읽는다."""
    from data_agent import data_chat
    from data_agent.response_cache import ResponseCache

    path = tmp_path / "trend_responses.db"
    cache = ResponseCache(path)
    with cs.using("nubi"):
        cache.put("k1", ["ok", {"items": ["누비"]}], "trend_popular")
    env = {"DATA_TREND_RESPONSES_DB": str(path)}
    assert data_chat._cached(env, "trend_popular", lambda o: True) is None  # noqa: SLF001
    with cs.using("nubi"):
        assert data_chat._cached(env, "trend_popular", lambda o: True) == {"items": ["누비"]}  # noqa: SLF001
