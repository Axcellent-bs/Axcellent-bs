"""미디어 사용량. 원화 단가표 대신 공급자가 쓰는 단위 그대로 센다(원화 환산 없음).

- 단위(예산 단위): 영상 `seconds`(클립 길이 합), 이미지 `images`(장), BGM `tracks`(곡).
  종류가 다르면 단위가 다르고, 서로 더하거나 환산하지 않는다. 남은 비율(%)은 종류별로 따로 본다.
- 토큰: 공급자 응답에 토큰이 있으면(Gemini 이미지·영상·BGM) 입력·출력 토큰을 같이 남긴다.
  토큰이 없는 공급자(Kling·Pika·fal·커스텀 등)는 토큰 칸이 None이고 단위 사용량만 남는다.
- 예산은 우리 키(서버 키)에만 건다. 고객사 키(`MARKETING_MEDIA_KEY_OWNER=customer`)는
  예산을 안 건다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from marketing_agent.media.base import MediaKind, MediaResult
from marketing_agent.media.video_limits import Clip

UNITS: dict[str, str] = {"video": "seconds", "image": "images", "audio": "tracks"}
UNIT_LABELS_KO: dict[str, str] = {"seconds": "초", "images": "장", "tracks": "곡"}
KINDS: tuple[MediaKind, ...] = ("video", "image", "audio")


@dataclass(frozen=True)
class UsageAmount:
    """한 건의 사용량. `amount`는 `unit` 단위 정수, 토큰은 응답에 있을 때만."""

    unit: str
    amount: int
    input_tokens: int | None = None
    output_tokens: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "amount": self.amount,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def unit_for(kind: str) -> str:
    return UNITS[kind]


def planned(kind: MediaKind, clips: list[Clip] | None) -> UsageAmount:
    """호출 전 예상 사용량. 영상은 클립 길이 합, 이미지·BGM은 1건."""
    if kind == "video":
        return UsageAmount("seconds", sum(c.duration_s for c in clips or []) or 1)
    return UsageAmount(UNITS[kind], 1)


def measured(kind: MediaKind, expected: UsageAmount, result: MediaResult) -> UsageAmount:
    """호출 뒤 사용량. 단위 사용량은 요청한 만큼(예상값), 토큰은 응답에 있을 때만 붙인다."""
    usage = result.usage
    if usage is None:
        return expected
    return UsageAmount(expected.unit, expected.amount, usage.input_tokens, usage.output_tokens)


def remaining_pct(used: int, limit: int | None) -> int | None:
    """남은 비율(0~100 정수, 버림). 예산이 없으면 None."""
    if limit is None or limit <= 0:
        return None
    return max(0, min(100, (limit - used) * 100 // limit))


def snapshot(used: dict[str, int], limits: dict[str, int | None]) -> dict[str, dict[str, Any]]:
    """종류별 이번 달 사용량·예산·남은 %. 화면·응답용."""
    out: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        limit = limits.get(kind)
        out[kind] = {
            "unit": UNITS[kind],
            "used": used.get(kind, 0),
            "limit": limit,
            "remaining_pct": remaining_pct(used.get(kind, 0), limit),
        }
    return out
