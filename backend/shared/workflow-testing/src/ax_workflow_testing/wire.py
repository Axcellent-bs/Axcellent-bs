"""시험 도우미: 기획·개발 두 서비스를 메모리 안 가짜 송신기로 잇는다 (분리 4단계).

서비스 코드는 쓰지 않는다 (도메인끼리 import 금지라 두 도메인을 함께 부르는 시험 도우미만).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from pydantic import BaseModel

from ax_workflow.dev_handoff import START_DEV
from dev_agent.service import DevService
from planning_agent.domain.service import PlanningService


class Wire:
    """기획(planning)·개발(dev) 서비스 둘을 메모리 안 가짜 송신기로 잇는다 (네트워크 없음).

    요청 → 기획서 → PR → 머지처럼 두 서비스를 함께 도는 시험이 쓴다. `run`은 액션 하나를
    돌리고 넘기기가 다 오갈 때까지 기다린다 (같은 이벤트 루프 안에서)."""

    def __init__(
        self,
        forward: bool = True,
        real_example: bool = False,
        gate: Any = None,
        planning: PlanningService | None = None,
        dev: DevService | None = None,
    ):
        """`planning`·`dev`를 주면 그 서비스를 그대로 쓴다 (넘기기만 이 송신기로 바꾼다)."""
        self.forward = forward
        self.sent: list[tuple[str, str]] = []
        self.planning = planning or PlanningService(real_example=real_example, gate=gate)
        self.dev = dev or DevService(real_example=real_example, gate=gate)
        self.planning._send_dev = self.send
        self.dev._send_dev = self.send

    async def send(self, skill: str, ctx: str, tenant: str, payload: BaseModel, key: str) -> str:
        self.sent.append((skill, key))
        if not self.forward:
            return "보냄"
        body = payload.model_dump(mode="json")
        if skill == START_DEV:
            await self.dev.start_dev(
                context_id=ctx, requester="planning", idempotency_key=key, **body
            )
        else:
            await self.planning.receive_dev_result(
                context_id=ctx, requester="dev", idempotency_key=key, **body
            )
        return "보냄"

    async def settle(self) -> None:
        for _ in range(10):
            await self.planning.wait_sent()
            await self.dev.wait_sent()

    def run(self, aw: Awaitable[Any]) -> Any:
        async def go() -> Any:
            out = await aw
            await self.settle()
            return out

        return asyncio.run(go())

    def skills(self) -> list[str]:
        return [k for k, _ in self.sent]

    def activity(self, ctx: str | None = None) -> list:
        """두 서비스의 활동을 시각 순으로 (넘기기로 같은 줄이 양쪽에 남으면 하나만)."""
        seen: set[tuple] = set()
        out = []
        both = [*self.planning.state.activity, *self.dev.state.activity]
        for a in sorted(both, key=lambda x: x.at):
            key = (a.at, a.context_id, a.point, a.text)
            if key in seen or (ctx is not None and a.context_id != ctx):
                continue
            seen.add(key)
            out.append(a)
        return out


def feed(dev: DevService) -> Wire:
    """이미 띄운 개발 서비스 앞에 기획 서비스를 붙인다 (기획 쪽 준비 → 개발로 넘김).

    같은 개발 서비스에는 같은 기획 서비스를 쓴다 (질문 답 → 확정처럼 이어지는 준비)."""
    w = dev.__dict__.get("_test_wire")
    if w is None:
        w = dev.__dict__["_test_wire"] = Wire(dev=dev, gate=dev._gate)
    return w


def plan(svc: PlanningService | DevService, action: str, *args: Any, **kw: Any) -> Any:
    """기획 액션 하나를 돌린다. 개발 서비스면 붙인 기획 서비스에서 돌리고 결과를 넘겨받는다."""
    if isinstance(svc, PlanningService):
        return asyncio.run(getattr(svc, action)(*args, **kw))
    w = feed(svc)
    return w.run(getattr(w.planning, action)(*args, **kw))
