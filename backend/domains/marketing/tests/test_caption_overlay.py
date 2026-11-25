"""자막·엔드카드 얹기(ffmpeg drawtext): 인자 목록·시간표·글꼴·실행 파일 없음. 가짜 실행기만."""

from __future__ import annotations

from pathlib import Path

import pytest

from marketing_agent.media import caption_overlay as co
from marketing_agent.media.audio_mix import AudioMixError, RunOutput


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(32))
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    return {"video": video, "font": font, "out": tmp_path / "overlay.mp4"}


class Runner:
    def __init__(self, returncode: int = 0, write: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.write = write

    def __call__(self, args, timeout_s):  # noqa: ANN001, ANN204
        self.calls.append(list(args))
        if self.write:
            Path(args[-1]).write_bytes(b"overlaid-video")
        return RunOutput(self.returncode, "boom" if self.returncode else "")


def _apply(files: dict[str, Path], runner: Runner, **kw):  # noqa: ANN003, ANN202
    kw.setdefault(
        "captions", [co.Caption("안녕하세요", 0.0, 2.0), co.Caption("둘째 자막", 2.0, 4.0)]
    )
    kw.setdefault("end_card", "AXcellent\n지금 만나 보세요")
    kw.setdefault("duration_s", 6.0)
    return co.apply_overlay(
        files["video"], files["out"], font=files["font"], runner=runner, ffmpeg="ffmpeg", **kw
    )


def test_args_have_one_drawtext_per_caption_plus_the_end_card(files: dict[str, Path]) -> None:
    runner = Runner()
    result = _apply(files, runner)
    args = runner.calls[0]
    filters = args[args.index("-vf") + 1]
    assert filters.count("drawtext=") == 3  # 자막 2 + 엔드카드 1
    assert "enable='between(t,0,2)'" in filters
    assert "enable='between(t,2,4)'" in filters
    assert "enable='between(t,4,6)'" in filters  # 마지막 2초
    assert filters.count("textfile=") == 3
    assert "box=1" in filters and "boxcolor=black@0.55" in filters
    assert "y=h*0.78" in filters  # 화면 아래 22%
    assert args[0] == "ffmpeg" and args[-1].endswith("overlay.tmp.mp4")
    assert result.captions == 2 and result.end_card is True
    assert files["out"].read_bytes() == b"overlaid-video"
    assert not files["out"].with_name("overlay.tmp.mp4").exists()
    assert files["video"].read_bytes().startswith(b"\x00\x00\x00\x18ftyp")  # 원본은 그대로


def test_caption_overlapping_the_end_card_is_cut_before_it(files: dict[str, Path]) -> None:
    runner = Runner()
    late = [co.Caption("끝까지", 3.0, 6.0), co.Caption("카드 뒤", 5.0, 6.0)]
    _apply(files, runner, captions=late)
    filters = runner.calls[0][runner.calls[0].index("-vf") + 1]
    assert "enable='between(t,3,4)'" in filters  # 엔드카드 시작(4초)에서 끊는다
    assert filters.count("drawtext=") == 2  # 카드 뒤에 시작하는 자막은 건너뛴다


def test_text_goes_through_textfile_and_is_removed_after(
    files: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    paths: list[Path] = []

    def runner(args, timeout_s):  # noqa: ANN001, ANN202
        filters = args[args.index("-vf") + 1]
        for part in filters.split("textfile='")[1:]:
            path = Path(part.split("'")[0].replace("\\:", ":"))
            seen.append(path.read_text(encoding="utf-8"))
            paths.append(path)
        Path(args[-1]).write_bytes(b"x")
        return RunOutput(0)

    _apply(files, runner, captions=[co.Caption("가나다라마바사아자차카타파하 가나다라마바", 0, 2)])
    assert "\n" in seen[0]
    assert all(len(line) <= 16 for line in seen[0].split("\n"))
    assert "AXcellent" in seen[1]
    assert paths and not any(p.exists() for p in paths)  # 끝나면 임시 글 파일을 지운다


def test_wrap_text() -> None:
    assert co.wrap_text("짧은 글") == "짧은 글"
    wrapped = co.wrap_text("열두 글자 넘는 자막 문장입니다 정말로")
    assert wrapped == "열두 글자 넘는 자막\n문장입니다 정말로"
    assert co.wrap_text("a" * 40).split("\n") == ["a" * 16, "a" * 16, "a" * 8]


def test_shots_without_times_split_the_video_evenly() -> None:
    shots = [
        {"caption_ko": "하나"},
        {"caption_ko": ""},  # 빈 자막 칸은 건너뛰되 자리는 차지한다
        {"caption_ko": "셋"},
        {"caption_ko": "넷", "start_s": 7.0, "end_s": 8.0},  # 시간이 있으면 그대로
    ]
    plan = co.plan_captions(shots, 8.0)
    assert [(c.text, c.start_s, c.end_s) for c in plan] == [
        ("하나", 0.0, 2.0),
        ("셋", 4.0, 6.0),
        ("넷", 7.0, 8.0),
    ]


def test_missing_font_raises_font_not_found(files: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(co.OverlayError) as error:
        co.apply_overlay(
            files["video"],
            files["out"],
            captions=[co.Caption("a", 0, 1)],
            end_card=None,
            duration_s=3,
            font=tmp_path / "none.ttf",
            runner=Runner(),
        )
    assert error.value.code == "font_not_found"


def test_font_path_comes_from_the_server_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(co.FONT_ENV, raising=False)
    assert co.font_path() is None
    monkeypatch.setenv(co.FONT_ENV, str(tmp_path / "missing.ttf"))
    assert co.font_path() is None
    font = tmp_path / "f.ttf"
    font.write_bytes(b"f")
    monkeypatch.setenv(co.FONT_ENV, str(font))
    assert co.font_path() == font


def test_ffmpeg_not_found_and_failures_carry_codes(files: dict[str, Path]) -> None:
    def missing(args, timeout_s):  # noqa: ANN001, ANN202
        raise AudioMixError("ffmpeg_not_found")

    with pytest.raises(co.OverlayError) as error:
        _apply(files, missing)
    assert error.value.code == "ffmpeg_not_found"
    with pytest.raises(co.OverlayError) as error:
        _apply(files, Runner(returncode=1))
    assert error.value.code == "ffmpeg_failed"
    with pytest.raises(co.OverlayError) as error:
        _apply(files, Runner(write=False))
    assert error.value.code == "output_missing"
    assert not files["out"].exists()


def test_nothing_to_overlay(files: dict[str, Path]) -> None:
    with pytest.raises(co.OverlayError) as error:
        _apply(files, Runner(), captions=[], end_card=None)
    assert error.value.code == "nothing_to_overlay"
