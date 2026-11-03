"""고객사 API 키 (BYOK): 서버 AI 코딩을 고객사 자기 LLM 키로 돌린다.

- 넣으면 코딩 에이전트(coder.py)가 서버 기본 키 대신 이 키로 부른다. 서버 AI 체험 한도는
  기본 키에만 걸리고, 고객사 키에는 월 예산(`State.monthly_budget_tokens`)만 걸린다.
- 키는 이 프로세스 메모리와 저장 파일(`PLANNING_LLM_KEY_STORE`, run_all.sh가 git에 안 올라가는
  backend/.data/에 둔다)에만 둔다. 화면·로그·상태 파일(planning_state)로는 돌려주지 않는다
  (넣었는지와 끝 4자리만).
- 주소·모델을 비우면 서버 설정(`PLANNING_LLM_*`)의 주소·모델을 쓴다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace

from ax_agent_template.llm import LLMSettings


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
    return os.getenv("PLANNING_LLM_KEY_STORE") or ""


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


def active() -> bool:
    return _key is not None


def apply(base: LLMSettings) -> LLMSettings:
    """서버 설정 위에 고객사 키(와 주소·모델)를 얹는다. 고객사 키가 없으면 그대로."""
    if _key is None:
        return base
    return replace(
        base,
        api_key=_key.api_key,
        base_url=_key.base_url or base.base_url,
        model=_key.model or base.model,
    )


def public() -> dict:
    """화면에 보여 줄 모양 (키 값은 넣지 않는다)."""
    if _key is None:
        return {"key_set": False, "key_hint": "", "base_url": "", "model": ""}
    return {
        "key_set": True,
        "key_hint": _hint(_key.api_key),
        "base_url": _key.base_url,
        "model": _key.model,
    }
