"""재무 관리 서비스 에이전트 (finance, 8105).

업무 로직은 `domains/finance`(finance_agent)를 그대로 쓰고,
이 폴더의 `agent-card.json`에 올린 skill만 받는다.
카드에 없는 skill은 템플릿 ACL이 막는다. 서비스 대응표: `ax_agent_template/services.py`.
"""

import os
from pathlib import Path

os.environ.setdefault(
    "FINANCE_AGENT_CARD", str(Path(__file__).resolve().parents[2] / "agent-card.json")
)

from finance_agent.main import app  # noqa: E402

__all__ = ["app"]
