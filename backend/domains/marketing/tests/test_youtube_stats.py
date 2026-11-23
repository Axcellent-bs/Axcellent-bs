"""올린 유튜브 영상 성과(`youtube_video_stats`)·올린 목록(`youtube_uploads_list`). 가짜 구글 API.

- 회사 토큰으로 videos.list(part=statistics)를 읽어 조회수·좋아요·댓글을 준다
- 숨긴 좋아요·댓글은 None(모름, 0과 구분), 못 찾은 영상은 not_found
- 기록 id로 물으면 render.youtube.video_id로 찾고, 없는 기록은 missing_records
- 5분 캐시(두 번째는 구글을 다시 부르지 않음), 연결 없음·HTTP 오류는 unknown + 사유
- 키·토큰 값은 응답에 없다
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import creative_records as cr
from marketing_agent import handlers
from marketing_agent import youtube as yt
from marketing_agent import youtube_stats as ys

CARDS = [
    Path(__file__).resolve().parents[1] / "agent-card.json",
    Path(__file__).resolve().parents[3] / "services" / "coupon" / "agent-card.json",
]
VID_A, VID_B, VID_GONE = "vidAAAAAAAA", "vidBBBBBBBB", "vidGONEGONE"


class Google:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.stats_calls: list[dict[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == yt.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "ya29.DUMMY"})
        if url.startswith(ys.STATS_URL):
            assert request.headers["Authorization"] == "Bearer ya29.DUMMY"
            params = dict(request.url.params)
            self.stats_calls.append(params)
            if self.status != 200:
                return httpx.Response(self.status, json={"error": {"message": "quota"}})
            items = []
            for vid in params["id"].split(","):
                if vid == VID_A:
                    stats = {"viewCount": "1500", "likeCount": "30", "commentCount": "4"}
                elif vid == VID_B:  # 좋아요 숨김·댓글 막음
                    stats = {"viewCount": "0"}
                else:
                    continue
                items.append({"id": vid, "statistics": stats})
            return httpx.Response(200, json={"items": items})
        return httpx.Response(404)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path))
    for key in yt.KEYS:
        monkeypatch.setenv(key, f"DUMMY-{key}")
    monkeypatch.delenv("AX_SECRET_STORE", raising=False)
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    google = Google()
    monkeypatch.setattr(
        yt, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(google.handle))
    )
    ys.clear_cache()
    yield {"store": store, "google": google}
    ys.clear_cache()
    cr.set_store(None)


def _uploaded(store: cr.CreativeRecords, video_id: str, **extra: Any) -> str:
    rid = store.add_sheet({"summary_ko": "흰 슈트", "prompt_en": "p", "negative_prompt_en": "n"},
                          None, None)  # fmt: skip
    store.set_render(rid, {"youtube": {"status": "done", "video_id": video_id,
                                       "url": f"https://youtu.be/{video_id}",
                                       "uploaded_at": "2026-11-23T01:00:00+00:00",
                                       "title": "AI 직원의 출근 첫날", "privacy": "unlisted",
                                       **extra}})  # fmt: skip
    return rid


def _meta(requester: str = "data") -> RequestMeta:
    return RequestMeta(context_id="ctx_test_ys", token="t", requester=requester,
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip


def _call(skill: str, body: dict[str, Any]) -> Any:
    request = AgentRequest(meta=_meta(), skill=skill, input=body)
    return asyncio.run(handlers.HANDLERS[skill](request))


def test_stats_reads_views_likes_comments_with_the_company_token(env: dict[str, Any]) -> None:
    got = _call("youtube_video_stats", {"video_ids": [VID_A, VID_B, VID_GONE]})
    assert got.status == TaskStatus.COMPLETED and got.outcome == "ok"
    out = got.output
    items = {i["video_id"]: i for i in out["items"]}
    assert (items[VID_A]["views"], items[VID_A]["likes"], items[VID_A]["comments"]) == (1500, 30, 4)
    assert items[VID_A]["url"] == f"https://youtu.be/{VID_A}"
    # 0은 0, 숨긴 칸은 None(모름)
    b = items[VID_B]
    assert (b["views"], b["likes"], b["comments"]) == (0, None, None)
    assert items[VID_GONE]["status"] == "not_found" and items[VID_GONE]["views"] is None
    (call,) = env["google"].stats_calls
    assert call["part"] == "statistics" and call["id"] == f"{VID_A},{VID_B},{VID_GONE}"
    text = json.dumps(got.model_dump(mode="json"), ensure_ascii=False)
    assert "DUMMY" not in text and "ya29" not in text


def test_record_ids_are_resolved_and_missing_ones_listed(env: dict[str, Any]) -> None:
    rid = _uploaded(env["store"], VID_A)
    not_uploaded = env["store"].add_sheet({"summary_ko": "x", "prompt_en": "p",
                                           "negative_prompt_en": "n"}, None, None)  # fmt: skip
    out = _call("youtube_video_stats", {"record_ids": [rid, not_uploaded]}).output
    assert [(i["video_id"], i["record_id"]) for i in out["items"]] == [(VID_A, rid)]
    assert out["missing_records"] == [not_uploaded]


def test_stats_are_cached_for_a_short_while(env: dict[str, Any]) -> None:
    _call("youtube_video_stats", {"video_ids": [VID_A]})
    again = _call("youtube_video_stats", {"video_ids": [VID_A]}).output
    assert again["items"][0]["views"] == 1500
    assert len(env["google"].stats_calls) == 1  # 두 번째는 캐시
    _call("youtube_video_stats", {"video_ids": [VID_A, VID_B]})
    assert env["google"].stats_calls[-1]["id"] == VID_B  # 캐시에 없는 것만 읽는다


def test_unknown_with_a_reason_when_not_connected_or_youtube_fails(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env["google"].status = 403
    failed = _call("youtube_video_stats", {"video_ids": [VID_A]})
    assert failed.status == TaskStatus.COMPLETED
    assert (failed.outcome, failed.output["reason"], failed.output["items"]) == (
        "unknown", "stats_http_403", [])  # fmt: skip
    nothing = _call("youtube_video_stats", {"record_ids": ["CR-000000000000"]}).output
    assert (nothing["status"], nothing["reason"]) == ("unknown", "no_videos")
    monkeypatch.delenv(yt.KEYS[2])
    missing = _call("youtube_video_stats", {"video_ids": [VID_A]}).output
    assert (missing["status"], missing["reason"]) == ("unknown", "missing_keys")
    assert "DUMMY" not in json.dumps(missing)


def test_bad_input_is_rejected(env: dict[str, Any]) -> None:
    for body in ({}, {"video_ids": ["not a video id"]}, {"record_ids": ["CR-xyz"]}):
        got = _call("youtube_video_stats", body)
        assert (got.status, got.outcome) == (TaskStatus.FAILED, "invalid_input")
    assert env["google"].stats_calls == []


def test_uploads_list_gives_done_uploads_and_filters_by_campaign(env: dict[str, Any]) -> None:
    linked = _uploaded(env["store"], VID_A, campaign_id="wr_abc0000001")
    other = _uploaded(env["store"], VID_B)
    pending = env["store"].add_sheet({"summary_ko": "x", "prompt_en": "p",
                                      "negative_prompt_en": "n"}, None, None)  # fmt: skip
    env["store"].set_render(pending, {"youtube": {"status": "awaiting_approval"}})
    every = _call("youtube_uploads_list", {}).output
    assert every["total"] == 2
    assert {i["record_id"] for i in every["items"]} == {linked, other}
    item = next(i for i in every["items"] if i["record_id"] == linked)
    assert item == {"record_id": linked, "video_id": VID_A, "url": f"https://youtu.be/{VID_A}",
                    "uploaded_at": "2026-11-23T01:00:00+00:00", "title": "AI 직원의 출근 첫날",
                    "privacy": "unlisted", "campaign_id": "wr_abc0000001"}  # fmt: skip
    only = _call("youtube_uploads_list", {"campaign_id": "wr_abc0000001"}).output
    assert [i["record_id"] for i in only["items"]] == [linked]


@pytest.mark.parametrize("card", CARDS, ids=["marketing", "coupon"])
def test_cards_list_both_skills_as_read_for_data_and_dashboard(card: Path) -> None:
    skills = {s["id"]: s for s in json.loads(card.read_text("utf-8"))["skills"]}
    for skill in ("youtube_video_stats", "youtube_uploads_list"):
        assert skills[skill]["grade"] == "read"
        assert skills[skill]["allowed_requesters"] == ["data", "dashboard-api"]
        assert skill in handlers.HANDLERS
