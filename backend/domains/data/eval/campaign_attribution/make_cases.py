"""campaign_effect 유입경로 기준 평가 케이스 만들기 (#648).

데이터는 결제·캠페인 평가와 같은 생성기(`eval_world`, shop-api 코드)로 만든다.

    cd backend
    uv run python domains/data/eval/campaign_attribution/make_cases.py            # cases/
    uv run python domains/data/eval/campaign_attribution/make_cases.py --holdout  # holdout/

규칙 (`README.md`):
- 유입 표시는 **합성 주입**이다(`attribution.tags`): 구간 안 주문마다 정해진 비율로 출처·매체·
  campaign_id 태그 하나를 붙인다(shop-api `src_source`·`src_medium`·`src_campaign`). 다른 캠페인
  태그도 같은 방식으로 심는다.
- **정답은 조건과 생성기 기록에서 정한다**:
  - 구간에 유입 표시가 있는 주문이 하나도 없으면 unknown(no_tags)
  - 아니면 known. 요청 campaign_id로 들어온 결제 완료 주문의 수·판매액(단가 × 수량, 할인 전)을
    출처별로 센 값(`counted`)이 정답이다. 다른 캠페인 태그 주문은 세지 않는다
  - 유입 표시가 있는 주문 비율 = 유입 표시(어느 캠페인이든)가 있는 결제 완료 주문
    ÷ 구간 결제 완료 주문
  - 관련 상품을 같이 요청하면: 이 캠페인으로 들어온 결제 완료 주문 중 그 상품 품목만 센 주문 수·
    수량·판매액(겹침)
- 직접 귀속이라 기준선과 비교하지 않는다(방향 정답 없음).
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
HOLDOUT_SEED = 2026110601  # 만든 날 YYYYMMDD + 그날 몇 번째
HOLDOUT_COUNT = 10
DAY = datetime(2026, 10, 14, tzinfo=UTC)
SOURCES = (("instagram", "social"), ("google", "cpc"), ("naver", "blog"), ("kakao", "message"))
NO_SOURCE = "(출처 없음)"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tag(source: str, medium: str, campaign_id: str, fraction: float) -> dict[str, Any]:
    return {"source": source, "medium": medium, "campaign_id": campaign_id, "fraction": fraction}


def spec(
    case_id: str, start: datetime, minutes: int, tags: list[dict[str, Any]], *,
    boost: tuple[str, float] | None = None,
) -> dict[str, Any]:  # fmt: skip
    window = {"from": _iso(start), "to": _iso(start + timedelta(minutes=minutes))}
    out: dict[str, Any] = {
        "id": case_id, "window": window, "traffic": {"orders_per_5m": 5},
        "injection": {"type": "none"},
    }  # fmt: skip
    if tags:
        out["attribution"] = {"tags": tags}
    if boost:
        out["product_boost"] = {"product_id": boost[0], "factor": boost[1]}
    return out


def counted(
    world: eval_world.World, campaign_id: str, products: list[str]
) -> dict[str, Any]:
    """생성기 기록에서 바로 센 값 (data 에이전트 코드가 아님). 결제 완료 주문만."""
    window_from, window_to = world.window
    with world.db.tx() as conn:
        rows = conn.execute(
            "SELECT o.order_id, o.created_at, o.src_source, o.src_campaign,"
            " i.product_id, i.qty, i.unit_price FROM orders o"
            " JOIN order_items i ON i.order_id = o.order_id WHERE o.status = 'paid'"
        ).fetchall()
    orders: dict[str, dict[str, Any]] = {}
    for oid, at, source, camp, pid, qty, price in rows:
        if not window_from <= eval_world.parse(at) < window_to:
            continue
        o = orders.setdefault(oid, {"source": source, "campaign": camp, "lines": []})
        o["lines"].append((pid, int(qty), int(price)))
    mine = [o for o in orders.values() if o["campaign"] == campaign_id]
    tagged = [o for o in orders.values() if o["campaign"] or o["source"]]
    by_source: dict[str, dict[str, int]] = {}
    for o in mine:
        row = by_source.setdefault(o["source"] or NO_SOURCE, {"orders": 0, "gross_krw": 0})
        row["orders"] += 1
        row["gross_krw"] += sum(q * p for _, q, p in o["lines"])
    out: dict[str, Any] = {
        "window_orders": len(orders),
        "tagged_orders": len(tagged),
        "tagged_share": round(len(tagged) / len(orders), 4) if orders else None,
        "orders": len(mine),
        "gross_krw": sum(r["gross_krw"] for r in by_source.values()),
        "by_source": dict(sorted(by_source.items())),
        "other_campaign_orders": sum(
            1 for o in orders.values() if o["campaign"] and o["campaign"] != campaign_id
        ),
    }
    if products:
        hits = [[ln for ln in o["lines"] if ln[0] in products] for o in mine]
        hits = [h for h in hits if h]
        out["overlap"] = {
            "orders": len(hits),
            "units": sum(q for h in hits for _, q, _ in h),
            "gross_krw": sum(q * p for h in hits for _, q, p in h),
        }
    return out


def case(
    case_id: str, kind: str, start: datetime, minutes: int, campaign_id: str,
    tags: list[dict[str, Any]], *, products: list[str] | None = None,
    boost: tuple[str, float] | None = None, memo: str = "",
) -> dict[str, Any]:  # fmt: skip
    scenario = spec(case_id, start, minutes, tags, boost=boost)
    world = eval_world.build(scenario)
    got = counted(world, campaign_id, products or [])
    if got["tagged_orders"] == 0:
        want: dict[str, Any] = {"status": "unknown", "unknown_reason": "no_tags"}
    else:
        want = {"status": "known"}
    expected: dict[str, Any] = {"attribution": want}
    if products:
        expected["overlap"] = (
            {"status": "unknown", "unknown_reason": "no_tags"} if want["status"] == "unknown"
            else {"status": "known"}
        )  # fmt: skip
    request: dict[str, Any] = {
        "campaign_id": campaign_id,
        "window_from": scenario["window"]["from"],
        "window_to": scenario["window"]["to"],
    }
    if products:
        request["products"] = products
    return {
        "id": case_id, "kind": kind, "input": request, "spec": scenario,
        "expected": expected, "counted": got, "memo": memo,
    }  # fmt: skip


def build_cases() -> list[dict[str, Any]]:
    h = lambda hour: DAY.replace(hour=hour)  # noqa: E731
    me = "cmp_attr"
    return [
        case("tagged_two_sources_2h_14h", "known", h(14), 120, me,
             [_tag("instagram", "social", me, 0.2), _tag("google", "cpc", me, 0.1)],
             memo="이 캠페인 태그 두 출처 (인스타 20%, 구글 10%)"),
        case("no_tags_2h_10h", "unknown", h(10), 120, me, [],
             memo="구간에 유입 표시가 있는 주문이 하나도 없음 → 유입 기록 없음"),
        case("other_campaign_only_2h_14h", "known", h(14), 120, me,
             [_tag("naver", "blog", "cmp_other", 0.3)],
             memo="다른 캠페인 태그만 있음 → 이 캠페인 주문 0건 (다른 캠페인은 세지 않음)"),
        case("mixed_campaigns_2h_19h", "known", h(19), 120, me,
             [_tag("instagram", "social", me, 0.15), _tag("naver", "blog", "cmp_other", 0.15)],
             memo="이 캠페인 15%, 다른 캠페인 15% → 이 캠페인 것만"),
        case("overlap_products_2h_14h", "known", h(14), 120, me,
             [_tag("instagram", "social", me, 0.3)], products=["prd_0004"],
             boost=("prd_0004", 2.0),
             memo="유입 + 관련 상품(무지 노트, 2배) → 이 캠페인으로 들어와 그 상품을 산 주문"),
        case("overlap_no_tags_1h_10h", "unknown", h(10), 60, me, [], products=["prd_0004"],
             memo="관련 상품만 있고 유입 표시 없음 → 유입·겹침 모두 unknown"),
    ]  # fmt: skip


def build_holdout() -> list[dict[str, Any]]:
    rng = random.Random(HOLDOUT_SEED)
    out = []
    for i in range(HOLDOUT_COUNT):
        me = f"cmp_ah{i:02d}"
        tags = []
        if rng.random() < 0.85:
            for source, medium in rng.sample(SOURCES, rng.randint(1, 3)):
                camp = me if rng.random() < 0.7 else f"cmp_ox{i:02d}"
                tags.append(_tag(source, medium, camp, round(rng.uniform(0.05, 0.25), 2)))
        products = ["prd_0004"] if rng.random() < 0.4 else None
        start = DAY.replace(hour=rng.randint(9, 20)) - timedelta(days=rng.randint(0, 20))
        minutes = rng.choice((60, 120, 180))
        kind = "known" if tags else "unknown"
        item = case(f"holdout_{HOLDOUT_SEED}_{i:02d}", kind, start, minutes, me, tags,
                    products=products)  # fmt: skip
        item["holdout"] = {"seed": HOLDOUT_SEED, "tags": len(tags)}
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
    parser = argparse.ArgumentParser(description="campaign_effect 유입경로 평가 케이스 만들기")
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
