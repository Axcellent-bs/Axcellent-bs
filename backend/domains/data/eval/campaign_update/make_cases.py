"""성과 결과 → 기획 보내기(receive_campaign_update)·재무 기록용 값 평가 케이스 생성기.

    cd backend
    uv run python domains/data/eval/campaign_update/make_cases.py

규칙 (이 파일이 직접 계산, skill과 따로 적음):
- 보내기 판정: 시험 캠페인 → 안 보냄("시험 캠페인"), proposal_id 없음 → 안 보냄("기획 제안 번호
  없음"), status unknown → 안 보냄(결과의 이유). 그 밖에는 시험 아닌 영상이 모두 7일을 쟀거나
  (측정 중 0개) 측정 기간(첫 게시 + 7일)이 끝났으면 보냄, 아니면 대기(첫 게시 + 7일 이후)
- direction(매출 판정 기준): 더 팔림 → 늘었음, 덜 팔림 → 줄었음, 효과 확인 안 됨 → 같음,
  표본 적음·측정 불가 → 확인 불가
- summary: "조회 목표의 N배 (영상 M개 중 K개 달성) · 매출 ±N원 · 주문 ±N건" (+ loss 알림이면
  " · 매출 감소 경고"). N배 = 7일이 끝난 영상(달성·미달)의 목표 대비 배수 중앙값.
  200자를 넘으면 자르지 않고 "<조회 판정> · 매출 <direction>"
- facts(최대 12, label 120·value 80·source 60, source "data.campaign_result"): 조회 판정, 목표 대비
  배수, 달성 영상 수, 매출 판정, 증가 매출, 증가 주문, 네 칸(귀속·상품·쿠폰·전체 매장) 각각,
  표본 적음이면 그 사실. 값이 없는 칸은 넣지 않는다. 마이너스는 그대로, 80자를 넘는 값은 79자 + "…"
- 기획 응답: applied·duplicate → 보냄, unknown_proposal → 안 보냄("기획에 없는 제안", 다시 안 함),
  그 밖의 거절 → 안 보냄("기획이 거절: <이유>", 다시 안 함), 연결 실패·5xx·시간 초과 → 실패,
  다음 루프에 다시 (캠페인당 한국 날짜 하루 3번까지)
- 기능 플래그(DATA_SEND_CAMPAIGN_UPDATE)가 꺼져 있으면 호출 0회, 상태 "off"
- 재무 기록용 값: gmv_krw = max(증가 매출, 0), orders = max(증가 주문, 0), measured_* = 잰 값
  그대로(음수 가능), 모르면 None (0으로 채우지 않음). spend_krw·campaign_name·channel·coupon_krw
  칸은 없다 (마케팅이 채움). 기간은 측정 기간(첫 게시 ~ 7일 뒤)의 한국 날짜
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import UTC, datetime, timedelta, timezone

T0 = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)  # 첫 게시 (한국 11월 1일 12시)
KST = timezone(timedelta(hours=9))
SOURCE = "data.campaign_result"
VIEWS_LABEL = {"achieved": "조회 달성", "missed": "조회 미달", "measuring": "측정 중",
               "none": "조회 판정 없음"}  # fmt: skip
SALES_LABEL = {"more": "더 팔림", "less": "덜 팔림", "no_effect": "효과 확인 안 됨"}
LOW_LABEL = "효과 확인 안 됨(표본 적음)"
DIRECTION = {"more": "늘었음", "less": "줄었음", "no_effect": "같음"}
CELLS = [
    ("attribution", "귀속 (쿠폰·링크로 온 주문)"),
    ("products", "상품 (캠페인 대상 책)"),
    ("coupons", "쿠폰"),
    ("store", "전체 매장"),
]
RETRY_PER_DAY = 3


def won(v: int) -> str:
    return f"{v:+,}원"


def count(v: int) -> str:
    return f"{v:+,}건"


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# -------------------------------------------------------- 결과 모양 (CampaignResultOutput 칸)


def video(verdict: str, ratio: float | None = None, test: bool = False) -> dict:
    return {"verdict": verdict, "ratio": ratio, "test": test}


def sales(verdict: str | None, gross: int | None = None, orders: int | None = None,
          low: str | None = None, label: str | None = None) -> dict:  # fmt: skip
    known = verdict is not None
    return {
        "status": "known" if known else "unknown",
        "verdict": verdict,
        "label": label or (LOW_LABEL if low else SALES_LABEL.get(verdict or "", "측정 불가")),
        "low_sample": low is not None,
        "low_sample_reason": low,
        "gross": gross,
        "orders": orders,
        "cells": _cells(gross, orders),
    }


def _cells(gross: int | None, orders: int | None) -> list[dict]:
    """네 칸: 귀속·상품은 따로 잰 값, 쿠폰은 사용률, 전체 매장은 증분 (더하지 않음)."""
    if gross is None:
        return [{"key": k, "label": lab, "status": "unknown", "line": None,
                 "reason": "프로모션 링크/쿠폰 없음"} for k, lab in CELLS]  # fmt: skip
    att = f"링크 {max(orders or 0, 0) // 2}건 · {max(gross, 0) // 3:,}원"
    lines = [att, won(gross // 2), "사용률 12.5% (5/40장)",
             won(gross) + (f" · 주문 {count(orders)}" if orders is not None else "")]
    return [{"key": k, "label": lab, "status": "known", "line": line, "reason": None}
            for (k, lab), line in zip(CELLS, lines, strict=True)]  # fmt: skip


def result(name: str, *, videos: list[dict], sale: dict, test: bool = False,
           proposal: str | None = "PROP-7", status: str = "known", reason: str | None = None,
           alerts: tuple[str, ...] = ()) -> dict:  # fmt: skip
    return {"campaign_id": f"cmp_{name}", "campaign_name": f"{name} 캠페인",
            "insight_id": "ins_book", "proposal_id": proposal, "test": test,
            "status": status, "reason": reason,
            "window_from": T0.isoformat(), "videos": videos, "sales": sale,
            "alerts": list(alerts)}  # fmt: skip


# ---------------------------------------------------------------- 정답 규칙


def decide(r: dict, now: datetime) -> dict:
    if r["test"]:
        return {"kind": "skip", "reason": "시험 캠페인"}
    if not r["proposal_id"]:
        return {"kind": "skip", "reason": "기획 제안 번호 없음"}
    if r["status"] == "unknown":
        return {"kind": "skip", "reason": r["reason"]}
    real = [v for v in r["videos"] if not v["test"]]
    end = datetime.fromisoformat(r["window_from"]) + timedelta(days=7)
    if not any(v["verdict"] == "measuring" for v in real) or now >= end:
        return {"kind": "send", "reason": None}
    return {"kind": "wait", "reason": None, "send_after": end.isoformat()}


def direction(r: dict) -> str:
    s = r["sales"]
    if s["status"] != "known" or s["low_sample"]:
        return "확인 불가"
    return DIRECTION[s["verdict"]]


def _views(r: dict) -> tuple[str, float | None, int, int]:
    real = [v for v in r["videos"] if not v["test"]]
    ratios = [v["ratio"] for v in real if v["verdict"] in ("achieved", "missed")
              and v["ratio"] is not None]  # fmt: skip
    if ratios:
        name = "achieved" if statistics.median(ratios) >= 1 else "missed"
    elif any(v["verdict"] == "measuring" for v in real):
        name = "measuring"
    else:
        name = "none"
    ratio = round(statistics.median(ratios), 2) if ratios else None
    return name, ratio, len(real), sum(1 for v in real if v["verdict"] == "achieved")


def summary(r: dict) -> str:
    name, ratio, total, done = _views(r)
    if name in ("achieved", "missed") and ratio is not None:
        first = f"조회 목표의 {ratio:.2f}배 (영상 {total}개 중 {done}개 달성)"
    elif name == "measuring":
        first = f"조회 측정 중 (영상 {total}개 중 {done}개 달성)"
    else:
        first = VIEWS_LABEL["none"]
    s = r["sales"]
    if s["gross"] is not None:
        second = f"매출 {won(s['gross'])}" + (f" · 주문 {count(s['orders'])}"
                                             if s["orders"] is not None else "")  # fmt: skip
    else:
        second = f"매출 {s['label']}"
    text = f"{first} · {second}" + (" · 매출 감소 경고" if "loss" in r["alerts"] else "")
    return text if len(text) <= 200 else f"{VIEWS_LABEL[name]} · 매출 {direction(r)}"


def facts(r: dict) -> list[dict]:
    name, ratio, total, done = _views(r)
    s = r["sales"]
    rows = [("조회 판정", VIEWS_LABEL[name])]
    if ratio is not None:
        rows.append(("목표 대비 배수 (7일 중앙값)", f"{ratio:.2f}배"))
    rows.append(("목표 달성 영상", f"{done}/{total}개"))
    rows.append(("매출 판정", s["label"]))
    if s["gross"] is not None:
        rows.append(("증가 매출 (기준선 대비)", won(s["gross"])))
    if s["orders"] is not None:
        rows.append(("증가 주문 (기준선 대비)", count(s["orders"])))
    for c in s["cells"]:
        rows.append((f"매출 칸: {c['label']}", c["line"] or f"측정 불가: {c['reason']}"))
    if s["low_sample"]:
        rows.append(("표본 적음", s["low_sample_reason"]))
    return [{"label": clip(a, 120), "value": clip(b, 80), "source": SOURCE} for a, b in rows][:12]


def finance(r: dict) -> dict:
    s = r["sales"]
    start = datetime.fromisoformat(r["window_from"])
    gross, orders = s["gross"], s["orders"]
    return {
        "campaign_id": r["campaign_id"],
        "period_from": start.astimezone(KST).date().isoformat(),
        "period_to": (start + timedelta(days=7)).astimezone(KST).date().isoformat(),
        "gmv_krw": None if gross is None else max(gross, 0),
        "orders": None if orders is None else max(orders, 0),
        "measured_gmv_krw": gross,
        "measured_orders": orders,
        "source": SOURCE,
    }


def replay(r: dict, ticks: list[dict]) -> dict:
    """루프마다 (지금, 기획 응답) → 호출 수·마지막 상태·실패 수 (한국 날짜 하루 3번)."""
    calls, state, failures, fail_day, reason = 0, "pending", 0, None, None
    for t in ticks:
        now = T0 + timedelta(hours=t["hours"])
        if state in ("sent", "not_sent") or decide(r, now)["kind"] != "send":
            continue
        day = now.astimezone(KST).date()
        if fail_day != day:
            failures, fail_day = 0, day
        if state == "failed" and failures >= RETRY_PER_DAY:
            continue
        calls += 1
        reply = t["reply"]
        if reply["status"] == "completed" and reply["outcome"] in ("applied", "duplicate"):
            state, reason = "sent", None
        elif reply["status"] == "completed" and reply["outcome"] == "unknown_proposal":
            state, reason = "not_sent", "기획에 없는 제안"
        elif reply["outcome"] in ("unreachable", "TIMEOUT") or reply["outcome"][:6] == "http_5":
            state, failures = "failed", failures + 1
        else:
            state, reason = "not_sent", f"기획이 거절: {reply['outcome']}"
    return {"calls": calls, "state": state, "failures": failures if state == "failed" else 0,
            "reason": reason}  # fmt: skip


# ---------------------------------------------------------------- 케이스


def cases() -> list[dict]:
    end = 7 * 24
    done2 = [video("achieved", 1.5), video("missed", 0.6), video("excluded", None, test=True)]
    out: list[dict] = []

    def add(kind: str, name: str, r: dict, **extra: object) -> None:
        out.append({"kind": kind, "name": name, "result": r, **extra})

    more = sales("more", 1_200_000, 40)
    # 보내기 판정 (지금 = 게시 후 시간)
    for name, r, hours in [
        ("all_measured", result("all", videos=done2, sale=more), end + 1),
        ("still_measuring", result("meas", videos=[video("achieved", 1.2), video("measuring")],
                                   sale=more), 72),  # fmt: skip
        ("period_over", result("over", videos=[video("achieved", 1.2), video("measuring")],
                               sale=more), end + 2),  # fmt: skip
        ("test_campaign", result("test", videos=[video("achieved", 2.0, test=True)], sale=more,
                                 test=True), end + 1),  # fmt: skip
        ("no_proposal", result("noprop", videos=done2, sale=more, proposal=None), end + 1),
        ("status_unknown", result("unk", videos=[], sale=sales(None), status="unknown",
                                  reason="등록된 영상이 없음"), end + 1),  # fmt: skip
    ]:
        now = T0 + timedelta(hours=hours)
        add("decide", name, r, now=now.isoformat(), expected=decide(r, now))
    # 매핑 (direction·summary·facts)
    long_label = "측정 불가: " + "쇼핑몰 주문 기록을 읽지 못함 " * 12
    for name, r in [
        ("map_more", result("more", videos=done2, sale=more)),
        ("map_less_loss", result("less", videos=done2, sale=sales("less", -300_000, -12),
                                 alerts=("loss",))),  # fmt: skip
        ("map_same", result("same", videos=done2, sale=sales("no_effect", 20_000, 1))),
        ("map_low_sample", result("low", videos=done2, sale=sales(
            "no_effect", 50_000, 2, low="기준선 2구간 (4구간 미만)"))),
        ("map_unmeasurable", result("none", videos=done2, sale=sales(None))),
        ("map_long_summary", result("long", videos=done2, sale=sales(None, label=long_label))),
    ]:
        add("payload", name, r, expected={"direction": direction(r), "summary": summary(r),
                                          "facts": facts(r)})  # fmt: skip
    # 기획 응답 처리 (같은 캠페인을 루프마다)
    ok = {"status": "completed", "outcome": "applied"}
    down = {"status": "failed", "outcome": "http_503"}
    ready = result("reply", videos=done2, sale=more)
    for name, ticks in [
        ("reply_applied_once", [{"hours": end + 1, "reply": ok}, {"hours": end + 2, "reply": ok}]),
        ("reply_duplicate", [{"hours": end + 1,
                              "reply": {"status": "completed", "outcome": "duplicate"}}]),
        ("reply_unknown_proposal", [{"hours": end + h, "reply": {
            "status": "completed", "outcome": "unknown_proposal"}} for h in (1, 2)]),
        ("reply_rejected", [{"hours": end + h, "reply": {"status": "failed",
                                                          "outcome": "INVALID_INPUT"}}
                            for h in (1, 2)]),  # fmt: skip
        # 168h = 한국 11월 8일 12시. 13~16시 네 번 실패 → 3번만, 다음 날(11월 9일) 다시
        ("reply_retry_3_per_day", [{"hours": end + h, "reply": down} for h in (1, 2, 3, 4)]
         + [{"hours": end + 24, "reply": down}]),
        ("reply_unreachable_then_ok", [{"hours": end + 1, "reply": {"status": "failed",
                                                                    "outcome": "unreachable"}},
                                       {"hours": end + 2, "reply": ok}]),  # fmt: skip
    ]:
        add("reply", name, ready, ticks=ticks, expected=replay(ready, ticks))
    add("flag_off", "flag_off_no_call", ready, ticks=[{"hours": end + 1, "reply": ok}],
        expected={"calls": 0, "state": "off"})
    # 재무 기록용 값
    for name, r in [
        ("finance_more", result("fmore", videos=done2, sale=more)),
        ("finance_less_negative", result("fless", videos=done2,
                                         sale=sales("less", -300_000, -12))),  # fmt: skip
        ("finance_unknown", result("fnone", videos=done2, sale=sales(None))),
    ]:
        add("finance", name, r, expected=finance(r))
    return out


if __name__ == "__main__":
    json.dump(cases(), sys.stdout, ensure_ascii=False, indent=1)
