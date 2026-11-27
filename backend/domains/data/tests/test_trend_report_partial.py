"""분석 보고서가 늘 "일부 완료"이던 것 (11/20 TR-158A "남성 30대 소설책").

1. 계정 늘리기: 후보 프롬프트는 업종 글자 없이 여러 갈래를 고루, 확인은 브랜드를 돌아가며.
   목표(6곳)보다 적으면 같은 업종 표시가 있는 레이더 계정(이미 읽은 것)을 더하고, 그래도 모자라면
   한계 문장. 업종 표시가 없으면 더하지 않는다
2. "봤는데 없음"(empty): 해시태그·콘텐츠·주제 후보. final_status는 known·empty를 다 한 칸으로
3. 데이터 대화·진행 알림이 empty를 "확인함 · 없음"으로 말한다
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas.data import TrendReport, TrendReportInput
from data_agent import data_chat as dc
from data_agent import instagram_store as st
from data_agent import trend_report_hooks as hooks
from data_agent.connectors.instagram_graph import OWNER_NAME
from data_agent.skills import company_accounts as ca
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.trend_report_store import TrendReportStore


def _load(name: str, file: str) -> Any:
    """tests 폴더는 패키지가 아니라 파일로 읽는다 (dataclass가 모듈을 찾게 sys.modules에 먼저)."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers_partial", "test_trend_report.py")
C = _load("trend_report_company_helpers_partial", "test_trend_report_company.py")
NOW = H.NOW


def _report(theme_mode: str | None = None) -> TrendReport:
    report = TrendReport(report_id="tr_p", status="running", created_at=NOW, caller="human",
                         brief=tb.input_brief(TrendReportInput()))  # fmt: skip
    report.brief.theme_mode = theme_mode  # type: ignore[assignment]
    for name in tr.SECTIONS:
        getattr(report.sections, name).status = "known"
    report.sections.theme.status = "skipped"
    return report


# ---------------------------------------------------------------- 2. 해시태그 empty / unknown


def test_one_account_cannot_count_shared_tags_and_report_is_partial() -> None:
    media = {"yes24_official": H._account("yes24_official", tag="#소설추천")}
    section, _ = tr.hashtag_section(media, tr.burst_posts(media, NOW), NOW, None)
    assert section.status == "unknown" and section.rows == []
    assert section.reason == "읽은 계정이 1곳뿐이라 같이 쓴 태그를 셀 수 없음 (2곳 이상 필요)"
    report = _report()
    report.sections.hashtags = section
    assert tr.final_status(report) == "partial"


def test_six_accounts_without_shared_tag_is_empty_and_report_is_done() -> None:
    media = {f"acc_{i}": H._account(f"acc_{i}", tag=f"#혼자쓴태그{i}") for i in range(6)}
    section, _ = tr.hashtag_section(media, tr.burst_posts(media, NOW), NOW, None)
    assert section.status == "empty" and section.rows == []
    assert section.reason == "읽은 계정 6곳이 같이 쓴 태그가 없음"
    assert "뷰티" not in tr.NO_SHARED_TAG
    report = _report()
    report.sections.hashtags = section
    assert tr.final_status(report) == "done"


def test_media_accounts_do_not_count_as_read_accounts() -> None:
    media = {"brand": H._account("brand", tag="#a"), "mag": H._account("mag", tag="#a")}
    section, _ = tr.hashtag_section(media, [], NOW, None, ("mag",))
    assert section.status == "unknown" and "1곳뿐" in (section.reason or "")


# ---------------------------------------------------------------- 2. 콘텐츠 empty / unknown


def test_content_read_without_bursts_is_empty_and_unread_stays_unknown() -> None:
    section, _ = tr.content_section([], True, None, None, 6)
    assert section.status == "empty"
    assert section.reason == "읽은 계정 6곳에 이번 7일 터진 게시물이 없음"
    unread, _ = tr.content_section([], False, None, "인스타 토큰·계정 id 없음")
    assert unread.status == "unknown" and unread.reason == "인스타 토큰·계정 id 없음"
    # 터진 게시물이 있으면 지금처럼 known
    media = {"a": H._account("a")}
    known, _ = tr.content_section(tr.burst_posts(media, NOW), True, None, None, 1)
    assert known.status == "known" and known.posts


# ---------------------------------------------------------------- 2. final_status 표


@pytest.mark.parametrize(("states", "theme_mode", "theme", "want"), [
    ({}, None, "skipped", "done"),
    ({"hashtags": "empty", "content": "empty"}, None, "skipped", "done"),
    ({"hashtags": "empty", "competitors": "unknown"}, None, "skipped", "partial"),
    ({"content": "failed"}, None, "skipped", "partial"),
    # 11/27 바뀐 기대값(예전 failed): 칸 하나 예외 + 나머지 unknown(설정·데이터 없음)은 partial
    # → 총괄이 의뢰를 멈추지 않고 의뢰문 기반으로 기획에 넘긴다
    ({"search": "unknown", "hashtags": "unknown", "content": "unknown",
      "competitors": "unknown", "recommendation": "failed"}, None, "skipped", "partial"),
    # 11/27 새 회사(설정 없음): 모든 칸 unknown → partial (예전 failed)
    ({"search": "unknown", "hashtags": "unknown", "content": "unknown",
      "competitors": "unknown", "recommendation": "unknown"}, None, "skipped", "partial"),
    # 진짜 예외로 전부 실패하면 지금처럼 failed
    ({"search": "failed", "hashtags": "failed", "content": "failed",
      "competitors": "failed", "recommendation": "failed"}, None, "skipped", "failed"),
    ({"search": "unknown", "hashtags": "empty", "content": "unknown",
      "competitors": "unknown", "recommendation": "unknown"}, None, "skipped", "partial"),
    ({}, "find", "empty", "done"),
    ({}, "find", "skipped", "partial"),  # find 의뢰인데 주제 칸을 못 함
    ({}, "use", "skipped", "done"),
])  # fmt: skip
def test_final_status_table(states: dict[str, str], theme_mode: str | None, theme: str,
                            want: str) -> None:  # fmt: skip
    report = _report(theme_mode)
    for name, status in states.items():
        getattr(report.sections, name).status = status
    report.sections.theme.status = theme  # type: ignore[assignment]
    assert tr.final_status(report) == want


def test_theme_all_candidates_checked_without_value_is_empty(tmp_path: Path) -> None:
    T = _load("trend_report_theme_helpers_partial", "test_trend_report_theme.py")
    from data_agent.skills import trend_report_theme as tt

    brief = tb.input_brief(TrendReportInput(theme="아이돌", theme_mode="find", target_gender="f",
                                            target_ages=["10"]))  # fmt: skip
    llm = T.ThemeLLM(T.FIND, ["없는그룹"])
    section, _, _, _ = asyncio.run(tt.theme_section(brief, llm, T.Datalab(), H._env(tmp_path),
                                                    NOW))  # fmt: skip
    assert section.status == "empty" and section.rows == []
    assert section.reason == "후보 1개를 모두 확인했지만 데이터랩 값이 있는 후보가 없음"
    assert [d.reason for d in section.dropped] == [tt.NO_VALUE]


# ---------------------------------------------------------------- 3. 진행 알림·데이터 대화


def test_progress_note_says_checked_but_none() -> None:
    report = _report()
    report.sections.hashtags.status = "empty"
    assert hooks.section_note(report, 2, 5, {"hashtags"}) == "섹션 2/5 · 해시태그 확인함 · 없음"
    assert hooks.section_note(report, 1, 5, {"search"}) == "섹션 1/5"


def test_chat_lists_empty_cells_separately_from_missing(tmp_path: Path) -> None:
    env = {"DATA_TREND_REPORT_DB": str(tmp_path / "reports.db")}
    sections = {n: {"status": "known"} for n in dc.SECTION_LABELS}
    sections["hashtags"] = {"status": "empty", "reason": "읽은 계정 6곳이 같이 쓴 태그가 없음"}
    sections["competitors"] = {"status": "unknown", "reason": "경쟁사 계정을 하나도 읽지 못함"}
    rid = "tr_158a000000000000"
    result = {"report_id": rid, "status": "partial", "created_at": NOW.isoformat(),
              "finished_at": NOW.isoformat(), "brief": {"product": "소설책"},
              "sections": sections}  # fmt: skip
    store = TrendReportStore(env["DATA_TREND_REPORT_DB"])
    store.create(rid, rid, NOW.date(), {}, "orchestrator", None, result, NOW)
    store.save(rid, result)
    store.close()
    part = dc.read_report(env, NOW + timedelta(minutes=1))
    assert part is not None
    item = part["facts"]["최근 보고서"][0]
    assert item["다 못 채운 칸"] == [{"칸": "경쟁사", "이유": "경쟁사 계정을 하나도 읽지 못함"}]
    assert item["확인했지만 없던 칸"] == [
        {"칸": "해시태그", "상태": "확인함 · 없음", "이유": "읽은 계정 6곳이 같이 쓴 태그가 없음"}]


# ---------------------------------------------------------------- 1. 계정 늘리기


class PromptLLM:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, str]]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.seen.append((prompt, dict(untrusted or {})))
        return schema.model_validate({"items": []})


def test_prompt_has_no_industry_words_and_same_shape_for_two_companies() -> None:
    books = C._profile("캣북스", "출판·교육", ["소설"], ["도서"], "hash_cat")
    mart = C._profile("그린마트", "식품", ["유기농"], ["채소"], "hash_green")
    llm = PromptLLM()
    for profile in (books, mart):
        asyncio.run(ca.find(profile, llm, C.FakeGraph(), env={}, now=NOW,
                            store=ca.AccountStore(":memory:")))  # fmt: skip
    (p1, d1), (p2, d2) = llm.seen
    assert p1 == p2 == ca.PROMPT and set(d1) == set(d2)
    assert d1["업종"] == "출판·교육" and d2["업종"] == "식품"  # 업종은 데이터 블록에서만
    for word in ("출판", "서점", "도서", "책", "식품", "뷰티", "캣북스", "그린마트"):
        assert word not in ca.PROMPT
    assert "갈래" in ca.PROMPT and ca.RULE_VERSION == 3


def test_ids_are_checked_round_robin_across_brands(tmp_path: Path) -> None:
    cfg = tmp_path / "tenant.toml"
    cfg.write_text("[report.accounts]\nmax_checks = 6\n", encoding="utf-8")
    llm = _BrandLLM([{"name_en": f"brand{i}", "reason": "x"} for i in range(6)])
    graph = _Graph({"brand3_official": 10})
    found = asyncio.run(ca.find(C.BOOK, llm, graph, env={ca.TENANT_CONFIG_ENV: str(cfg)},
                                now=NOW, store=ca.AccountStore(":memory:")))  # fmt: skip
    # 상한 6번이 브랜드 하나의 꼴 5개에 쓰이지 않고 6개 브랜드 첫 아이디에 고루 쓰인다
    assert graph.asked == [f"brand{i}" for i in range(6)]
    assert found.accounts == []


class _BrandLLM:
    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        return schema.model_validate({"items": self.items})


class _Graph:
    configured = True

    def __init__(self, real: dict[str, int]) -> None:
        self.real, self.asked = real, []

    async def business_profile(self, username: str) -> dict[str, Any]:
        from data_agent.connectors.instagram_graph import IgError

        self.asked.append(username)
        if username not in self.real:
            raise IgError("NO_DATA", "계정 없음")
        return {"username": username, "name": username, "followers_count": self.real[username],
                "media_count": 5}  # fmt: skip


def _radar(tmp_path: Path, accounts: list[str]) -> str:
    path = tmp_path / "radar.db"
    store = st.RadarStore(path)
    day = NOW.date()
    for a in accounts:
        items = H._account(a)
        items[0][OWNER_NAME] = a.upper()
        store.save(day, a, items)
    store.complete(day, NOW, len(accounts))
    store.close()
    return str(path)


def _env(tmp_path: Path, trends: str, radar: list[str]) -> dict[str, str]:
    cfg = tmp_path / "trends.toml"
    cfg.write_text(trends, encoding="utf-8")
    return {ca.DB_ENV: str(tmp_path / "accounts.db"), ca.TREND_CONFIG_ENV: str(cfg),
            ca.TENANT_CONFIG_ENV: str(tmp_path / "tenant.toml"),
            st.RADAR_ENV: _radar(tmp_path, radar)}  # fmt: skip


def _ctx(env: dict[str, str]) -> Any:
    store = ca.AccountStore(":memory:")
    company = C.Company(C.BOOK, ["소설"])
    _, ctx = C._resolve(company, C.FakeLLM(), C.FakeGraph(), store, env)
    return ctx


def test_short_accounts_add_a_limit_sentence(tmp_path: Path) -> None:
    ctx = _ctx(_env(tmp_path, "[instagram]\nradar_accounts = []\n", []))
    assert ctx.accounts == ["book_b", "book_a"]  # 공식 API로 확인된 2곳
    assert ("확인된 인스타 계정 2곳 (목표 6곳) · 회사 설정 [report.accounts] include로"
            " 더할 수 있어요") in ctx.limits


def test_radar_accounts_without_industry_marking_are_not_added(tmp_path: Path) -> None:
    trends = '[instagram]\nradar_accounts = ["beauty_x", "book_x"]\n'
    ctx = _ctx(_env(tmp_path, trends, ["beauty_x", "book_x"]))
    assert ctx.accounts == ["book_b", "book_a"]
    # 다른 업종 표시만 있어도 더하지 않는다 (뷰티 계정을 책 보고서에 넣지 않는다)
    other = tmp_path / "other"
    other.mkdir()
    marked = trends + '[instagram.radar_industries]\n"뷰티" = ["beauty_x"]\n'
    assert _ctx(_env(other, marked, ["beauty_x", "book_x"])).accounts == ["book_b", "book_a"]


def test_same_industry_radar_accounts_already_read_are_added(tmp_path: Path) -> None:
    trends = ('[instagram]\nradar_accounts = ["book_x", "book_y", "book_unread", "beauty_x"]\n'
              '[instagram.radar_industries]\n"출판·교육" = ["book_x", "book_y", "book_unread"]\n'
              '"뷰티" = ["beauty_x"]\n')  # fmt: skip
    env = _env(tmp_path, trends, ["book_x", "book_y", "beauty_x"])  # book_unread는 안 읽음
    ctx = _ctx(env)
    assert ctx.accounts == ["book_b", "book_a", "book_x", "book_y"]
    assert ctx.names["book_x"] == "BOOK_X"
    assert any("레이더 계정 2곳을 더함" in x for x in ctx.limits)
    assert any("확인된 인스타 계정 4곳 (목표 6곳)" in x for x in ctx.limits)


CATBOOKS_TRENDS = Path(__file__).resolve().parents[3] / "config/tenants/catbooks/trends.toml"


def test_catbooks_radar_industry_marking_fills_book_accounts(tmp_path: Path) -> None:
    import tomllib

    from data_agent.skills.domain_profile import INDUSTRIES

    assert "출판·교육" in INDUSTRIES  # 업종 이름이 도메인 프로필과 글자까지 같다
    ig = tomllib.loads(CATBOOKS_TRENDS.read_text(encoding="utf-8"))["instagram"]
    env = {ca.TREND_CONFIG_ENV: str(CATBOOKS_TRENDS), st.RADAR_ENV: str(tmp_path / "radar.db")}
    marked = ca.radar_industry_accounts(env, "출판·교육")
    assert len(marked) == 27  # 출판사 16 · 서점 4 · 책·웹소설 7
    assert set(marked) <= set(ig["radar_accounts"])  # 레이더 목록에 있는 계정만
    assert list(ig["radar_industries"]) == ["출판·교육"]  # 밈·브랜드·해외 묶음은 넣지 않음
    assert not {"9gag", "oliveyoung_official", "netflixkr"} & set(marked)
    # 레이더가 이미 읽은 책 계정만 목표까지 더한다 (안 읽은 계정·다른 묶음 계정은 빼고)
    env[st.RADAR_ENV] = _radar(tmp_path, ["yes24_official", "kyobobook_official", "munhakdongne",
                                          "kakaopage", "oliveyoung_official"])  # fmt: skip
    have = [{"account": "yes24_official"}]
    added = ca.radar_fill(have, "출판·교육", env, 4)
    assert [a["account"] for a in added] == ["munhakdongne", "kyobobook_official", "kakaopage"]
    assert all(a["reason"] == ca.RADAR_REASON for a in added)


def test_min_accounts_setting_and_full_target_has_no_limit(tmp_path: Path) -> None:
    env = _env(tmp_path, "[instagram]\nradar_accounts = []\n", [])
    Path(env[ca.TENANT_CONFIG_ENV]).write_text("[report.accounts]\nmin_accounts = 2\n",
                                               encoding="utf-8")  # fmt: skip
    assert ca.min_accounts(env) == 2
    assert not any("목표" in x for x in _ctx(env).limits)
    assert ca.min_accounts({}) == ca.MIN_REPORT_ACCOUNTS == 6
