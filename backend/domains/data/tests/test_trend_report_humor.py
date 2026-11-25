"""웃음 재료 · 상황과 형식 · 추천 형식 카드 (11/26 E). 실제 LLM·인스타를 부르지 않는다.

- 원문과 연속 10자 넘게 같은 요약은 버린다 · 공지·광고 게시물은 재료로 안 쓴다
- 오디오 "Reels Sound - <형식>" 집계 · 새로 N일(관측 3일 미만)
- 카드 파일 읽기·형식 검사 · 형식 id 검증 · 추천 순서
- LLM 실패면 humor_status unknown + 이유 (보고서는 계속)
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import format_cards as fc
from ax_contracts.schemas.data import TrendReport
from data_agent.skills import trend_report_humor as th
from data_agent.skills import trend_report_topic as tt

FIXTURE = Path(__file__).parent / "fixtures" / "trend_report_example.json"
NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
DAY = date(2026, 11, 25)


class FakeLLM:
    def __init__(self, answer: dict[str, Any] | None = None, error: Exception | None = None):
        self.answer, self.error = answer or {"situations": []}, error
        self.calls: list[dict[str, Any]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append(dict(untrusted or {}))
        if self.error:
            raise self.error
        return schema.model_validate(self.answer)


def _report() -> TrendReport:
    r = TrendReport.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    post = r.sections.content.posts[0]
    post.caption_excerpt = "시험 일주일 전인데 계획표만 세 번째 새로 쓰는 중 #시험기간"
    ad = post.model_copy(update={"media_id": "ad1", "account": "brandco",
                                 "notice_ad": "공지 낱말 티켓",
                                 "caption_excerpt": "콘서트 티켓 예매 오픈 안내"})  # fmt: skip
    r.sections.content.posts.append(ad)
    return r


def test_card_file_loads_and_is_valid() -> None:
    cards = fc.load_cards()
    assert 10 <= len(cards) <= 15
    ids = [c.id for c in cards]
    assert len(ids) == len(set(ids)) and all(i.startswith("F-") for i in ids)
    for c in cards:
        assert abs(sum(b.ratio for b in c.beats) - 1) < 0.01
        assert sum(b.punch for b in c.beats) == 1
        assert c.punchline in fc.PUNCHLINES and c.ai_difficulty in fc.DIFFICULTIES
        # 추천에 쓰는 카드는 한 화면 인물 2명 이하
        assert not c.recommend or c.people <= fc.MAX_PEOPLE


def test_feasible_card_rules_11_26() -> None:
    """한 화면 인물 2명 이하 · 같은 사람 둘 한 화면 금지 · 타임랩스·화면 나누기 없음."""
    got = fc.by_id()
    assert got["F-14"].recommend is False and got["F-14"].people > fc.MAX_PEOPLE
    assert all(c.recommend for cid, c in got.items() if cid != "F-14")
    assert "타임랩스" not in got["F-09"].name and "Timelapse" not in got["F-09"].audio_names
    assert any("컷" in b.label for b in got["F-09"].beats)
    assert "둘로 나누" not in got["F-06"].hook and "날짜 자막" in got["F-06"].hook
    assert "번갈아 컷" in got["F-08"].hook and "한 화면에 같은 사람 둘 금지" in got["F-08"].hook


def test_crowded_card_must_not_be_recommendable(tmp_path: Path) -> None:
    bad = tmp_path / "cards.toml"
    body = ('[[cards]]\nid = "F-01"\nname = "x"\nbeats = [["a", 0.5], ["b", 0.5]]\n'
            'punch_beat = 1\npunchline = "표정"\npeople = 3\n'
            'ai_difficulty = "어려움"\n')  # fmt: skip
    bad.write_text(body, encoding="utf-8")
    with pytest.raises(fc.FormatCardError):
        fc.load_cards(bad)
    bad.write_text(body + "recommend = false\n", encoding="utf-8")
    assert fc.load_cards(bad)[0].recommend is False


def test_not_recommendable_card_is_never_picked() -> None:
    from ax_contracts.schemas.data import TrendFormatSignal, TrendHumorSituation

    cards = fc.cards()
    sit = [TrendHumorSituation(text="a", format_id="F-14", source_kind="shorts_title")]
    sig = [TrendFormatSignal(name="Squad", count=3, days_seen=5, format_id="F-14")]
    assert th.recommend(sit, sig, cards) == []
    raw = th._Situations.model_validate({"situations": [
        {"text": "다 같이 놀라는 순간", "format_id": "F-14", "source": "shorts_title"}]})
    got = th.clean(raw, [("shorts_title", "아무 제목")], [c.id for c in cards if c.recommend])
    assert got[0].format_id == ""  # 형식 추정에서도 쓰지 않는다


def test_bad_card_file_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "cards.toml"
    bad.write_text('[[cards]]\nid = "X1"\nname = "x"\nbeats = [["a", 0.5], ["b", 0.5]]\n'
                   'punch_beat = 0\npunchline = "자막"\npeople = 1\nai_difficulty = "쉬움"\n',
                   encoding="utf-8")  # fmt: skip
    with pytest.raises(fc.FormatCardError):
        fc.load_cards(bad)
    bad.write_text('[[cards]]\nid = "F-01"\nname = "x"\nbeats = [["a", 0.5], ["b", 0.4]]\n'
                   'punch_beat = 0\npunchline = "자막"\npeople = 1\nai_difficulty = "쉬움"\n',
                   encoding="utf-8")  # fmt: skip
    with pytest.raises(fc.FormatCardError):
        fc.load_cards(bad)


def test_copied_summary_is_dropped() -> None:
    src = [("reels_caption", "시험 일주일 전인데 계획표만 세 번째 새로 쓰는 중")]
    assert th.copies("계획표만 세 번째 새로 쓰는 중", [t for _, t in src])  # 이어서 13자 같음
    assert not th.copies("계획표만 다시 쓰는 시험 전", [t for _, t in src])
    raw = th._Situations.model_validate({"situations": [
        {"text": "계획표만 세 번째 새로 쓰는 중", "format_id": "F-07", "source": "reels_caption"},
        {"text": "시험 전 계획만 또 세우는 나", "format_id": "f-02", "source": "reels_caption"},
        {"text": "딱 5분 쉬었는데 아침", "format_id": "F-99", "source": "모름"},
    ]})  # fmt: skip
    got = th.clean(raw, src, ["F-02", "F-07"])
    assert [s.text for s in got] == ["시험 전 계획만 또 세우는 나", "딱 5분 쉬었는데 아침"]
    assert got[0].format_id == "F-02"  # 소문자도 카드 id로
    assert got[1].format_id == "" and got[1].source_kind == "reels_caption"  # 없는 id·종류는 비움


def test_notice_ad_posts_are_not_material_and_originals_stay_out() -> None:
    r = _report()
    tt.apply(r, None)  # 공지·광고 → excluded
    given = th.sources(r)
    texts = [t for _, t in given]
    assert any("계획표" in t for t in texts)
    assert not any("티켓" in t for t in texts)
    llm = FakeLLM({"situations": [{"text": "시험 전 계획만 또 세우는 나", "format_id": "F-02",
                                    "source": "reels_caption"}]})  # fmt: skip
    calls = asyncio.run(th.humor_section(r, llm, {}, NOW, audio={}))
    assert calls == 1 and r.humor_status == "known"
    assert [s.text for s in r.humor_situations] == ["시험 전 계획만 또 세우는 나"]
    dumped = json.dumps([s.model_dump() for s in r.humor_situations], ensure_ascii=False)
    assert "permalink" not in dumped and "계획표만 세 번째" not in dumped  # 원문·링크 없음


def _audio(names_by_day: dict[int, list[str]]) -> dict[date, list[dict]]:
    return {DAY - timedelta(days=d): [{"title": f"Reels Sound - {n}", "artist": "x"} for n in ns]
            for d, ns in names_by_day.items()}  # fmt: skip


def test_audio_format_signals_and_new_chip() -> None:
    cards = fc.cards()
    days = _audio({0: ["Before & After", "Before & After", "Listicle", "Lip Sync"],
                   1: ["Before & After", "Listicle"], 2: ["Before & After"]})  # fmt: skip
    days[DAY].append({"title": "아무 노래", "artist": "누군가"})
    got = {s.name: s for s in th.format_signals(days, cards)}
    assert [(s.name, s.count) for s in th.format_signals(days, cards)][0] == ("Before & After", 2)
    assert got["Before & After"].days_seen == 3 and not got["Before & After"].new
    assert got["Listicle"].days_seen == 2 and got["Listicle"].new  # 관측 3일 미만
    assert got["Before & After"].format_id == "F-05" and got["Listicle"].format_id == "F-04"
    assert got["Lip Sync"].format_id is None
    assert "아무 노래" not in got
    assert th.format_signals({}, cards) == []


def test_recommend_order_easy_first_on_tie() -> None:
    from ax_contracts.schemas.data import TrendFormatSignal, TrendHumorSituation

    cards = fc.cards()
    sit = [TrendHumorSituation(text="a", format_id="F-02", source_kind="shorts_title"),
           TrendHumorSituation(text="b", format_id="F-03", source_kind="shorts_title")]  # fmt: skip
    # F-02(쉬움) = F-03(보통) = 2점 → 쉬운 것 먼저. 신호 F-05 4개(최대 3) = 3점 → 맨 앞
    sig = [TrendFormatSignal(name="Before & After", count=4, days_seen=5, format_id="F-05")]
    assert th.recommend(sit, sig, cards) == ["F-05", "F-02", "F-03"]
    new = [TrendFormatSignal(name="Listicle", count=2, days_seen=1, new=True, format_id="F-04")]
    assert th.recommend([], new, cards) == ["F-04"]  # 새 신호는 반(1점)이지만 점수가 있으면 추천
    assert th.recommend([], [], cards) == []


def test_llm_failure_marks_section_unknown_and_keeps_going() -> None:
    r = _report()
    tt.apply(r, None)
    calls = asyncio.run(th.humor_section(r, FakeLLM(error=RuntimeError("down")), {}, NOW,
                                         audio=_audio({0: ["Before & After"]})))  # fmt: skip
    assert calls == 1
    assert r.humor_status == "unknown" and "RuntimeError" in (r.humor_reason or "")
    assert r.humor_situations == []
    assert r.recommended_formats == ["F-05"]  # 형식 신호로는 추천을 계속
    assert [c.id for c in r.recommended_cards] == ["F-05"]
    assert sum(b.punch for b in r.recommended_cards[0].beats) == 1


def test_no_material_is_skipped_without_llm() -> None:
    r = _report()
    r.sections.content.posts = []
    r.sections.hashtags.rows = []
    r.sections.content.shorts = []
    llm = FakeLLM()
    assert asyncio.run(th.humor_section(r, llm, {}, NOW, audio={})) == 0
    assert llm.calls == [] and r.humor_status == "skipped"
