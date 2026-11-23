"""③-쓰기 (W1 뼈대 + W2 관문 확인 + W3 직접 쓰기): AI가 shop-api로 알고 보낸 쓰기를 판정해
고객사에 직접 쓰거나 할 일함에 넣는다.

설계: `docs/superpowers/specs/2026-11-18-site-adapter-design.md` "③-쓰기"·"② 사람 반영".

판정 (위에서부터, 하나라도 걸리면 그 줄로 끝):

1. 보낸 서비스가 그 동작의 허용 서비스가 아님 → 403 `AGENT_NOT_ALLOWED` (`allowed_service`)
2. 돈이 걸린 동작(환불·쿠폰)인데 safety 실행 기록 확인 실패 → 403 `GATE_NOT_PASSED`,
   safety에 닿지 못함 → 503 `GATE_UNAVAILABLE` (`gate_check.confirm`). 장부 잠금 밖에서
   safety를 부르고, 실패하면 장부·할 일함에 아무것도 남기지 않는다
6. 같은 멱등 키로 이미 받음 → 같은 지문이면 앞 결과(같은 할 일), 다른 지문이면 409
   `IDEMPOTENCY_CONFLICT` (할 일을 새로 만들기 전에 본다)
3. 짝 파일에 그 동작의 `writes` 항목 없음 → 할 일함
4. 회사 연결에서 그 동작의 직접 쓰기를 꺼 둠(동작별, 기본 꺼짐) → 할 일함
5. 키에 쓰기 권한이 없다고 기록됨(앞서 고객사가 401·403, `direct.denied_actions`) → 할 일함
- 모두 지나면 "직접 쓰기 가능"(`DIRECT_READY`, 판정 7). 본문 식을 계산하지 못하면(칸·번호 짝
  없음) 또는 시간당 호출 한도에 닿으면 할 일함. 아니면 장부에 `sending`을 먼저 쓰고(파일 잠금)
  고객사에 한 번 보낸다(`direct`). 응답 처리:
  - 짝의 `ok` 코드 → `done`, shop-api 성공 모양 + `applied: "site"` (+ 짝 `result` 칸)
  - 400·404·409·422 → `rejected`, 409 `SITE_REJECTED` + `error.site_code` (재시도 안 함)
  - 401·403 → `rejected` + 그 동작을 "키에 쓰기 권한 없음"으로 표시, 할 일함
  - 짝의 `partial_codes`에 든 오류 코드(W5, 예: 누비투어 502 `REFUND_FAILED`) → `partial`,
    재시도 안 함, 할 일함 "결제사 환불 확인"(반영은 됐으니 모름이 아니다)
  - 그 밖(429·5xx·3xx·시간 초과·연결 끊김) → `unknown`, 재시도 안 함, 할 일함
    "사이트에 반영됐는지 확인 후 처리"(멱등 키·본문 요약)

할 일함으로 간 쓰기의 응답은 shop-api 성공 모양에 `applied: "pending_human"`·`handoff_task_id`를
더한 것이다(부르는 쪽은 완료가 아니라 사람 반영 대기로 본다).

쓰기 장부 `<변환기 폴더>/writes.json`: 키 = (보낸 서비스, 동작, 멱등 키의 sha256),
값 = 본문 지문(sha256)·상태(sending·done·rejected·partial·unknown·handoff)·응답 요약·할 일 id·시각·
context_id·확인한 safety `execution_id`(돈이 걸린 동작)·고객사 메서드·호스트·경로 틀·응답 코드·
`x-request-id`·오류 코드. 본문 원문·멱등 키 원문·고객사 키·사이트 오류 문구는 남기지 않는다.

직접 쓰기 켜기: `<변환기 폴더>/direct_writes.json` `{"enabled": ["inquiry_answer", ...]}`.
없으면 모두 꺼짐. 회사 연결 화면(W4, `write_setup.py`)이 동작별로 켜고 끈다(`set_direct_write`,
누가 언제 바꿨는지 `changes`). 돈이 걸린 동작은 관문 확인을 지난 쓰기(`execution_id` 있음)에서만
켜진 것으로 본다.
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
from ax_contracts.schemas import UNASSIGNED_UNIT, HandoffCreate
from ax_dashboard_api import handoffs
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.site_adapter import direct, gate_check, upstream
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
# 고객사 직접 쓰기 시간당 상한 (보안 요구 5번). 넘으면 보내지 않고 할 일함
WRITES_PER_HOUR_ENV = "AX_SITE_ADAPTER_WRITES_PER_HOUR"
DEFAULT_WRITES_PER_HOUR = 60

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
# 돈이 걸린 동작: safety 실행 기록 확인(판정 2)을 지나야 받는다
MONEY_ACTIONS: frozenset[str] = frozenset({"refund", "coupon_issue"})
# 판정 3·4를 모두 지남 = 직접 쓰기 가능 (W3가 여기서 고객사를 부른다)
DIRECT_READY = "direct_ready"

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
    # W1 장부에 남은 까닭 (W2부터 새로 쓰지 않는다)
    "gate_not_ready": "돈이 걸린 쓰기라 관문 기록 확인(W2) 전에는 사람이 반영해요",
    "direct_not_ready": "직접 쓰기(W3) 전이라 사람이 반영해요",
    "left_unfinished": "앞선 쓰기가 끝나지 않은 채 남아 사이트 반영 여부를 사람이 확인해요",
    # W3 직접 쓰기
    "no_write_permission": "사이트 키에 쓰기 권한이 없어 사람이 반영해요 (회사 연결에서 키 확인)",
    "calc_failed": "쓰기 짝의 본문 칸을 요청에서 찾지 못해 사람이 반영해요",
    "link_missing": "사이트 쪽 번호 짝(link)이 아직 없어 사람이 반영해요",
    "rate_limited": "사이트 쓰기 시간당 한도에 닿아 사람이 반영해요",
    "site_unknown": "사이트에 반영됐는지 알 수 없어요. 사이트에서 확인한 뒤 처리해 주세요",
    # W5: 사이트가 쓰기는 반영했는데 뒤따르는 일만 실패했다고 답함 (짝의 `partial_codes`)
    "site_refund_failed": (
        "사이트에 취소 결정은 반영됐지만 결제사 환불이 실패했어요. "
        "사이트 결제 내역에서 환불을 확인하고 다시 시도해 주세요"
    ),
    "site_partial": "사이트에 반영은 됐지만 뒤따르는 처리가 실패했어요. 사이트에서 확인해 주세요",
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
    # 판정 2에서 safety 기록과 맞춰 볼 금액 칸 (돈이 걸린 동작만, 본문에서 꺼낸 값)
    gate_claim: dict[str, Any] = field(default_factory=dict)
    # 판정 2를 지나며 확인한 safety `execution_id` (`apply`가 채운다)
    execution_id: str | None = None
    # 직접 쓰기 본문 식의 재료: 요청 본문(장부·로그·할 일함에 남기지 않는다)과 shop-api 경로 값
    source: dict[str, Any] = field(default_factory=dict, repr=False)
    path_values: dict[str, str] = field(default_factory=dict)


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


def direct_write_on(action: WriteAction, execution_id: str | None = None) -> bool:
    """회사 연결에서 그 동작의 직접 쓰기를 켰는지. 파일이 없거나 깨졌으면 꺼짐(기본).

    돈이 걸린 동작은 관문 기록을 확인한 쓰기(`execution_id` 있음)에서만 켜진 것으로 본다.
    """
    if action in MONEY_ACTIONS and not execution_id:
        return False
    try:
        raw = json.loads((adapter_dir() / SWITCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    enabled = raw.get("enabled") if isinstance(raw, dict) else None
    return isinstance(enabled, list) and action in enabled


MAX_SWITCH_CHANGES = 50


def _read_switch(folder: Path) -> dict[str, Any]:
    try:
        raw = json.loads((folder / SWITCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def enabled_actions() -> list[str]:
    """회사 연결에서 직접 쓰기를 켠 동작 (파일 값 그대로, 돈이 걸린 동작의 관문 조건은 빼고)."""
    enabled = _read_switch(adapter_dir()).get("enabled")
    return [str(a) for a in enabled] if isinstance(enabled, list) else []


def set_direct_write(action: WriteAction, on: bool, by: str) -> list[str]:
    """동작 하나의 직접 쓰기를 켜거나 끈다(W4 화면). 누가 언제 바꿨는지 `changes`에 남긴다.

    다른 동작의 켜짐은 그대로 둔다. 켠 동작 목록을 돌려준다.
    """
    folder = adapter_dir()
    with _locked(folder):
        raw = _read_switch(folder)
        enabled = [str(a) for a in raw.get("enabled", []) if isinstance(a, str)]
        if on and action not in enabled:
            enabled.append(action)
        if not on:
            enabled = [a for a in enabled if a != action]
        changes = raw.get("changes")
        changes = [c for c in changes if isinstance(c, dict)] if isinstance(changes, list) else []
        changes.append({"at": _now(), "by": by, "action": action, "on": on})
        out = {"enabled": enabled, "changes": changes[-MAX_SWITCH_CHANGES:]}
        tmp = folder / (SWITCH_FILE + ".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, folder / SWITCH_FILE)
    logger.info("사이트 직접 쓰기 %s: action=%s (by %s)", "켬" if on else "끔", action, by)
    return enabled


def clear_denied(action: WriteAction, by: str) -> dict[str, str]:
    """키에 쓰기 권한 없음 표시를 푼다(키를 바꾼 뒤, W4 화면). 남은 표시를 돌려준다.

    다음 쓰기가 다시 401·403을 받으면 다시 표시된다(`direct.mark_denied`).
    """
    folder = adapter_dir()
    with _locked(folder):  # mark_denied와 같은 잠금 안에서 바꾼다
        actions = direct.denied_actions()
        if action in actions:
            del actions[action]
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / (direct.DENIED_FILE + ".tmp")
            tmp.write_text(json.dumps({"actions": actions}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, folder / direct.DENIED_FILE)
    logger.info("키 쓰기 권한 없음 표시 풂: action=%s (by %s)", action, by)
    return actions


RECENT_LIMIT = 50
# 최근 사이트 쓰기에 내보내는 칸 (W4). 본문 원문·멱등 키(지문 포함)·고객사 키·context_id·
# safety 실행 id·응답 본문은 내보내지 않는다
RECENT_FIELDS = (
    "at",
    "finished_at",
    "service",
    "action",
    "status",
    "reason",
    "site_method",
    "site_path",
    "site_status",
    "site_code",
    "handoff_task_id",
)


def recent(limit: int = RECENT_LIMIT) -> list[dict[str, Any]]:
    """쓰기 장부의 최근 줄 (새것 먼저). 화면용 칸만, `applied`는 응답의 그 값만 옮긴다."""
    rows: list[dict[str, Any]] = []
    for entry in read_ledger().values():
        if not isinstance(entry, dict):
            continue
        row = {name: entry.get(name) for name in RECENT_FIELDS}
        response = entry.get("response")
        applied = response.get("applied") if isinstance(response, dict) else None
        row["applied"] = applied if applied in ("site", "pending_human") else None
        reason = entry.get("reason")
        row["reason_text"] = REASONS.get(reason) if isinstance(reason, str) else None
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def route(action: WriteAction, execution_id: str | None = None) -> str:
    """판정 3·4·5: 할 일함으로 가는 까닭, 모두 지나면 `DIRECT_READY`."""
    try:
        mapping = load_mapping()
    except MappingError:
        return "mapping_broken"
    if mapping is None or action not in mapping.writes:
        return "no_write_map"
    if not direct_write_on(action, execution_id):
        return "direct_off"
    if action in direct.denied_actions():
        return "no_write_permission"
    return DIRECT_READY


def _writes_per_hour() -> int:
    try:
        return max(0, int(os.getenv(WRITES_PER_HOUR_ENV, "") or DEFAULT_WRITES_PER_HOUR))
    except ValueError:
        return DEFAULT_WRITES_PER_HOUR


def _sent_last_hour(ledger: dict[str, dict[str, Any]], now: datetime) -> int:
    count = 0
    for entry in ledger.values():
        try:
            sent = datetime.fromisoformat(str(entry.get("sent_at") or ""))
        except ValueError:
            continue
        if (now - sent).total_seconds() < 3600:
            count += 1
    return count


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


def _handoff(req: WriteRequest, reason: str, extra: dict[str, Any] | None = None) -> str:
    """할 일함에 넣고 할 일 id. 같은 멱등 키면 할 일함이 처음 일을 돌려준다."""
    skill = f"site_apply_{req.action}"
    assignment = routing.assignment_for(req.service, skill)
    task = handoffs.get_store().add(
        HandoffCreate(
            tenant_id=gate_check.request_tenant(),  # 그 회사 할 일함 (캣북스는 예전 값)
            context_id=req.context_id or DEFAULT_CONTEXT,
            from_service=FROM_SERVICE,
            to_service=req.service,
            skill=skill,
            input={
                "title": TITLES[req.action],
                "shop_path": req.shop_path,
                **req.summary,
                **(extra or {}),
            },
            reason=REASONS[reason],
            assignee_unit=assignment.unit if assignment else UNASSIGNED_UNIT,
            assignee=assignment.assignee if assignment else None,
            # 서비스·동작을 붙여 다른 동작의 같은 키와 겹치지 않게 한다
            idempotency_key=f"site_adapter:{req.service}:{req.action}:{req.idempotency_key}",
        )
    )
    return task.task_id


async def apply(req: WriteRequest) -> tuple[dict[str, Any], bool]:
    """판정해 (응답 `data`, 앞 결과를 그대로 돌려줬는지)."""
    allowed_service(req.action, req.service)
    req.execution_id = None  # 부르는 쪽 값은 믿지 않는다: 판정 2에서만 채운다
    if req.action in MONEY_ACTIONS:
        # 판정 2: 장부 잠금 밖에서 safety를 부른다. 실패면 여기서 끝(장부·할 일함 안 남김)
        req.execution_id = await gate_check.confirm(
            req.action, req.service, req.context_id, req.idempotency_key, req.gate_claim
        )
    folder = adapter_dir()
    key_digest = digest(req.idempotency_key)
    entry_key = f"{req.service}|{req.action}|{key_digest}"
    plan: direct.Plan | None = None
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
            replayed = _replay(prior)
            if replayed is not None:
                return replayed, True
            # 앞 기록이 sending으로 남았으면(보내는 도중 죽음) 다시 보내지 않고 사람에게 맡긴다
            reason = "left_unfinished"
        else:
            reason = route(req.action, req.execution_id)
            if reason == DIRECT_READY:
                reason, plan = _prepare(req, ledger)
        if plan is None:
            data = _to_inbox(req, reason, ledger, entry_key, key_digest)
        else:
            # 판정 7: 보내기 전에 sending을 먼저 남긴다(도중에 죽어도 두 번 보내지 않게)
            ledger[entry_key] = {
                **_entry(req, key_digest),
                "status": "sending",
                "reason": None,
                "sent_at": _now(),
                "site_method": plan.method,
                "site_host": plan.host,
                "site_path": plan.path_template,
            }
        _save_ledger(folder, ledger)
    if plan is None:
        logger.info(
            "사이트 쓰기를 할 일함으로: service=%s action=%s reason=%s",
            req.service,
            req.action,
            reason,
        )
        return data, False
    # 장부 잠금 밖에서 보낸다(같은 키가 그 사이 오면 sending을 보고 할 일함으로 간다)
    reply = await direct.send(plan)
    return _finish(req, entry_key, key_digest, plan, reply), False


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _entry(req: WriteRequest, key_digest: str) -> dict[str, Any]:
    return {
        "at": _now(),
        "service": req.service,
        "action": req.action,
        "shop_path": req.shop_path,
        "context_id": req.context_id,
        "execution_id": req.execution_id,
        "key_digest": key_digest,
        "fingerprint": req.fingerprint,
    }


def _pending(req: WriteRequest, task_id: str) -> dict[str, Any]:
    data = {**req.response, "applied": "pending_human", "handoff_task_id": task_id}
    data.update(dict.fromkeys(req.task_id_fields, task_id))
    return data


def _site_rejected(error: dict[str, Any], message: str | None = None) -> ApiError:
    return ApiError(
        409,
        "SITE_REJECTED",
        "고객사 사이트가 이 쓰기를 받지 않았어요 (입력·상태 거절)",
        {**error, "site_message": message},
    )


def _replay(prior: dict[str, Any]) -> dict[str, Any] | None:
    """같은 쓰기가 다시 왔을 때 앞 결과. 거절이면 같은 409, sending이면 None(할 일함으로)."""
    status = prior.get("status")
    error = prior.get("error")
    if status == "rejected" and isinstance(error, dict):
        raise _site_rejected(error)
    response = prior.get("response")
    if status != "sending" and isinstance(response, dict):
        return dict(response)
    return None


def _to_inbox(
    req: WriteRequest,
    reason: str,
    ledger: dict[str, dict[str, Any]],
    entry_key: str,
    key_digest: str,
) -> dict[str, Any]:
    task_id = _handoff(req, reason)
    data = _pending(req, task_id)
    ledger[entry_key] = {
        **_entry(req, key_digest),
        "status": "handoff",
        "reason": reason,
        "handoff_task_id": task_id,
        "response": data,
    }
    return data


def _prepare(
    req: WriteRequest, ledger: dict[str, dict[str, Any]]
) -> tuple[str, direct.Plan | None]:
    """판정 7 직전: 본문을 계산하고 호출 한도를 본다. 못 보내면 (할 일함 까닭, None)."""
    try:
        found = load_mapping()
    except MappingError:
        return "mapping_broken", None
    if found is None or req.action not in found.writes:
        return "no_write_map", None
    if _sent_last_hour(ledger, datetime.now(UTC)) >= _writes_per_hour():
        logger.warning("사이트 쓰기 시간당 한도에 닿음: action=%s", req.action)
        return "rate_limited", None
    ctx = direct.Context(
        source=req.source,
        path_values=req.path_values,
        key_material=f"{req.service}|{req.action}|{req.idempotency_key}",
    )
    try:
        return DIRECT_READY, direct.plan(found, found.writes[req.action], ctx)
    except direct.CalcError as exc:
        logger.warning("쓰기 본문을 계산하지 못함: action=%s why=%s", req.action, exc.why)
        return ("link_missing" if exc.why == "link" else "calc_failed"), None
    except upstream.UpstreamError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc


def _finish(
    req: WriteRequest, entry_key: str, key_digest: str, plan: direct.Plan, reply: direct.Reply
) -> dict[str, Any]:
    """고객사 응답을 장부에 적고 AI가 받을 `data`를 낸다 (직접 쓰기 결과 표)."""
    outcome = direct.classify(reply, plan.ok, plan.partial_codes)
    site_code, short = (
        direct.site_error(reply.body) if outcome in ("rejected", "partial") else (None, None)
    )
    rejected: ApiError | None = None
    data: dict[str, Any] = {}
    folder = adapter_dir()
    with _locked(folder):
        ledger = read_ledger(folder)
        entry = ledger.get(entry_key) or {**_entry(req, key_digest), "status": "sending"}
        # 그 사이 같은 키가 와서 할 일함으로 갔으면 그 결과(응답)는 그대로 두고 사이트 결과만 적는다
        mine = entry.get("status") == "sending"
        entry.update(
            finished_at=_now(),
            site_status=reply.status,
            site_request_id=reply.request_id,
            site_code=site_code,
            site_error=reply.error_kind,
        )
        update: dict[str, Any]
        if outcome == "done":
            data = {
                **req.response,
                "applied": "site",
                **direct.result_fields(plan.result, reply.body),
            }
            update = {"status": "done", "response": data}
        elif outcome == "rejected":
            error = {"site_code": site_code, "site_status": reply.status}
            rejected = _site_rejected(error, short)
            update = {"status": "rejected", "reason": "site_rejected", "error": error}
        elif outcome == "denied":
            direct.mark_denied(req.action)
            task_id = _handoff(req, "no_write_permission")
            data = _pending(req, task_id)
            update = {
                "status": "rejected",
                "reason": "no_write_permission",
                "handoff_task_id": task_id,
                "response": data,
            }
        elif outcome == "partial":
            # 사이트에 반영은 됐다(다시 보내지 않는다). 뒤따르는 일(결제사 환불)만 사람이 본다
            reason = "site_refund_failed" if req.action == "refund" else "site_partial"
            task_id = _handoff(
                req,
                reason,
                {
                    "site_method": plan.method,
                    "site_path": plan.path_template,
                    "site_status": reply.status,
                    "site_code": site_code,
                },
            )
            data = _pending(req, task_id)
            update = {
                "status": "partial",
                "reason": reason,
                "handoff_task_id": task_id,
                "response": data,
            }
        else:
            task_id = _handoff(
                req,
                "site_unknown",
                {
                    "idempotency_key": req.idempotency_key,
                    "site_method": plan.method,
                    "site_path": plan.path_template,
                    "site_status": reply.status,
                },
            )
            data = _pending(req, task_id)
            update = {
                "status": "unknown",
                "reason": "site_unknown",
                "handoff_task_id": task_id,
                "response": data,
            }
        if mine:
            entry.update(update)
        ledger[entry_key] = entry
        _save_ledger(folder, ledger)
    logger.info(
        "사이트 직접 쓰기: service=%s action=%s site_status=%s outcome=%s",
        req.service,
        req.action,
        reply.status,
        outcome,
    )
    if rejected is not None:
        raise rejected
    return data
