"""예전 고객사 키 파일(BYOK)을 회사 AI 키로 옮긴다 (2026-11-23, 회사별 LLM 키).

    python -m dev_agent.migrate_llm_key [--dry-run] [--company <회사 ID>]

- 읽는 곳: `DEV_LLM_KEY_STORE`(예전 `PLANNING_LLM_KEY_STORE`)의 JSON `{api_key, base_url, model}`
- 넣는 곳: 키 저장소(db 모드) tenant=회사 ID의 `DEV_CODER_LLM_API_KEY`, 그리고 회사 AI 설정의
  코딩 에이전트를 "따로 설정"(`coder.mode=separate`, 예전 주소·모델)으로.
  예전에도 코딩만 이 키로 돌았으니 동작이 같다
- 회사 키(`LLM_API_KEY`)와 같은 값이면 따로 키를 만들지 않고 코딩 설정도 그대로 둔다
- 코딩 따로 키가 이미 있고 값이 다르면 아무것도 바꾸지 않는다(`conflict`, 사람이 정한다)
- 다 되면 예전 파일을 `.moved`로 남긴다(이미 있으면 `.moved.<시각>`). 지우지 않는다
- 값은 출력하지 않는다. 결과 코드만: no_legacy_file · secret_store_not_db · no_company · conflict
  · same_as_company · imported · already_imported (--dry-run이면 앞에 `would_`)
- 끝나면 개발 서비스를 다시 켜면 메모리의 예전 키도 비워진다(다시 켜지 않아도 회사 키가 먼저다)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from ax_agent_template import company_ai
from ax_contracts.schemas.company_ai import CompanyAiCoder
from dev_agent import llm_env

WHO = "migrate_llm_key"


def _read_legacy(path: str) -> dict[str, str] | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            "api_key": str(raw["api_key"]).strip(),
            "base_url": str(raw.get("base_url") or "").strip(),
            "model": str(raw.get("model") or "").strip(),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _move_aside(path: str) -> str:
    target = path + ".moved"
    if os.path.exists(target):
        target = f"{target}.{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    os.replace(path, target)
    return target


def migrate(*, dry_run: bool = False, company: str | None = None) -> str:
    """결과 코드 하나를 돌려준다(값은 담지 않는다)."""
    from ax_secret_store import is_db_mode, resolver

    prefix = "would_" if dry_run else ""
    path = llm_env.key_store_path()
    legacy = _read_legacy(path) if path and os.path.exists(path) else None
    if not legacy or not legacy["api_key"]:
        return "no_legacy_file"
    if not is_db_mode():
        return "secret_store_not_db"
    cid = company or company_ai.company_id()
    if not cid:
        return "no_company"
    value = legacy["api_key"]
    coder_now = company_ai.key(company_ai.CODER_KEY, cid)
    if coder_now and coder_now != value:
        return "conflict"
    if company_ai.key(company_ai.COMPANY_KEY, cid) == value:
        if not dry_run:
            _move_aside(path)
        return prefix + "same_as_company"
    settings = company_ai.load_settings(cid)
    coder = CompanyAiCoder(
        mode="separate",
        model=legacy["model"] or settings.coder.model,
        base_url=legacy["base_url"] or settings.coder.base_url,
    )
    code = "already_imported" if coder_now else "imported"
    if dry_run:
        return prefix + code
    if not coder_now:
        resolver.service().import_value(cid, company_ai.CODER_KEY, value, WHO)
        resolver.invalidate()
    company_ai.save_coder(cid, coder, WHO)
    _move_aside(path)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="예전 고객사 키 파일을 회사 AI 키로 옮긴다")
    parser.add_argument("--dry-run", action="store_true", help="바꾸지 않고 할 일만 보인다")
    parser.add_argument("--company", default=None, help="회사 ID (기본: 회사 연결의 지금 회사)")
    args = parser.parse_args(argv)
    code = migrate(dry_run=args.dry_run, company=args.company)
    print(code)
    ok = code in {"no_legacy_file", "imported", "already_imported", "same_as_company"}
    return 0 if ok or code.startswith("would_") else 2


if __name__ == "__main__":
    sys.exit(main())
