"""분석 의뢰 보고서 보완 (11/20 TR-86F1 → PROP-12 기준) + 인스타 토큰 만료 표시.

1. 경쟁사 후보: LLM은 브랜드 이름만, 아이디 후보는 코드가 만든다 (business_discovery로만 남김)
2. 업종 → 네이버 쇼핑 분야 짝 표 (INDUSTRIES가 빠짐없이)
3. 타깃에 10대 미만이 없으면 어린이 낱말이 든 회사 키워드는 뺀다
4. 콘티 LLM 입력에 캡션 글자가 없다
5. 기획에 넘기는 body_md의 모든 숫자가 facts(label·value)·제목 안에 있다
6. 토큰 만료: debug_token 확인 → 추정(넣은 시각 + 60일) → 모름. 토큰 값은 어디에도 없다
7. 대화 분석 의뢰 묶음: partial이면 다 못 채운 칸·이유·판정 규칙
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas.data import TrendReportInput
from data_agent import data_chat as dc
from data_agent import instagram_store as st
from data_agent.connectors.instagram_graph import IgError, InstagramGraph
from data_agent.connectors.naver_shopping import CATEGORY_CANDIDATES
from data_agent.skills import company_accounts as ca
from data_agent.skills import instagram
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.skills import trend_report_company as trc
from data_agent.skills import trend_report_insight as ti
from data_agent.skills.domain_profile import INDUSTRIES
from data_agent.trend_report_store import TrendReportStore


@pytest.fixture(autouse=True)
def _product_promo(monkeypatch: pytest.MonkeyPatch) -> None:
    """11/24: 이 파일은 지금 규칙(제품·업종 소개)의 보고서·기획 넘김 숫자를 본다.
    회사 기본값은 재미 먼저라 여기서만 product로 둔다 (재미 먼저: test_trend_report_promo.py)."""
    from ax_agent_template import tenant

    monkeypatch.setattr(tenant, "promo", lambda name=None: {"promo_style": "product",
                                                            "brand_exposure": "end_card"})


def _load(name: str, file: str) -> Any:
    """tests 폴더는 패키지가 아니라 파일로 읽는다 (dataclass가 모듈을 찾게 sys.modules에 먼저)."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers", "test_trend_report.py")
C = _load("trend_report_company_helpers", "test_trend_report_company.py")

NOW = datetime(2026, 11, 20, 5, 36, tzinfo=UTC)  # 한국 14:36
SECRET = "EAAGsecret-token-value"


# ---------------------------------------------------------------- 1. 경쟁사 후보


class BrandLLM:
    """경쟁사 후보 LLM: 브랜드 이름만 (아이디는 주지 않음)."""

    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items, self.calls = items, 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls += 1
        return schema.model_validate({"items": self.items})


class Graph:
    configured = True

    def __init__(self, real: dict[str, int]) -> None:
        self.real, self.asked = real, []

    async def business_profile(self, username: str) -> dict[str, Any]:
        self.asked.append(username)
        if username not in self.real:
            raise IgError("NO_DATA", "계정 없음")
        return {"username": username, "name": username, "followers_count": self.real[username],
                "media_count": 5}  # fmt: skip


def test_code_makes_id_candidates_from_the_english_name() -> None:
    got = ca.id_candidates("YES24")
    assert got == ["yes24", "yes24_official", "yes24.official", "yes24_kr", "yes24official"]
    spaced = ca.id_candidates("Millie Book-Club")
    assert {"milliebookclub", "millie_book_club", "millie.book.club",
            "millie_book_club_official"} <= set(spaced)  # fmt: skip
    assert ca.id_candidates("") == []


def test_brand_names_are_checked_and_only_verified_ids_stay(tmp_path: Path) -> None:
    llm = BrandLLM([
        {"name_ko": "예스24", "name_en": "YES24", "reason": "온라인 서점"},
        {"name_ko": "밀리의서재", "name_en": "millie", "account": "millie_bookclub",
         "reason": "전자책 구독"},
        {"name_ko": "없는서점", "name_en": "Ghost Books", "reason": "지어낸 이름"},
    ])  # fmt: skip
    # yes24·yes24_official 둘 다 있으면 팔로워 큰 하나만 (한 브랜드 = 한 계정)
    graph = Graph({"yes24_official": 90_000, "yes24": 300, "millie_bookclub": 120_000})
    store = ca.AccountStore(":memory:")
    found = asyncio.run(ca.find(C.BOOK, llm, graph, env={}, now=NOW, store=store))
    assert [a["account"] for a in found.accounts] == ["millie_bookclub", "yes24_official"]
    assert "yes24_official" in found.candidates and llm.calls == 1
    dropped = {d["account"]: d["reason"] for d in found.dropped}
    assert "팔로워가 더 많음" in dropped["yes24"]
    assert "ghostbooks" in dropped  # 공식 API로 확인 안 됨
    assert found.accounts[1]["reason"].startswith("예스24:")


def test_checks_are_capped_per_company(tmp_path: Path) -> None:
    cfg = tmp_path / "tenant.toml"
    cfg.write_text("[report.accounts]\nmax_checks = 4\n", encoding="utf-8")
    llm = BrandLLM([{"name_en": f"brand{i}", "reason": "x"} for i in range(10)])
    graph = Graph({})
    found = asyncio.run(ca.find(C.BOOK, llm, graph, env={ca.TENANT_CONFIG_ENV: str(cfg)},
                                now=NOW, store=ca.AccountStore(":memory:")))  # fmt: skip
    assert len(graph.asked) == 4 and len(found.candidates) == 4


def test_old_rule_version_is_searched_again(tmp_path: Path) -> None:
    path = tmp_path / "accounts.db"
    old = sqlite3.connect(path)  # 11/20 전 저장분: rule_version 열이 없다
    old.execute("CREATE TABLE company_accounts (tenant TEXT NOT NULL, profile_hash TEXT NOT NULL,"
                " found_at TEXT NOT NULL, accounts TEXT NOT NULL, dropped TEXT NOT NULL,"
                " candidates TEXT NOT NULL, PRIMARY KEY (tenant, profile_hash))")  # fmt: skip
    old.execute("INSERT INTO company_accounts VALUES (?,?,?,?,?,?)",
                ("테스트서점", "hash_book", NOW.isoformat(),
                 json.dumps([{"account": "munhakdongne", "followers_count": 1}]), "[]", "[]"))
    old.commit()
    old.close()
    store = ca.AccountStore(path)
    assert store.fresh("테스트서점", "hash_book", NOW) is None  # 버전 1 → 다시 찾음
    llm = BrandLLM([{"name_en": "YES24", "reason": "x"}])
    found = asyncio.run(ca.find(C.BOOK, llm, Graph({"yes24_official": 10}), env={}, now=NOW,
                                store=store))  # fmt: skip
    assert not found.reused and [a["account"] for a in found.accounts] == ["yes24_official"]
    again = asyncio.run(ca.find(C.BOOK, llm, Graph({}), env={}, now=NOW, store=store))
    assert again.reused and llm.calls == 1
    store.close()


# ---------------------------------------------------------------- 2. 쇼핑 분야 짝


def test_every_industry_has_a_shopping_pair_entry() -> None:
    assert set(INDUSTRIES) <= set(trc.INDUSTRY_SHOPPING)  # 하나라도 빠지면 실패
    names = {n for n, _ in CATEGORY_CANDIDATES}
    assert all(v is None or v in names for v in trc.INDUSTRY_SHOPPING.values())
    assert trc.shopping_category_of("출판·교육") == "50005542"  # 도서
    assert trc.shopping_category_of("반려동물") is None and trc.shopping_category_of("기타") is None


# ---------------------------------------------------------------- 3. 어린이 낱말


def _ctx(ages: list[str], env: dict[str, str] | None = None) -> trc.CompanyContext:
    return trc.build_context(C.BOOK, favorites=["어린이", "에세이", "초등 학습"],
                             favorites_reason=None,
                             pool=["그림책 추천", "소설", "키즈 도서", "시집"], accounts=[],
                             accounts_reason=None, request_text="영상", env=env or {},
                             ages=ages)  # fmt: skip


def test_child_words_are_dropped_when_the_target_has_no_under_ten() -> None:
    ctx = _ctx(["10", "20"])
    assert ctx.keywords == ["에세이", "독서모임", "고양이 에세이", "소설", "시집"]
    line = next(x for x in ctx.limits if x.startswith("타깃과 맞지 않아 뺀 키워드"))
    assert "어린이" in line and "그림책 추천" in line and "키즈 도서" in line
    kept = _ctx(["0", "30"])  # 10대 미만이 있으면 그대로
    assert kept.keywords[0] == "어린이"
    assert _ctx([]).keywords[0] == "어린이"  # 나이 없음 = 전 연령: 빼지 않는다


def test_child_words_come_from_settings(tmp_path: Path) -> None:
    cfg = tmp_path / "trends.toml"
    cfg.write_text('[report]\nchild_words = ["에세이"]\n', encoding="utf-8")
    ctx = _ctx(["20"], {"DATA_TREND_CONFIG": str(cfg)})
    assert "에세이" not in ctx.keywords and ctx.keywords[0] == "어린이"


def test_request_text_keywords_are_not_dropped() -> None:
    resolved = tb.Resolved(tb.input_brief(TrendReportInput(keywords=["어린이 책"])), None, [], [],
                           0)  # fmt: skip
    resolved.brief.target_ages = ["20"]
    tb.apply_company(resolved, _ctx(["20"]))
    assert resolved.brief.keywords == ["어린이 책"] and resolved.brief.sources.keywords == "input"


# ---------------------------------------------------------------- 4·5. 콘티·body_md


def _report_with_caption(tmp_path: Path) -> tuple[Any, Any]:
    request = TrendReportInput(preset="eco_beauty")
    accounts = tb.load_preset("eco_beauty").accounts[:5]  # type: ignore[union-attr]
    media = {}
    for a in accounts:
        items = H._account(a)
        items[0]["caption"] = "다음 달 펀딩 소식! 13일 오픈 #클린뷰티"
        media[a] = items
    llm = H.FakeLLM()
    report = H._report(request)
    asyncio.run(tr.build(report, request, H._deps(tmp_path, llm, H.FakeGraph(media),
                                                  memes=H.MEMES),
                         TrendReportStore(), now=H.NOW))  # fmt: skip
    return report, llm


def test_conti_llm_gets_no_caption_text(tmp_path: Path) -> None:
    report, llm = _report_with_caption(tmp_path)
    assert report.sections.content.posts  # 캡션이 든 게시물이 근거에 있다
    assert any("펀딩" in (p.caption_excerpt or "") for p in report.sections.content.posts)
    sent = next(data for name, data in llm.calls if name == "_Recommendation")
    assert all("펀딩" not in v and "캡션" not in v for v in sent.values())
    assert any(k.startswith("근거_hashtag") for k in sent)  # 해시태그 이름은 그대로 넘긴다


def test_body_md_numbers_are_all_in_the_facts(tmp_path: Path) -> None:
    report, _ = _report_with_caption(tmp_path)
    insight, why = ti.build_insight(report)
    assert insight is not None, why
    body = insight.body_md or ""
    # 11/23: "## 콘티"(장면 줄) 대신 "## 기획에 넘길 재료"(재료 줄)
    assert "## 섹션 요약" in body and "## 한계" in body and "## 기획에 넘길 재료" in body
    allowed = ti.allowed_numbers(report, insight.facts)
    assert ti.numbers(body) <= allowed, ti.numbers(body) - allowed
    assert "0-3초" not in body and "## 콘티" not in body and "- 재료(해시태그):" in body
    assert "펀딩" not in body
    assert ti.build_insight(report)[0] == insight  # 같은 보고서면 같은 결과 (insight_id 그대로)


# ---------------------------------------------------------------- 6. 토큰 만료


def test_token_info_uses_read_only_debug_token_and_never_refreshes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        exp = int((NOW + timedelta(days=40)).timestamp())
        return httpx.Response(200, json={"data": {"is_valid": True, "expires_at": exp,
                                                  "data_access_expires_at": exp}})  # fmt: skip

    env = {"DATA_IG_ACCESS_TOKEN": SECRET, "DATA_IG_USER_ID": "17841400000000000"}
    graph = InstagramGraph(env, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    got = asyncio.run(graph.token_info())
    assert got["is_valid"] and got["expires_at"] > 0 and SECRET not in json.dumps(got)
    [req] = seen
    assert req.url.host == "graph.facebook.com" and req.url.path.endswith("/debug_token")
    assert req.method == "GET" and "refresh" not in str(req.url) and "exchange" not in str(req.url)


class TokenGraph:
    configured = True
    calls_today = 0

    def __init__(self, expires_in_days: int | None) -> None:
        self.days, self.token_calls = expires_in_days, 0

    async def token_info(self) -> dict[str, Any]:
        self.token_calls += 1
        if self.days is None:
            raise IgError("PERMISSION", "권한 없음")
        return {"expires_at": int((NOW + timedelta(days=self.days)).timestamp()),
                "data_access_expires_at": None, "is_valid": True}  # fmt: skip

    async def audio(self, kind: str) -> list[dict[str, Any]]:
        return []

    async def business_media(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        return []


def _env(tmp_path: Path) -> dict[str, str]:
    return {st.AUDIO_ENV: str(tmp_path / "ig_audio.db"), st.RADAR_ENV: str(tmp_path / "radar.db"),
            "DATA_TREND_CONFIG": str(tmp_path / "none.toml"),
            "DATA_IG_ACCESS_TOKEN": SECRET}  # fmt: skip


def test_collect_checks_the_token_once_a_day_and_shows_days_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instagram, "ig_radar_accounts", lambda: None)
    env, graph = _env(tmp_path), TokenGraph(40)
    asyncio.run(instagram.collect(env, graph, now=NOW))
    asyncio.run(instagram.collect(env, graph, now=NOW + timedelta(hours=1)))
    assert graph.token_calls == 1  # 하루 한 번
    got = instagram.token_expiry(env, NOW, put_at=lambda e: None)
    assert got.basis == "checked" and got.days_left == 40 and not got.warning
    assert got.line == "만료까지 40일(확인)"
    raw = Path(env[st.AUDIO_ENV]).read_bytes()
    assert SECRET.encode() not in raw  # 토큰 값은 저장하지 않는다


def test_fourteen_days_or_less_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instagram, "ig_radar_accounts", lambda: None)
    env = _env(tmp_path)
    asyncio.run(instagram.collect(env, TokenGraph(9), now=NOW))
    got = instagram.token_expiry(env, NOW, put_at=lambda e: None)
    assert got.warning and got.line.startswith("만료까지 9일(확인) · 곧 만료")


def test_estimated_from_the_put_time_then_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instagram, "ig_radar_accounts", lambda: None)
    env = _env(tmp_path)
    asyncio.run(instagram.collect(env, TokenGraph(None), now=NOW))  # debug_token 실패 → 이유만
    put = NOW - timedelta(days=50)
    got = instagram.token_expiry(env, NOW, put_at=lambda e: put)
    assert got.basis == "estimated" and got.days_left == 10 and got.warning
    assert got.line.startswith("만료까지 10일(추정)") and "PERMISSION" in (got.reason or "")
    none = instagram.token_expiry(env, NOW, put_at=lambda e: None)
    assert none.basis == "unknown" and none.line == "만료 모름"


def test_data_sources_and_chat_show_the_line(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setattr(instagram, "ig_radar_accounts", lambda: None)
    env = _env(tmp_path)
    asyncio.run(instagram.collect(env, TokenGraph(30), now=NOW))
    assert dc.token_line(env, NOW) == "만료까지 30일(확인)"
    part = dc.read_instagram(env, NOW)
    assert part is not None and part["facts"]["토큰 만료"] == "만료까지 30일(확인)"
    assert SECRET not in json.dumps(part, ensure_ascii=False)


# ---------------------------------------------------------------- 7. 대화: 일부 완료 이유


def _saved(env: dict[str, str], rid: str, status: str) -> None:
    sections = {n: {"status": "known"} for n in dc.SECTION_LABELS}
    if status == "partial":
        sections["hashtags"] = {"status": "unknown", "reason": "두 계정 넘게 쓴 해시태그가 없음"}
        sections["competitors"] = {"status": "unknown", "reason": None}
    result = {"report_id": rid, "status": status, "created_at": NOW.isoformat(),
              "finished_at": NOW.isoformat(), "brief": {"product": "책"}, "sections": sections}
    store = TrendReportStore(env["DATA_TREND_REPORT_DB"])
    store.create(rid, rid, NOW.date(), {}, "orchestrator", None, result, NOW)
    store.save(rid, result)
    store.close()


def test_chat_says_which_cells_are_missing_for_partial(tmp_path: Path) -> None:
    env = {"DATA_TREND_REPORT_DB": str(tmp_path / "reports.db")}
    _saved(env, "tr_86f1272807b34143", "partial")
    _saved(env, "tr_aaaa0000", "done")
    part = dc.read_report(env, NOW + timedelta(minutes=1))
    assert part is not None
    by = {i["번호"]: i for i in part["facts"]["최근 보고서"]}
    partial = by["TR-86F1"]
    assert partial["다 못 채운 칸"] == [
        {"칸": "해시태그", "이유": "두 계정 넘게 쓴 해시태그가 없음"},
        {"칸": "경쟁사", "이유": "이유 기록 없음"},
    ]
    assert partial["판정 규칙"] == dc.PARTIAL_RULE
    assert "다 못 채운 칸" not in by["TR-AAAA"] and "판정 규칙" not in by["TR-AAAA"]
    assert dc.pick_bundles("보고서가 왜 일부 완료야?") == ["report"]
