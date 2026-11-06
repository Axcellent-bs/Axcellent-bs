"""회사 연결: 고객사 판매 서버(shop-api 규격) 주소를 화면에서 정한다.

- 저장: `AX_COMPANY_CONNECTION_FILE`이 가리키는 JSON 파일
  (`{"shop_api_url", "enabled", "verified_at", "updated_at", "updated_by"}`).
  쓰는 곳은 dashboard-api 한 곳이다 (시스템 관리 > 회사 연결, 사람 토큰).
- 읽기: `config.agent_url("shop-api")`가 부를 때마다 바뀐 시각·크기로 확인해 읽는다
  (재시작 없이 반영).
  순서는 이 파일 → `AX_SHOP_API_URL` → 기본 포트 표다.
- `enabled`가 false면 주소는 남겨 두고 쓰지 않는다(내부 데이터 모드). 없으면 true(예전 파일).
- 파일이 없으면 연결이 없는 것이다(로컬 개발 기본, 경고 없음).
  깨졌으면 경고를 한 번 남기고 없는 것으로 본다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

CONNECTION_FILE_ENV = "AX_COMPANY_CONNECTION_FILE"
MAX_URL_LENGTH = 300

logger = logging.getLogger("ax.company_connection")


@dataclass(frozen=True)
class Connection:
    """저장된 회사 연결 한 건."""

    shop_api_url: str
    verified_at: str | None
    updated_at: str | None
    updated_by: str | None
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "shop_api_url": self.shop_api_url,
            "enabled": self.enabled,
            "verified_at": self.verified_at,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def connection_file() -> str:
    """연결 파일 경로. 설정하지 않았으면 빈 문자열(연결 기능 꺼짐)."""
    return os.getenv(CONNECTION_FILE_ENV, "").strip()


def validate_shop_url(raw: str) -> str:
    """판매 서버 주소를 검사해 끝의 `/`를 뗀 값으로 돌려준다. 어긋나면 ValueError.

    http·https만, 호스트 필수, 계정(`user:pw@`)·쿼리·프래그먼트는 받지 않는다.
    """
    url = (raw or "").strip()
    if not url:
        raise ValueError("주소를 입력하세요")
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"주소가 너무 깁니다 ({MAX_URL_LENGTH}자 이하)")
    if any(c.isspace() for c in url):
        raise ValueError("주소에 공백이 있습니다")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("주소는 http:// 또는 https://로 시작해야 합니다")
    if not parts.hostname:
        raise ValueError("주소에 호스트가 없습니다")
    if parts.username is not None or parts.password is not None:
        raise ValueError("주소에 계정(user:pw@)을 넣지 마세요")
    if parts.query or parts.fragment or url.endswith(("?", "#")):
        raise ValueError("주소에 ?나 #을 넣지 마세요")
    try:
        _ = parts.port  # 숫자가 아니거나 범위를 벗어난 포트는 여기서 ValueError
    except ValueError as exc:
        raise ValueError("포트가 올바르지 않습니다") from exc
    return url.rstrip("/")


def _parse(raw: Any, path: str) -> Connection:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 맨 위가 객체여야 한다")
    url = raw.get("shop_api_url")
    if not isinstance(url, str):
        raise ValueError(f"{path}: shop_api_url(문자열)이 필요하다")

    def text(key: str) -> str | None:
        value = raw.get(key)
        return value if isinstance(value, str) else None

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"{path}: enabled는 true·false여야 한다")
    return Connection(
        shop_api_url=validate_shop_url(url),
        verified_at=text("verified_at"),
        updated_at=text("updated_at"),
        updated_by=text("updated_by"),
        enabled=enabled,
    )


# 바뀐 시각·크기가 같으면 다시 읽지 않는다. 깨진 파일 경고도 같은 상태면 한 번만 남긴다
_cache: tuple[str, int, int, Connection | None] | None = None


def read_connection() -> Connection | None:
    """저장된 회사 연결. 파일 경로가 없거나 파일이 없거나 깨졌으면 None."""
    global _cache
    path = connection_file()
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None  # 연결을 저장한 적 없음: 정상
    key = (path, stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[:3] == key:
        return _cache[3]
    try:
        found: Connection | None = _parse(json.loads(Path(path).read_text(encoding="utf-8")), path)
    except (OSError, ValueError) as exc:  # json.JSONDecodeError는 ValueError다
        logger.warning("회사 연결 파일이 깨져 쓰지 않는다: %s (%s)", path, exc)
        found = None
    _cache = (*key, found)
    return found


def shop_api_override() -> str | None:
    """화면에서 정한 판매 서버 주소. 없거나 꺼져 있으면(내부 데이터 모드) None."""
    found = read_connection()
    return found.shop_api_url if found and found.enabled else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_connection(
    url: str, *, verified_at: str | None, updated_by: str, enabled: bool = True
) -> Connection:
    """연결을 저장한다(임시 파일에 쓰고 바꿔치기). 파일 경로가 없으면 RuntimeError."""
    path = connection_file()
    if not path:
        raise RuntimeError(f"{CONNECTION_FILE_ENV}가 설정되지 않아 저장할 수 없다")
    saved = Connection(
        shop_api_url=validate_shop_url(url),
        verified_at=verified_at,
        updated_at=_now(),
        updated_by=updated_by,
        enabled=enabled,
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(saved.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return saved


def clear_connection() -> bool:
    """연결 파일을 지운다. 지웠으면 True, 원래 없었으면 False."""
    path = connection_file()
    if not path:
        return False
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True
