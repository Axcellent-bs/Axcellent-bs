"""제작 기록 영상 자막 얹기 흐름: 영상이 붙으면 자동 1회, final_video가 생기면 다시, 다시 얹기,
고르는 규칙(약속 B), 실패해도 흐름 계속, 회사 폴더 분리. 가짜 ffmpeg 실행기만 쓴다."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import creative_overlay as ov
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import creative_thumbnails as ct
from marketing_agent import handlers
from marketing_agent import youtube as yt
from marketing_agent.media import caption_overlay as co
from marketing_agent.media.audio_mix import RunOutput

META = RequestMeta(
    context_id="ctx_test_overlay",
    token="t",
    idempotency_key="k0",
    deadline=datetime.now(UTC) + timedelta(seconds=60),
)
SHEET = {"summary_ko": "소녀", "prompt_en": "sheet", "negative_prompt_en": "x"}
SHOTS = [
    {"no": 1, "scene_ko": "a", "caption_ko": "첫 자막", "start_s": 0, "end_s": 2},
    {"no": 2, "scene_ko": "b", "caption_ko": "", "start_s": 2, "end_s": 4},
    {"no": 3, "scene_ko": "c", "caption_ko": "셋째 자막", "start_s": 4, "end_s": 6},
]
BOARD = {"title_ko": "들판", "sheet_prompt_en": "board", "cta_ko": "지금 확인", "shots": SHOTS}


class FakeFfmpeg:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail = False

    def __call__(self, args, timeout_s):  # noqa: ANN001, ANN204
        self.calls.append(list(args))
        if self.fail:
            return RunOutput(1, "boom")
        Path(args[-1]).write_bytes(b"overlay-" + str(len(self.calls)).encode())
        return RunOutput(0)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    font = tmp_path / "font.ttf"
    font.write_bytes(b"f")
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv(co.FONT_ENV, str(font))
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    ff = FakeFfmpeg()
    monkeypatch.setattr(ov, "RUNNER", ff)
    monkeypatch.setattr(ov, "PROBE", lambda path: 8.0)  # 마지막 2초(6~8초)가 엔드카드
    yield {"store": store, "ff": ff, "media": tmp_path / "media"}
    cr.set_store(None)


def _record(env: dict[str, Any], parts: tuple[str, ...] = ("video",)) -> str:
    store = env["store"]
    rid = store.add_sheet(SHEET, None, None)
    store.attach_storyboard(rid, "story", len(SHOTS), BOARD)
    folder = env["media"] / "creative" / rid
    folder.mkdir(parents=True)
    for part in parts:
        _put(env, rid, part)
    return rid


def _put(env: dict[str, Any], rid: str, part: str, data: bytes | None = None) -> None:
    name = {"video": "video.mp4", "final_video": "final.mp4"}[part]
    body = data or part.encode()
    (env["media"] / "creative" / rid / name).write_bytes(body)
    import hashlib

    render = env["store"].get_render(rid)
    render[part] = {
        "status": "done",
        "asset": name,
        "sha256": hashlib.sha256(body).hexdigest(),
        "seconds": 6,
    }
    env["store"].set_render(rid, render)


def _run(coro_fn):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await coro_fn()
        await rr.wait_idle()
        return out

    return asyncio.run(go())


def _render(env: dict[str, Any], rid: str) -> dict[str, Any]:
    return env["store"].get_render(rid)


def test_overlay_is_applied_once_to_the_latest_video(env: dict[str, Any]) -> None:
    rid = _record(env)
    state = _run(lambda: ov.apply(rid))
    assert state["status"] == "applied" and state["source_part"] == "video"
    assert state["captions"] == 2 and state["error"] is None  # 빈 자막 칸은 건너뜀
    args = env["ff"].calls[0]
    filters = args[args.index("-vf") + 1]
    assert filters.count("drawtext=") == 3  # 자막 2 + 엔드카드
    assert "enable='between(t,0,2)'" in filters and "enable='between(t,4,6)'" in filters
    assert "enable='between(t,6,8)'" in filters  # 엔드카드
    done = _render(env, rid)["overlay_video"]
    assert done["status"] == "done" and done["asset"] == "overlay.mp4"
    assert (env["media"] / "creative" / rid / "overlay.mp4").is_file()
    _run(lambda: ov.apply(rid))  # 같은 영상에는 다시 얹지 않는다
    assert len(env["ff"].calls) == 1


def test_final_video_attached_later_gets_its_own_overlay(env: dict[str, Any]) -> None:
    rid = _record(env)
    _run(lambda: ov.apply(rid))
    _put(env, rid, "final_video")
    state = _run(lambda: ov.apply(rid))
    assert state["source_part"] == "final_video" and state["status"] == "applied"
    assert len(env["ff"].calls) == 2
    assert env["ff"].calls[1][env["ff"].calls[1].index("-i") + 1].endswith("final.mp4")


def test_attach_upload_triggers_the_overlay_without_waiting(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _record(env)

    async def allow(action, params, meta, evidence):  # noqa: ANN001, ANN202
        from ax_contracts.schemas import ExecuteDecision

        return ExecuteDecision(execution_id="ex_1", grade="A", status="executed")

    monkeypatch.setattr(rr, "execute", allow)
    body = b"uploaded-final"
    import hashlib

    (env["media"] / "creative" / rid / "final.mp4").write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    _run(
        lambda: rr.attach_upload(
            META, rid, "final_video", "final.mp4", sha, len(body), None, None
        )
    )
    render = _render(env, rid)
    assert render["overlay"]["status"] == "applied"
    assert render["overlay"]["source_part"] == "final_video"


def test_no_font_means_skipped_and_the_flow_goes_on(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _record(env)
    monkeypatch.delenv(co.FONT_ENV)
    state = _run(lambda: ov.apply(rid))
    assert (state["status"], state["error"]) == ("skipped", "font_not_found")
    assert env["ff"].calls == []
    rec = env["store"].get(rid)
    assert rr.publish_video_part(rec) == "video"  # 원본이 그대로 나간다


def test_no_ffmpeg_means_skipped(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from marketing_agent.media.audio_mix import AudioMixError

    def missing(args, timeout_s):  # noqa: ANN001, ANN202
        raise AudioMixError("ffmpeg_not_found")

    monkeypatch.setattr(ov, "RUNNER", missing)
    rid = _record(env)
    state = _run(lambda: ov.apply(rid))
    assert (state["status"], state["error"]) == ("skipped", "ffmpeg_not_found")


def test_a_failed_overlay_does_not_stop_the_video_flow(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _record(env)
    env["ff"].fail = True
    state = _run(lambda: ov.apply(rid))
    assert state["status"] == "failed" and state["error"] == "ffmpeg_failed"
    assert _render(env, rid)["video"]["status"] == "done"  # 영상은 그대로
    assert rr.publish_video_part(env["store"].get(rid)) == "video"

    # 영상 만들기 끝에서 부르는 길은 예외를 삼킨다
    async def raising(*a: Any, **k: Any) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(ov, "apply", raising)
    _run(lambda: rr._overlay_after_attach(rid, None))  # noqa: SLF001


def test_publish_video_part_picks_the_right_file(env: dict[str, Any]) -> None:
    pick = rr.publish_video_part
    assert pick({"render": {}}) is None
    assert pick(None) is None
    rid = _record(env)
    _run(lambda: ov.apply(rid))
    rec = env["store"].get(rid)
    # 1) video만 있고 overlay 적용됨 → overlay_video
    assert pick(rec) == "overlay_video"
    # 2) overlay 상태가 적용이 아님 → 최신 영상
    rec2 = env["store"].get(rid)
    rec2["render"]["overlay"]["status"] = "failed"
    assert pick(rec2) == "video"
    # 3) final_video가 생기면 overlay의 원본이 최신이 아니므로 final_video
    _put(env, rid, "final_video")
    rec3 = env["store"].get(rid)
    assert pick(rec3) == "final_video"
    # 4) final_video에 다시 얹으면 overlay_video
    _run(lambda: ov.apply(rid))
    assert pick(env["store"].get(rid)) == "overlay_video"
    # 같은 칸이어도 올린 파일이 바뀌었으면(해시 다름) 원본 최신 영상
    _put(env, rid, "final_video", b"replaced")
    assert pick(env["store"].get(rid)) == "final_video"


def test_youtube_and_thumbnails_use_the_same_pick(env: dict[str, Any]) -> None:
    rid = _record(env)
    _run(lambda: ov.apply(rid))
    rec = env["store"].get(rid)
    assert yt._default_part(rec) == "overlay_video"  # noqa: SLF001
    part, info = ct.source_video(rec) or ("", {})
    assert part == "overlay_video" and info["asset"] == "overlay.mp4"
    # 원본(video)로 되돌아가는 경우도 같다
    rec["render"]["overlay"]["status"] = "skipped"
    assert yt._default_part(rec) == "video"  # noqa: SLF001
    assert (ct.source_video(rec) or ("",))[0] == "video"


def test_redo_uses_edited_captions_and_keeps_them(env: dict[str, Any]) -> None:
    rid = _record(env)
    _run(lambda: ov.apply(rid))
    done = _run(lambda: ov.redo(rid, {"1": "고친 자막", "3": ""}))
    assert done["overlay"]["status"] == "applied" and done["overlay"]["captions"] == 1
    assert done["overlay"]["edits"] == {"1": "고친 자막", "3": ""}
    assert len(env["ff"].calls) == 2
    # 스토리보드 원본은 건드리지 않는다
    assert env["store"].get(rid)["storyboard"]["shots"][0]["caption_ko"] == "첫 자막"
    # 다음 자동 얹기(새 final_video)도 고친 글을 쓴다
    _put(env, rid, "final_video")
    state = _run(lambda: ov.apply(rid))
    assert state["captions"] == 1 and state["edits"]["1"] == "고친 자막"


def test_redo_refusals(env: dict[str, Any]) -> None:
    with pytest.raises(ov.OverlayRefused) as error:
        _run(lambda: ov.redo("CR-000000000000", {"1": "a"}))
    assert error.value.code == "not_found"
    store = env["store"]
    rid = store.add_sheet(SHEET, None, None)
    with pytest.raises(ov.OverlayRefused) as error:
        _run(lambda: ov.redo(rid, {"1": "a"}))
    assert error.value.code == "video_not_ready"
    with pytest.raises(ov.OverlayRefused) as error:
        _run(lambda: ov.redo(rid, {}))
    assert error.value.code == "invalid_input"


def test_redo_skill_runs_through_the_handler(env: dict[str, Any]) -> None:
    rid = _record(env)
    request = AgentRequest(
        meta=META,
        skill="creative_overlay_redo",
        input={"record_id": rid, "captions": [{"no": 1, "caption_ko": "핸들러 자막"}]},
    )
    result = asyncio.run(handlers.creative_overlay_redo(request))
    assert result.status == TaskStatus.COMPLETED
    assert result.output["overlay"]["status"] == "applied"
    assert result.output["overlay_video"]["asset"] == "overlay.mp4"
    missing = AgentRequest(
        meta=META,
        skill="creative_overlay_redo",
        input={"record_id": "CR-000000000000", "captions": [{"no": 1, "caption_ko": "a"}]},
    )
    refused = asyncio.run(handlers.creative_overlay_redo(missing))
    assert refused.status == TaskStatus.FAILED and refused.outcome == "not_found"


def test_end_card_shows_the_company_name_only_when_the_settings_have_one(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _record(env)
    monkeypatch.setattr(ov, "get_company", lambda cid: None)
    assert ov._company_label(None) == ""  # noqa: SLF001

    class Company:
        name = "  우리  서점 "

    monkeypatch.setattr(ov, "get_company", lambda cid: Company())
    assert ov._company_label(None) == "우리 서점"  # noqa: SLF001
    assert rid


def test_company_folders_do_not_mix(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from ax_agent_template import company_scope

    store_a = env["store"]
    rid = _record(env)
    # 다른 회사 폴더에는 같은 이름 영상이 있어도 그 회사 자리(companies/<회사>)만 본다
    other = env["media"] / "companies" / "nubitour" / "creative" / rid
    other.mkdir(parents=True)
    (other / "video.mp4").write_bytes(b"other-company")
    with company_scope.using("nubitour"):
        folder = ov._folder(rid, None)  # noqa: SLF001
    assert folder == other
    assert ov._folder(rid, None) == env["media"] / "creative" / rid  # noqa: SLF001
    assert store_a.get(rid) is not None
