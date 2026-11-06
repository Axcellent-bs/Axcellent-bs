"""campaign_result 평가 케이스 생성기: 합성 campaign_effect 결과 + 영상 → 매출 판정·네 칸·알림.

    cd backend
    uv run python domains/data/eval/campaign_result/make_cases.py   # 케이스마다 정답 출력

케이스는 파일로 두지 않고 여기 명세에서 그때그때 만든다 (같은 명세면 늘 같음).

정답 규칙 (이 파일이 직접 계산, skill과 따로 적음):
- 매출 판정: 판매액 증분 범위(하한 = 구간 − 가장 많은 날, 상한 = 구간 − 가장 적은 날)
  하한 > 0 → more "더 팔림", 상한 < 0 → less "덜 팔림", 아니면 no_effect "효과 확인 안 됨"
- 표본 적음: 기준선 구간 < 4, 또는 구간 주문·기준선 평균 주문이 둘 다 < 5 → no_effect
  "효과 확인 안 됨(표본 적음)" (단정하지 않음, 알림 loss 없음)
- 판매액을 모르면 판정 없음, 라벨 "측정 불가: <이유>"
- 알림: less → loss, 판매액 이유에 "공백" 또는 영상 시점 데이터 끊김 → data_gap
- 네 칸 값은 입력 그대로 (마이너스 그대로), 네 칸을 더한 값은 응답 어디에도 없어야 함
- 영상에 promo_id·coupon_id가 없으면 "측정 불가: 프로모션 링크/쿠폰 없음",
  캠페인 줄 "영상 N개 중 매출 측정 가능 M개" (시험 영상 제외)
- 조회 달성 영상이 있는데 덜 팔림 → "조회는 목표의 X.XX배였지만 매출은 줄었습니다"
"""

from __future__ import annotations

import json
import sys

W0, W1 = "2026-11-01T00:00:00Z", "2026-11-08T00:00:00Z"
UNMEASURABLE = "측정 불가: 프로모션 링크/쿠폰 없음"
DATA_GAP = "놓침: 그 시점에 재지 못함 (데이터 끊김)"
LABELS = {"more": "더 팔림", "less": "덜 팔림", "no_effect": "효과 확인 안 됨"}
LOW_LABEL = "효과 확인 안 됨(표본 적음)"

# 케이스: sales = (증분, 하한, 상한, 기준선 구간, 구간 주문, 기준선 평균 주문) 또는 모름 이유,
# orders = 증분 주문, products = (증분, 하한, 상한), link = (주문, 판매액), coupon = (주문, 판매액),
# videos = [(id, 판정, 7일 목표 대비, promo_id, coupon_id, 1일 데이터 끊김)]
SPECS = {
    "in_range": {"note": "흔들림 안", "sales": (120_000, -300_000, 450_000, 4, 40, 38),
                 "orders": 3, "videos": [("vidinrange1", "achieved", 1.4, "promo-a", None, False)]},
    "above": {"note": "흔들림 위", "sales": (1_200_000, 400_000, 1_900_000, 4, 80, 40),
              "orders": 40, "videos": [("vidabove001", "achieved", 2.1, "promo-b", None, False)]},
    "below": {"note": "흔들림 아래 → loss", "sales": (-900_000, -1_500_000, -300_000, 4, 20, 45),
              "orders": -25, "videos": [("vidbelow001", "missed", 0.6, None, "cpn-1", False)]},
    "low_sample_negative": {"note": "표본 적음 + 마이너스: 단정하지 않고 값은 그대로",
                            "sales": (-60_000, -90_000, -20_000, 3, 2, 3), "orders": -1,
                            "videos": [("vidlowsamp1", "missed", 0.4, "promo-c", None, False)]},
    "no_promo": {"note": "프로모션 링크·쿠폰 없음 → 영상 매출 측정 불가",
                 "sales": (30_000, -200_000, 260_000, 4, 30, 29), "orders": 1,
                 "videos": [("vidnopromo1", "achieved", 1.2, None, None, False),
                            ("vidnopromo2", "missed", 0.7, None, None, False)]},
    "negative_preserved": {"note": "상품 칸 마이너스 그대로, 전체 매장은 범위 안",
                           "sales": (50_000, -250_000, 310_000, 4, 35, 34), "orders": 2,
                           "products": (-150_000, -400_000, -20_000), "link": (4, 80_000),
                           "coupon": (2, 30_000),
                           "videos": [("vidnegprod1", "missed", 0.9, "promo-d", None, False)]},
    "no_sum": {"note": "네 칸 합산 금지", "sales": (500_000, -100_000, 900_000, 4, 50, 45),
               "orders": 5, "products": (100_000, -50_000, 260_000), "link": (12, 300_000),
               "coupon": (8, 200_000),
               "videos": [("vidnosum001", "achieved", 1.8, "promo-e", "cpn-2", False)]},
    "views_up_sales_down": {"note": "조회 달성인데 매출 감소",
                            "sales": (-700_000, -1_200_000, -150_000, 4, 25, 40), "orders": -15,
                            "videos": [("vidviewsup1", "achieved", 2.5, "promo-f", None, False)]},
    "data_gap": {"note": "주문 기록 공백 + 영상 1일 수집 끊김 → data_gap, 판정 없음",
                 "sales": "결제 기록 공백: 구간 주문 3건이 결제 대기이고 결제 완료가 0건",
                 "orders": None,
                 "videos": [("viddatagap1", "missed", 0.8, "promo-g", None, True)]},
}  # fmt: skip


def _direction(low: float, high: float, days: int) -> str:
    if days < 2:
        return "uncertain"
    return "up" if low > 0 else "down" if high < 0 else "uncertain"


def _totals(orders: int, gross: int) -> dict:
    return {"orders": orders, "gross_krw": gross, "discount_krw": 0, "paid_krw": gross,
            "refund_krw": 0}  # fmt: skip


def effect_of(name: str) -> dict:
    """campaign_effect 결과 모양 (합성). 계산은 하지 않고 정해진 값을 넣는다."""
    spec = SPECS[name]
    s = spec["sales"]
    out: dict = {
        "campaign_id": f"cmp_{name}", "window_from": W0, "window_to": W1, "summary": "x",
        "orders": {"status": "known", "direction": "none", "excess": float(spec["orders"] or 0),
                   "baseline_days": 4},
        "revenue": {"status": "unknown", "unknown_reason": "매출 지표 없음"},
        "coupons": {"status": "unknown", "unknown_reason": "캠페인 쿠폰 없음"},
        "incremental_orders": spec["orders"],
    }  # fmt: skip
    if isinstance(s, str):
        out["sales"] = {"status": "unknown", "unknown_reason": s, "baseline_days": 4}
    else:
        inc, low, high, days, period_orders, base_orders = s
        base_gross = 5_000_000
        coupon = spec.get("coupon")
        out["sales"] = {
            "status": "known", "period": _totals(period_orders, base_gross + inc),
            "baseline": _totals(base_orders, base_gross),
            "baseline_rule": "시작 전 4주 같은 요일·시간대",
            "baseline_days": days, "incremental_gross_krw": inc, "incremental_paid_krw": inc,
            "incremental_low_krw": low, "incremental_high_krw": high,
            "direction": _direction(low, high, days), "direction_reason": "범위",
            "direct_attributed_krw": coupon[1] if coupon else None,
            "direct_orders": coupon[0] if coupon else None,
            "direct_reason": None if coupon else "캠페인 쿠폰을 쓴 주문 없음",
        }  # fmt: skip
    if "products" in spec:
        inc, low, high = spec["products"]
        measure = {"period": 1_000_000.0, "baseline": 1_000_000.0 - inc, "incremental": float(inc),
                   "incremental_low": float(low), "incremental_high": float(high),
                   "direction": _direction(low, high, 4)}  # fmt: skip
        out["products_effect"] = {
            "status": "known", "matched": [{"product_id": "p1", "name": "책", "asked": "p1"}],
            "orders": {"period": 10.0}, "units": {"period": 12.0}, "gross_krw": measure,
            "baseline_days": 4,
        }  # fmt: skip
    if "link" in spec:
        orders, gross = spec["link"]
        out["attribution_effect"] = {"status": "known", "campaign_id": f"cmp_{name}",
                                     "orders": orders, "gross_krw": gross}  # fmt: skip
    return out


def videos_of(name: str) -> list[dict]:
    out = []
    for vid, verdict, ratio, promo, coupon, gap in SPECS[name]["videos"]:
        cps = [{"checkpoint": "1d", "status": "unknown", "reason": DATA_GAP}] if gap else []
        cps.append({"checkpoint": "7d", "status": "known", "views": round(ratio * 5000),
                    "target_ratio": ratio})  # fmt: skip
        out.append({"video_id": vid, "title": vid, "campaign_id": f"cmp_{name}",
                    "verdict": verdict, "checkpoints": cps, "promo_id": promo,
                    "coupon_id": coupon, "tracking": True, "platform": "youtube"})  # fmt: skip
    return out


def expected(name: str) -> dict:
    spec = SPECS[name]
    s = spec["sales"]
    videos = videos_of(name)
    alerts: set[str] = set()
    cells: dict[str, dict] = {}
    notes: list[str] = []
    if isinstance(s, str):
        verdict, label, low_sample = None, f"측정 불가: {s}", False
        if "공백" in s:
            alerts.add("data_gap")
    else:
        inc, low, high, days, period_orders, base_orders = s
        raw = {"up": "more", "down": "less"}.get(_direction(low, high, days), "no_effect")
        low_sample = days < 4 or (period_orders < 5 and base_orders < 5)
        verdict = "no_effect" if low_sample else raw
        label = LOW_LABEL if low_sample else LABELS[verdict]
        cells["store"] = {"incremental_gross_krw": inc, "incremental_low_krw": low,
                          "incremental_high_krw": high,
                          "incremental_orders": spec["orders"]}  # fmt: skip
        if "products" in spec:
            p_inc, p_low, p_high = spec["products"]
            cells["products"] = {"incremental_gross_krw": p_inc, "incremental_low_krw": p_low,
                                 "incremental_high_krw": p_high}  # fmt: skip
        if "link" in spec or "coupon" in spec:
            cells["attribution"] = {}
            if "link" in spec:
                cells["attribution"] |= {"orders": spec["link"][0], "gross_krw": spec["link"][1]}
            if "coupon" in spec:
                cells["attribution"] |= {"coupon_orders": spec["coupon"][0],
                                         "coupon_gross_krw": spec["coupon"][1]}  # fmt: skip
        if verdict == "less":
            alerts.add("loss")
            won = [v["checkpoints"][-1]["target_ratio"] for v in videos
                   if v["verdict"] == "achieved"]  # fmt: skip
            if won:
                notes.append(f"조회는 목표의 {max(won):.2f}배였지만 매출은 줄었습니다")
    if any(c.get("reason") == DATA_GAP for v in videos for c in v["checkpoints"]):
        alerts.add("data_gap")
    unmeasurable = [v["video_id"] for v in videos if not v["promo_id"] and not v["coupon_id"]]
    m = len(videos) - len(unmeasurable)
    summed = None
    if all(k in spec for k in ("products", "link", "coupon")) and not isinstance(s, str):
        summed = spec["link"][1] + spec["coupon"][1] + spec["products"][0] + s[0]
    return {"verdict": verdict, "label": label, "low_sample": low_sample, "alerts": sorted(alerts),
            "cells": cells, "unmeasurable": unmeasurable, "notes": notes, "summed": summed,
            "measurable_line": f"영상 {len(videos)}개 중 매출 측정 가능 {m}개"}  # fmt: skip


def build(name: str) -> dict:
    return {"name": name, "note": SPECS[name]["note"], "effect": effect_of(name),
            "videos": videos_of(name), "expected": expected(name)}  # fmt: skip


def cases() -> list[dict]:
    return [build(name) for name in SPECS]


def main(argv: list[str]) -> int:
    for case in cases():
        sys.stdout.write(f"{case['name']}: {case['note']}\n")
        sys.stdout.write(json.dumps(case["expected"], ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
