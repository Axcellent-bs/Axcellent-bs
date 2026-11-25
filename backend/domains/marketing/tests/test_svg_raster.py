"""SVG 콘티 → PNG (`media/svg_raster.py`, 표준 라이브러리만). 픽셀·곡선·상한·잘못된 입력."""

from __future__ import annotations

import struct
import zlib

import pytest

from marketing_agent.media import svg_raster as sr

NS = 'xmlns="http://www.w3.org/2000/svg"'


def _svg(body: str, box: str = "0 0 90 160") -> str:
    return f'<svg {NS} viewBox="{box}">{body}</svg>'


def _px(img: tuple[int, int, bytes], x: int, y: int) -> tuple[int, int, int]:
    w, _, rgb = img
    i = (y * w + x) * 3
    return (rgb[i], rgb[i + 1], rgb[i + 2])


def test_rect_fills_its_box_on_white() -> None:
    img = sr.render(_svg('<rect x="0" y="0" width="45" height="80" fill="#f00"/>'))
    assert img[:2] == (720, 1280)
    assert _px(img, 10, 10) == (255, 0, 0)
    assert _px(img, 359, 639) == (255, 0, 0)  # 45×8 = 360, 80×8 = 640 바로 안쪽
    assert _px(img, 361, 10) == (255, 255, 255)
    assert _px(img, 10, 641) == (255, 255, 255)


def test_circle_ellipse_and_stroke_line() -> None:
    body = (
        '<circle cx="45" cy="40" r="10" fill="blue"/>'
        '<ellipse cx="45" cy="120" rx="30" ry="5" fill="#00ff00"/>'
        '<line x1="0" y1="80" x2="90" y2="80" stroke="black" stroke-width="2"/>'
    )
    img = sr.render(_svg(body))
    assert _px(img, 360, 320) == (0, 0, 255)  # 원 가운데
    assert _px(img, 360 + 70, 320) == (0, 0, 255)  # 반지름 80px 안
    assert _px(img, 360 + 90, 320) == (255, 255, 255)  # 밖
    assert _px(img, 360 + 230, 960) == (0, 255, 0)  # 타원은 가로로 길다
    assert _px(img, 360, 960 + 50) == (255, 255, 255)
    assert _px(img, 500, 640) == (0, 0, 0)  # 두께 2 → 16px 선
    assert _px(img, 500, 660) == (255, 255, 255)


def test_path_relative_commands_and_curves() -> None:
    # 상대 m·h·v·z로 사각형, Q 곡선은 직선 16분할로 근사
    body = (
        '<path d="m10 10 h20 v20 h-20 z" fill="#123456"/>'
        '<path d="M10 100 Q45 60 80 100 Z" fill="#abcdef"/>'
    )
    img = sr.render(_svg(body))
    assert _px(img, 160, 160) == (0x12, 0x34, 0x56)
    assert _px(img, 300, 160) == (255, 255, 255)
    # 곡선 아래(y=90 근처, 가운데)는 칠해지고 곡선 위(y=75)는 비어 있다
    assert _px(img, 360, 90 * 8) == (0xAB, 0xCD, 0xEF)
    assert _px(img, 360, 75 * 8) == (255, 255, 255)


def test_cubic_curve_and_unknown_command_stops_the_path() -> None:
    curve = sr._path("M0 0 C 10 0, 20 10, 20 20")  # noqa: SLF001
    pts = curve[0][0]
    assert len(pts) == 1 + sr.CURVE_STEPS and pts[-1] == pytest.approx((20, 20))
    partial = sr._path("M0 0 L 10 0 A 5 5 0 0 1 20 0 L 30 0")  # noqa: SLF001 - A는 모른다
    assert partial == [([(0.0, 0.0), (10.0, 0.0)], False)]


def test_transform_translate_scale_and_group_inherit() -> None:
    body = (
        '<g fill="#00f" transform="translate(45 0)">'
        '<rect x="0" y="0" width="10" height="10" transform="scale(2)"/></g>'
        '<text x="0" y="150">글자는 그리지 않는다</text>'
    )
    img = sr.render(_svg(body))
    assert _px(img, 45 * 8 + 5, 5) == (0, 0, 255)
    assert _px(img, (45 + 19) * 8, 19 * 8) == (0, 0, 255)  # 2배라 20까지
    assert _px(img, 45 * 8 - 5, 5) == (255, 255, 255)
    assert _px(img, 4, 150 * 8 - 4) == (255, 255, 255)  # text 없음


def test_colors_none_unknown_and_low_opacity() -> None:
    assert sr.color("#abc") == (0xAA, 0xBB, 0xCC)
    assert sr.color("none") is None and sr.color("transparent") is None
    assert sr.color("chartreuse-ish") == sr.GRAY
    img = sr.render(_svg('<rect width="90" height="160" fill="red" opacity="0.05"/>'))
    assert _px(img, 100, 100) == (255, 255, 255)


def test_gradient_fill_is_the_average_of_its_stops() -> None:
    body = (
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#ff0000"/>'
        '<stop offset="1" style="stop-color:#0000ff"/></linearGradient></defs>'
        '<rect width="90" height="80" fill="url(#g)"/>'
        '<rect y="80" width="90" height="80" fill="url(#missing)"/>'
    )
    img = sr.render(_svg(body))
    assert _px(img, 100, 100) == (128, 0, 128)  # 빨강·파랑 평균
    assert _px(img, 100, 1000) == sr.GRAY  # 없는 그라데이션은 회색
    assert sr.color("rgba(10, 20, 30, 0.5)") == (10, 20, 30)
    assert sr.color("rgb(100%, 0%, 0%)") == (255, 0, 0)
    assert sr.color("rgba(0,0,0,0.01)") is None


def test_stroke_ends_are_round() -> None:
    img = sr.render(_svg('<line x1="20" y1="80" x2="70" y2="80" stroke="black" stroke-width="4"/>'))
    # 두께 4 → 32px. 끝점(160, 640) 왼쪽 12px은 둥근 끝 안, 모서리 쪽(왼쪽 12·위 14)은 밖
    assert _px(img, 148, 640) == (0, 0, 0)
    assert _px(img, 148, 626) == (255, 255, 255)


def test_png_is_a_valid_white_background_file() -> None:
    png = sr.to_png(_svg('<rect width="10" height="10" fill="black"/>'), 90, 160)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    length = struct.unpack(">I", png[8:12])[0]
    w, h, depth, ctype = struct.unpack(">IIBB", png[16:26])
    assert (length, w, h, depth, ctype) == (13, 90, 160, 8, 2)
    idat = png.index(b"IDAT")
    size = struct.unpack(">I", png[idat - 4 : idat])[0]
    raw = zlib.decompress(png[idat + 4 : idat + 4 + size])
    assert len(raw) == 160 * (1 + 90 * 3)
    assert raw[1:4] == b"\x00\x00\x00" and raw[-3:] == b"\xff\xff\xff"


def test_size_for_aspect() -> None:
    assert sr.size_for("9:16") == (720, 1280)
    assert sr.size_for("16:9") == (1280, 720)
    assert sr.size_for("??") == (720, 1280)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not xml",
        f"<div {NS}/>",
        '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>',
        _svg("<rect/>" * 401),
        "<svg>" + " " * (sr.MAX_INPUT_CHARS + 1) + "</svg>",
    ],
)
def test_bad_input_is_an_error(bad: str) -> None:
    with pytest.raises(sr.RasterError):
        sr.render(bad)


def test_limits_on_points_and_work() -> None:
    many = " ".join(f"{i % 90},{i % 160}" for i in range(sr.MAX_POINTS + 1))
    with pytest.raises(sr.RasterError):
        sr.render(_svg(f'<polygon points="{many}"/>'))
    with pytest.raises(sr.RasterError):
        sr.render(_svg("<rect/>"), 4000, 10)
