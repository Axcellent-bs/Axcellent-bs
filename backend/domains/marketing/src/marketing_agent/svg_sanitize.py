"""LLM이 그린 SVG 콘티 칸 정리 (순수 함수, 2026-11-25).

화면이 SVG 원문을 그대로 그리므로 허용한 요소·속성만 남기고 나머지는 버린다.

- 파서는 표준 라이브러리 `xml.etree.ElementTree`.
  입력에 `<!DOCTYPE`·`<!ENTITY`가 있으면 통째로 버린다.
- 요소는 네임스페이스를 뗀 로컬 이름으로 판단한다(`{http://www.w3.org/2000/svg}rect` = rect).
  SVG가 아닌 네임스페이스 요소와 허용 밖 요소는 자식까지 통째로 버린다.
- 속성은 네임스페이스 없는 허용 이름만 남긴다(`xlink:href`·`on*`·`style` 등은 버린다).
  값에 `url(`·`javascript:`가 있으면(대소문자·공백 무시) 그 속성을 버린다. 단 fill·stroke의
  같은 SVG 안 그라데이션 참조 `url(#id)`(영문·숫자·_·-)만은 남긴다(11/25 3D 마네킹 콘티).
- 그라데이션(defs·linearGradient·radialGradient·stop)은 남긴다. id는 영문·숫자·_·-만.
- 글자는 `text`·`tspan` 안에서만 남기고, 직렬화는 ElementTree가 이스케이프한다.
- 뿌리가 svg가 아니거나 결과가 16KB(16384바이트, 11/25 8KB에서 늘림)를 넘으면 None.
- 기획 `planning_agent.storyboard_svg.sanitize_svg`와 같은 규칙이다(도메인끼리 import하지 않음).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405 - DOCTYPE·ENTITY가 든 입력은 파싱 전에 버린다
from typing import Final

from ax_contracts.schemas.marketing_storyboard import MAX_SVG_BYTES

SVG_NS: Final = "http://www.w3.org/2000/svg"
MAX_INPUT_CHARS: Final = 64 * 1024  # 이보다 큰 입력은 파싱하지 않는다
MAX_TEXT_CHARS: Final = 120
MAX_DEPTH: Final = 12

ALLOWED_ELEMENTS: Final = frozenset(
    {"svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path"}
    | {"text", "tspan"}
    # 11/25 기획 3D 마네킹 콘티의 구 음영·배경 (같은 SVG 안 그라데이션만)
    | {"defs", "linearGradient", "radialGradient", "stop"}
)
ALLOWED_ATTRS: Final = frozenset(
    {
        "viewBox", "x", "y", "dx", "dy", "width", "height", "cx", "cy", "r", "rx", "ry",
        "x1", "y1", "x2", "y2", "points", "d", "fill", "stroke", "stroke-width",
        "stroke-linecap", "opacity", "transform", "text-anchor", "font-size", "font-weight",
        "stroke-linejoin", "offset", "stop-color", "stop-opacity", "id", "gradientUnits",
    }
)  # fmt: skip
TEXT_ELEMENTS: Final = frozenset({"text", "tspan"})
_FORBIDDEN_DOC = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BAD_VALUE = ("url(", "javascript:")
# fill·stroke만 같은 SVG 안 그라데이션 참조 url(#id)를 받는다. 그 밖 url(·외부 주소는 버린다
_PAINT_ATTRS = frozenset({"fill", "stroke"})
_LOCAL_URL = re.compile(r"url\(#[A-Za-z0-9_-]{1,40}\)")
_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
_GRADIENT_UNITS = frozenset({"objectBoundingBox", "userSpaceOnUse"})


def _local(tag: object) -> tuple[str, str]:
    """(네임스페이스, 로컬 이름). 요소가 아닌 노드(주석 등)는 ("", "")."""
    if not isinstance(tag, str):
        return "", ""
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


def _bad_value(value: str) -> bool:
    squeezed = re.sub(r"\s+", "", value).lower()
    return any(word in squeezed for word in _BAD_VALUE)


def _keep_attr(key: str, value: object) -> bool:
    if key not in ALLOWED_ATTRS or not isinstance(value, str):
        return False
    if key == "id":
        return _ID.fullmatch(value) is not None
    if key == "gradientUnits":
        return value in _GRADIENT_UNITS
    if key in _PAINT_ATTRS and _LOCAL_URL.fullmatch(value.strip()):
        return True
    return not _bad_value(value)


def _clean(src: ET.Element, depth: int) -> ET.Element | None:
    ns, name = _local(src.tag)
    if ns not in ("", SVG_NS) or name not in ALLOWED_ELEMENTS or depth > MAX_DEPTH:
        return None
    attrs = {k: v for k, v in src.attrib.items() if _keep_attr(k, v)}
    out = ET.Element(name, attrs)
    if name in TEXT_ELEMENTS and src.text:
        out.text = _CONTROL.sub("", src.text)[:MAX_TEXT_CHARS]
    for child in src:
        kept = _clean(child, depth + 1)
        if kept is None:
            continue
        if name in TEXT_ELEMENTS and child.tail:
            kept.tail = _CONTROL.sub("", child.tail)[:MAX_TEXT_CHARS]
        out.append(kept)
    return out


def sanitize_svg(raw: object) -> str | None:
    """정리한 SVG 원문. 쓸 수 없으면(형식 오류·svg 아님·금지 선언·16KB 초과) None."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > MAX_INPUT_CHARS or _FORBIDDEN_DOC.search(text):
        return None
    try:
        root = ET.fromstring(_CONTROL.sub("", text))  # noqa: S314 - 위에서 DOCTYPE·ENTITY 거부
    except (ET.ParseError, ValueError):
        return None
    ns, name = _local(root.tag)
    if name != "svg" or ns not in ("", SVG_NS):
        return None
    cleaned = _clean(root, 0)
    if cleaned is None:
        return None
    cleaned.set("xmlns", SVG_NS)
    out = ET.tostring(cleaned, encoding="unicode")
    if len(out.encode("utf-8")) > MAX_SVG_BYTES:
        return None
    return out
