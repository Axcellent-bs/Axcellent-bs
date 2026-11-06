"""화면에 그대로 보이는 문장에서 내부 이름(관문 액션·결정 값)을 사람 말로 바꾼다.

긴 문장은 뜻이 끊기는 자리에 `\\n`을 넣는다. 화면(LinkedLines)이 그 줄대로 보여 준다.
"""

from __future__ import annotations

_ACTION = {
    "generate_pr": "PR 열기",
    "merge_pr": "머지",
    "review_pr": "PR 리뷰",
    "approve_repo": "레포 승인",
    "reject_repo": "레포 거절",
    "dev_task_cancel": "작업 중단",
    "dev_task_covered": "이미 반영됨으로 닫기",
    "dev_task_discard": "작업 폐기",
    "dev_task_retry": "다시 시도",
    "dev_task_return": "기획에 돌려보내기",
    "dev_task_revise": "추가 지시",
}
_DECISION = {"held": "보류", "rejected": "거절", "expired": "만료"}


def action_ko(action: str) -> str:
    return _ACTION.get(action, action)


def gate_text(action: str, status: str | None = None) -> str:
    """관문이 막은 일 한 줄: "안전 점검에서 PR 열기 보류" """
    what = action_ko(action)
    if status is None:
        return f"안전 점검에서 {what} 막힘"
    return f"안전 점검에서 {what} {_DECISION.get(status, status)}"
