"""`publish_sns` 설정. 값은 환경변수에서 읽고 코드에 박지 않는다 (기본값만 둔다).

| 변수 | 뜻 | 기본 |
| --- | --- | --- |
| `MARKETING_SNS_PROVIDER` | `mock` 또는 `instagram`. 그 밖의 값은 호출 전에 거부 | `mock` |
| `MARKETING_SNS_PENDING_DB` | 게시 대기 저장소(SQLite 파일). 비우면 메모리 | `:memory:` |
| `MARKETING_SNS_REVIEW_POLL_S` | 대기 건의 승인 여부를 safety에 묻는 간격(초). 0이면 끔 | `15` |
| `MARKETING_SNS_MAX_RETRIES` | 일시 오류 재시도 상한 | `3` |
| `MARKETING_SNS_RETRY_BASE_S` | 재시도 백오프 시작 간격(초, 2배씩) | `2` |
| `MARKETING_SNS_STATUS_POLL_S` | 컨테이너 상태 조회 간격(초). Meta 안내는 1분에 1회 | `60` |
| `MARKETING_SNS_STATUS_MAX_POLLS` | 컨테이너 상태 조회 최대 횟수(60초 x 5 = 최대 5분) | `5` |
| `MARKETING_SNS_LIMITS_CONFIG` | 게시 한도 파일(`publish_limits.toml`) 경로 | 번들 파일 |

24시간 게시 상한·중단 해제 시각은 환경변수가 아니라 `publish_limits.toml`에서 정한다.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LIMITS_CONFIG_ENV = "MARKETING_SNS_LIMITS_CONFIG"
DEFAULT_LIMITS_FILE = Path(__file__).with_name("publish_limits.toml")


def _num(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class SnsConfig:
    review_poll_s: float
    max_retries: int
    retry_base_s: float
    status_poll_s: float
    status_max_polls: int
    max_posts_per_24h: int = 45
    halt_cleared_before: datetime | None = None

    @classmethod
    def from_env(cls) -> SnsConfig:
        cap, cleared = load_limits()
        return cls(
            max_posts_per_24h=cap,
            halt_cleared_before=cleared,
            review_poll_s=_num("MARKETING_SNS_REVIEW_POLL_S", 15),
            max_retries=int(_num("MARKETING_SNS_MAX_RETRIES", 3)),
            retry_base_s=_num("MARKETING_SNS_RETRY_BASE_S", 2),
            status_poll_s=_num("MARKETING_SNS_STATUS_POLL_S", 60),
            status_max_polls=max(1, int(_num("MARKETING_SNS_STATUS_MAX_POLLS", 5))),
        )


class LimitsConfigError(RuntimeError):
    """한도 파일을 읽을 수 없다. 게시를 막는다(fail-closed)."""


def load_limits() -> tuple[int, datetime | None]:
    """(`max_posts_per_24h`, `halt_cleared_before`). 읽을 수 없거나 값이 이상하면 예외."""
    raw = (os.getenv(LIMITS_CONFIG_ENV) or "").strip()
    path = Path(raw) if raw else DEFAULT_LIMITS_FILE
    try:
        publish = tomllib.loads(path.read_text(encoding="utf-8"))["publish"]
        cap = publish["max_posts_per_24h"]
        cleared_raw = str(publish.get("halt_cleared_before", "")).strip()
        cleared = datetime.fromisoformat(cleared_raw) if cleared_raw else None
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError):
        raise LimitsConfigError("publish_limits_unreadable") from None
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0:
        raise LimitsConfigError("publish_limits_invalid")
    if cleared is not None and cleared.tzinfo is None:
        raise LimitsConfigError("publish_limits_invalid")  # 시각대가 없으면 해제 시점이 모호하다
    return cap, cleared
