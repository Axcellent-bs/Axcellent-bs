"""판매자 정산 서비스 에이전트 (settlement, 8126).

업무 로직은 `domains/support`(support_agent)를 그대로 쓰고,
이 폴더의 `agent-card.json`에 올린 skill만 받는다.
카드에 없는 skill은 템플릿 ACL이 막는다. 서비스 대응표: `ax_agent_template/services.py`.

정산은 클레임(claims)에 있다가 따로 나왔다(2026-11-12). 주문 30만 건 장부를 읽고 계산하는 동안
같은 프로세스의 클레임 처리가 느려졌기 때문이다.
"""

import os
from pathlib import Path

os.environ.setdefault(
    "SUPPORT_AGENT_CARD", str(Path(__file__).resolve().parents[2] / "agent-card.json")
)

from support_agent.main import app  # noqa: E402

__all__ = ["app"]
