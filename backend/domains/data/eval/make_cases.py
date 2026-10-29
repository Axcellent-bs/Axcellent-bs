"""정답지(`cases/*.json`)를 만든다: uv run python domains/data/eval/make_cases.py

시나리오 하나 = 입력 구간 + 주입한 상황 + 주입한 원인 + 정답.
정답은 지어내지 않는다. 시나리오를 실제 shop-api 코드로 만든 뒤(`eval_world.build`) 그 결제
기록에서 센다. 판매자 케이스의 라벨은 주입한 판매자 사실(사기 패턴인지)이다.
명세를 바꾸면 이 파일을 다시 돌려 정답을 다시 계산한다. 테스트가 정답과 다시 센 값이 같은지 본다.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_world  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
HOLDOUT = Path(__file__).resolve().parent / "holdout"
# holdout(처음 보는 케이스) 생성 seed와 개수. 다시 만들 때는 seed를 바꾸고 README에 이유를 적는다
# seed는 만든 날(YYYYMMDD) + 그날 몇 번째(2자리). v2(#132)는 2026-10-29 두 번째
HOLDOUT_SEED = 2026102902
HOLDOUT_COUNT = 40
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


def _with_code(spec: dict[str, Any], code: str) -> dict[str, Any]:
    """장애로 실패한 결제의 실패 코드를 `code`로 바꾸는 시나리오."""
    return {**spec, "injection": {**spec["injection"], "failure_code": code}}


LOW_REASON = "새벽 10분 50% 장애는 실패 1건이라 잡음 1건과 데이터로 구분 불가. 기록 여부로 채점"


def _low(spec: dict[str, Any]) -> dict[str, Any]:
    """사건(이상)이 아니라 낮은 등급(low) 기록으로 채점하는 장애 시나리오."""
    return {**spec, "expect": "low", "answer_reason": LOW_REASON}


def _with_fault_code(spec: dict[str, Any], code: str) -> dict[str, Any]:
    """장애로 실패한 결제의 코드를 알려진 장애 쪽 코드 `code`로 바꾼다 (영향 숫자는 낸다)."""
    return {**spec, "injection": {**spec["injection"], "fault_code": code}}


# 낮 케이스 주문량 (5분, 시간대 배수 전). 평소 장애 쪽 실패 1~3건을 넣을 주문이 모자라지 않게
DAY_TRAFFIC = 20


USUAL_MARGIN_MINUTES = 60  # λ가 보는 같은 시각 ±1시간. 지난 날 그 범위에도 주문·평소 실패를 만든다


def _usual_faults(cid: str) -> list[int]:
    """구간 시작 기준 분: 5분마다 평소 장애 쪽 실패 1~3건을 넣을 시각. 케이스 id로 늘 같은 값이다.

    잡음(`fault_noise`)과 같은 방식이라 `history`면 지난 7일 같은 시각에도 같은 수가 난다.
    구간 앞뒤 1시간에도 주문이 있으므로(`history_margin_minutes`, 그날은 앞쪽만) 그 범위까지 만든다.
    그날 구간 뒤 시각은 주문이 없어 쓰이지 않는다.
    """
    rng = random.Random(f"usual:{cid}")
    first = -USUAL_MARGIN_MINUTES // 5
    last = (WINDOW_MINUTES + USUAL_MARGIN_MINUTES) // 5
    return [5 * b for b in range(first, last) for _ in range(rng.randint(1, 3))]


def _usual_day(spec: dict[str, Any]) -> dict[str, Any]:
    """낮에 평소에도 장애 쪽 실패(PG_TIMEOUT)가 5분에 1~3건씩 나는 시나리오로 바꾼다 (#131).

    장애 없음은 `fault_noise`로, 결제 장애는 그 위에 같은 잡음을 얹는다. 주문량은 `DAY_TRAFFIC`.
    """
    minutes = _usual_faults(spec["id"])
    injection = dict(spec["injection"])
    if injection["type"] == "none":
        injection = {
            "type": "fault_noise",
            "at_minutes": minutes,
            "fault_code": "PG_TIMEOUT",
            "history": True,
        }
    else:
        injection.update(noise_at_minutes=minutes, noise_history=True, noise_code="PG_TIMEOUT")
    traffic = {"orders_per_5m": DAY_TRAFFIC, "history_margin_minutes": USUAL_MARGIN_MINUTES}
    return {**spec, "traffic": traffic, "injection": injection}


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
    _fault("pay_half_20m_14h", 4, 14, 20, 20, 0.5, NOTICE, "realistic_notation"),
    _fault("pay_weak20_20m_10h", 5, 10, 20, 20, 0.2, NONE, "realistic_notation"),
    _fault("pay_weak20_45m_19h", 6, 19, 10, 45, 0.2, DEPLOY, "fail"),
    _fault(
        "pay_full_20m_03h_night", 7, 3, 20, 20, 1.0, DEPLOY, note="결제 장애 20분, 새벽(주문 적음)"
    ),
    # 새벽 10분 50% 장애는 장애 쪽 실패가 1건뿐이라 잡음 1건과 데이터로 구분할 수 없다.
    # 사건(이상)으로 채점하지 않고, 1건을 낮은 등급(low)으로 기록했는지로 채점한다
    _low(_fault("pay_half_10m_03h_night", 8, 3, 30, 10, 0.5, NONE, "hallucinate")),
    _fault("pay_full_20m_10h_two_deploys_rule", 9, 10, 20, 20, 1.0, DEPLOY_WITH_OLD, "fail"),
    # 결제 장애인데 shop-api가 처음 보는 실패 코드를 준다 → 영향 숫자는 "확인할 수 없음"
    _with_code(
        _fault(
            "pay_full_20m_14h_new_code",
            22,
            14,
            20,
            20,
            1.0,
            DEPLOY,
            note="결제 장애 20분, 실패 코드가 처음 보는 PG_RATE_LIMITED",
        ),
        "PG_RATE_LIMITED",
    ),
    # 주문 급감 (결제는 정상)
    _shape(
        "order_drop_70_30m_10h",
        "order_drop",
        10,
        10,
        {"type": "order_drop", "start": 20, "minutes": 30, "factor": 0.3},
        DEPLOY,
        "realistic_notation",
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
    # 잡음: 장애가 아닌데 장애 쪽 실패 코드(결제사 타임아웃)가 가끔 1건씩 섞인다 (정답: 정상)
    _shape(
        "noise_timeout_14h",
        "normal",
        23,
        14,
        {"type": "fault_noise", "at_minutes": [7, 38], "fault_code": "PG_TIMEOUT", "history": True},
        NONE,
        note="평소 14시, 매일 결제사 타임아웃이 가끔 1건씩 (정상)",
    ),
    _shape(
        "noise_timeout_03h_night",
        "normal",
        24,
        3,
        {
            "type": "fault_noise",
            "at_minutes": [12, 47],
            "fault_code": "PG_TIMEOUT",
            "history": True,
        },
        NONE,
        "hallucinate",
        note="평소 새벽 3시, 매일 결제사 타임아웃이 가끔 1건씩 (정상)",
    ),
    _shape(
        "noise_single_timeout_03h_night",
        "normal",
        25,
        3,
        {"type": "fault_noise", "at_minutes": [22], "fault_code": "PG_TIMEOUT"},
        NONE,
        note="새벽 3시, 결제사 타임아웃 1건만 나고 끝남 (정상)",
    ),
    # 새벽에 장애 쪽 실패가 여러 5분 구간 계속된다 (정답: 이상). 코드는 알려진 PG_TIMEOUT
    _with_fault_code(
        _fault(
            "pay_timeout_25m_03h_night",
            26,
            3,
            20,
            25,
            1.0,
            NONE,
            note="새벽 3시, 결제사 타임아웃 25분 계속 (PG_TIMEOUT)",
        ),
        "PG_TIMEOUT",
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
    # 새벽 20분 50% 장애: 장애 쪽 실패가 2건 이상 나와 데이터로 장애라고 말할 수 있다 (정답: 이상)
    # 새 케이스는 맨 뒤에 둔다 (앞 케이스의 원인 id 번호가 밀리지 않게)
    _fault(
        "pay_half_20m_03h_night",
        27,
        3,
        40,
        20,
        0.5,
        NONE,
        note="결제 장애 20분(03:40~04:00), 세기 50%, 새벽 3시 — 장애 쪽 실패 3건",
    ),
    # 낮에 평소에도 장애 쪽 실패가 5분에 1~3건씩 난다 (#131). 시간대별 평소 실패율(λ) 기준(#112)의
    # 효과를 재려는 케이스다. 장애 없음 3개(정답: 이상 아님), 장애 3개(정답: 이상)
    _usual_day(
        _shape(
            "usual_fault_normal_10h",
            "normal",
            28,
            10,
            {"type": "none"},
            NONE,
            note="평소 10시, 매일 장애 쪽 실패가 5분에 1~3건 (정상)",
        )
    ),
    _usual_day(
        _shape(
            "usual_fault_normal_12h",
            "normal",
            29,
            12,
            {"type": "none"},
            NONE,
            "hallucinate",
            note="평소 12시, 매일 장애 쪽 실패가 5분에 1~3건 (정상)",
        )
    ),
    _usual_day(
        _shape(
            "usual_fault_normal_15h",
            "normal",
            30,
            15,
            {"type": "none"},
            NONE,
            "fail",
            note="평소 15시, 매일 장애 쪽 실패가 5분에 1~3건 (정상)",
        )
    ),
    _usual_day(
        _fault(
            "usual_fault_pay_full_15m_11h",
            2,
            11,
            25,
            15,
            1.0,
            DEPLOY,
            "realistic_notation",
            note="결제 장애 15분, 세기 100%, 11시 — 평소에도 장애 쪽 실패 5분에 1~3건",
        )
    ),
    _usual_day(
        _fault(
            "usual_fault_pay_half_20m_13h",
            3,
            13,
            20,
            20,
            0.5,
            NOTICE,
            "hallucinate",
            note="결제 장애 20분, 세기 50%, 13시 — 평소에도 장애 쪽 실패 5분에 1~3건",
        )
    ),
    _usual_day(
        _fault(
            "usual_fault_pay_weak20_30m_16h",
            4,
            16,
            15,
            30,
            0.2,
            NONE,
            note="결제 장애 30분, 세기 20%, 16시 — 평소에도 장애 쪽 실패 5분에 1~3건",
        )
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
    # 추가 18명 (사기 6, 정상 12). 정상 쪽에 경계 케이스(신규 정상·세일 저가·대량 등록)를 넣는다.
    # 기존 6명은 그대로 두고 뒤에 붙인다
    {
        "id": "seller_fraud_bulk_new",
        "label": "fraud",
        "description": "가입 당일, 저가 90%, 24시간 100개 등록, 지난 사기 판매자와 비슷",
        "facts": {
            "joined_days": 0,
            "low_price_ratio": 0.9,
            "business_hash_duplicated": False,
            "listings_last_24h": 100,
            "max_fraud_card_similarity": 0.6,
        },
    },
    {
        "id": "seller_fraud_dup_dump",
        "label": "fraud",
        "description": "가입 4일, 중복 사업자, 저가 75%, 24시간 60개 등록",
        "facts": {
            "joined_days": 4,
            "low_price_ratio": 0.75,
            "business_hash_duplicated": True,
            "listings_last_24h": 60,
            "max_fraud_card_similarity": 0.2,
        },
    },
    {
        "id": "seller_fraud_aged",
        "label": "fraud",
        "description": "가입 20일(신규 아님), 중복 사업자, 저가 80%, 사기 판매자와 비슷, 등록 적음",
        "facts": {
            "joined_days": 20,
            "low_price_ratio": 0.8,
            "business_hash_duplicated": True,
            "listings_last_24h": 5,
            "max_fraud_card_similarity": 0.7,
        },
    },
    {
        "id": "seller_fraud_similar_only",
        "label": "fraud",
        "description": "가입 2일, 지난 사기 판매자와 거의 같음, 저가 30%, 등록 적음",
        "facts": {
            "joined_days": 2,
            "low_price_ratio": 0.3,
            "business_hash_duplicated": False,
            "listings_last_24h": 15,
            "max_fraud_card_similarity": 0.95,
        },
    },
    {
        "id": "seller_fraud_cheap_only",
        "label": "fraud",
        "description": "가입 1일, 모든 상품 저가, 24시간 40개 등록",
        "facts": {
            "joined_days": 1,
            "low_price_ratio": 1.0,
            "business_hash_duplicated": False,
            "listings_last_24h": 40,
            "max_fraud_card_similarity": 0.5,
        },
    },
    {
        "id": "seller_fraud_reactivated",
        "label": "fraud",
        "description": "가입 90일 휴면 계정이 갑자기 저가 60% 상품 80개 등록, 중복 사업자",
        "facts": {
            "joined_days": 90,
            "low_price_ratio": 0.6,
            "business_hash_duplicated": True,
            "listings_last_24h": 80,
            "max_fraud_card_similarity": 0.3,
        },
    },
    {
        "id": "seller_new_honest_launch",
        "label": "normal",
        "description": "경계: 가입 1일 신규 브랜드가 첫날 60개 등록, 가격 정상",
        "facts": {
            "joined_days": 1,
            "low_price_ratio": 0.05,
            "business_hash_duplicated": False,
            "listings_last_24h": 60,
            "max_fraud_card_similarity": 0.0,
        },
    },
    {
        "id": "seller_new_honest_small",
        "label": "normal",
        "description": "경계: 가입 당일 신규, 상품 5개, 가격 정상",
        "facts": {
            "joined_days": 0,
            "low_price_ratio": 0.0,
            "business_hash_duplicated": False,
            "listings_last_24h": 5,
            "max_fraud_card_similarity": 0.0,
        },
    },
    {
        "id": "seller_new_honest_cheap",
        "label": "normal",
        "description": "경계: 가입 4일 신규, 저렴한 상품 25%, 등록 10개",
        "facts": {
            "joined_days": 4,
            "low_price_ratio": 0.25,
            "business_hash_duplicated": False,
            "listings_last_24h": 10,
            "max_fraud_card_similarity": 0.05,
        },
    },
    {
        "id": "seller_sale_clearance",
        "label": "normal",
        "description": "경계: 오래된 판매자 폐업 정리 세일 (저가 90%)",
        "facts": {
            "joined_days": 800,
            "low_price_ratio": 0.9,
            "business_hash_duplicated": False,
            "listings_last_24h": 8,
            "max_fraud_card_similarity": 0.0,
        },
    },
    {
        "id": "seller_sale_bulk_event",
        "label": "normal",
        "description": "경계: 세일 행사로 저가 60% 상품 50개 등록",
        "facts": {
            "joined_days": 200,
            "low_price_ratio": 0.6,
            "business_hash_duplicated": False,
            "listings_last_24h": 50,
            "max_fraud_card_similarity": 0.1,
        },
    },
    {
        "id": "seller_sale_new_promo",
        "label": "normal",
        "description": "경계: 가입 6일 신규가 오픈 기념 세일 (저가 50%)",
        "facts": {
            "joined_days": 6,
            "low_price_ratio": 0.5,
            "business_hash_duplicated": False,
            "listings_last_24h": 20,
            "max_fraud_card_similarity": 0.0,
        },
    },
    {
        "id": "seller_big_bulk_daily",
        "label": "normal",
        "description": "경계: 대형 판매자, 하루 300개 등록, 가격 정상",
        "facts": {
            "joined_days": 1500,
            "low_price_ratio": 0.05,
            "business_hash_duplicated": False,
            "listings_last_24h": 300,
            "max_fraud_card_similarity": 0.05,
        },
    },
    {
        "id": "seller_big_bulk_cheap",
        "label": "normal",
        "description": "경계: 대형 판매자, 저가 카테고리 상품 40%, 하루 150개 등록",
        "facts": {
            "joined_days": 700,
            "low_price_ratio": 0.4,
            "business_hash_duplicated": False,
            "listings_last_24h": 150,
            "max_fraud_card_similarity": 0.1,
        },
    },
    {
        "id": "seller_steady_small",
        "label": "normal",
        "description": "오래된 작은 판매자, 하루 2개 등록",
        "facts": {
            "joined_days": 1000,
            "low_price_ratio": 0.0,
            "business_hash_duplicated": False,
            "listings_last_24h": 2,
            "max_fraud_card_similarity": 0.0,
        },
    },
    {
        "id": "seller_steady_mid",
        "label": "normal",
        "description": "중간 규모 판매자, 가격 정상",
        "facts": {
            "joined_days": 120,
            "low_price_ratio": 0.1,
            "business_hash_duplicated": False,
            "listings_last_24h": 12,
            "max_fraud_card_similarity": 0.1,
        },
    },
    {
        "id": "seller_used_books",
        "label": "normal",
        "description": "중고 책 판매자 (중고라 저가 45%)",
        "facts": {
            "joined_days": 300,
            "low_price_ratio": 0.45,
            "business_hash_duplicated": False,
            "listings_last_24h": 20,
            "max_fraud_card_similarity": 0.2,
        },
    },
    {
        "id": "seller_transfer_honest",
        "label": "normal",
        "description": "가입 30일, 다른 몰에서 옮겨 와 45개 등록 (저가 20%)",
        "facts": {
            "joined_days": 30,
            "low_price_ratio": 0.2,
            "business_hash_duplicated": False,
            "listings_last_24h": 45,
            "max_fraud_card_similarity": 0.3,
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
    low = spec.get("expect") == "low"
    return {
        "anomaly": {
            # low 케이스: 장애를 넣었지만 사건(이상)이 아니다. 낮은 등급 기록으로 채점한다
            "expected": kind in EXPECTED_METRIC and not low,
            "low_expected": low,
            "metric": EXPECTED_METRIC.get(kind),
            # 탐지 지연은 장애를 넣은 시각부터 잰다
            "onset": injection.get("from") if kind in EXPECTED_METRIC else None,
            "first_failure_bucket": t.onset,  # 장애로 실패한 첫 결제가 든 5분 버킷
            "unknown_expected": kind == "empty",  # 빈 구간은 "이상 없음"이 아니라 확인할 수 없음
            "noise_fault_failures": t.noise_failures,  # 구간 안 잡음 실패 (장애 아님)
        },
        "impact": {
            # 처음 보는 실패 코드면 장애인지 알 수 없어 숫자를 내지 않아야 한다
            "unknown_expected": bool(injection.get("failure_code")),
            # 정답: 구간 안 장애 쪽 실패 결제 전부 (주입한 장애 + 그 구간 잡음). eval/README.md
            "failed_order_count": t.impact_failed_orders,
            "affected_customer_count": t.impact_customers,
            "damage_amount": t.impact_amount,
            # 주입한 장애로 생긴 실패만 (예전 정의). 초과분 지표 `impact.excess_*`의 정답
            "injected_failed_order_count": t.fault_failed_orders,
            "injected_customer_count": t.fault_customers,
            "injected_damage_amount": t.fault_amount,
            "noise_failed_orders": t.noise_failures,
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


# ---------------------------------------------------------------- holdout (처음 보는 케이스)

# 장애 종류: (이름, 결제 장애 세기 또는 None=주문 급감, 실패 코드)
HOLDOUT_FAULTS: list[tuple[str, float | None, str | None]] = [
    ("pay_full", 1.0, None),
    ("pay_half", 0.5, None),
    ("pay_weak20", 0.2, None),
    ("pay_timeout", 1.0, "PG_TIMEOUT"),
    ("order_drop", None, None),
]
HOLDOUT_START = eval_world.parse("2026-11-01T00:00:00Z")


def holdout_specs(seed: int = HOLDOUT_SEED, count: int = HOLDOUT_COUNT) -> list[dict[str, Any]]:
    """seed로 무작위 시나리오를 만든다. 장애 없는 케이스와 장애 케이스가 반반이다.

    무작위로 고르는 것: 시각(0~23시), 장애 종류(결제 100%·50%·20%·타임아웃·주문 급감),
    길이(5~60분), 세일 여부, 잡음 건수. 같은 seed면 늘 같은 시나리오다.
    세일과 주문 급감은 함께 고르지 않는다(#132): 구간 전체 세일이 주문 급감을 덮어 생성 조합만으로
    정답을 말할 수 없게 된다(seed 20261029 분석 `_33`). 주문 급감이면 세일 여부를 뽑지 않는다.
    """
    rng = random.Random(seed)
    specs: list[dict[str, Any]] = []
    for index in range(count):
        fault = index % 2 == 1
        hour = rng.randrange(24)
        start = HOLDOUT_START + timedelta(days=index, hours=hour)
        window = {"from": eval_world.iso(start), "to": eval_world.iso(start + timedelta(hours=1))}
        noise = sorted(rng.sample(range(60), rng.choice([0, 0, 1, 2, 3])))
        noise_history = rng.random() < 0.5
        llm = rng.choice(["grounded", "hallucinate", "fail"])
        condition: dict[str, Any] = {
            "hour": hour,
            "fault": None,
            "minutes": None,
            "strength": None,
            "sale_factor": None,
            "noise": len(noise),
        }
        if fault:
            name, fraction, code = HOLDOUT_FAULTS[rng.randrange(len(HOLDOUT_FAULTS))]
            minutes = rng.randint(5, 60)
            offset = rng.randint(0, 60 - minutes)
            block = _block(window, offset, minutes)
            if fraction is None:
                factor = rng.choice([0.1, 0.3, 0.5])
                kind, injection = "order_drop", {"type": "order_drop", **block, "factor": factor}
                condition.update(fault=name, minutes=minutes, strength=f"주문 {factor:.0%}로")
            else:
                kind = "payment_fault"
                injection = {"type": "payment_failure", **block, "fraction": fraction}
                if code:
                    injection["fault_code"] = code
                condition.update(fault=name, minutes=minutes, strength=f"실패 {fraction:.0%}")
        else:
            kind, injection = "normal", {"type": "none"}
        sale = kind != "order_drop" and rng.random() < 0.25
        sale_factor = rng.choice([2.0, 3.0]) if sale else None
        condition["sale_factor"] = sale_factor
        if noise:
            injection.update(
                noise_at_minutes=noise, noise_history=noise_history, noise_code="PG_TIMEOUT"
            )
        specs.append(
            {
                "id": f"holdout_{seed}_{index:02d}",
                "kind": kind,
                "description": "무작위 생성 (holdout)",
                "window": window,
                "traffic": {"orders_per_5m": 5, **({"sale_factor": sale_factor} if sale else {})},
                "injection": injection,
                "cause": dict(NONE),
                "llm": llm,
                "holdout": {"seed": seed, **condition},
            }
        )
    return specs


def holdout_expected(spec: dict[str, Any]) -> dict[str, Any]:
    """holdout 정답. 생성 조건으로 정하고, 결제 장애는 만들어진 장애 쪽 실패 수로 다듬는다.

    - 결제 장애에서 장애 쪽 실패가 1건뿐이면 지금 정의대로 low 기록으로 채점한다(사건 아님)
    - 0건이면(주문이 없어 실패할 기회가 없었음) 데이터에 장애 흔적이 없어 이상 아님으로 본다
    - 여기서 세는 실패는 주입한 장애로 생긴 것만이다(잡음 제외). 잡음은 장애가 아니다
    """
    answer = expected(spec)
    if spec["kind"] == "payment_fault":
        failures = answer["impact"]["injected_failed_order_count"]
        if failures <= 1:
            answer["anomaly"]["expected"] = False
            answer["anomaly"]["low_expected"] = failures == 1
    return answer


def write_holdout(seed: int = HOLDOUT_SEED, count: int = HOLDOUT_COUNT) -> None:
    HOLDOUT.mkdir(exist_ok=True)
    for old in HOLDOUT.glob("*.json"):
        old.unlink()
    for spec in holdout_specs(seed, count):
        case = {**spec, "expected": holdout_expected(spec)}
        path = HOLDOUT / f"{spec['id']}.json"
        path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {count} holdout scenarios (seed {seed}) to {HOLDOUT}")


def main() -> None:
    parser = argparse.ArgumentParser(description="정답지를 만든다")
    parser.add_argument("--holdout", action="store_true", help="holdout(처음 보는 케이스)만 만든다")
    parser.add_argument("--seed", type=int, default=HOLDOUT_SEED, help="holdout seed")
    args = parser.parse_args()
    if args.holdout:
        write_holdout(args.seed)
        return
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
