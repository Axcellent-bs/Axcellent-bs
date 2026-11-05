"""환경변수 설정. 값은 모두 `.env`(사람 입력)에서만 온다. 기본값을 지어내지 않는다.

- 공급자·모델: `MARKETING_IMAGE_PROVIDER/MODEL`, `MARKETING_VIDEO_PROVIDER/MODEL`.
  공급자 기본은 `mock`, 모델 기본은 비움.
- 키: `MARKETING_IMAGE_API_KEY`, `MARKETING_VIDEO_API_KEY`. 이 모듈은 이름만 정의하고,
  설정돼 있는지만 `has_api_key`로 알려 준다. 값은 돌려주지도 로그에 내지도 않는다
  (실제 어댑터가 직접 읽는다).
- 비용: 환율 `MARKETING_FX_USD_KRW`·기준일 `MARKETING_FX_AS_OF`·
  허용 경과일 `MARKETING_FX_MAX_AGE_DAYS`·여유 `MARKETING_COST_SAFETY_MARGIN_PCT`,
  건당 상한 `MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM`, 월 상한 `MARKETING_MEDIA_MONTHLY_BUDGET_KRW`.
  비어 있으면 실제 공급자 호출을 거부한다(목 모드는 허용).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from marketing_agent.media.base import MediaKind

MOCK = "mock"
LEDGER_DB_ENV = "MARKETING_MEDIA_LEDGER_DB"
MEDIA_DIR_ENV = "MARKETING_MEDIA_DIR"
API_KEY_ENVS = {"image": "MARKETING_IMAGE_API_KEY", "video": "MARKETING_VIDEO_API_KEY"}


def _text(name: str) -> str:
    return os.getenv(name, "").strip()


def _decimal(name: str) -> Decimal | None:
    raw = _text(name)
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value >= 0 else None


def _integer(name: str) -> int | None:
    raw = _text(name)
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def _date(name: str) -> date | None:
    try:
        return date.fromisoformat(_text(name))
    except ValueError:
        return None


@dataclass(frozen=True)
class MediaSettings:
    image_provider: str
    image_model: str
    video_provider: str
    video_model: str
    fx_usd_krw: Decimal | None
    fx_as_of: date | None
    fx_max_age_days: int | None
    margin_pct: Decimal | None
    max_cost_krw_per_item: int | None
    monthly_budget_krw: int | None
    media_dir: Path

    @classmethod
    def from_env(cls) -> MediaSettings:
        fx = _decimal("MARKETING_FX_USD_KRW")
        return cls(
            image_provider=_text("MARKETING_IMAGE_PROVIDER").lower() or MOCK,
            image_model=_text("MARKETING_IMAGE_MODEL"),
            video_provider=_text("MARKETING_VIDEO_PROVIDER").lower() or MOCK,
            video_model=_text("MARKETING_VIDEO_MODEL"),
            fx_usd_krw=fx if fx else None,  # 0도 설정 안 한 것으로 본다
            fx_as_of=_date("MARKETING_FX_AS_OF"),
            fx_max_age_days=_integer("MARKETING_FX_MAX_AGE_DAYS"),
            margin_pct=_decimal("MARKETING_COST_SAFETY_MARGIN_PCT"),
            max_cost_krw_per_item=_integer("MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM"),
            monthly_budget_krw=_integer("MARKETING_MEDIA_MONTHLY_BUDGET_KRW"),
            media_dir=Path(_text(MEDIA_DIR_ENV) or Path(tempfile.gettempdir()) / "marketing_media"),
        )

    def provider(self, kind: MediaKind) -> str:
        return self.image_provider if kind == "image" else self.video_provider

    def model(self, kind: MediaKind) -> str:
        return self.image_model if kind == "image" else self.video_model

    def fx_problem(self, today: date) -> str | None:
        """실제 공급자 호출을 막아야 하면 이유 코드, 괜찮으면 None."""
        if self.fx_usd_krw is None or self.fx_as_of is None or self.margin_pct is None:
            return "fx_not_configured"
        if self.fx_max_age_days is None:
            return "fx_max_age_not_configured"
        if self.fx_as_of > today or (today - self.fx_as_of).days > self.fx_max_age_days:
            return "fx_stale"
        return None


def has_api_key(kind: MediaKind) -> bool:
    """키가 설정돼 있는지만 알려 준다. 값은 돌려주지 않는다."""
    return bool(_text(API_KEY_ENVS[kind]))
