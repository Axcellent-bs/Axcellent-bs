"""문의 급증 이벤트(`support.inquiry_surge`) 기준값. 기준은 이 파일 한 곳에서만 정한다.

임시 기준은 같은 분류 5건/10분이다(준하님·태윤님 합의, 실제 장애 기록이 없어 가정이다). 기록이
쌓이면 환경 변수나 아래 기본값만 고친다.

- `SUPPORT_SURGE_EVENTS`: `off`·`0`·`false`면 이벤트를 내보내지 않는다(기본 켜짐).
- `SUPPORT_SURGE_MIN_COUNT`: 이 건수 이상이어야 급증(기본 5, 1~1000).
- `SUPPORT_SURGE_WINDOW_MINUTES`: 건수를 세는 창 길이(기본 10, 1~1440). 같은 분류는 이 길이 동안
  한 번만 내보낸다.
"""

from __future__ import annotations

import os

ENV_ENABLED = "SUPPORT_SURGE_EVENTS"
ENV_MIN_COUNT = "SUPPORT_SURGE_MIN_COUNT"
ENV_WINDOW = "SUPPORT_SURGE_WINDOW_MINUTES"

DEFAULT_MIN_COUNT = 5
DEFAULT_WINDOW_MINUTES = 10
RATIO = 3.0  # 평소의 이 배수 이상(평소가 0이면 건수 기준만 쓴다)
BASELINE_HOURS = 24  # 평소를 정하는 기간
BASELINE_METHOD = "median"  # 지난 한 시간에 몰린 문의에 평소가 부풀지 않게 중앙값을 쓴다


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int((os.getenv(name) or "").strip())
    except ValueError:
        return default
    return value if low <= value <= high else default


def enabled() -> bool:
    return (os.getenv(ENV_ENABLED) or "").strip().lower() not in ("off", "0", "false")


def min_count() -> int:
    return _bounded_int(ENV_MIN_COUNT, DEFAULT_MIN_COUNT, 1, 1000)


def window_minutes() -> int:
    return _bounded_int(ENV_WINDOW, DEFAULT_WINDOW_MINUTES, 1, 1440)
