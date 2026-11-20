"""기획·개발 상태를 파일에 이어 쓴다. 서버를 다시 켜도 요청·기획서·작업·PR·대화 기록이 남는다.

`PLANNING_STATE_DIR`가 있을 때만 쓴다(없으면 예전처럼 프로세스 메모리만). 기획(8101)과
개발(8111)은 같은 코드를 쓰지만 서비스 역할마다 파일을 따로 둔다(`planning_state_<역할>.json`).
같은 파일을 두 프로세스가 쓰면 서로 덮어쓰기 때문이다.

- 상태와 함께 중복을 거르는 키 기록(`_idempotent`)·넘기기 지문(`_dev_sent`)·못 보낸 넘기기
  (`_unsent`)도 남긴다.
  재시작 뒤 같은 넘기기를 다시 처리하지 않게 하려는 것이다.
- 쓰기는 임시 파일에 쓰고 바꿔 끼운다(쓰는 중에 꺼져도 앞 파일이 남는다).
- 읽을 때 돌던 서버 AI 코딩 작업(대기·읽기·쓰기·리뷰 중, PR 연 직후)은 다시 켜면 그 실행이 없다.
  열린 PR이 없으면 실패로 바꿔 사람이 다시 시도·재개로 잇고, 열린 PR이 있으면 사람 확인으로
  바꿔 머지·추가 지시로 잇는다 (열린 PR이 있으면 다시 시도는 거절되기 때문이다).
  내 Claude(구독형) 작업은 이 서버가 돌리지 않아 그대로 둔다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from pydantic import ValidationError

from ax_workflow.models import State, dump

ENV_DIR = "PLANNING_STATE_DIR"
RUNNING = ("queued", "reading", "writing", "reviewing")


def path_for(role: str) -> str | None:
    folder = (os.getenv(ENV_DIR) or "").strip()
    if not folder:
        return None
    return os.path.join(folder, f"planning_state_{role or 'all'}.json")


def save(
    path: str,
    state: State,
    idempotent: dict[str, Any],
    dev_sent: dict[str, str],
    unsent: dict[str, list] | None = None,
) -> None:
    data = {
        "state": dump(state),
        "idempotent": {k: asdict(v) for k, v in idempotent.items()},
        "dev_sent": dict(dev_sent),
        "unsent": dict(unsent or {}),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def load(path: str) -> tuple[State, dict[str, dict], dict[str, str], dict[str, list]] | None:
    """저장한 상태. 없거나 읽지 못하면 None (시드로 시작)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        state = State.model_validate(data["state"])
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        return None
    for task in state.tasks:
        cr = task.coding
        if cr is None or cr.mode == "subscription":
            continue  # 내 Claude 작업은 이 서버가 돌리지 않는다 (대기열·임대·PR 맞추기가 본다)
        if cr.status not in (*RUNNING, "opened"):
            continue
        cr.waiting_for = []
        if cr.status == "opened" and any(p.id == cr.pr and p.merged for p in state.prs):
            continue  # 연 PR이 이미 머지됨: 끊긴 실행이 없으니 실패로 바꾸지 않는다
        open_pr = any(p.task == task.id and not p.merged and not p.closed for p in state.prs)
        if open_pr:
            # PR은 열려 있어 다시 시도는 거절된다: 사람이 리뷰를 보고 머지·추가 지시로 잇는다
            cr.status = "waiting_human"
            cr.transient = False
            cr.error = "서버 재시작으로 리뷰를 다시 확인해야 해요 → PR을 보고 머지하거나 추가 지시"
            continue
        cr.status = "failed"
        cr.error = "서버를 다시 켜서 멈춤 → 다시 시도로 이어서"
    return (
        state,
        dict(data.get("idempotent") or {}),
        dict(data.get("dev_sent") or {}),
        dict(data.get("unsent") or {}),
    )
