"""모션 참조 영상 (11/25): 회사 계정 영상 모델이 video_references로 동작만 따라 만든다.

가짜 Higgsfield·가짜 관문·가짜 ffmpeg·임시 저장소만 쓴다(실제로 부르지 않는다).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import company_scope
from marketing_agent import creative_mcp as cm
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import motion_ref as mr
from marketing_agent.media.audio_mix import RunOutput


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = _load("creative_render_mcp_helpers_motion", "test_creative_render_mcp.py")
FRONT = b"\xff\xd8\xff\xe0" + bytes(40)
CLIP = b"\x00\x00\x00\x18ftypmp42" + bytes(40)  # 잘라 낸 모션 참조(가짜)


def _fake_ffmpeg(ok: bool = True) -> Any:
    calls: list[list[str]] = []

    def run(args: Sequence[str], timeout_s: int) -> RunOutput:
        calls.append(list(args))
        if ok:
            Path(args[-1]).write_bytes(CLIP)
        return RunOutput(0 if ok else 1, "")

    run.calls = calls  # type: ignore[attr-defined]
    return run


Made = tuple[dict[str, Any], list[tuple[bytes, str]], Any]


def _make(
    e: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    motion: bool,
    ffmpeg_ok: bool = True,
    model: str = "kling_4_0_flash",
) -> Made:
    sent: list[tuple[bytes, str]] = []
    real_upload = cm.upload

    async def upload(hf: Any, data: bytes, filename: str, content_type: str) -> str:
        sent.append((data, content_type))
        return await real_upload(hf, data, filename, content_type)

    monkeypatch.setattr(cm, "upload", upload)
    runner = _fake_ffmpeg(ffmpeg_ok)
    monkeypatch.setattr(mr, "RUNNER", runner)
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", model)
    with company_scope.using("catbooks"):
        rid = cr.store().add_sheet(M.SHEET | {"kind": "virtual_human"}, None, None)
        rr.save_original(rid, FRONT, "image/jpeg")
    e["store"].attach_storyboard(rid, "무대에서 노래한다", 6, M.BOARD)
    M._run(lambda: rr.request_video(e["ledger"], M.META, rid, "gaon"))  # noqa: SLF001
    if motion:  # 승인을 기다리는 동안 사람이 모션 참조 영상을 올린다
        cid = e["store"].get_render(rid)["video"]["company_id"]
        (rr._dir(rid, cid) / mr.FILE).write_bytes(b"original motion clip")  # noqa: SLF001
        rr._set(rid, mr.PART, status="done", asset=mr.FILE)  # noqa: SLF001
    e["gate"].status["generate_creative_video"] = "executed"
    e["hf"].waits = 0
    M._run(lambda: rr.check_video(e["ledger"], M.META, rid))  # noqa: SLF001
    assert e["store"].get_render(rid)["video"]["status"] == "done"
    batch = dict(e["hf"].tools)["generate_video_batch"]["requests"][0]["params"]
    return batch, sent, runner


def test_a_motion_clip_goes_as_video_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gen = M.env.__wrapped__(tmp_path, monkeypatch)
    e = next(gen)
    try:
        batch, sent, runner = _make(e, monkeypatch, motion=True)
        roles = [m["role"] for m in batch["medias"]]
        assert roles == ["image_references", "video_references"]
        assert batch["mode"] == "reference-to-video"
        assert batch["prompt"].startswith(mr.PROMPT_EN)
        assert (CLIP, "video/mp4") in sent  # 잘라 낸 클립을 올린다(원본 그대로가 아니라)
        args = runner.calls[0]
        assert args[args.index("-t") + 1] == str(mr.MAX_SECONDS) and "-an" in args
    finally:
        gen.close()


def test_without_a_motion_clip_nothing_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gen = M.env.__wrapped__(tmp_path, monkeypatch)
    e = next(gen)
    try:
        batch, _, runner = _make(e, monkeypatch, motion=False)
        assert [m["role"] for m in batch["medias"]] == ["image_references"]
        assert not batch["prompt"].startswith(mr.PROMPT_EN) and runner.calls == []
    finally:
        gen.close()


def test_a_failed_trim_makes_the_video_without_motion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gen = M.env.__wrapped__(tmp_path, monkeypatch)
    e = next(gen)
    try:
        batch, _, _ = _make(e, monkeypatch, motion=True, ffmpeg_ok=False)
        assert [m["role"] for m in batch["medias"]] == ["image_references"]
    finally:
        gen.close()


def test_only_models_that_take_video_references_get_one() -> None:
    assert cm.takes_video_references("kling_4_0_flash") is True
    assert cm.takes_video_references("kling3_0_turbo") is False
    assert cm.takes_video_references("unknown") is False
    params = cm.with_video_reference({"model": "kling_4_0_flash", "medias": []}, "m1")
    assert params["medias"] == [{"value": "m1", "role": "video_references"}]
    assert params["mode"] == "reference-to-video"


def test_motion_video_is_an_uploadable_part() -> None:
    assert rr.UPLOAD_FILES["motion_video"] == ("motion.mp4",)
    assert rr.FILES["motion_video"] == "motion.mp4"
