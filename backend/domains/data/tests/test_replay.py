"""외부 API 응답 저장·재생 (DATA_EXTERNAL_MODE). 실제 네이버·YouTube를 부르지 않는다 (가짜 HTTP).

- record: 부르고 응답 본문을 저장한다. 키·토큰은 파일에 들어가지 않는다
- replay: 부르지 않고 저장된 응답만 쓴다. 할당량·하루 호출 수를 세지 않는다. 없으면 REPLAY_MISS
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import httpx
import pytest

from ax_contracts.schemas.data import TrendKeywordGroup, TrendScanInput
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import DailyCache, NaverDatalabConnector
from data_agent.connectors.youtube import YouTubeConnector

SECRET_ID = "naver-id-should-not-be-saved"
SECRET_KEY = "naver-secret-should-not-be-saved"
YT_KEY = "youtube-key-should-not-be-saved"

QUERY = TrendScanInput(
    keyword_groups=[TrendKeywordGroup(group_name="t1", keywords=["t1"])],
    start_date=date(2026, 11, 1),
    end_date=date(2026, 11, 3),
)
NAVER_PAYLOAD = {
    "startDate": "2026-11-01", "endDate": "2026-11-03", "timeUnit": "date",
    "results": [{"title": "t1", "keywords": ["t1"],
                 "data": [{"period": "2026-11-01", "ratio": 50},
                          {"period": "2026-11-03", "ratio": 100}]}],
}  # fmt: skip
VIDEO_PAYLOAD = {
    "items": [{
        "id": "v1",
        "snippet": {"publishedAt": "2026-11-01T00:00:00Z", "channelId": "c1", "title": "제목"},
        "statistics": {"viewCount": "123"},
    }]
}  # fmt: skip


def _client(payload: dict, calls: list[httpx.Request]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _env(tmp_path: Path, mode: str) -> dict[str, str]:
    return {
        "DATA_EXTERNAL_MODE": mode, "DATA_REPLAY_DIR": str(tmp_path / "replay"),
        "NAVER_HUB_CLIENT_ID": SECRET_ID, "NAVER_HUB_CLIENT_SECRET": SECRET_KEY,
        "YOUTUBE_API_KEY": YT_KEY,
    }  # fmt: skip


def test_naver_record_then_replay_without_calling(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    rec = NaverDatalabConnector(_env(tmp_path, "record"), client=_client(NAVER_PAYLOAD, calls),
                                cache=DailyCache())  # fmt: skip
    first = asyncio.run(rec.search_trend(QUERY, ages=("3", "4")))
    assert len(calls) == 1 and rec.calls_today == 1
    saved = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "replay").glob("*.json"))
    assert SECRET_ID not in saved and SECRET_KEY not in saved  # 키는 헤더로만, 파일에 없다
    again: list[httpx.Request] = []
    rep = NaverDatalabConnector(_env(tmp_path, "replay"), client=_client({}, again),
                                cache=DailyCache())  # fmt: skip
    second = asyncio.run(rep.search_trend(QUERY, ages=("3", "4")))
    assert again == [] and rep.calls_today == 0  # 부르지 않고 세지도 않는다
    assert second.series == first.series


def test_naver_replay_miss_is_unavailable_not_made_up(tmp_path: Path) -> None:
    rep = NaverDatalabConnector(_env(tmp_path, "replay"), cache=DailyCache())
    with pytest.raises(ConnectorUnavailable) as caught:
        asyncio.run(rep.search_trend(QUERY, ages=("5",)))
    assert caught.value.code == "REPLAY_MISS"


def test_youtube_record_then_replay_without_quota(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    rec = YouTubeConnector(_env(tmp_path, "record"), client=_client(VIDEO_PAYLOAD, calls))
    [first] = asyncio.run(rec.videos(["v1"]))
    assert len(calls) == 1 and rec.quota_used == 1
    saved = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "replay").glob("*.json"))
    assert YT_KEY not in saved
    again: list[httpx.Request] = []
    rep = YouTubeConnector(_env(tmp_path, "replay"), client=_client({}, again))
    [second] = asyncio.run(rep.videos(["v1"]))
    assert again == [] and rep.quota_used == 0
    assert (second.video_id, second.view_count, second.title) == ("v1", 123, "제목")
    with pytest.raises(ConnectorUnavailable) as caught:
        asyncio.run(rep.videos(["v2"]))
    assert caught.value.code == "REPLAY_MISS"


def test_unknown_mode_falls_back_to_live() -> None:
    assert NaverDatalabConnector({"DATA_EXTERNAL_MODE": "nope"}).mode == "live"
