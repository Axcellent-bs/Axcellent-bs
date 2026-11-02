"""campaign_effect 평가 케이스 만들기.

데이터는 결제 평가와 같은 생성기(`eval_world`, shop-api 코드)로 만든다.

    cd backend
    uv run python domains/data/eval/campaign/make_cases.py            # cases/
    uv run python domains/data/eval/campaign/make_cases.py --holdout  # holdout/ (결과 숫자만 본다)

규칙은 `README.md`. 요약:
- 주문 증감은 **합성 주입**이다: 캠페인 구간의 주문 수준에 정해진 배수를 곱한다
  (`injection.type` sale = 늘림, order_drop = 줄임, none = 그대로). 기준선은 지난 7일 같은 시간대다.
- **정답(방향·배수)은 주입 조건에서 정한다.** 데이터를 보고 정하지 않는다.
- 쿠폰: 지난 7일보다 먼저 캠페인 쿠폰을 발급하고, 구간 안 주문이 정해진 확률로 쓴다. 구간이 끝난 뒤
  쓰는 몫(`used_after`)도 넣어 "조회 시점 누적 사용률에 캠페인 뒤 사용분이 섞인다"를
  잴 수 있게 한다.
  발급 장수·구간 뒤 사용 장수는 조건에서, 실제로 쓴 장수는 생성기가 만든 주문 기록에서
  센다(`counted`).
- `counted`는 생성기가 만든 기록에서 바로 센 참고값이다(data 에이전트 코드로 세지 않는다).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
sys.path.insert(0, str(EVAL))

import eval_world  # noqa: E402 - 같은 폴더의 생성기 (결제 평가와 공용)

CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"
HOLDOUT_SEED = 2026110203  # 만든 날 YYYYMMDD + 그날 몇 번째 (trend 평가 01·02 다음)
HOLDOUT_COUNT = 12
HOLDOUT_FACTORS = (0.5, 0.7, 1.0, 1.5, 2.0, 3.0)
DAY = datetime(2026, 10, 14, tzinfo=UTC)  # 캠페인 날 (지난 7일 기준선이 들어가는 아무 날)
NOTE_CUMULATIVE = "조회 시점 기준 누적 사용률이며 캠페인 뒤 사용분도 섞인다"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def direction(factor: float) -> str:
    return "up" if factor > 1 else "down" if factor < 1 else "none"


# ---------------------------------------------------------------- 케이스 한 개


def spec(
    case_id: str,
    start: datetime,
    minutes: int,
    factor: float,
    coupons: dict[str, Any] | None,
    *,
    empty_history: bool = False,
) -> dict[str, Any]:
    """`eval_world.build`가 읽는 시나리오 명세."""
    end = start + timedelta(minutes=minutes)
    window = {"from": _iso(start), "to": _iso(end)}
    if empty_history:
        injection: dict[str, Any] = {"type": "empty", "history_empty": True}
    elif factor > 1:
        injection = {"type": "sale", "factor": factor, **window}
    elif factor < 1:
        injection = {"type": "order_drop", "factor": factor, **window}
    else:
        injection = {"type": "none"}
    out: dict[str, Any] = {
        "id": case_id,
        "window": window,
        "traffic": {"orders_per_5m": 5},
        "injection": injection,
    }
    if coupons:
        out["campaign"] = coupons
    return out


def counted(world: eval_world.World, campaign_id: str | None) -> dict[str, Any]:
    """생성기가 만든 기록에서 바로 센 값 (참고·채점용, data 에이전트 코드가 아님)."""
    window_from, window_to = world.window
    ranges = [
        (window_from - timedelta(days=d), window_to - timedelta(days=d))
        for d in range(1, eval_world.HISTORY_DAYS + 1)
    ]

    def orders_in(start: datetime, end: datetime) -> list[eval_world.Payment]:
        return [p for p in world.payments if start <= p.at < end]

    now = orders_in(window_from, window_to)
    past = [orders_in(s, e) for s, e in ranges]
    revenue = lambda rows: sum(p.amount for p in rows if p.failure_code is None)  # noqa: E731
    out: dict[str, Any] = {
        "orders_actual": len(now),
        "orders_baseline": round(sum(len(p) for p in past) / len(past), 4),
        "revenue_actual": revenue(now),  # 결제 성공 금액 합 (원)
        "revenue_baseline": round(sum(revenue(p) for p in past) / len(past), 4),
        "history_days_with_orders": sum(1 for p in past if p),
    }
    if campaign_id:
        with world.db.tx() as conn:
            issued = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM coupons WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT o.created_at, o.coupon_units FROM orders o JOIN coupons c "
                "ON o.coupon_id = c.coupon_id WHERE c.campaign_id = ? AND o.status != 'cancelled'",
                (campaign_id,),
            ).fetchall()
        used_in = sum(int(r[1]) for r in rows if window_from <= eval_world.parse(r[0]) < window_to)
        used_after = sum(int(r[1]) for r in rows if eval_world.parse(r[0]) >= window_to)
        out |= {
            "coupon_issued_units": int(issued),
            "coupon_used_in_window": used_in,
            "coupon_used_after": used_after,
            "coupon_used_units": used_in + used_after,  # 조회 시점 누적 (구간 뒤 포함)
            "coupon_use_rate": round((used_in + used_after) / issued, 4) if issued else None,
        }
    return out


def case(
    case_id: str,
    kind: str,
    start: datetime,
    minutes: int,
    factor: float,
    *,
    campaign_id: str | None,
    coupons: dict[str, Any] | None,
    empty_history: bool = False,
    memo: str = "",
) -> dict[str, Any]:
    scenario = spec(case_id, start, minutes, factor, coupons, empty_history=empty_history)
    world = eval_world.build(scenario)
    end = start + timedelta(minutes=minutes)
    if empty_history:
        orders: dict[str, Any] = {"status": "unknown", "unknown_reason": "no_history"}
        revenue: dict[str, Any] = {"status": "unknown", "unknown_reason": "no_history"}
    else:
        orders = {"status": "known", "direction": direction(factor), "multiplier": factor}
        # 매출 방향은 주문 방향을 따른다(같은 상품 분포). 쿠폰 할인만큼 배수는 주문보다 작을 수 있다
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
            "note": NOTE_CUMULATIVE,  # 출력에 이 설명이 있어야 한다
        }
    return {
        "id": case_id,
        "kind": kind,
        "input": {"campaign_id": campaign_id, "window_from": _iso(start), "window_to": _iso(end)},
        "spec": scenario,
        "expected": {"orders": orders, "revenue": revenue, "coupon": coupon},
        "counted": counted(world, campaign_id),
        "memo": memo,
    }


def _coupons(campaign_id: str, buyers: int, use_fraction: float, used_after: int) -> dict[str, Any]:
    return {"campaign_id": campaign_id, "buyers": buyers, "units_each": 3, "unit_value": 1000,
            "use_fraction": use_fraction, "used_after": used_after}  # fmt: skip


# ---------------------------------------------------------------- cases/ · holdout/


def build_cases() -> list[dict[str, Any]]:
    h = lambda hour: DAY.replace(hour=hour)  # noqa: E731
    return [
        case("up_x2_2h_14h", "up", h(14), 120, 2.0, campaign_id="cmp_up2",
             coupons=_coupons("cmp_up2", 20, 0.3, 3), memo="주문 2배 + 쿠폰, 구간 뒤 3장 더 씀"),
        case("flat_2h_10h", "none", h(10), 120, 1.0, campaign_id="cmp_flat",
             coupons=_coupons("cmp_flat", 15, 0.3, 0), memo="주문 변화 없음 + 쿠폰"),
        case("down_half_2h_19h", "down", h(19), 120, 0.5, campaign_id="cmp_down",
             coupons=_coupons("cmp_down", 15, 0.3, 2), memo="주문 절반 + 쿠폰, 구간 뒤 2장"),
        case("up_x3_30m_10h", "up", h(10), 30, 3.0, campaign_id="cmp_up3",
             coupons=_coupons("cmp_up3", 10, 0.6, 0), memo="짧은 캠페인(30분) 주문 3배"),
        case("up_x2_no_campaign_id_1h_14h", "up", h(14), 60, 2.0, campaign_id=None, coupons=None,
             memo="campaign_id 없이 요청 → 주문·매출만, 쿠폰은 확인할 수 없음"),
        case("flat_no_coupons_2h_10h", "none", h(10), 120, 1.0, campaign_id="cmp_nocoupon",
             coupons=None, memo="campaign_id는 있지만 그 캠페인 쿠폰이 없음 → 쿠폰 확인할 수 없음"),
        case("no_history_1h_14h", "unknown", h(14), 60, 1.0, campaign_id="cmp_empty", coupons=None,
             empty_history=True, memo="구간·지난 7일 주문 기록이 없음 → 주문·매출 확인할 수 없음"),
    ]  # fmt: skip


def build_holdout() -> list[dict[str, Any]]:
    rng = random.Random(HOLDOUT_SEED)
    out = []
    for i in range(HOLDOUT_COUNT):
        factor = rng.choice(HOLDOUT_FACTORS)
        start = DAY.replace(hour=rng.randint(9, 20)) - timedelta(days=rng.randint(0, 20))
        minutes = rng.choice((60, 120, 180))
        campaign_id = f"cmp_h{i:02d}"
        coupons = _coupons(campaign_id, rng.randint(8, 20), round(rng.uniform(0.1, 0.6), 2),
                           rng.randint(0, 4))  # fmt: skip
        item = case(f"holdout_{HOLDOUT_SEED}_{i:02d}", direction(factor), start, minutes, factor,
                    campaign_id=campaign_id, coupons=coupons)  # fmt: skip
        item["holdout"] = {"seed": HOLDOUT_SEED, "factor": factor, "minutes": minutes}
        out.append(item)
    return out


def write(folder: Path, items: list[dict[str, Any]]) -> None:
    folder.mkdir(exist_ok=True)
    for old in folder.glob("*.json"):
        old.unlink()
    for item in items:
        text = json.dumps(item, ensure_ascii=False, indent=1) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="campaign_effect 평가 케이스 만들기")
    parser.add_argument("--holdout", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    items = build_holdout() if args.holdout else build_cases()
    folder = args.out or (HOLDOUT if args.holdout else CASES)
    write(folder, items)
    kinds: dict[str, int] = {}
    for item in items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    print(f"{folder.name}: {len(items)}개 {kinds}")


if __name__ == "__main__":
    main()
