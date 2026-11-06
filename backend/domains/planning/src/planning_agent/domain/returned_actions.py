"""개발이 "코드 변경이 아니다"로 돌려보낸 작업을 사람이 처리한다 (기획 화면 요청 상세).

개발(dev coder `not_code`)은 기획이 넘긴 작업이 다른 부서 일이면 PR 없이
`coding.status = "returned"`(`returned_to`·`returned_reason`)로 두고 `receive_dev_result`로 알린다.
기획은 그 요청을 "개발이 돌려보냄"으로 띄우고, 사람이 셋 중 하나를 고른다.

- 마케팅으로 보내기(`returned_to_marketing`): 기획안을 마케팅에 넘긴다(`receive_campaign_brief`).
  제안에서 만든 기획안이면 그 제안으로, 아니면 기획서 번호를 `proposal_id` 자리에 넣어 보낸다.
- 개발로 다시 보내기(`returned_to_dev`, 이유 필수): 기획서에 `keep_dev`를 켜고 이유를 남겨
  `start_dev`를 다시 보낸다. 개발은 그 작업을 처음부터 다시 코딩하고 다시 돌려보내지 않는다.
- 닫기(`dismiss_returned`): 기획 화면에서만 숨긴다 (개발 작업은 그대로).

처리했는지는 활동(`ctx = returned:<작업 id>`)으로 남기고 화면도 그것으로 가린다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ax_contracts.schemas.planning import CampaignBriefInput
from ax_workflow.core import ActionError, ActionResult
from ax_workflow.models import ProposalHandoff, Spec, State, Task

RETURNED_MARKETING = "returned_marketing"
RETURNED_DEV = "returned_dev"
RETURNED_DISMISSED = "returned_dismissed"
DEV_RETURNED = "dev_returned"  # 개발이 돌려보낸 것을 받았다 (기록만)
HANDLED = (RETURNED_MARKETING, RETURNED_DEV, RETURNED_DISMISSED)
REDEV_NOTE = "개발에 다시 맡긴 이유"
DEPT_KO = {"marketing": "마케팅", "support": "고객지원", "finance": "재무", "hr": "인사",
           "data": "데이터분석", "other": "다른 부서"}  # fmt: skip


def return_ctx(task_id: str) -> str:
    return f"returned:{task_id}"


def handled(s: State, task_id: str) -> bool:
    ctx = return_ctx(task_id)
    return any(a.context_id == ctx and a.point in HANDLED for a in s.activity)


def pending_return(s: State, task_id: str) -> Task | None:
    """사람이 아직 처리하지 않은, 개발이 돌려보낸 작업."""
    t = next((x for x in s.tasks if x.id == task_id), None)
    if t is None or t.discarded or t.coding is None or t.coding.status != "returned":
        return None
    return None if handled(s, t.id) else t


class ReturnedParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    task: str


class ReturnedDevParams(ReturnedParams):
    reason: str = Field(min_length=1, max_length=500)


class ReturnedMixin:
    """`PlanningService`가 섞어 쓴다. _act·clock·_sending·_marketing·_spawn_brief를 쓴다."""

    RETURNED_ACTIONS: dict[str, tuple[str, type[BaseModel]]] = {
        "returned_to_marketing": ("_returned_to_marketing", ReturnedParams),
        "returned_to_dev": ("_returned_to_dev", ReturnedDevParams),
        "dismiss_returned": ("_dismiss_returned", ReturnedParams),
    }

    def _returned(self: Any, s: State, task: str) -> tuple[Task, Spec]:
        t = pending_return(s, task)
        if t is None:
            raise ActionError("NOT_RETURNED", f"{task}는 개발이 돌려보낸 처리 전 작업이 아닙니다")
        sp = next((x for x in s.specs if x.id == t.spec), None)
        if sp is None:
            raise ActionError("NOT_FOUND", f"{task}의 기획서 {t.spec}가 없습니다")
        return t, sp

    def _ret_ctx(self: Any, s: State, sp: Spec) -> str:
        req = next((r for r in s.requests if r.id == sp.request or r.spec == sp.id), None)
        return req.context_id if req else f"spec:{sp.id}"

    async def _returned_to_marketing(self: Any, s: State, task: str) -> ActionResult | None:
        t, sp = self._returned(s, task)
        p = next((x for x in (s.proposals or []) if x.spec == sp.id), None)
        from planning_agent.domain.proposal_actions import plan_markdown

        body = plan_markdown(sp)
        if (
            p is not None
        ):  # 제안에서 만든 기획안: 제안 그대로 마케팅에 (성과도 이 제안으로 이어진다)
            p.status = "마케팅 전달"
            if self._marketing is None:
                p.handoff = ProposalHandoff(ok=True, at=self.clock())
            else:
                self._spawn_brief(p, body)
        elif self._marketing is not None:
            reason = t.coding.returned_reason if t.coding else None
            brief = CampaignBriefInput(
                proposal_id=sp.id, title=sp.title, idea=sp.goal or sp.title,
                why=reason or sp.problem or sp.title, approved_by="사람", body_md=body,
            )  # fmt: skip
            job = asyncio.create_task(self._send_returned_brief(t.id, sp, brief))
            self._sending.add(job)
            job.add_done_callback(self._sending.discard)
        text = f"{sp.id} {t.id} 개발이 돌려보낸 일 → 마케팅으로 보냄"
        act = self._act(self.clock(), return_ctx(t.id), "planning", "marketing",
                        RETURNED_MARKETING, "사람", text)  # fmt: skip
        s.activity.append(act)
        return ActionResult(output={"task_id": t.id, "spec_id": sp.id, "to": "marketing"})

    async def _send_returned_brief(
        self: Any, task_id: str, sp: Spec, brief: CampaignBriefInput
    ) -> None:
        out = await self._marketing.send(return_ctx(task_id), brief, f"returned:{task_id}:brief")
        ok = not isinstance(out, str) and out.outcome != "rejected"
        why = out if isinstance(out, str) else f"마케팅이 거절 ({out.reason_code})"

        async def record(s: State) -> None:
            text = f"{sp.id} 마케팅 " + ("받음" if ok else f"전달 실패 · {why}")
            s.activity.append(
                self._act(self.clock(), return_ctx(task_id), "marketing", "planning",
                          "receive_campaign_brief", "A2A", text)
            )  # fmt: skip

        await self._apply(record)

    async def _returned_to_dev(self: Any, s: State, task: str, reason: str) -> ActionResult | None:
        t, sp = self._returned(s, task)
        why = " ".join(reason.split())
        if not why:
            raise ActionError("REASON_REQUIRED", "개발로 다시 보내는 이유를 적어 주세요")
        sp.keep_dev = True
        sp.extra = {**sp.extra, REDEV_NOTE: why}  # 개발이 기획서와 같이 받아 읽는다
        self._redev_queue().append((sp.id, t.id))
        text = f"{sp.id} {t.id} 개발로 다시 보냄 · {why}"
        act = self._act(self.clock(), return_ctx(t.id), "planning", "dev",
                        RETURNED_DEV, "사람", text)  # fmt: skip
        s.activity.append(act)
        return ActionResult(output={"task_id": t.id, "spec_id": sp.id, "to": "dev"})

    async def _dismiss_returned(self: Any, s: State, task: str) -> ActionResult | None:
        t, sp = self._returned(s, task)
        text = f"{sp.id} {t.id} 돌려보낸 일 닫음 (기획 화면에서만)"
        act = self._act(self.clock(), return_ctx(t.id), "human", "planning",
                        RETURNED_DISMISSED, "사람", text)  # fmt: skip
        s.activity.append(act)
        return ActionResult(output={"task_id": t.id})

    def _redev_queue(self: Any) -> list[tuple[str, str]]:
        """이번 액션에서 개발로 다시 보낼 (기획서, 작업). `_dev_outgoing`이 꺼내 보낸다."""
        if not hasattr(self, "_redev"):
            self._redev = []
        return self._redev

    def _log_new_returns(self: Any, s: State, before: dict[str, str | None], ctx: str) -> None:
        """개발 결과로 새로 돌려보낸 작업이 왔으면 한 줄 남긴다 (화면 "개발이 돌려보냄")."""
        for t in s.tasks:
            cr = t.coding
            if cr is None or cr.status != "returned" or before.get(t.id) == "returned":
                continue
            dept = DEPT_KO.get(cr.returned_to or "", cr.returned_to or "다른 부서")
            text = f"{t.spec} {t.id} 개발이 돌려보냄 → {dept}" + (
                f" · {cr.returned_reason}" if cr.returned_reason else ""
            )
            s.activity.append(
                self._act(self.clock(), ctx, "dev", "planning", DEV_RETURNED, "A2A", text, "A")
            )
