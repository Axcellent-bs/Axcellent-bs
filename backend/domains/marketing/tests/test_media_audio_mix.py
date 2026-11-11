"""BGM 후처리: 가짜 실행기로 ffmpeg 인자 구성·길이·검사·오류를 검증한다(실제 ffmpeg 호출 없음)."""

import hashlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from marketing_agent.media import audio_mix
from marketing_agent.media.audio_mix import AudioMixError, RunOutput, count_tracks, overlay_bgm

VIDEO_TRACK = b"....hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide-x"
AUDIO_TRACK = b"....hdlr\x00\x00\x00\x00\x00\x00\x00\x00soun-x"
MUXED_OK = b"\x00\x00\x00\x20ftypisom" + VIDEO_TRACK + AUDIO_TRACK


class FakeRunner:
    """실제 ffmpeg 대신 출력 파일을 흉내 내 쓴다."""

    def __init__(self, muxed: bytes = MUXED_OK, fail_at: int | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.muxed = muxed
        self.fail_at = fail_at

    def __call__(self, args: Sequence[str], timeout_s: int) -> RunOutput:
        self.calls.append(tuple(args))
        index = len(self.calls) - 1
        if self.fail_at == index:
            return RunOutput(1, "Invalid data found")
        Path(args[-1]).write_bytes(self.muxed if index == 1 else b"AAC")
        return RunOutput(0)


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    video, bgm = tmp_path / "in.mp4", tmp_path / "bgm.mp3"
    video.write_bytes(b"\x00\x00\x00\x20ftypisom" + VIDEO_TRACK + AUDIO_TRACK)  # 오디오 있는 입력
    bgm.write_bytes(b"ID3bgm")
    return video, bgm, tmp_path / "out.mp4"


def test_commands_trim_fade_and_drop_input_audio(files) -> None:
    video, bgm, out = files
    runner = FakeRunner()
    result = overlay_bgm(video, bgm, out, duration_s=8, runner=runner, ffmpeg="ff-bin")
    trim, mux = runner.calls
    assert trim[0] == "ff-bin" and mux[0] == "ff-bin"
    assert trim[trim.index("-t") + 1] == "8"
    assert "-vn" in trim and trim[trim.index("-c:a") + 1] == "aac"
    filters = trim[trim.index("-af") + 1]
    assert filters == "afade=t=in:st=0:d=0.2,afade=t=out:st=7.2:d=0.8"
    assert str(bgm.resolve()) in trim
    assert mux[mux.index("-c:v") + 1] == "copy"
    maps = [mux[i + 1] for i, a in enumerate(mux) if a == "-map"]
    assert maps == ["0:v:0", "1:a:0"]  # 입력 영상의 오디오는 어디에서도 가져오지 않는다
    assert "-an" not in mux and str(video.resolve()) in mux and mux[-1] == str(out.resolve())
    assert all(isinstance(a, str) for call in runner.calls for a in call)
    assert result.commands == (trim, mux) and result.audio_tracks == 1
    assert result.sha256 == hashlib.sha256(MUXED_OK).hexdigest() and result.ffmpeg_bin == "ff-bin"
    assert out.read_bytes() == MUXED_OK
    assert not list(out.parent.glob("*.m4a"))  # 임시 파일은 지운다


def test_short_video_uses_its_length(files) -> None:
    video, bgm, out = files
    runner = FakeRunner()
    result = overlay_bgm(video, bgm, out, duration_s=3.5, runner=runner, fade_out_s=0.5)
    assert runner.calls[0][runner.calls[0].index("-t") + 1] == "3.5"
    assert "afade=t=out:st=3:d=0.5" in runner.calls[0][runner.calls[0].index("-af") + 1]
    assert result.duration_s == 3.5


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"duration_s": 8.5}, "invalid_duration"),
        ({"duration_s": 0}, "invalid_duration"),
        ({"duration_s": 8, "fade_in_s": 0.05}, "invalid_fade_in"),
        ({"duration_s": 8, "fade_in_s": 0.5}, "invalid_fade_in"),
        ({"duration_s": 8, "fade_out_s": 0.2}, "invalid_fade_out"),
        ({"duration_s": 8, "fade_out_s": 1.5}, "invalid_fade_out"),
        ({"duration_s": 0.5}, "fade_longer_than_clip"),
    ],
)
def test_invalid_parameters_never_run_ffmpeg(files, kwargs, code) -> None:
    video, bgm, out = files
    runner = FakeRunner()
    with pytest.raises(AudioMixError) as info:
        overlay_bgm(video, bgm, out, runner=runner, **kwargs)
    assert info.value.code == code and runner.calls == []


def test_missing_inputs_and_overwrite_are_refused(files, tmp_path: Path) -> None:
    video, bgm, out = files
    runner = FakeRunner()
    with pytest.raises(AudioMixError) as info:
        overlay_bgm(tmp_path / "none.mp4", bgm, out, duration_s=8, runner=runner)
    assert info.value.code == "video_not_found"
    with pytest.raises(AudioMixError) as info:
        overlay_bgm(video, tmp_path / "none.mp3", out, duration_s=8, runner=runner)
    assert info.value.code == "bgm_not_found"
    with pytest.raises(AudioMixError) as info:
        overlay_bgm(video, bgm, video, duration_s=8, runner=runner)
    assert info.value.code == "output_overwrites_input" and runner.calls == []


@pytest.mark.parametrize("fail_at", [0, 1])
def test_ffmpeg_failure_is_reported_and_cleans_up(files, fail_at) -> None:
    video, bgm, out = files
    runner = FakeRunner(fail_at=fail_at)
    with pytest.raises(AudioMixError) as info:
        overlay_bgm(video, bgm, out, duration_s=8, runner=runner)
    assert info.value.code == "ffmpeg_failed" and len(runner.calls) == fail_at + 1
    assert not out.exists() and not list(out.parent.glob("*.m4a"))


@pytest.mark.parametrize(
    "muxed",
    [
        b"ftyp" + VIDEO_TRACK,  # 오디오 없음
        b"ftyp" + VIDEO_TRACK + AUDIO_TRACK + AUDIO_TRACK,  # 오디오 2개
        b"ftyp" + AUDIO_TRACK,  # 영상 없음
    ],
)
def test_output_with_wrong_tracks_is_rejected_and_removed(files, muxed) -> None:
    video, bgm, out = files
    with pytest.raises(AudioMixError) as info:
        overlay_bgm(video, bgm, out, duration_s=8, runner=FakeRunner(muxed=muxed))
    assert info.value.code == "output_track_check_failed" and not out.exists()


def test_track_counter() -> None:
    assert count_tracks(MUXED_OK, b"soun") == 1 and count_tracks(MUXED_OK, b"vide") == 1
    assert count_tracks(b"no boxes", b"soun") == 0


def test_ffmpeg_binary_comes_from_setting_and_missing_binary_is_clear(
    files, monkeypatch: pytest.MonkeyPatch
) -> None:
    video, bgm, out = files
    monkeypatch.setenv("MARKETING_FFMPEG_BIN", "custom-ffmpeg")
    runner = FakeRunner()
    overlay_bgm(video, bgm, out, duration_s=8, runner=runner)
    assert runner.calls[0][0] == "custom-ffmpeg"

    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(AudioMixError) as info:
        overlay_bgm(video, bgm, out, duration_s=8)  # 기본 실행기(가짜 subprocess)
    assert info.value.code == "ffmpeg_not_found" and not out.exists()


def test_default_runner_uses_arg_list_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen.update(args=args, kwargs=kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    audio_mix.subprocess_runner(["ffmpeg", "-version"], 5)
    assert seen["args"] == ["ffmpeg", "-version"]
    assert not seen["kwargs"].get("shell") and seen["kwargs"]["timeout"] == 5

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 5)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(AudioMixError) as info:
        audio_mix.subprocess_runner(["ffmpeg"], 5)
    assert info.value.code == "ffmpeg_timeout"
