"""대사 예외 시연용 PG 정산 파일을 만들어 재무에 올린다 (운영자 명령, `pg_demo_file.sh`).

실제 PG가 아직 없어서, 재무 대사는 결제 기록으로 만든 모의 자료와 맞춘다. 그래서 실제 모드
대사 화면에는 예외가 나오지 않는다(재무는 불일치를 지어내지 않는다). 대사 예외를 보여 주려면
**PG 쪽에서 일이 생긴 정산 파일**이 있어야 한다. 이 명령이 그 파일을 만든다. 쇼핑몰 장애 넣기
(`shop_fault.sh`)처럼 운영자가 일부러 일으키는 시연이다.

- 쇼핑몰(shop-api)의 실제 결제 기록으로 최근 정산된 기간(판매일 오늘−6일 ~ 오늘−지급일)의
  PG 정산 파일을 만든다. 금액·수수료는 기본 계약(3.1%)대로다
- 그중 4건에 PG 쪽 사건을 넣는다. 무엇을 바꿨는지 화면에 모두 출력한다
  1. 부분 취소가 PG에만 반영됨: PG 금액 −2,000원 → 금액 다름
  2. PG 정산 보류: 파일에 없음 → PG에 없음
  3. 계약과 다른 수수료 3.5% → 수수료 이상
  4. PG 금액 +1,000원 → 금액 다름
- 파일 맨 위 줄과 이름에 "시연 시나리오"를 적는다. 올린 기록(`pg_imports`)에 이름이 남는다
- 재무 화면에서 올리는 것과 같은 길(`POST /api/finance/pg/file`)로 올린다
- 끝나면 `clear`로 올린 PG 자료를 모두 뺀다(빼는 기록은 남는다)

    bash backend/scripts/pg_demo_file.sh          # 만들고 올리기
    bash backend/scripts/pg_demo_file.sh --dry    # 만들기만 (backend/.data/에 파일)
    bash backend/scripts/pg_demo_file.sh clear    # 올린 PG 자료 빼기
    bash backend/scripts/pg_demo_file.sh status   # 올린 기록 보기

사람 토큰은 run_all.sh가 만든 backend/.data/web.env에서 읽는다(출력하지 않는다).
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from finance_agent import finance_config as fc

BACKEND = Path(__file__).resolve().parent.parent
DATA = BACKEND / ".data"
SHOP = os.getenv("SHOP_API_URL", "http://127.0.0.1:8010")
FINANCE = os.getenv("FINANCE_URL", "http://127.0.0.1:8105")
EVENTS = (
    ("partial_cancel", "부분 취소가 PG에만 반영됨 (PG 금액 −2,000원)"),
    ("held", "PG 정산 보류 (파일에 없음)"),
    ("fee_3_5", "계약과 다른 수수료 3.5%"),
    ("over", "PG 금액 +1,000원"),
)


def human_token() -> str:
    try:
        for line in (DATA / "web.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("VITE_SAFETY_HUMAN_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    sys.exit(
        "backend/.data/web.env 가 없습니다. 먼저 bash scripts/run_all.sh 로 백엔드를 띄우세요."
    )


def settled_payments() -> tuple[list[dict[str, Any]], str, str]:
    cfg = fc.load_config().pg
    today = fc.today()
    lo, hi = today - timedelta(days=6), today - timedelta(days=cfg.payout_days)
    query = urlencode({"since": fc.iso(fc.day_start(lo))})
    r = httpx.get(f"{SHOP}/api/shop/orders?{query}", timeout=120)
    r.raise_for_status()
    rows = []
    for order in r.json().get("data") or []:
        for p in order.get("payments") or []:
            at = fc.parse_dt(p.get("created_at"))
            if p.get("status") != "succeeded" or at is None or not isinstance(p.get("amount"), int):
                continue
            if lo <= at.date() <= hi:
                rows.append(
                    {
                        "payment_id": p["payment_id"],
                        "order_id": order["order_id"],
                        "amount": p["amount"],
                        "sold": at.date(),
                    }
                )
    rows.sort(key=lambda x: (x["sold"], x["payment_id"]))
    return rows, lo.isoformat(), hi.isoformat()


def build() -> tuple[str, str, list[str]]:
    cfg = fc.load_config().pg
    payments, lo, hi = settled_payments()
    candidates = [p for p in payments if p["amount"] > 2000]
    if len(candidates) < len(EVENTS):
        sys.exit(f"판매일 {lo} ~ {hi}에 정산된 결제가 {len(candidates)}건이라 넣지 못했습니다.")
    step = len(candidates) // (len(EVENTS) + 1)
    picked = {candidates[step * (i + 1)]["payment_id"]: e for i, e in enumerate(EVENTS)}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f"# AXcellent 시연 시나리오 PG 정산 파일 (운영자 생성, 판매일 {lo} ~ {hi})"])
    w.writerow(["payment_id", "order_id", "amount", "fee", "sold_date", "paid_out_date"])
    changes = []
    for p in payments:
        amount, rate = p["amount"], cfg.fee_rate
        event = picked.get(p["payment_id"])
        if event is not None:
            kind, label = event
            changes.append(f"{p['payment_id']} (판매일 {p['sold']}): {label}")
            if kind == "held":
                continue
            amount += {"partial_cancel": -2000, "over": 1000}.get(kind, 0)
            rate = 0.035 if kind == "fee_3_5" else rate
        fee = fc.rate_amount(amount, rate)
        payout = p["sold"] + timedelta(days=cfg.payout_days)
        w.writerow(
            [p["payment_id"], p["order_id"], amount, fee, p["sold"].isoformat(), payout.isoformat()]
        )
    name = f"pg-demo-scenario-{fc.today().isoformat()}.csv"
    return name, out.getvalue(), changes


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "make"
    if cmd == "status":
        r = httpx.get(f"{FINANCE}/api/finance/pg/imports", timeout=30)
        print(json.dumps(r.json().get("data"), ensure_ascii=False, indent=2))
        return
    if cmd == "clear":
        r = httpx.post(
            f"{FINANCE}/api/finance/pg/clear",
            headers={"Authorization": f"Bearer {human_token()}"},
            json={"reason": "대사 예외 시연 끝 (pg_demo_file.sh clear)"},
            timeout=30,
        )
        print(r.status_code, json.dumps(r.json(), ensure_ascii=False))
        return
    if cmd not in ("make", "--dry"):
        sys.exit("사용법: pg_demo_file.sh [--dry|clear|status]")
    name, text, changes = build()
    print(f"PG 정산 파일 {name}: {text.count(chr(10)) - 2}줄. PG 쪽 사건 {len(changes)}건:")
    for line in changes:
        print(f"  - {line}")
    if cmd == "--dry":
        path = DATA / name
        path.write_text(text, encoding="utf-8")
        print(f"올리지 않고 {path}에 저장했습니다.")
        return
    r = httpx.post(
        f"{FINANCE}/api/finance/pg/file",
        params={"name": name},
        content=text.encode("utf-8"),
        headers={"Authorization": f"Bearer {human_token()}", "Content-Type": "text/csv"},
        timeout=60,
    )
    body = r.json()
    if r.status_code != 200:
        sys.exit(f"올리지 못했습니다 ({r.status_code}): {json.dumps(body, ensure_ascii=False)}")
    rec = body["data"]
    print(f"올렸습니다: {rec['rows']}줄, 기간 {rec['period']['from']} ~ {rec['period']['to']}.")
    print("재무 화면 ② 매출·PG 대사에서 확인. 끝나면: bash backend/scripts/pg_demo_file.sh clear")


if __name__ == "__main__":
    main(sys.argv[1:])
