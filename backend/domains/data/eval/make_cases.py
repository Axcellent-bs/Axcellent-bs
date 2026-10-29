"""정답지(`cases/*.json`)를 만든다: uv run python domains/data/eval/make_cases.py

시나리오 하나 = 입력 구간 + 주입한 상황 + 주입한 원인 + 정답.
정답은 지어내지 않는다. 시나리오를 실제 shop-api 코드로 만든 뒤(`eval_world.build`) 그 결제
기록에서 센다. 판매자 케이스의 라벨은 주입한 판매자 사실(사기 패턴인지)이다.
명세를 바꾸면 이 파일을 다시 돌려 정답을 다시 계산한다. 테스트가 정답과 다시 센 값이 같은지 본다.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_world  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
WINDOW_MINUTES = 60


def _window(day: int, hour: int) -> dict[str, str]:
    start = eval_world.parse(f"2026-09-{day:02d}T{hour:02d}:00:00Z")
    return {"from": eval_world.iso(start), "to": eval_world.iso(start + timedelta(minutes=60))}


def _block(window: dict[str, str], start_min: int, minutes: int) -> dict[str, str]:
    start = eval_world.parse(window["from"]) + timedelta(minutes=start_min)
    return {"from": eval_world.iso(start), "to": eval_world.iso(start + timedelta(minutes=minutes))}


def _fault(cid, day, hour, start, minutes, fraction, cause, llm="grounded", note=""):  # noqa: ANN001, ANN202
    window = _window(day, hour)
    return {
        "id": cid,
        "kind": "payment_fault",
        "description": note or f"결제 장애 {minutes}분, 세기 {int(fraction * 100)}%, {hour}시",
        "window": window,
        "traffic": {"orders_per_5m": 5},
        "injection": {
            "type": "payment_failure",
            **_block(window, start, minutes),
            "fraction": fraction,
        },
        "cause": cause,
        "llm": llm,
    }


def _shape(cid, kind, day, hour, injection, cause, llm="grounded", note=""):  # noqa: ANN001, ANN202
    window = _window(day, hour)
    if "start" in injection:
        block = _block(window, injection.pop("start"), injection.pop("minutes"))
        injection = {**injection, **block}
    return {
        "id": cid,
        "kind": kind,
        "description": note,
        "window": window,
        "traffic": {"orders_per_5m": 5},
        "injection": injection,
        "cause": cause,
        "llm": llm,
    }


DEPLOY = {"type": "deploy", "minutes_before": 8}
DEPLOY_WITH_OLD = {"type": "deploy", "minutes_before": 6, "older_deploy_minutes_before": 27}
NOTICE = {"type": "notice", "minutes_before": 12}
CAMPAIGN = {"type": "campaign", "minutes_before": 5}
NONE = {"type": "none"}

SPECS: list[dict[str, Any]] = [
    # 결제 장애: 길이·세기·시간대를 다르게
    _fault("pay_full_20m_10h", 1, 10, 20, 20, 1.0, DEPLOY),
    _fault("pay_full_10m_19h", 2, 19, 25, 10, 1.0, DEPLOY, "hallucinate"),
    _fault("pay_full_45m_10h_two_deploys", 3, 10, 10, 45, 1.0, DEPLOY_WITH_OLD),
    _fault("pay_half_20m_14h", 4, 14, 20, 20, 0.5, NOTICE),
    _fault("pay_weak20_20m_10h", 5, 10, 20, 20, 0.2, NONE),
    _fault("pay_weak20_45m_19h", 6, 19, 10, 45, 0.2, DEPLOY, "fail"),
    _fault(
        "pay_full_20m_03h_night", 7, 3, 20, 20, 1.0, DEPLOY, note="결제 장애 20분, 새벽(주문 적음)"
    ),
    _fault("pay_half_10m_03h_night", 8, 3, 30, 10, 0.5, NONE, "hallucinate"),
    _fault("pay_full_20m_10h_two_deploys_rule", 9, 10, 20, 20, 1.0, DEPLOY_WITH_OLD, "fail"),
    # 주문 급감 (결제는 정상)
    _shape(
        "order_drop_70_30m_10h",
        "order_drop",
        10,
        10,
        {"type": "order_drop", "start": 20, "minutes": 30, "factor": 0.3},
        DEPLOY,
        note="주문 70% 급감 30분 (결제 정상)",
    ),
    _shape(
        "order_drop_90_20m_19h",
        "order_drop",
        11,
        19,
        {"type": "order_drop", "start": 25, "minutes": 20, "factor": 0.1},
        NONE,
        note="주문 90% 급감 20분",
    ),
    _shape(
        "order_drop_50_45m_14h",
        "order_drop",
        12,
        14,
        {"type": "order_drop", "start": 10, "minutes": 45, "factor": 0.5},
        NOTICE,
        note="주문 50% 감소 45분",
    ),
    # 장애 없는 평소 구간 (오탐 확인)
    _shape("normal_10h", "normal", 13, 10, {"type": "none"}, NONE, note="평소 10시"),
    _shape("normal_19h", "normal", 14, 19, {"type": "none"}, NONE, "hallucinate", note="평소 19시"),
    _shape("normal_03h_night", "normal", 15, 3, {"type": "none"}, NONE, note="평소 새벽 3시"),
    _shape("normal_14h", "normal", 16, 14, {"type": "none"}, NONE, "fail", note="평소 14시"),
    # 세일: 평소보다 주문이 많지만 정상
    _shape(
        "sale_x2_10h",
        "sale",
        17,
        10,
        {"type": "sale", "start": 0, "minutes": 60, "factor": 2.0},
        CAMPAIGN,
        note="세일 주문 2배 (정상)",
    ),
    _shape(
        "sale_x3_19h",
        "sale",
        18,
        19,
        {"type": "sale", "start": 0, "minutes": 60, "factor": 3.0},
        CAMPAIGN,
        note="세일 주문 3배 (정상)",
    ),
    _shape(
        "sale_x4_burst_14h",
        "sale",
        19,
        14,
        {"type": "sale", "start": 15, "minutes": 30, "factor": 4.0},
        CAMPAIGN,
        note="타임세일 30분 주문 4배 (정상)",
    ),
    # 데이터가 비어 있는 구간
    _shape(
        "empty_window_10h",
        "empty",
        20,
        10,
        {"type": "empty"},
        NONE,
        note="구간에 주문·결제가 하나도 없음 (지난 7일은 있음)",
    ),
    _shape(
        "empty_all_10h",
        "empty",
        21,
        10,
        {"type": "empty", "history_empty": True},
        NONE,
        note="구간과 지난 7일 모두 데이터 없음",
    ),
]

# 판매자 케이스: 주입한 판매자 사실과 라벨. 사기 패턴 = 신규 + 저가 덤핑 + 대량 등록(+ 중복 사업자)
SELLERS: list[dict[str, Any]] = [
    {
        "id": "seller_fraud_dump",
        "label": "fraud",
        "description": "가입 2일, 상품 80% 저가, 24시간 120개 등록, 중복 사업자",
        "facts": {
            "joined_days": 2,
            "low_price_ratio": 0.8,
            "business_hash_duplicated": True,
            "listings_last_24h": 120,
            "max_fraud_card_similarity": 0.3,
        },
    },
    {
        "id": "seller_fraud_copy",
        "label": "fraud",
        "description": "가입 1일, 지난 사기 판매자와 매우 비슷, 저가 60%",
        "facts": {
            "joined_days": 1,
            "low_price_ratio": 0.6,
            "business_hash_duplicated": False,
            "listings_last_24h": 70,
            "max_fraud_card_similarity": 0.9,
        },
    },
    {
        "id": "seller_fraud_quiet",
        "label": "fraud",
        "description": "가입 5일, 중복 사업자, 저가 50%, 등록은 적음",
        "facts": {
            "joined_days": 5,
            "low_price_ratio": 0.5,
            "business_hash_duplicated": True,
            "listings_last_24h": 12,
            "max_fraud_card_similarity": 0.4,
        },
    },
    {
        "id": "seller_new_honest",
        "label": "normal",
        "description": "가입 3일 신규, 가격 정상, 등록 20개",
        "facts": {
            "joined_days": 3,
            "low_price_ratio": 0.05,
            "business_hash_duplicated": False,
            "listings_last_24h": 20,
            "max_fraud_card_similarity": 0.0,
        },
    },
    {
        "id": "seller_big_honest",
        "label": "normal",
        "description": "오래된 대형 판매자, 하루 200개 등록",
        "facts": {
            "joined_days": 400,
            "low_price_ratio": 0.1,
            "business_hash_duplicated": False,
            "listings_last_24h": 200,
            "max_fraud_card_similarity": 0.1,
        },
    },
    {
        "id": "seller_sale_honest",
        "label": "normal",
        "description": "1년 된 판매자가 재고 정리 세일 (저가 70%)",
        "facts": {
            "joined_days": 365,
            "low_price_ratio": 0.7,
            "business_hash_duplicated": False,
            "listings_last_24h": 30,
            "max_fraud_card_similarity": 0.0,
        },
    },
]

EXPECTED_METRIC = {
    "payment_fault": "payment_fail_rate",
    "order_drop": "order_count",
}


def expected(spec: dict[str, Any]) -> dict[str, Any]:
    """시나리오를 만들고 그 데이터에서 정답을 센다."""
    world = eval_world.build(spec)
    t = eval_world.truth(world)
    kind = spec["kind"]
    injection = spec["injection"]
    cause = spec["cause"]["type"]
    return {
        "anomaly": {
            "expected": kind in EXPECTED_METRIC,
            "metric": EXPECTED_METRIC.get(kind),
            # 탐지 지연은 장애를 넣은 시각부터 잰다
            "onset": injection.get("from") if kind in EXPECTED_METRIC else None,
            "first_failure_bucket": t.onset,  # 장애로 실패한 첫 결제가 든 5분 버킷
            "unknown_expected": kind == "empty",  # 빈 구간은 "이상 없음"이 아니라 확인할 수 없음
        },
        "impact": {
            "failed_order_count": t.fault_failed_orders,
            "affected_customer_count": t.fault_customers,
            "damage_amount": t.fault_amount,
            "all_failed_payments": t.all_failed_payments,
            "declined_payments": t.declined_payments,
        },
        "root_cause": {"injected": cause, "ref": cause_ref(spec)},
    }


def cause_ref(spec: dict[str, Any]) -> str | None:
    """주입한 원인이 사실 목록에서 갖는 출처 id (planning PR 번호·공지 id)."""
    cause = spec["cause"]
    if cause["type"] == "deploy":
        return f"pr:{cause['pr_number']}"
    if cause["type"] in ("notice", "campaign"):
        return cause["notice_id"]
    return None


def _with_ids(index: int, spec: dict[str, Any]) -> dict[str, Any]:
    """주입한 원인에 출처 id를 붙인다 (PR 번호·공지 id). 앞 배포가 있으면 그 PR 번호도."""
    cause = dict(spec["cause"])
    if cause["type"] == "deploy":
        cause["pr_number"] = 700 + index
        if "older_deploy_minutes_before" in cause:
            cause["older_pr_number"] = 600 + index
    elif cause["type"] in ("notice", "campaign"):
        cause["notice_id"] = f"ntc_eval_{index:02d}"
    return {**spec, "cause": cause}


def main() -> None:
    CASES.mkdir(exist_ok=True)
    for old in CASES.glob("*.json"):
        old.unlink()
    for index, raw in enumerate(SPECS):
        spec = _with_ids(index, raw)
        case = {**spec, "expected": expected(spec)}
        path = CASES / f"{spec['id']}.json"
        path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sellers = {"description": "라벨 있는 판매자 케이스 (사기 점수)", "sellers": SELLERS}
    (CASES / "sellers.json").write_text(
        json.dumps(sellers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(SPECS)} scenarios + {len(SELLERS)} sellers to {CASES}")


if __name__ == "__main__":
    main()
