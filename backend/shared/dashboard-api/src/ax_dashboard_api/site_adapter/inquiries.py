"""① 문의 받은함: 고객사 1:1 문의 → shop-api `GET /admin/inquiries` 모양 (11/25).

고객지원(`support_agent.inquiry_pull`)은 `state=new&limit=`로 읽을 차례인 문의를 받아
`create_inquiry`로 넣고, `POST /admin/inquiries/{seq}/synced`(성공)·`/failed`(실패)로 알린다.
줄마다 `seq`(정수)·`key`·`context_id`·`buyer_id`(`buy_` + 숫자 4~9자리)·`customer_name`·
`order_id`·`subject`·`message`, `meta.total`·`meta.failed`.

짝 (`mapping.inboxes.inquiries`, 읽기 짝과 같은 `ResourceMap`):

- `fields`: `inquiry_id`(고객사 문의 id)·`subject`·`message`(고객이 쓴 글, untrusted).
  선택 `status`(값 표로 `open`이면 아직 답 전)·`order_id`·`buyer_id`·`customer_name`·`created_at`.
  짝 식 안에서 `order.<칸>`은 그 문의가 가리키는 주문 원본 행이다(없으면 빈 행)
- `status` 짝이 없으면 모든 문의가 답 전으로 본다

번호 짝:

- `seq` = 고객사 문의 id에 매긴 정수(`links.numbers("inquiries", …)`)
- 구매자: 고객지원은 `buy_<숫자>`만 받는다. 고객사 구매자 id(없으면 `guest-inq-<문의 id>`)에
  `links.numbers("buyers", …)`로 고정 번호를 매겨 `buy_0001` 모양으로 준다.
  같은 구매자는 늘 같은 번호

처리 기록 (`<변환기 폴더>/inquiries.json`):

- `synced`면 `support_inquiry_id`만, `failed`면 실패 횟수만 남긴다(문의 본문·이름은 남기지 않는다)
- 읽어 간 문의와 실패가 `MAX_FAILS`번 쌓인 문의는 `state=new`에서 빠진다(실패 수는 `meta.failed`).
  `retry`는 실패 기록을 지워 다시 읽는 차례로 돌린다
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ax_dashboard_api.site_adapter import links
from ax_dashboard_api.site_adapter.mapping import MissingField, ResourceMap, adapter_dir, pick
from ax_dashboard_api.site_adapter.resources import MappingBroken

STATE_FILE = "inquiries.json"
INQUIRIES = "inquiries"
BUYERS = "buyers"
OPEN = "open"
MAX_FAILS = 3

_lock = threading.Lock()


def _get(row: dict[str, Any], m: ResourceMap, name: str, *, required: bool = True) -> Any:
    expr = m.fields.get(name)
    if expr is None:
        if required:
            raise MappingBroken(INQUIRIES, name)
        return None
    try:
        value = pick(row, expr)
    except MissingField as exc:
        if required:
            raise MappingBroken(INQUIRIES, exc.field) from exc
        return None
    table = m.values.get(name)
    if table is not None and isinstance(value, str):
        return table.get(value, value)
    return value


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def to_inquiry(
    site_row: dict[str, Any], m: ResourceMap, orders: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """고객사 문의 한 줄 → 받은함 재료. 번호(`seq`·구매자 번호)는 아직 없다."""
    order_id = _get(site_row, m, "order_id", required=False)
    order_id = str(order_id) if order_id not in (None, "") else None
    row = {**site_row, "order": orders.get(order_id or "", {})}
    site_id = str(_get(row, m, "inquiry_id"))
    buyer = _get(row, m, "buyer_id", required=False)
    status = _get(row, m, "status", required=False) if "status" in m.fields else OPEN
    return {
        "site_id": site_id,
        "buyer_key": str(buyer) if buyer not in (None, "") else f"guest-inq-{site_id}",
        "customer_name": _text(_get(row, m, "customer_name", required=False)),
        "order_id": order_id,
        "subject": _text(_get(row, m, "subject")),
        "message": _text(_get(row, m, "message")),
        "status": status,
        "created_at": _get(row, m, "created_at", required=False),
    }


def number(inquiries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """문의마다 `seq`, 구매자마다 `buy_` 번호를 매긴다(같은 id는 늘 같은 번호)."""
    seqs = links.numbers(INQUIRIES, [q["site_id"] for q in inquiries])
    buyers = links.numbers(BUYERS, list(dict.fromkeys(q["buyer_key"] for q in inquiries)))
    out = [
        {**q, "seq": seqs[q["site_id"]], "buyer_id": f"buy_{buyers[q['buyer_key']]:04d}"}
        for q in inquiries
    ]
    out.sort(key=lambda q: q["seq"])
    return out


def inbox_row(q: dict[str, Any]) -> dict[str, Any]:
    """`state=new` 한 줄 (고객지원 `inquiry_pull._request`가 읽는 칸)."""
    return {
        "seq": q["seq"],
        "key": q["site_id"],
        "context_id": f"ctx_site_inquiry_{q['seq']}",
        "buyer_id": q["buyer_id"],
        "customer_name": q["customer_name"],
        "order_id": q["order_id"],
        "subject": q["subject"],
        "message": q["message"],
        "created_at": q["created_at"],
    }


# ---------------------------------------------------------------- 처리 기록


def _read(folder: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def book() -> dict[str, dict[str, Any]]:
    """`seq` → 처리 기록 (`support_inquiry_id`·`fails`)."""
    return _read(adapter_dir())


def record(seq: str, action: str, body: dict[str, Any]) -> None:
    """고객지원 알림(`synced`·`failed`·`retry`)을 처리 기록에 반영한다. 그 밖의 동작은 무시."""
    if action not in ("synced", "failed", "retry") or not seq.isdigit():
        return
    folder = adapter_dir()
    with _lock:
        state = _read(folder)
        entry = state.get(seq, {})
        if action == "synced":
            sid = body.get("support_inquiry_id")
            entry = {"support_inquiry_id": sid if isinstance(sid, int) else None}
        elif action == "failed":
            entry = {**entry, "fails": int(entry.get("fails") or 0) + 1}
        else:  # retry
            entry = {k: v for k, v in entry.items() if k != "fails"}
        entry["at"] = datetime.now(UTC).isoformat(timespec="seconds")
        state[seq] = entry
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, folder / STATE_FILE)


def new_rows(inquiries: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int, int]:
    """`state=new`: 답 전이고 아직 안 읽어 갔고 실패로 빠지지 않은 문의.

    (한 쪽, 기다리는 수, 실패 수).
    """
    state = book()
    waiting: list[dict[str, Any]] = []
    failed = 0
    for q in inquiries:
        entry = state.get(str(q["seq"]), {})
        if q["status"] != OPEN or "support_inquiry_id" in entry:
            continue
        if int(entry.get("fails") or 0) >= MAX_FAILS:
            failed += 1
            continue
        waiting.append(q)
    return [inbox_row(q) for q in waiting[:limit]], len(waiting), failed
