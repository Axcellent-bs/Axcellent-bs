"""판매자 정산 묶음: 지급 승인 요청과 그 상태.

- 정산 기간(주)마다 한 묶음. 보류 사유가 없고 줄 돈이 있는 판매자를 묶어
  `execute("confirm_settlement")`로 요청하면 안전 관문이 항상 C로 보류하고 사람이 승인한다.
  돈을 실제로 보내는 연동은 없다(승인 = 지급 기록)
- 요청 중인 묶음은 읽을 때마다 안전 장치의 보류 건 상태를 다시 읽어 반영한다
  - 승인 → approved, 거절·시간 초과 → rejected(`closed_reason`)
  - 안전 장치가 그 보류 건을 모르면(404, 보류 큐는 메모리에만 있어 재시작하면 비워진다)
    rejected(`closed_reason: review_missing`).
    승인된 적이 없으니 다시 요청해도 두 번 지급되지 않는다
  - 연결 실패·5xx는 그대로 둔다
- 묶음 기록은 `SUPPORT_SETTLEMENT_FILE`(JSON)에 남긴다. 비우면 메모리에만 둔다(테스트)
- 옮기기(2026-11-12): 이 파일이 아직 없고 `SUPPORT_SETTLEMENT_IMPORT_FILE`(재무가 쓰던
  `finance_state.json`)이 있으면, 처음 한 번 거기 남은 묶음 기록(`records.batches`)을 가져온다.
  이미 승인된 주를 잊고 다시 지급을 요청하지 않게 한다
"""

from __future__ import annotations

import json
import logging
import os
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


async def refresh(batch: dict[str, Any]) -> bool:
    """요청 중인 묶음의 보류 건 결정을 안전 장치에서 읽어 반영한다. 바뀌었으면 True."""
    if batch.get("status") != "requested" or not batch.get("review_id"):
        return False
    url = f"{agent_url('safety')}/api/safety/reviews/{batch['review_id']}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=REVIEW_CHECK_SECONDS)
        if response.status_code == 404:
            batch["status"] = "rejected"
            batch["closed_reason"] = "review_missing"
            return True
        status = response.json()["data"]["status"] if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return False
    if status == "approved":
        batch["status"] = "approved"
        batch.setdefault("approved_at", sc.iso(sc.now()))
        return True
    if status in ("rejected", "expired"):
        batch["status"] = "rejected"
        batch["closed_reason"] = status
        return True
    return False


async def refreshed(period: str) -> dict[str, Any] | None:
    """기간의 묶음을 안전 장치 상태로 맞춰 돌려준다."""
    batch = STORE.get(period)
    if batch is not None and await refresh(batch):
        STORE.save()
    return batch


async def refreshed_all() -> dict[str, dict[str, Any]]:
    changed = False
    for batch in STORE.batches.values():
        changed = await refresh(batch) or changed
    if changed:
        STORE.save()
    return STORE.batches


def batch_status(decision_status: str) -> str:
    return STATUS_OF_DECISION.get(decision_status, "rejected")
