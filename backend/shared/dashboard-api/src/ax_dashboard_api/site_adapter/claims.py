"""① 신청 받은함 (W5): 고객사 취소·환불 신청 → shop-api `GET /admin/claims` 모양.

설계: `docs/superpowers/specs/2026-11-18-site-adapter-design.md` "① 짝 맞춰 읽기"
(`/admin/claims` 취소·환불 신청 "사이트에 있으면 짝"), "③-쓰기" W5.

고객지원(`support_agent.shop_client`)은 신청함을 두 가지로 읽는다.

- `state=new&limit=` → 읽을 차례가 된 신청. 줄마다 `seq`(정수)·`key`·`context_id`·`buyer_id`·
  `customer_name`·`order_id`·`type`·`reason`·`items`·`unsealed`·`evidence_count`·`description`,
  `meta.total`(기다리는 수)·`meta.failed`(실패로 뺀 수)
- `state=open&after=&limit=` → 읽어 갔지만 끝나지 않은 신청. 줄마다 `seq`·`support_claim_id`·
  `status`·`result_digest`, 다음 쪽은 `meta.next_after`

짝 (`mapping.inboxes.claims`, 읽기 짝과 같은 `ResourceMap`):

- `fields`: `claim_id`(고객사 신청 id)·`order_id`(주문 = 예약 번호)·`status`(값 표로 `requested`면
  아직 결정 전)·`type`·`reason`(shop-api 값, 보통 `const:`)·`description`(고객이 쓴 글, untrusted)·
  `created_at`. 짝 식 안에서 `order.<칸>`은 그 신청의 주문 원본 행이다(`buyer_id`·`customer_name`을
  주문에서 꺼낸다)
- 회원이 아닌 주문(구매자 칸이 빔)은 `guest-<주문 번호>`로 짝짓는다(판매 기록 내보내기와 같은
  이름표). 주문 하나 읽기도 같은 값을 준다(`resources.order_detail`)

번호 짝:

- `seq` = 고객사 신청 id에 매긴 정수(`links.numbers("claims", …)`). `links["claims"][seq]` = 신청 id
- 결정 전 신청은 `links["payments"][<결제 번호>]` = 신청 id도 적는다. 주문 하나 읽기의 결제 번호는
  주문 번호다(`resources.order_detail`) → 환불 쓰기 짝이 `link:payments.payment_id`로 신청을 찾는다

처리 기록 (`<변환기 폴더>/claims.json`, 고객지원 장부):

- 고객지원이 `POST /admin/claims/{seq}/result`로 판정 결과를 적으면 `support_claim_id`·`status`·
  결과 지문(`result_digest`, 고객지원 `inquiry_pull.claim_digest`와 같은 방식)만 남긴다(결과 본문·
  고객 이름은 남기지 않는다)
- 결과가 적힌 신청은 `state=new`에서 빠지고, 끝나지 않은 상태면 `state=open`에 나온다
- `failed`·`retry`는 예전처럼 접수 동기화 기록(`bookkeeping`)에만 남긴다
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ax_dashboard_api.site_adapter import links
from ax_dashboard_api.site_adapter.mapping import MissingField, ResourceMap, adapter_dir, pick
from ax_dashboard_api.site_adapter.resources import MappingBroken

STATE_FILE = "claims.json"
CLAIMS = "claims"
PAYMENTS = "payments"
# 결정 전 신청 (짝 `values.status`가 옮긴 값)
REQUESTED = "requested"
# 고객지원 클레임 상태 중 끝난 것 (`state=open`에 내지 않는다)
FINISHED = frozenset({"completed", "rejected", "not_accepted"})
DEFAULT_TYPE = "cancel"
DEFAULT_REASON = "other"

_lock = threading.Lock()


def _get(row: dict[str, Any], m: ResourceMap, name: str, *, required: bool = True) -> Any:
    expr = m.fields.get(name)
    if expr is None:
        if required:
            raise MappingBroken(CLAIMS, name)
        return None
    try:
        value = pick(row, expr)
    except MissingField as exc:
        if required:
            raise MappingBroken(CLAIMS, exc.field) from exc
        return None
    table = m.values.get(name)
    if table is not None and isinstance(value, str):
        return table.get(value, value)
    return value


def guest_buyer(order_id: str) -> str:
    """회원이 아닌 주문의 구매자 이름표 (`resources._buyer`와 같은 모양)."""
    return f"guest-{order_id}"


def to_claim(
    site_row: dict[str, Any], m: ResourceMap, orders: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """고객사 신청 한 줄 → 받은함 재료. 번호(`seq`)는 아직 없다.

    `orders`는 주문 번호 → 주문 원본 행. 짝 식의 `order.<칸>`이 그 행을 본다(없으면 빈 행).
    """
    order_id = str(_get(site_row, m, "order_id"))
    row = {**site_row, "order": orders.get(order_id, {})}
    buyer = _get(row, m, "buyer_id", required=False)
    name = _get(row, m, "customer_name", required=False)
    description = _get(row, m, "description", required=False)
    return {
        "site_id": str(_get(row, m, "claim_id")),
        "order_id": order_id,
        "status": _get(row, m, "status"),
        "buyer_id": str(buyer) if buyer not in (None, "") else guest_buyer(order_id),
        "customer_name": name if isinstance(name, str) else "",
        "type": _get(row, m, "type", required=False) or DEFAULT_TYPE,
        "reason": _get(row, m, "reason", required=False) or DEFAULT_REASON,
        "description": description if isinstance(description, str) else "",
        "created_at": _get(row, m, "created_at", required=False),
    }


def number(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """신청마다 `seq`를 매기고 번호 짝을 적는다. 결정 전 신청은 결제 번호(= 주문 번호) 짝도.

    같은 주문의 신청이 여럿이면(거절 뒤 다시 신청) 결정 전 신청이 결제 번호 짝을 가진다.
    """
    seqs = links.numbers(CLAIMS, [c["site_id"] for c in claims])
    out = []
    for claim in claims:
        if claim["status"] == REQUESTED:
            links.remember(PAYMENTS, claim["order_id"], claim["site_id"])
        out.append({**claim, "seq": seqs[claim["site_id"]]})
    out.sort(key=lambda c: c["seq"])
    return out


def inbox_row(claim: dict[str, Any]) -> dict[str, Any]:
    """`state=new` 한 줄 (고객지원 `_claim_request`가 읽는 칸)."""
    return {
        "seq": claim["seq"],
        "key": claim["site_id"],
        # 신청마다 고정 (고객지원 클레임·안전 관문 기록이 이 사건 번호로 묶인다)
        "context_id": f"ctx_site_claim_{claim['seq']}",
        "buyer_id": claim["buyer_id"],
        "customer_name": claim["customer_name"],
        "order_id": claim["order_id"],
        "type": claim["type"],
        "reason": claim["reason"],
        "items": None,  # 사이트 신청은 주문(예약) 전체
        "unsealed": False,
        "evidence_count": 0,
        "description": claim["description"],
        "created_at": claim["created_at"],
    }


# ---------------------------------------------------------------- 처리 기록


def result_digest(view: Any) -> str:
    """고객지원 `inquiry_pull.claim_digest`와 같은 지문 (다르면 고객지원이 결과를 다시 적는다)."""
    text = json.dumps(view, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _read(folder: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def results() -> dict[str, dict[str, Any]]:
    """`seq` → 고객지원이 적은 처리 결과 요약 (`support_claim_id`·`status`·`result_digest`)."""
    return _read(adapter_dir())


def record_result(seq: str, body: dict[str, Any]) -> None:
    """고객지원 판정 결과를 적는다. 결과 본문은 지문만 남긴다."""
    claim_id = body.get("support_claim_id")
    status = body.get("status")
    folder = adapter_dir()
    with _lock:
        book = _read(folder)
        book[seq] = {
            "support_claim_id": claim_id if isinstance(claim_id, int) else None,
            "status": status if isinstance(status, str) else None,
            "result_digest": result_digest(body.get("result")),
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, folder / STATE_FILE)


def new_rows(claims: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int]:
    """`state=new`: 결정 전이고 고객지원 결과가 아직 없는 신청 (`seq` 순). (한 쪽, 전체 수)."""
    done = results()
    waiting = [c for c in claims if c["status"] == REQUESTED and str(c["seq"]) not in done]
    return [inbox_row(c) for c in waiting[:limit]], len(waiting)


def open_rows(after: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
    """`state=open`: 결과가 적혔고 끝나지 않은 신청 (`seq` 순, `after` 뒤). (한 쪽, 다음 after)."""
    rows = []
    for seq, entry in results().items():
        if not seq.isdigit() or int(seq) <= after:
            continue
        if entry.get("status") in FINISHED or entry.get("support_claim_id") is None:
            continue
        rows.append(
            {
                "seq": int(seq),
                "support_claim_id": entry["support_claim_id"],
                "status": entry.get("status"),
                "result_digest": entry.get("result_digest"),
            }
        )
    rows.sort(key=lambda r: r["seq"])
    page = rows[:limit]
    return page, (page[-1]["seq"] if len(rows) > limit else None)
