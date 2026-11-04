"""campaign_effect 여러 날 캠페인 평가 케이스 — 4주 기준선 정의 (`multiday4/`).

    cd backend
    uv run python domains/data/eval/campaign/make_multiday4.py            # multiday4/cases/
    uv run python domains/data/eval/campaign/make_multiday4.py --holdout  # multiday4/holdout/

`make_multiday.py`(7·14일 전 2구간 정의)와 같은 생성기·같은 요일 배수에 기준선 정의만 바꾼다.
- 기대(`counted.orders_baseline`): **7·14·21·28일 전 같은 요일·같은 길이 구간의 평균**. 시작 전 기록
  (`traffic.history_days`) 안에 들어오는 구간만 쓴다
- 쓸 수 있는 구간이 `MIN_WEEKS`(3)보다 적으면 주문·매출은 확인할 수 없음(`short_history`)이 정답
- 주문 증감은 합성 주입(sale·order_drop·none). **정답(방향·배수)은 주입 조건에서 정한다**
README "여러 날 캠페인 (4주 기준선)".
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import eval_world  # noqa: E402 - 같은 생성기
from make_cases import NOTE_CUMULATIVE, _coupons, _iso, direction, write  # noqa: E402
from make_multiday import HOLDOUT_FACTORS, WEEKDAY_KO, day, spec  # noqa: E402

CASES = HERE / "multiday4" / "cases"
HOLDOUT = HERE / "multiday4" / "holdout"
HOLDOUT_SEED = 2026110402  # 만든 날 YYYYMMDD + 그날 몇 번째
HOLDOUT_COUNT = 10
WEEKS = (7, 14, 21, 28)  # 같은 요일 = 1·2·3·4주 전
MIN_WEEKS = 3  # 이만큼 구간이 있어야 기대를 낸다
FULL_HISTORY = 28  # 시작 전 기록 (4구간 모두)


def usable(history_days: int) -> list[int]:
    """시작 전 기록 안에 들어오는 같은 요일 구간 (며칠 전)."""
    return [k for k in WEEKS if k <= history_days]


def counted(world: eval_world.World, campaign_id: str | None, history_days: int) -> dict[str, Any]:
    """생성기가 만든 기록에서 바로 센 값 (data 코드가 아님)."""
    window_from, window_to = world.window

    def rows(start: Any, end: Any) -> list[eval_world.Payment]:
        return [p for p in world.payments if start <= p.at < end]

    def revenue(items: list[eval_world.Payment]) -> int:
        return sum(p.amount for p in items if p.failure_code is None)

    weeks = usable(history_days)
    past = [rows(window_from - timedelta(days=k), window_to - timedelta(days=k)) for k in weeks]
    full = len(weeks) >= MIN_WEEKS
    now = rows(window_from, window_to)
    out: dict[str, Any] = {
        "orders_actual": len(now),
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
    case_id: str, kind: str, start: Any, days: int, factor: float, *,
    campaign_id: str | None, coupons: dict[str, Any] | None,
    history_days: int = FULL_HISTORY, memo: str = "",
) -> dict[str, Any]:  # fmt: skip
    scenario = spec(case_id, start, days, factor, coupons, history_days)
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


def build_cases() -> list[dict[str, Any]]:
    c = _coupons
    return [
        case("up_x2_2d_tue", "up", day(1), 2, 2.0, campaign_id="cmp_m4_up2",
             coupons=c("cmp_m4_up2", 15, 0.2, 3), memo="평일 2일, 주문 2배"),
        case("up_x1_3_3d_fri_weekend", "up", day(4), 3, 1.3, campaign_id="cmp_m4_up13",
             coupons=c("cmp_m4_up13", 15, 0.2, 2), memo="금~일(주말 포함), 주문 1.3배"),
        case("flat_3d_fri_weekend", "none", day(4), 3, 1.0, campaign_id="cmp_m4_flat",
             coupons=c("cmp_m4_flat", 15, 0.2, 0), memo="금~일, 변화 없음"),
        case("down_x0_6_2d_mon", "down", day(0), 2, 0.6, campaign_id="cmp_m4_down",
             coupons=c("cmp_m4_down", 15, 0.2, 2), memo="월~화, 주문 0.6배"),
        case("small_up_x1_2_3d_tue", "up", day(1), 3, 1.2, campaign_id="cmp_m4_s12",
             coupons=c("cmp_m4_s12", 15, 0.2, 0), memo="평일 3일, 작은 효과 1.2배"),
        case("flat_7d_mon", "none", day(0), 7, 1.0, campaign_id="cmp_m4_flat7",
             coupons=c("cmp_m4_flat7", 15, 0.1, 1), memo="7일, 변화 없음"),
        case("three_weeks_up_x2_2d_tue", "up", day(1), 2, 2.0, campaign_id=None, coupons=None,
             history_days=21, memo="시작 전 기록 21일 → 3구간(최소)으로 판정"),
        case("short_history_2d_tue", "unknown", day(1), 2, 2.0, campaign_id="cmp_m4_short",
             coupons=c("cmp_m4_short", 15, 0.2, 1), history_days=14,
             memo="시작 전 기록 14일 → 2구간뿐(최소 3) → 주문·매출 확인할 수 없음, 쿠폰은 냄"),
    ]  # fmt: skip


def build_holdout() -> list[dict[str, Any]]:
    rng = random.Random(HOLDOUT_SEED)
    out = []
    for i in range(HOLDOUT_COUNT):
        factor = rng.choice(HOLDOUT_FACTORS)
        days = rng.choice((2, 3, 7))
        start = day(rng.randint(0, 6), week=rng.randint(-2, 1))
        history = rng.choice((FULL_HISTORY, FULL_HISTORY, 21))
        campaign_id = f"cmp_m4h{i:02d}"
        coupons = _coupons(campaign_id, rng.randint(8, 20), round(rng.uniform(0.05, 0.3), 2),
                           rng.randint(0, 4))  # fmt: skip
        item = case(f"holdout_{HOLDOUT_SEED}_{i:02d}", direction(factor), start, days, factor,
                    campaign_id=campaign_id, coupons=coupons, history_days=history)  # fmt: skip
        item["holdout"] = {"seed": HOLDOUT_SEED, "factor": factor, "days": days}
        out.append(item)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="여러 날 캠페인 평가 케이스 (4주 기준선)")
    parser.add_argument("--holdout", action="store_true")
    args = parser.parse_args()
    items = build_holdout() if args.holdout else build_cases()
    folder = HOLDOUT if args.holdout else CASES
    folder.parent.mkdir(exist_ok=True)
    write(folder, items)
    kinds: dict[str, int] = {}
    for item in items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    print(f"multiday4/{folder.name}: {len(items)}개 {kinds}")


if __name__ == "__main__":
    main()
