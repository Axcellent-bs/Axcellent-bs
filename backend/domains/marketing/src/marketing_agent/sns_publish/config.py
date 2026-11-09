"""`publish_sns` 설정. 값은 환경변수에서 읽고 코드에 박지 않는다 (기본값만 둔다).

| 변수 | 뜻 | 기본 |
| --- | --- | --- |
| `MARKETING_SNS_PROVIDER` | `mock` 또는 `instagram`. 그 밖의 값은 호출 전에 거부 | `mock` |
| `MARKETING_SNS_PENDING_DB` | 게시 대기 저장소(SQLite 파일). 비우면 메모리 | `:memory:` |
| `MARKETING_SNS_REVIEW_POLL_S` | 대기 건의 승인 여부를 safety에 묻는 간격(초). 0이면 끔 | `15` |
| `MARKETING_SNS_MAX_RETRIES` | 일시 오류 재시도 상한 | `3` |
| `MARKETING_SNS_RETRY_BASE_S` | 재시도 백오프 시작 간격(초, 2배씩) | `2` |
| `MARKETING_SNS_STATUS_POLL_S` | 컨테이너 상태 조회 간격(초) | `5` |
| `MARKETING_SNS_STATUS_MAX_POLLS` | 컨테이너 상태 조회 최대 횟수 | `60` |
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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

    @classmethod
    def from_env(cls) -> SnsConfig:
        return cls(
            review_poll_s=_num("MARKETING_SNS_REVIEW_POLL_S", 15),
            max_retries=int(_num("MARKETING_SNS_MAX_RETRIES", 3)),
            retry_base_s=_num("MARKETING_SNS_RETRY_BASE_S", 2),
            status_poll_s=_num("MARKETING_SNS_STATUS_POLL_S", 5),
            status_max_polls=max(1, int(_num("MARKETING_SNS_STATUS_MAX_POLLS", 60))),
        )
