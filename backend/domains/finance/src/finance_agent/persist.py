"""재무 기록을 파일에 남기고 다시 켤 때 읽는다 (`FINANCE_STATE_FILE`).

메모리에만 있던 기록(판매자 심사·정산 보류, 이벤트로 쌓은 쿠폰·환불 장부, 사람의 결산 확정·
대사 메모·정산 묶음 요청, 끝난 상태 변경 결과)을 JSON 하나에 담는다. 백엔드를 다시 켜도
재무 화면과 멱등 처리(같은 요청을 두 번 실행하지 않음)가 이어진다.

- 경로가 없으면(테스트·단독 실행) 아무것도 하지 않는다
- 몇 초마다 바뀐 것만 쓰고, 서버를 끌 때 한 번 더 쓴다
- 임시 파일에 쓴 뒤 바꿔 끼워 반쯤 쓴 파일이 남지 않게 한다
- 읽다가 형식이 틀리면 버리지 않고 옆에 `.broken`으로 옮기고 빈 상태로 시작한다
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from ax_contracts.schemas import AgentResult
from finance_agent.handlers import State

logger = logging.getLogger(__name__)

VERSION = 1
SAVE_SECONDS = 5.0


def state_file() -> Path | None:
    raw = os.getenv("FINANCE_STATE_FILE", "").strip()
    return Path(raw) if raw else None


def dump(s: State) -> dict[str, Any]:
    ledger, records = s.ledger, s.records
    return {
        "version": VERSION,
        "ledger": {
            "coupons": ledger.coupons,
            "refunds": ledger.refunds,
            "seen_events": sorted(ledger.seen_events),
            "done": {k: v.model_dump(mode="json") for k, v in ledger.done.items()},
            "market_ops": ledger.market_ops,
            "support_costs": {str(k): v for k, v in ledger.support_costs.items()},
            "coupon_events": ledger.coupon_events,
        },
        "records": {
            "closings": [[p, d, v] for (p, d), v in records.closings.items()],
            "recon_notes": records.recon_notes,
            "recon_note_log": records.recon_note_log,
            "batches": records.batches,
        },
    }


def restore(s: State, data: dict[str, Any]) -> None:
    if data.get("version") != VERSION:
        raise ValueError(f"모르는 재무 기록 버전: {data.get('version')}")
    led, rec = data["ledger"], data["records"]
    s.ledger.coupons = {k: dict(v) for k, v in led["coupons"].items()}
    s.ledger.refunds = dict(led["refunds"])
    s.ledger.seen_events = set(led["seen_events"])
    s.ledger.done = {k: AgentResult.model_validate(v) for k, v in led["done"].items()}
    s.ledger.market_ops = list(led["market_ops"])
    s.ledger.support_costs = {int(k): v for k, v in led["support_costs"].items()}
    s.ledger.coupon_events = {k: list(v) for k, v in led["coupon_events"].items()}
    s.records.closings = {(p, d): v for p, d, v in rec["closings"]}
    s.records.recon_notes = dict(rec["recon_notes"])
    s.records.recon_note_log = list(rec["recon_note_log"])
    s.records.batches = dict(rec["batches"])


def _text(s: State) -> str:
    return json.dumps(dump(s), ensure_ascii=False, sort_keys=True)


def load(s: State, path: Path) -> bool:
    """파일이 있으면 읽어 넣는다. 읽었으면 True."""
    if not path.exists():
        return False
    try:
        restore(s, json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError) as exc:
        broken = path.with_suffix(path.suffix + ".broken")
        path.replace(broken)
        logger.warning(
            "재무 기록 파일을 읽지 못해 %s로 옮기고 빈 상태로 시작한다 (%s)", broken, exc
        )
        return False
    return True


def save(s: State, path: Path, last: str | None = None) -> str:
    """바뀌었으면 쓴다. 쓴(또는 그대로인) 내용을 돌려준다."""
    text = _text(s)
    if text != last:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return text


async def save_loop(get_state: Any, path: Path, last: str | None) -> None:
    while True:
        await asyncio.sleep(SAVE_SECONDS)
        try:
            last = save(get_state(), path, last)
        except Exception:  # 다음 회차에 다시 쓴다
            logger.exception("재무 기록 저장 중 오류")
