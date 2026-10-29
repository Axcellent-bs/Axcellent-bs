"""요청·기획서 서비스 에이전트 (planning, 8101).

업무 로직은 `domains/planning`(planning_agent)를 그대로 쓰고,
이 폴더의 `agent-card.json`에 올린 skill만 받는다.
카드에 없는 skill은 템플릿 ACL이 막는다. 서비스 대응표: `ax_agent_template/services.py`.
"""

import os
from pathlib import Path

os.environ.setdefault(
    "PLANNING_AGENT_CARD", str(Path(__file__).resolve().parents[2] / "agent-card.json")
)
os.environ.setdefault("PLANNING_ROLE", "planning")

from planning_agent.main import app  # noqa: E402

__all__ = ["app"]
