"""③-쓰기 뼈대 (W1): AI가 shop-api로 알고 보낸 쓰기를 판정하고 할 일함에 넣는다.

설계: `docs/superpowers/specs/2026-11-18-site-adapter-design.md` "③-쓰기"·"② 사람 반영".

판정 (위에서부터, 하나라도 걸리면 그 줄로 끝):

1. 보낸 서비스가 그 동작의 허용 서비스가 아님 → 403 `AGENT_NOT_ALLOWED` (`allowed_service`)
6. 같은 멱등 키로 이미 받음 → 같은 지문이면 앞 결과(같은 할 일), 다른 지문이면 409
   `IDEMPOTENCY_CONFLICT` (할 일을 새로 만들기 전에 본다)
3. 짝 파일에 그 동작의 `writes` 항목 없음 → 할 일함
4. 회사 연결에서 그 동작의 직접 쓰기를 꺼 둠(동작별, 기본 꺼짐) → 할 일함
- 돈이 걸린 동작(환불·쿠폰)은 관문 기록 확인(판정 2, W2) 전이라 무조건 할 일함
- 그 밖도 직접 쓰기(W3) 전이라 할 일함

그래서 지금은 1번에서 막히지 않은 모든 쓰기가 할 일함이다. 응답은 shop-api 성공 모양에
`applied: "pending_human"`·`handoff_task_id`를 더한 것이다(부르는 쪽은 완료가 아니라 사람 반영
대기로 본다).

쓰기 장부 `<변환기 폴더>/writes.json`: 키 = (보낸 서비스, 동작, 멱등 키의 sha256),
값 = 본문 지문(sha256)·상태(sending·done·rejected·unknown·handoff)·응답 요약·할 일 id·시각·
context_id. 본문 원문·멱등 키 원문·고객사 키는 남기지 않는다.

직접 쓰기 켜기: `<변환기 폴더>/direct_writes.json` `{"enabled": ["inquiry_answer", ...]}`.
없으면 모두 꺼짐. 지금은 읽기만 한다(켜는 화면은 W4). 돈이 걸린 동작은 W2 전에는 켜 있어도 꺼짐.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from ax_agent_template import routing
from ax_contracts.schemas import TENANT_ID_DEFAULT, UNASSIGNED_UNIT, HandoffCreate
from ax_dashboard_api import handoffs
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.site_adapter.mapping import (
    MappingError,
    WriteAction,
    adapter_dir,
    load_mapping,
)

LEDGER_FILE = "writes.json"
LOCK_FILE = "writes.json.lock"
SWITCH_FILE = "direct_writes.json"
DEFAULT_CONTEXT = "ctx_site_adapter"
FROM_SERVICE = "site-adapter"

logger = logging.getLogger("ax.site_adapter")

# 판정 1: 동작마다 받는 서비스 (에이전트 토큰의 이름)
ALLOWED_SERVICE: dict[WriteAction, str] = {
    "refund": "support",
    "claim_result": "support",
    "inquiry_answer": "support",
    "coupon_issue": "marketing",
    "banner_post": "marketing",
    "banner_end": "marketing",
    "payment_rollback": "infra",
    "fault_release": "infra",
}
# 돈이 걸린 동작: safety 기록 확인(판정 2, W2) 전에는 직접 쓰지 않는다
MONEY_ACTIONS: frozenset[str] = frozenset({"refund", "coupon_issue"})

# 할 일함에 보이는 일 이름
TITLES: dict[WriteAction, str] = {
    "refund": "사이트에서 환불 처리",
    "coupon_issue": "사이트에 쿠폰 발급",
    "banner_post": "사이트 공지·배너 게시",
    "banner_end": "사이트 공지·배너 내림",
    "claim_result": "신청 처리 결과를 사이트에 반영",
    "inquiry_answer": "문의 답변·상태를 사이트에 반영",
    "payment_rollback": "사이트 운영자에게 조치 요청 (결제 설정 되돌리기)",
    "fault_release": "사이트 운영자에게 조치 요청 (결제 장애 해제)",
}
REASONS = {
    "no_write_map": "짝 파일에 이 동작의 쓰기 짝이 없어요",
    "mapping_broken": "짝 파일이 깨져 쓰기 짝을 읽지 못했어요",
    "direct_off": "회사 연결에서 이 동작의 직접 쓰기가 꺼져 있어요",
    "gate_not_ready": "돈이 걸린 쓰기라 관문 기록 확인(W2) 전에는 사람이 반영해요",
    "direct_not_ready": "직접 쓰기(W3) 전이라 사람이 반영해요",
    "left_unfinished": "앞선 쓰기가 끝나지 않은 채 남아 사이트 반영 여부를 사람이 확인해요",
}

_lock = threading.Lock()


@dataclass
class WriteRequest:
    """변환기가 받은 쓰기 하나. 본문 원문은 지문·요약으로만 넘긴다."""

    action: WriteAction
    service: str
    shop_path: str
    idempotency_key: str
    fingerprint: str
    context_id: str | None
    # 사람이 처리할 만큼의 요약 (할 일함 `input`, 개인정보 최소)
    summary: dict[str, Any] = field(default_factory=dict)
    # shop-api 성공 응답 모양 (`applied`·`handoff_task_id`는 여기서 더한다)
    response: dict[str, Any] = field(default_factory=dict)
    # 응답에서 할 일 id로 채울 칸 (쿠폰 `coupon_id`: AI가 필수로 본다, 설계 ②)
    task_id_fields: tuple[str, ...] = ()


def digest(value: Any) -> str:
    """값의 sha256 (JSON은 키 순서를 고정해 같은 내용이면 같은 지문)."""
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def allowed_service(action: WriteAction, service: str | None) -> None:
    """판정 1: 그 동작의 허용 서비스가 아니면 403 `AGENT_NOT_ALLOWED` (할 일함에도 안 넣음)."""
    if service != ALLOWED_SERVICE[action]:
        logger.warning("허용되지 않은 사이트 쓰기: action=%s service=%s", action, service)
        raise ApiError(
            403,
            "AGENT_NOT_ALLOWED",
            f"이 쓰기({action})는 {ALLOWED_SERVICE[action]} 에이전트만 보낼 수 있어요",
        )


def direct_write_on(action: WriteAction) -> bool:
    """회사 연결에서 그 동작의 직접 쓰기를 켰는지. 파일이 없거나 깨졌으면 꺼짐(기본)."""
    if action in MONEY_ACTIONS:
        return False  # 관문 기록 확인(W2) 전에는 켤 수 없다
    try:
        raw = json.loads((adapter_dir() / SWITCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    enabled = raw.get("enabled") if isinstance(raw, dict) else None
    return isinstance(enabled, list) and action in enabled


def route(action: WriteAction) -> str:
    """직접 쓰기로 못 가는 까닭 (판정 3·4, 그리고 W1에 아직 없는 단계)."""
    try:
        mapping = load_mapping()
    except MappingError:
        return "mapping_broken"
    if mapping is None or action not in mapping.writes:
        return "no_write_map"
    if not direct_write_on(action):
        return "direct_off"
    return "gate_not_ready" if action in MONEY_ACTIONS else "direct_not_ready"


def _os_lock(handle: IO[str]) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # 잡힐 때까지 (최대 약 10초)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _os_unlock(handle: IO[str]) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked(folder: Path) -> Iterator[None]:
    """장부 잠금: 같은 프로세스는 스레드 잠금, 다른 프로세스는 OS 파일 잠금."""
    with _lock:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / LOCK_FILE).open("a+", encoding="utf-8") as handle:
            _os_lock(handle)
            try:
                yield
            finally:
                _os_unlock(handle)


def read_ledger(folder: Path | None = None) -> dict[str, dict[str, Any]]:
    path = (folder or adapter_dir()) / LEDGER_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # 깨진 장부를 빈 장부로 보면 같은 쓰기를 두 번 넣게 된다: 받지 않는다
        raise ApiError(503, "WRITE_LEDGER_BROKEN", "쓰기 장부를 읽지 못했어요") from exc
    return raw if isinstance(raw, dict) else {}


def _save_ledger(folder: Path, ledger: dict[str, dict[str, Any]]) -> None:
    path = folder / LEDGER_FILE
    tmp = folder / (LEDGER_FILE + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _handoff(req: WriteRequest, reason: str) -> str:
    """할 일함에 넣고 할 일 id. 같은 멱등 키면 할 일함이 처음 일을 돌려준다."""
    skill = f"site_apply_{req.action}"
    assignment = routing.assignment_for(req.service, skill)
    task = handoffs.get_store().add(
        HandoffCreate(
            tenant_id=os.getenv("TENANT_ID", TENANT_ID_DEFAULT),
            context_id=req.context_id or DEFAULT_CONTEXT,
            from_service=FROM_SERVICE,
            to_service=req.service,
            skill=skill,
            input={"title": TITLES[req.action], "shop_path": req.shop_path, **req.summary},
            reason=REASONS[reason],
            assignee_unit=assignment.unit if assignment else UNASSIGNED_UNIT,
            assignee=assignment.assignee if assignment else None,
            # 서비스·동작을 붙여 다른 동작의 같은 키와 겹치지 않게 한다
            idempotency_key=f"site_adapter:{req.service}:{req.action}:{req.idempotency_key}",
        )
    )
    return task.task_id


def apply(req: WriteRequest) -> tuple[dict[str, Any], bool]:
    """판정해 (응답 `data`, 앞 결과를 그대로 돌려줬는지)."""
    allowed_service(req.action, req.service)
    folder = adapter_dir()
    key_digest = digest(req.idempotency_key)
    entry_key = f"{req.service}|{req.action}|{key_digest}"
    with _locked(folder):
        ledger = read_ledger(folder)
        prior = ledger.get(entry_key)
        if prior is not None:
            if prior.get("fingerprint") != req.fingerprint:
                logger.warning(
                    "같은 멱등 키에 다른 본문: service=%s action=%s", req.service, req.action
                )
                raise ApiError(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "같은 멱등 키로 내용이 다른 쓰기가 왔어요. 새 키로 보내 주세요",
                )
            response = prior.get("response")
            if prior.get("status") in ("handoff", "done") and isinstance(response, dict):
                return dict(response), True
        # 앞 기록이 sending·unknown으로 남았으면 다시 보내지 않고 사람에게 확인을 맡긴다
        reason = "left_unfinished" if prior is not None else route(req.action)
        task_id = _handoff(req, reason)
        data = {**req.response, "applied": "pending_human", "handoff_task_id": task_id}
        data.update(dict.fromkeys(req.task_id_fields, task_id))
        ledger[entry_key] = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "service": req.service,
            "action": req.action,
            "shop_path": req.shop_path,
            "context_id": req.context_id,
            "key_digest": key_digest,
            "fingerprint": req.fingerprint,
            "status": "handoff",
            "reason": reason,
            "handoff_task_id": task_id,
            "response": data,
        }
        _save_ledger(folder, ledger)
    logger.info(
        "사이트 쓰기를 할 일함으로: service=%s action=%s reason=%s task=%s",
        req.service,
        req.action,
        reason,
        task_id,
    )
    return data, False
