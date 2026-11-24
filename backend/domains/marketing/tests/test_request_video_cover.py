"""의뢰 영상 썸네일 후보 3장 → 사람이 고르기 → 게시·유튜브 승인 해시 (request_video_cover).

ffmpeg는 부르지 않는다(가짜 프레임 뽑기·가짜 signalstats). 승인 관문·Instagram·구글 API도 가짜다.
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

from ax_contracts.schemas import AgentRequest, ExecuteDecision, HoldReason, RequestMeta, TaskStatus
from marketing_agent import creative_records as cr
from marketing_agent import handlers, sns, sns_media
from marketing_agent import request_video as rv
from marketing_agent import request_video_cover as cover
from marketing_agent import request_video_youtube as rvy
from marketing_agent import youtube as yt
from marketing_agent.sns_publish import instagram, pending, publisher
from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter

CTX = "ctx_wr_cover1"
DRAFT_ID = "spd_cov"
PLAN = "AI 직원이 첫 출근을 한다"
LABEL_TEXT = "이 영상은 AI로 만들었습니다"
BRIGHT = {"YLOW": 30.0, "YHIGH": 200.0, "ULOW": 90.0, "UHIGH": 160.0, "VLOW": 90.0, "VHIGH": 170.0}
BLACK = {"YLOW": 16.0, "YHIGH": 18.0, "ULOW": 128.0, "UHIGH": 128.0, "VLOW": 128.0, "VHIGH": 128.0}
FLAT = {"YLOW": 120.0, "YHIGH": 125.0, "ULOW": 100.0, "UHIGH": 104.0, "VLOW": 150.0, "VHIGH": 153.0}
META = RequestMeta(
    context_id="ctx_test_cover",
    token="t",
    idempotency_key="k0",
    deadline=datetime.now(UTC) + timedelta(seconds=60),
)


# ---------------------------------------------------------------- 시점·빈 프레임
def test_times_follow_scene_starts_plus_half_a_second() -> None:
    # 장면 4개(0·3·6·9초 시작, 12초): 장면 1 · 가운데(3번째) · 마지막에서 0.5초 뒤
    assert cover.pick_times(12.0, [0, 3, 6, 9], 12.0) == [0.5, 6.5, 9.5]
    # 실제 길이가 기획과 다르면 비율로 맞춘다(12초 기획 → 10초 영상)
    assert cover.pick_times(10.0, [0, 3, 6, 9], 12.0) == [0.5, 5.5, 8.0]


def test_times_without_scenes_are_20_50_80_percent() -> None:
    assert cover.pick_times(10.0) == [2.0, 5.0, 8.0]
    assert cover.pick_times(10.0, [], None) == [2.0, 5.0, 8.0]
    # 장면이 하나뿐이면 겹치지 않는 비율 시점으로 채운다
    assert cover.pick_times(10.0, [0], 10.0) == [0.5, 2.0, 5.0]
    assert cover.pick_times(None) == [] and cover.pick_times(0) == []


def test_black_or_single_colour_frames_are_skipped() -> None:
    assert cover.is_blank(BLACK) and cover.is_blank(FLAT)
    assert not cover.is_blank(BRIGHT) and not cover.is_blank(None)
    # 0.5초씩 최대 3번 뒤로, 영상 끝을 넘지 않는다
    assert cover.frame_times(2.0, 10.0) == [2.0, 2.5, 3.0, 3.5]
    assert cover.frame_times(9.5, 10.0) == [9.5]


# ---------------------------------------------------------------- 준비
def _jpeg(tag: str) -> bytes:
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    return b"\xff\xd8" + app0 + tag.encode() + b"\xff\xd9"


class Frames:
    """가짜 ffmpeg: 시각마다 다른 그림을 쓰고, 정한 시각은 검은 화면으로 답한다."""

    def __init__(self, black: set[float] | None = None) -> None:
        self.black = black or set()
        self.grabbed: list[float] = []

    def grab(self, video: Path, at: float, out: Path) -> None:
        self.grabbed.append(at)
        out.write_bytes(_jpeg(f"frame@{at}"))

    def stats(self, video: Path, at: float) -> dict[str, float]:
        return BLACK if at in self.black else BRIGHT


def _draft(ctx: str = CTX, starts: list[float] | None = None) -> sns.SnsPostDraft:
    caption = sns._compose(PLAN, "reel")  # noqa: SLF001
    return sns.SnsPostDraft(
        post_draft_id=DRAFT_ID,
        source_kind="brief",
        source_ref="prop_1",
        platform="instagram",
        media_kind="reel",
        caption=caption,
        hashtags=[],
        caption_sha256=sns._sha(caption),  # noqa: SLF001
        required_text=[PLAN, *sns.notice_texts()],
        context_id=ctx,
        scene_starts_s=starts or [],
        plan_duration_s=10.0 if starts else None,
    )


def _labeled(env: dict[str, Any], *, label: bool = True) -> Path:
    folder = env["media"] / "catbooks" / CTX / "med_1"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "labeled.mp4"
    video.write_bytes(b"labeled-video")
    sha = sns_media.sha256_file(video)
    meta: dict[str, Any] = {"media_id": "med_1", "media_kind": "video",
                            "post_draft_id": DRAFT_ID, "duration_s": 10}  # fmt: skip
    if label:
        meta["ai_label"] = {
            "label_overlay": True,
            "labeled_sha256": sha,
            "labeled_file": "labeled.mp4",
            "ai_generator_model": "kling-3.0-turbo",
            "ai_generated_at": "2026-11-23T01:00:00Z",
            "text": LABEL_TEXT,
        }
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    draft = env["sns"].get(DRAFT_ID)
    ref = sns_media.asset_ref_for(video)
    env["sns"].update_meta([draft.model_copy(update={"asset_ref": ref, "asset_sha256": sha})])
    return video


def _make(env: dict[str, Any], frames: Frames | None = None) -> dict[str, Any]:
    f = frames or Frames()
    return cover.make_covers(
        env["sns"], DRAFT_ID, grab=f.grab, stats=f.stats, duration=lambda _p: 10.0
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in ("MARKETING_SNS_PROVIDER", "MARKETING_SNS_PENDING_DB", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    for key in yt.KEYS:
        monkeypatch.setenv(key, f"DUMMY-{key}")
    monkeypatch.delenv("AX_SECRET_STORE", raising=False)
    store = cr.CreativeRecords(str(tmp_path / "creative.db"))
    cr.set_store(store)
    sns_store = sns.SnsStore(str(tmp_path / "sns.db"))
    sns_store.save(_draft())
    monkeypatch.setattr(handlers.STATE, "sns", sns_store)  # 게시 직전 확인이 읽는 초안 저장소
    publisher.reset_runtime()
    instagram.reset_adapter()

    async def no_sleep(_: float) -> None:
        return None

    async def no_hook(_row: Any) -> None:
        return None

    publisher.RT.sleep = no_sleep
    publisher.RT.on_published = no_hook
    yield {"sns": sns_store, "media": media.resolve(), "dir": tmp_path}
    publisher.reset_runtime()
    instagram.reset_adapter()
    cr.set_store(None)


# ---------------------------------------------------------------- 만들기
def test_no_candidates_for_an_unlabeled_video(env: dict[str, Any]) -> None:
    _labeled(env, label=False)
    frames = Frames()
    assert _make(env, frames) == {"covers_skipped": "label_missing"}
    assert frames.grabbed == [] and env["sns"].get(DRAFT_ID).cover_candidates == []
    # 영상이 아예 연결되지 않은 초안도 같다
    env["sns"].update_meta([_draft()])
    assert _make(env)["covers_skipped"] == "label_missing"


def test_three_candidates_carry_the_ai_mark_in_the_company_folder(env: dict[str, Any]) -> None:
    video = _labeled(env)
    frames = Frames(black={2.0, 2.5})  # 20% 지점은 검은 화면 두 번 → 3.0초
    assert _make(env, frames) == {"covers": 3}
    draft = env["sns"].get(DRAFT_ID)
    assert [c["at_s"] for c in draft.cover_candidates] == [3.0, 5.0, 8.0]
    for c in draft.cover_candidates:
        path = env["media"] / c["asset_ref"]
        # 회사 폴더(<미디어>/catbooks/<의뢰>/med_*/covers/) 안
        assert path.parent == video.parent / "covers" and path.name == f"{c['media_id']}.jpg"
        assert c["is_ai_generated"] is True and c["ai_label_text"] == LABEL_TEXT
        xmp = sns_media.jpeg_xmp(path) or ""
        assert 'catbooks:ai_generated="true"' in xmp and "kling-3.0-turbo" in xmp
        assert LABEL_TEXT in xmp
    record = json.loads((video.parent / "covers" / "meta.json").read_text(encoding="utf-8"))
    assert all(r["label_overlay"] and r["ai_generated"] for r in record["ai_label"].values())
    # 기본값은 첫 후보, 목록에는 media_id·at_s만
    item = rv._covers_of(draft, True)  # noqa: SLF001
    assert item["covers"] == [{"media_id": c["media_id"], "at_s": c["at_s"]}
                              for c in draft.cover_candidates]  # fmt: skip
    assert item["cover_media_id"] == draft.cover_candidates[0]["media_id"]
    assert item["cover_picked"] is False
    # 다시 불러도 새로 뽑지 않는다
    again = Frames()
    assert _make(env, again)["covers_reused"] is True and again.grabbed == []


def test_scene_times_are_used_when_the_plan_has_scenes(env: dict[str, Any]) -> None:
    env["sns"].update_meta([_draft(starts=[0, 2.5, 5, 7.5])])
    _labeled(env)
    frames = Frames()
    _make(env, frames)
    assert frames.grabbed == [0.5, 5.5, 8.0]


def test_all_black_frames_fall_back_to_the_last_try(env: dict[str, Any]) -> None:
    _labeled(env)
    frames = Frames(black={2.0, 2.5, 3.0, 3.5})
    _make(env, frames)
    assert frames.grabbed[0] == 3.5  # 3번 넘긴 뒤에는 그 시각을 쓴다


def test_attach_label_makes_covers_only_when_draft_ready(env: dict[str, Any]) -> None:
    calls: list[str] = []

    def maker(store: sns.SnsStore, draft_id: str) -> dict[str, Any]:
        calls.append(draft_id)
        return {"covers": 3}

    async def fake_attach(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"label": "ok", "draft_stage": stage}

    from ax_contracts.schemas import AgentResult

    request = AgentRequest(
        meta=META, skill="generate_media", input={"post_draft_id": DRAFT_ID, "media_kind": "video"}
    )
    result = AgentResult(meta=META, status=TaskStatus.COMPLETED, outcome="generated", output={})
    original = rv._attach  # noqa: SLF001
    try:
        rv._attach = fake_attach  # type: ignore[assignment]  # noqa: SLF001
        stage = "check_rejected"
        out = asyncio.run(rv.attach_label_after_generate(request, result, store=env["sns"],
                                                         covers=maker))  # fmt: skip
        assert calls == [] and "covers" not in out.output
        stage = "draft_ready"
        out = asyncio.run(rv.attach_label_after_generate(request, result, store=env["sns"],
                                                         covers=maker))  # fmt: skip
        assert calls == [DRAFT_ID] and out.output["covers"] == 3
    finally:
        rv._attach = original  # type: ignore[assignment]  # noqa: SLF001


# ---------------------------------------------------------------- 고르기
def _ids(env: dict[str, Any]) -> list[str]:
    return [c["media_id"] for c in env["sns"].get(DRAFT_ID).cover_candidates]


def test_pick_only_from_candidates_and_other_company_cannot(env: dict[str, Any]) -> None:
    _labeled(env)
    _make(env)
    ids = _ids(env)
    with pytest.raises(cover.CoverError, match="cover_not_candidate"):
        cover.select_cover(env["sns"], DRAFT_ID, "cov_ffffffffffff")
    other = sns.SnsStore(str(env["dir"] / "sns.db"), company_id="axcellent")
    with pytest.raises(cover.CoverError, match="not_found"):
        cover.select_cover(other, DRAFT_ID, ids[1])
    picked = cover.select_cover(env["sns"], DRAFT_ID, ids[1])
    assert (picked["changed"], picked["cover_rev"], picked["approvals_reset"]) == (True, 1, [])
    assert env["sns"].get(DRAFT_ID).cover_media_id == ids[1]
    same = cover.select_cover(env["sns"], DRAFT_ID, ids[1])
    assert same["changed"] is False and same["cover_rev"] == 1


def test_skill_maps_refusals(env: dict[str, Any]) -> None:
    _labeled(env)
    _make(env)
    body = {"post_draft_id": DRAFT_ID, "media_id": "cov_ffffffffffff"}
    bad = AgentRequest(meta=META, skill="request_video_cover_select", input=body)
    got = asyncio.run(handlers.request_video_cover_select(bad))
    assert (got.status, got.outcome) == (TaskStatus.FAILED, "cover_not_candidate")
    ok = bad.model_copy(update={"input": {"post_draft_id": DRAFT_ID, "media_id": _ids(env)[2]}})
    done = asyncio.run(handlers.request_video_cover_select(ok))
    assert done.status == TaskStatus.COMPLETED and done.output["cover_media_id"] == _ids(env)[2]


# ---------------------------------------------------------------- 옛 영상 채우기
def test_backfill_reports_missing_ffmpeg_and_retries_later(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cover, "_tried", {})
    _labeled(env)
    calls: list[str] = []
    now = [100.0]

    def maker(store: sns.SnsStore, pid: str) -> dict[str, Any]:
        calls.append(pid)
        return _make(env)

    def run(missing: str | None) -> tuple[int, dict[str, str]]:
        return cover.backfill(
            env["sns"], [DRAFT_ID], maker=maker, preflight=lambda: missing, clock=lambda: now[0]
        )

    assert run("ffmpeg_missing") == (0, {DRAFT_ID: "ffmpeg_missing"})  # 조용히 넘기지 않는다
    assert calls == []
    now[0] += 60
    assert run(None) == (0, {DRAFT_ID: "ffmpeg_missing"})  # 10분 안에는 다시 시도하지 않는다
    now[0] += cover.RETRY_AFTER_S
    assert run(None) == (1, {})  # ffmpeg를 깔고 나면 재시작 없이 만든다
    assert calls == [DRAFT_ID] and len(env["sns"].get(DRAFT_ID).cover_candidates) == 3


def test_backfill_makes_at_most_two_per_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cover, "_tried", {})
    calls: list[str] = []

    def maker(store: Any, pid: str) -> dict[str, Any]:
        calls.append(pid)
        return {"covers_skipped": "frame_grab_failed"} if pid == "b" else {"covers": 3}

    made, errors = cover.backfill(
        None,
        ["a", "b", "c"],
        maker=maker,
        preflight=lambda: None,  # type: ignore[arg-type]
    )
    assert (made, errors, calls) == (1, {"b": "frame_grab_failed"}, ["a", "b"])


def test_listing_old_videos_backfills_covers_and_shows_the_reason(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cover, "_tried", {})
    _labeled(env)
    env["sns"].update_meta(
        [
            env["sns"]
            .get(DRAFT_ID)
            .model_copy(update={"media_check": {"verdict": "approved", "publishable": True}})
        ]  # fmt: skip
    )
    monkeypatch.setattr(cover, "tools_missing", lambda: "ffmpeg_missing")
    req = AgentRequest(meta=META, skill="list_request_videos", input={})
    got = asyncio.run(handlers.list_request_videos_skill(req))
    item = got.output["items"][0]
    assert (item["covers"], item["covers_error"]) == ([], "ffmpeg_missing")

    monkeypatch.setattr(cover, "_tried", {})
    monkeypatch.setattr(cover, "tools_missing", lambda: None)
    frames, real = Frames(), cover.make_covers
    monkeypatch.setattr(
        cover,
        "make_covers",
        lambda store, pid: real(
            store, pid, grab=frames.grab, stats=frames.stats, duration=lambda _p: 10.0
        ),  # fmt: skip
    )
    got = asyncio.run(handlers.list_request_videos_skill(req))
    item = got.output["items"][0]
    assert len(item["covers"]) == 3 and "covers_error" not in item, item


# ---------------------------------------------------------------- Instagram 승인
class Gate:
    def __init__(self) -> None:
        self.by_key: dict[str, ExecuteDecision] = {}
        self.params: list[dict[str, Any]] = []

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        key = meta.idempotency_key or ""
        self.params.append(params)
        if key not in self.by_key:
            n = len(self.by_key) + 1
            self.by_key[key] = ExecuteDecision(
                execution_id=f"exe_{n}", grade="C", status="held",
                hold_reason=HoldReason.OVER_THRESHOLD, review_id=f"rev_{n}",
            )  # fmt: skip
        return self.by_key[key]

    def approve_all(self) -> None:
        for key, d in self.by_key.items():
            self.by_key[key] = d.model_copy(update={"status": "executed"})


def _publish_request(env: dict[str, Any]) -> AgentRequest:
    draft = env["sns"].get(DRAFT_ID)
    body = {
        "post_draft_id": DRAFT_ID,
        "asset_ref": draft.asset_ref,
        "asset_sha256": draft.asset_sha256,
        "caption": draft.caption,
        "media_kind": "reel",
        "share_to_feed": True,
    }
    meta = META.model_copy(update={"context_id": CTX})
    return AgentRequest(meta=meta, skill="publish_sns", input=body)


@pytest.fixture
def ig(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> tuple[Gate, MockInstagramAdapter]:
    _labeled(env)
    _make(env)
    gate = Gate()
    monkeypatch.setattr(publisher, "execute", gate.execute)
    adapter = MockInstagramAdapter()
    instagram.set_adapter(adapter)
    return gate, adapter


def _row() -> pending.PendingPost:
    return publisher.store().with_draft(DRAFT_ID)[-1]  # 만든 순서


def test_default_cover_goes_into_the_approval_and_the_reel(
    env: dict[str, Any], ig: tuple[Gate, MockInstagramAdapter]
) -> None:
    gate, adapter = ig
    held = asyncio.run(publisher.publish_sns(_publish_request(env), env["sns"]))
    assert held.outcome == "held"
    first = _ids(env)[0]
    assert env["sns"].get(DRAFT_ID).cover_media_id == first  # 승인 요청 전에 첫 후보를 적는다
    assert gate.params[0]["cover_media_id"] == first
    gate.approve_all()
    asyncio.run(publisher.apply_decision(_row(), "approved", "event"))
    row = _row()
    assert row.status == pending.PUBLISHED
    sent = adapter.create_requests[-1]
    assert sent.cover_ref == env["sns"].get(DRAFT_ID).cover_candidates[0]["asset_ref"]
    assert row.result["cover"] == {"media_id": first, "applied": True, "reason": None}


def test_changing_cover_after_request_cancels_the_approval(
    env: dict[str, Any], ig: tuple[Gate, MockInstagramAdapter]
) -> None:
    gate, adapter = ig
    assert cover.approval_waiting(DRAFT_ID, None) is False
    asyncio.run(publisher.publish_sns(_publish_request(env), env["sns"]))
    assert cover.approval_waiting(DRAFT_ID, None) is True  # 화면은 바꾸기 전에 확인을 묻는다
    old_key = _row().idempotency_key
    picked = cover.select_cover(env["sns"], DRAFT_ID, _ids(env)[1])
    assert picked["approvals_reset"] == ["instagram"]
    closed = publisher.store().get(old_key)
    assert (closed.status, closed.failure_code) == (pending.FAILED, "cover_changed")
    # 옛 승인이 나중에 와도 올리지 않는다
    gate.approve_all()
    asyncio.run(publisher.apply_decision(closed, "approved", "event"))
    assert adapter.calls["media_publish"] == 0
    # 다시 요청하면 새 해시·새 승인
    again = asyncio.run(publisher.publish_sns(_publish_request(env), env["sns"]))
    assert again.outcome == "held" and _row().idempotency_key != old_key


def test_cover_changed_behind_the_approval_is_not_published(
    env: dict[str, Any], ig: tuple[Gate, MockInstagramAdapter]
) -> None:
    gate, adapter = ig
    asyncio.run(publisher.publish_sns(_publish_request(env), env["sns"]))
    # 승인 화면 밖에서 초안의 썸네일이 바뀌었다(고르기 경로를 거치지 않음)
    draft = env["sns"].get(DRAFT_ID)
    env["sns"].update_meta([draft.model_copy(update={"cover_media_id": _ids(env)[2]})])
    gate.approve_all()
    asyncio.run(publisher.apply_decision(_row(), "approved", "event"))
    row = _row()
    assert (row.status, row.failure_code) == (pending.FAILED, "content_changed")
    assert adapter.calls["media_publish"] == 0


def test_cover_file_swapped_just_before_publish_is_not_published(
    env: dict[str, Any], ig: tuple[Gate, MockInstagramAdapter]
) -> None:
    gate, adapter = ig
    asyncio.run(publisher.publish_sns(_publish_request(env), env["sns"]))
    row = _row()
    publisher.store().transition(row.idempotency_key, pending.HELD, pending.APPROVED)
    first = env["sns"].get(DRAFT_ID).cover_candidates[0]
    (env["media"] / first["asset_ref"]).write_bytes(_jpeg("swapped"))
    asyncio.run(publisher._run_publish(publisher.store().get(row.idempotency_key)))  # noqa: SLF001
    done = publisher.store().get(row.idempotency_key)
    assert (done.status, done.failure_code) == (pending.FAILED, "cover_changed")
    assert adapter.calls["create_container"] == 0


def test_published_video_cover_is_locked(
    env: dict[str, Any], ig: tuple[Gate, MockInstagramAdapter]
) -> None:
    gate, _ = ig
    asyncio.run(publisher.publish_sns(_publish_request(env), env["sns"]))
    gate.approve_all()
    asyncio.run(publisher.apply_decision(_row(), "approved", "event"))
    with pytest.raises(cover.CoverError, match="cover_locked"):
        cover.select_cover(env["sns"], DRAFT_ID, _ids(env)[1])


# ---------------------------------------------------------------- 유튜브 승인
class Google:
    def __init__(self, thumbnail_status: int = 200) -> None:
        self.thumbnail_status = thumbnail_status
        self.thumbnails: list[bytes] = []
        self.videos: list[bytes] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == yt.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "ya29.DUMMY"})
        if url.startswith(yt.THUMBNAIL_URL):
            self.thumbnails.append(request.content)
            return httpx.Response(self.thumbnail_status, json={})
        if url.startswith(yt.UPLOAD_URL):
            return httpx.Response(200, headers={"location": "https://upload.example/s1"})
        if url == "https://upload.example/s1":
            self.videos.append(request.content)
            return httpx.Response(200, json={"id": "vidCOV"})
        return httpx.Response(404)


class YtGate:
    def __init__(self) -> None:
        self.status = "held"
        self.params: list[dict[str, Any]] = []

    async def __call__(self, action, params, meta, evidence):  # noqa: ANN001, ANN204
        self.params.append(params)
        extra: dict[str, Any] = {}
        if self.status == "held":
            extra = {"hold_reason": list(HoldReason)[0], "review_id": "rev_yt"}
        return ExecuteDecision(execution_id="ex_yt", grade="C", status=self.status, **extra)


@pytest.fixture
def youtube(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> tuple[YtGate, Google]:
    _labeled(env)
    _make(env)
    gate, google = YtGate(), Google()
    monkeypatch.setattr(rvy, "execute", gate)
    monkeypatch.setattr(
        yt, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(google.handle))
    )
    return gate, google


def _yt_run(coro_fn):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await coro_fn()
        await yt.wait_idle()
        return out

    return asyncio.run(go())


def test_youtube_approval_includes_cover_and_sets_the_thumbnail(
    env: dict[str, Any], youtube: tuple[YtGate, Google]
) -> None:
    gate, google = youtube
    _yt_run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, lambda: None, "ctx"))
    held = _yt_run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    first = env["sns"].get(DRAFT_ID).cover_candidates[0]
    assert held["status"] == "awaiting_approval"
    assert gate.params[0]["cover_media_id"] == first["media_id"]
    gate.status = "executed"
    _yt_run(lambda: rvy.check_upload(META, env["sns"], DRAFT_ID))
    done = rvy.get(DRAFT_ID)
    assert done["status"] == "done" and google.videos == [b"labeled-video"]
    assert google.thumbnails == [(env["media"] / first["asset_ref"]).read_bytes()]
    assert done["thumbnail"] == {"media_id": first["media_id"], "applied": True, "reason": None}


def test_youtube_channel_without_custom_thumbnails_records_it(
    env: dict[str, Any], youtube: tuple[YtGate, Google]
) -> None:
    gate, google = youtube
    google.thumbnail_status = 403  # 채널 미인증
    _yt_run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, lambda: None, "ctx"))
    _yt_run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    gate.status = "executed"
    _yt_run(lambda: rvy.check_upload(META, env["sns"], DRAFT_ID))
    done = rvy.get(DRAFT_ID)
    assert done["status"] == "done"  # 영상은 올라가고 썸네일만 못 붙였다고 남긴다
    assert done["thumbnail"]["applied"] is False
    assert done["thumbnail"]["reason"] == "thumbnail_http_403"


def test_youtube_cover_change_while_waiting_returns_to_draft(
    env: dict[str, Any], youtube: tuple[YtGate, Google]
) -> None:
    gate, google = youtube
    _yt_run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, lambda: None, "ctx"))
    _yt_run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    picked = cover.select_cover(env["sns"], DRAFT_ID, _ids(env)[1])
    assert picked["approvals_reset"] == ["youtube"]
    cur = rvy.get(DRAFT_ID)
    assert (cur["status"], cur["error"], cur.get("key")) == ("draft", "cover_changed", None)
    gate.status = "executed"  # 옛 승인이 나도 올리지 않는다
    _yt_run(lambda: rvy.check_upload(META, env["sns"], DRAFT_ID))
    assert google.videos == [] and rvy.get(DRAFT_ID)["status"] == "draft"
    # 다시 요청하면 새 썸네일로 새 승인
    gate.status = "held"
    again = _yt_run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    assert again["status"] == "awaiting_approval"
    assert gate.params[-1]["cover_media_id"] == _ids(env)[1]


def test_youtube_cover_changed_behind_the_approval_is_not_uploaded(
    env: dict[str, Any], youtube: tuple[YtGate, Google]
) -> None:
    gate, google = youtube
    _yt_run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, lambda: None, "ctx"))
    _yt_run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    draft = env["sns"].get(DRAFT_ID)
    env["sns"].update_meta([draft.model_copy(update={"cover_media_id": _ids(env)[2]})])
    gate.status = "executed"
    _yt_run(lambda: rvy.check_upload(META, env["sns"], DRAFT_ID))
    cur = rvy.get(DRAFT_ID)
    assert (cur["status"], cur["error"]) == ("failed", "cover_changed")
    assert google.videos == []
