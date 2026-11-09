"""campaign_effect 매출(증분 판매액) 평가 케이스 만들기.

데이터는 결제·캠페인 평가와 같은 생성기(`eval_world`, shop-api 코드)로 만든다.

    cd backend
    uv run python domains/data/eval/campaign_revenue/make_cases.py            # cases/
    uv run python domains/data/eval/campaign_revenue/make_cases.py --holdout  # holdout/

규칙 (`README.md`):
- 금액 증감은 **합성 주입**이다: 캠페인 구간의 주문 수준 배수(`factor`)와 주문 수량 배수
  (`basket.qty_factor`, 주문 수는 그대로 금액만 늘림)를 곱한다. 기준선은 지난 7일 같은 시간대다.
- **정답 방향은 주입 조건에서 정한다** (factor × qty_factor > 1 이면 up, < 1 이면 down, = 1 이면
  none). 결제 기록 공백(`payment_gap.days_ago`에 0 = 구간)을 심으면 정답은 unknown(payment_gap)이다.
  지난 날 하나에만 공백을 심으면 그날을 기준선에서 빼고 판정해야 한다(정답은 known).
  공백이 아닌 지난 날이 `MIN_BASELINE_DAYS`(3)보다 적으면 정답은 unknown(baseline_short).
- 쿠폰 귀속: 캠페인 쿠폰을 구간 안 주문이 정해진 확률로 쓴다.
- `counted`는 생성기가 만든 기록에서 바로 센 참고값이다(data 에이전트 코드로 세지 않는다):
  결제 완료 주문의 주문 금액(상품가 × 수량, 할인 전) 합. 증분 = 구간 − 공백이 아닌 지난 날 평균.
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

import eval_world  # noqa: E402 - 같은 폴더의 생성기 (결제·캠페인 평가와 공용)

CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"
HOLDOUT_SEED = 2026110401  # 만든 날 YYYYMMDD + 그날 몇 번째
HOLDOUT_COUNT = 12
DAY = datetime(2026, 10, 14, tzinfo=UTC)  # 캠페인 날 (지난 7일 기준선이 들어가는 아무 날)
# 증분을 내려면 기준선 날이 이만큼 있어야 한다 (skill 설정 기본값과 같은 정의, 평가가 정답을 정한다)
MIN_BASELINE_DAYS = 3


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def direction(lift: float) -> str:
    return "up" if lift > 1 else "down" if lift < 1 else "none"


def spec(
    case_id: str, start: datetime, minutes: int, factor: float, *, qty: int = 1,
    gap_days: list[int] | None = None, coupons: dict[str, Any] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """`eval_world.build`가 읽는 시나리오 명세."""
    window = {"from": _iso(start), "to": _iso(start + timedelta(minutes=minutes))}
    if factor > 1:
        injection: dict[str, Any] = {"type": "sale", "factor": factor, **window}
    elif factor < 1:
        injection = {"type": "order_drop", "factor": factor, **window}
    else:
        injection = {"type": "none"}
    out: dict[str, Any] = {
        "id": case_id, "window": window, "traffic": {"orders_per_5m": 5}, "injection": injection,
    }  # fmt: skip
    if qty > 1:
        out["basket"] = {"qty_factor": qty}
    if gap_days:
        out["payment_gap"] = {"days_ago": gap_days}
    if coupons:
        out["campaign"] = coupons
    return out


def counted(
    world: eval_world.World, campaign_id: str | None, gap_days: list[int]
) -> dict[str, Any]:
    """생성기 기록에서 바로 센 값: 결제 완료 주문의 주문 금액 합 (원, 할인 전)."""
    window_from, window_to = world.window
    with world.db.tx() as conn:
        rows = conn.execute(
            "SELECT o.created_at, o.coupon_id, SUM(i.qty * i.unit_price) FROM orders o "
            "JOIN order_items i ON i.order_id = o.order_id WHERE o.status = 'paid' "
            "GROUP BY o.order_id"
        ).fetchall()
        campaign_coupons = {
            r[0]
            for r in conn.execute(
                "SELECT coupon_id FROM coupons WHERE campaign_id = ?", (campaign_id or "",)
            )
        }
    paid = [(eval_world.parse(r[0]), r[1], int(r[2])) for r in rows]

    def gross(start: datetime, end: datetime) -> int:
        return sum(g for at, _, g in paid if start <= at < end)

    past = {
        d: gross(window_from - timedelta(days=d), window_to - timedelta(days=d))
        for d in range(1, eval_world.HISTORY_DAYS + 1)
        if d not in gap_days
    }
    now = gross(window_from, window_to)
    baseline = sum(past.values()) / len(past) if past else None
    direct = sum(g for at, c, g in paid if window_from <= at < window_to and c in campaign_coupons)
    return {
        "gross_actual": now,
        "gross_baseline": round(baseline, 4) if baseline is not None else None,
        "gross_excess": round(now - baseline, 4) if baseline is not None else None,
        "gross_past_days": past,
        "direct_attributed": direct if campaign_id else None,
    }


def case(
    case_id: str, kind: str, start: datetime, minutes: int, factor: float, *, qty: int = 1,
    gap_days: list[int] | None = None, campaign_id: str | None = None,
    coupons: dict[str, Any] | None = None, memo: str = "",
) -> dict[str, Any]:  # fmt: skip
    gaps = gap_days or []
    scenario = spec(case_id, start, minutes, factor, qty=qty, gap_days=gaps, coupons=coupons)
    world = eval_world.build(scenario)
    left = eval_world.HISTORY_DAYS - len([d for d in gaps if d > 0])  # 공백이 아닌 지난 날
    if 0 in gaps:
        sales: dict[str, Any] = {"status": "unknown", "unknown_reason": "payment_gap"}
    elif left < MIN_BASELINE_DAYS:  # 기준선 부족: 증분·방향은 모름 (기간 판매액만)
        sales = {"status": "unknown", "unknown_reason": "baseline_short", "baseline_days": left}
    else:
        sales = {"status": "known", "direction": direction(factor * qty), "lift": factor * qty}
        sales["direct_attributed"] = bool(campaign_id and coupons)
    return {
        "id": case_id,
        "kind": kind,
        "input": {
            "campaign_id": campaign_id,
            "window_from": scenario["window"]["from"],
            "window_to": scenario["window"]["to"],
        },
        "spec": scenario,
        "expected": {"sales": sales},
        "counted": counted(world, campaign_id, gaps),
        "memo": memo,
    }


def _coupons(campaign_id: str, buyers: int, use_fraction: float) -> dict[str, Any]:
    return {"campaign_id": campaign_id, "buyers": buyers, "units_each": 3, "unit_value": 1000,
            "use_fraction": use_fraction, "used_after": 0}  # fmt: skip


def build_cases() -> list[dict[str, Any]]:
    h = lambda hour: DAY.replace(hour=hour)  # noqa: E731
    return [
        case("sale_x2_2h_14h", "up", h(14), 120, 2.0, memo="주문 2배 → 금액도 늘어남"),
        case("basket_x2_2h_10h", "up", h(10), 120, 1.0, qty=2,
             memo="주문 수는 그대로, 수량 2배 → 금액만 늘어남"),
        case("flat_2h_10h", "none", h(10), 120, 1.0, memo="변화 없음 (불확실·같음이 정답)"),
        case("drop_half_2h_19h", "down", h(19), 120, 0.5, memo="주문 절반 → 금액 줄어듦"),
        case("gap_window_1h_14h", "unknown", h(14), 60, 2.0, gap_days=[0],
             memo="구간 주문은 있는데 결제 기록이 없음 → 결제 기록 공백 unknown"),
        case("gap_history_2h_14h", "up", h(14), 120, 2.0, gap_days=[2],
             memo="2일 전 같은 시간대만 결제 공백 → 그날을 빼고 판정"),
        case("coupon_x2_2h_14h", "up", h(14), 120, 2.0, campaign_id="cmp_rev2",
             coupons=_coupons("cmp_rev2", 25, 0.5), memo="캠페인 쿠폰 주문의 금액 합을 따로"),
        case("coupon_flat_2h_10h", "none", h(10), 120, 1.0, campaign_id="cmp_revflat",
             coupons=_coupons("cmp_revflat", 15, 0.4), memo="변화 없음 + 쿠폰 귀속"),
        case("short_baseline_2h_14h", "unknown", h(14), 120, 2.0, gap_days=[1, 2, 3, 4, 5],
             memo="지난 7일 중 5일이 결제 공백 → 기준선 2일(최소 3일) → 증분·방향 unknown"),
    ]  # fmt: skip


def build_holdout() -> list[dict[str, Any]]:
    rng = random.Random(HOLDOUT_SEED)
    out = []
    for i in range(HOLDOUT_COUNT):
        factor = rng.choice((0.5, 0.7, 1.0, 1.5, 2.0, 3.0))
        qty = rng.choice((1, 1, 2))
        start = DAY.replace(hour=rng.randint(9, 20)) - timedelta(days=rng.randint(0, 20))
        minutes = rng.choice((60, 120, 180))
        roll = rng.random()
        gaps = [0] if roll < 0.17 else [rng.randint(1, 7)] if roll < 0.34 else []
        campaign_id = f"cmp_rh{i:02d}" if rng.random() < 0.5 else None
        coupons = (
            _coupons(campaign_id, rng.randint(8, 25), round(rng.uniform(0.2, 0.6), 2))
            if campaign_id else None
        )  # fmt: skip
        kind = "unknown" if 0 in gaps else direction(factor * qty)
        item = case(f"holdout_{HOLDOUT_SEED}_{i:02d}", kind, start, minutes, factor, qty=qty,
                    gap_days=gaps, campaign_id=campaign_id, coupons=coupons)  # fmt: skip
        item["holdout"] = {"seed": HOLDOUT_SEED, "factor": factor, "qty": qty, "gaps": gaps}
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
    parser = argparse.ArgumentParser(description="campaign_effect 매출 평가 케이스 만들기")
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
