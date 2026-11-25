"""분석 의뢰 표본 (11/26 D): 계정 언어(한글 비율) · 대상 맞음 · 사람 확인 · 한국어 계정 순위 앞 ·
인스타 상태. 코드 계산, 호출 없음.
"""

from __future__ import annotations

from ax_contracts.schemas.data import (
    InstagramRadarPost,
    RadarAccountMeta,
    TrendReportBrief,
    TrendReportBriefSources,
)
from data_agent.skills import trend_report_sample as rs
from data_agent.skills.trend_report import IgRead

SRC = TrendReportBriefSources(product="input", target="input", format="input",
                              competitors="input", keywords="input")  # fmt: skip


def _brief(ages: list[str] | None = None, product: str | None = "캣북스") -> TrendReportBrief:
    return TrendReportBrief(product=product, target_ages=ages or [], sources=SRC)


def _media(caption: str, n: int = 3) -> list[dict]:
    return [{"id": str(i), "timestamp": f"2026-11-2{i}T00:00:00Z", "caption": caption}
            for i in range(n)]  # fmt: skip


KO = "시험 기간에 보면 좋은 책 추천 모음입니다 다들 힘내세요"
EN = "Weekend vibes with the new collection, link in bio for more details"


def test_hangul_ratio_and_unknown_when_short() -> None:
    assert rs.hangul_ratio([KO]) == 1.0
    assert rs.hangul_ratio([EN]) == 0.0
    assert rs.hangul_ratio(["ㅋㅋ", "123 !!!"]) is None  # 글자가 적으면 모름
    mixed = rs.hangul_ratio([KO, EN])
    assert mixed is not None and 0.2 < mixed < rs.KOREAN_MIN  # 영문 글자가 더 많으면 한국어 아님


def test_languages_merge_sources_first_wins() -> None:
    got = rs.account_languages({"ko": _media(KO)}, {"ko": _media(EN), "en": _media(EN), "x": []})
    assert got == {"ko": 1.0, "en": 0.0, "x": None}


def test_sample_counts() -> None:
    languages = {"ko20": 1.0, "ko40": 1.0, "ko_nometa": 0.9, "en20": 0.0, "unknown": None}
    metas = [
        RadarAccountMeta(account="ko20", ages=["20s"], tag_source="human"),  # type: ignore[list-item]
        RadarAccountMeta(account="ko40", ages=["40s"], tag_source="ai"),  # type: ignore[list-item]
        RadarAccountMeta(account="en20", ages=["20s"], tag_source="ai"),  # type: ignore[list-item]
        RadarAccountMeta(account="meta_only", ages=["20s"]),  # type: ignore[list-item]
    ]
    s = rs.sample(_brief(["20"]), languages, metas, korean_first=True, connected=True, read=3)
    assert s.total == 6  # 언어 5 + 메타만 1
    assert s.korean == 3
    assert s.matched == 1  # 한국어 + 20대 태그 (ko20). 메타 없는 계정은 대상층을 몰라 뺀다
    assert s.human_checked == 1
    assert s.korean_first and s.instagram_connected and s.instagram_read == 3
    every = rs.sample(_brief(), languages, metas, korean_first=False, connected=None, read=None)
    assert every.matched == 3  # 대상층 전체면 한국어 계정 모두
    zero = rs.sample(_brief(["20"]), {}, [], korean_first=False, connected=False, read=0)
    assert (zero.total, zero.matched, zero.human_checked) == (0, 0, 0)


def _post(account: str, ratio: float) -> InstagramRadarPost:
    return InstagramRadarPost(account=account, media_id=account, metric="view_count", value=1,
                              account_median=1.0, ratio=ratio)  # fmt: skip


def test_korean_accounts_go_first_without_dropping() -> None:
    posts = [_post("en_big", 50.0), _post("ko_small", 3.0), _post("unknown", 9.0),
             _post("ko_mid", 5.0)]  # fmt: skip
    got = rs.korean_first(posts, {"en_big": 0.0, "ko_small": 1.0, "ko_mid": 0.8})
    assert [p.account for p in got] == ["ko_small", "ko_mid", "en_big", "unknown"]
    assert len(got) == len(posts)


def test_korea_target_and_ig_connected_flag() -> None:
    assert rs.korea_target(_brief(), None)
    assert rs.korea_target(_brief(product=None), "시험 기간 릴스")
    assert not rs.korea_target(_brief(product="Catbooks"), "exam season reels")
    assert IgRead().connected is True and IgRead().radar_media == {}


def test_report_without_instagram_key_marks_not_connected(tmp_path) -> None:  # noqa: ANN001
    import asyncio
    from datetime import UTC, datetime

    from data_agent.skills.trend_report import read_accounts

    class Store:
        def media(self, day, accounts):  # noqa: ANN001, ANN201
            return {}

    env = {"DATA_IG_RADAR_DB": str(tmp_path / "none.db")}
    got = asyncio.run(read_accounts(["a"], None, Store(), env, datetime(2026, 11, 25, tzinfo=UTC)))
    assert got.connected is False and got.calls == 0 and "a" in got.failed
