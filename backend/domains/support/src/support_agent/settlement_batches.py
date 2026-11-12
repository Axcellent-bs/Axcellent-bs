"""판매자 정산 묶음: 지급 승인 요청과 그 상태.

- 정산 기간(주)마다 한 묶음. 보류 사유가 없고 줄 돈이 있는 판매자를 묶어
  `execute("confirm_settlement")`로 요청하면 안전 관문이 항상 C로 보류하고 사람이 승인한다.
  돈을 실제로 보내는 연동은 없다(승인 = 지급 기록)
- 요청 중인 묶음은 읽을 때마다 안전 장치의 보류 건 상태를 다시 읽어 반영한다
  - 승인 → approved, 거절·시간 초과 → rejected(`closed_reason`)
  - 안전 장치가 그 보류 건을 모르면(404, 보류 큐는 메모리에만 있어 재시작하면 비워진다)
    먼저 실행 기록(`/api/safety/executions`)에서 같은 기간의 `confirm_settlement`가 실행됐는지
    본다. 실행됐으면 approved, 기록도 없을 때만 rejected(`closed_reason: review_missing`).
    `review_missing` 뒤 다시 요청하기 전에도 실행 기록을 한 번 더 본다(두 번 요청하지 않게)
  - 승인된(approved) 묶음은 404가 나도 되돌리지 않는다. 연결 실패·5xx는 그대로 둔다
- 묶음 기록은 `SUPPORT_SETTLEMENT_FILE`(JSON)에 남긴다. 비우면 메모리에만 둔다(테스트)
- 옮기기(2026-11-12): 이 파일이 아직 없고 `SUPPORT_SETTLEMENT_IMPORT_FILE`(재무가 쓰던
  `finance_state.json`)이 있으면, 처음 한 번 거기 남은 묶음 기록(`records.batches`)을 가져온다.
  이미 승인된 주를 잊고 다시 지급을 요청하지 않게 한다
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ax_agent_template.config import agent_url
from support_agent import settlement_config as sc

logger = logging.getLogger("ax.support.settlement_batches")

ENV_FILE = "SUPPORT_SETTLEMENT_FILE"
ENV_IMPORT = "SUPPORT_SETTLEMENT_IMPORT_FILE"
REVIEW_CHECK_SECONDS = 3.0
STATUS_OF_DECISION = {"held": "requested", "verifying": "requested", "executed": "approved"}


class BatchStore:
    """정산 기간 → 묶음 기록. 바꿀 때마다 파일에 쓴다(작고 드물게 바뀐다)."""

    def __init__(self, path: Path | None, import_from: Path | None = None) -> None:
        self.path = path
        self.batches: dict[str, dict[str, Any]] = {}
        if path is not None and path.exists():
            # 못 읽으면 서버를 띄우지 않는다. 지급 기록을 잊은 채 같은 주를 다시 요청하지 않게 한다
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.batches = dict(raw.get("batches") or {})
        elif import_from is not None and import_from.exists():
            self.batches = imported_batches(import_from)
            logger.info("재무 기록에서 정산 묶음 %d개를 가져왔다", len(self.batches))
            self.save()

    def get(self, period: str) -> dict[str, Any] | None:
        return self.batches.get(period)

    def put(self, period: str, batch: dict[str, Any]) -> None:
        self.batches[period] = batch
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"batches": self.batches}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(self.path)


def imported_batches(path: Path) -> dict[str, dict[str, Any]]:
    """재무가 쓰던 `finance_state.json`의 `records.batches`. 못 읽으면 예외."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw.get("records") if isinstance(raw, dict) else None
    batches = records.get("batches") if isinstance(records, dict) else None
    return {str(k): dict(v) for k, v in (batches or {}).items() if isinstance(v, dict)}


def build_store() -> BatchStore:
    raw = os.getenv(ENV_FILE, "").strip()
    source = os.getenv(ENV_IMPORT, "").strip()
    return BatchStore(Path(raw) if raw else None, Path(source) if source else None)


STORE = build_store()


def use_store(store: BatchStore) -> None:
    """테스트가 저장소를 바꿔 끼운다."""
    global STORE
    STORE = store


def context_id_of(period: str) -> str:
    """정산 기간 묶음 요청의 context_id.

    시도마다 멱등 키는 `{context_id}:confirm_settlement:{기간}:{시도}`.
    """
    return f"ctx_req_settlement_{period}"


def key_prefix(period: str) -> str:
    return f"{context_id_of(period)}:confirm_settlement:{period}:"


class SafetyUnreachable(Exception):
    """안전 장치 실행 기록을 읽지 못했다. 모르는 채로 다시 요청하지 않는다."""


async def executed_in_safety(period: str, execution_id: str | None = None) -> dict[str, Any] | None:
    """안전 장치에 이 기간의 `confirm_settlement`가 실행(승인)된 기록이 있으면 그 기록.

    보류 건을 잃었어도(404) 실행 기록이 남아 있으면 이미 승인된 것이다. 같은 기간의 멱등 키
    접두사(어느 시도든)나 같은 `execution_id`로 찾는다. 읽지 못하면 `SafetyUnreachable`.
    """
    url = f"{agent_url('safety')}/api/safety/executions"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url, params={"context_id": context_id_of(period)}, timeout=REVIEW_CHECK_SECONDS
            )
        if response.status_code != 200:
            raise SafetyUnreachable(f"HTTP {response.status_code}")
        records = response.json()["data"]
        if not isinstance(records, list):
            raise SafetyUnreachable("실행 기록 모양이 아니다")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SafetyUnreachable(type(exc).__name__) from exc
    prefix = key_prefix(period)
    for record in records:
        if not isinstance(record, dict):
            continue
        request = record.get("request") or {}
        decision = record.get("decision") or {}
        if request.get("action") != "confirm_settlement" or decision.get("status") != "executed":
            continue
        key = str(request.get("idempotency_key") or "")
        if key.startswith(prefix) or (execution_id and record.get("execution_id") == execution_id):
            return record
    return None


def decided_time(value: Any) -> str | None:
    """안전 장치가 준 결정·실행 시각(ISO)을 `...Z` 형식으로. 없거나 못 읽으면 None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.isoformat().replace("+00:00", "Z")


def set_approved_at(batch: dict[str, Any], decided: Any) -> None:
    """승인 시각을 남긴다. 이미 있으면 절대 바꾸지 않는다.

    안전 장치가 결정한 시각(보류 건 `decided_at`, 없으면 실행 기록 `created_at`)을 쓰고, 모를 때만
    지금 시각을 쓴다. 다시 읽은 시각을 쓰면 재무 은행 출금 대조(승인일부터 이체를 찾음)가 어긋난다.
    """
    if batch.get("approved_at"):
        return
    batch["approved_at"] = decided_time(decided) or sc.iso(sc.now())


def mark_approved(batch: dict[str, Any], record: dict[str, Any]) -> None:
    """안전 장치 실행 기록으로 승인을 반영한다 (보류 건을 잃은 뒤에도 두 번 요청하지 않게)."""
    batch["status"] = "approved"
    batch.pop("closed_reason", None)
    batch["execution_id"] = record.get("execution_id") or batch.get("execution_id")
    set_approved_at(batch, record.get("created_at"))


async def refresh(batch: dict[str, Any], period: str) -> bool:
    """요청 중인 묶음의 보류 건 결정을 안전 장치에서 읽어 반영한다. 바뀌었으면 True.

    `requested`만 본다(승인된 묶음은 절대 되돌리지 않는다). 보류 건이 404면 실행 기록을 먼저
    보고, 실행됐으면 approved, 기록도 없을 때만 rejected(`review_missing`). 실행 기록을 못 읽으면
    그대로 둔다.
    """
    if batch.get("status") != "requested" or not batch.get("review_id"):
        return False
    url = f"{agent_url('safety')}/api/safety/reviews/{batch['review_id']}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=REVIEW_CHECK_SECONDS)
        if response.status_code == 404:
            return await _review_gone(batch, period)
        data = response.json()["data"] if response.status_code == 200 else {}
        status = data["status"] if data else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return False
    if status == "approved":
        batch["status"] = "approved"
        set_approved_at(batch, data.get("decided_at"))
        return True
    if status in ("rejected", "expired"):
        batch["status"] = "rejected"
        batch["closed_reason"] = status
        return True
    return False


async def _review_gone(batch: dict[str, Any], period: str) -> bool:
    """보류 건이 안전 장치에 없다(404). 실행 기록으로 승인됐는지 먼저 본다."""
    try:
        record = await executed_in_safety(period, batch.get("execution_id"))
    except SafetyUnreachable:
        return False  # 모르면 요청 중으로 둔다 (다시 요청하게 열지 않는다)
    if record is not None:
        mark_approved(batch, record)
        return True
    batch["status"] = "rejected"
    batch["closed_reason"] = "review_missing"
    return True


async def refreshed(period: str) -> dict[str, Any] | None:
    """기간의 묶음을 안전 장치 상태로 맞춰 돌려준다."""
    batch = STORE.get(period)
    if batch is not None and await refresh(batch, period):
        STORE.save()
    return batch


async def refreshed_all() -> dict[str, dict[str, Any]]:
    changed = False
    for period, batch in STORE.batches.items():
        changed = await refresh(batch, period) or changed
    if changed:
        STORE.save()
    return STORE.batches


def batch_status(decision_status: str) -> str:
    return STATUS_OF_DECISION.get(decision_status, "rejected")
