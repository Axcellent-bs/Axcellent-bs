"""campaign_effect 상품 기준 평가 케이스 만들기 (#646).

데이터는 결제·캠페인 평가와 같은 생성기(`eval_world`, shop-api 코드)로 만든다.

    cd backend
    uv run python domains/data/eval/campaign_products/make_cases.py            # cases/
    uv run python domains/data/eval/campaign_products/make_cases.py --holdout  # holdout/

규칙 (`README.md`):
- 상품 하나만 늘림은 **합성 주입**이다(`product_boost`): 구간 안에서 그 상품 주문을 더 넣어 그
  상품의 기대 주문 수를 `factor`배로 만든다. 가게 전체 증감(`injection` sale·order_drop)은 모든
  상품에 같이 곱해진다. 기준선은 지난 7일 같은 시간대다.
- **정답 방향은 주입 조건에서 정한다.** 요청한 상품의 배수 = 가게 전체 배수 × (그 상품을 늘렸으면
  boost 배수, 아니면 1). 다른 상품만 늘렸으면 요청 상품은 가게 배수 그대로다(변화 없음이 정답).
  주문 수·판매 수량·판매액 세 칸 모두 같은 방향이다(같은 상품, 같은 수량 분포).
- 이름 매칭: 카탈로그 이름과 정규화(NFKC, 소문자, 공백 모두 뺌)한 값이 정확히 같을 때만 맞는다.
  일부만 같은 이름("무지 노트")은 맞지 않는다(추측하지 않음). 맞는 상품이 하나도 없으면 정답은
  unknown(no_match). 정답 매칭 결과는 카탈로그 시드(`PRODUCTS`)로 여기서 정한다.
- 결제 기록 공백(`payment_gap.days_ago`에 0 = 구간)이면 정답은 unknown(payment_gap). 공백이 아닌
  지난 날이 `MIN_BASELINE_DAYS`(3)보다 적으면 unknown(baseline_short).
- `counted`는 생성기 기록에서 바로 센 참고값이다(data 에이전트 코드로 세지 않는다): 결제 완료
  주문 중 맞은 상품이 든 주문 수, 그 상품 수량 합, 그 상품 금액(단가 × 수량) 합.
  증분 = 구간 − 공백이 아닌 지난 날 평균.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
sys.path.insert(0, str(EVAL))

import eval_world  # noqa: E402 - 같은 폴더의 생성기 (결제·캠페인 평가와 공용)

CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"
HOLDOUT_SEED = 2026110501  # 만든 날 YYYYMMDD + 그날 몇 번째
HOLDOUT_COUNT = 12
DAY = datetime(2026, 10, 14, tzinfo=UTC)  # 캠페인 날 (지난 7일 기준선이 들어가는 아무 날)
MIN_BASELINE_DAYS = 3  # 증분을 내려면 기준선 날이 이만큼 (매출 평가와 같은 정의)
PARTS = ("orders", "units", "gross")  # 주문 수, 판매 수량, 판매액(주문 금액, 할인 전)
CATALOG = {pid: name for pid, _seller, name, *_ in eval_world.PRODUCTS}


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def direction(lift: float) -> str:
    return "up" if lift > 1 else "down" if lift < 1 else "none"


def normalize(text: str) -> str:
    """이름 비교용: NFKC, 소문자, 공백 모두 뺌."""
    return "".join(unicodedata.normalize("NFKC", text).lower().split())


def match(products: list[str]) -> tuple[list[str], list[str]]:
    """요청 값 → (맞은 상품 id, 못 맞춘 값). id가 정확히 같거나 정규화한 이름이 정확히 같을 때만."""
    by_name = {normalize(name): pid for pid, name in CATALOG.items()}
    matched: list[str] = []
    unmatched: list[str] = []
    for value in products:
        pid = value if value in CATALOG else by_name.get(normalize(value))
        if pid is None:
            unmatched.append(value)
        elif pid not in matched:
            matched.append(pid)
    return matched, unmatched


def spec(
    case_id: str, start: datetime, minutes: int, factor: float, *,
    boost: tuple[str, float] | None = None, gap_days: list[int] | None = None,
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
    if boost:
        out["product_boost"] = {"product_id": boost[0], "factor": boost[1]}
    if gap_days:
        out["payment_gap"] = {"days_ago": gap_days}
    return out


def counted(world: eval_world.World, matched: list[str], gap_days: list[int]) -> dict[str, Any]:
    """생성기 기록에서 바로 센 값: 결제 완료 주문 중 맞은 상품의 주문 수·수량·금액."""
    window_from, window_to = world.window
    marks = ",".join("?" for _ in matched) or "''"
    with world.db.tx() as conn:
        rows = conn.execute(
            "SELECT o.created_at, o.order_id, SUM(i.qty), SUM(i.qty * i.unit_price) FROM orders o "
            "JOIN order_items i ON i.order_id = o.order_id WHERE o.status = 'paid' "
            f"AND i.product_id IN ({marks}) GROUP BY o.order_id",  # noqa: S608 - 자리표시자만
            matched,
        ).fetchall()
    paid = [(eval_world.parse(r[0]), int(r[2]), int(r[3])) for r in rows]

    def tally(start: datetime, end: datetime) -> dict[str, int]:
        inside = [(u, g) for at, u, g in paid if start <= at < end]
        return {"orders": len(inside), "units": sum(u for u, _ in inside),
                "gross": sum(g for _, g in inside)}  # fmt: skip

    past = {
        d: tally(window_from - timedelta(days=d), window_to - timedelta(days=d))
        for d in range(1, eval_world.HISTORY_DAYS + 1)
        if d not in gap_days
    }
    now = tally(window_from, window_to)
    out: dict[str, Any] = {"actual": now, "past_days": past}
    for part in PARTS:
        if past:
            base = sum(p[part] for p in past.values()) / len(past)
            out[f"{part}_baseline"] = round(base, 4)
            out[f"{part}_excess"] = round(now[part] - base, 4)
        else:
            out[f"{part}_baseline"] = out[f"{part}_excess"] = None
    return out


def case(
    case_id: str, kind: str, start: datetime, minutes: int, factor: float, products: list[str], *,
    boost: tuple[str, float] | None = None, gap_days: list[int] | None = None, memo: str = "",
) -> dict[str, Any]:  # fmt: skip
    gaps = gap_days or []
    scenario = spec(case_id, start, minutes, factor, boost=boost, gap_days=gaps)
    world = eval_world.build(scenario)
    matched, unmatched = match(products)
    left = eval_world.HISTORY_DAYS - len([d for d in gaps if d > 0])  # 공백이 아닌 지난 날
    want: dict[str, Any] = {"matched": matched, "unmatched": unmatched}
    if not matched:
        want |= {"status": "unknown", "unknown_reason": "no_match"}
    elif 0 in gaps:
        want |= {"status": "unknown", "unknown_reason": "payment_gap"}
    elif left < MIN_BASELINE_DAYS:
        want |= {"status": "unknown", "unknown_reason": "baseline_short", "baseline_days": left}
    else:
        lift = factor * (boost[1] if boost and boost[0] in matched else 1.0)
        want |= {"status": "known", "lift": lift, **{p: direction(lift) for p in PARTS}}
    return {
        "id": case_id,
        "kind": kind,
        "input": {
            "campaign_id": None,
            "window_from": scenario["window"]["from"],
            "window_to": scenario["window"]["to"],
            "products": products,
        },
        "spec": scenario,
        "expected": {"products": want},
        "counted": counted(world, matched, gaps),
        "memo": memo,
    }


def build_cases() -> list[dict[str, Any]]:
    h = lambda hour: DAY.replace(hour=hour)  # noqa: E731
    return [
        case("product_up_x3_2h_14h", "up", h(14), 120, 1.0, ["prd_0004"],
             boost=("prd_0004", 3.0), memo="그 상품만 3배 (상품 id로 요청)"),
        case("product_by_name_x3_2h_10h", "up", h(10), 120, 1.0, [" 소설 베스트  3종"],
             boost=("prd_0007", 3.0), memo="그 상품만 3배 (이름으로 요청, 공백이 달라도 같음)"),
        case("other_product_up_2h_14h", "none", h(14), 120, 1.0, ["prd_0004"],
             boost=("prd_0005", 3.0), memo="다른 상품만 3배 → 요청 상품은 변화 없음이 정답"),
        case("flat_2h_10h", "none", h(10), 120, 1.0, ["prd_0001"], memo="아무것도 늘리지 않음"),
        case("store_drop_half_2h_19h", "down", h(19), 120, 0.5, ["prd_0002"],
             memo="가게 전체 주문 절반 → 요청 상품도 줄어듦"),
        case("name_unmatched_2h_14h", "unknown", h(14), 120, 1.0, ["무지 노트", "없는 상품"],
             boost=("prd_0004", 3.0),
             memo="일부만 같은 이름·없는 이름 → 추측하지 않고 둘 다 못 맞춤, 상품 기준 unknown"),
        case("partly_unmatched_x2_2h_14h", "up", h(14), 120, 1.0, ["prd_0004", "없는 상품"],
             boost=("prd_0004", 2.0), memo="맞은 상품으로만 계산하고 못 맞춘 값은 따로 돌려줌"),
        case("gap_window_1h_14h", "unknown", h(14), 60, 1.0, ["prd_0004"],
             boost=("prd_0004", 3.0), gap_days=[0],
             memo="구간 주문은 있는데 결제 기록이 없음 → 결제 기록 공백 unknown"),
        case("short_baseline_2h_14h", "unknown", h(14), 120, 1.0, ["prd_0004"],
             boost=("prd_0004", 3.0), gap_days=[1, 2, 3, 4, 5],
             memo="지난 7일 중 5일이 결제 공백 → 기준선 2일(최소 3일) → unknown"),
    ]  # fmt: skip


def build_holdout() -> list[dict[str, Any]]:
    rng = random.Random(HOLDOUT_SEED)
    ids = list(CATALOG)
    out = []
    for i in range(HOLDOUT_COUNT):
        factor = rng.choice((0.5, 1.0, 1.0, 1.5))
        boosted = rng.choice(ids)
        boost = rng.choice((1.0, 2.0, 3.0))
        asked = boosted if rng.random() < 0.6 else rng.choice([p for p in ids if p != boosted])
        by_name = rng.random() < 0.3
        products = [CATALOG[asked] if by_name else asked]
        start = DAY.replace(hour=rng.randint(9, 20)) - timedelta(days=rng.randint(0, 20))
        minutes = rng.choice((60, 120, 180))
        gaps = [0] if rng.random() < 0.15 else []
        lift = factor * (boost if asked == boosted else 1.0)
        kind = "unknown" if gaps else direction(lift)
        item = case(f"holdout_{HOLDOUT_SEED}_{i:02d}", kind, start, minutes, factor, products,
                    boost=(boosted, boost) if boost > 1 else None, gap_days=gaps)  # fmt: skip
        item["holdout"] = {"seed": HOLDOUT_SEED, "factor": factor, "boost": boost,
                           "boosted_is_asked": asked == boosted, "gaps": gaps}  # fmt: skip
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
    parser = argparse.ArgumentParser(description="campaign_effect 상품 기준 평가 케이스 만들기")
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
