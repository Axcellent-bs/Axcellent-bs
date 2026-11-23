"""제작 기록 영상 → AI 유튜브 초안 → 사람 승인(C) → 올리기. 가짜 구글 API·가짜 관문·가짜 LLM."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas import ExecuteDecision, HoldReason, RequestMeta
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import youtube as yt

META = RequestMeta(
    context_id="ctx_test_yt",
    token="t",
    idempotency_key="k0",
    deadline=datetime.now(UTC) + timedelta(seconds=60),
)
SHEET = {"summary_ko": "흰 슈트 안드로이드", "prompt_en": "sheet", "negative_prompt_en": "x"}
BOARD = {"title_ko": "AXcellent 출근 첫날", "sheet_prompt_en": "board", "shots": []}
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(64)


class Google:
    def __init__(self) -> None:
        self.uploaded: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == yt.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "ya29.DUMMY"})
        if url.startswith(yt.CHANNEL_URL):
            return httpx.Response(200, json={"items": [{"snippet": {"title": "AXcellent"}}]})
        if url.startswith(yt.UPLOAD_URL):
            assert request.headers["Authorization"] == "Bearer ya29.DUMMY"
            self.uploaded.append(__import__("json").loads(request.content))
            return httpx.Response(200, headers={"location": "https://upload.example/session1"})
        if url == "https://upload.example/session1":
            assert request.content == VIDEO
            return httpx.Response(200, json={"id": "vid123"})
        return httpx.Response(404)


class Gate:
    def __init__(self, status: str = "held") -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def __call__(self, action, params, meta, evidence):  # noqa: ANN001, ANN204
        self.calls.append((action, params, str(meta.idempotency_key)))
        extra: dict[str, Any] = {}
        if self.status == "held":
            extra = {"hold_reason": list(HoldReason)[0], "review_id": "rev_yt"}
        return ExecuteDecision(execution_id="ex_yt", grade="C", status=self.status, **extra)


class FakeLlm:
    async def structured(self, prompt, model, untrusted, context_id):  # noqa: ANN001, ANN201
        assert untrusted["storyboard_title"] == "AXcellent 출근 첫날"
        return model(title="AI 직원의 출근 첫날", description="AXcellent AI 직원 소개", tags=["AI"])


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path))
    for key in yt.KEYS:
        monkeypatch.setenv(key, f"DUMMY-{key}")
    monkeypatch.delenv("AX_SECRET_STORE", raising=False)
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    google = Google()
    gate = Gate()
    monkeypatch.setattr(
        yt, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(google.handle))
    )
    monkeypatch.setattr(yt, "execute", gate)
    yt._status_cache.update(at=0.0, value=None)  # noqa: SLF001
    rid = store.add_sheet(SHEET, None, None)
    store.attach_storyboard(rid, "AI 직원이 출근한다", 6, BOARD)
    yield {"store": store, "google": google, "gate": gate, "rid": rid, "dir": tmp_path}
    cr.set_store(None)


def _with_video(env: dict[str, Any], part: str = "final_video") -> None:
    rid = env["rid"]
    path = (
        rr.asset_path(rid, "video")
        if part == "video"
        else env["dir"] / "creative" / rid / "final.mp4"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(VIDEO)
    render = env["store"].get_render(rid)
    render[part] = {
        "status": "done",
        "asset": path.name,
        "sha256": hashlib.sha256(VIDEO).hexdigest(),
    }
    env["store"].set_render(rid, render)


def _run(coro_fn):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await coro_fn()
        await yt.wait_idle()
        return out

    return asyncio.run(go())


def test_ai_draft_then_human_edit_keeps_the_ai_notice(env: dict[str, Any]) -> None:
    _with_video(env)
    draft = _run(lambda: yt.write_draft(env["rid"], FakeLlm, "ctx"))
    assert (draft["status"], draft["source"], draft["privacy"]) == ("draft", "ai", "unlisted")
    assert draft["title"] == "AI 직원의 출근 첫날" and yt.AI_NOTICE in draft["description"]
    assert draft["part"] == "final_video"
    edited = _run(
        lambda: yt.write_draft(
            env["rid"], FakeLlm, "ctx", edits={"title": "새 제목", "privacy": "public"}
        )
    )
    assert (edited["title"], edited["source"], edited["privacy"]) == (
        "새 제목",
        "human_edit",
        "public",
    )
    assert yt.AI_NOTICE in edited["description"]


def test_without_llm_a_template_draft_is_written(env: dict[str, Any]) -> None:
    draft = _run(lambda: yt.write_draft(env["rid"], lambda: None, "ctx"))
    assert draft["source"] == "template" and "AXcellent" in draft["title"]
    assert draft["part"] is None  # 아직 영상이 없다


def test_upload_needs_video_and_connection(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(lambda: yt.write_draft(env["rid"], lambda: None, "ctx"))
    with pytest.raises(yt.YoutubeError, match="video_not_ready"):
        _run(lambda: yt.request_upload(META, env["rid"], "태윤"))
    _with_video(env)
    _run(lambda: yt.write_draft(env["rid"], lambda: None, "ctx", edits={"part": "final_video"}))
    monkeypatch.delenv(yt.KEYS[2])
    with pytest.raises(yt.YoutubeError, match="youtube_not_connected"):
        _run(lambda: yt.request_upload(META, env["rid"], "태윤"))
    assert env["gate"].calls == []


def test_held_then_approved_uploads_with_synthetic_media(env: dict[str, Any]) -> None:
    _with_video(env)
    _run(lambda: yt.write_draft(env["rid"], FakeLlm, "ctx"))
    held = _run(lambda: yt.request_upload(META, env["rid"], "태윤"))
    assert (held["status"], held["review_id"]) == ("awaiting_approval", "rev_yt")
    assert env["gate"].calls[0][0] == "publish_youtube" and env["google"].uploaded == []
    key = env["gate"].calls[0][2]

    env["gate"].status = "executed"
    _run(lambda: yt.check_upload(META, env["rid"]))
    assert env["gate"].calls[-1][2] == key  # 같은 키로 다시 묻는다
    done = env["store"].get_render(env["rid"])["youtube"]
    assert (done["status"], done["video_id"], done["url"]) == (
        "done",
        "vid123",
        "https://youtu.be/vid123",
    )
    body = env["google"].uploaded[0]
    assert body["status"]["containsSyntheticMedia"] is True
    assert (
        body["status"]["privacyStatus"] == "unlisted"
        and body["snippet"]["title"] == "AI 직원의 출근 첫날"
    )
    with pytest.raises(yt.YoutubeError, match="already_requested"):
        _run(lambda: yt.write_draft(env["rid"], FakeLlm, "ctx", regenerate=True))


def test_rejected_review_is_recorded(env: dict[str, Any]) -> None:
    _with_video(env)
    _run(lambda: yt.write_draft(env["rid"], FakeLlm, "ctx"))
    _run(lambda: yt.request_upload(META, env["rid"], "태윤"))
    env["gate"].status = "rejected"
    _run(lambda: yt.check_upload(META, env["rid"]))
    cur = env["store"].get_render(env["rid"])["youtube"]
    assert (cur["status"], cur["error"]) == ("rejected", "gate_rejected")
    assert env["google"].uploaded == []


def test_status_reports_missing_keys_and_channel(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = asyncio.run(yt.status(force=True))
    assert (ok["connection"], ok["channel"]) == ("ok", "AXcellent")
    monkeypatch.delenv(yt.KEYS[0])
    missing = asyncio.run(yt.status(force=True))
    assert missing["connection"] == "missing_keys" and "DUMMY" not in str(missing)


# ---------------------------------------------------------------- 설명 끝 해시태그
def _tags_in(body: str) -> list[str]:
    return [t.casefold() for t in yt.HASHTAG_RE.findall(body)]


def test_description_gets_three_to_five_hashtags_from_the_tags() -> None:
    tags = ["AI직원", "AI 직원", "업무자동화", "axcellent", "AI", "Shorts", "추가"]
    _title, body, _tags = yt._clean("제목", "AXcellent AI 직원 소개", tags)  # noqa: SLF001
    assert yt.AI_NOTICE in body  # AI 표시는 그대로
    assert body.endswith(f"{yt.AI_NOTICE}\n#AI직원 #업무자동화 #AI #Shorts")
    found = _tags_in(body)
    assert len(found) == 5 and len(set(found)) == 5  # #AXcellent 포함, 중복 없음
    assert "axcellent" in found and "ai 직원" not in found  # 공백 있는 태그는 빼고


def test_hashtags_are_not_added_again_on_a_second_clean_or_human_edit() -> None:
    _t, body, tags = yt._clean("제목", "본문", ["AI직원", "업무자동화"])  # noqa: SLF001
    _t, again, _ = yt._clean("제목", body, tags)  # noqa: SLF001
    assert again == body
    edited = "사람이 고친 설명 #AI직원"  # 사람이 AI 표시를 지우고 해시태그 하나를 넣었다
    _t, fixed, _ = yt._clean("제목", edited, tags)  # noqa: SLF001
    assert yt.AI_NOTICE in fixed  # AI 표시는 다시 붙는다
    found = _tags_in(fixed)
    assert found.count("ai직원") == 1 and found.count("axcellent") == 1
    assert 3 <= len(found) <= 5


def test_few_tags_still_give_at_least_three_hashtags() -> None:
    _t, body, _ = yt._clean("제목", "본문", ["공백 있는 태그", "#!?"])  # noqa: SLF001
    assert _tags_in(body) == ["axcellent", "ai직원", "ai"]


def test_long_description_keeps_the_notice_and_hashtags() -> None:
    _t, body, _ = yt._clean("제목", "가" * 6000, ["업무자동화"])  # noqa: SLF001
    assert len(body) <= 5000 and yt.AI_NOTICE in body and "#업무자동화" in body


def test_draft_description_carries_hashtags(env: dict[str, Any]) -> None:
    draft = _run(lambda: yt.write_draft(env["rid"], lambda: None, "ctx"))
    found = _tags_in(draft["description"])
    assert 3 <= len(found) <= 5 and len(set(found)) == len(found)
    assert yt.AI_NOTICE in draft["description"]


@pytest.mark.parametrize(
    ("item", "url"),
    [
        ({"id": "UCabcdefghijklmnop12", "snippet": {"title": "A", "customUrl": "@axcellent"}},
         "https://www.youtube.com/@axcellent"),
        ({"id": "UCabcdefghijklmnop12", "snippet": {"title": "A", "customUrl": "axcellent"}},
         "https://www.youtube.com/@axcellent"),
        ({"id": "UCabcdefghijklmnop12", "snippet": {"title": "A"}},
         "https://www.youtube.com/channel/UCabcdefghijklmnop12"),
        ({"snippet": {"title": "A"}}, None),
    ],
)  # fmt: skip
def test_status_gives_channel_profile_url(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, item: dict[str, Any], url: str | None
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if str(request.url) == yt.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "ya29.DUMMY"})
        return httpx.Response(200, json={"items": [item]})

    monkeypatch.setattr(
        yt, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    out = asyncio.run(yt.status(force=True))
    assert (out["connection"], out["channel_url"]) == ("ok", url)


def test_status_cache_is_per_company(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from ax_agent_template import company_ai

    monkeypatch.setattr(company_ai, "company_id", lambda: "catbooks")
    assert asyncio.run(yt.status())["connection"] == "ok"
    monkeypatch.delenv(yt.KEYS[2])
    monkeypatch.setattr(company_ai, "company_id", lambda: "nubi")
    assert (
        asyncio.run(yt.status())["connection"] == "missing_keys"
    )  # 캣북스 결과를 빌려 쓰지 않는다
