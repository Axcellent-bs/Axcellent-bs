"""기획에 넘기는 근거(facts)를 보고서 결론·재료가 쓴 근거부터 + 욕설·성인 계정 근거 빼기 (11/24).

TR-0E4C·TR-D262: 결론·카드 근거는 천만 조회·밈 계정이었는데 facts(PROP-26·27)는 "인스타
#책추천를 쓴 뷰티 계정"·경쟁사였다. 재료가 쓴 천만 조회(`mega:`)·쇼츠(`shorts:`) 근거 행이 facts
후보에 없었고, 해시태그 줄에 "뷰티"가 박혀 있었고, "fuckjerry" 계정이 광고 근거로 올라왔다.

가짜 그래프·가짜 LLM만 쓴다(실제로 부르지 않는다).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import ad_words
from ax_contracts.schemas.data import (
    MegaViewItem,
    MemeShortsVideo,
    TrendMaterial,
    TrendReportEvidence,
)
from data_agent.skills import instagram as ig
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_insight as ti


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers_facts_materials", "test_trend_report.py")


@pytest.fixture
def report(tmp_path: Path):  # noqa: ANN201
    got = H._filled(tmp_path)  # noqa: SLF001
    ref = got.finished_at or got.created_at
    c = got.sections.content
    c.mega = [
        MegaViewItem(platform="instagram", account="memezar", item_id="m1", views=24_650_000,
                     posted_at=ref - timedelta(days=3), link="https://www.instagram.com/p/m1/"),
        MegaViewItem(platform="youtube", account="웃긴채널", item_id="y1", views=12_000_000),
    ]  # fmt: skip
    c.shorts = [MemeShortsVideo(video_id="v1", keyword="상황극", channel_title="쇼츠채널",
                                views=1_200_000, views_per_subscriber=3.5)]  # fmt: skip
    return got


def _material(*ids: str) -> TrendMaterial:
    return TrendMaterial(kind="format", text="반전 상황극 형식",
                         evidence_items=[TrendReportEvidence(id=i, text="근거", short="근거")
                                         for i in ids])  # fmt: skip


def _labels(report: Any) -> list[str]:
    return [f.label for f in ti.pick_facts(report) if f.label != "방향"]


# ---------------------------------------------------------------- 1. facts 순서


def test_materials_mega_and_shorts_come_first(report: Any) -> None:
    rec = report.sections.recommendation
    rec.materials = [_material("mega:1", "shorts:1"), _material("mega:2", "mega:1")]
    facts = [f for f in ti.pick_facts(report) if f.label != "방향"]
    assert facts[0].label == "릴스 조회 (@memezar, 3일 전)" and facts[0].value == "2,465만"
    assert facts[0].link == "https://www.instagram.com/p/m1/"
    assert facts[1].label == "쇼츠 '상황극' 조회 (쇼츠채널)"
    assert facts[1].value == "120만 · 구독자 대비 3.5배"
    assert facts[2].label == "유튜브 쇼츠 조회 (웃긴채널)" and facts[2].value == "1,200만"
    labels = [f.label for f in facts]
    assert len(labels) == len(set(labels))  # 중복 없이 (mega:1은 한 번)


def test_without_material_evidence_the_order_stays(report: Any) -> None:
    rec = report.sections.recommendation
    rec.materials, rec.beats = [], []
    rows = ti.row_facts(report)
    theme = [k for k in rows if k.startswith("theme:")]
    want = [rows[k][0] for k in dict.fromkeys([*theme, *ti._top(report, rows)])]  # noqa: SLF001
    assert _labels(report) == want[: len(_labels(report))]
    assert not any(x.startswith(("릴스 조회", "쇼츠 '")) for x in _labels(report))


def test_cap_twelve_and_direction_first_line_stay(report: Any) -> None:
    rec = report.sections.recommendation
    rec.materials = [_material("mega:1", "mega:2", "shorts:1")]
    rec.promo_style, rec.brand_exposure = "fun_first", "end_card"
    facts = ti.pick_facts(report)
    assert facts[0].label == "방향" and facts[1].label.startswith("릴스 조회")
    assert len(facts) <= ti.FACTS_MAX


def test_new_row_numbers_are_allowed_in_body(report: Any) -> None:
    report.sections.recommendation.materials = [_material("mega:1", "shorts:1")]
    facts = ti.pick_facts(report)
    allowed = ti.allowed_numbers(report, facts)
    assert {"2465", "3", "120", "3.5"} <= allowed
    assert ti.numbers(ti.body_md(report, facts)) <= allowed


# ---------------------------------------------------------------- 2. 해시태그 문구


def test_hashtag_line_has_no_industry_word(report: Any) -> None:
    rows = ti.row_facts(report)
    s = report.sections.hashtags.rows
    # 태그 이름(#클린뷰티)을 뺀 고정 문구에 업종 단어가 없다
    tags = [v[0].replace(s[int(k.split(":")[1]) - 1].tag, "")
            for k, v in rows.items() if k.startswith("hashtag:")]  # fmt: skip
    assert tags and all("뷰티" not in t for t in tags)
    assert all(t.endswith("계정 (이번 7일)") for t in tags)


def test_hashtag_line_uses_the_known_account_kind(report: Any) -> None:
    s = report.sections
    row = s.hashtags.rows[0]
    post = s.content.posts[0]
    row.used_by = [post.account]
    post.account_kind = "meme"
    want = f"인스타 {row.tag}를 쓴 밈·코미디 계정 (이번 7일)"
    assert ti.row_facts(report)["hashtag:1"][0] == want


# ---------------------------------------------------------------- 3. 욕설·성인 계정


def test_blocked_words_catch_joined_and_cased_text() -> None:
    assert ad_words.blocked_word(["fuckjerry"]) == "fuck"
    assert ad_words.blocked_word(["F.U.C.K jerry"]) == "fuck"
    assert ad_words.blocked_word(["오늘 씨 발 진짜"]) == "씨발"
    assert ad_words.blocked_word(["memezar", "책 읽는 고양이", None]) is None
    assert ad_words.mark(["fuckjerry"]) == ad_words.MARK == "광고 근거 제외: 표현"
    # 회사 설정 단어 (부분 일치, 대소문자·띄어쓰기 무시)
    assert ad_words.words_from({"blocked_words": ["Bad Word", 3, ""]}) == ("badword", "3")
    assert ad_words.blocked_word(["my_badword_page"], ["Bad Word"]) == "badword"
    assert ad_words.words_from({"blocked_words": "x"}) == ()


def test_blocked_account_leaves_evidence_but_keeps_raw_mark(report: Any) -> None:
    c = report.sections.content
    c.posts[0].account = "fuckjerry"
    c.posts[1].caption_excerpt = "19금 농담 모음"
    c.mega[0].account = "fuckjerry"
    count = len(c.posts)
    limits = tr.mark_ad_excluded(c)
    # 원자료 표에는 그대로 남고 표시만 붙는다
    assert len(c.posts) == count
    marks = {c.posts[0].ad_excluded, c.posts[1].ad_excluded, c.mega[0].ad_excluded}
    assert marks == {ad_words.MARK}
    assert c.mega[1].ad_excluded is None and c.shorts[0].ad_excluded is None
    assert limits and "fuckjerry" in limits[0] and "19금" not in limits[0]
    # 보고서 근거(결론·재료가 고르는 목록)와 facts에서 빠진다
    catalog = tr.evidence_catalog(report)
    assert "content:1" not in catalog and "content:2" not in catalog and "mega:1" not in catalog
    assert "mega:2" in catalog
    rows = ti.row_facts(report)
    assert "content:1" not in rows and "mega:1" not in rows
    report.sections.recommendation.materials = [_material("mega:1", "mega:2")]
    assert all("fuckjerry" not in f.label for f in ti.pick_facts(report))


def test_company_words_are_added_from_trends_toml(tmp_path: Path, report: Any) -> None:
    path = tmp_path / "trends.toml"
    path.write_text('[instagram]\nblocked_words = ["memezar"]\n', encoding="utf-8")
    words = ig.load_config({ig.TREND_CONFIG_ENV: str(path)}).blocked_words
    assert words == ("memezar",)
    tr.mark_ad_excluded(report.sections.content, words)
    assert report.sections.content.mega[0].ad_excluded == ad_words.MARK
    assert "mega:1" not in tr.evidence_catalog(report)
    assert ig.load_config({}).blocked_words == ()  # 설정이 없으면 기본 목록만
