"""영상 다시 만들기가 승인 뒤 만들기에서 실패해도 예전 영상 (11/26 K).

거절·기한 지남은 #1857에서 되돌린다. 남은 경우는 승인 뒤 만들기 자체가 실패할 때다. 그때 예전
파일이 덮였을 수 있어 되돌리지 못했다. 이제 다시 만들기면 새 영상을 임시 이름으로 받는다. 끝까지
받고 길이를 확인한 뒤에만 예전 파일 자리로 바꿔 넣는다.
가짜 Higgsfield·가짜 관문·가짜 ffmpeg만 쓴다.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from marketing_agent import creative_qc as qc
from marketing_agent import creative_render as rr

sys.path.insert(0, str(Path(__file__).parent))
from test_creative_qc import FREEZE_ERR, FakeFfmpeg, _patch, _video_done  # noqa: E402
from test_creative_render import (  # noqa: E402, F401 - env는 시험 도구(fixture)
    META,
    _board_done,
    _run,
    env,
)

OLD = b"old-video"


def _approved_regenerate(ctx: dict[str, Any], monkeypatch) -> tuple[str, Path]:  # noqa: ANN001
    """다 만든 예전 영상(OLD) + 확인 필요 → [영상 다시 만들기] → 사람 승인 직전."""
    _patch(monkeypatch, FakeFfmpeg(freeze=FREEZE_ERR))
    rid = _video_done(ctx)
    final = ctx["dir"] / "creative" / rid / "video.mp4"
    final.write_bytes(OLD)
    render = ctx["store"].get_render(rid)
    render["video"]["sha256"] = hashlib.sha256(OLD).hexdigest()
    ctx["store"].set_render(rid, render)
    qc.inspect(rid, None)
    redo = _run(lambda: rr.request_video(ctx["ledger"], META, rid, "gaon", regenerate=True))
    assert redo["status"] == "awaiting_approval"
    ctx["gate"].status["generate_creative_video"] = "executed"  # 사람이 승인
    return rid, final


def _assert_old_back(ctx: dict[str, Any], rid: str, final: Path, reason: str) -> None:
    rec = ctx["store"].get(rid)
    video = rec["render"]["video"]
    assert video["status"] == "done" and video["sha256"] == hashlib.sha256(OLD).hexdigest()
    assert video["regenerate"]["status"] == "failed" and video["regenerate"]["reason"] == reason
    assert rr.publish_video_part(rec) == "video"  # 게시·썸네일 다시 예전 영상
    assert final.read_bytes() == OLD  # 예전 파일 그대로
    assert not rr.staging_path(final).exists()  # 임시 파일만 지운다


def test_download_failure_keeps_old_file_and_restores(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    rid, final = _approved_regenerate(env, monkeypatch)
    real = rr._download  # noqa: SLF001

    async def broken(client, url, path):  # noqa: ANN001, ANN202
        await real(client, url, path)  # 바이트는 이미 디스크에 썼다(최악의 경우)
        raise rr.RenderError("download_broken")

    monkeypatch.setattr(rr, "_download", broken)
    monkeypatch.setattr(rr, "PROBE_VIDEO", lambda path: 15.0)
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    _assert_old_back(env, rid, final, "download_broken")


def test_unchecked_length_keeps_old_file(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    rid, final = _approved_regenerate(env, monkeypatch)
    monkeypatch.setattr(rr, "PROBE_VIDEO", lambda path: None)  # 길이를 읽지 못함
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    _assert_old_back(env, rid, final, "regen_duration_unknown")


def test_success_swaps_in_new_file_and_drops_previous(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    rid, final = _approved_regenerate(env, monkeypatch)
    probed: list[Path] = []
    monkeypatch.setattr(rr, "PROBE_VIDEO", lambda path: probed.append(path) or 15.0)
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    video = env["store"].get_render(rid)["video"]
    assert video["status"] == "done"
    assert video["sha256"] == hashlib.sha256(b"video-bytes").hexdigest()
    assert video.get("previous") is None and video.get("regenerate") is None
    assert final.read_bytes() == b"video-bytes"  # 새 파일로 바뀜
    assert probed == [rr.staging_path(final)]  # 바꿔 넣기 전 임시 파일 길이를 봤다
    assert not rr.staging_path(final).exists()


def test_first_video_failure_is_unchanged(env: dict[str, Any]) -> None:  # noqa: F811
    rid = _board_done(env)
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    env["gate"].status["generate_creative_video"] = "executed"
    env["hf"].fail = True
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    video = env["store"].get_render(rid)["video"]
    assert video["status"] == "failed" and video["error"] == "job_failed"
    assert video.get("regenerate") is None
    final = env["dir"] / "creative" / rid / "video.mp4"
    assert not final.exists() and not rr.staging_path(final).exists()
