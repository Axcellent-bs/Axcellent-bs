"""campaign_effect 여러 날 캠페인 평가 케이스 만들기 (`multiday/cases`·`multiday/holdout`).

    cd backend
    uv run python domains/data/eval/campaign/make_multiday.py            # multiday/cases/
    uv run python domains/data/eval/campaign/make_multiday.py --holdout  # multiday/holdout/

규칙은 `README.md` "여러 날 캠페인". 요약:
- 데이터는 같은 생성기(`eval_world`, shop-api 코드). 구간 시작 28일 전부터 이어진 기록을 만들고
  (`traffic.history_days`), 요일 배수(`traffic.weekday_levels`, 주말이 높음)를 곱한다.
- 주문 증감은 합성 주입(sale·order_drop·none). **정답(방향·배수)은 주입 조건에서 정한다.**
  심은 조건·정답은 예전(14일 기록)과 같고, 기록 길이와 기대 정의만 4주로 바꿨다.
- 기대(`counted.orders_baseline`)는 다음 코드 PR의 기준선 정의로 센다: **7·14·21·28일 전 같은
  요일·같은 길이 구간의 평균** (시작 전 기록 안에 들어오는 구간만, 최소 3구간). `multiday4/`와 같은
  정의다. 7일 이하 캠페인만 만든다. 채점기(`score.py`)는 이 칸으로 증분 오차를 잰다.
- 쓸 수 있는 구간이 3개보다 적으면(시작 전 기록 21일 미만) 주문·매출은 확인할 수 없음
  (`short_history`)이 정답이다.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))  # 이 폴더의 make_cases가 결제 평가 make_cases보다 먼저

import eval_world  # noqa: E402 - 결제 평가와 같은 생성기
from make_cases import NOTE_CUMULATIVE, _coupons, _iso, direction, write  # noqa: E402

CASES = HERE / "multiday" / "cases"
HOLDOUT = HERE / "multiday" / "holdout"
HOLDOUT_SEED = 2026110204  # 만든 날 YYYYMMDD + 그날 몇 번째 (campaign holdout 03 다음)
HOLDOUT_COUNT = 10
HOLDOUT_FACTORS = (0.6, 0.8, 1.0, 1.15, 1.2, 1.3, 2.0)
BASELINE_DAYS = 28  # 만드는 시작 전 기록 (같은 요일 4구간 모두)
WEEKS = (7, 14, 21, 28)  # 같은 요일 = 1·2·3·4주 전
MIN_WEEKS = 3  # 이만큼 구간이 있어야 기대를 낸다
OLD_HISTORY_DAYS = 14  # 4주로 늘리기 전 기록 길이. 이 뒤 칸은 늘리기 전과 같은 데이터다
ORDERS_PER_5M = 0.5  # 평일 하루 약 140건 (테스트 시간 때문에 결제 평가보다 적게)
WEEKDAY_LEVELS = [1.0, 1.0, 1.0, 1.0, 1.1, 1.4, 1.3]  # 월~일. 주말이 높다
MONDAY = datetime(2026, 10, 12, tzinfo=UTC)  # 기준 월요일 (UTC 날짜·요일로 센다)
WEEKDAY_KO = "월화수목금토일"


def day(weekday: int, week: int = 0) -> datetime:
    """기준 주의 그 요일 0시(UTC). `week`만큼 주를 옮긴다."""
    return MONDAY + timedelta(days=weekday + 7 * week)


def spec(
    case_id: str, start: datetime, days: int, factor: float, coupons: dict[str, Any] | None,
    history_days: int = BASELINE_DAYS, older_rng_before_days: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    end = start + timedelta(days=days)
    window = {"from": _iso(start), "to": _iso(end)}
    if factor > 1:
        injection: dict[str, Any] = {"type": "sale", "factor": factor, **window}
    elif factor < 1:
        injection = {"type": "order_drop", "factor": factor, **window}
    else:
        injection = {"type": "none"}
    out: dict[str, Any] = {
        "id": case_id,
        "window": window,
        "traffic": {
            "orders_per_5m": ORDERS_PER_5M,
            "history_days": history_days,
            "weekday_levels": WEEKDAY_LEVELS,
        },  # fmt: skip
        "injection": injection,
    }
    if older_rng_before_days is not None:  # 늘린 앞쪽 기록만 따로 둔 난수 (eval_world)
        out["traffic"]["older_rng_before_days"] = older_rng_before_days
    if coupons:
        out["campaign"] = coupons
    return out


def usable(history_days: int) -> list[int]:
    """시작 전 기록 안에 들어오는 같은 요일 구간 (며칠 전)."""
    return [k for k in WEEKS if k <= history_days]


def counted(world: eval_world.World, campaign_id: str | None, history_days: int) -> dict[str, Any]:
    """생성기 기록에서 바로 센 값 (기대는 같은 요일·같은 길이 4구간 정의, data 코드가 아님)."""
    window_from, window_to = world.window

    def rows(start: datetime, end: datetime) -> list[eval_world.Payment]:
        return [p for p in world.payments if start <= p.at < end]

    def revenue(items: list[eval_world.Payment]) -> int:
        return sum(p.amount for p in items if p.failure_code is None)

    now = rows(window_from, window_to)
    weeks = usable(history_days)
    full = len(weeks) >= MIN_WEEKS
    past = [rows(window_from - timedelta(days=k), window_to - timedelta(days=k)) for k in weeks]
    out: dict[str, Any] = {
        "orders_actual": len(now),
        # 쓸 수 있는 구간이 3개보다 적으면 기대를 셀 수 없다 (반쯤 빈 평균을 넣지 않는다)
        "orders_baseline": round(sum(len(p) for p in past) / len(past), 4) if full else None,
        "revenue_actual": revenue(now),
        "revenue_baseline": round(sum(revenue(p) for p in past) / len(past), 4) if full else None,
        "baseline_rule": "7·14·21·28일 전 같은 요일·같은 길이 구간 평균 (최소 3구간)",
        "baseline_weeks": weeks,
        "history_days_before_start": history_days,
    }
    if campaign_id:
        with world.db.tx() as conn:
            issued = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM coupons WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()[0]
            used = conn.execute(
                "SELECT o.created_at, o.coupon_units FROM orders o JOIN coupons c "
                "ON o.coupon_id = c.coupon_id WHERE c.campaign_id = ? AND o.status != 'cancelled'",
                (campaign_id,),
            ).fetchall()
        used_in = sum(int(r[1]) for r in used if window_from <= eval_world.parse(r[0]) < window_to)
        used_after = sum(int(r[1]) for r in used if eval_world.parse(r[0]) >= window_to)
        out |= {
            "coupon_issued_units": int(issued),
            "coupon_used_in_window": used_in,
            "coupon_used_after": used_after,
            "coupon_used_units": used_in + used_after,
            "coupon_use_rate": round((used_in + used_after) / issued, 4) if issued else None,
        }
    return out


def case(
    case_id: str, kind: str, start: datetime, days: int, factor: float, *,
    campaign_id: str | None, coupons: dict[str, Any] | None,
    history_days: int = BASELINE_DAYS, memo: str = "",
) -> dict[str, Any]:  # fmt: skip
    # 예전 14일 기록은 그대로 두고 앞쪽만 늘린다 (늘린 칸은 따로 둔 난수)
    older = OLD_HISTORY_DAYS if history_days > OLD_HISTORY_DAYS else None
    scenario = spec(case_id, start, days, factor, coupons, history_days, older)
    world = eval_world.build(scenario)
    end = start + timedelta(days=days)
    if len(usable(history_days)) < MIN_WEEKS:
        orders: dict[str, Any] = {"status": "unknown", "unknown_reason": "short_history"}
        revenue: dict[str, Any] = {"status": "unknown", "unknown_reason": "short_history"}
    else:
        orders = {"status": "known", "direction": direction(factor), "multiplier": factor}
        revenue = {"status": "known", "direction": direction(factor)}
    if not campaign_id:
        coupon: dict[str, Any] = {"status": "unknown", "unknown_reason": "no_campaign_id"}
    elif not coupons:
        coupon = {"status": "unknown", "unknown_reason": "no_coupons"}
    else:
        coupon = {
            "status": "known",
            "issued_units": int(coupons["buyers"]) * int(coupons["units_each"]),
            "used_after_units": int(coupons.get("used_after", 0)),
            "note": NOTE_CUMULATIVE,
        }
    weekdays = "".join(WEEKDAY_KO[(start + timedelta(days=i)).weekday()] for i in range(days))
    return {
        "id": case_id,
        "kind": kind,
        "input": {"campaign_id": campaign_id, "window_from": _iso(start), "window_to": _iso(end)},
        "spec": scenario,
        "expected": {"orders": orders, "revenue": revenue, "coupon": coupon},
        "counted": counted(world, campaign_id, history_days),
        "memo": f"{days}일 캠페인 ({weekdays}). {memo}".strip(),
    }


# ---------------------------------------------------------------- multiday/cases · holdout


def build_cases() -> list[dict[str, Any]]:
    c = _coupons
    return [
        case("up_x2_2d_tue", "up", day(1), 2, 2.0, campaign_id="cmp_m_up2",
             coupons=c("cmp_m_up2", 15, 0.2, 3), memo="평일 2일, 주문 2배"),
        case("up_x1_3_3d_fri_weekend", "up", day(4), 3, 1.3, campaign_id="cmp_m_up13",
             coupons=c("cmp_m_up13", 15, 0.2, 2), memo="금~일(주말 포함), 주문 1.3배"),
        case("flat_3d_fri_weekend", "none", day(4), 3, 1.0, campaign_id="cmp_m_flat_we",
             coupons=c("cmp_m_flat_we", 15, 0.2, 0),
             memo="금~일, 변화 없음. 주말이 원래 높아서 직전 평일과 비교하면 늘어난 것처럼 보인다"),
        case("down_x0_6_2d_mon", "down", day(0), 2, 0.6, campaign_id="cmp_m_down",
             coupons=c("cmp_m_down", 15, 0.2, 2), memo="월~화, 주문 0.6배"),
        case("small_up_x1_15_7d_mon", "up", day(0), 7, 1.15, campaign_id="cmp_m_s115",
             coupons=c("cmp_m_s115", 15, 0.1, 2),
             memo="7일, 작은 효과 1.15배 (크기는 증분 오차로 본다)"),
        case("small_up_x1_2_3d_tue", "up", day(1), 3, 1.2, campaign_id="cmp_m_s12",
             coupons=c("cmp_m_s12", 15, 0.2, 0), memo="평일 3일, 작은 효과 1.2배"),
        case("flat_7d_mon", "none", day(0), 7, 1.0, campaign_id="cmp_m_flat7",
             coupons=c("cmp_m_flat7", 15, 0.1, 1), memo="7일, 변화 없음"),
        case("up_x1_3_7d_thu", "up", day(3), 7, 1.3, campaign_id=None, coupons=None,
             memo="목~수 7일(주말 낌), 1.3배. campaign_id 없이 요청 → 쿠폰 확인할 수 없음"),
        case("short_history_2d_tue", "unknown", day(1), 2, 2.0, campaign_id="cmp_m_short",
             coupons=c("cmp_m_short", 15, 0.2, 1), history_days=10,
             memo="시작 전 기록이 10일뿐 → 주문·매출 확인할 수 없음, 쿠폰은 냄"),
    ]  # fmt: skip


def build_holdout() -> list[dict[str, Any]]:
    rng = random.Random(HOLDOUT_SEED)
    out = []
    for i in range(HOLDOUT_COUNT):
        factor = rng.choice(HOLDOUT_FACTORS)
        days = rng.choice((2, 3, 7))
        start = day(rng.randint(0, 6), week=rng.randint(-2, 1))
        campaign_id = f"cmp_mh{i:02d}"
        coupons = _coupons(campaign_id, rng.randint(8, 20), round(rng.uniform(0.05, 0.3), 2),
                           rng.randint(0, 4))  # fmt: skip
        item = case(f"holdout_{HOLDOUT_SEED}_{i:02d}", direction(factor), start, days, factor,
                    campaign_id=campaign_id, coupons=coupons)  # fmt: skip
        item["holdout"] = {"seed": HOLDOUT_SEED, "factor": factor, "days": days}
        out.append(item)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="campaign_effect 여러 날 캠페인 평가 케이스 만들기"
    )
    parser.add_argument("--holdout", action="store_true")
    args = parser.parse_args()
    items = build_holdout() if args.holdout else build_cases()
    folder = HOLDOUT if args.holdout else CASES
    folder.parent.mkdir(exist_ok=True)
    write(folder, items)
    kinds: dict[str, int] = {}
    for item in items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    print(f"multiday/{folder.name}: {len(items)}개 {kinds}")


if __name__ == "__main__":
    main()
