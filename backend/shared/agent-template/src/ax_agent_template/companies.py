"""회사 목록과 회사 폴더 경로 (여러 회사 설계 1-2, 2026-11-20).

사이트 하나에 여러 회사가 산다. 이 모듈은 읽기만 한다(목록 파일은 dashboard-api가 쓴다).
도메인 서버도 dashboard-api를 import하지 않고 회사 목록을 읽을 수 있게 여기 둔다.

- 회사 ID: 계약 `TenantId` 규칙(영어 소문자·숫자·`-`, 3~40자). 옛 값 `shop-demo`는 캣북스의
  별칭이라 읽을 때 늘 `catbooks`로 바꾼다(`canonical_company_id`)
- 회사 목록: `AX_COMPANIES_FILE` → 회사 연결 파일 옆 `companies.json`.
  파일이 없거나 깨졌으면 캣북스 하나(지금 동작 그대로). 읽기만 하고 파일을 만들지 않는다
- 회사 폴더: `AX_COMPANIES_DIR` → 회사 연결 파일 옆 `companies/`. 회사 파일은
  `company_path(회사 ID, 이름)` = `<회사 폴더>/<회사 ID>/<이름>`. 폴더를 만들지 않는다
  (옮기기는 1-4에서)
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import Company
from ax_contracts.schemas.common import TENANT_ID_ALIASES, TENANT_ID_CATBOOKS, TENANT_ID_PATTERN

COMPANIES_FILE_ENV = "AX_COMPANIES_FILE"
COMPANIES_FILE = "companies.json"
COMPANIES_DIR_ENV = "AX_COMPANIES_DIR"
COMPANIES_DIR = "companies"
_CONNECTION_FILE_ENV = "AX_COMPANY_CONNECTION_FILE"  # company_connection.CONNECTION_FILE_ENV
DEFAULT_COMPANY_ENV = "AX_DEFAULT_COMPANY"
# 기본 회사를 아무 데서도 못 정했을 때의 마지막 값.
# 코드 안에 남은 유일한 기본값 리터럴(옛 호환).
# 다른 모듈은 이 값을 직접 쓰지 말고 `default_company_id()`를 부른다
LEGACY_DEFAULT_COMPANY = TENANT_ID_CATBOOKS
_ID_RE = re.compile(TENANT_ID_PATTERN)
# 회사 목록 파일이 없을 때의 한 줄: 지금 이 설치의 회사(캣북스).
# 만든 시각은 회사 목록이 생긴 날(설계 1-2)로 둔다.
# 옛 호환 이름이다(LEGACY_DEFAULT_COMPANY의 회사 한 줄). 새 코드는 `default_company_id()`를 쓴다
CATBOOKS = Company(
    company_id=LEGACY_DEFAULT_COMPANY,
    name="캣북스",
    industry="",
    site_url="",
    created_at=datetime(2026, 11, 20, tzinfo=UTC),
)

logger = logging.getLogger("ax.companies")
_legacy_warned = False


def canonical_company_id(value: Any) -> str | None:
    """회사 ID를 정식 id로. 별칭은 바꾸고, 규칙에 안 맞으면 None."""
    if not isinstance(value, str):
        return None
    value = TENANT_ID_ALIASES.get(value, value)
    return value if _ID_RE.fullmatch(value) else None


def _beside_connection(name: str) -> Path | None:
    conn = os.getenv(_CONNECTION_FILE_ENV, "").strip()
    return Path(conn).with_name(name) if conn else None


def companies_file() -> Path | None:
    """회사 목록 파일 경로. 정한 곳이 없으면 None."""
    raw = os.getenv(COMPANIES_FILE_ENV, "").strip()
    return Path(raw) if raw else _beside_connection(COMPANIES_FILE)


def companies_dir() -> Path | None:
    """회사 폴더들이 있는 곳. 정한 곳이 없으면 None."""
    raw = os.getenv(COMPANIES_DIR_ENV, "").strip()
    return Path(raw) if raw else _beside_connection(COMPANIES_DIR)


def _read_rows(path: Path) -> list[Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("회사 목록을 읽지 못했다: %s (%s)", path, type(exc).__name__)
        return None
    rows = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        logger.warning("회사 목록 모양이 다르다: %s", path)
        return None
    return rows


def _valid_companies(rows: list[Any], path: Path | None) -> list[Company]:
    out: list[Company] = []
    seen: set[str] = set()
    for row in rows:
        try:
            company = Company.model_validate(row)  # TenantId가 별칭을 정식 id로 바꾼다
        except ValidationError:
            logger.warning("회사 목록의 맞지 않는 줄을 건너뛴다: %s", path)
            continue
        if company.company_id in seen:
            continue
        seen.add(company.company_id)
        out.append(company)
    return out


def list_companies() -> list[Company]:
    """회사 목록. 파일이 없거나 깨졌거나 맞는 줄이 없으면 기본 회사(기본은 캣북스) 하나."""
    path = companies_file()
    rows = _read_rows(path) if path is not None else None
    out = _valid_companies(rows or [], path)
    if out:
        return out
    # 파일이 없을 때: 옛 동작 그대로 기본 회사 한 줄.
    # 기본 회사가 env로 캣북스 말고 정해졌으면 그 id로 한 줄
    cid = _env_default()
    if cid is None or cid == CATBOOKS.company_id:
        return [CATBOOKS]
    return [CATBOOKS.model_copy(update={"company_id": cid, "name": cid})]


def _env_default() -> str | None:
    raw = os.getenv(DEFAULT_COMPANY_ENV, "").strip()
    if not raw:
        return None
    cid = canonical_company_id(raw)
    if cid is None:
        logger.warning("%s 값이 회사 ID 규칙에 맞지 않아 무시한다: %r", DEFAULT_COMPANY_ENV, raw)
    return cid


def default_company_id() -> str:
    """이 설치의 기본 회사 ID(정식 id). 절대 raise하지 않는다.

    순서: env `AX_DEFAULT_COMPANY`(별칭은 정식 id로) → 회사 목록 파일에 회사가 정확히 하나면 그 id
    → 옛 호환 `LEGACY_DEFAULT_COMPANY`(프로세스당 한 번 경고).
    """
    global _legacy_warned
    cid = _env_default()
    if cid is not None:
        return cid
    path = companies_file()
    rows = _read_rows(path) if path is not None else None
    if rows is not None:
        found = _valid_companies(rows, path)
        if len(found) == 1:
            return found[0].company_id
    if not _legacy_warned:
        _legacy_warned = True
        logger.warning(
            "기본 회사를 정하지 않아 옛 기본값 %r을 쓴다. "
            "%s를 설정하거나 회사 목록을 두자(옛 호환, 곧 없앤다)",
            LEGACY_DEFAULT_COMPANY,
            DEFAULT_COMPANY_ENV,
        )
    return LEGACY_DEFAULT_COMPANY


def company_ids() -> list[str]:
    return [c.company_id for c in list_companies()]


def get_company(value: Any) -> Company | None:
    """회사 하나. 별칭도 받는다. 목록에 없으면 None."""
    cid = canonical_company_id(value)
    if cid is None:
        return None
    return next((c for c in list_companies() if c.company_id == cid), None)


def is_company(value: Any) -> bool:
    return get_company(value) is not None


def _safe_name(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError(f"회사 파일 이름이 맞지 않다: {name!r}")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"회사 파일 이름이 맞지 않다: {name!r}")
    return PurePosixPath(*parts)


def company_path(company_id: str, name: str) -> Path | None:
    """회사 파일 경로 `<회사 폴더>/<정식 회사 ID>/<이름>`. 회사 폴더를 정하지 않았으면 None.

    회사 ID가 규칙에 안 맞거나 이름이 회사 폴더 밖을 가리키면 ValueError.
    경로만 만들고 폴더·파일은 만들지 않는다.
    """
    cid = canonical_company_id(company_id)
    if cid is None:
        raise ValueError(f"회사 ID가 맞지 않다: {company_id!r}")
    rel = _safe_name(name)
    root = companies_dir()
    if root is None:
        return None
    return root / cid / Path(*rel.parts)
