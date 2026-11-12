"""AI 라벨 위치: 이미지·영상 모두 오른쪽 하단, 9:16·4:5·1:1에서 잘리거나 겹치지 않는다.

위치·여백·크기는 `sns_label.toml` 설정이다(코드에 박지 않음). 계산 시험은 도구 없이 돌고,
실제 ffmpeg로 합성 이미지·영상에 입혀 상자 위치를 픽셀로 재는 시험은 ffmpeg·ffprobe가
없으면 사유를 밝히고 건너뛴다. 번들 글꼴(Pretendard Bold)을 쓰므로 한글 문구 그대로 확인한다.
"""

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest

from marketing_agent import sns_label

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(
    not FFMPEG, reason="ffmpeg·ffprobe가 없어 실제 오버레이 시험을 건너뜀"
)
GRAY = 128
SIZES = {"9x16": (1080, 1920), "4x5": (1080, 1350), "1x1": (1080, 1080), "1.91x1": (1080, 565)}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sns_label.CONFIG_ENV, raising=False)
    monkeypatch.delenv(sns_label.FONT_ENV, raising=False)


def _ffmpeg(*args: str) -> bytes:
    done = subprocess.run(  # noqa: S603, S607
        ["ffmpeg", "-y", "-loglevel", "error", *args], check=True, capture_output=True
    )
    return done.stdout


def _bbox(image: Path, width: int, height: int) -> tuple[int, int, int, int]:
    """배경(회색)과 다른 픽셀의 (왼, 위, 오른, 아래) 상자. 오른쪽·아래는 포함하지 않는 끝."""
    raw = _ffmpeg("-i", str(image), "-f", "rawvideo", "-pix_fmt", "gray", "-")
    assert len(raw) == width * height
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        row = raw[y * width : (y + 1) * width]
        hit = [x for x, v in enumerate(row) if abs(v - GRAY) > 30]
        if hit:
            xs += [hit[0], hit[-1]]
            ys.append(y)
    assert xs, "라벨이 그려지지 않았다"
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def test_default_config_is_bottom_right_and_tall_screens_get_a_bigger_bottom_margin() -> None:
    cfg = sns_label.load_config()
    assert (cfg.position, cfg.align) == ("bottom", "right")
    tall = sns_label.label_box(cfg, 1080, 1920)
    feed = sns_label.label_box(cfg, 1080, 1350)
    assert tall["margin_y"] == round(1920 * cfg.margin_ratio_tall)
    assert feed["margin_y"] == round(1350 * cfg.margin_ratio)
    assert tall["margin_y"] / 1920 > feed["margin_y"] / 1350  # 릴스 하단 UI 안전영역


@pytest.mark.parametrize(
    ("align", "expected"),
    [("right", "x=w-text_w-"), ("left", "x=58"), ("center", "x=(w-text_w)/2")],
)
def test_align_comes_from_config_not_code(tmp_path: Path, align: str, expected: str) -> None:
    cfg = dataclasses.replace(sns_label.load_config(), align=align)
    draw = sns_label.build_drawtext(cfg, "/f.ttf", 1000, 1250, str(tmp_path / "t.txt"), "라벨")
    assert expected in draw


def test_invalid_align_is_rejected(tmp_path: Path) -> None:
    text = sns_label.DEFAULT_CONFIG.read_text(encoding="utf-8").replace(
        'align = "right"', 'align = "middle"'
    )
    path = tmp_path / "bad.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(sns_label.LabelError) as caught:
        sns_label.load_config(path)
    assert caught.value.code == "config_invalid"


def test_old_config_without_align_still_loads_as_bottom_right(tmp_path: Path) -> None:
    lines = [
        line
        for line in sns_label.DEFAULT_CONFIG.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("align", "margin_ratio_tall", "tall_aspect_below"))
    ]
    path = tmp_path / "old.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    cfg = sns_label.load_config(path)
    assert (cfg.position, cfg.align, cfg.margin_ratio_tall) == ("bottom", "right", cfg.margin_ratio)


def _assert_bottom_right_inside(box: tuple[int, int, int, int], size: tuple[int, int]) -> None:
    width, height = size
    cfg = sns_label.load_config()
    geom = sns_label.label_box(cfg, width, height)
    left, top, right, bottom = box
    tol = 3  # JPEG 번짐
    assert 0 < left and right < width and 0 < top and bottom < height  # 잘리지 않는다
    assert right >= width * 0.5 and bottom >= height * 0.5  # 오른쪽 하단
    assert abs((width - right) - geom["margin_x"]) <= tol  # 오른쪽 여백
    assert abs((height - bottom) - geom["margin_y"]) <= tol  # 아래 여백
    assert left >= geom["margin_x"] - tol  # 왼쪽 여백 안쪽(줄바꿈이 폭을 넘지 않는다)
    assert top > height * 0.5  # 위쪽 절반을 침범하지 않는다


@needs_ffmpeg
@pytest.mark.parametrize("name", SIZES)
def test_image_label_is_inside_bottom_right_for_every_ratio(tmp_path: Path, name: str) -> None:
    width, height = SIZES[name]
    src = tmp_path / "src.png"
    _ffmpeg("-f", "lavfi", "-i", f"color=c=0x808080:s={width}x{height}", "-frames:v", "1", str(src))
    result = sns_label.apply_label(src, tmp_path / "out", generator_model="m", media_kind="image")
    _assert_bottom_right_inside(_bbox(result.labeled_path, width, height), (width, height))


@needs_ffmpeg
def test_video_label_is_inside_bottom_right_on_9x16(tmp_path: Path) -> None:
    width, height = SIZES["9x16"]
    src = tmp_path / "src.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=0x808080:s={width}x{height}:r=30:d=3",
        "-pix_fmt", "yuv420p", str(src),
    )  # fmt: skip
    result = sns_label.apply_label(src, tmp_path / "out", generator_model="m", media_kind="video")
    frame = tmp_path / "frame.png"
    _ffmpeg("-ss", "1", "-i", str(result.labeled_path), "-frames:v", "1", str(frame))
    _assert_bottom_right_inside(_bbox(frame, width, height), (width, height))


@needs_ffmpeg
def test_small_image_label_text_is_not_clipped(tmp_path: Path) -> None:
    """너비 320(규격 하한)에서도 줄바꿈으로 폭 안에 들어온다."""
    width, height = 320, 400
    src = tmp_path / "src.png"
    _ffmpeg("-f", "lavfi", "-i", f"color=c=0x808080:s={width}x{height}", "-frames:v", "1", str(src))
    result = sns_label.apply_label(src, tmp_path / "out", generator_model="m", media_kind="image")
    left, top, right, bottom = _bbox(result.labeled_path, width, height)
    assert left > 0 and top > 0 and right < width and bottom < height
