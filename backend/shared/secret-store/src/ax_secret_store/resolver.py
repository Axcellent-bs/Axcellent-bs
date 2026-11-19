"""비밀값 해석: `get_secret()` 한 곳. 스위치 `AX_SECRET_STORE=env|db`(기본 env).

- env 모드: `os.getenv(env_fallback_name or name, default)`와 완전히 같다.
  DB·마스터 키를 보지 않는다.
- db 모드: DB의 암호화 값을 복호화해 쓴다. 이 프로세스 메모리에 스냅샷(전체)을 두고
  `AX_SECRET_CACHE_TTL` 초(기본 15)마다 다시 읽는다.
  읽기 실패(DB 장애 등)면 마지막으로 읽은 값으로 계속 동작하고 사유 코드만 로그에 남긴다.
  db 모드에서 DB에 없는 이름은 비어 있는 것으로 본다(env로 되돌아가지 않는다).
- `startup_check()`: db 모드에서 서비스를 켤 때 부른다. 마스터 키가 없거나 틀리거나 DB가 안 열리면
  `SecretStoreError`(사유 코드)로 시작을 거부한다.
- 기본 tenant: `AX_SECRET_DEFAULT_TENANT`(기본 `default`).
"""

from __future__ import annotations

import logging
import os
import threading
import time

from ax_secret_store import errors
from ax_secret_store.errors import SecretStoreError
from ax_secret_store.service import SecretService, check_tenant

MODE_ENV = "AX_SECRET_STORE"
TTL_ENV = "AX_SECRET_CACHE_TTL"
DEFAULT_TENANT_ENV = "AX_SECRET_DEFAULT_TENANT"
DEFAULT_TTL = 15.0

logger = logging.getLogger("ax.secret_store")
_lock = threading.Lock()
_service: SecretService | None = None
_snapshot: dict[tuple[str, str], str] | None = None
_loaded_at = 0.0
_last_logged_code: str | None = None


def mode() -> str:
    raw = os.getenv(MODE_ENV, "").strip().lower()
    if raw in ("", "env"):
        return "env"
    if raw == "db":
        return "db"
    raise SecretStoreError(errors.MODE_INVALID)


def is_db_mode() -> bool:
    try:
        return mode() == "db"
    except SecretStoreError:
        return False  # 시작 때 startup_check가 거부한다


def default_tenant() -> str:
    return os.getenv(DEFAULT_TENANT_ENV, "").strip() or "default"


def _ttl() -> float:
    try:
        return max(0.0, float(os.getenv(TTL_ENV, "")))
    except ValueError:
        return DEFAULT_TTL


def reset_for_tests() -> None:
    global _service, _snapshot, _loaded_at, _last_logged_code
    with _lock:
        _service, _snapshot, _loaded_at, _last_logged_code = None, None, 0.0, None


def invalidate() -> None:
    """이 프로세스의 캐시를 버린다(교체·삭제 직후). 다른 프로세스는 TTL 안에 따라온다."""
    global _snapshot, _loaded_at
    with _lock:
        _snapshot, _loaded_at = None, 0.0


def service() -> SecretService:
    """db 모드의 관리 서비스. env 모드에서는 쓰지 않는다."""
    global _service
    with _lock:
        if _service is None:
            _service = SecretService.from_env()
        return _service


def _load(svc: SecretService, previous: dict[tuple[str, str], str] | None) -> dict:
    """전체를 읽어 복호화한다. 한 행만 못 풀면 그 행은 이전 값을 유지하고 코드를 로그에 남긴다."""
    snap: dict[tuple[str, str], str] = {}
    for row in svc.repo.list(None):
        try:
            snap[(row.tenant, row.name)] = svc.decrypt_row(row)
        except SecretStoreError as error:
            _log_code(error.code, f"{row.tenant}/{row.name}")
            if previous and (row.tenant, row.name) in previous:
                snap[(row.tenant, row.name)] = previous[(row.tenant, row.name)]
    return snap


def _log_code(code: str, target: str = "") -> None:
    global _last_logged_code
    if code != _last_logged_code:  # 같은 사유는 반복해서 쓰지 않는다
        _last_logged_code = code
        logger.warning("비밀 저장소 읽기 실패: %s %s", code, target)


def startup_check() -> str:
    """서비스를 켤 때 한 번. env 모드는 아무것도 하지 않는다. 실패하면 SecretStoreError."""
    global _snapshot, _loaded_at, _service
    if mode() == "env":
        return "env"
    svc = SecretService.from_env()  # master_key_missing·invalid, db_path_missing, db_unavailable
    snap: dict[tuple[str, str], str] = {}
    for row in svc.repo.list(None):
        try:
            snap[(row.tenant, row.name)] = svc.decrypt_row(row)
        except SecretStoreError as error:
            if error.code == errors.DECRYPT_FAILED:
                raise SecretStoreError(errors.MASTER_KEY_MISMATCH) from None
            raise
    with _lock:
        _service, _snapshot, _loaded_at = svc, snap, time.monotonic()
    return "db"


def _current_snapshot() -> dict[tuple[str, str], str]:
    global _snapshot, _loaded_at
    with _lock:
        snap, loaded = _snapshot, _loaded_at
    if snap is not None and time.monotonic() - loaded < _ttl():
        return snap
    try:
        svc = service()
        fresh = _load(svc, snap)
    except SecretStoreError as error:
        _log_code(error.code)
        return snap if snap is not None else {}
    with _lock:
        _snapshot, _loaded_at = fresh, time.monotonic()
    return fresh


def get_secret(
    name: str,
    *,
    tenant: str | None = None,
    env_fallback_name: str | None = None,
    default: str = "",
) -> str:
    """비밀값 하나. env 모드는 `os.getenv(env_fallback_name or name, default)`와 같다."""
    if not is_db_mode():
        return os.getenv(env_fallback_name or name, default)
    use = tenant or default_tenant()
    try:
        check_tenant(use)
    except SecretStoreError as error:
        _log_code(error.code)
        return default
    return _current_snapshot().get((use, name), default)


def has_secret(name: str, *, tenant: str | None = None) -> bool:
    return bool(get_secret(name, tenant=tenant).strip())
