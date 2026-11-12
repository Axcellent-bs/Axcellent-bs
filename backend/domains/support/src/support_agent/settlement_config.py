"""판매자 정산 설정과 시각·금액 도우미 (`settlement_policy.toml`).

2026-11-12 재무(`finance_agent.finance_config`)에서 판매자 정산에 쓰던 부분만 옮겨 왔다.
도메인끼리 import하지 않으므로 같은 계산을 여기 따로 둔다.
"""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from importlib import resources

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommissionConfig(_Strict):
    default_rate: float = Field(ge=0, le=1)
    category_rates: dict[str, float] = Field(default_factory=dict)

    def rate_for(self, category: str | None) -> float:
        return self.category_rates.get(category or "", self.default_rate)


class SettlementConfig(_Strict):
    pay_days_after_period_end: int = Field(ge=0)
    deadline_days: int = Field(default=20, ge=1)
    deadline_basis: str = "구매 확정일부터"
    deadline_warn_days: int = Field(default=5, ge=0)
    deadline_lookback_days: int = Field(default=70, ge=7)


class RuleConfig(_Strict):
    id: str
    title: str
    statement: str
    basis: str
    review: str
    version: int


class SettlementPolicy(_Strict):
    commission: CommissionConfig
    settlement: SettlementConfig
    rule: RuleConfig


@lru_cache(maxsize=1)
def load_policy() -> SettlementPolicy:
    text = resources.files("support_agent").joinpath("settlement_policy.toml").read_text("utf-8")
    return SettlementPolicy.model_validate(tomllib.loads(text))


def applied_rule() -> dict[str, object]:
    """정산서에 붙이는 적용 규칙 한 줄(재무 화면과 같은 모양)."""
    r = load_policy().rule
    return {
        "rule_id": r.id,
        "title": r.title,
        "statement": r.statement,
        "basis": r.basis,
        "review": r.review,
        "detail": None,
        "rules_version": r.version,
    }


# ---------------------------------------------------------------- 시각
def now() -> datetime:
    """지금(UTC). 테스트는 이 함수를 바꿔 날짜를 고정한다."""
    return datetime.now(UTC)


def today() -> date:
    return now().date()


def day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def parse_dt(value: object) -> datetime | None:
    """ISO8601 문자열 → UTC datetime. 시간대가 없으면 UTC로 본다. 못 읽으면 None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def week_range(period: str | None, ref: date) -> tuple[str, date, date]:
    """`YYYY-Www` → (이름, 월요일, 일요일). 없으면 `ref`가 속한 주의 지난주."""
    if period:
        match = WEEK_RE.match(period)
        if not match:
            raise ValueError(f"정산 기간 형식이 아니다(YYYY-Www): {period}")
        try:
            monday = date.fromisocalendar(int(match[1]), int(match[2]), 1)
        except ValueError as exc:
            raise ValueError(f"없는 주: {period}") from exc
    else:
        monday = ref - timedelta(days=ref.weekday() + 7)
    year, week, _ = monday.isocalendar()
    return f"{year}-W{week:02d}", monday, monday + timedelta(days=6)


def week_of(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def rate_amount(amount: int, rate: float) -> int:
    """금액 × 비율을 원 단위로 반올림(0.5 올림)."""
    value = Decimal(amount) * Decimal(str(rate))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def won(n: int | None) -> str:
    return "확인할 수 없음" if n is None else f"{n:,}원"
