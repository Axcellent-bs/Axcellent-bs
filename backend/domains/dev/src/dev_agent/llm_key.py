"""고객사 API 키 (BYOK): 서버 AI 코딩을 고객사 자기 LLM 키로 돌린다.

- 넣으면 코딩 에이전트(coder.py)가 서버 기본 키 대신 이 키로 부른다. 서버 AI 체험 한도는
  기본 키에만 걸리고, 고객사 키에는 월 예산(`State.monthly_budget_tokens`)만 걸린다.
- 키는 이 프로세스 메모리와 저장 파일(`DEV_LLM_KEY_STORE`, 예전 이름
  `PLANNING_LLM_KEY_STORE`도 읽음. run_all.sh가 git에 안 올라가는 backend/.data/에
  둔다)에만 둔다. 화면·로그·상태 파일(planning_state)로는 돌려주지 않는다 (넣었는지와 끝 4자리만).
- 주소·모델을 비우면 서버 설정(`DEV_LLM_*`·`LLM_*`)의 주소·모델을 쓴다.

회사 AI 키로 옮김(2026-11-23, 회사 공간 나누기 7단계 "회사별 LLM 키"):
- 먼저 회사 AI 키를 본다. 관리자가 회사 연결 › 공통 AI 도구 연결에 넣은 키다
  (키 저장소 tenant=지금 회사. 코딩 에이전트 "따로 설정"이면 `DEV_CODER_LLM_API_KEY`,
  아니면 회사 키 `LLM_API_KEY`). 읽기는 `ax_agent_template.company_ai`
- 회사 키가 없을 때만 위의 예전 파일 키(BYOK)를 쓴다. 예전 파일은
  `python -m dev_agent.migrate_llm_key`로 회사 AI 키(코딩 따로 키)로 옮기고 `.moved`로 남긴다
- 키 저장소가 db 모드면 예전 파일에 새로 넣지 않는다(`legacy_writes_allowed`).
  넣는 곳은 공통 AI 도구 연결
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace

from ax_agent_template import company_ai
from ax_agent_template.llm import LLMSettings
from dev_agent import llm_env


@dataclass(frozen=True)
class CompanyKey:
    api_key: str = field(repr=False)
    base_url: str = ""
    model: str = ""


_key: CompanyKey | None = None


class LlmKeyError(ValueError):
    """입력이 틀렸다 (메시지에 키 값을 넣지 않는다)."""


def _hint(key: str) -> str:
    return f"••••{key[-4:]}" if len(key) >= 12 else "••••"


def _store_path() -> str:
    return llm_env.key_store_path()


def _save() -> None:
    path = _store_path()
    if not path:
        return
    if _key is None:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    # 키 파일은 만든 사용자만 읽는다. 프로세스 umask(서버는 0002)로 만들면 그룹·다른 사용자가
    # 읽을 수 있으므로 처음부터 0600으로 만들고, 예전 임시 파일이 남아 있어도 권한을 다시 맞춘다
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(tmp, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(_key), f)
    os.replace(tmp, path)


def load_saved() -> bool:
    """저장해 둔 고객사 키를 읽는다. 읽었으면 True (없거나 깨졌으면 False)."""
    global _key
    path = _store_path()
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        _key = CompanyKey(
            api_key=str(raw["api_key"]),
            base_url=str(raw.get("base_url") or ""),
            model=str(raw.get("model") or ""),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def set_key(api_key: str | None, base_url: str = "", model: str = "") -> None:
    """고객사 키를 넣거나 바꾼다. 키를 비우면 이전 키를 그대로 쓰고 주소·모델만 바꾼다."""
    global _key
    new = (api_key or "").strip()
    if new and (not new.isascii() or any(ch.isspace() for ch in new) or len(new) > 400):
        raise LlmKeyError("키에 쓸 수 없는 글자(한글·공백 등)가 있어요. 다시 복사해 넣어 주세요")
    url = (base_url or "").strip()
    if url and not url.startswith(("https://", "http://")):
        raise LlmKeyError("주소는 http(s)://로 시작해야 해요")
    name = (model or "").strip()
    if any(ch.isspace() for ch in name) or len(name) > 100:
        raise LlmKeyError("모델 이름이 올바르지 않아요")
    key = new or (_key.api_key if _key else "")
    if not key:
        raise LlmKeyError("고객사 API 키를 넣어 주세요")
    _key = CompanyKey(api_key=key, base_url=url, model=name)
    _save()


def clear() -> None:
    global _key
    _key = None
    _save()


def legacy_writes_allowed() -> bool:
    """예전 파일 키(BYOK)에 새로 넣어도 되나. 키 저장소가 db 모드면 공통 AI 도구 연결에서 넣는다."""
    from ax_secret_store import is_db_mode

    return not is_db_mode()


def _company() -> company_ai.CompanyLlm | None:
    return company_ai.coder_llm()


def active() -> bool:
    """서버 기본 키가 아닌 고객사 키(회사 AI 키 또는 예전 파일 키)로 도나."""
    return _company() is not None or _key is not None


def apply(base: LLMSettings) -> LLMSettings:
    """서버 설정 위에 고객사 키(와 주소·모델)를 얹는다. 회사 AI 키 → 예전 파일 키 → 그대로."""
    company = _company()
    if company is not None:
        return replace(
            base,
            api_key=company.api_key,
            base_url=company.base_url or base.base_url,
            model=company.model or base.model,
        )
    if _key is None:
        return base
    return replace(
        base,
        api_key=_key.api_key,
        base_url=_key.base_url or base.base_url,
        model=_key.model or base.model,
    )


def public() -> dict:
    """화면에 보여 줄 모양 (키 값은 넣지 않는다). 회사 AI 키면 `source`: company·coder."""
    company = _company()
    if company is not None:
        return {
            "key_set": True,
            "key_hint": _hint(company.api_key),
            "base_url": company.base_url,
            "model": company.model,
            "source": company.source,
        }
    if _key is None:
        return {"key_set": False, "key_hint": "", "base_url": "", "model": ""}
    return {
        "key_set": True,
        "key_hint": _hint(_key.api_key),
        "base_url": _key.base_url,
        "model": _key.model,
    }
