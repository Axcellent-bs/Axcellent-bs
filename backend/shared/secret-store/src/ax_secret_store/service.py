"""비밀값 관리 서비스: 검증 → 암호화 → 저장소. 값을 돌려주는 화면용 함수는 없다.

- 추가(add)·교체(replace)·삭제(delete)는 모두 대기 요청(`stage`)을 먼저 만든다. 새 값은 요청 때
  바로 암호화해 대기 행에 넣고, 승인·적용(`apply`)에서 그 암호문을 옮긴다. 그래서 평문은 요청한
  순간 화면 → 이 함수까지만 오고, 안전 서버·A2A·이력에는 지문(fingerprint)과 끝 글자만 간다.
- 이력에는 누가·언제·무슨 동작·tenant·이름만 남긴다(값 없음).
- 값을 읽는 함수는 `reveal_for_runtime`(서비스 내부 소비자 전용, HTTP로 내보내지 않는다) 하나뿐이다.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_secret_store import errors
from ax_secret_store.crypto import Keyring
from ax_secret_store.errors import SecretStoreError
from ax_secret_store.repository import (
    PendingRow,
    SecretRepository,
    SecretRow,
    SqliteRepository,
    db_path,
)

TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
MIN_VALUE_LENGTH = 8
MAX_VALUE_LENGTH = 4096
REQUEST_TTL = timedelta(days=7)
OPS = ("add", "replace", "delete")


def check_tenant(tenant: str) -> str:
    if not isinstance(tenant, str) or not TENANT_RE.match(tenant):
        raise SecretStoreError(errors.INVALID_TENANT)
    return tenant


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SecretStoreError(errors.INVALID_NAME)
    return name


def check_value(value: str) -> str:
    if (
        not isinstance(value, str)
        or not MIN_VALUE_LENGTH <= len(value) <= MAX_VALUE_LENGTH
        or value != value.strip()
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    ):
        raise SecretStoreError(errors.INVALID_VALUE)
    return value


def hint_of(value: str) -> str:
    """화면에 보이는 끝 글자: 최대 4자, 짧은 값은 길이의 1/4(8자면 2자)."""
    return value[-min(4, len(value) // 4) :]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SecretService:
    def __init__(self, repo: SecretRepository, keyring: Keyring) -> None:
        self.repo = repo
        self.keyring = keyring

    @classmethod
    def from_env(cls) -> SecretService:
        keyring = Keyring.from_env()
        return cls(SqliteRepository(db_path()), keyring)

    # ---- 읽기(값 없음) ----

    def list_status(self, tenants: list[str] | None) -> list[dict[str, Any]]:
        return [
            {
                "tenant": r.tenant,
                "name": r.name,
                "configured": True,
                "hint": r.hint,
                "version": r.version,
                "updated_by": r.updated_by,
                "updated_at": r.updated_at,
            }
            for r in self.repo.list(tenants)
        ]

    def list_requests(self, tenants: list[str] | None) -> list[dict[str, Any]]:
        return [
            {
                "id": p.id,
                "op": p.op,
                "tenant": p.tenant,
                "name": p.name,
                "hint": p.hint,
                "fingerprint": p.fingerprint,
                "reason": p.reason,
                "requested_by": p.requested_by,
                "requested_at": p.requested_at,
                "review_id": p.review_id,
            }
            for p in self.repo.list_pending(tenants)
        ]

    def history(self, tenants: list[str] | None, limit: int = 30) -> list[dict[str, Any]]:
        return self.repo.history(tenants, limit)

    # ---- 요청 ----

    def stage(
        self,
        op: str,
        tenant: str,
        name: str,
        value: str | None,
        requested_by: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """대기 요청을 만든다. 같은 키의 이전 대기 요청은 이것으로 대체된다."""
        if op not in OPS:
            raise SecretStoreError(errors.INVALID_VALUE)
        check_tenant(tenant)
        check_name(name)
        exists = self.repo.get(tenant, name) is not None
        if op == "add" and exists:
            raise SecretStoreError(errors.ALREADY_EXISTS)
        if op in ("replace", "delete") and not exists:
            raise SecretStoreError(errors.NOT_FOUND)
        kid = nonce = blob = None
        hint = fingerprint = ""
        if op != "delete":
            checked = check_value(value or "")
            kid, nonce, blob = self.keyring.encrypt(tenant, name, checked)
            hint = hint_of(checked)
            fingerprint = self.keyring.fingerprint(tenant, name, checked)
        row = PendingRow(
            id="sr-" + secrets.token_hex(8),
            op=op,
            tenant=tenant,
            name=name,
            kid=kid,
            nonce=nonce,
            ciphertext=blob,
            hint=hint,
            fingerprint=fingerprint,
            reason=reason.strip()[:200],
            requested_by=requested_by[:64],
            requested_at=_now(),
            review_id=None,
        )
        self.repo.put_pending(
            row,
            {
                "at": row.requested_at,
                "by": row.requested_by,
                "action": f"request_{op}",
                "tenant": tenant,
                "name": name,
                "request_id": row.id,
            },
        )
        return self.pending_view(row)

    @staticmethod
    def pending_view(p: PendingRow) -> dict[str, Any]:
        return {
            "id": p.id,
            "op": p.op,
            "tenant": p.tenant,
            "name": p.name,
            "hint": p.hint,
            "fingerprint": p.fingerprint,
            "requested_by": p.requested_by,
            "requested_at": p.requested_at,
            "review_id": p.review_id,
        }

    def pending(self, request_id: str) -> PendingRow:
        row = self.repo.get_pending(request_id)
        if row is None:
            raise SecretStoreError(errors.REQUEST_NOT_FOUND)
        if datetime.fromisoformat(row.requested_at) + REQUEST_TTL < datetime.now(UTC):
            raise SecretStoreError(errors.REQUEST_EXPIRED)
        return row

    def gate_params(self, p: PendingRow) -> dict[str, Any]:
        """안전 관문에 보내는 값. 평문·암호문 없이 지문과 끝 글자만 담는다."""
        return {
            "op": p.op,
            "tenant": p.tenant,
            "name": p.name,
            "fingerprint": p.fingerprint,
            "hint": p.hint,
            "request_id": p.id,
            "requested_by": p.requested_by,
        }

    def record_review(self, request_id: str, review_id: str) -> None:
        self.repo.set_review(request_id, review_id)

    # ---- 적용 ----

    def apply(self, request_id: str, actor: str) -> dict[str, Any]:
        """승인(또는 A 등급 즉시 실행)이 끝난 요청을 적용한다."""
        p = self.pending(request_id)
        stamp = _now()
        history = {
            "at": stamp,
            "by": actor[:64],
            "action": p.op,
            "tenant": p.tenant,
            "name": p.name,
            "request_id": p.id,
            "review_id": p.review_id,
        }
        current = self.repo.get(p.tenant, p.name)
        if p.op == "delete":
            if current is None:
                raise SecretStoreError(errors.NOT_FOUND)
            self.repo.commit(delete=(p.tenant, p.name), drop_pending=p.id, history=history)
            return {"op": "delete", "tenant": p.tenant, "name": p.name}
        if p.kid is None or p.nonce is None or p.ciphertext is None:
            raise SecretStoreError(errors.REQUEST_MISMATCH)
        if p.op == "add" and current is not None:
            raise SecretStoreError(errors.ALREADY_EXISTS)
        if p.op == "replace" and current is None:
            raise SecretStoreError(errors.NOT_FOUND)
        row = SecretRow(
            tenant=p.tenant,
            name=p.name,
            kid=p.kid,
            nonce=p.nonce,
            ciphertext=p.ciphertext,
            hint=p.hint,
            version=(current.version + 1) if current else 1,
            created_at=current.created_at if current else stamp,
            updated_by=actor[:64],
            updated_at=stamp,
        )
        self.repo.commit(upsert=row, drop_pending=p.id, history=history)
        return {"op": p.op, "tenant": p.tenant, "name": p.name, "hint": p.hint}

    # ---- 가져오기·재암호화(CLI) ----

    def import_value(
        self, tenant: str, name: str, value: str, who: str, *, overwrite: bool = False
    ) -> str:
        """`.env` 값을 DB로 옮긴다. 반환: imported | skipped_exists."""
        check_tenant(tenant)
        check_name(name)
        check_value(value)
        current = self.repo.get(tenant, name)
        if current is not None and not overwrite:
            return "skipped_exists"
        stamp = _now()
        kid, nonce, blob = self.keyring.encrypt(tenant, name, value)
        row = SecretRow(
            tenant=tenant,
            name=name,
            kid=kid,
            nonce=nonce,
            ciphertext=blob,
            hint=hint_of(value),
            version=(current.version + 1) if current else 1,
            created_at=current.created_at if current else stamp,
            updated_by=who[:64],
            updated_at=stamp,
        )
        history = {"at": stamp, "by": who[:64], "action": "migrate", "tenant": tenant, "name": name}
        self.repo.commit(upsert=row, history=history)
        return "imported"

    def rewrap_all(self, who: str) -> int:
        """모든 값을 현재 키 버전으로 다시 암호화한다(마스터 키 교체 뒤). 바뀐 개수."""
        changed = 0
        for r in self.repo.list(None):
            if r.kid == self.keyring.current_kid:
                continue
            plain = self.reveal_for_runtime(r.tenant, r.name)
            if plain is None:
                continue
            stamp = _now()
            kid, nonce, blob = self.keyring.encrypt(r.tenant, r.name, plain)
            row = SecretRow(
                tenant=r.tenant, name=r.name, kid=kid, nonce=nonce, ciphertext=blob,
                hint=r.hint, version=r.version + 1, created_at=r.created_at,
                updated_by=who[:64], updated_at=stamp,
            )  # fmt: skip
            history = {
                "at": stamp, "by": who[:64], "action": "rewrap", "tenant": r.tenant, "name": r.name,
            }  # fmt: skip
            self.repo.commit(upsert=row, history=history)
            changed += 1
        return changed

    def retenant(
        self, source: str, target: str, who: str, *, dry_run: bool = False
    ) -> list[tuple[str, str]]:
        """`source` tenant의 값을 `target`으로 옮긴다(회사 공간 나누기 1-7: default → catbooks).

        암호문의 AAD·지문에 tenant가 들어 있어 행의 tenant 칸만 바꿀 수 없다. 값마다 옛 tenant로
        풀고 새 tenant로 다시 암호화한 뒤, 새 행 쓰기와 옛 행 지우기를 한 트랜잭션으로 한다.
        결과(이름별): `moved` · `would_move`(dry-run) · `same_exists`(대상에 같은 값이 있어 옛 행만
        지움) · `conflict`(대상에 다른 값이 있어 둘 다 그대로). 처리 안 된 요청이 `source`에 있으면
        `PENDING_EXISTS`로 아무것도 하지 않는다(대기 암호문도 tenant에 묶여 있어서다).
        """
        check_tenant(source)
        check_tenant(target)
        if source == target:
            raise SecretStoreError(errors.INVALID_TENANT)
        if self.repo.list_pending([source]):
            raise SecretStoreError(errors.PENDING_EXISTS)
        results: list[tuple[str, str]] = []
        for r in self.repo.list([source]):
            plain = self.decrypt_row(r)
            there = self.repo.get(target, r.name)
            if there is not None:
                same = self.decrypt_row(there) == plain
                if not same:
                    results.append((r.name, "conflict"))
                    continue
                if not dry_run:
                    stamp = _now()
                    history = {
                        "at": stamp, "by": who[:64], "action": "retenant_drop_duplicate",
                        "tenant": source, "name": r.name,
                    }  # fmt: skip
                    self.repo.commit(delete=(source, r.name), history=history)
                results.append((r.name, "same_exists"))
                continue
            if dry_run:
                results.append((r.name, "would_move"))
                continue
            stamp = _now()
            kid, nonce, blob = self.keyring.encrypt(target, r.name, plain)
            row = SecretRow(
                tenant=target, name=r.name, kid=kid, nonce=nonce, ciphertext=blob,
                hint=r.hint, version=r.version + 1, created_at=r.created_at,
                updated_by=who[:64], updated_at=stamp,
            )  # fmt: skip
            history = {
                "at": stamp, "by": who[:64], "action": f"retenant_from_{source}"[:64],
                "tenant": target, "name": r.name,
            }  # fmt: skip
            self.repo.commit(upsert=row, delete=(source, r.name), history=history)
            results.append((r.name, "moved"))
        return results

    def fingerprint_of_stored(self, tenant: str, name: str) -> str | None:
        plain = self.reveal_for_runtime(tenant, name)
        return None if plain is None else self.keyring.fingerprint(tenant, name, plain)

    # ---- 서비스 내부 소비자 전용 ----

    def reveal_for_runtime(self, tenant: str, name: str) -> str | None:
        row = self.repo.get(tenant, name)
        if row is None:
            return None
        return self.keyring.decrypt(row.tenant, row.name, row.kid, row.nonce, row.ciphertext)

    def decrypt_row(self, row: SecretRow) -> str:
        return self.keyring.decrypt(row.tenant, row.name, row.kid, row.nonce, row.ciphertext)
