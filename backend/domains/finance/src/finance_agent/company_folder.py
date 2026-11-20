"""재무 파일의 자리 (여러 회사 설계 1-6, 2026-11-20).

`finance_state.json`·`finance_exports/`·`finance_chat.db`를 `<회사 폴더>/catbooks/` 아래에 둔다.
지금은 회사가 캣북스 하나라 늘 캣북스 폴더다(동작 그대로).

- 파일별 환경변수(`FINANCE_STATE_FILE`·`FINANCE_EXPORT_DIR`·`FINANCE_CHAT_DB`)를 정했으면 그 경로.
  `AX_COMPANY_FOLDERS=off`(run_all.sh가 옛 경로를 정한다)면 그 경로가 비어 있을 때 회사 폴더
  값으로 되살린다(`company_files.restore_old`)
- 안 정했고 회사 폴더를 켰으면(`AX_COMPANIES_DIR`) 회사 폴더. 옛 자리(`<회사 폴더의 부모>/<이름>`)에
  있으면 처음 읽을 때 옮기고 옛 것은 `.moved`(`company_files.company_file`)
- 둘 다 없으면 None(저장하지 않음, 테스트·단독 실행)
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from ax_agent_template import company_files

# SQLite 딸린 파일. 꺼질 때 남은 되돌림 기록(-journal)도 같이 옮겨야 DB가 깨지지 않는다
SQLITE_EXTRAS = ("-journal", "-wal", "-shm")


def explicit(env: str, name: str, *, extras: Sequence[str] = ()) -> Path | None:
    """환경변수로 정한 경로. off면 비어 있을 때 회사 폴더 값으로 되살린다. 안 정했으면 None."""
    raw = os.getenv(env, "").strip()
    if not raw:
        return None
    path = Path(raw)
    company_files.restore_old(name, target=path, extras=extras)
    return path


def in_company_folder(name: str, *, extras: Sequence[str] = ()) -> Path | None:
    """회사 폴더의 경로(켰을 때만). 옛 자리에 있으면 옮긴다. 회사 폴더들은 0700으로 만든다."""
    if not company_files.folders_on():
        return None
    path = company_files.company_file(name, extras=extras)
    folder = path.parent
    if not folder.exists():
        folder.parent.parent.mkdir(parents=True, exist_ok=True)
        folder.parent.mkdir(mode=company_files.DIR_MODE, exist_ok=True)
        folder.mkdir(mode=company_files.DIR_MODE, exist_ok=True)
    return path


def path_for(env: str, name: str, *, extras: Sequence[str] = ()) -> Path | None:
    """환경변수 → 회사 폴더 → None."""
    pinned = explicit(env, name, extras=extras)
    return pinned if pinned is not None else in_company_folder(name, extras=extras)
