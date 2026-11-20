"""마케팅 저장소의 회사 칸·회사 폴더 도우미 (여러 회사 설계 1-6, 2026-11-20).

- 회사 칸: 저장소 표마다 `tenant_id`(회사 ID) 칸을 둔다. 옛 DB는 켤 때 `ALTER TABLE ... ADD COLUMN
  tenant_id TEXT NOT NULL DEFAULT 'catbooks'`로 칸을 붙여 기존 줄이 캣북스가 된다(멱등: 칸이 이미
  있으면 건너뛴다). 저장소는 만들 때 회사 ID(`company_id`, 기본 캣북스)를 받아 쓰기에 그 값을 적고,
  읽기·고치기 쿼리에 `tenant_id = ?`로 거른다. 별칭 `shop-demo`는 `catbooks`로 바꾸고,
  맞지 않는 값은 캣북스로 둔다. 회사 ID 없는 요청 거절은 하지 않는다(4단계)
- 회사 폴더: 미디어 폴더·설정 파일은 `<회사 폴더>/<회사 ID>/` 아래
  (`ax_agent_template.company_files`, #1537). 이관·`.moved`·`AX_COMPANY_FOLDERS=off`
  되살리기는 그쪽 그대로다
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ax_agent_template import company_files
from ax_agent_template.companies import canonical_company_id
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

TENANT_COLUMN = "tenant_id"
# SQLite 딸린 파일. 꺼질 때 남은 되돌림 기록(-journal)도 같이 옮겨야 DB가 깨지지 않는다
SQLITE_EXTRAS = ("-journal", "-wal", "-shm")


def scope_id(company_id: str | None) -> str:
    """저장소가 쓸 정식 회사 ID. 없거나 맞지 않으면 캣북스."""
    return canonical_company_id(company_id) or TENANT_ID_CATBOOKS


def ensure_tenant_column(conn: sqlite3.Connection, table: str) -> None:
    """옛 표에 회사 칸을 붙인다(기존 줄은 캣북스). 이미 있으면 안 한다. 호출자가 트랜잭션을 연다."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - 상수 표 이름
    if TENANT_COLUMN not in have:
        conn.execute(
            f"ALTER TABLE {table}"  # noqa: S608 - 상수 표 이름
            f" ADD COLUMN {TENANT_COLUMN} TEXT NOT NULL DEFAULT '{TENANT_ID_CATBOOKS}'"
        )


def explicit(env: str, name: str, *, extras: Sequence[str] = ()) -> Path | None:
    """환경변수로 정한 경로. off면 비어 있을 때 회사 폴더 값으로 되살린다. 안 정했으면 None."""
    raw = os.getenv(env, "").strip()
    if not raw:
        return None
    path = Path(raw)
    company_files.restore_old(name, target=path, extras=extras)
    return path


def in_company_folder(
    name: str,
    *,
    source: Path | None = None,
    extras: Sequence[str] = (),
    company_id: str = TENANT_ID_CATBOOKS,
) -> Path | None:
    """회사 폴더의 경로(켰을 때만). 옛 자리에 있으면 옮긴다. 회사 폴더들은 0700으로 만든다."""
    if not company_files.folders_on():
        return None
    path = company_files.company_file(
        name, source=source, extras=extras, company_id=scope_id(company_id)
    )
    folder = path.parent
    if not folder.exists():
        folder.parent.parent.mkdir(parents=True, exist_ok=True)
        folder.parent.mkdir(mode=company_files.DIR_MODE, exist_ok=True)
        folder.mkdir(mode=company_files.DIR_MODE, exist_ok=True)
    return path


MEDIA_DIR_ENV = "MARKETING_MEDIA_DIR"
MEDIA_NAME = "marketing_media"
HISTORY_EXTRA = ".history.jsonl"


def _default_media_dir() -> Path:
    return Path(tempfile.gettempdir()) / MEDIA_NAME


def media_dir(company_id: str | None = None) -> Path:
    """미디어 저장 폴더.

    `MARKETING_MEDIA_DIR`을 정했으면 그 경로(off면 비어 있을 때 회사 폴더 값으로 되살린다).
    안 정했고 회사 폴더를 켰으면 `<회사 폴더>/catbooks/marketing_media`
    (옛 `<회사 폴더의 부모>/marketing_media`가 있으면 처음 읽을 때 옮긴다).
    둘 다 아니면 예전처럼 임시 폴더.
    """
    pinned = explicit(MEDIA_DIR_ENV, MEDIA_NAME)
    if pinned is not None:
        return pinned
    folder = in_company_folder(MEDIA_NAME, company_id=scope_id(company_id))
    return folder if folder is not None else _default_media_dir()


def config_file(env: str, name: str, company_id: str | None = None) -> Path:
    """설정 파일(`cost_limits.json`·`video_providers.json`)과 딸린 `.history.jsonl`의 자리.

    파일별 환경변수를 정했으면 그 경로(off면 비어 있을 때 회사 폴더 값으로 되살린다).
    안 정했으면 회사 폴더를 켰을 때 `<회사 폴더>/catbooks/<이름>`.
    옛 자리(`<MARKETING_MEDIA_DIR>/<이름>`, 없으면 임시 폴더)에 있으면 처음 읽을 때 옮기고
    옛 것은 `.moved`. 회사 폴더를 껐으면 옛 자리(비어 있으면 회사 폴더 값으로 되살린다).
    """
    raw = os.getenv(env, "").strip()
    if raw:
        return explicit(env, name, extras=(HISTORY_EXTRA,)) or Path(raw)
    base = os.getenv(MEDIA_DIR_ENV, "").strip()
    legacy = (Path(base) if base else _default_media_dir()) / name
    folder = in_company_folder(
        name, source=legacy, extras=(HISTORY_EXTRA,), company_id=scope_id(company_id)
    )
    if folder is not None:
        return folder
    company_files.restore_old(
        name, target=legacy, extras=(HISTORY_EXTRA,), company_id=scope_id(company_id)
    )
    return legacy
