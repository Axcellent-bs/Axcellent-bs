"""SVG 콘티 → PNG (순수 함수, 표준 라이브러리 zlib·struct만, 2026-11-25).

키프레임 그림(`keyframes.py`)의 구도 참조로 쓴다. 기획이 그린 장면 첫·끝 화면 SVG(`VideoScene.
start_svg`·`end_svg`, viewBox 0 0 90 160)를 흰 배경 PNG(9:16이면 720×1280)로 바꾼다. 그림 모델이
"첫 참조의 구도를 그대로 따르라"는 문장과 같이 받는다. Pillow·cairosvg 같은 패키지는 쓰지 않는다.

- 도형: rect·circle·ellipse·line·polyline·polygon·path. g는 묶음(칠·선·두께를 물려준다).
  text·tspan은 그리지 않는다(화면 표시용).
- 칠하기: 스캔라인 다각형(짝홀 규칙).
  선: 두께 있는 선분(사각형) + 끝·꺾이는 점은 둥글게(round cap).
- 그라데이션 fill·stroke(`url(#id)`)은 stop 색 평균으로 칠한다(구도만 쓰므로).
  defs 안은 그리지 않는다.
  원·타원은 32각형, path의 C·Q는 16분할 직선. path 명령은 M·L·H·V·Z·C·Q(상대 좌표 포함)만
  쓰고, 모르는 명령을 만나면 그 path는 거기까지만 그린다.
- transform은 translate·scale만(다른 것은 무시). viewBox는 비율을 지키며 가운데에 맞춘다.
- 색: #rgb·#rrggbb·rgb()·rgba()·none·이름 몇 개. 모르는 색은 회색.
  opacity가 아주 낮으면(0.15 미만) 그리지 않는다(섞지 않고 불투명으로 칠한다).
- 상한: 입력 64KB, 요소 400개, 점 20,000개, 그리는 일의 양(스캔라인 × 변) 2,500만.
  넘거나 SVG가 아니면 `RasterError`.
"""

from __future__ import annotations

import math
import re
import struct
import xml.etree.ElementTree as ET  # noqa: S405 - DOCTYPE·ENTITY가 든 입력은 파싱 전에 버린다
import zlib
from collections.abc import Iterator
from typing import Final

MAX_INPUT_CHARS: Final = 64 * 1024
MAX_ELEMENTS: Final = 400
MAX_POINTS: Final = 20_000
MAX_WORK: Final = 25_000_000
MAX_SIDE: Final = 2048
CURVE_STEPS: Final = 16
CIRCLE_STEPS: Final = 32
MIN_OPACITY: Final = 0.15
GRAY: Final = (128, 128, 128)
WHITE: Final = (255, 255, 255)
SIZES: Final = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720), "3:4": (720, 960)}
COLORS: Final = {
    "black": (0, 0, 0),
    "white": WHITE,
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "pink": (255, 192, 203),
    "brown": (165, 42, 42),
    "gray": GRAY,
    "grey": GRAY,
    "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211),
    "darkgray": (169, 169, 169),
    "darkgrey": (169, 169, 169),
    "navy": (0, 0, 128),
    "skyblue": (135, 206, 235),
    "beige": (245, 245, 220),
}
_FORBIDDEN_DOC = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_PATH_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_TRANSFORM = re.compile(r"(translate|scale|[a-zA-Z]+)\s*\(([^)]*)\)")

Point = tuple[float, float]
Color = tuple[int, int, int]


class RasterError(ValueError):
    """그릴 수 없는 입력(SVG가 아님·상한 넘음)."""


def size_for(aspect: str | None) -> tuple[int, int]:
    """비율 → (가로, 세로) 픽셀. 모르는 비율이면 9:16."""
    return SIZES.get(str(aspect or ""), SIZES["9:16"])


# ---------------------------------------------------------------- 값 읽기
def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2] if tag.startswith("{") else tag


def _num(raw: str | None, default: float | None = None) -> float | None:
    if raw is None:
        return default
    m = _NUMBER.match(raw.strip())
    if not m:
        return default
    value = float(m.group())
    return value if math.isfinite(value) else default


_RGBA = re.compile(r"rgba?\(\s*([^)]*)\)")


def color(raw: str | None, gradients: dict[str, Color] | None = None) -> Color | None:
    """색 글 → RGB. none·transparent는 None, 모르는 색은 회색.

    rgb()·rgba()는 섞지 않고 불투명으로(알파가 아주 낮으면 None). 그라데이션 `url(#id)`은 stop 색
    평균(구도만 쓰므로)."""
    text = (raw or "").strip().lower()
    if text in ("none", "transparent"):
        return None
    if text.startswith("url("):
        ref = re.fullmatch(r"url\(\s*#([^)\s]+)\s*\)", text)
        return (gradients or {}).get(ref.group(1), GRAY) if ref else GRAY
    if m := _RGBA.fullmatch(text):
        nums = [n.strip() for n in m.group(1).replace("/", ",").split(",") if n.strip()]
        try:
            vals = [float(n[:-1]) * 2.55 if n.endswith("%") else float(n) for n in nums[:3]]
            alpha = float(nums[3]) if len(nums) > 3 else 1.0
        except ValueError:
            return GRAY
        if len(vals) < 3:
            return GRAY
        if alpha < MIN_OPACITY:
            return None
        r, g, b = (max(0, min(255, round(v))) for v in vals)
        return (r, g, b)
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        return (int(text[1] * 2, 16), int(text[2] * 2, 16), int(text[3] * 2, 16))
    if re.fullmatch(r"#[0-9a-f]{6}", text):
        return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
    return COLORS.get(text, GRAY)


class _Xf:
    """translate·scale만 있는 변환: (x, y) → (x·sx + tx, y·sy + ty)."""

    __slots__ = ("sx", "sy", "tx", "ty")

    def __init__(self, sx: float = 1.0, sy: float = 1.0, tx: float = 0.0, ty: float = 0.0):
        self.sx, self.sy, self.tx, self.ty = sx, sy, tx, ty

    def then(self, inner: _Xf) -> _Xf:
        """먼저 `inner`, 그다음 self."""
        return _Xf(
            self.sx * inner.sx,
            self.sy * inner.sy,
            self.sx * inner.tx + self.tx,
            self.sy * inner.ty + self.ty,
        )

    def apply(self, p: Point) -> Point:
        return (p[0] * self.sx + self.tx, p[1] * self.sy + self.ty)

    @property
    def scale(self) -> float:
        return (abs(self.sx) + abs(self.sy)) / 2


def _transform(raw: str | None) -> _Xf:
    out = _Xf()
    for name, args in _TRANSFORM.findall(raw or ""):
        nums = [float(n) for n in _NUMBER.findall(args)]
        if name == "translate" and nums:
            step = _Xf(tx=nums[0], ty=nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale" and nums:
            step = _Xf(sx=nums[0], sy=nums[1] if len(nums) > 1 else nums[0])
        else:
            continue  # rotate·skew·matrix는 무시
        out = out.then(step)
    return out


# ---------------------------------------------------------------- 도형 → 점 목록
def _ellipse(cx: float, cy: float, rx: float, ry: float) -> list[Point]:
    return [
        (cx + rx * math.cos(2 * math.pi * k / CIRCLE_STEPS),
         cy + ry * math.sin(2 * math.pi * k / CIRCLE_STEPS))
        for k in range(CIRCLE_STEPS)
    ]  # fmt: skip


def _points(raw: str | None) -> list[Point]:
    nums = [float(n) for n in _NUMBER.findall(raw or "")]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def _path(d: str | None) -> list[tuple[list[Point], bool]]:
    """path d → [(점들, 닫힘)]. 모르는 명령을 만나면 거기까지."""
    tokens = _PATH_TOKEN.findall(d or "")
    out: list[tuple[list[Point], bool]] = []
    cur: list[Point] = []
    x = y = sx = sy = 0.0
    i, cmd = 0, ""

    def take(n: int) -> list[float] | None:
        nonlocal i
        if i + n > len(tokens) or any(t.isalpha() for t in tokens[i : i + n]):
            return None
        vals = [float(t) for t in tokens[i : i + n]]
        i += n
        return vals

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    out.append((cur, True))
                cur, x, y = [], sx, sy
                continue
        if cmd not in "MmLlHhVvCcQq" or not cmd:
            break
        rel = cmd.islower()
        c = cmd.upper()
        n = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4}[c]
        vals = take(n)
        if vals is None:
            break
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            if cur:
                out.append((cur, False))
            x, y = vals[0] + ox, vals[1] + oy
            sx, sy = x, y
            cur = [(x, y)]
            cmd = "l" if rel else "L"  # M 뒤의 숫자 쌍은 L
        elif c == "L":
            x, y = vals[0] + ox, vals[1] + oy
            cur.append((x, y))
        elif c == "H":
            x = vals[0] + (x if rel else 0.0)
            cur.append((x, y))
        elif c == "V":
            y = vals[0] + (y if rel else 0.0)
            cur.append((x, y))
        else:
            ctrl = [(vals[k] + ox, vals[k + 1] + oy) for k in range(0, n, 2)]
            p0 = (x, y)
            if not cur:
                cur = [p0]
            for s in range(1, CURVE_STEPS + 1):
                t = s / CURVE_STEPS
                u = 1 - t
                if c == "C":
                    (a, b), (e, f), (g, h) = ctrl
                    px = u**3 * p0[0] + 3 * u * u * t * a + 3 * u * t * t * e + t**3 * g
                    py = u**3 * p0[1] + 3 * u * u * t * b + 3 * u * t * t * f + t**3 * h
                else:
                    (a, b), (g, h) = ctrl
                    px = u * u * p0[0] + 2 * u * t * a + t * t * g
                    py = u * u * p0[1] + 2 * u * t * b + t * t * h
                cur.append((px, py))
            x, y = ctrl[-1]
    if cur:
        out.append((cur, False))
    return out


def _shape(el: ET.Element, name: str) -> list[tuple[list[Point], bool]] | None:
    """요소 → [(점들, 닫힘)]. 값이 모자라면 None(그리지 않음)."""
    a = el.attrib
    if name == "rect":
        w, h = _num(a.get("width"), 0.0) or 0.0, _num(a.get("height"), 0.0) or 0.0
        if w <= 0 or h <= 0:
            return None
        x, y = _num(a.get("x"), 0.0) or 0.0, _num(a.get("y"), 0.0) or 0.0
        return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
    if name in ("circle", "ellipse"):
        cx, cy = _num(a.get("cx"), 0.0) or 0.0, _num(a.get("cy"), 0.0) or 0.0
        if name == "circle":
            rx = ry = _num(a.get("r"), 0.0) or 0.0
        else:
            rx, ry = _num(a.get("rx"), 0.0) or 0.0, _num(a.get("ry"), 0.0) or 0.0
        if rx <= 0 or ry <= 0:
            return None
        return [(_ellipse(cx, cy, rx, ry), True)]
    if name == "line":
        p = [_num(a.get(k), 0.0) or 0.0 for k in ("x1", "y1", "x2", "y2")]
        return [([(p[0], p[1]), (p[2], p[3])], False)]
    if name in ("polyline", "polygon"):
        pts = _points(a.get("points"))
        return [(pts, name == "polygon")] if len(pts) >= 2 else None
    if name == "path":
        return _path(a.get("d")) or None
    return None


# ---------------------------------------------------------------- 칠하기
class _Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.w, self.h = width, height
        self.buf = bytearray(bytes(WHITE) * (width * height))
        self.work = 0

    def fill(self, rings: list[list[Point]], rgb: Color) -> None:
        """여러 고리를 한 번에 짝홀 규칙으로 칠한다(픽셀 좌표)."""
        edges: list[tuple[float, float, float, float]] = []
        for ring in rings:
            for k in range(len(ring)):
                (x0, y0), (x1, y1) = ring[k], ring[(k + 1) % len(ring)]
                if y0 != y1:
                    edges.append((x0, y0, x1, y1) if y0 < y1 else (x1, y1, x0, y0))
        if not edges:
            return
        top = max(0, math.ceil(min(e[1] for e in edges) - 0.5))
        bottom = min(self.h - 1, math.floor(max(e[3] for e in edges) - 0.5))
        if bottom < top:
            return
        edges.sort(key=lambda e: e[1])
        active: list[tuple[float, float, float, float]] = []
        nxt = 0
        color_px = bytes(rgb)
        for row in range(top, bottom + 1):
            yc = row + 0.5
            while nxt < len(edges) and edges[nxt][1] <= yc:
                active.append(edges[nxt])
                nxt += 1
            active = [e for e in active if e[3] > yc]
            self.work += len(active) + 1
            if self.work > MAX_WORK:
                raise RasterError("too_complex")
            xs = sorted(e[0] + (yc - e[1]) * (e[2] - e[0]) / (e[3] - e[1]) for e in active)
            base = row * self.w * 3
            for k in range(0, len(xs) - 1, 2):
                c0 = max(0, math.ceil(xs[k] - 0.5))
                c1 = min(self.w, math.ceil(xs[k + 1] - 0.5))
                if c1 > c0:
                    self.buf[base + c0 * 3 : base + c1 * 3] = color_px * (c1 - c0)

    def stroke(self, pts: list[Point], closed: bool, width: float, rgb: Color) -> None:
        half = max(width, 1.0) / 2
        seq = [*pts, pts[0]] if closed and len(pts) > 2 else pts
        for (x0, y0), (x1, y1) in zip(seq, seq[1:], strict=False):
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length == 0:
                continue
            nx, ny = -dy / length * half, dx / length * half
            self.fill([[(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
                        (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]], rgb)  # fmt: skip
        if half < 1.0:
            return
        for x, y in seq:  # 꺾이는 점·끝점은 둥글게(round cap·join, 다각형 근사)
            self.fill([_ellipse(x, y, half, half)], rgb)


def _walk(
    el: ET.Element, xf: _Xf, style: dict[str, str], depth: int = 0
) -> Iterator[tuple[ET.Element, str, _Xf, dict[str, str]]]:
    if depth > 16:
        raise RasterError("too_deep")
    for child in el:
        name = _local(child.tag)
        if name in ("text", "tspan", "", "defs", "linearGradient", "radialGradient", "stop"):
            continue
        inherited = dict(style)
        for key in ("fill", "stroke", "stroke-width", "opacity"):
            if key in child.attrib:
                inherited[key] = child.attrib[key]
        here = xf.then(_transform(child.attrib.get("transform")))
        if name == "g":
            yield from _walk(child, here, inherited, depth + 1)
        else:
            yield child, name, here, inherited


def _gradients(root: ET.Element) -> dict[str, Color]:
    """linearGradient·radialGradient id → stop 색 평균. stop이 없으면 회색."""
    out: dict[str, Color] = {}
    for el in root.iter():
        if _local(el.tag) not in ("linearGradient", "radialGradient") or not el.get("id"):
            continue
        stops = []
        for stop in el:
            if _local(stop.tag) != "stop":
                continue
            raw = stop.get("stop-color")
            style = stop.get("style") or ""
            if raw is None and (m := re.search(r"stop-color\s*:\s*([^;]+)", style)):
                raw = m.group(1)
            rgb = color(raw or "black")
            if rgb is not None:
                stops.append(rgb)
        if not stops:
            out[str(el.get("id"))] = GRAY
            continue
        n = len(stops)
        out[str(el.get("id"))] = (
            round(sum(c[0] for c in stops) / n),
            round(sum(c[1] for c in stops) / n),
            round(sum(c[2] for c in stops) / n),
        )
    return out


def _png(width: int, height: int, rgb: bytes) -> bytes:
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[r * stride : (r + 1) * stride] for r in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def render(svg: str, width: int = 720, height: int = 1280) -> tuple[int, int, bytes]:
    """SVG → (가로, 세로, RGB 바이트). 픽셀을 시험하려고 PNG 전 단계를 따로 둔다."""
    if not isinstance(svg, str) or not svg.strip():
        raise RasterError("empty")
    if len(svg) > MAX_INPUT_CHARS:
        raise RasterError("too_large")
    if _FORBIDDEN_DOC.search(svg):
        raise RasterError("doctype")
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise RasterError("bad_size")
    try:
        root = ET.fromstring(svg)  # noqa: S314 - DOCTYPE·ENTITY는 위에서 버렸다
    except ET.ParseError as error:
        raise RasterError("parse_error") from error
    if _local(root.tag) != "svg":
        raise RasterError("not_svg")
    box = [float(n) for n in _NUMBER.findall(root.attrib.get("viewBox", ""))]
    if len(box) == 4 and box[2] > 0 and box[3] > 0:
        vx, vy, vw, vh = box
    else:
        vx, vy = 0.0, 0.0
        vw = _num(root.attrib.get("width"), float(width)) or float(width)
        vh = _num(root.attrib.get("height"), float(height)) or float(height)
    s = min(width / vw, height / vh)  # 비율을 지키고 가운데에
    view = _Xf(s, s, (width - vw * s) / 2 - vx * s, (height - vh * s) / 2 - vy * s)
    view = view.then(_transform(root.attrib.get("transform")))
    canvas = _Canvas(width, height)
    grads = _gradients(root)
    base = {k: root.attrib[k] for k in ("fill", "stroke", "stroke-width", "opacity")
            if k in root.attrib}  # fmt: skip
    count = points = 0
    for el, name, xf, style in _walk(root, view, base):
        count += 1
        if count > MAX_ELEMENTS:
            raise RasterError("too_many_elements")
        if (_num(style.get("opacity"), 1.0) or 0.0) < MIN_OPACITY:
            continue
        shapes = _shape(el, name)
        if not shapes:
            continue
        points += sum(len(p) for p, _ in shapes)
        if points > MAX_POINTS:
            raise RasterError("too_many_points")
        px = [([xf.apply(p) for p in pts], closed) for pts, closed in shapes]
        fill = color(style.get("fill", "black"), grads)
        if fill is not None and name != "line":
            canvas.fill([pts for pts, _ in px if len(pts) >= 3], fill)
        stroke = color(style.get("stroke", "none"), grads)
        if stroke is not None:
            width_px = (_num(style.get("stroke-width"), 1.0) or 1.0) * xf.scale
            for pts, closed in px:
                canvas.stroke(pts, closed or name in ("rect", "circle", "ellipse", "polygon"),
                              width_px, stroke)  # fmt: skip
    return width, height, bytes(canvas.buf)


def to_png(svg: str, width: int = 720, height: int = 1280) -> bytes:
    """SVG → 흰 배경 PNG 바이트. 그릴 수 없으면 `RasterError`."""
    w, h, rgb = render(svg, width, height)
    return _png(w, h, rgb)


__all__ = ["RasterError", "color", "render", "size_for", "to_png"]
