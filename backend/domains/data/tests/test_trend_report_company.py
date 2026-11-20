"""분석 의뢰 빈칸을 연결된 회사로 채우기 (11/20, TR-5BB1: brief가 비어 1.6초 만에 failed).

가짜 프로필(도서·화장품)·가짜 판매 집단·가짜 인스타 커넥터만 쓴다. 코드·프로필 어디에도 실제
회사 이름을 넣지 않는다 — 회사를 바꾸면 같은 의뢰문에서 그 회사 값이 나와야 한다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas.data import (
    DomainProfileOutput,
    ProductGroup,
    SeedKeyword,
    TrendReport,
    TrendReportInput,
)
from data_agent.connectors.instagram_graph import IgError
from data_agent.llm_budget import LLMDailyCapReached
from data_agent.skills import company_accounts as ca
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.skills import trend_report_company as trc

NOW = datetime(2026, 11, 20, 4, 0, tzinfo=UTC)
ASK = "30대 남성들이 좋아할만한 우리 회사 광고 영상 만들어주세요"


def _seed(word: str) -> SeedKeyword:
    return SeedKeyword(keyword=word, evidence=f"{word} 원문", evidence_from="about")


def _profile(tenant: str, industry: str, seeds: list[str], groups: list[str],
             key: str) -> DomainProfileOutput:  # fmt: skip
    return DomainProfileOutput(status="known", tenant=tenant, industry=industry,
                               product_groups=[ProductGroup(name=g) for g in groups],
                               seed_keywords=[_seed(s) for s in seeds],
                               catalog_hash=key)  # fmt: skip


BOOK = _profile("테스트서점", "출판·교육", ["독서모임", "고양이 그림책"], ["그림책", "에세이"],
                "hash_book")  # fmt: skip
BEAUTY = _profile("테스트코스메틱", "뷰티", ["수분크림", "선크림"], ["스킨케어"], "hash_beauty")
# LLM 후보 (업종마다). fake_ghost는 공식 API에 없다 → 버려야 한다
CANDIDATES = {
    "출판·교육": ["book_a", "book_b", "fake_ghost", "Bad Id!"],
    "뷰티": ["beauty_a", "beauty_b"],
}
REAL = {"book_a": 50_000, "book_b": 120_000, "beauty_a": 30_000, "beauty_b": 10_000}


class FakeLLM:
    """의뢰문 추출(_Extracted)과 경쟁사 후보(_Candidates)를 답한다."""

    def __init__(self, cap: bool = False) -> None:
        self.cap, self.calls = cap, []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append(schema.__name__)
        if schema.__name__ == "_Extracted":
            return schema.model_validate({"gender": "m", "ages": ["30"]})
        if self.cap:
            raise LLMDailyCapReached("LLM 하루 상한")
        items = [{"account": a, "reason": "같은 업종"} for a in CANDIDATES[untrusted["업종"]]]
        return schema.model_validate({"items": items})


class FakeGraph:
    configured = True

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def business_profile(self, username: str) -> dict[str, Any]:
        self.asked.append(username)
        if username not in REAL:
            raise IgError("NO_DATA", "계정 없음")
        return {"username": username, "name": username.upper(), "followers_count": REAL[username],
                "media_count": 10}  # fmt: skip


@dataclass
class Company:
    profile: Any
    favorites: list[str]
    favorites_reason: str | None = None
    pool: tuple[str, ...] = ("책 추천", "그림책", "독서")


def _gather(company: Company, llm: FakeLLM, graph: FakeGraph, store: ca.AccountStore,
            env: dict[str, str], now: datetime = NOW) -> Any:  # fmt: skip
    async def profile() -> Any:
        return company.profile

    async def favorites(brief: Any) -> tuple[list[str], str | None]:
        return list(company.favorites), company.favorites_reason

    async def pool() -> list[str]:
        return list(company.pool)

    async def accounts(p: Any, note: Any) -> Any:
        return await ca.find(p, llm, graph, env=env, now=now, store=store)

    async def run(brief: Any) -> Any:
        return await trc.gather(brief, ASK, None, env=env, profile_fn=profile,
                                favorites_fn=favorites, pool_fn=pool,
                                accounts_fn=accounts)  # fmt: skip

    return run


def _resolve(company: Company, llm: FakeLLM, graph: FakeGraph, store: ca.AccountStore,
             env: dict[str, str]) -> tuple[Any, Any]:  # fmt: skip
    resolved = asyncio.run(tb.resolve(TrendReportInput(request_text=ASK), llm))
    ctx, _, _ = asyncio.run(_gather(company, llm, graph, store, env)(resolved.brief))
    tb.apply_company(resolved, ctx)
    return resolved, ctx


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {ca.DB_ENV: str(tmp_path / "accounts.db"),
            "DATA_TREND_CONFIG": str(tmp_path / "none.toml"),
            ca.TENANT_CONFIG_ENV: str(tmp_path / "tenant.toml")}  # fmt: skip


def test_book_company_fills_keywords_format_and_verified_competitors(env: dict[str, str]) -> None:
    store, graph = ca.AccountStore(":memory:"), FakeGraph()
    resolved, ctx = _resolve(Company(BOOK, ["그림책", "에세이"]), FakeLLM(), graph, store, env)
    b, src = resolved.brief, resolved.brief.sources
    assert b.preset is None  # 의뢰문이 프리셋 낱말과 안 맞음
    assert (b.target_gender, b.target_ages) == ("m", ["30"])
    assert b.keywords == ["그림책", "에세이", "독서모임", "고양이 그림책", "책 추천"]
    assert src.keywords == "company" and len(b.keywords) == 5
    assert b.format == "릴스 30초" and src.format == "company"
    assert b.product == "테스트서점 (출판·교육)" and src.product == "company"
    # 공식 API로 확인된 것만, 팔로워 큰 순 (fake_ghost·잘못된 아이디는 버림)
    assert b.competitors == ["book_b", "book_a"] and src.competitors == "company"
    assert "fake_ghost" in graph.asked and "bad id!" not in graph.asked
    assert resolved.names == {"book_b": "BOOK_B", "book_a": "BOOK_A"}
    assert resolved.shopping_category is None  # 업종 이름이 쇼핑 분야와 달라 건너뜀
    assert any("쇼핑 칸은 건너뜀" in x for x in resolved.limits)
    assert set(resolved.accounts) >= {"book_a", "book_b"}


def test_switching_company_changes_values_without_code(env: dict[str, str]) -> None:
    store = ca.AccountStore(":memory:")
    resolved, _ = _resolve(Company(BEAUTY, ["스킨케어"], pool=("보습",)), FakeLLM(), FakeGraph(),
                           store, env)  # fmt: skip
    b = resolved.brief
    assert b.product == "테스트코스메틱 (뷰티)"
    assert b.keywords == ["스킨케어", "수분크림", "선크림", "보습"]
    assert b.competitors == ["beauty_a", "beauty_b"]


def test_found_accounts_are_reused_for_30_days_and_redone_when_hash_changes(
    env: dict[str, str],
) -> None:
    store, graph, llm = ca.AccountStore(":memory:"), FakeGraph(), FakeLLM()
    first = asyncio.run(ca.find(BOOK, llm, graph, env=env, now=NOW, store=store))
    asked = len(graph.asked)
    assert asked == 3 and first.ig_calls == 3 and not first.reused  # 잘못된 아이디는 부르지 않음
    again = asyncio.run(ca.find(BOOK, llm, graph, env=env, now=NOW + timedelta(days=29),
                                store=store))  # fmt: skip
    assert again.reused and len(graph.asked) == asked and llm.calls.count("_Candidates") == 1
    later = asyncio.run(ca.find(BOOK, llm, graph, env=env, now=NOW + timedelta(days=31),
                                store=store))  # fmt: skip
    assert not later.reused and len(graph.asked) == asked * 2
    changed = BOOK.model_copy(update={"catalog_hash": "hash_book_v2"})  # 카탈로그가 바뀜
    redo = asyncio.run(ca.find(changed, llm, graph, env=env, now=NOW, store=store))
    assert not redo.reused and len(graph.asked) == asked * 3
    assert [d["account"] for d in first.dropped] == ["Bad Id!", "fake_ghost"]


def test_no_company_profile_leaves_fields_empty_with_a_reason(env: dict[str, str]) -> None:
    store = ca.AccountStore(":memory:")
    unknown = DomainProfileOutput(status="unknown", reason="회사 소개도 카탈로그도 없음")
    for profile in (None, unknown):
        resolved, ctx = _resolve(Company(profile, ["그림책"]), FakeLLM(), FakeGraph(), store, env)
        assert resolved.brief.keywords == [] and resolved.brief.competitors == []
        assert resolved.brief.product is None and ctx.product is None
        assert any(trc.NO_COMPANY in x for x in resolved.limits)


def test_llm_cap_fills_company_keywords_but_defers_competitors(env: dict[str, str]) -> None:
    store, graph = ca.AccountStore(":memory:"), FakeGraph()
    llm = FakeLLM(cap=True)
    llm_extract_ok = FakeLLM()  # 의뢰문 추출은 되고 경쟁사 후보 LLM만 상한
    resolved = asyncio.run(tb.resolve(TrendReportInput(request_text=ASK), llm_extract_ok))
    ctx, llm_calls, ig_calls = asyncio.run(
        _gather(Company(BOOK, ["그림책"]), llm, graph, store, env)(resolved.brief))
    tb.apply_company(resolved, ctx)
    assert resolved.brief.keywords and resolved.brief.sources.keywords == "company"
    assert resolved.brief.competitors == [] and graph.asked == [] and ig_calls == 0
    assert any("미룸" in x for x in resolved.limits)
    assert store.get("테스트서점", "hash_book") is None  # 미룬 것은 저장하지 않음 (다음 회차)


def test_segment_insufficient_skips_favorites_with_a_reason(env: dict[str, str]) -> None:
    store = ca.AccountStore(":memory:")
    company = Company(BOOK, [], favorites_reason="집단 구매자 수가 기준보다 적음")
    resolved, _ = _resolve(company, FakeLLM(), FakeGraph(), store, env)
    assert resolved.brief.keywords[:2] == ["독서모임", "고양이 그림책"]  # ①을 건너뛰고 ②부터
    assert any("타깃 집단 순위를 건너뜀" in x for x in resolved.limits)


def test_human_overrides_include_and_exclude(env: dict[str, str]) -> None:
    Path(env[ca.TENANT_CONFIG_ENV]).write_text(
        '[report.accounts]\ninclude = ["@Hand_Picked"]\nexclude = ["book_b"]\n', encoding="utf-8")
    store = ca.AccountStore(":memory:")
    resolved, _ = _resolve(Company(BOOK, ["그림책"]), FakeLLM(), FakeGraph(), store, env)
    assert resolved.brief.competitors == ["hand_picked", "book_a"]


def test_company_accounts_reader(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from data_agent.skills import domain_profile

    store = ca.AccountStore(env[ca.DB_ENV])
    asyncio.run(ca.find(BOOK, FakeLLM(), FakeGraph(), env=env, now=NOW, store=store))
    store.close()
    monkeypatch.setattr(domain_profile, "load_tenant",
                        lambda e: domain_profile.TenantProfile(name="테스트서점"))  # fmt: skip
    got = ca.company_accounts(env)
    assert [a["account"] for a in got] == ["book_b", "book_a"]
    assert ca.company_accounts({ca.DB_ENV: str(Path(env[ca.DB_ENV]).with_name("none.db"))}) == []


def test_target_filters_match_catalog_labels_not_codes() -> None:
    @dataclass
    class Attr:
        key: str
        values: dict[str, str]

    attrs = [Attr("age_band", {"20s": "20대", "30s": "30대"}),
             Attr("gender", {"female": "여성", "male": "남성"})]  # fmt: skip
    assert trc.target_filters(attrs, "m", ["30"]) == [{"gender": "male", "age_band": "30s"}]
    other = [Attr("sex", {"M": "남성"}), Attr("age", {"A3": "30대 초반"})]  # 다른 회사 코드
    assert trc.target_filters(other, "m", ["30"]) == [{"sex": "M", "age": "A3"}]
    assert trc.target_filters(attrs, None, []) == []


def test_format_rule_and_default(env: dict[str, str]) -> None:
    assert trc.format_of("홍보 릴스 부탁해요", env) == "릴스 30초"
    assert trc.format_of("요즘 트렌드 알려줘", env) == "릴스"
    Path(env["DATA_TREND_CONFIG"]).write_text('[report]\ndefault_format = "쇼츠 15초"\n',
                                              encoding="utf-8")  # fmt: skip
    assert trc.format_of("요즘 트렌드 알려줘", env) == "쇼츠 15초"
    assert trc.shopping_category_of("화장품/미용") == "50000002"
    assert trc.shopping_category_of("뷰티") is None


def test_eco_preset_request_does_not_touch_the_company(tmp_path: Path) -> None:
    """의뢰문이 프리셋 낱말과 맞으면 프리셋이 먼저: 칸이 다 차서 회사 정보를 읽지 않는다."""
    resolved = asyncio.run(tb.resolve(TrendReportInput(preset="eco_beauty"), FakeLLM()))
    assert not tr.needs_company(resolved.brief)
    assert resolved.brief.sources.keywords == "preset" and resolved.shopping_category == "50000002"


def test_one_section_known_is_partial() -> None:
    report = TrendReport(report_id="tr_x", status="running", created_at=NOW, caller="orchestrator",
                         brief=tb.input_brief(TrendReportInput()))  # fmt: skip
    report.sections.search.status = "known"
    for name in ("hashtags", "content", "competitors", "recommendation"):
        getattr(report.sections, name).status = "unknown"
    assert tr.final_status(report) == "partial"


def test_build_uses_company_when_fields_are_empty(tmp_path: Path) -> None:
    """빌드 경로: 빈 brief면 deps.company를 한 번 부르고 결과를 칸·출처·호출 수에 반영한다."""
    calls: list[str] = []

    async def company(brief: Any, text: Any, note: Any) -> tuple[Any, int, int]:
        calls.append(text)
        if note is not None:
            got = note("회사 경쟁사 찾는 중")
            if asyncio.iscoroutine(got):
                await got
        ctx = trc.CompanyContext(product="테스트서점 (출판·교육)", format="릴스 30초",
                                 keywords=["그림책"], competitors=[], accounts=[],
                                 limits=["회사 경쟁사: 확인된 인스타 계정이 없음"])  # fmt: skip
        return ctx, 1, 3

    notes: list[str] = []
    deps = tr.Deps(env={"DATA_TREND_AGE_DB": str(tmp_path / "age.db")}, llm=FakeLLM(), graph=None,
                   search=None, shopping=None, company=company)  # fmt: skip
    report = TrendReport(report_id="tr_c", status="queued", created_at=NOW, caller="orchestrator",
                         brief=tb.input_brief(TrendReportInput(request_text=ASK)))  # fmt: skip
    from data_agent.trend_report_store import TrendReportStore

    asyncio.run(tr.build(report, TrendReportInput(request_text=ASK), deps, TrendReportStore(),
                         now=NOW, note=notes.append))  # fmt: skip
    assert calls == [ASK] and notes == ["회사 경쟁사 찾는 중"]
    assert report.brief.keywords == ["그림책"] and report.brief.sources.keywords == "company"
    assert report.calls.instagram >= 3 and report.calls.llm >= 2
    assert any("확인된 인스타 계정이 없음" in x for x in report.limits)
