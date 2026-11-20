"""기획 대화·노션 연결 파일의 자리 (여러 회사 설계 1-6, 2026-11-20).

`PLANNING_STATE_DIR`가 없으면 빈 문자열(파일 없음, 메모리만: 테스트·eval). 있으면:
- 회사 폴더를 켰으면(`AX_COMPANIES_DIR`) `<회사 폴더>/catbooks/<이름>`. 옛 자리
  `PLANNING_STATE_DIR/<이름>`에 있으면 처음 열 때 옮기고 옛 것은 `.moved`(`company_files`)
- 안 켰으면 옛 자리 그대로
- `AX_COMPANY_FOLDERS=off`면 옛 자리. 옛 자리가 비어 있으면 회사 폴더 값으로 되살린다

지금은 회사가 캣북스 하나라 늘 캣북스 폴더다(동작 그대로).
업무 흐름 상태 파일(`ax_workflow.store`)은 여기서 다루지 않는다(`PLANNING_STATE_DIR` 그대로).
"""

from __future__ import annotations

import os
from pathlib import Path

from ax_agent_template import company_files

STATE_DIR_ENV = "PLANNING_STATE_DIR"
# SQLite 딸린 파일. 꺼질 때 남은 되돌림 기록(-journal)도 같이 옮겨야 DB가 깨지지 않는다
SQLITE_EXTRAS = ("-journal", "-wal", "-shm")


def state_path(name: str, *, extras: tuple[str, ...] = ()) -> str:
    """파일 경로. 회사 폴더면 폴더가 있게 해 둔다(SQLite·임시 파일이 바로 만들 수 있게)."""
    base = os.getenv(STATE_DIR_ENV, "").strip()
    if not base:
        return ""
    old = Path(base) / name
    if company_files.folders_off():
        company_files.restore_old(name, target=old, extras=extras)
        return str(old)
    if not company_files.folders_on():
        return str(old)
    path = company_files.company_file(name, source=old, extras=extras)
    if path != old and not path.parent.exists():
        # 회사 폴더들은 0700 (company_files와 같다). 토큰이 들어가는 파일도 있다
        path.parent.parent.parent.mkdir(parents=True, exist_ok=True)
        path.parent.parent.mkdir(mode=company_files.DIR_MODE, exist_ok=True)
        path.parent.mkdir(mode=company_files.DIR_MODE, exist_ok=True)
    return str(path)
