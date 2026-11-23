"""회사 AI 설정·키 읽기 (서비스 공용, 2026-11-23).

관리자가 시스템 관리 › 회사 연결 › 공통 AI 도구 연결에서 넣은 회사 AI 설정과 키를 서비스가 읽는다.
쓰는 쪽은 dashboard-api(`ax_dashboard_api.company_ai`)이고 저장 규칙은 계약
`ax_contracts.schemas.company_ai`와 같다.

- 설정: `AX_COMPANY_AI_DIR`, 없으면 회사 연결 파일(`AX_COMPANY_CONNECTION_FILE`) 옆 `company_ai/`
  아래 `<회사 ID>/company_ai.json`(계약 `CompanyAiSettings`)
- 키: 암호화 키 저장소(`ax-secret-store`, tenant=회사 ID)의 `COMPANY_AI_KEY_NAMES`.
  키 저장소가 db 모드가 아니면 회사 키는 없는 것으로 본다(env 모드의 같은 이름 환경값은 서버 기본
  키라서 회사 키로 섞지 않는다)
- 지금 회사는 `tenant.current()`(회사 연결)에서만 꺼낸다
- 키 값은 로그·응답에 넣지 않는다. 화면용은 `hint()`(끝 4자리)만
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ax_agent_template import tenant as tenants
from ax_agent_template.companies import canonical_company_id
from ax_contracts.schemas.company_ai import CompanyAiCoder, CompanyAiSettings

log = logging.getLogger(__name__)

DIR_ENV = "AX_COMPANY_AI_DIR"
SETTINGS_FILE = "company_ai.json"
HISTORY_FILE = "company_ai.history.jsonl"
COMPANY_KEY = "LLM_API_KEY"
CODER_KEY = "DEV_CODER_LLM_API_KEY"


@dataclass(frozen=True)
class CompanyLlm:
    """회사 AI 키로 부를 때의 값. 주소·모델이 비면 서비스 기본 설정 값을 쓴다."""

    api_key: str = field(repr=False)
    base_url: str = ""
    model: str = ""
    source: str = "company"  # company(회사 키) | coder(코딩 에이전트 따로 키)


def company_id() -> str | None:
    """지금 회사 id(정식). 회사 연결 전이면 None."""
    return canonical_company_id(tenants.current())


def base_dir() -> Path | None:
    raw = os.getenv(DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name("company_ai") if conn else None


def folder(cid: str) -> Path | None:
    root = base_dir()
    return root / cid if root else None


def load_settings(cid: str) -> CompanyAiSettings:
    """저장한 회사 AI 설정. 없거나 깨졌으면 빈 설정."""
    where = folder(cid)
    if where is None:
        return CompanyAiSettings()
    try:
        raw = json.loads((where / SETTINGS_FILE).read_text(encoding="utf-8"))
        return CompanyAiSettings.model_validate(raw)
    except (OSError, ValueError):
        return CompanyAiSettings()


def save_coder(cid: str, coder: CompanyAiCoder, who: str) -> None:
    """코딩 에이전트 설정만 바꿔 저장하고 이력에 남긴다(옮기기 명령용). 0600으로 쓴다."""
    where = folder(cid)
    if where is None:
        raise RuntimeError(f"회사 AI 설정을 둘 곳이 없다 ({DIR_ENV} 필요)")
    where.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    settings = load_settings(cid).model_copy(
        update={"coder": coder, "updated_at": now, "updated_by": who[:64]}
    )
    path = where / SETTINGS_FILE
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(settings.model_dump_json())
    os.replace(tmp, path)
    entry = {"at": now.isoformat(), "by": who[:64], "action": "settings", "what": "coder"}
    with (where / HISTORY_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def key(name: str, cid: str) -> str:
    """회사 키 하나. 키 저장소가 db 모드가 아니거나 없으면 빈 문자열."""
    from ax_secret_store import get_secret, is_db_mode

    if not is_db_mode():
        return ""
    try:
        return get_secret(name, tenant=cid).strip()
    except Exception as error:  # noqa: BLE001 - 키 저장소 장애는 회사 키 없음으로 본다
        log.warning("회사 AI 키를 읽지 못함 (%s)", type(error).__name__)
        return ""


def coder_llm(cid: str | None = None) -> CompanyLlm | None:
    """개발 AI 코딩 에이전트가 쓸 회사 키. 없으면 None.

    `coder.mode == "separate"`면 `DEV_CODER_LLM_API_KEY`와 코딩 주소·모델(비면 회사 값),
    아니면 회사 키 `LLM_API_KEY`와 회사 주소·모델.
    """
    use = cid or company_id()
    if not use:
        return None
    settings = load_settings(use)
    if settings.coder.mode == "separate":
        value = key(CODER_KEY, use)
        if not value:
            return None
        return CompanyLlm(
            api_key=value,
            base_url=settings.coder.base_url or settings.base_url,
            model=settings.coder.model or settings.model,
            source="coder",
        )
    value = key(COMPANY_KEY, use)
    if not value:
        return None
    return CompanyLlm(api_key=value, base_url=settings.base_url, model=settings.model)


def hint(value: str) -> str:
    """화면용 끝 4자리(짧으면 없음)."""
    return value[-4:] if len(value) >= 12 else ""
