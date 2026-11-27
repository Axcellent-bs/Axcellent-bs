"""Higgsfield 렌더(시트 그림 → 스토리보드 그림 → 사람 승인 영상). 가짜 Higgsfield·관문."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas import ExecuteDecision, HoldReason, RequestMeta
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent.media.ledger import MediaLedger

META = RequestMeta(
    context_id="ctx_test_render",
    token="t",
    idempotency_key="k0",
    deadline=datetime.now(UTC) + timedelta(seconds=60),
)
SHEET = {"summary_ko": "소녀", "prompt_en": "Character sheet ...", "negative_prompt_en": "blurry"}
BOARD = {"title_ko": "들판", "sheet_prompt_en": "A storyboard sheet ...", "negative_prompt_en": "x"}
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(32)


class FakeHiggsfield:
    """업로드·생성·상태·내려받기를 흉내 낸다. `fail`이면 상태가 failed."""

    def __init__(self) -> None:
        self.fail = False
        self.submits: list[tuple[str, dict[str, Any]]] = []
        self.auth_on_put: list[str | None] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/files/generate-upload-url"):
            assert request.headers["Authorization"] == "Key kid:ksecret"
            return httpx.Response(
                200,
                json={
                    "public_url": "https://cdn.example/ref.jpg",
                    "upload_url": "https://upload.example/put",
                    "upload_headers": {"Content-Type": "image/jpeg"},
                },
            )
        if url == "https://upload.example/put":
            self.auth_on_put.append(request.headers.get("Authorization"))
            return httpx.Response(200)
        if "/requests/" in url and url.endswith("/status"):
            if self.fail:
                return httpx.Response(200, json={"status": "failed"})
            job = url.split("/requests/")[1].split("/")[0]
            if job.startswith("vid"):
                body = {"status": "completed", "video": {"url": "https://cdn.example/v.mp4"}}
            else:
                body = {"status": "completed", "images": [{"url": "https://cdn.example/i.jpg"}]}
            return httpx.Response(200, json=body)
        if url.startswith("https://cdn.example/"):
            return httpx.Response(200, content=b"video-bytes" if url.endswith(".mp4") else JPEG)
        if url.startswith(rr.API_HOST + "/"):
            model = url[len(rr.API_HOST) + 1 :]
            self.submits.append((model, json.loads(request.content)))
            prefix = "vid" if "video" in model else "img"
            return httpx.Response(200, json={"request_id": f"{prefix}-{len(self.submits):08d}"})
        return httpx.Response(404)


class Gate:
    """가짜 안전 관문. 액션별로 돌려줄 상태를 정한다."""

    def __init__(self) -> None:
        self.status = {"generate_media": "executed", "generate_creative_video": "held"}
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def __call__(
        self, action: str, params: dict, meta: RequestMeta, evidence: Any
    ) -> ExecuteDecision:
        self.calls.append((action, params, str(meta.idempotency_key)))
        status = self.status[action]
        extra: dict[str, Any] = {}
        if status == "held":
            extra = {"hold_reason": list(HoldReason)[0], "review_id": "rev_1"}
        return ExecuteDecision(
            execution_id=f"ex_{len(self.calls)}",
            grade="C" if action == "generate_creative_video" else "B",
            status=status,  # type: ignore[arg-type]
            **extra,
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_API_KEY", "kid:ksecret")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_IMAGE_MODEL", "google/nano-banana/edit")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_VIDEO_MODEL", "kling/video/image-to-video")
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("MARKETING_QUOTA_IMAGE_COUNT_MONTHLY", "10")
    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "60")
    monkeypatch.setenv("MARKETING_QUOTA_AUDIO_COUNT_MONTHLY", "5")
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    hf = FakeHiggsfield()
    gate = Gate()
    monkeypatch.setattr(
        rr, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(hf.handle))
    )
    monkeypatch.setattr(rr, "execute", gate)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(rr, "SLEEP", no_sleep)
    yield {
        "store": store,
        "hf": hf,
        "gate": gate,
        "ledger": MediaLedger(":memory:"),
        "dir": tmp_path,
    }
    cr.set_store(None)


def _run(coro_fn):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await coro_fn()
        await rr.wait_idle()
        return out

    return asyncio.run(go())


def _sheet_done(env: dict[str, Any]) -> str:
    rid = env["store"].add_sheet(SHEET, None, None)

    async def go() -> None:
        rr.start_sheet(env["ledger"], META, rid, SHEET, JPEG)

    _run(go)
    return rid


def _board_done(env: dict[str, Any]) -> str:
    rid = _sheet_done(env)
    env["store"].attach_storyboard(rid, "들판 이야기", 6, BOARD)

    async def go() -> None:
        rr.maybe_start_board(env["ledger"], META, rid)

    _run(go)
    return rid


def test_without_key_the_sheet_is_unavailable(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MARKETING_HIGGSFIELD_API_KEY")
    rid = _sheet_done(env)
    assert env["store"].get_render(rid)["sheet_image"]["status"] == "unavailable"
    assert env["hf"].submits == [] and env["gate"].calls == []


def test_sheet_image_goes_through_gate_and_is_saved(env: dict[str, Any]) -> None:
    rid = _sheet_done(env)
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "done" and part["asset"] == "sheet.jpg" and part["sha256"]
    assert (env["dir"] / "creative" / rid / "sheet.jpg").read_bytes() == JPEG
    action, params, _key = env["gate"].calls[0]
    assert action == "generate_media" and params["media_kind"] == "image"
    model, payload = env["hf"].submits[0]
    assert model == "google/nano-banana/edit"
    assert payload["prompt"] == SHEET["prompt_en"]
    assert payload["image_urls"] == ["https://cdn.example/ref.jpg"]
    assert env["hf"].auth_on_put == [None]  # 업로드 PUT에는 키를 보내지 않는다


def test_board_image_follows_when_a_storyboard_is_attached(env: dict[str, Any]) -> None:
    rid = _board_done(env)
    board = env["store"].get_render(rid)["board_image"]
    assert board["status"] == "done" and (env["dir"] / "creative" / rid / "board.jpg").exists()
    assert env["hf"].submits[-1][1]["prompt"] == BOARD["sheet_prompt_en"]


def test_blocked_gate_releases_and_marks_blocked(env: dict[str, Any]) -> None:
    env["gate"].status["generate_media"] = "rejected"
    rid = _sheet_done(env)
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "blocked" and part["error"] == "gate_rejected"
    assert env["hf"].submits == []


def test_higgsfield_failure_is_recorded(env: dict[str, Any]) -> None:
    env["hf"].fail = True
    rid = _sheet_done(env)
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "failed" and part["error"] == "job_failed"


def test_video_waits_for_human_approval_then_renders(env: dict[str, Any]) -> None:
    rid = _board_done(env)
    video = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon"))
    assert video["status"] == "awaiting_approval" and video["review_id"] == "rev_1"
    assert not any("video" in m for m, _ in env["hf"].submits)  # 승인 전에는 만들지 않는다

    _run(lambda: rr.check_video(env["ledger"], META, rid))  # 아직 보류
    assert env["store"].get_render(rid)["video"]["status"] == "awaiting_approval"

    env["gate"].status["generate_creative_video"] = "executed"  # 사람이 승인
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    done = env["store"].get_render(rid)["video"]
    assert done["status"] == "done" and done["asset"] == "video.mp4"
    assert (env["dir"] / "creative" / rid / "video.mp4").read_bytes() == b"video-bytes"
    keys = {k for a, _, k in env["gate"].calls if a == "generate_creative_video"}
    assert len(keys) == 1  # 같은 멱등 키로 다시 물었다


def test_rejected_video_is_marked(env: dict[str, Any]) -> None:
    rid = _board_done(env)
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    env["gate"].status["generate_creative_video"] = "rejected"
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    assert env["store"].get_render(rid)["video"]["status"] == "rejected"


def test_video_needs_the_storyboard_image_first(env: dict[str, Any]) -> None:
    rid = env["store"].add_sheet(SHEET, None, None)
    with pytest.raises(rr.RenderError) as err:
        _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    assert str(err.value) == "board_image_not_ready"


def test_video_without_model_is_refused(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _board_done(env)
    monkeypatch.delenv("MARKETING_HIGGSFIELD_VIDEO_MODEL")
    with pytest.raises(rr.RenderError) as err:
        _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    assert str(err.value) == "model_not_configured"


# ---------------------------------------------------------------- 사람이 올린 파일
def _put_file(env: dict[str, Any], rid: str, name: str, data: bytes) -> str:
    import hashlib

    folder = env["dir"] / "creative" / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_uploaded_final_video_is_attached_after_the_gate(env: dict[str, Any]) -> None:
    env["gate"].status["attach_creative_file"] = "executed"
    rid = env["store"].add_sheet(SHEET, None, None)
    sha = _put_file(env, rid, "final.mp4", b"\x00\x00\x00\x18ftypmp42" + bytes(20))

    item = _run(
        lambda: rr.attach_upload(META, rid, "final_video", "final.mp4", sha, 32, "2D.mp4", "태윤")
    )

    assert item["status"] == "done" and item["source"] == "upload" and item["sha256"] == sha
    assert env["store"].get(rid)["render"]["final_video"]["label"] == "2D.mp4"
    assert [c[0] for c in env["gate"].calls] == ["attach_creative_file"]


def test_uploaded_motion_video_is_attached_after_the_gate(env: dict[str, Any]) -> None:
    env["gate"].status["attach_creative_file"] = "executed"
    rid = env["store"].add_sheet(SHEET, None, None)
    sha = _put_file(env, rid, "motion.mp4", b"\x00\x00\x00\x18ftypmp42" + bytes(20))

    item = _run(
        lambda: rr.attach_upload(META, rid, "motion_video", "motion.mp4", sha, 32, "m.mp4", "재헌")
    )

    assert item["status"] == "done" and item["source"] == "upload" and item["sha256"] == sha
    assert "motion_video" in env["store"].get(rid)["render"]


def test_upload_refuses_a_changed_file_and_a_held_gate(env: dict[str, Any]) -> None:
    rid = env["store"].add_sheet(SHEET, None, None)
    sha = _put_file(env, rid, "sheet.png", b"\x89PNG\r\n\x1a\n" + bytes(8))
    with pytest.raises(rr.RenderError, match="file_missing"):
        _run(
            lambda: rr.attach_upload(
                META, rid, "sheet_image", "sheet.png", "0" * 64, 16, None, None
            )
        )
    with pytest.raises(rr.RenderError, match="bad_asset"):
        _run(lambda: rr.attach_upload(META, rid, "sheet_image", "final.mp4", sha, 16, None, None))
    env["gate"].status["attach_creative_file"] = "rejected"
    with pytest.raises(rr.RenderError, match="gate_rejected"):
        _run(lambda: rr.attach_upload(META, rid, "sheet_image", "sheet.png", sha, 16, None, None))
    assert "sheet_image" not in env["store"].get(rid)["render"]


def test_storyboard_video_prompt_is_the_same_sentence_the_screen_copies() -> None:
    """화면 [영상 프롬프트 복사](frontend/src/lib/higgsfieldWeb.test.ts)와 같은 문장이어야 한다."""
    base = (
        "Animate this storyboard as one continuous short film, panel by panel in order, "
        "keeping the same characters, outfits and art style."
    )
    assert rr.storyboard_video_prompt("flat pastel 2D") == f"{base} flat pastel 2D"
    assert rr.storyboard_video_prompt(None) == base
    assert rr.storyboard_video_prompt("") == base
