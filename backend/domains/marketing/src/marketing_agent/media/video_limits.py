"""영상 길이 상한(설정)과 이어 붙이기 계획. 외부 호출·파일 접근이 없는 순수 계산이다.

- 절대 상한 `ABSOLUTE_MAX_DURATION_S`(90초, 숏폼 상한)는 코드 상수이고, 운영자가 정하는 상한은
  환경변수 `MARKETING_VIDEO_MAX_DURATION_S`(1~90, 기본 15)다. 절대 상한을 넘거나 숫자가 아니면
  영상 생성을 거부한다(`video_max_duration_invalid`, 호출 전 fail-closed).
  safety 정책(`thresholds.toml`의 `video_duration`)도 같은 90초라 설정이 정책 상한을 넘을 수 없다
  (같은 값인지 시험이 확인한다).
- 경고 길이 `MARKETING_VIDEO_WARN_DURATION_S`(1~운영 상한, 기본 30)는 화면 경고창 기준이다.
  UX 안내일 뿐 안전 관문이 아니다(잘못된 값은 기본값으로 바꾸고 사유 코드만 로그에 남긴다).
- 단일 클립 한도는 공급자 사실이다(`VideoCaps`, 공급자 어댑터가 `video_caps`로 알린다. 없으면 길이
  제한 없는 단일 클립으로 본다. 목 포함). 요청 길이가 단일 클립 한도를 넘으면 `plan_clips`가 클립
  길이 목록을 만든다.
  1) 공급자가 연장(extension)을 지원하면 연장 총 길이(Omni 30초)까지는 `first` + `extend` 클립
     (첫 클립 3~15초, 연장 회당 3~10초). 이전 클립의 interaction id만 다음 요청에 넘긴다.
  2) 그 뒤(또는 연장이 없으면)는 이미지→영상을 지원하는 공급자에서만, 이전 클립 마지막 프레임을
     시작 이미지로 쓰는 `image` 클립(공급자 클립 한도)으로 이어 간다.
  3) 둘 다 안 되면 `long_video_unsupported`로 거절한다(비용 전).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

ABSOLUTE_MAX_DURATION_S = 90  # 숏폼 상한. safety `video_duration` 정책값과 같아야 한다
DEFAULT_MAX_DURATION_S = 15
MAX_DURATION_ENV = "MARKETING_VIDEO_MAX_DURATION_S"
DEFAULT_WARN_DURATION_S = 30
WARN_DURATION_ENV = "MARKETING_VIDEO_WARN_DURATION_S"

ClipMode = Literal["first", "extend", "image"]


class VideoLimitError(Exception):
    """길이 계획을 세울 수 없다. 메시지는 결과 코드다."""


@dataclass(frozen=True)
class VideoCaps:
    """공급자 한 곳의 영상 규격. 기본값은 '제한 없는 단일 클립'(목·테스트 공급자)."""

    min_s: int = 1
    max_s: int = ABSOLUTE_MAX_DURATION_S  # 단일 클립 한도
    extension: bool = False  # 이전 영상에서 이어 만들기(연장)
    extension_min_s: int = 3
    extension_max_s: int = 10  # 연장 1회 길이
    extension_total_s: int = 0  # 연장으로 만들 수 있는 총 길이(첫 클립 포함)
    image_start: bool = False  # 시작 이미지로 영상 만들기(마지막 프레임 이어 붙이기에 쓴다)


@dataclass(frozen=True)
class Clip:
    mode: ClipMode
    duration_s: int
    prev_extend_s: int = 0  # 연장: 이전 영상 중 입력으로 쓰이는 길이(비용 추정용)


@dataclass(frozen=True)
class MaxDuration:
    """설정에서 읽은 상한. `problem`이 있으면 영상 생성을 거부한다."""

    seconds: int
    problem: str | None = None


def parse_max_duration(raw: str | None) -> MaxDuration:
    text = (raw or "").strip()
    if not text:
        return MaxDuration(DEFAULT_MAX_DURATION_S)
    if not text.isascii() or not text.isdigit():
        return MaxDuration(DEFAULT_MAX_DURATION_S, "video_max_duration_invalid")
    value = int(text)
    if not 1 <= value <= ABSOLUTE_MAX_DURATION_S:
        return MaxDuration(DEFAULT_MAX_DURATION_S, "video_max_duration_invalid")
    return MaxDuration(value)


def max_duration_from_env() -> MaxDuration:
    return parse_max_duration(os.getenv(MAX_DURATION_ENV))


@dataclass(frozen=True)
class WarnDuration:
    """경고창을 띄우는 길이(초). 이 길이를 넘는 요청은 화면이 시작 전에 사람 확인을 받는다.

    화면(UX) 안내용 값일 뿐 안전 관문이 아니다. 상한·비용 가드는 이 값과 무관하게 그대로 돈다.
    `problem`은 설정이 잘못돼 기본값으로 바꿨다는 사유 코드(로그용, 값은 담지 않는다).
    """

    seconds: int
    problem: str | None = None


def parse_warn_duration(raw: str | None, max_s: int) -> WarnDuration:
    """`1 ~ max_s`(운영 상한) 정수만 받는다. 비었거나 잘못되면 기본값(상한 이하)이다."""
    default = min(DEFAULT_WARN_DURATION_S, max_s)
    text = (raw or "").strip()
    if not text:
        return WarnDuration(default)
    if not text.isascii() or not text.isdigit() or not 1 <= int(text) <= max_s:
        return WarnDuration(default, "video_warn_duration_invalid")
    return WarnDuration(int(text))


def warn_duration_from_env(max_s: int) -> WarnDuration:
    return parse_warn_duration(os.getenv(WARN_DURATION_ENV), max_s)


def _split(total: int, lo: int, hi: int) -> list[int]:
    """total을 lo~hi 길이 조각으로 고르게 나눈다(합 = total)."""
    if total < lo:
        raise VideoLimitError("long_video_unsupported")
    n = -(-total // hi)
    base, extra = divmod(total, n)
    parts = [base + (1 if i < extra else 0) for i in range(n)]
    if min(parts) < lo or max(parts) > hi:
        raise VideoLimitError("long_video_unsupported")
    return parts


def effective_caps(caps: VideoCaps, price_max_s: int | None) -> VideoCaps:
    """단가표가 허용하는 클립 길이(`max_duration_s`)를 넘지 않게 단일 클립 한도를 줄인다."""
    if price_max_s is None or price_max_s >= caps.max_s:
        return caps
    return VideoCaps(
        min_s=caps.min_s,
        max_s=price_max_s,
        extension=caps.extension,
        extension_min_s=caps.extension_min_s,
        extension_max_s=min(caps.extension_max_s, price_max_s),
        extension_total_s=caps.extension_total_s,
        image_start=caps.image_start,
    )


def plan_clips(total_s: int, caps: VideoCaps) -> list[Clip]:
    """요청 길이를 클립 목록으로 나눈다. 단일 클립에 들어가면 `first` 하나다."""
    if not 1 <= total_s <= ABSOLUTE_MAX_DURATION_S:
        raise VideoLimitError("duration_over_limit")
    if total_s < caps.min_s:
        raise VideoLimitError("duration_under_min")
    if total_s <= caps.max_s:
        return [Clip("first", total_s)]
    clips: list[Clip] = []
    rest = total_s
    if caps.extension and caps.extension_total_s >= caps.max_s:
        ext_total = min(total_s, caps.extension_total_s)
        tail = total_s - ext_total
        if 0 < tail < caps.min_s:  # 남는 조각이 클립 최소보다 짧으면 연장 쪽에서 덜어 온다
            ext_total -= caps.min_s - tail
        first = min(ext_total, caps.max_s)
        left = ext_total - first
        if 0 < left < caps.extension_min_s:
            first -= caps.extension_min_s - left
            left = caps.extension_min_s
        clips.append(Clip("first", first))
        done = first
        if left:
            for part in _split(left, caps.extension_min_s, caps.extension_max_s):
                clips.append(Clip("extend", part, prev_extend_s=min(done, caps.extension_max_s)))
                done += part
        rest = total_s - ext_total
        if rest == 0:
            return clips
        if not caps.image_start:
            raise VideoLimitError("long_video_unsupported")
        for part in _split(rest, caps.min_s, caps.max_s):
            clips.append(Clip("image", part))
        return clips
    if not caps.image_start:
        raise VideoLimitError("long_video_unsupported")
    parts = _split(total_s, caps.min_s, caps.max_s)
    return [Clip("first" if i == 0 else "image", p) for i, p in enumerate(parts)]
