"""3D 마네킹 콘티 렌더러 (11/25, 표준 라이브러리만 + 계약 KeyPose).

LLM은 포즈 숫자(`KeyPose`)만 내고, 그림은 이 코드가 그린다. 어떤 모델이 포즈를 내도 같은
마네킹·같은 바닥·같은 빛이라 칸마다 인물 모양이 달라지지 않는다. 화면 시안(3D 마네킹 콘티)의
JS(skeleton·cam·project·draw)를 옮겼다.

콘티를 만드는 길이 기획(제작안)과 마케팅(의뢰 스토리보드·스토리보드 다시 만들기) 둘이라 공용으로
둔다(11/25, 도메인끼리 import하지 않음. 처음엔 `planning_agent.pose_render`였다).
LLM 답 파싱(`PoseLLM` → `key_pose`)·포즈 숫자 지시문(`POSE_FIELDS`)도 같이 쓴다.

- 관절 뼈대 → 몸 자세(posture: 섬·앉음·무릎·누움) → 몸 방향(yaw) 회전 → 샷 크기·앵글별 카메라
  원근 투영
- 11/26 3D 그레이박스(CR-cb113dca245a: 배경이 바닥 격자뿐이었다): 장면(`Scene`)의 장소 낱말로
  방(바닥·뒷벽·옆벽 원근)이나 바깥(하늘·땅·건물)을 놓고, 가구는 직육면체(면 3개, 면마다 밝기
  다르게)로 같은 투영·깊이순으로 그린다. 장소를 모르면(장면 없음) 예전 바닥 격자(0.5m)
- 마네킹은 한 색 입체: 팔다리 캡슐(테두리 + 몸 색 + 한쪽 밝은 줄), 몸통·골반 둥근 상자, 머리
  구(방사형 그라데이션) + 보는 쪽 코 점, 카메라 쪽이면 눈, 바닥 그림자 타원
- viewBox 180×320(9:16, 다른 비율이면 맞춤), 숫자 소수 1자리, 위쪽 label_ko 한 줄만 글자
- 순수 함수: 같은 포즈·장면·비율이면 같은 SVG. 칸당 16KB 이하(넘으면 가구부터 빼고, 그래도
  넘으면 격자를 1m로)
- 엔드카드 칸(`end_card`)은 사람 없이 어두운 판 + "엔드카드" 글만
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
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
# 11/26 camera_en의 앵글 → (카메라 높이 더함 m, 바라보는 점 높이 더함 m, 카메라 거리 배율).
# eye(눈높이)는 위 CAMERAS 그대로
ANGLES: Final = {
    "eye": (0.0, 0.0, 1.0),
    "low": (-0.85, 0.25, 1.0),
    "high": (0.7, 0.0, 1.0),
    "overhead": (2.2, -0.35, 0.55),
}
ANGLE_WORDS: Final = (
    ("overhead", ("overhead", "top-down", "top down", "bird's-eye", "birds-eye", "bird's eye",
                  "bird eye", "aerial")),
    ("low", ("low angle", "low-angle", "from below", "worm's-eye", "worm's eye", "upward")),
    ("high", ("high angle", "high-angle", "from above", "slightly high", "looking down")),
)  # fmt: skip
FIGURE_RATIO: Final = (0.55, 0.70)  # 칸 높이에서 보이는 인물 비율 (시험이 본다)
SIZES: Final = {"9:16": (180, 320), "1:1": (180, 180), "16:9": (320, 180)}

# 마네킹 한 색 (11/26 옅은 회색·베이지 배경과 구분되는 밝은 상아색, 테두리로 윤곽)
MANNEQUIN, MANNEQUIN_LINE, SHOE = "#f2e8d6", "#9a8770", "#6f655a"
GRID, FLOOR, LABEL, EYE, NOSE = "#e6dccd", "#fdfbf7", "#4a4238", "#333", "#9a8770"
HIGHLIGHT, SHADOW = "rgba(255,255,255,.6)", "rgba(60,50,40,.25)"
LABEL_BG = "rgba(255,255,255,.85)"
BG_TOP, BG_BOTTOM = "#f3efe7", "#fbf8f2"
END_BG, END_INK = "#232340", "#ffffff"
END_CARD_TEXT: Final = "엔드카드"

SHOTS: Final = ("close", "medium", "full")
PROPS: Final = ("book", "desk", "phone", "shelf", "cup")
POSTURES: Final = ("stand", "sit", "kneel", "lie")
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
# 11/26 몸 자세 낱말 (LLM 지시·규칙 포즈 같은 표, 앞에 있는 것이 먼저). 영문은 낱말 앞부분으로
POSTURE_WORDS: Final = (
    ("무릎 꿇", "kneel"), ("무릎을 꿇", "kneel"), ("kneel", "kneel"),
    ("눕", "lie"), ("누워", "lie"), ("누운", "lie"), ("lying", "lie"), ("lies down", "lie"),
    ("lie down", "lie"),
    ("앉", "sit"), ("소파", "sit"), ("의자", "sit"), ("sofa", "sit"), ("couch", "sit"),
    ("chair", "sit"), ("sit", "sit"), ("seated", "sit"),
)  # fmt: skip
_TIME_HEAD = re.compile(r"^\s*\d+(?:\.\d+)?\s*초\s*·?\s*")

# 포즈 숫자 지시문 (기획·마케팅 같은 문장). 앞에 "무엇을 보고 shot을 고르나" 한 줄을 붙여 쓴다
POSE_FIELDS = """  - x: 좌우 위치 -1.5~1.5 (m, + = 화면 오른쪽), z: 카메라에서 먼 정도 0~3 (m)
  - yaw: 몸 방향 -180~180 (0 = 카메라를 봄, 90 = 화면 왼쪽을 봄, -90 = 화면 오른쪽, 180 = 뒷모습)
  - head: {yaw: -80~80 (몸 기준 좌우 시선), pitch: -40~40 (+ = 아래를 봄)}
  - arm_l·arm_r: {raise: -30~170 (0 = 내림, 90 = 앞으로 뻗음, 170 = 머리 위), bend: 0~150 (팔꿈치)}
  - step: 걸음 폭 0~0.6 (0 = 서 있음, 0.4 넘으면 크게 걷거나 뜀)
  - posture: 몸 자세 "stand"(섬)·"sit"(앉음)·"kneel"(무릎 꿇음)·"lie"(누움) 중 하나. 장면 글에
    앉·소파·의자·sit이 있으면 "sit", 무릎 꿇·kneel이면 "kneel", 눕·누워·lie면 "lie", 아니면 "stand"
  - prop: 없으면 null. "book"(왼손 책)·"desk"(앞 책상)·"phone"(오른손 휴대폰)·"shelf"(뒤 책장)·
    "cup"(오른손 컵) 중 하나
  - extra_props: 그 밖의 소품 0~2개 목록. "mic"(손 마이크)·"laptop"(앞 노트북)·"bag"(손 가방)·
    "spotlight"(뒤 무대 조명)·"screen"(뒤 큰 화면)·"sign_blank"(손 빈 팻말) 중에서만
  - label_ko: 칸 위 한 줄, 동작만 짧게 (한국어 12자 안팎, 예: "놀라 돌아봄"). 시각은 코드가
    붙이니 쓰지 않는다. 자막·회사 이름·행동 문구도 쓰지 않는다
"""


# ---------------------------------------------------------------- 장면 (11/26)


@dataclass(frozen=True)
class Scene:
    """칸의 장면: 장소(place_ko)·장면 글(visual·첫/끝 화면)·camera_en. 장소 낱말로 방·가구를,
    camera_en의 앵글로 카메라 높이를 정한다. 없으면(None) 예전 바닥 격자·눈높이."""

    place: str = ""
    text: str = ""
    camera: str = ""


PLACES: Final = ("airport", "cafe", "office", "living", "snow", "street")
# 장소 묶음 낱말 (place_ko를 먼저, 없으면 장면 글. 표 순서대로 먼저 걸린 것)
PLACE_WORDS: Final = (
    ("airport", ("공항", "대합실", "터미널", "기차역", "지하철역", "플랫폼", "탑승", "airport",
                 "terminal", "station", "platform", "gate")),
    ("cafe", ("카페", "커피숍", "찻집", "cafe", "café", "coffee shop")),
    ("office", ("사무실", "회의실", "오피스", "office", "meeting room")),
    ("living", ("거실", "소파", "침실", "안방", "자취방", "집 안", "집안", "집에", "우리 집",
                "living room", "sofa", "couch", "bedroom", " home")),
    ("snow", ("눈 덮", "눈이 내", "눈 내리", "눈 오는", "눈밭", "설원", "설경", "겨울 거리",
              "겨울 바깥", "겨울 길", "snow")),
    ("street", ("거리", "골목", "바깥", "야외", "도로", "공원", "광장", "길 위", "street",
                "outside", "outdoor", "road", "alley", "park")),
)  # fmt: skip


def place_of(scene: Scene | None) -> str:
    """장소 묶음 id (PLACES 중 하나). 모르면 "" (예전 바닥 격자)."""
    if scene is None:
        return ""
    for source in (scene.place, scene.text):
        low = f" {' '.join((source or '').lower().split())} "
        if not low.strip():
            continue
        for kind, words in PLACE_WORDS:
            if any(w in low for w in words):
                return kind
    return ""


def angle_of(camera: str) -> str:
    """camera_en의 앵글 → ANGLES 키 (모르면 eye)."""
    low = " ".join((camera or "").lower().split())
    for kind, words in ANGLE_WORDS:
        if any(w in low for w in words):
            return kind
    return "eye"


def posture_from_text(text: str) -> str:
    """장면 글 낱말로 고른 몸 자세 (POSTURE_WORDS 순서). 없으면 stand."""
    low = (text or "").lower()
    for word, posture in POSTURE_WORDS:
        if word.isascii():
            if re.search(rf"\b{re.escape(word)}", low):
                return posture
        elif word in low:
            return posture
    return "stand"


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
    """LLM이 내는 포즈 숫자. 모르는 shot·prop은 기본값으로 바꾸고 범위는 계약 KeyPose가 자른다.
    posture를 안 적었거나 모르는 값이면 장면 글 낱말로 정한다(`posture_from_text`)."""

    model_config = ConfigDict(extra="ignore")

    shot: str = "medium"
    x: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    head: HeadLLM = Field(default_factory=HeadLLM)
    arm_l: ArmLLM = Field(default_factory=ArmLLM)
    arm_r: ArmLLM = Field(default_factory=ArmLLM)
    step: float = 0.0
    posture: str | None = None
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
    낱말이 있으면 그 소품을 넣는다. posture가 없거나 모르는 값이면 `text` 낱말로(소파 → sit)."""
    posture = got.posture if got.posture in POSTURES else posture_from_text(text)
    try:
        return KeyPose.model_validate({
            "shot": got.shot if got.shot in SHOTS else "medium",
            "x": got.x, "z": got.z, "yaw": got.yaw,
            "head": {"yaw": got.head.yaw, "pitch": got.head.pitch},
            "arm_l": {"raise": got.arm_l.raise_, "bend": got.arm_l.bend},
            "arm_r": {"raise": got.arm_r.raise_, "bend": got.arm_r.bend},
            "step": got.step, "prop": got.prop if got.prop in PROPS else None,
            "extra_props": extra_props_of(got, text), "posture": posture,
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


def shade(color: str, k: float) -> str:
    """#rrggbb 밝기 배율 (면마다 밝기 다르게)."""
    rgb = [min(255, max(0, round(int(color[i:i + 2], 16) * k))) for i in (1, 3, 5)]
    return "#" + "".join(f"{v:02x}" for v in rgb)


class _Pt:
    __slots__ = ("s", "x", "y", "z")

    def __init__(self, x: float, y: float, z: float, s: float) -> None:
        self.x, self.y, self.z, self.s = x, y, z, s


class _Camera:
    def __init__(self, shot: str, w: int, h: int, angle: str = "eye") -> None:
        pos, look, f = CAMERAS[shot]
        dy, dl, k = ANGLES.get(angle, ANGLES["eye"])
        self.pos: Vec = (pos[0], max(pos[1] + dy, 0.25), pos[2] * k)
        look = (look[0], look[1] + dl, look[2])
        self.f = f * min(w / 180, h / 320)  # 다른 비율이면 인물이 칸에 들어오게 줄인다
        self.w, self.h = w, h
        # 11/26 바라보는 점이 칸 가운데 오게 기울인다 (예전엔 cos·sin(-pitch)라 기울기가 두 배였다.
        # 눈높이에선 몇 도라 티가 안 났지만 고·저앵글이면 바닥·천장만 보였다)
        pitch = math.atan2(look[1] - self.pos[1], look[2] - self.pos[2])
        self.cy, self.sy = math.cos(pitch), math.sin(pitch)

    def project(self, p: Vec) -> _Pt:
        dx, dy, dz = p[0] - self.pos[0], p[1] - self.pos[1], p[2] - self.pos[2]
        y2 = dy * self.cy - dz * self.sy
        z2 = max(dy * self.sy + dz * self.cy, 0.05)
        k = self.f / z2 * 0.5
        return _Pt(self.w / 2 + dx * k, self.h * 0.5 - y2 * k, z2, k)


_LEGS: Final = frozenset(f"{j}_{s}" for j in ("hip", "kn", "ft") for s in ("l", "r"))


def _standing(pose: KeyPose) -> dict[str, Vec]:
    """선 자세 관절 (몸 기준 좌표, m. 바닥 y=0, 몸 앞 = -z)."""
    j: dict[str, Vec] = {
        "pelvis": (0, 0.95, 0), "chest": (0, 1.3, 0), "neck": (0, 1.48, 0), "nb": (0, 1.38, 0),
        "t_l": (-0.12, 1.36, 0), "t_r": (0.12, 1.36, 0), "w_l": (-0.07, 0.97, 0),
        "w_r": (0.07, 0.97, 0), "p_l": (-0.08, 0.84, 0), "p_r": (0.08, 0.84, 0),
    }  # fmt: skip
    hy, hp = _rad(pose.head.yaw), _rad(pose.head.pitch)
    j["head"] = _add((0, 1.62, 0), _rot_y((0, -math.sin(hp) * 0.04, math.sin(hp) * 0.04), hy))
    j["nose"] = _add(j["head"], _rot_y((0, -0.01, -0.11), hy))
    for side, sign in (("l", -1), ("r", 1)):
        arm = pose.arm_l if side == "l" else pose.arm_r
        sh: Vec = (0.2 * sign, 1.42, 0)
        r, b = _rad(arm.raise_), _rad(arm.bend)
        el: Vec = (sh[0] + math.sin(r) * 0.08 * sign, sh[1] - math.cos(r) * 0.28,
                   -math.sin(r) * 0.18)  # fmt: skip
        hand: Vec = (el[0], el[1] - math.cos(r + b) * 0.26, el[2] - math.sin(r + b) * 0.24)
        j[f"sh_{side}"], j[f"el_{side}"], j[f"hd_{side}"] = sh, el, hand
        st = pose.step * (1 if side == "l" else -1)
        j[f"hip_{side}"] = (0.11 * sign, 0.92, 0)
        j[f"kn_{side}"] = (0.11 * sign, 0.5, -st * 0.6)
        j[f"ft_{side}"] = (0.11 * sign, 0.05, -st)
    return j


# 자세별 윗몸 옮김(m)·다리 관절 (x는 좌우 부호를 곱한다). 11/26 소파 장면도 서 있었다
POSTURE_BODY: Final = {
    "sit": ((0.0, -0.45, 0.12), {"hip": (0.11, 0.47, 0.06), "kn": (0.11, 0.5, -0.36),
                                  "ft": (0.11, 0.05, -0.42)}),
    "kneel": ((0.0, -0.42, 0.0), {"hip": (0.11, 0.5, 0.0), "kn": (0.11, 0.06, -0.08),
                                   "ft": (0.11, 0.05, 0.38)}),
}  # fmt: skip


def _posed(pose: KeyPose, j: dict[str, Vec]) -> dict[str, Vec]:
    """몸 자세 적용 (몸 기준 좌표). sit·kneel은 엉덩이를 낮추고 다리를 접고, lie는 등을 대고 눕는다
    (머리 = +x, 몸 앞 = 위)."""
    if pose.posture == "lie":
        return {k: (v[1] - 0.95, 0.12 - v[2], v[0]) for k, v in j.items()}
    body = POSTURE_BODY.get(pose.posture)
    if body is None:
        return j
    shift, legs = body
    out = {k: _add(v, shift) for k, v in j.items() if k not in _LEGS}
    for side, sign in (("l", -1), ("r", 1)):
        for name, (x, y, z) in legs.items():
            out[f"{name}_{side}"] = (x * sign, y, z)
    return out


def skeleton(pose: KeyPose) -> dict[str, Vec]:
    """관절 3D 좌표(m). 바닥 y=0, 카메라는 -z 쪽에서 +z를 본다 (시안 skeleton())."""
    yaw = _rad(pose.yaw)
    base: Vec = (pose.x, 0.0, pose.z)
    return {k: _add(_rot_y(v, yaw), base) for k, v in _posed(pose, _standing(pose)).items()}


def render(
    pose: KeyPose, aspect: str = "9:16", scene: Scene | None = None, at_s: float | None = None
) -> str:
    """포즈 한 칸 → SVG 원문. 16KB를 넘으면 가구를 빼고, 그래도 넘으면 바닥 격자를 1m로.

    11/26 scene: 장면(장소·글·camera_en, 없으면 예전 격자·눈높이). at_s: 주면 라벨 시각을 그
    초로 바꾼다(칸 시각 = 장면 start_s, 끝 칸이면 end_s)."""
    if at_s is not None:
        pose = pose.model_copy(update={"label_ko": time_label(pose.label_ko, at_s)[:30]})
    out = ""
    for furniture, grid in ((True, 0.5), (False, 0.5), (False, 1.0)):
        out = _Frame(pose, aspect, grid, scene, furniture).svg()
        if len(out.encode("utf-8")) <= MAX_BYTES:
            break
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


def end_card(aspect: str = "9:16") -> str:
    """11/26 엔드카드 칸: 사람 없이 어두운 판 + "엔드카드" 글만 (채널 이름은 영상에 코드가
    얹는다)."""
    w, h = SIZES.get(aspect, SIZES["9:16"])
    rect = _tag("rect", {"x": 0, "y": 0, "width": w, "height": h, "fill": END_BG})
    text = _tag("text", {"x": w / 2, "y": h / 2 + 6, "font-size": 18, "font-weight": 700,
                         "text-anchor": "middle", "fill": END_INK}, END_CARD_TEXT)  # fmt: skip
    return f'<svg xmlns="{SVG_NS}" viewBox="0 0 {w} {h}">{rect}{text}</svg>'


def _line(a: _Pt, b: _Pt, **attrs: float | str) -> Attrs:
    return {"x1": a.x, "y1": a.y, "x2": b.x, "y2": b.y, **attrs}


def _points(pts: list[_Pt]) -> str:
    return " ".join(f"{_n(q.x)},{_n(q.y)}" for q in pts)


# ---------------------------------------------------------------- 3D 그레이박스 (11/26)

ROOM_W, ROOM_H, ROOM_BACK = 2.4, 2.7, 3.8  # 방 반폭·높이·뒷벽 거리 (m)
# 실내: (천장, 뒷벽, 옆벽, 바닥)
ROOM_COLORS: Final = {
    "living": ("#e9e5de", "#dcd7ce", "#cdc7bc", "#bfb4a3"),
    "cafe": ("#ece6dc", "#ddd3c4", "#cfc4b3", "#b8a58c"),
    "airport": ("#eceef0", "#dfe2e6", "#d0d4da", "#c4c7cb"),
    "office": ("#eceae6", "#dedbd5", "#d0ccc5", "#c2bdb4"),
}
# 바깥: (하늘 위, 하늘 아래, 땅, 길, 건물, 지붕)
OUTDOOR_COLORS: Final = {
    "street": ("#c9d3df", "#eef1f4", "#cfcbc3", "#b7b3ab", "#a9b0b9", "#c3c9d0"),
    "snow": ("#aab4c0", "#dfe4ea", "#f4f6f9", "#e3e8ee", "#a3acb8", "#fbfcfd"),
}
SOFA, TABLE, LAMP_POLE, LAMP_SHADE = "#a39c90", "#b59b7c", "#8a8680", "#f1d999"
WINDOW, CHAIR, SUITCASE, DESK, MONITOR, SEAT = (
    "#cfdbe6", "#9ea3aa", "#7d8696", "#b9a586", "#4a4d55", "#aaa49a",
)  # fmt: skip
TOP_K, FRONT_K, SIDE_K = 1.12, 1.0, 0.8  # 상자 면 밝기 (위·앞·옆)
# 직육면체: (x0, x1, y0, y1, z0, z1) m
Box = tuple[float, float, float, float, float, float]


def _seat(px: float, pz: float, half: float) -> list[tuple[Box, str]]:
    return [((px - half, px + half, 0, 0.42, pz - 0.08, pz + 0.5), SEAT)]


def furniture(kind: str, px: float, pz: float, posture: str) -> list[tuple[Box, str]]:
    """장소 묶음의 가구 상자 (인물 위치 px·pz 기준). 앉으면 소파·의자가 엉덩이 밑에 온다."""
    sit = posture == "sit"
    boxes: list[tuple[Box, str]] = []
    if kind == "living":
        sofa_z = pz if sit else 2.9
        sofa_x = px if sit else 0.0
        boxes += [((sofa_x - 0.75, sofa_x + 0.75, 0, 0.42, sofa_z - 0.08, sofa_z + 0.5), SOFA),
                  ((sofa_x - 0.75, sofa_x + 0.75, 0, 0.88, sofa_z + 0.42, sofa_z + 0.64), SOFA),
                  ((px + 0.95, px + 1.55, 0, 0.4, pz - 0.45, pz + 0.15), TABLE),
                  ((1.74, 1.8, 0, 1.5, 3.2, 3.26), LAMP_POLE),
                  ((1.57, 1.97, 1.5, 1.78, 3.03, 3.43), LAMP_SHADE)]  # fmt: skip
    elif kind == "cafe":
        boxes += [((px + 0.55, px + 1.15, 0, 0.74, pz - 0.3, pz + 0.3), TABLE),
                  ((px + 0.78, px + 0.88, 0.74, 0.84, pz - 0.05, pz + 0.05), "#f4f1ea")]
        if sit:
            boxes += [((px - 0.25, px + 0.25, 0, 0.44, pz - 0.08, pz + 0.42), CHAIR),
                      ((px - 0.25, px + 0.25, 0, 0.98, pz + 0.38, pz + 0.46), CHAIR)]
    elif kind == "airport":
        boxes += [((-1.9 + i * 0.55, -1.42 + i * 0.55, 0, 0.44, 2.4, 2.85), CHAIR)
                  for i in range(4)]  # fmt: skip
        boxes += [((-1.9, 0.23, 0, 0.88, 2.82, 2.92), CHAIR),
                  ((px + 0.38, px + 0.68, 0, 0.56, pz - 0.12, pz + 0.12), SUITCASE)]
        if sit:
            boxes += [((px - 0.25, px + 0.25, 0, 0.44, pz - 0.08, pz + 0.42), CHAIR)]
    elif kind == "office":
        boxes += [((px - 0.65, px + 0.65, 0, 0.74, pz + 0.55, pz + 1.15), DESK),
                  ((px - 0.26, px + 0.26, 0.74, 1.1, pz + 0.86, pz + 0.92), MONITOR)]
        if sit:
            boxes += [((px - 0.25, px + 0.25, 0, 0.44, pz - 0.08, pz + 0.42), CHAIR)]
    elif kind in OUTDOOR_COLORS:
        if sit:  # 바깥 벤치
            boxes += _seat(px, pz, 0.7)
    elif sit:  # 장소를 몰라도 앉으면 단순한 앉을 상자
        boxes += _seat(px, pz, 0.3)
    return boxes


# 바깥 건물 상자 (x0, x1, 높이, z0, z1)
BUILDINGS: Final = ((-3.6, -1.7, 3.6, 6.0, 8.5), (1.8, 3.8, 4.6, 7.0, 9.5),
                    (-1.2, 1.0, 6.5, 16.0, 18.0))  # fmt: skip


class _Frame:
    """칸 하나 (시안 draw()). 배경(격자·방·바깥)·그림자는 바로 그리고, 가구·인물·소품은 깊이순."""

    def __init__(self, pose: KeyPose, aspect: str, grid: float, scene: Scene | None = None,
                 with_furniture: bool = True) -> None:  # fmt: skip
        self.pose, self.grid = pose, grid
        self.w, self.h = SIZES.get(aspect, SIZES["9:16"])
        self.place = place_of(scene)
        self.with_furniture = with_furniture
        self.c = _Camera(pose.shot, self.w, self.h, angle_of(scene.camera) if scene else "eye")
        self.j = skeleton(pose)
        self.parts: list[str] = []
        self.items: list[tuple[float, Callable[[], None]]] = []
        self.surface: tuple[float, float, float] | None = None  # 탁자 윗면 (x, y, z) 가운데

    def put(self, z: float, text: str) -> None:
        self.items.append((z, lambda: self.parts.append(text)))

    def at(self, v: Vec) -> _Pt:
        return self.c.project(_add(v, (self.pose.x, 0.0, self.pose.z)))

    def svg(self) -> str:
        self.defs()
        if self.place in ROOM_COLORS:
            self.room()
        elif self.place in OUTDOOR_COLORS:
            self.outdoor()
        else:
            self.background()
        self.shadow()
        if self.place or self.pose.posture == "sit":
            self.boxes()
        self.props_behind()
        self.extras_behind()
        self.figure()
        self.props_in_hand()
        self.extras_in_hand()
        self.items.sort(key=lambda it: -it[0])  # 먼 것부터 (안정 정렬)
        for _, draw in self.items:
            draw()
        self.label()
        return f'<svg xmlns="{SVG_NS}" viewBox="0 0 {self.w} {self.h}">{"".join(self.parts)}</svg>'

    def label(self) -> None:
        text = self.pose.label_ko
        if not text:
            return
        width = sum(9.0 if ord(ch) > 0x2000 else 5.2 for ch in text) + 8
        self.parts.append(_tag("rect", {"x": 4, "y": 5, "width": min(width, self.w - 8),
                                        "height": 15, "rx": 3, "fill": LABEL_BG}))  # fmt: skip
        self.parts.append(_tag("text", {"x": 8, "y": 16, "font-size": 9, "fill": LABEL},
                               escape(text)))  # fmt: skip

    def defs(self) -> None:
        balls = "".join(
            _tag("radialGradient", {"id": gid, "cx": "35%", "cy": "30%", "r": "75%"}, _stops(a, b))
            for gid, a, b in (("sk", "#fffaf0", "#cdbd9f"), ("st", "#fbf4e6", "#d2c1a3"))
        )
        sky = ""
        if self.place in OUTDOOR_COLORS:
            top, bottom = OUTDOOR_COLORS[self.place][:2]
            sky = _tag("linearGradient", {"id": "sky", "x1": 0, "y1": 0, "x2": 0, "y2": 1},
                       _stops(top, bottom))  # fmt: skip
        self.parts.append(f"<defs>{_bg_gradient()}{sky}{balls}</defs>")

    def poly(self, corners: list[Vec], fill: str) -> str:
        """절대 좌표(m) 다각형 하나 (원근 투영)."""
        return _tag("polygon", {"points": _points([self.c.project(v) for v in corners]),
                                "fill": fill})  # fmt: skip

    def background(self) -> None:
        """예전 배경: 그라데이션 + 바닥 + 0.5m 격자 (장소를 모를 때)."""
        add, c, w, h = self.parts.append, self.c, self.w, self.h
        add(_tag("rect", {"x": 0, "y": 0, "width": w, "height": h, "fill": "url(#bg)"}))
        hz = c.project((0, 0, 30)).y
        add(_tag("rect", {"x": 0, "y": hz, "width": w, "height": max(h - hz, 0), "fill": FLOOR}))
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

    def room(self) -> None:
        """실내: 천장 색 판 + 바닥면 + 뒷벽 + 옆벽 둘 (원근)."""
        ceiling, back, side, floor = ROOM_COLORS[self.place]
        wd, ht, zb = ROOM_W, ROOM_H, ROOM_BACK
        zn = self.c.pos[2] + 0.3  # 카메라 바로 앞부터
        add = self.parts.append
        add(_tag("rect", {"x": 0, "y": 0, "width": self.w, "height": self.h, "fill": ceiling}))
        add(self.poly([(-wd, 0, zn), (wd, 0, zn), (wd, 0, zb), (-wd, 0, zb)], floor))
        add(self.poly([(-wd, 0, zb), (wd, 0, zb), (wd, ht, zb), (-wd, ht, zb)], back))
        add(self.poly([(-wd, 0, zn), (-wd, 0, zb), (-wd, ht, zb), (-wd, ht, zn)], side))
        add(self.poly([(wd, 0, zn), (wd, 0, zb), (wd, ht, zb), (wd, ht, zn)], side))
        if self.place == "cafe":  # 뒷벽 창 (창틀 십자)
            add(self.poly([(-1.5, 1.0, zb - 0.01), (0.5, 1.0, zb - 0.01), (0.5, 2.2, zb - 0.01),
                           (-1.5, 2.2, zb - 0.01)], WINDOW))  # fmt: skip
            self.window_bars([(-0.5, 1.0, 2.2)], 1.6, -1.5, 0.5, zb - 0.02)
        if self.place == "airport":  # 뒷벽 큰 창 (창살 3개)
            add(self.poly([(-2.2, 0.6, zb - 0.01), (2.2, 0.6, zb - 0.01), (2.2, 2.4, zb - 0.01),
                           (-2.2, 2.4, zb - 0.01)], WINDOW))  # fmt: skip
            self.window_bars([(x, 0.6, 2.4) for x in (-1.1, 0.0, 1.1)], None, -2.2, 2.2, zb - 0.02)

    def window_bars(self, bars: list[Vec], mid: float | None, x0: float, x1: float,
                    z: float) -> None:  # fmt: skip
        lines = [_tag("line", _line(self.c.project((x, y0, z)), self.c.project((x, y1, z))))
                 for x, y0, y1 in bars]  # fmt: skip
        if mid is not None:
            lines.append(_tag("line", _line(self.c.project((x0, mid, z)),
                                            self.c.project((x1, mid, z)))))  # fmt: skip
        self.parts.append(_tag("g", {"stroke": "#f7f9fb", "stroke-width": 1.6}, "".join(lines)))

    def outdoor(self) -> None:
        """바깥: 하늘 그라데이션 + 땅 + 길 + 건물 상자 (눈이면 흐린 하늘·눈 바닥·흰 지붕)."""
        _, _, ground, road, building, roof = OUTDOOR_COLORS[self.place]
        zn = self.c.pos[2] + 0.3
        add = self.parts.append
        add(_tag("rect", {"x": 0, "y": 0, "width": self.w, "height": self.h, "fill": "url(#sky)"}))
        add(self.poly([(-30, 0, zn), (30, 0, zn), (30, 0, 60), (-30, 0, 60)], ground))
        add(self.poly([(-1.3, 0, zn), (1.3, 0, zn), (1.3, 0, 60), (-1.3, 0, 60)], road))
        if not self.with_furniture:
            return
        for x0, x1, ht, z0, z1 in BUILDINGS:
            self.cuboid((x0, x1, 0, ht, z0, z1), building, top=roof)

    def cuboid(self, box: Box, color: str, top: str = "") -> None:
        """직육면체 하나: 보이는 옆면·윗면·앞면 3개를 면마다 다른 밝기로 (앞면을 마지막에)."""
        x0, x1, y0, y1, z0, z1 = box
        cx = self.c.pos[0]
        sx = x0 if cx < (x0 + x1) / 2 else x1
        faces = (
            ([(sx, y0, z0), (sx, y0, z1), (sx, y1, z1), (sx, y1, z0)], shade(color, SIDE_K)),
            ([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], top or shade(color, TOP_K)),
            ([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)], shade(color, FRONT_K)),
        )
        text = "".join(self.poly(corners, fill) for corners, fill in faces)
        center = self.c.project(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
        self.put(center.z, _tag("g", {"stroke": shade(color, 0.65), "stroke-width": 0.5,
                                      "stroke-linejoin": "round"}, text))  # fmt: skip

    def boxes(self) -> None:
        if not self.with_furniture and self.pose.posture != "sit":
            return
        pelvis = self.j["pelvis"]
        for box, color in furniture(self.place, pelvis[0], pelvis[2] - (
                0.12 if self.pose.posture == "sit" else 0.0), self.pose.posture):  # fmt: skip
            if not self.with_furniture and color not in (SEAT, SOFA, CHAIR):
                continue
            self.cuboid(box, color)
            if color == TABLE or color == DESK:
                x0, x1, _, y1, z0, z1 = box
                self.surface = ((x0 + x1) / 2, y1, (z0 + z1) / 2)

    def shadow(self) -> None:
        pelvis = self.j["pelvis"]
        sh = self.c.project((pelvis[0] + 0.25, 0, pelvis[2] + 0.1))
        rx = 0.6 if self.pose.posture == "lie" else 0.35
        self.parts.append(_tag("ellipse", {"cx": sh.x, "cy": sh.y, "rx": rx * sh.s,
                                           "ry": 0.08 * sh.s, "fill": SHADOW}))  # fmt: skip

    def box(self, corners: list[Vec], fill: str, stroke: str, z_bias: float) -> None:
        pts = [self.at(v) for v in corners]
        attrs: Attrs = {"points": _points(pts), "fill": fill, "stroke": stroke,
                        "stroke-width": 0.8}  # fmt: skip
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

    def laptop(self) -> None:
        """노트북: 장소에 탁자·책상이 있으면 그 위, 없으면 인물 앞 허리 높이 (바닥 판 + 화면 판)."""
        if self.surface is not None:
            sx, sy, sz = self.surface
            base = [(sx - 0.18, sy, sz - 0.14), (sx + 0.18, sy, sz - 0.14),
                    (sx + 0.18, sy, sz + 0.1), (sx - 0.18, sy, sz + 0.1)]  # fmt: skip
            lid = [(sx - 0.18, sy, sz + 0.1), (sx + 0.18, sy, sz + 0.1),
                   (sx + 0.18, sy + 0.22, sz + 0.12), (sx - 0.18, sy + 0.22, sz + 0.12)]
            z = self.c.project((sx, sy, sz)).z
            self.put(z - 0.05, self.poly(base, "#9aa3b2") + self.poly(lid, "#2b2f36"))
            return
        self.box([(-0.18, 0.8, -0.45), (0.18, 0.8, -0.45), (0.18, 0.8, -0.2),
                  (-0.18, 0.8, -0.2)], "#9aa3b2", "#5c6370", -0.05)  # fmt: skip
        self.box([(-0.18, 0.8, -0.2), (0.18, 0.8, -0.2), (0.18, 1.02, -0.18),
                  (-0.18, 1.02, -0.18)], "#2b2f36", "#111", -0.06)  # fmt: skip

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
            head = {"cx": top.x, "cy": top.y, "r": 0.08 * top.s, "fill": "#3a3a46"}
            light = {"points": _points([top, left, right]), "fill": "rgba(255,236,170,.35)"}
            self.put(top.z + 0.5, _tag("polygon", light) + _tag("circle", head))
        if "laptop" in extra:
            self.laptop()

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

    def limb(self, a: str, b: str, width: float, color: str = MANNEQUIN) -> None:
        """캡슐 팔다리: 테두리 선 + 몸 색 선 + 한쪽(왼쪽 위) 밝은 줄."""
        pa, pb = self.c.project(self.j[a]), self.c.project(self.j[b])
        wd = width * (pa.s + pb.s) / 2
        round_cap = {"stroke-linecap": "round"}
        edge = _line(pa, pb, stroke=MANNEQUIN_LINE, **{"stroke-width": wd + 1.2}, **round_cap)
        main = _line(pa, pb, stroke=color, **{"stroke-width": wd}, **round_cap)
        dx, dy = pb.x - pa.x, pb.y - pa.y
        length = math.hypot(dx, dy) or 1.0
        ox, oy = -dy / length * wd * 0.22, dx / length * wd * 0.22
        if ox + oy > 0:  # 늘 왼쪽 위로 (빛이 왼쪽 위에서)
            ox, oy = -ox, -oy
        shine: Attrs = {"x1": pa.x + ox, "y1": pa.y + oy, "x2": pb.x + ox, "y2": pb.y + oy,
                        "stroke": HIGHLIGHT, "stroke-width": wd / 4, **round_cap}  # fmt: skip
        self.put((pa.z + pb.z) / 2, _tag("line", edge) + _tag("line", main) + _tag("line", shine))

    def slab(self, names: tuple[str, ...], thick: float, z_bias: float = 0.0) -> None:
        """둥근 상자 (몸통·골반): 관절 네 점 다각형 + 굵은 둥근 테두리로 두께."""
        pts = [self.c.project(self.j[k]) for k in names]
        wd = thick * sum(p.s for p in pts) / len(pts)
        joint = {"stroke-linejoin": "round", "points": _points(pts)}
        edge = _tag("polygon", {**joint, "fill": MANNEQUIN_LINE, "stroke": MANNEQUIN_LINE,
                                "stroke-width": wd + 1.2})  # fmt: skip
        body = _tag("polygon", {**joint, "fill": "url(#st)", "stroke": "url(#st)",
                                "stroke-width": wd})  # fmt: skip
        self.put(sum(p.z for p in pts) / len(pts) + z_bias, edge + body)

    def ball(self, name: str, r: float, fill: str) -> None:
        p = self.c.project(self.j[name])
        circle = _tag("circle", {"cx": p.x, "cy": p.y, "r": r * p.s, "fill": fill})
        self.put(p.z - 0.001, circle)

    def figure(self) -> None:
        for side in ("l", "r"):
            self.limb(f"hip_{side}", f"kn_{side}", 0.15)
            self.limb(f"kn_{side}", f"ft_{side}", 0.12)
        self.ball("ft_l", 0.07, SHOE)
        self.ball("ft_r", 0.07, SHOE)
        self.slab(("w_l", "w_r", "p_r", "p_l"), 0.14, 0.06)
        self.slab(("t_l", "t_r", "w_r", "w_l"), 0.16)
        for side in ("l", "r"):
            self.limb(f"sh_{side}", f"el_{side}", 0.1)
            self.limb(f"el_{side}", f"hd_{side}", 0.085)
        self.ball("sh_l", 0.09, "url(#st)")
        self.ball("sh_r", 0.09, "url(#st)")
        self.ball("hd_l", 0.045, "url(#sk)")
        self.ball("hd_r", 0.045, "url(#sk)")
        self.limb("neck", "nb", 0.08)  # 목은 몸통 위끝까지만
        self.head()

    def head(self) -> None:
        """머리 구(그라데이션) + 보는 쪽 코 점 + 카메라 쪽을 볼 때만 눈."""
        hd, nose = self.c.project(self.j["head"]), self.c.project(self.j["nose"])
        r = 0.11 * hd.s
        out = _tag("circle", {"cx": hd.x, "cy": hd.y, "r": r, "fill": "url(#sk)",
                              "stroke": MANNEQUIN_LINE, "stroke-width": 0.7})  # fmt: skip
        fx, fy = nose.x - hd.x, nose.y - hd.y
        if nose.z < hd.z + 0.03:  # 앞쪽 반구·옆얼굴일 때만 (뒤통수엔 안 그림)
            dot = {"cx": hd.x + fx * 0.85, "cy": hd.y + fy * 0.85, "r": 0.022 * hd.s,
                   "fill": NOSE}  # fmt: skip
            out += _tag("circle", dot)
        if abs(fx) < 0.1 * hd.s and nose.z < hd.z and self.pose.posture != "lie":
            for dx in (-0.035, 0.035):
                eye = {"cx": hd.x + fx * 0.6 + dx * hd.s, "cy": hd.y - 0.01 * hd.s,
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
