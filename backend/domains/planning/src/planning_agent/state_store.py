"""기획·개발 상태를 파일에 이어 쓴다. 서버를 다시 켜도 요청·기획서·작업·PR·대화 기록이 남는다.

`PLANNING_STATE_DIR`가 있을 때만 쓴다(없으면 예전처럼 프로세스 메모리만). 기획(8101)과
개발(8111)은 같은 코드를 쓰지만 서비스 역할마다 파일을 따로 둔다(`planning_state_<역할>.json`).
같은 파일을 두 프로세스가 쓰면 서로 덮어쓰기 때문이다.

- 상태와 함께 중복을 거르는 키 기록(`_idempotent`)·넘기기 지문(`_dev_sent`)도 남긴다.
  재시작 뒤 같은 넘기기를 다시 처리하지 않게 하려는 것이다.
- 쓰기는 임시 파일에 쓰고 바꿔 끼운다(쓰는 중에 꺼져도 앞 파일이 남는다).
- 읽을 때 돌던 코딩 작업(읽기·쓰기·리뷰 중)은 실패로 바꾼다. 다시 켜면 그 실행은 없기 때문이다.
  사람이 다시 시도·재개로 이어 간다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from pydantic import ValidationError

from planning_agent.domain.models import State, dump

ENV_DIR = "PLANNING_STATE_DIR"
RUNNING = ("queued", "reading", "writing", "reviewing")


def path_for(role: str) -> str | None:
    folder = (os.getenv(ENV_DIR) or "").strip()
    if not folder:
        return None
    return os.path.join(folder, f"planning_state_{role or 'all'}.json")


def save(path: str, state: State, idempotent: dict[str, Any], dev_sent: dict[str, str]) -> None:
    data = {
        "state": dump(state),
        "idempotent": {k: asdict(v) for k, v in idempotent.items()},
        "dev_sent": dict(dev_sent),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def load(path: str) -> tuple[State, dict[str, dict], dict[str, str]] | None:
    """저장한 상태. 없거나 읽지 못하면 None (시드로 시작)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        state = State.model_validate(data["state"])
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        return None
    for task in state.tasks:
        cr = task.coding
        if cr is not None and cr.status in RUNNING:
            cr.status = "failed"
            cr.error = "서버를 다시 켜서 멈춤 → 다시 시도로 이어서"
            cr.waiting_for = []
    return state, dict(data.get("idempotent") or {}), dict(data.get("dev_sent") or {})
