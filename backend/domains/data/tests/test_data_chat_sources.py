"""데이터분석 AI 대화의 새 재료 (11/20): 인스타 레이더·분석 의뢰·네이버 하루 몫·급상승 검색어.

11/20 13:18 "인스타 수집 잘 되고 있어?" → "제가 가진 데이터에 없어요". 저장된 값만 읽고(외부 호출·
다시 계산 없음) 토큰 값은 재료에 넣지 않는다. 저장소는 전부 tmp에 만든 가짜.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from data_agent import data_chat as dc
from data_agent import instagram_store as st
from data_agent import llm_budget
from data_agent.data_chat_store import DataChatStore

NOW = datetime(2026, 11, 20, 4, 18, tzinfo=UTC)  # 한국 13:18
TODAY = date(2026, 11, 20)
SECRET = "IGQVJsecret-token-value"


@pytest.fixture(autouse=True)
def budget(monkeypatch: pytest.MonkeyPatch) -> llm_budget.Budget:
    fresh = llm_budget.Budget(None)
    monkeypatch.setattr(llm_budget, "BUDGET", fresh)
    return fresh


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    from data_agent import (
        campaign_update,
        fresh_shorts,
        response_cache,
        trend_report_store,
        youtube_store,
    )
    from data_agent.connectors.instagram_graph import TOKEN_ENV, USER_ENV
    from data_agent.skills import instagram, trend_age

    monkeypatch.setattr(instagram, "ig_radar_accounts", lambda: None)  # 화면 저장 목록 안 씀
    trends = tmp_path / "trends.toml"
    trends.write_text('[instagram]\nradar_accounts = ["a_one", "b_two", "c_three"]\n'
                      "radar_batch_size = 2\nradar_batch_interval_min = 20\n"
                      "[age]\ndaily_calls = 200\n", encoding="utf-8")  # fmt: skip
    return {response_cache.DB_ENV: str(tmp_path / "responses.db"),
            fresh_shorts.DB_ENV: str(tmp_path / "fresh.db"),
            youtube_store.DB_ENV: str(tmp_path / "yt.db"),
            campaign_update.DB_ENV: str(tmp_path / "sends.db"),
            st.RADAR_ENV: str(tmp_path / "ig_radar.db"),
            st.AUDIO_ENV: str(tmp_path / "ig_audio.db"),
            trend_report_store.DB_ENV: str(tmp_path / "reports.db"),
            trend_age.DB_ENV: str(tmp_path / "trend_age.db"),
            "DATA_TREND_CONFIG": str(trends),
            TOKEN_ENV: SECRET, USER_ENV: "17841400000000000"}  # fmt: skip


def _radar(env: dict[str, str]) -> None:
    radar = st.RadarStore(env[st.RADAR_ENV])
    radar.save_cursor(TODAY, 2, NOW - timedelta(minutes=5), "h")
    radar.complete(date(2026, 11, 19), NOW - timedelta(hours=20), 3)
    radar.close()
    audio = st.AudioStore(env[st.AUDIO_ENV])
    audio.log("ig_business_discovery", TODAY, "rate_limited", "앱 시간당 한도 " + "가" * 300, NOW)
    audio.log("ig_audio", TODAY, "connected", None, NOW)
    audio.close()


def _report(env: dict[str, str], rid: str, status: str, product: str, at: datetime,
            handoff: dict | None = None) -> None:  # fmt: skip
    from data_agent.trend_report_store import TrendReportStore

    sections = {n: {"status": "known" if status != "running" else "waiting"}
                for n in ("search", "hashtags", "content", "competitors", "recommendation")}
    sections["search"]["status"] = "known"
    end = None if status == "running" else (at + timedelta(minutes=3)).isoformat()
    result = {"report_id": rid, "status": status, "created_at": at.isoformat(),
              "finished_at": end,
              "brief": {"product": product}, "sections": sections,
              "summary_line": "30대 남성은 독서모임 검색이 오름"}  # fmt: skip
    store = TrendReportStore(env["DATA_TREND_REPORT_DB"])
    store.create(rid, rid, at.date(), {}, "orchestrator", None, result, at)
    store.save(rid, result)
    if handoff:
        store.save_handoff(rid, handoff)
    store.close()


# ---------------------------------------------------------------- 묶음 고르기


@pytest.mark.parametrize(("question", "bundle"), [
    ("인스타 수집 잘 되고 있어?", "instagram"),
    ("레이더 몇 곳까지 돌았어?", "instagram"),
    ("분석 의뢰 어떻게 됐어?", "report"),
    ("TR-B835 보고서 끝났어?", "report"),
    ("네이버 오늘 얼마나 썼어?", "collect"),
    ("데이터랩 한도 남았어?", "collect"),
    ("급상승 검색어 뭐 있어?", "detect"),
    ("요즘 검색량 오른 거 알려줘", "detect"),
])  # fmt: skip
def test_questions_pick_the_new_bundles(question: str, bundle: str) -> None:
    assert bundle in dc.pick_bundles(question)
    assert not dc.is_trend_request(question)


def test_bare_token_stays_llm_usage_and_token_expiry_is_instagram() -> None:
    assert dc.pick_bundles("토큰 얼마나 썼어?") == ["collect"]  # 기존 평가 그대로
    assert "instagram" in dc.pick_bundles("토큰 만료 언제야?")


def test_report_questions_go_to_the_bundle_but_requests_still_make_a_card() -> None:
    assert dc.pick_bundles("보고서 어떻게 됐어?") == ["report"]
    ask = "30대 남성들이 좋아할만한 우리 회사 광고 영상 분석 의뢰 만들어주세요"
    assert dc.is_trend_request(ask)  # 의뢰 문장은 카드가 먼저 (_answer 순서)


def test_collect_instagram_is_still_refused_as_an_action() -> None:
    assert dc.is_action("인스타 수집해 줘")
    assert not dc.is_action("인스타 수집 잘 되고 있어?")


# ---------------------------------------------------------------- 읽기


def test_instagram_reads_cursor_cycles_runs_without_the_token(env: dict[str, str]) -> None:
    _radar(env)
    part = dc.read_instagram(env, NOW)
    assert part is not None
    f = part["facts"]
    assert f["레이더 계정 수(설정)"] == 3 and f["이번 바퀴 진행"] == "2/3곳"
    assert f["바퀴 시작일"] == "2026-11-20" and f["마지막 묶음 시각"] == "11월 20일 13:13"
    assert f["묶음 크기(설정)"] == "2곳" and f["묶음 간격(설정)"] == "20분"
    assert f["마지막 완료 바퀴"].startswith("2026-11-19")
    run = f["레이더 계정 읽기 마지막 기록"]
    assert run["상태"] == "rate_limited" and len(run["이유"]) == 200
    assert f["인기 오디오 마지막 기록"]["상태"] == "connected"
    assert f["토큰 만료까지"].startswith("모름")
    material = dc.read_material(["instagram"], env, NOW)
    text = json.dumps(material, ensure_ascii=False)
    assert SECRET not in text and "17841400000000000" not in text
    assert len(text) <= dc.MATERIAL_CHARS


def test_report_reads_the_last_five_with_handoff(env: dict[str, str]) -> None:
    for i in range(6):
        _report(env, f"tr_{i:04x}aaaa", "partial", f"제품{i}", NOW - timedelta(hours=6 - i))
    _report(env, "tr_b835ffff", "running", "캣북스", NOW,
            handoff={"outcome": "created", "proposal_id": "prop_1"})  # fmt: skip
    part = dc.read_report(env, NOW)
    assert part is not None
    items = part["facts"]["최근 보고서"]
    assert len(items) == 5
    first = items[0]
    assert first["번호"] == "TR-B835" and first["상태"] == "running" and first["진행"] == "20%"
    assert first["기획 전달"] == {"결과": "created", "제안 id": "prop_1"}
    assert first["끝난 시각"] is None and first["결론 한 줄"].startswith("30대")
    assert items[1]["진행"] == "100%"


def test_naver_shares_read_the_same_counters(env: dict[str, str],
                                             monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from data_agent.connectors import naver_datalab, naver_searchad, naver_shopping
    from data_agent.skills.trend_age import AgeStore

    class Counter:
        def __init__(self, calls: int) -> None:
            self.calls = calls

        def get(self, key: str) -> None:
            return None

    monkeypatch.setattr(naver_datalab, "_cache", Counter(41))
    monkeypatch.setattr(naver_searchad, "_cache", Counter(7))
    monkeypatch.setattr(naver_shopping, "_cache", Counter(0))
    store = AgeStore(env["DATA_TREND_AGE_DB"])
    store.add_calls(TODAY, 12, "keyword_trend")
    store.close()
    f = dc.read_collect(env, NOW)["facts"]
    assert f["네이버 데이터랩 오늘 사용"] == "41/1,000번"
    assert f["네이버 검색광고 오늘 사용"] == "7/300번"
    assert f["네이버 쇼핑인사이트 오늘 사용"] == "0/1,000번"
    assert f["데이터랩 중 연령대·급상승 공유 몫"] == "12/200번"
    assert f["네이버 다음 초기화"] == "한국 0시" and "네이버 확인 못 한 칸" not in f


def test_naver_unknown_counter_is_left_out_with_the_screen_note(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from data_agent.connectors import naver_searchad

    monkeypatch.setattr(naver_searchad, "_cache", object())  # 카운터 없음
    f = dc.read_collect(env, NOW)["facts"]
    assert "네이버 검색광고 오늘 사용" not in f
    assert f["네이버 확인 못 한 칸"].endswith(dc.NAVER_SCREEN)
    assert "검색광고" in f["네이버 확인 못 한 칸"]


def test_detect_adds_rising_search_words(env: dict[str, str]) -> None:
    from data_agent.response_cache import ResponseCache

    rising = [{"keyword": f"키워드{i}", "level": "rising", "ratio": 3.0 + i,
               "why_line": "뉴스 " + "가" * 100} for i in range(7)]  # fmt: skip
    rising += [{"keyword": "따뜻", "level": "warming"}, {"keyword": "보통", "level": "normal"}]
    out = {"window": "day", "generated_at": "2026-11-19T22:00:00+00:00", "rising": rising,
           "judged_on": "2026-11-19", "stale_reason": "데이터랩 하루 몫을 다 써서 11-19 판정",
           "candidate_counts": {"pool": 20, "news": 3}}  # fmt: skip
    cache = ResponseCache(env["DATA_TREND_RESPONSES_DB"])
    cache.put("week", ["ok", {**out, "window": "week"}, {}], "keyword_trend")
    cache.put("day", ["ok", out, {}], "keyword_trend")
    part = dc.read_detect(env, NOW)
    assert part is not None and part["basis"] == "급상승 검색어 · 11월 20일 07:00"
    f = part["facts"]["급상승 검색어"]
    assert len(f["급상승"]) == 5 and f["급상승"][0]["배수"] == 3.0
    assert len(f["급상승"][0]["왜"]) == 60
    assert f["오르는 중 수"] == 1 and f["후보 출처별 개수"] == {"pool": 20, "news": 3}
    assert f["직전 판정을 다시 쓴 날"] == "2026-11-19"


def test_each_new_reader_without_a_store_is_none_and_says_no_record(
    env: dict[str, str],
) -> None:
    assert dc.read_instagram(env, NOW) is None
    assert dc.read_report(env, NOW) is None
    assert dc.read_report({}, NOW) is None  # DB 경로 없음(메모리 저장소)도 없음으로
    got = dc.read_material(["instagram", "report"], env, NOW)
    assert got == {"bundles": [], "missing": ["instagram", "report"]}
    service = dc.DataChatService(DataChatStore(":memory:"), material=lambda b: got,
                                 llm=lambda: object())  # fmt: skip

    async def run() -> str:
        th = service.send("재현", None, "인스타 레이더 어때?")
        await service.wait_idle()
        return service.get("재현", th["id"])["messages"][-1]["text"]

    assert asyncio.run(run()) == "아직 계산된 결과가 없어요. 인스타 레이더 화면에서 확인해 주세요."


def test_readers_call_no_external_service(env: dict[str, str],
                                          monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("외부 호출")

    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(httpx.AsyncClient, "send", boom)
    _radar(env)
    _report(env, "tr_c0deffff", "done", "책", NOW)
    got = dc.read_material(["instagram", "report", "collect", "detect"], env, NOW)
    assert {b["name"] for b in got["bundles"]} == {"인스타 레이더", "분석 의뢰 보고서", "수집 장부"}
    assert got["missing"] == ["detect"]
