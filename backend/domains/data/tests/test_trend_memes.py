"""trend_memes: 요즘 유행·밈. 가짜 표본·가짜 LLM·가짜 네이버로만 본다 (실제 API를 부르지 않음)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.llm import LLMNotConfiguredError
from ax_contracts.schemas.data import TrendMemesInput
from data_agent.connectors import CatalogProduct
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.skills import topic_category
from data_agent.skills import trend_memes as tm
from data_agent.skills.product_link import CatalogRead
from data_agent.skills.topic_category import CategoryResult
from data_agent.skills.trend_age import AgeStore

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)  # 한국 11-18 12:00
CONFIG = tm.MemeConfig()


def _v(vid: str, title: str, channel: str | None, days_ago: float, *, tags: tuple[str, ...] = (),
       views: int = 100, known: bool = True) -> tm.Video:  # fmt: skip
    at = NOW - timedelta(days=days_ago) if known else None
    return tm.Video(vid, title, f"ch:{channel}" if channel else None, channel, views, tags, at)


def _filler(n: int, days_ago: float, prefix: str = "f") -> list[tm.Video]:
    """서로 겹치지 않는 영상 (흔한 단위 비율을 낮추고 기간 표본을 채운다)."""
    return [_v(f"{prefix}{i}", f"z{prefix}{i}a z{prefix}{i}b", f"c{prefix}{i}", days_ago)
            for i in range(n)]  # fmt: skip


def _meme(n: int, channels: int, days_ago: float, prefix: str = "m") -> list[tm.Video]:
    return [
        _v(f"{prefix}{i}", f"책장 털기 챌린지 {i}호 #책장털기", f"ch{i % channels}", days_ago,
           views=1000 - i)  # fmt: skip
        for i in range(n)
    ]


# ---------------------------------------------------------------- 정규화·단위


def test_normalize_nfc_lower_hash_and_edge_punctuation() -> None:
    decomposed = unicodedata.normalize("NFD", "#Café!!")
    assert tm.normalize(decomposed) == "café"
    assert tm.normalize('"이거 나만 몰랐어?"') == "이거 나만 몰랐어"
    assert tm.normalize("  ##책장털기…  ") == "책장털기"
    assert tm.compact("책장 털기") == tm.compact("#책장털기") == "책장털기"


def test_units_hashtags_tags_phrases_and_quotes() -> None:
    units = tm.units_of('"이거 나만 몰랐어?" 진짜로 #책장털기', ["Shorts", "#책장털기"])
    keys = set(units)
    assert "hashtag:책장털기" in keys and "hashtag:shorts" in keys
    assert "phrase:이거 나만 몰랐어" in keys  # 따옴표 안 구절
    assert {"phrase:이거 나만", "phrase:나만 몰랐어", "phrase:몰랐어 진짜로"} <= keys
    assert all(len(k.split(":", 1)[1].split()) <= 4 for k in keys if k.startswith("phrase:"))
    assert units["hashtag:책장털기"].surface == "#책장털기"


# ---------------------------------------------------------------- 묶기·기준


def test_merges_units_with_overlapping_videos_and_passes_criteria() -> None:
    videos = _meme(6, 4, 2) + _meme(1, 1, 10, "p") + _filler(40, 3) + _filler(40, 9, "g")
    built = tm.build(videos, tm.windows("week", NOW), CONFIG)
    assert len(built.candidates) == 1
    c = built.candidates[0]
    # 해시태그와 구절이 같은 영상들이라 하나로 묶였다
    assert "#책장털기" in c.members and "책장 털기" in c.members
    assert c.channels == 4 and c.videos == 6
    assert c.ratio_status == "known" and c.ratio == 6.0 and c.previous_videos == 1
    assert c.video_ids[0] == "m0"  # 조회수 순


def test_new_when_previous_window_has_sample_but_not_this_unit() -> None:
    videos = _meme(5, 3, 1) + _filler(40, 2) + _filler(10, 10, "g")
    c = tm.build(videos, tm.windows("week", NOW), CONFIG).candidates[0]
    assert c.ratio_status == "new" and c.ratio is None and "새로 등장" in (c.ratio_reason or "")


def test_unknown_ratio_when_no_previous_sample() -> None:
    built = tm.build(_meme(5, 3, 1) + _filler(40, 2), tm.windows("week", NOW), CONFIG)
    c = built.candidates[0]
    assert c.ratio_status == "unknown" and c.ratio is None and c.ratio_reason == tm.NO_PREVIOUS
    assert built.previous == 0


COUNT = tm.MemeConfig(judge_by="count")  # 예전 판정 (영상 수 배수)


def test_below_criteria_channels_and_ratio() -> None:
    few_channels = _meme(6, 2, 1) + _filler(40, 2) + _filler(10, 10, "g")
    built = tm.build(few_channels, tm.windows("week", NOW), CONFIG)
    assert built.candidates == [] and built.below >= 1
    # count 모드(예전 결과 그대로): 직전 4개 → 이번 6개 = 1.5배 < 2배
    slow = _meme(6, 4, 1) + _meme(4, 4, 10, "p") + _filler(60, 2) + _filler(10, 10, "g")
    assert tm.build(slow, tm.windows("week", NOW), COUNT).candidates == []
    # share 모드: 직전 표본이 14개(< 30)라 몫을 비교하지 않음 → unknown + 이유
    got = tm.build(slow, tm.windows("week", NOW), CONFIG).candidates
    assert [c.ratio_status for c in got] == ["unknown"] and "몫을 비교하지 않음" in (
        got[0].ratio_reason or "")
    # share 모드, 직전 표본 충분: 몫 6/72 ÷ 4/44 = 0.92배 < 2배 → 아님
    enough = slow + _filler(30, 10, "h")
    assert tm.build(enough, tm.windows("week", NOW), CONFIG).candidates == []


def test_share_passes_when_sample_shrinks_but_share_rises() -> None:
    """지난주 표본 300 → 이번 주 100: 영상 수는 10 → 12(1.2배)라 count로는 떨어지지만
    몫은 3.3% → 12%(3.6배)라 share로는 통과 (새 서버 11/19: 표본 307 → 104)."""
    videos = (_meme(12, 4, 1) + _meme(10, 4, 10, "p") + _filler(88, 2)
              + _filler(290, 10, "g"))  # fmt: skip
    win = tm.windows("week", NOW)
    assert tm.build(videos, win, COUNT).candidates == []
    c = tm.build(videos, win, CONFIG).candidates[0]
    assert c.judged_by == "share" and c.ratio == 1.2 and c.previous_videos == 10
    assert c.share == round(12 / 100, 4) and c.previous_share == round(10 / 300, 4)
    assert c.share_ratio == round(c.share / c.previous_share, 2) and c.share_ratio >= 2.0
    assert "몫이 지난주의 2배 이상" in tm.criteria_text(CONFIG)
    assert "몫" not in tm.criteria_text(COUNT)


def test_judge_by_setting(tmp_path: Path) -> None:
    cfg = tmp_path / "trends.toml"
    cfg.write_text('[memes]\njudge_by = "count"\nmin_previous_sample = 50\n', encoding="utf-8")
    got = tm.load_config({tm.TREND_CONFIG_ENV: str(cfg)})
    assert got.judge_by == "count" and got.min_previous_sample == 50
    assert tm.MemeConfig().judge_by == "share" and tm.MemeConfig().min_previous_sample == 30


def test_exclude_tags_seeds_and_stop_share_are_skipped_with_reasons() -> None:
    common = [_v(f"s{i}", f"q{i}a q{i}b", f"cs{i}", 1, tags=("Shorts", "캣북스")) for i in range(6)]
    # "흔한태그"는 표본 20%에 붙는다 (기준 15%)
    loud = [_v(f"l{i}", f"r{i}a r{i}b", f"cl{i}", 1, tags=("흔한태그",)) for i in range(12)]
    videos = common + loud + _filler(42, 2) + _filler(10, 10, "g")
    built = tm.build(videos, tm.windows("week", NOW), CONFIG, ["캣북스"])
    kinds = {s.name: s.kind for s in built.skipped}
    assert kinds.get("#Shorts") == "exclude_tag" and kinds.get("#캣북스") == "exclude_tag"
    assert kinds.get("#흔한태그") == "stop_share"
    assert built.candidates == []


def test_unknown_published_is_counted_not_used() -> None:
    videos = _meme(5, 3, 1) + [_v("u1", "책장 털기 챌린지 #책장털기", "x", 0, known=False)]
    built = tm.build(videos + _filler(40, 2), tm.windows("week", NOW), CONFIG)
    assert built.unknown_published == 1 and built.candidates[0].videos == 5


def test_day_window_compares_with_same_weekday_average() -> None:
    win = tm.windows("day", NOW)
    yesterday = win.current[0] + timedelta(hours=12)
    days_ago = (NOW - yesterday).total_seconds() / 86400
    videos = _meme(6, 3, days_ago) + _filler(40, days_ago)
    # 지난 같은 요일 두 번: 한 번은 이 유행 1개, 한 번은 표본만 (나머지 두 주는 표본 없음)
    videos += _meme(1, 1, days_ago + 7, "p") + _filler(5, days_ago + 7, "g")
    videos += _filler(5, days_ago + 14, "h")
    built = tm.build(videos, win, CONFIG, day=True)
    c = built.candidates[0]
    assert built.previous_days == 2 and c.previous_videos == 0.5 and c.ratio == 12.0


# ---------------------------------------------------------------- skill 전체 (가짜 LLM·네이버·상품)


class FakeLLM:
    def __init__(self, line: str = "자기 책장에서 안 읽은 책을 꺼내 보여 주는 형식") -> None:
        self.line = line
        self.asked: list[dict[str, str]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.asked.append(dict(untrusted or {}))
        names = [k for k in (untrusted or {}) if "_" not in k]
        return schema(items=[{"no": n, "description": self.line} for n in range(1, len(names) + 1)])

    async def embed(self, texts, context_id=None):  # noqa: ANN001, ANN201
        raise LLMNotConfiguredError("embed")


class FakeNaver:
    daily_limit = 1000

    def __init__(self) -> None:
        self.calls_today = 0

    async def search_trend(self, query, ages=()):  # noqa: ANN001, ANN201
        self.calls_today += 1
        g = query.keyword_groups[0]
        start, end = query.start_date, query.end_date
        days = (end - start).days + 1
        points = tuple(
            (start + timedelta(days=i), 40.0 if i >= days - 7 else 10.0 + (i % 3) * 0.3)
            for i in range(days)
        )  # fmt: skip
        series = TrendSeries(g.group_name, tuple(g.keywords), points)
        return TrendResponse(start, end, "date", (series,))


def _cats(given: dict[str, CategoryResult]):  # noqa: ANN202
    table = {"긴급뉴스": _cat("뉴스·사건"), **given}  # 표본의 뉴스 묶음은 늘 뉴스로 분류

    async def fake(topics, llm, *, cache=None, context_id=None):  # noqa: ANN001, ANN202
        return {t.keyword: table[t.keyword] for t in topics if t.keyword in table}

    return fake


def _cat(category: str, **kw: Any) -> CategoryResult:
    return CategoryResult(category, None, 1, is_person=kw.get("is_person", False),
                          real_entity=kw.get("real_entity", False))  # fmt: skip


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {"DATA_TREND_CONFIG": str(tmp_path / "none.toml"),
            "DATA_TREND_MEMES_DB": str(tmp_path / "memes.db"),
            "DATA_TREND_AGE_DB": str(tmp_path / "age.db")}  # fmt: skip


async def _catalog() -> CatalogRead:
    book = CatalogProduct(product_id="b1", name="책장 정리의 기술", category_code="c")
    return CatalogRead([book], {"c": "자기계발"})


def _videos() -> list[tm.Video]:
    other = [
        _v(f"n{i}", f"속보 화재 현장 {i}보 #긴급뉴스", f"nc{i % 3}", 1, views=50) for i in range(5)
    ]  # fmt: skip
    return _meme(6, 4, 2) + other + _filler(40, 3) + _filler(30, 9, "g")


def test_skill_end_to_end_with_person_news_exclusion(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(topic_category, "classify_topics", _cats({
        "책장털기": _cat("유머"), "긴급뉴스": _cat("뉴스·사건"),
    }))  # fmt: skip
    llm = FakeLLM()
    result = asyncio.run(tm.trend_memes(
        TrendMemesInput(), llm, env=env, connector=FakeNaver(), now=NOW,
        catalog_provider=_catalog, videos=_videos(),
    ))  # fmt: skip
    out = result.output
    assert out.status == "known" and [i.name for i in out.items] == ["#책장털기"]
    item = out.items[0]
    assert item.channels == 4 and item.videos == 6 and item.ratio_status == "new"
    assert item.ad_material == "ok" and item.search.status == "rising" and item.search.ratio
    assert [p.product_id for p in item.products] == ["b1"]  # 이름 일치 (product_link 기준)
    assert item.description and len(item.top_videos) == 2
    assert item.top_videos[0].video_url == "https://www.youtube.com/shorts/m0"
    assert {s.name: s.kind for s in out.skipped}.get("#긴급뉴스") == "category"
    assert out.naver_calls == 1
    # 대표 제목은 데이터 블록(untrusted)에만, 최대 5개
    asked = llm.asked[0]
    assert asked["유행1"] == "#책장털기" and "유행1_제목6" not in asked and "유행1_제목5" in asked
    # 설명 하루 캐시: 다시 불러도 LLM을 부르지 않는다
    asyncio.run(tm.trend_memes(TrendMemesInput(), llm, env=env, connector=FakeNaver(), now=NOW,
                               catalog_provider=_catalog, videos=_videos()))  # fmt: skip
    assert len(llm.asked) == 1


def test_person_name_is_skipped(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(topic_category, "classify_topics",
                        _cats({"책장털기": _cat("연예·스포츠", is_person=True)}))  # fmt: skip
    out = asyncio.run(tm.trend_memes(TrendMemesInput(), FakeLLM(), env=env, now=NOW,
                                     videos=_videos())).output  # fmt: skip
    assert out.items == [] and {s.name: s.kind for s in out.skipped}["#책장털기"] == "person"
    assert out.reason and "기준을 넘은 유행이 없음" in out.reason


def test_llm_numbers_are_dropped(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(topic_category, "classify_topics", _cats({"책장털기": _cat("유머")}))
    out = asyncio.run(tm.trend_memes(TrendMemesInput(), FakeLLM("영상 41개가 몰린 형식"), env=env,
                                     now=NOW, videos=_videos())).output  # fmt: skip
    item = out.items[0]
    assert item.description is None and "숫자" in (item.description_reason or "")
    assert tm.check_line("숫자 없는 한 줄") == ("숫자 없는 한 줄", None)


def test_naver_limit_marks_not_checked(env: dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                       tmp_path: Path) -> None:  # fmt: skip
    config = tmp_path / "trends.toml"
    config.write_text("[age]\ndaily_calls = 0\n", encoding="utf-8")
    env = {**env, "DATA_TREND_CONFIG": str(config)}
    monkeypatch.setattr(topic_category, "classify_topics", _cats({"책장털기": _cat("유머")}))
    naver = FakeNaver()
    out = asyncio.run(tm.trend_memes(TrendMemesInput(), FakeLLM(), env=env, connector=naver,
                                     now=NOW, videos=_videos())).output  # fmt: skip
    assert out.items[0].search.status == "not_checked"
    assert out.items[0].search.reason == tm.SEARCH_CAPPED and naver.calls_today == 0


def test_naver_calls_are_counted_in_shared_budget(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(topic_category, "classify_topics", _cats({"책장털기": _cat("유머")}))
    store = AgeStore(env["DATA_TREND_AGE_DB"])
    try:
        asyncio.run(tm.trend_memes(TrendMemesInput(), FakeLLM(), env=env, connector=FakeNaver(),
                                   now=NOW, videos=_videos(), age_store=store))  # fmt: skip
        assert store.calls_by_caller(date(2026, 11, 18)) == {"trend_memes": 1}
    finally:
        store.close()


def test_empty_sample_is_unknown_and_writes_no_file(env: dict[str, str], tmp_path: Path) -> None:
    out = asyncio.run(tm.trend_memes(TrendMemesInput(window="day"), FakeLLM(), env=env, now=NOW,
                                     videos=[])).output  # fmt: skip
    assert out.status == "unknown" and out.items == [] and out.window == "day"
    assert not Path(env["DATA_TREND_MEMES_DB"]).exists()
    assert not Path(env["DATA_TREND_AGE_DB"]).exists()


def test_load_videos_dedupes_and_reads_both_stores(tmp_path: Path) -> None:
    shorts = tmp_path / "shorts.db"
    conn = sqlite3.connect(shorts)
    conn.executescript(
        "CREATE TABLE searched_videos (day TEXT, video_id TEXT, category_id TEXT, title TEXT,"
        " channel_title TEXT, duration_s INTEGER, views INTEGER, tags TEXT, language TEXT);"
        "CREATE TABLE published (video_id TEXT, published_at TEXT, at TEXT);"
        "CREATE TABLE video_channels (video_id TEXT, channel_id TEXT, at TEXT);"
    )
    rows = [("2026-11-16", "a", "20", "책장 털기", "채널A", 30, 5, json.dumps(["책"]), "ko"),
            ("2026-11-17", "a", "20", "책장 털기", "채널A", 30, 9, json.dumps(["책"]), "ko"),
            ("2026-11-17", "long", "20", "긴 영상", "채널B", 600, 9, "[]", "ko")]  # fmt: skip
    conn.executemany("INSERT INTO searched_videos VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO published VALUES ('a', '2026-11-16T00:00:00+00:00', 'x')")
    conn.execute("INSERT INTO video_channels VALUES ('a', 'UCa', 'x')")
    conn.commit()
    conn.close()
    from data_agent.connectors.youtube import VideoStat
    from data_agent.youtube_store import Row, SnapshotStore

    yt = tmp_path / "yt.db"
    store = SnapshotStore(yt)
    stat = VideoStat("p", "UCp", "20", NOW - timedelta(days=1), 77, 1, 1, ("밈",), rank=1)
    store.save(NOW, [Row(stat, ("popular",))])
    store.close()
    conn = sqlite3.connect(yt)
    conn.execute("INSERT INTO video_details VALUES ('p', '채널P', 20, 'x')")
    conn.execute("INSERT INTO video_titles VALUES ('p', '인기 숏츠 제목', 'x')")
    conn.commit()
    conn.close()
    env = {"DATA_SHORTS_DB": str(shorts), "DATA_YOUTUBE_DB": str(yt),
           "DATA_TREND_CONFIG": str(tmp_path / "none.toml")}  # fmt: skip
    got = {v.video_id: v for v in tm.load_videos(env, NOW)}
    assert set(got) == {"a", "p"}  # 긴 영상은 숏츠가 아님
    assert got["a"].views == 9 and got["a"].channel == "ch:UCa" and got["a"].published_at
    assert got["p"].channel == "ch:UCp" and got["p"].title == "인기 숏츠 제목"
