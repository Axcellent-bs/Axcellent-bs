"""영상 기술 검사 (11/26 H): 검은 화면·멈춘 화면·길이. ffmpeg는 가짜 실행기로만 부른다.

- 가짜 stderr로 black → warn·ranges, 엔드카드 마지막 2초 안 구간은 뺀다, freeze → warn
- 길이 15.0/목표 15 → pass, 13.2 → warn
- ffmpeg 없음 → 그 검사만 error, 흐름은 계속
- 같은 영상 sha면 다시 안 돌린다
- regenerate=True이고 qc에 warn이 있을 때만 다 만든 영상도 새 멱등 키로 다시 승인 요청
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from marketing_agent import creative_qc as qc
from marketing_agent import creative_render as rr

sys.path.insert(0, str(Path(__file__).parent))
from test_creative_render import (  # noqa: E402, F401 - env는 시험 도구(fixture)
    META,
    _board_done,
    _run,
    env,
)

BLACK_ERR = (
    "[blackdetect @ 0x1] black_start:0 black_end:0.8 black_duration:0.8\n"
    "[blackdetect @ 0x1] black_start:13.4 black_end:15 black_duration:1.6\n"
)
FREEZE_ERR = (
    "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 6.2\n"
    "[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 2.2\n"
    "[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 8.4\n"
    "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 13.5\n"
)


class FakeFfmpeg:
    def __init__(self, black: str = "", freeze: str = "", missing: bool = False) -> None:
        self.black, self.freeze, self.missing = black, freeze, missing
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout_s: int) -> tuple[int, str]:
        self.calls.append(args)
        if self.missing:
            raise qc.FfmpegMissing
        vf = args[args.index("-vf") + 1]
        return 0, self.black if vf.startswith("blackdetect") else self.freeze


def test_black_ranges_warn_and_end_card_ignored(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    qc.RUNNER, before = FakeFfmpeg(black=BLACK_ERR), qc.RUNNER
    try:
        got = qc.check_black(video, 15.0)
    finally:
        qc.RUNNER = before
    assert got["status"] == "warn"
    assert got["ranges"] == [[0.0, 0.8]]  # 13.4~15는 엔드카드(마지막 2초) 안이라 뺀다
    assert "0.0~0.8초" in got["detail_ko"]


def test_freeze_warn_and_open_end_closed_by_duration() -> None:
    ranges = qc.freeze_ranges(FREEZE_ERR, 15.0)
    assert ranges == [[6.2, 8.4], [13.5, 15.0]]
    assert qc.outside_end_card(ranges, 15.0, qc.FREEZE_MIN_S) == [[6.2, 8.4]]
    # 엔드카드에 걸친 구간은 그 앞까지만, 남은 길이가 짧으면 뺀다
    assert qc.outside_end_card([[12.0, 15.0]], 15.0, qc.FREEZE_MIN_S) == []
    assert qc.outside_end_card([[10.0, 15.0]], 15.0, qc.FREEZE_MIN_S) == [[10.0, 13.0]]


def test_duration_pass_and_warn() -> None:
    assert qc.check_duration(15.0, 15)["status"] == "pass"
    assert qc.check_duration(15.9, 15)["status"] == "pass"
    warn = qc.check_duration(13.2, 15)
    assert warn["status"] == "warn" and warn["detail_ko"] == "13.2초 (목표 15초 ±1)"
    assert qc.check_duration(None, 15)["status"] == "error"
    assert qc.check_duration(15.0, None)["status"] == "error"


def _video_done(ctx: dict[str, Any], sha: str = "a" * 64) -> str:
    rid = _board_done(ctx)
    folder = ctx["dir"] / "creative" / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "video.mp4").write_bytes(b"video-bytes")
    render = ctx["store"].get_render(rid)
    render["video"] = {"status": "done", "asset": "video.mp4", "sha256": sha, "seconds": 15,
                       "summary": {"credits_estimate": 27}}  # fmt: skip
    ctx["store"].set_render(rid, render)
    return rid


def _patch(monkeypatch, fake: FakeFfmpeg, duration: float | None = 15.0) -> None:  # noqa: ANN001
    monkeypatch.setattr(qc, "RUNNER", fake)
    monkeypatch.setattr(qc, "PROBE", lambda video: duration)


def test_inspect_records_checks_and_skips_same_sha(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    fake = FakeFfmpeg(black="", freeze=FREEZE_ERR)
    _patch(monkeypatch, fake)
    rid = _video_done(env)
    got = qc.inspect(rid, None)
    assert got is not None and got["video_sha"] == "a" * 64 and got["part"] == "video"
    by = {c["id"]: c for c in got["checks"]}
    assert by["black"]["status"] == "pass" and by["freeze"]["status"] == "warn"
    assert by["freeze"]["ranges"] == [[6.2, 8.4]] and by["duration"]["status"] == "pass"
    assert env["store"].get_render(rid)["qc"]["checks"] == got["checks"]
    calls = len(fake.calls)
    qc.inspect(rid, None)
    assert len(fake.calls) == calls  # 같은 sha면 다시 안 돌린다
    render = env["store"].get_render(rid)
    render["video"]["sha256"] = "b" * 64  # 영상이 바뀌면 다시
    env["store"].set_render(rid, render)
    qc.inspect(rid, None)
    assert len(fake.calls) == calls + 2


def test_ffmpeg_missing_is_error_but_flow_continues(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    _patch(monkeypatch, FakeFfmpeg(missing=True), duration=13.2)
    rid = _video_done(env)
    got = _run(lambda: qc.run(rid, None))
    by = {c["id"]: c for c in got["checks"]}
    assert by["black"]["status"] == "error" and "ffmpeg가 없어" in by["black"]["detail_ko"]
    assert by["freeze"]["status"] == "error"
    assert by["duration"]["status"] == "warn"  # 길이는 ffprobe로 따로
    assert env["store"].get_render(rid)["video"]["status"] == "done"  # 영상은 그대로


def test_overlay_hook_runs_qc_even_if_overlay_fails(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    from marketing_agent import creative_overlay

    async def broken(record_id, cid):  # noqa: ANN001, ANN202
        raise RuntimeError("overlay down")

    monkeypatch.setattr(creative_overlay, "apply", broken)
    _patch(monkeypatch, FakeFfmpeg())
    rid = _video_done(env)
    _run(lambda: rr._overlay_after_attach(rid, None))  # noqa: SLF001
    assert env["store"].get_render(rid)["qc"]["video_sha"] == "a" * 64


def test_regenerate_only_when_qc_has_warn(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    _patch(monkeypatch, FakeFfmpeg(freeze=FREEZE_ERR))
    rid = _video_done(env)
    # qc가 없으면 regenerate여도 지금 영상을 그대로 돌려준다
    same = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon", regenerate=True))
    assert same["status"] == "done"
    qc.inspect(rid, None)  # freeze warn
    # regenerate가 아니면 지금과 같다
    again = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon"))
    assert again["status"] == "done"
    redo = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon", regenerate=True))
    assert redo["status"] == "awaiting_approval"  # 사람 승인 관문 그대로
    assert redo["key"].endswith(":retry1") and redo["attempt"] == 1  # 새 멱등 키
    keys = [k for a, _, k in env["gate"].calls if a == "generate_creative_video"]
    assert keys and keys[-1].endswith(":retry1")


def test_regenerate_ignored_when_all_pass(
    env: dict[str, Any], monkeypatch  # noqa: ANN001, F811
) -> None:
    _patch(monkeypatch, FakeFfmpeg())
    rid = _video_done(env)
    qc.inspect(rid, None)
    assert not qc.has_warn(env["store"].get_render(rid))
    got = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon", regenerate=True))
    assert got["status"] == "done"
    assert not [a for a, _, _ in env["gate"].calls if a == "generate_creative_video"]
