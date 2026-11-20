"""회사 목록 파일 쓰기 (여러 회사 설계 1-2, 2026-11-20).

읽기는 `ax_agent_template.companies`(도메인 서버도 쓴다). 여기는 쓰기만 한다.
- `save(목록)`: 정식 회사 ID로만, 같은 회사는 한 번만 쓴다(별칭 `shop-demo`는 남기지 않는다)
- `init_file()`: 파일이 없을 때만 지금 목록(캣북스 하나)으로 만든다. 서버 시작 때 부르지 않는다

명령: `uv run python -m ax_dashboard_api.companies init` / `list`
(`AX_COMPANIES_FILE` 또는 `AX_COMPANY_CONNECTION_FILE`이 정해진 환경에서)
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

from ax_agent_template import companies as read
from ax_contracts.schemas import Company

_lock = threading.Lock()


def _place() -> Path:
    path = read.companies_file()
    if path is None:
        raise RuntimeError(
            f"{read.COMPANIES_FILE_ENV}·AX_COMPANY_CONNECTION_FILE가 없어 회사 목록을 쓸 수 없다"
        )
    return path


def save(rows: Iterable[Company]) -> None:
    """회사 목록을 통째로 쓴다(임시 파일 → 바꿔치기)."""
    out: list[dict] = []
    seen: set[str] = set()
    for company in rows:
        # 계약 TenantId가 별칭을 정식 id로 바꾸지만, 만든 뒤 바뀐 값도 다시 거른다
        company = Company.model_validate(company.model_dump())
        if company.company_id in seen:
            continue
        seen.add(company.company_id)
        out.append(company.model_dump(mode="json"))
    if not out:
        raise ValueError("회사가 하나도 없는 목록은 쓰지 않는다")
    path = _place()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"companies": out}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)


def init_file() -> bool:
    """파일이 없으면 지금 목록으로 만들고 True. 이미 있으면(깨졌어도) 건드리지 않고 False."""
    path = _place()
    if path.exists():
        return False
    save(read.list_companies())
    return True


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["init"]:
        made = init_file()
        where = read.companies_file()
        done = "회사 목록을 만들었다" if made else "회사 목록이 이미 있어 그대로 둔다"
        print(f"{done}: {where}")
        return 0
    if args == ["list"]:
        for company in read.list_companies():
            print(f"{company.company_id}\t{company.name}")
        return 0
    print("사용법: python -m ax_dashboard_api.companies init|list", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
