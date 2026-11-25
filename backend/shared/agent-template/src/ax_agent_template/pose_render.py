"""3D 마네킹 콘티 렌더러 (11/25, 표준 라이브러리만 + 계약 KeyPose).

LLM은 포즈 숫자(`KeyPose`)만 내고, 그림은 이 코드가 그린다. 어떤 모델이 포즈를 내도 같은
마네킹·같은 바닥·같은 빛이라 칸마다 인물 모양이 달라지지 않는다. 화면 시안(3D 마네킹 콘티)의
JS(skeleton·cam·project·draw)를 그대로 옮겼다.

콘티를 만드는 길이 기획(제작안)과 마케팅(의뢰 스토리보드·스토리보드 다시 만들기) 둘이라 공용으로
둔다(11/25, 도메인끼리 import하지 않음. 처음엔 `planning_agent.pose_render`였고 출력은 같다).
LLM 답 파싱(`PoseLLM` → `key_pose`)·포즈 숫자 지시문(`POSE_FIELDS`)도 같이 쓴다.

- 관절 뼈대 → 몸 방향(yaw) 회전 → 샷 크기별 카메라 원근 투영
- 배경 그라데이션·바닥 격자(0.5m)·그림자 타원·소품 → 깊이순으로 팔다리(굵은 둥근 선 + 하이라이트),
  관절·머리(방사형 그라데이션 구), 머리카락 반원, 카메라 쪽을 보면 눈
- viewBox 180×320(9:16, 다른 비율이면 맞춤), 숫자 소수 1자리, 위쪽 label_ko 한 줄만 글자
- 순수 함수: 같은 포즈·비율이면 같은 SVG. 칸당 16KB 이하(넘으면 격자를 1m로)
- 시안에 없던 소품(phone·cup·shelf)은 같은 화풍(단색 + 테두리)으로 더했다
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any, Final
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field

from ax_contracts.schemas.planning import KeyPose

Vec = tuple[float, float, float]
Attrs = dict[str, float | int | str]
MAX_BYTES: Final = 16384
SVG_NS: Final = "http://www.w3.org/2000/svg"

# 샷 크기별 카메라: 위치·바라보는 점·초점 거리 (시안 cam()).
# 11/26 인물이 칸 높이의 55~70%가 되게 높이·초점을 맞췄다(예전 값은 45~54%로 칸 아래쪽에 작았다:
# close (1.6, 1.55, 520) · medium (1.3, 1.0, 470) · full (1.3, 0.85, 560))
CAMERAS: Final = {
    "close": ((0.0, 1.5, -1.6), (0.0, 1.5, 0.0), 560.0),
    "medium": ((0.0, 1.2, -2.6), (0.0, 1.0, 0.0), 620.0),
    "full": ((0.0, 1.1, -3.6), (0.0, 0.85, 0.0), 780.0),
}
FIGURE_RATIO: Final = (0.55, 0.70)  # 칸 높이에서 보이는 인물 비율 (시험이 본다)
SIZES: Final = {"9:16": (180, 320), "1:1": (180, 180), "16:9": (320, 180)}

PANTS, BODY, SLEEVE, SKIN_LINE, SHOE = "#3a5590", "#4f7fe0", "#5b8ae6", "#f0c69c", "#222"
GRID, FLOOR, LABEL, EYE = "#e6dccd", "#fdfbf7", "#6b6256", "#333"
HIGHLIGHT, SHADOW = "rgba(255,255,255,.25)", "rgba(60,50,40,.25)"
BG_TOP, BG_BOTTOM = "#f3efe7", "#fbf8f2"

SHOTS: Final = ("close", "medium", "full")
PROPS: Final = ("book", "desk", "phone", "shelf", "cup")
# 11/26 책방 밖 소품 (계약 KeyPose.extra_props, 칸당 최대 2개). 단순 도형만, 글자 없음
EXTRA_PROPS: Final = ("mic", "laptop", "bag", "spotlight", "screen", "sign_blank")
MAX_EXTRA_PROPS: Final = 2
# 장면·칸 글에 이 낱말이 있으면 LLM 답에 없어도 그 소품을 넣는다 (영문은 소문자로 비교)
PROP_WORDS: Final = (
    ("마이크", "mic"), ("microphone", "mic"), ("mic", "mic"),
    ("노트북", "laptop"), ("laptop", "laptop"),
    ("가방", "bag"), ("bag", "bag"),
    ("스포트라이트", "spotlight"), ("조명", "spotlight"), ("spotlight", "spotlight"),
    ("스크린", "screen"), ("전광판", "screen"), ("screen", "screen"),
    ("팻말", "sign_blank"), ("피켓", "sign_blank"), ("sign", "sign_blank"),
)  # fmt: skip
_TIME_HEAD = re.compile(r"^\s*\d+(?:\.\d+)?\s*초\s*·?\s*")

# 포즈 숫자 지시문 (기획·마케팅 같은 문장). 앞에 "무엇을 보고 shot을 고르나" 한 줄을 붙여 쓴다
POSE_FIELDS = """  - x: 좌우 위치 -1.5~1.5 (m, + = 화면 오른쪽), z: 카메라에서 먼 정도 0~3 (m)
  - yaw: 몸 방향 -180~180 (0 = 카메라를 봄, 90 = 화면 왼쪽을 봄, -90 = 화면 오른쪽, 180 = 뒷모습)
  - head: {yaw: -80~80 (몸 기준 좌우 시선), pitch: -40~40 (+ = 아래를 봄)}
  - arm_l·arm_r: {raise: -30~170 (0 = 내림, 90 = 앞으로 뻗음, 170 = 머리 위), bend: 0~150 (팔꿈치)}
  - step: 걸음 폭 0~0.6 (0 = 서 있음, 0.4 넘으면 크게 걷거나 뜀)
  - prop: 없으면 null. "book"(왼손 책)·"desk"(앞 책상)·"phone"(오른손 휴대폰)·"shelf"(뒤 책장)·
    "cup"(오른손 컵) 중 하나
  - extra_props: 그 밖의 소품 0~2개 목록. "mic"(손 마이크)·"laptop"(앞 노트북)·"bag"(손 가방)·
    "spotlight"(뒤 무대 조명)·"screen"(뒤 큰 화면)·"sign_blank"(손 빈 팻말) 중에서만
  - label_ko: 칸 위 한 줄, 동작만 짧게 (한국어 12자 안팎, 예: "놀라 돌아봄"). 시각은 코드가
    붙이니 쓰지 않는다. 자막·회사 이름·행동 문구도 쓰지 않는다
"""


# ---------------------------------------------------------------- LLM 답 파싱


class ArmLLM(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    raise_: float = Field(default=0.0, alias="raise")
    bend: float = 0.0


class HeadLLM(BaseModel):
    model_config = ConfigDict(extra="ignore")

    yaw: float = 0.0
    pitch: float = 0.0


class PoseLLM(BaseModel):
    """LLM이 내는 포즈 숫자. 모르는 shot·prop은 기본값으로 바꾸고 범위는 계약 KeyPose가 자른다."""

    model_config = ConfigDict(extra="ignore")

    shot: str = "medium"
    x: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    head: HeadLLM = Field(default_factory=HeadLLM)
    arm_l: ArmLLM = Field(default_factory=ArmLLM)
    arm_r: ArmLLM = Field(default_factory=ArmLLM)
    step: float = 0.0
    prop: str | None = None
    extra_props: list[str] = Field(default_factory=list)
    label_ko: str = ""


def secs_label(at_s: float) -> str:
    """칸 시각 "4초" (소수는 한 자리)."""
    r = round(float(at_s), 1)
    return f"{int(r)}초" if r == int(r) else f"{r:.1f}초"


def time_label(label: str, at_s: float | None) -> str:
    """칸 위 글: "N초 · 동작". LLM이 앞에 적은 시각("0초 · ")은 지우고 코드 시각을 붙인다
    (11/26 LLM이 지시문 예시 "0초"를 베껴 모든 칸이 0초였다). 시각을 모르면 받은 글 그대로."""
    if at_s is None:
        return " ".join((label or "").split())
    text = _TIME_HEAD.sub("", " ".join((label or "").split()))
    return f"{secs_label(at_s)} · {text}" if text else secs_label(at_s)


def props_from_text(text: str) -> list[str]:
    """장면·칸 글의 낱말로 고른 소품 id (표 순서, 중복 없음)."""
    low = (text or "").lower()
    out: list[str] = []
    for word, pid in PROP_WORDS:
        if pid not in out and (word in low if not word.isascii() else re.search(
            rf"\b{re.escape(word)}\b", low)):  # fmt: skip
            out.append(pid)
    return out


def extra_props_of(got: Any, text: str = "") -> list[str]:
    """목록 안의 LLM 소품 + 글 낱말로 보충한 소품 (최대 2개). 목록 밖 id는 버린다."""
    raw = getattr(got, "extra_props", None) or []
    out = [p for p in dict.fromkeys(str(x) for x in raw) if p in EXTRA_PROPS]
    for pid in props_from_text(text):
        if pid not in out:
            out.append(pid)
    return out[:MAX_EXTRA_PROPS]


def key_pose(
    got: PoseLLM, label: str, at_s: float | None = None, text: str = ""
) -> KeyPose | None:
    """LLM 포즈 → 계약 KeyPose (범위는 자른다). 모르는 shot은 medium, 모르는 prop은 없음.

    11/26 `at_s`(칸 시각)를 주면 라벨 시각은 코드가 붙인다. `text`(장면·칸 글)에 마이크 같은
    낱말이 있으면 그 소품을 넣는다."""
    try:
        return KeyPose.model_validate({
            "shot": got.shot if got.shot in SHOTS else "medium",
            "x": got.x, "z": got.z, "yaw": got.yaw,
            "head": {"yaw": got.head.yaw, "pitch": got.head.pitch},
            "arm_l": {"raise": got.arm_l.raise_, "bend": got.arm_l.bend},
            "arm_r": {"raise": got.arm_r.raise_, "bend": got.arm_r.bend},
            "step": got.step, "prop": got.prop if got.prop in PROPS else None,
            "extra_props": extra_props_of(got, text),
            "label_ko": time_label(got.label_ko.strip() or label, at_s),
        })  # fmt: skip
    except ValueError:
        return None


# ---------------------------------------------------------------- 그리기


def _n(v: float) -> str:
    """소수 1자리. 끝 .0은 뺀다 (-0도 0으로)."""
    r = round(v, 1)
    if r == 0:
        return "0"
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def _tag(name: str, attrs: Attrs, body: str | None = None) -> str:
    """요소 하나. 숫자는 소수 1자리로, 글은 그대로(코드가 정한 값만 넣는다)."""
    text = " ".join(f'{k}="{_n(v) if isinstance(v, int | float) else v}"' for k, v in attrs.items())
    return f"<{name} {text}/>" if body is None else f"<{name} {text}>{body}</{name}>"


def _rad(d: float) -> float:
    return d * math.pi / 180


def _rot_y(p: Vec, a: float) -> Vec:
    c, s = math.cos(a), math.sin(a)
    return (p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c)


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


class _Pt:
    __slots__ = ("s", "x", "y", "z")

    def __init__(self, x: float, y: float, z: float, s: float) -> None:
        self.x, self.y, self.z, self.s = x, y, z, s


class _Camera:
    def __init__(self, shot: str, w: int, h: int) -> None:
        self.pos, look, f = CAMERAS[shot]
        self.f = f * min(w / 180, h / 320)  # 다른 비율이면 인물이 칸에 들어오게 줄인다
        self.w, self.h = w, h
        pitch = math.atan2(look[1] - self.pos[1], look[2] - self.pos[2])
        self.cy, self.sy = math.cos(-pitch), math.sin(-pitch)

    def project(self, p: Vec) -> _Pt:
        dx, dy, dz = p[0] - self.pos[0], p[1] - self.pos[1], p[2] - self.pos[2]
        y2 = dy * self.cy - dz * self.sy
        z2 = max(dy * self.sy + dz * self.cy, 0.05)
        k = self.f / z2 * 0.5
        return _Pt(self.w / 2 + dx * k, self.h * 0.5 - y2 * k, z2, k)


def skeleton(pose: KeyPose) -> dict[str, Vec]:
    """관절 3D 좌표(m). 바닥 y=0, 카메라는 -z 쪽에서 +z를 본다 (시안 skeleton())."""
    yaw = _rad(pose.yaw)
    base: Vec = (pose.x, 0.0, pose.z)

    def put(v: Vec) -> Vec:
        return _add(_rot_y(v, yaw), base)

    j: dict[str, Vec] = {
        "pelvis": put((0, 0.95, 0)), "chest": put((0, 1.3, 0)), "neck": put((0, 1.48, 0)),
    }  # fmt: skip
    hy, hp = _rad(pose.head.yaw), _rad(pose.head.pitch)
    tilt = _rot_y((0, -math.sin(hp) * 0.04, math.sin(hp) * 0.04), yaw + hy)
    j["head"] = _add(put((0, 1.62, 0)), tilt)
    j["nose"] = _add(j["head"], _rot_y((0, -0.01, -0.11), yaw + hy))
    for side, sign in (("l", -1), ("r", 1)):
        arm = pose.arm_l if side == "l" else pose.arm_r
        sh: Vec = (0.2 * sign, 1.42, 0)
        r, b = _rad(arm.raise_), _rad(arm.bend)
        el: Vec = (sh[0] + math.sin(r) * 0.08 * sign, sh[1] - math.cos(r) * 0.28,
                   -math.sin(r) * 0.18)  # fmt: skip
        hand: Vec = (el[0], el[1] - math.cos(r + b) * 0.26, el[2] - math.sin(r + b) * 0.24)
        j[f"sh_{side}"], j[f"el_{side}"], j[f"hd_{side}"] = put(sh), put(el), put(hand)
        st = pose.step * (1 if side == "l" else -1)
        j[f"hip_{side}"] = put((0.11 * sign, 0.92, 0))
        j[f"kn_{side}"] = put((0.11 * sign, 0.5, -st * 0.6))
        j[f"ft_{side}"] = put((0.11 * sign, 0.05, -st))
    return j


def render(pose: KeyPose, aspect: str = "9:16") -> str:
    """포즈 한 칸 → SVG 원문. 16KB를 넘으면 바닥 격자를 1m로 줄여 다시 그린다."""
    out = _Frame(pose, aspect, 0.5).svg()
    if len(out.encode("utf-8")) > MAX_BYTES:
        out = _Frame(pose, aspect, 1.0).svg()
    return out


def _stops(c1: str, c2: str) -> str:
    return (_tag("stop", {"offset": "0%", "stop-color": c1})
            + _tag("stop", {"offset": "100%", "stop-color": c2}))  # fmt: skip


def _bg_gradient() -> str:
    return _tag("linearGradient", {"id": "bg", "x1": 0, "y1": 0, "x2": 0, "y2": 1},
                _stops(BG_TOP, BG_BOTTOM))  # fmt: skip


def backdrop(aspect: str = "9:16") -> str:
    """인물·글자 없이 배경 그라데이션만 (엔드카드 칸. 글자는 영상에 코드가 얹는다)."""
    w, h = SIZES.get(aspect, SIZES["9:16"])
    rect = _tag("rect", {"x": 0, "y": 0, "width": w, "height": h, "fill": "url(#bg)"})
    return f'<svg xmlns="{SVG_NS}" viewBox="0 0 {w} {h}"><defs>{_bg_gradient()}</defs>{rect}</svg>'


def _line(a: _Pt, b: _Pt, **attrs: float | str) -> Attrs:
    return {"x1": a.x, "y1": a.y, "x2": b.x, "y2": b.y, **attrs}


class _Frame:
    """칸 하나 (시안 draw()). 바닥·그림자는 바로 그리고, 인물·소품은 깊이순으로 그린다."""

    def __init__(self, pose: KeyPose, aspect: str, grid: float) -> None:
        self.pose, self.grid = pose, grid
        self.w, self.h = SIZES.get(aspect, SIZES["9:16"])
        self.c = _Camera(pose.shot, self.w, self.h)
        self.j = skeleton(pose)
        self.parts: list[str] = []
        self.items: list[tuple[float, Callable[[], None]]] = []

    def put(self, z: float, text: str) -> None:
        self.items.append((z, lambda: self.parts.append(text)))

    def at(self, v: Vec) -> _Pt:
        return self.c.project(_add(v, (self.pose.x, 0.0, self.pose.z)))

    def svg(self) -> str:
        self.background()
        self.props_behind()
        self.extras_behind()
        self.figure()
        self.props_in_hand()
        self.extras_in_hand()
        self.items.sort(key=lambda it: -it[0])  # 먼 것부터 (안정 정렬, 시안과 같은 순서)
        for _, draw in self.items:
            draw()
        if self.pose.label_ko:
            label = {"x": 8, "y": 16, "font-size": 9, "fill": LABEL}
            self.parts.append(_tag("text", label, escape(self.pose.label_ko)))
        return f'<svg xmlns="{SVG_NS}" viewBox="0 0 {self.w} {self.h}">{"".join(self.parts)}</svg>'

    def background(self) -> None:
        add, c, w, h = self.parts.append, self.c, self.w, self.h
        bg = _bg_gradient()
        balls = "".join(
            _tag("radialGradient", {"id": gid, "cx": "35%", "cy": "30%", "r": "75%"}, _stops(a, b))
            for gid, a, b in (("sk", "#ffe6c7", "#d9a77a"), ("st", "#8fb6ff", "#3f6fd1"),
                              ("pt", "#5f7bb0", "#2f4677"), ("hr", "#6b4a3a", "#2b1d16"))
        )  # fmt: skip
        add(f"<defs>{bg}{balls}</defs>")
        add(_tag("rect", {"x": 0, "y": 0, "width": w, "height": h, "fill": "url(#bg)"}))
        hz = c.project((0, 0, 30)).y
        add(_tag("rect", {"x": 0, "y": hz, "width": w, "height": h - hz, "fill": FLOOR}))
        lines = [
            _tag("line", _line(c.project((-6 + i * self.grid, 0, -1)),
                               c.project((-6 + i * self.grid, 0, 14))))
            for i in range(round(12 / self.grid) + 1)
        ] + [
            _tag("line", _line(c.project((-6, 0, -1 + i * self.grid)),
                               c.project((6, 0, -1 + i * self.grid))))
            for i in range(round(15 / self.grid) + 1)
        ]  # fmt: skip
        add(_tag("g", {"stroke": GRID, "stroke-width": 0.6}, "".join(lines)))
        sh = c.project((self.pose.x + 0.25, 0, self.pose.z + 0.1))
        add(_tag("ellipse", {"cx": sh.x, "cy": sh.y, "rx": 0.35 * sh.s, "ry": 0.08 * sh.s,
                             "fill": SHADOW}))  # fmt: skip

    def box(self, corners: list[Vec], fill: str, stroke: str, z_bias: float) -> None:
        pts = [self.at(v) for v in corners]
        poly = " ".join(f"{_n(q.x)},{_n(q.y)}" for q in pts)
        attrs: Attrs = {"points": poly, "fill": fill, "stroke": stroke, "stroke-width": 0.8}
        self.put(pts[0].z + z_bias, _tag("polygon", attrs))

    def props_behind(self) -> None:
        if self.pose.prop == "desk":
            self.box([(-0.1, 0.75, -0.45), (0.9, 0.75, -0.45), (0.9, 0.75, 0.1),
                      (-0.1, 0.75, 0.1)], "#c9a27a", "#8a6a4a", -0.01)  # fmt: skip
        if self.pose.prop == "shelf":  # 인물 뒤 오른쪽 책장 (앞면 + 선반 3칸)
            self.box([(0.6, 0, 0.8), (1.3, 0, 0.8), (1.3, 1.9, 0.8), (0.6, 1.9, 0.8)],
                     "#b98d63", "#7a5a3c", 0.0)  # fmt: skip
            front = self.at((0.6, 0, 0.8)).z  # 선반 선은 책장 판 바로 앞에 (판에 가리지 않게)
            for k in (0.5, 1.0, 1.5):
                a, b = self.at((0.6, k, 0.8)), self.at((1.3, k, 0.8))
                attrs = _line(a, b, stroke="#7a5a3c", **{"stroke-width": 0.8})
                self.put(front - 0.001, _tag("line", attrs))

    def extras_behind(self) -> None:
        """11/26 인물 뒤·바닥 소품: 무대 조명(spotlight)·큰 화면(screen)·앞 노트북(laptop)."""
        extra = self.pose.extra_props
        if "screen" in extra:  # 인물 뒤 벽의 빈 화면 (테두리 + 밝은 면, 글자 없음)
            self.box([(-1.1, 0.9, 1.2), (1.1, 0.9, 1.2), (1.1, 2.2, 1.2), (-1.1, 2.2, 1.2)],
                     "#2b2f45", "#151827", 0.02)  # fmt: skip
            self.box([(-1.0, 1.0, 1.19), (1.0, 1.0, 1.19), (1.0, 2.1, 1.19), (-1.0, 2.1, 1.19)],
                     "#5b6fd6", "#3a49a0", 0.01)  # fmt: skip
        if "spotlight" in extra:  # 위에서 비추는 조명 원뿔 (반투명) + 조명 머리
            top, left, right = self.at((-0.7, 2.6, 0.9)), self.at((-0.2, 0, 0.3)), self.at(
                (0.6, 0, 0.3))  # fmt: skip
            cone = f"{_n(top.x)},{_n(top.y)} {_n(left.x)},{_n(left.y)} {_n(right.x)},{_n(right.y)}"
            head = {"cx": top.x, "cy": top.y, "r": 0.08 * top.s, "fill": "#3a3a46"}
            light = {"points": cone, "fill": "rgba(255,236,170,.35)"}
            self.put(top.z + 0.5, _tag("polygon", light) + _tag("circle", head))
        if "laptop" in extra:  # 인물 앞 허리 높이 노트북 (바닥 판 + 화면 판)
            self.box([(-0.18, 0.8, -0.45), (0.18, 0.8, -0.45), (0.18, 0.8, -0.2),
                      (-0.18, 0.8, -0.2)], "#9aa3b2", "#5c6370", -0.05)  # fmt: skip
            self.box([(-0.18, 0.8, -0.2), (0.18, 0.8, -0.2), (0.18, 1.02, -0.18),
                      (-0.18, 1.02, -0.18)], "#2b2f36", "#111", -0.06)  # fmt: skip

    def extras_in_hand(self) -> None:
        """11/26 손 소품: 마이크(mic)·가방(bag)·빈 팻말(sign_blank). 손이 차 있으면 다른 손."""
        extra, prop = self.pose.extra_props, self.pose.prop
        right_busy, left_busy = prop in ("phone", "cup"), prop == "book"
        if "mic" in extra:
            hand = "hd_l" if right_busy else "hd_r"
            p = self.c.project(self.j[hand])
            body = self.held(hand, (0.03, 0.02, 0.12), "#2b2f36", "#111")
            ball = {"cx": p.x, "cy": p.y - 0.05 * p.s, "r": 0.035 * p.s, "fill": "#8a8f99",
                    "stroke": "#444", "stroke-width": 0.6}  # fmt: skip
            self.put(p.z - 0.1, body + _tag("circle", ball))
            right_busy, left_busy = right_busy or hand == "hd_r", left_busy or hand == "hd_l"
        if "bag" in extra:
            hand = "hd_r" if left_busy and not right_busy else "hd_l"
            p = self.c.project(self.j[hand])
            self.put(p.z - 0.1, self.held(hand, (0.2, -0.02, 0.16), "#b0703c", "#6e4423"))
            right_busy, left_busy = right_busy or hand == "hd_r", left_busy or hand == "hd_l"
        if "sign_blank" in extra:
            hand = "hd_r" if left_busy and not right_busy else "hd_l"
            p = self.c.project(self.j[hand])
            board = self.held(hand, (0.3, 0.32, 0.22), "#fdfbf7", "#8a7a66")
            stick = _line(p, _Pt(p.x, p.y - 0.1 * p.s, p.z, p.s), stroke="#8a6a4a",
                          **{"stroke-width": 0.02 * p.s})  # fmt: skip
            self.put(p.z - 0.1, _tag("line", stick) + board)

    def limb(self, a: str, b: str, width: float, color: str) -> None:
        pa, pb = self.c.project(self.j[a]), self.c.project(self.j[b])
        s = pa.s + pb.s
        main = _line(pa, pb, stroke=color, **{"stroke-width": width * s / 2,
                                              "stroke-linecap": "round"})  # fmt: skip
        shine: Attrs = {"x1": pa.x - 0.6, "y1": pa.y - 0.6, "x2": pb.x - 0.6, "y2": pb.y - 0.6,
                        "stroke": HIGHLIGHT, "stroke-width": width * s / 6,
                        "stroke-linecap": "round"}  # fmt: skip
        self.put((pa.z + pb.z) / 2, _tag("line", main) + _tag("line", shine))

    def ball(self, name: str, r: float, fill: str) -> None:
        p = self.c.project(self.j[name])
        circle = _tag("circle", {"cx": p.x, "cy": p.y, "r": r * p.s, "fill": fill})
        self.put(p.z + (0.08 if name == "pelvis" else -0.001), circle)

    def figure(self) -> None:
        skin, shirt, pants = "url(#sk)", "url(#st)", "url(#pt)"
        for side in ("l", "r"):
            self.limb(f"hip_{side}", f"kn_{side}", 0.15, PANTS)
            self.limb(f"kn_{side}", f"ft_{side}", 0.12, PANTS)
        self.ball("ft_l", 0.07, SHOE)
        self.ball("ft_r", 0.07, SHOE)
        self.limb("pelvis", "chest", 0.36, BODY)
        self.ball("chest", 0.2, shirt)
        self.ball("pelvis", 0.15, pants)
        for side in ("l", "r"):
            self.limb(f"sh_{side}", f"el_{side}", 0.11, SLEEVE)
            self.limb(f"el_{side}", f"hd_{side}", 0.09, SKIN_LINE)
        self.ball("sh_l", 0.1, shirt)
        self.ball("sh_r", 0.1, shirt)
        self.ball("hd_l", 0.04, skin)
        self.ball("hd_r", 0.04, skin)
        self.limb("neck", "chest", 0.09, SKIN_LINE)
        self.head()

    def head(self) -> None:
        hd, nose = self.c.project(self.j["head"]), self.c.project(self.j["nose"])
        r = 0.11 * hd.s
        out = _tag("circle", {"cx": hd.x, "cy": hd.y, "r": r, "fill": "url(#sk)"})
        arc = f"M{_n(hd.x - r)} {_n(hd.y - 0.01 * hd.s)} a{_n(r)} {_n(r)} 0 0 1 {_n(2 * r)} 0 z"
        out += _tag("path", {"d": arc, "fill": "url(#hr)"})
        fx = nose.x - hd.x
        if abs(fx) < 0.1 * hd.s and nose.z < hd.z:  # 카메라 쪽을 볼 때만 눈 (뒤통수엔 안 그림)
            for dx in (-0.035, 0.035):
                eye = {"cx": hd.x + fx * 0.6 + dx * hd.s, "cy": hd.y + 0.02 * hd.s,
                       "r": 0.012 * hd.s, "fill": EYE}  # fmt: skip
                out += _tag("circle", eye)
        self.put(hd.z - 0.05, out)

    def held(self, hand: str, size: tuple[float, float, float], fill: str, stroke: str) -> str:
        """손에 든 네모 소품. size = (너비, 위로 올림, 높이) (m 비율)."""
        p = self.c.project(self.j[hand])
        w, up, h = size
        attrs: Attrs = {"x": p.x - w / 2 * p.s, "y": p.y - up * p.s, "width": w * p.s,
                        "height": h * p.s, "rx": 1, "fill": fill, "stroke": stroke,
                        "stroke-width": 0.6}  # fmt: skip
        return _tag("rect", attrs)

    def props_in_hand(self) -> None:
        prop = self.pose.prop
        if prop == "book":
            z = self.c.project(self.j["hd_l"]).z - 0.1
            self.put(z, self.held("hd_l", (0.18, 0.13, 0.13), "#e2552f", "#9a3014"))
        if prop in ("phone", "cup"):
            p = self.c.project(self.j["hd_r"])
            if prop == "phone":
                text = self.held("hd_r", (0.05, 0.1, 0.09), "#2b2f36", "#111")
            else:
                top = {"cx": p.x, "cy": p.y - 0.08 * p.s, "rx": 0.03 * p.s, "ry": 0.008 * p.s,
                       "fill": "#6b4a3a"}  # fmt: skip
                text = self.held("hd_r", (0.06, 0.08, 0.07), "#f4f1ea", "#8a7a66")
                text += _tag("ellipse", top)
            self.put(p.z - 0.1, text)
