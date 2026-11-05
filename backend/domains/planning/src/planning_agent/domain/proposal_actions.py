"""기획 제안 액션 (PlanningService에 섞어 쓴다). 바깥 연결·판단 초안은 `planning_agent.proposals`.

흐름: 데이터분석 인사이트(근거 수치) → 기획 판단(할 일·이유·목표) → 사람 확정·폐기
      → 확정하면 마케팅(approve_proposal) 또는 개발(proposal_to_request) → 성과 기록

사람 버튼: 받아오기(pull_insights) · 확정→마케팅(approve_proposal)
          · 확정→개발(proposal_to_request) · 폐기(reject_proposal) · 고치기(edit_proposal)
받는 skill: receive_insight(data·marketing → 제안)
          · receive_campaign_update(marketing 게시 · data 성과)

LLM 초안·다른 부서 호출(data promo_concepts, marketing receive_campaign_brief)은
상태 잠금 밖에서 한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ax_contracts.schemas.planning import (
    CampaignBriefInput,
    InsightFact,
    ReceiveCampaignUpdateInput,
    ReceiveInsightInput,
)
from ax_contracts.schemas.planning import ProposalTarget as TargetIn
from ax_workflow.core import ActionError, ActionResult
from ax_workflow.models import (
    Para,
    Proposal,
    ProposalFact,
    ProposalHandoff,
    ProposalResult,
    ProposalTarget,
    Request,
    Spec,
    State,
)
from planning_agent import proposals as ext

PULL_CTX = "ctx_proposals"
DEPT_LABEL = {"data": "데이터분석", "marketing": "마케팅"}
EFFECT_MAX_WINDOW = timedelta(days=7)  # 데이터분석 campaign_effect MAX_WINDOW와 같게


class ProposalParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    proposal: str


class ApproveProposalParams(ProposalParams):
    note: str = ""


class RejectProposalParams(ProposalParams):
    reason: str = Field(min_length=1, max_length=300)


class EditProposalParams(ProposalParams):
    title: str = Field(default="", max_length=120)
    idea: str = Field(default="", max_length=400)
    why: str = Field(default="", max_length=400)
    target_label: str = Field(default="", max_length=120, alias="targetLabel")
    target_value: str = Field(default="", max_length=80, alias="targetValue")


def effect_window(
    handoff: ProposalHandoff, created_at: datetime, now: datetime
) -> tuple[datetime, datetime]:
    """성과를 잴 기간. 마케팅이 게시를 알렸으면(`published_at`) 그때부터, 아니면 넘긴 시각부터.
    데이터분석 campaign_effect는 7일보다 긴 기간을 판정하지 않아서(확인 불가) 7일에서 자른다."""
    start = handoff.published_at or handoff.at or created_at
    return start, min(now, start + EFFECT_MAX_WINDOW)


def next_proposal_id(s: State) -> str:
    nums = [int(x.id.split("-")[1]) for x in s.proposals if x.id.split("-")[-1].isdigit()]
    return f"PROP-{max(nums, default=0) + 1}"


def _ctx(p: Proposal) -> str:
    return f"proposal:{p.id}"


def _facts_in(p: Proposal) -> list[InsightFact]:
    return [
        InsightFact(label=f.label, value=f.value, source=f.source, link=f.link) for f in p.facts
    ]


def _facts_of(facts: list[InsightFact]) -> list[ProposalFact]:
    return [ProposalFact(label=f.label, value=f.value, source=f.source, link=f.link) for f in facts]


def _target_in(p: Proposal) -> TargetIn | None:
    t = p.target
    return TargetIn(label=t.label, value=t.value, measure=t.measure) if t else None


def _recheck(p: Proposal) -> None:
    """사람이 고친 뒤 숫자 검사를 다시 한다 (근거·목표에 없는 숫자 경고)."""
    texts = {"할 일": p.idea, "이유": p.why, "설명": p.body_md or ""}
    p.warnings = ext.number_warnings(texts, _facts_in(p), _target_in(p), p.title)


PROPOSAL_ORIGIN = "proposal"  # 기획 제안에서 만든 기획서 (Spec.origin)


def plan_markdown(sp: Spec) -> str:
    """기획안(기획서) → 마크다운. 사람이 고친 칸과 자유 작성 칸이 그대로 넘어간다."""
    out = [f"# {sp.title}", "", "## 왜", sp.problem, "", "## 목표", sp.goal, ""]
    out += ["## 요구사항", *[f"- {x.id} {x.text}" for x in sp.paras if x.kind == "req"], ""]
    out += ["## 완료 기준", *[f"- {x.id} {x.text}" for x in sp.paras if x.kind == "done"], ""]
    if sp.basis:
        out += ["## 근거", *[f"- {b}" for b in sp.basis], ""]
    if sp.body_md:
        out += ["## 자유 작성", sp.body_md]
    return "\n".join(out)[:8000]


def _plan_spec(s: State, p: Proposal) -> Spec | None:
    return next((x for x in s.specs if x.id == p.spec), None) if p.spec else None


def _next_num(ids: list[str]) -> int:
    nums = [int(x.split("-")[1]) for x in ids if x.split("-")[-1].isdigit()]
    return max(nums, default=0) + 1


def _can_send(p: Proposal) -> bool:
    """마케팅에 (다시) 보낼 수 있는 상태: 확인 대기·기획안, 또는 전달에 실패한 채 남은 예전 기록."""
    failed = p.status == "마케팅 전달" and p.handoff is not None and not p.handoff.ok
    return p.status in ("확인 대기", "기획안") or failed


def _undo_send(s: State, p: Proposal) -> None:
    """전달 실패: 제안은 기획안으로, 기획안 기획서는 초안으로, 요청은 기획 중으로 되돌린다."""
    p.status = "기획안" if p.spec else "확인 대기"
    sp = next((x for x in s.specs if x.id == p.spec), None) if p.spec else None
    if sp is not None and not sp.handed_to_dev:
        sp.status = "초안"
        req = next((r for r in s.requests if r.id == sp.request), None)
        if req is not None and req.state == "작업 중":
            req.state = "기획 중"


def _find(s: State, pid: str) -> Proposal | None:
    return next((x for x in s.proposals if x.id == pid), None)


def _same_insight(s: State, ref: str, frm: str) -> Proposal | None:
    return next((p for p in s.proposals if p.insight_id == ref and p.from_ == frm), None)


# 이 기간 안에 받은 제안과 견준다 (폐기한 것 포함: 사람이 버린 걸 다시 올리지 않게)
SIMILAR_DAYS = 30
SIMILAR_TITLE = 0.8  # 제목 두 글자 묶음 겹침 비율이 이 이상이면 같은 제안으로 본다


def _bigrams(text: str) -> set[str]:
    t = "".join(ch for ch in text.lower() if ch.isalnum())
    return {t[i : i + 2] for i in range(len(t) - 1)} or {t}


def similar_proposal(s: State, inp: ReceiveInsightInput, now: datetime) -> Proposal | None:
    """받아온 인사이트와 비슷한 최근 제안 (데이터분석 id가 매번 바뀌어도 거른다, #664).

    코드 규칙만 쓴다 (LLM 없음, 같은 입력이면 같은 결과):
    - 관련 상품이 같고 근거(트렌드·숏츠 형식 등 facts 이름)가 하나라도 같다
    - 또는 제목의 두 글자 묶음이 `SIMILAR_TITLE` 이상 겹친다
    """
    since = now - timedelta(days=SIMILAR_DAYS)
    goods = {x.strip().lower() for x in inp.products if x.strip()}
    labels = {f.label for f in inp.facts}
    title = _bigrams(inp.title)
    for p in s.proposals:
        if p.created_at < since:
            continue
        same_goods = goods and goods == {x.strip().lower() for x in p.products if x.strip()}
        if same_goods and labels & {f.label for f in p.facts}:
            return p
        other = _bigrams(p.title)
        if len(title & other) / len(title | other) >= SIMILAR_TITLE:
            return p
    return None


class ProposalMixin:
    """`PlanningService`가 섞어 쓴다. 서비스의 _apply·_act·_execute·clock·create_ticket·
    _sending과 _insights·_marketing·_proposal_writer를 쓴다 (`self: Any`)."""

    PROPOSAL_ACTIONS: dict[str, tuple[str, type[BaseModel]]] = {
        "make_plan": ("_make_plan", ProposalParams),
        "approve_proposal": ("_approve_proposal", ApproveProposalParams),
        "reject_proposal": ("_reject_proposal", RejectProposalParams),
        "edit_proposal": ("_edit_proposal", EditProposalParams),
        "delete_proposal": ("_delete_proposal", ProposalParams),
    }
    # 잠금 밖에서 다른 부서·LLM을 부르는 액션 (dispatch가 따로 받는다)
    OUTSIDE_LOCK = {"pull_insights", "proposal_to_request", "measure_proposal"}

    # ---- 받아오기 (data promo_concepts → 인사이트 → 제안)

    async def pull_insights(self: Any) -> ActionResult:
        if self._insights is None:
            raise ActionError(
                "INSIGHTS_OFF", "데이터분석 연결이 꺼져 있어 받아올 수 없습니다 (목 모드)"
            )
        got = await self._insights.pull(PULL_CTX)
        if isinstance(got, str):
            raise ActionError("INSIGHTS_FAILED", f"데이터분석에서 받지 못했습니다: {got}")
        created: list[str] = []
        similar: list[dict[str, str]] = []
        for inp in got:
            if _same_insight(self.state, inp.insight_id, "data") is None:
                near = similar_proposal(self.state, inp, self.clock())
                if near is not None:  # id는 새것이지만 이미 받은 제안과 같은 내용
                    similar.append({"title": inp.title, "proposal_id": near.id})
                    continue
            res = await self.receive_insight(requester="data", inp=inp)
            if res.outcome == "created":
                created.append(res.output["proposal_id"])
        out = {"created": created, "received": len(got), "similar": similar}
        if similar:
            near_ids = ", ".join(dict.fromkeys(x["proposal_id"] for x in similar))
            text = (
                f"데이터분석 받아오기: 새 제안 {len(created)}개 · 비슷한 제안이 있어 건너뜀"
                f" {len(similar)}개 ({near_ids}과 비슷)"
            )

            async def note(s: State) -> ActionResult:
                s.activity.append(
                    self._act(self.clock(), PULL_CTX, "data", "planning", "pull_insights", "A2A",
                              text)
                )  # fmt: skip
                return ActionResult(outcome="pulled", output=out)

            return await self._apply(note)
        return ActionResult(outcome="pulled", output=out)

    # ---- 받는 skill

    async def receive_insight(
        self: Any, *, requester: str, inp: ReceiveInsightInput
    ) -> ActionResult:
        frm = "marketing" if requester == "marketing" else "data"
        same = _same_insight(self.state, inp.insight_id, frm)
        if same is not None:  # 재전송: LLM을 다시 부르지 않는다
            out = {"outcome": "duplicate", "proposal_id": same.id}
            return ActionResult(changed=False, outcome="duplicate", output=out)
        draft = await ext.write(self._proposal_writer, inp, f"insight:{inp.insight_id}")

        async def run(s: State) -> ActionResult:
            dup = _same_insight(s, inp.insight_id, frm)
            if dup is not None:
                out = {"outcome": "duplicate", "proposal_id": dup.id}
                return ActionResult(outcome="duplicate", output=out)
            pid = next_proposal_id(s)
            summary = f"{frm} 인사이트 → {pid} ({inp.skill})"
            await self._execute("receive_insight", f"proposal:{pid}", pid, summary)
            t = draft.target
            target = ProposalTarget(label=t.label, value=t.value, measure=t.measure) if t else None
            p = Proposal(
                id=pid, title=inp.title, from_=frm, skill=inp.skill, insight_id=inp.insight_id,
                insight=inp.summary, facts=_facts_of(inp.facts), products=inp.products,
                audience=inp.audience, shorts_format=inp.shorts_format,
                body_md=inp.body_md, idea=draft.idea, why=draft.why, target=target,
                format=draft.format, written_by=draft.written_by, warnings=draft.warnings,
                created_at=self.clock(),
            )  # fmt: skip
            s.proposals.append(p)
            warn = f", 경고 {len(p.warnings)}" if p.warnings else ""
            text = f'{pid} "{p.title}" 제안 (근거 {len(p.facts)}개{warn})'
            act = self._act(self.clock(), _ctx(p), frm, "planning", "receive_insight", "A2A", text)
            act.grade = "A"
            s.activity.append(act)
            out = {"outcome": "created", "proposal_id": pid}
            return ActionResult(outcome="created", output=out)

        return await self._apply(run)

    async def receive_campaign_update(
        self: Any, *, inp: ReceiveCampaignUpdateInput, requester: str = "marketing"
    ) -> ActionResult:
        async def run(s: State) -> ActionResult:
            p = _find(s, inp.proposal_id)
            if p is None:
                out = {"outcome": "unknown_proposal"}
                return ActionResult(outcome="unknown_proposal", output=out)
            summary = f"{p.id} {inp.stage} ({inp.campaign_id})"
            await self._execute("receive_campaign_update", _ctx(p), p.id, summary)
            now = self.clock()
            if inp.stage == "published":
                if p.status in ("진행 중", "성과 확인"):
                    return ActionResult(outcome="duplicate", output={"outcome": "duplicate"})
                p.status = "진행 중"
                text = f"{p.id} 게시 ({inp.campaign_id})"
            else:
                facts = _facts_of(inp.facts)
                if p.result and p.result.summary == inp.summary and p.result.facts == facts:
                    return ActionResult(outcome="duplicate", output={"outcome": "duplicate"})
                p.status = "성과 확인"
                p.result = ProposalResult(
                    direction=inp.direction, summary=inp.summary or "", facts=facts, at=now
                )
                text = f"{p.id} 성과 {inp.direction} · {inp.summary}"
            upd: dict[str, Any] = {"campaign": inp.campaign_id}
            if inp.stage == "published":
                pub = {"channel": inp.channel, "published_at": inp.published_at}
                upd.update({k: v for k, v in {**pub, "post_ref": inp.post_ref}.items() if v})
            p.handoff = (p.handoff or ProposalHandoff(ok=True)).model_copy(update=upd)
            frm = "data" if requester == "data" else "marketing"
            act = self._act(now, _ctx(p), frm, "planning", "receive_campaign_update", "A2A", text)
            act.grade = "A"
            s.activity.append(act)
            return ActionResult(outcome="applied", output={"outcome": "applied"})

        return await self._apply(run)

    # ---- 사람 버튼: 기획안으로 만들기 / 폐기 / 고치기 (마케팅·개발로 넘기기는 기획안 다음 단계)

    async def _make_plan(self: Any, s: State, proposal: str) -> ActionResult | None:
        """사람 확정 → 기획안을 기획서(SPEC-n, 초안)로 만든다. 바로 넘기지 않고, 기획서 화면에서
        사람이 마케팅·개발 중 고른다 (자동 확정·자동 개발 전달 안 함, origin proposal)."""
        p = _find(s, proposal)
        if p is None or p.status != "확인 대기":
            return None
        n = _next_num([r.id for r in s.requests])
        used = {r.context_id for r in s.requests}
        c = n
        while f"ctx_req_{c:04d}" in used:
            c += 1
        req_id, ctx = f"REQ-{n}", f"ctx_req_{c:04d}"
        spec_id = f"SPEC-{_next_num([x.id for x in s.specs])}"
        paras = [Para(id="R1", kind="req", text=p.idea)]
        extra = " · ".join(x for x in [p.format, p.shorts_format, *p.products] if x)
        if extra:
            paras.append(Para(id="R2", kind="req", text=f"형식·상품: {extra}"))
        if p.target:
            measure = f" ({p.target.measure}로 잰다)" if p.target.measure else ""
            done = f"{p.target.label} {p.target.value}{measure}"
        else:
            done = "성과를 잴 기준을 정한다"
        paras.append(Para(id="C1", kind="done", text=done))
        basis = [f"{f.label} {f.value} ({f.source})" for f in p.facts]
        basis.append(f"기획 제안 {p.id} · 인사이트 {p.insight_id or '-'}")
        body = [f"> {DEPT_LABEL.get(p.from_, p.from_)} 인사이트: {p.insight}"]
        if p.audience:
            body.append(f"> 타깃: {p.audience}")
        if p.body_md:
            body += ["", p.body_md]
        evidence = " · ".join(f"{f.label} {f.value}" for f in p.facts)
        s.requests.append(
            Request(
                id=req_id, context_id=ctx, from_=p.from_, via=f"기획 제안 {p.id}", title=p.title,
                evidence=evidence, state="기획 중", spec=spec_id, source_ref=p.id,
                evidence_ids=[x for x in [p.insight_id] if x],
            )
        )  # fmt: skip
        s.specs.append(
            Spec(
                id=spec_id, title=p.title, request=req_id, status="초안",
                problem=f"{p.insight} {p.why}".strip()[:300],  # 제안서 양식에서는 "배경"
                goal=p.target and f"{p.target.label} {p.target.value}" or p.idea, paras=paras,
                basis=basis, origin=PROPOSAL_ORIGIN, body_md="\n".join(body),
                extra={k: v for k, v in (("audience", p.audience or ""),) if v},
            )
        )  # fmt: skip
        p.status = "기획안"
        p.spec = spec_id
        p.request = req_id
        text = f"{p.id} 확정 → 기획안 {spec_id} ({req_id})"
        s.activity.append(
            self._act(self.clock(), ctx, "human", "planning", "make_plan", "사람", text)
        )
        return ActionResult(output={"proposal_id": p.id, "spec_id": spec_id, "request_id": req_id})

    async def _approve_proposal(
        self: Any, s: State, proposal: str, note: str
    ) -> ActionResult | None:
        p = _find(s, proposal)
        if p is None or not _can_send(p):
            return None
        p.status = "마케팅 전달"
        p.note = note or p.note
        sp = _plan_spec(s, p)
        if sp is not None:  # 기획안은 확정하되 개발로 넘기지 않는다 (마케팅이 실행)
            sp.status = "확정"
            sp.auto_review = None
            req = next((r for r in s.requests if r.id == sp.request), None)
            if req is not None:
                req.state = "작업 중"
        if self._marketing is None:
            p.handoff = ProposalHandoff(ok=True, at=self.clock())
        text = f'{p.id} "{p.title}" 확정 → 마케팅 (근거 {len(p.facts)}개)'
        point = "receive_campaign_brief"
        act = self._act(self.clock(), _ctx(p), "planning", "marketing", point, "A2A", text)
        act.grade = "A"
        s.activity.append(act)
        if self._marketing is not None:
            self._spawn_brief(p, plan_markdown(sp) if sp else p.body_md)
        return ActionResult(output={"proposal_id": p.id})

    def _spawn_brief(self: Any, p: Proposal, body_md: str | None) -> None:
        brief = CampaignBriefInput(
            proposal_id=p.id, insight_id=p.insight_id, title=p.title, idea=p.idea, why=p.why,
            facts=_facts_in(p), target=_target_in(p), products=p.products, format=p.format,
            approved_by="사람", note=p.note, body_md=body_md,
        )  # fmt: skip
        task = asyncio.create_task(self._send_brief(p.id, brief))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def _send_brief(self: Any, pid: str, brief: CampaignBriefInput) -> None:
        out = await self._marketing.send(f"proposal:{pid}", brief, f"proposal:{pid}:brief")

        async def record(s: State) -> None:
            p = _find(s, pid)
            if p is None:
                return
            now = self.clock()
            if isinstance(out, str) or out.outcome == "rejected":
                why = out if isinstance(out, str) else f"마케팅이 거절 ({out.reason_code})"
                p.handoff = ProposalHandoff(ok=False, reason=why, at=now)
                _undo_send(s, p)  # 못 넘겼으면 기획안으로 되돌려 다시 보낼 수 있게
                text = f"{p.id} 마케팅 전달 실패 · {why} → 기획안으로 되돌림"
                s.activity.append(
                    self._act(now, _ctx(p), "marketing", "planning", "receive_campaign_brief",
                              "A2A", text)
                )  # fmt: skip
            else:
                p.handoff = ProposalHandoff(ok=True, campaign=out.campaign_id, at=now)

        await self._apply(record)

    async def _reject_proposal(
        self: Any, s: State, proposal: str, reason: str
    ) -> ActionResult | None:
        p = _find(s, proposal)
        if p is None or p.status != "확인 대기":
            return None
        p.status = "폐기"
        p.note = reason
        text = f"{p.id} 폐기 · {reason}"
        s.activity.append(
            self._act(self.clock(), _ctx(p), "human", "planning", "reject_proposal", "사람", text)
        )
        return ActionResult(output={"proposal_id": p.id})

    async def _delete_proposal(self: Any, s: State, proposal: str) -> ActionResult | None:
        """사람 버튼 "삭제": 잘못 들어온 제안을 지운다
        (예: 데이터분석이 나중에 참고용으로 바꾼 인사이트).
        기획안으로 만든 기획서·요청도 같이 지운다. 개발로 작업이 나갔으면 막는다.
        마케팅에 이미 넘긴 기획안은 마케팅 쪽 보관본이 남는다 (화면에서 안내)."""
        p = _find(s, proposal)
        if p is None:
            return None
        sp = next((x for x in s.specs if x.id == p.spec), None) if p.spec else None
        req_ids = {r.id for r in s.requests if r.source_ref == p.id or r.id == p.request}
        spec_ids = {r.spec for r in s.requests if r.id in req_ids and r.spec} | (
            {sp.id} if sp else set()
        )
        busy = any(t.spec in spec_ids for t in s.tasks)
        if busy or (sp is not None and sp.handed_to_dev):
            raise ActionError("PROPOSAL_IN_USE", f"{p.id}는 개발 작업이 나가 지울 수 없습니다")
        if sp is not None:
            s.specs = [x for x in s.specs if x.id != sp.id]
        s.requests = [r for r in s.requests if r.id not in req_ids]
        s.proposals = [x for x in s.proposals if x.id != p.id]
        gone = " · ".join(x for x in [sp.id if sp else "", *sorted(req_ids)] if x)
        text = f"{p.id} 제안 삭제" + (f" ({gone} 같이)" if gone else "")
        s.activity.append(
            self._act(self.clock(), _ctx(p), "human", "planning", "delete_proposal", "사람", text)
        )
        return ActionResult(output={"proposal_id": p.id, "deleted": True})

    async def _edit_proposal(
        self: Any,
        s: State,
        proposal: str,
        title: str,
        idea: str,
        why: str,
        target_label: str,
        target_value: str,
    ) -> ActionResult | None:
        p = _find(s, proposal)
        if p is None or p.status != "확인 대기":
            return None
        p.title = title.strip() or p.title
        p.idea = idea.strip() or p.idea
        p.why = why.strip() or p.why
        if target_label.strip() and target_value.strip():
            measure = p.target.measure if p.target else "campaign_effect"
            p.target = ProposalTarget(
                label=target_label.strip(), value=target_value.strip(), measure=measure
            )
        p.written_by = "human"
        _recheck(p)
        return ActionResult(output={"proposal_id": p.id, "warnings": p.warnings})

    async def measure_proposal(self: Any, proposal: str) -> ActionResult:
        """성과 재기: 데이터분석 campaign_effect를 부르고(insight_id·proposal_id·campaign_id 함께)
        결과를 제안에 기록한다. 기간은 `effect_window` (게시 시각부터, 최대 7일)."""
        p = _find(self.state, proposal)
        if p is None or p.handoff is None or not p.handoff.campaign:
            raise ActionError("NO_CAMPAIGN", "마케팅 캠페인 번호가 있어야 성과를 잴 수 있습니다")
        if self._insights is None:
            raise ActionError(
                "INSIGHTS_OFF", "데이터분석 연결이 꺼져 있어 잴 수 없습니다 (목 모드)"
            )
        start, end = effect_window(p.handoff, p.created_at, self.clock())
        if end <= start:
            raise ActionError("NO_WINDOW", "게시 시각이 아직 오지 않아 잴 기간이 없습니다")
        out = await self._insights.effect(
            _ctx(p), p.handoff.campaign, start, end, p.insight_id, p.id, p.products
        )
        if isinstance(out, str):
            raise ActionError("EFFECT_FAILED", f"성과를 재지 못했습니다: {out}")
        direction, summary, facts = ext.effect_result(out)
        inp = ReceiveCampaignUpdateInput(
            proposal_id=p.id, campaign_id=p.handoff.campaign, stage="result",
            direction=direction, summary=summary or direction, facts=facts,  # type: ignore[arg-type]
        )  # fmt: skip
        return await self.receive_campaign_update(inp=inp, requester="data")

    async def proposal_to_request(self: Any, proposal: str) -> ActionResult:
        """확정 → 개발: 요청 접수(create_ticket과 같은 길) → 기획서 흐름."""
        p = _find(self.state, proposal)
        if p is None or p.status not in ("확인 대기", "기획안"):
            raise ActionError(
                "PROPOSAL_NOT_OPEN", "확인 대기·기획안인 제안만 개발로 보낼 수 있습니다"
            )
        if p.spec:  # 기획안 → 그 기획서를 사람이 확정한 것으로 보고 개발로 (작업 분할·전달)

            async def confirm(s: State) -> ActionResult:
                x = _find(s, proposal)
                if x is None or x.status != "기획안" or not x.spec:
                    return ActionResult(changed=False)
                await self._confirm_spec(s, x.spec)
                x.status = "기획서로"
                return ActionResult(output={"proposal_id": proposal, "request_id": x.request})

            return await self._apply(confirm)
        evidence = " · ".join(f"{f.label} {f.value}" for f in p.facts)
        if p.target:
            evidence += f" · 목표 {p.target.label} {p.target.value}"
        res = await self.create_ticket(
            context_id=_ctx(p), requester=p.from_, title=p.title, evidence=evidence,
            source_ref=p.id, evidence_ids=[x for x in [p.insight_id] if x],
            idempotency_key=f"proposal:{p.id}:request",
        )  # fmt: skip
        req_id = res.output.get("request_id")

        async def mark(s: State) -> ActionResult:
            x = _find(s, proposal)
            if x is not None:
                x.status = "기획서로"
                x.request = req_id
            return ActionResult(output={"proposal_id": proposal, "request_id": req_id})

        return await self._apply(mark)

    async def dispatch_proposal(
        self: Any, name: str, action: dict[str, Any]
    ) -> ActionResult | None:
        """잠금 밖 액션이면 처리하고, 아니면 None (원래 dispatch가 한다)."""
        if name == "pull_insights":
            return await self.pull_insights()
        if name == "measure_proposal":
            pid = action.get("proposal")
            if not isinstance(pid, str):
                raise ActionError("INVALID_ACTION", "proposal이 없다")
            return await self.measure_proposal(pid)
        if name == "proposal_to_request":
            pid = action.get("proposal")
            if not isinstance(pid, str):
                raise ActionError("INVALID_ACTION", "proposal이 없다")
            return await self.proposal_to_request(pid)
        return None
