"""기획 제안 액션 (PlanningService에 섞어 쓴다). 바깥 연결·판단 초안은 `planning_agent.proposals`.

흐름: 데이터분석 인사이트(근거 수치) → 기획 판단(할 일·이유·목표) → 사람 확정·폐기
      → 확정하면 마케팅(approve_proposal) 또는 개발(proposal_to_request) → 성과 기록

사람 버튼: 받아오기(pull_insights) · 확정→마케팅(approve_proposal)
          · 확정→개발(proposal_to_request) · 폐기(reject_proposal) · 고치기(edit_proposal)
받는 skill: receive_insight(data·marketing·finance → 제안)
          · receive_campaign_update(marketing 게시 · data 성과)

LLM 초안·다른 부서 호출(data promo_concepts, marketing receive_campaign_brief)은
상태 잠금 밖에서 한다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ax_contracts.schemas.data import TrendInsight
from ax_contracts.schemas.marketing_channels import ChannelFollowersOutput
from ax_contracts.schemas.planning import (
    CampaignBriefInput,
    InsightFact,
    ReceiveCampaignUpdateInput,
    ReceiveInsightInput,
    ReviseVideoPlanInput,
    VideoPlan,
)
from ax_contracts.schemas.planning import ProposalTarget as TargetIn
from ax_workflow.core import ActionError, ActionResult
from ax_workflow.models import (
    CompanyCharacter,
    DeletedProposal,
    HumorSituation,
    Para,
    Proposal,
    ProposalFact,
    ProposalHandoff,
    ProposalPast,
    ProposalResult,
    ProposalTarget,
    Request,
    Spec,
    State,
)
from planning_agent import idea_options, storyboard_svg, video_plan
from planning_agent import proposals as ext

log = logging.getLogger("ax.planning")
REVISE_ACTION = "revise_video_plan"  # 안전 관문 액션 (registry.toml 등록은 사람이 한다)
REVISE_MAX_VERSION = 2  # 제안당 고치기 1회 (1판 → 2판)


def character_reason(got: Any) -> str:
    """회사 캐릭터 읽기 실패(MarketingBrief.character의 이유 글·예외 종류) → 이유 코드.

    승인 관문 보류는 A2A input-required, 시트에 외형 글이 없으면 no_look, 그 밖은 read_failed."""
    text = str(got or "")
    if text.startswith("input-required"):
        return "gate_held"
    if text == ext.NO_LOOK:
        return "no_look"
    return "read_failed"
PULL_CTX = "ctx_proposals"
PULL_DONE = "pull_insights"  # 받아오기 결과 기록 (화면이 마지막 것을 보여 준다)
PULL_FAILED = "pull_insights_failed"
DEPT_LABEL = {"data": "데이터분석", "marketing": "마케팅", "finance": "재무"}
OPTION_SOURCE_TIMEOUT = (
    10.0  # 방향 선택지 출처(트렌드·팔로워)는 짧게 기다리고, 늦으면 확인할 수 없음
)
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


# 이유가 인사이트 두 글자 묶음을 이만큼 이상 담고 있으면 인사이트를 되풀이한 것으로 본다
WHY_REPEATS = 0.6


def _background(p: Proposal) -> str:
    """기획서 "배경" = 인사이트 + 이유.

    이유가 인사이트를 되풀이하면 이유만 쓴다 (같은 문장 두 번 방지)."""
    ins, why = _bigrams(p.insight), _bigrams(p.why or "")
    if ins and why and len(ins & why) / len(ins) >= WHY_REPEATS:
        return p.why.strip()[:300]
    return f"{p.insight} {p.why}".strip()[:300]


def _same_insight(s: State, ref: str, frm: str) -> Proposal | None:
    """같은 인사이트로 만든 제안 중 가장 최근 것 (후속 제안이 있으면 그것)."""
    xs = [p for p in s.proposals if p.insight_id == ref and p.from_ == frm]
    return xs[-1] if xs else None


# 후속 기획: 앞 제안이 실행됐고(진행 중·성과 확인) 데이터분석이 지난 캠페인 결과를 붙여 다시 보내면
# 중복으로 버리지 않고 같은 캠페인을 이어 가는 후속 제안을 만든다 (11/6 예진 결정: 계속하는 방향)
FOLLOW_FROM = ("진행 중", "성과 확인")


def _follow_of(s: State, same: Proposal | None, inp: ReceiveInsightInput) -> Proposal | None:
    """이어 갈 앞 제안. 이미 후속 제안이 있으면 또 만들지 않는다 (받아오기를 거듭해도 하나)."""
    if same is None or not inp.past or same.status not in FOLLOW_FROM:
        return None
    if any(p.follow_of == same.id for p in s.proposals):
        return None
    return same


# 이 기간 안에 받은 제안과 견준다 (폐기한 것 포함: 사람이 버린 걸 다시 올리지 않게)
SIMILAR_DAYS = 30
SIMILAR_TITLE = 0.8  # 제목 두 글자 묶음 겹침 비율이 이 이상이면 같은 제안으로 본다


def _bigrams(text: str) -> set[str]:
    t = "".join(ch for ch in text.lower() if ch.isalnum())
    return {t[i : i + 2] for i in range(len(t) - 1)} or {t}


# 사람이 지운 제안은 이 기간 동안 같은·비슷한 인사이트를 다시 만들지 않는다 (11/6 예진 결정)
DELETED_DAYS = 7


def _alike(inp: ReceiveInsightInput, title: str, products: list[str], labels: set[str]) -> bool:
    """같은 상품 + 근거 하나 이상 같음, 또는 제목 두 글자 묶음이 SIMILAR_TITLE 이상 겹침."""
    goods = {x.strip().lower() for x in inp.products if x.strip()}
    same_goods = goods and goods == {x.strip().lower() for x in products if x.strip()}
    if same_goods and {f.label for f in inp.facts} & labels:
        return True
    a, b = _bigrams(inp.title), _bigrams(title)
    return len(a & b) / len(a | b) >= SIMILAR_TITLE


def recently_deleted(
    s: State, inp: ReceiveInsightInput, frm: str, now: datetime
) -> DeletedProposal | None:
    """최근 DELETED_DAYS일 안에 사람이 지운 제안과 같은(같은 인사이트 id)·비슷한 인사이트."""
    since = now - timedelta(days=DELETED_DAYS)
    for x in s.deleted_proposals:
        if x.deleted_at < since or x.source != frm:
            continue
        if x.insight_id == inp.insight_id or _alike(inp, x.title, x.products, set()):
            return x
    return None


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
    OUTSIDE_LOCK = {
        "pull_insights", "proposal_to_request", "measure_proposal", "choose_angle", "make_plan",
        "redraw_storyboard",
    }  # fmt: skip

    # ---- 받아오기 (data promo_concepts → 인사이트 → 제안)

    async def pull_insights(self: Any) -> ActionResult:
        if self._insights is None:
            raise ActionError(
                "INSIGHTS_OFF", "데이터분석 연결이 꺼져 있어 받아올 수 없습니다 (목 모드)"
            )
        got, sources = await asyncio.gather(
            self._insights.pull(PULL_CTX), self._option_sources(PULL_CTX)
        )
        # 선택지(#1282) 전에 만들어진 제안도 고를 수 있게 채운다. 받아오기가 실패해도 한다
        filled = await self._fill_options(sources)
        if isinstance(got, str):
            # 실패도 기록에 남긴다: 화면이 알림 뒤에도 "마지막 받아오기 실패 · 이유"를 보여 준다
            await self._apply(self._pull_note(PULL_FAILED, f"데이터분석 받아오기 실패\n{got}"))
            raise ActionError("INSIGHTS_FAILED", f"데이터분석에서 받지 못했습니다: {got}")
        created: list[str] = []
        similar: list[dict[str, str]] = []
        deleted: list[str] = []
        for inp in got:
            if _same_insight(self.state, inp.insight_id, "data") is None:
                near = similar_proposal(self.state, inp, self.clock())
                if (
                    near is not None and _follow_of(self.state, near, inp) is not None
                ):  # 후속 기획으로 잇는다
                    res = await self.receive_insight(
                        requester="data", inp=inp, near=near.id, sources=sources
                    )
                    if res.outcome == "created":
                        created.append(res.output["proposal_id"])
                    continue
                if near is not None:  # id는 새것이지만 이미 받은 제안과 같은 내용
                    similar.append({"title": inp.title, "proposal_id": near.id})
                    continue
            res = await self.receive_insight(requester="data", inp=inp, sources=sources)
            if res.outcome == "created":
                created.append(res.output["proposal_id"])
            elif res.outcome == "deleted_recently":
                deleted.append(res.output["proposal_id"])
        out = {"created": created, "received": len(got), "similar": similar, "filled": filled}
        text = f"데이터분석 받아오기: 받은 {len(got)}개 · 새 제안 {len(created)}개"
        if similar:
            near_ids = ", ".join(dict.fromkeys(x["proposal_id"] for x in similar))
            text += f"\n비슷한 제안이 있어 건너뜀 {len(similar)}개 ({near_ids}과 비슷)"
        if deleted:
            ids = ", ".join(deleted)
            text += f"\n최근 {DELETED_DAYS}일 안에 지운 제안이라 건너뜀 {len(deleted)}개 ({ids})"
        if len(got) > len(created) + len(similar) + len(deleted):
            text += f"\n이미 받은 것 {len(got) - len(created) - len(similar) - len(deleted)}개"
        await self._apply(self._pull_note(PULL_DONE, text))
        return ActionResult(outcome="pulled", output=out)

    async def _fill_options(
        self: Any, sources: tuple[list[TrendInsight] | None, ChannelFollowersOutput | None]
    ) -> list[str]:
        """확인 대기인데 방향 선택지가 없는 제안(선택지 기능 전에 받은 것)에 선택지·규모를 채운다.
        받을 때와 같은 규칙(`idea_options`)이고 LLM을 부르지 않는다. 채운 제안 번호를 돌려준다."""
        trends, followers = sources
        filled: list[str] = []

        async def run(s: State) -> None:
            for p in s.proposals:
                if p.status != "확인 대기" or p.options:
                    continue
                scale = idea_options.scale_facts(p.past, followers)
                p.options = idea_options.build_options(p, trends, scale)
                p.scale = scale
                filled.append(p.id)
            if filled:
                text = f"방향 선택지 채움 {', '.join(filled)} (선택지 기능 전에 받은 제안)"
                s.activity.append(
                    self._act(self.clock(), PULL_CTX, "planning", "planning", "fill_options",
                              "A2A", text)
                )  # fmt: skip

        await self._apply(run)
        return filled

    def _pull_note(self: Any, point: str, text: str) -> Callable[[State], Awaitable[None]]:
        async def note(s: State) -> None:
            s.activity.append(
                self._act(self.clock(), PULL_CTX, "data", "planning", point, "A2A", text)
            )

        return note

    # ---- 받는 skill

    async def receive_insight(
        self: Any,
        *,
        requester: str,
        inp: ReceiveInsightInput,
        near: str | None = None,
        sources: tuple[list[TrendInsight] | None, ChannelFollowersOutput | None] | None = None,
    ) -> ActionResult:
        """near: 받아오기가 찾은 비슷한 앞 제안 (인사이트 id가 날마다 바뀌어도 후속으로 잇는다).
        sources: 받아오기가 한 번 받아 둔 방향 선택지 출처 (건마다 다시 부르지 않게)."""
        frm = requester if requester in ("marketing", "finance") else "data"
        if requester == "orchestrator":  # 총괄이 넘겨준 재무 의견은 재무에서 온 제안이다 (11/18)
            frm = "finance" if inp.skill == "finance_insights" else "data"
        same = _same_insight(self.state, inp.insight_id, frm) or (
            _find(self.state, near) if near else None
        )
        video_for = requester == "orchestrator" and inp.skill != "finance_insights"
        if (
            same is not None and _follow_of(self.state, same, inp) is None
        ):  # 재전송: LLM을 다시 부르지 않는다
            out = {"outcome": "duplicate", "proposal_id": same.id}
            if video_for and same.video_plan is not None:
                out["video_plan"] = same.video_plan.model_dump(mode="json")
            return ActionResult(changed=False, outcome="duplicate", output=out)
        gone = None if same is not None else recently_deleted(self.state, inp, frm, self.clock())
        if gone is not None:  # 사람이 최근 지운 제안: 일주일 동안 다시 만들지 않는다
            out = {"outcome": "deleted_recently", "proposal_id": gone.id}
            return ActionResult(changed=False, outcome="deleted_recently", output=out)
        ctx_in = f"insight:{inp.insight_id}"
        # 총괄 의뢰: 총괄 계획의 앞 단계가 넘긴 인사이트와 숫자만 쓴다. 기획이 데이터분석·마케팅에
        # 직접 묻지 않는다 (11/19 준하 님 16번). 재무 의견을 넘기는 건 영상 흐름이 아니다
        relay = requester == "orchestrator"
        if relay and sources is None:
            sources = (None, None)

        async def given() -> tuple[list[TrendInsight] | None, ChannelFollowersOutput | None]:
            assert sources is not None
            return sources

        draft, (trends, followers) = await asyncio.gather(
            ext.write(self._proposal_writer, inp, ctx_in),
            given() if sources is not None else self._option_sources(ctx_in),
        )
        scale = idea_options.scale_facts(inp.past, followers)
        # 의뢰 문장에 길이가 있으면 그 길이가 형식이다 (제작안 길이 duration_of가 형식을 읽는다)
        asked = video_plan.requested_format(inp.request_text)
        options = idea_options.build_options(inp, trends, scale)

        async def run(s: State) -> ActionResult:
            dup = _same_insight(s, inp.insight_id, frm) or (_find(s, near) if near else None)
            follow = _follow_of(s, dup, inp)
            if dup is not None and follow is None:
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
                product_ids=inp.product_ids,
                content_category=inp.content_category, content_format=inp.content_format,
                promo_style=inp.promo_style, brand_exposure=inp.brand_exposure,
                audience=inp.audience, shorts_format=video_plan.shorts_only(inp.shorts_format),
                body_md=inp.body_md, idea=draft.idea, why=draft.why, target=target,
                format=asked or draft.format, written_by=draft.written_by, warnings=draft.warnings,
                created_at=self.clock(), follow_of=follow.id if follow else None,
                past=[ProposalPast(**x.model_dump()) for x in inp.past],
                options=options, scale=scale,
                brand_character_record_id=inp.brand_character_record_id,
                request_text=inp.request_text if requester == "orchestrator" else None,
                format_ids=list(inp.format_ids),
                humor_situations=[HumorSituation(text_ko=s.text_ko, format_id=s.format_id)
                                  for s in inp.humor_situations],
            )  # fmt: skip
            s.proposals.append(p)
            warn = f", 경고 {len(p.warnings)}" if p.warnings else ""
            pick = next((o.label for o in options if o.recommended), None)
            rec = f"추천 {pick}" if pick else "추천 없음"
            text = f'{pid} "{p.title}" 제안 (근거 {len(p.facts)}개{warn}, {rec})'
            if follow is not None:
                text += f"\n후속 기획: {follow.id} 캠페인을 이어 감 (지난 결과 {len(p.past)}개)"
            act = self._act(self.clock(), _ctx(p), frm, "planning", "receive_insight", "A2A", text)
            act.grade = "A"
            s.activity.append(act)
            out = {"outcome": "created", "proposal_id": pid}
            return ActionResult(outcome="created", output=out)

        res = await self._apply(run)
        if not video_for or res.outcome != "created":
            return res
        return await self._video_for_relay(res)

    async def _video_for_relay(self: Any, res: ActionResult) -> ActionResult:
        """총괄 의뢰로 만든 제안에 영상 제작안을 쓴다 (LLM은 잠금 밖). 사람 확인 단계가 이 초안을
        보고, 기획안으로 만들 때 다시 쓰지 않고 이어 쓴다."""
        pid = res.output["proposal_id"]
        p = _find(self.state, pid)
        if p is None:
            return res
        writer = self._proposal_writer  # 테스트 가짜는 llm이 없다 → 규칙 제작안
        character, why = await self._company_character_why(p)
        if character is not None:
            p = p.model_copy(update={"company_character": character})
        video = await video_plan.write(getattr(writer, "llm", None), p, _ctx(p))
        if character is None and why:  # 11/26 U: 회사 브랜드로 쓴 이유 코드 (화면 "주인공" 줄 옆)
            video = video.model_copy(update={"character_source_reason": why})

        async def keep(s: State) -> ActionResult:
            q = _find(s, pid)
            if q is not None and q.company_character is None and character is not None:
                q.company_character = character
            if q is not None and q.video_plan is None:
                q.video_plan = video
            return ActionResult(outcome="created", output=res.output)

        await self._apply(keep)
        out = {**res.output, "video_plan": video.model_dump(mode="json")}
        return ActionResult(changed=res.changed, outcome=res.outcome, output=out)

    async def revise_video_plan(
        self: Any, requester: str, inp: ReviseVideoPlanInput
    ) -> ActionResult:
        """마케팅 요청으로 제작안 고치기 (11/26 A2A 재협업, 제안당 1회).

        같은 제안에 다음 판을 저장해 돌려준다. 이미 고친 판이 있으면(from_version이 앞 판) 그 판을
        그대로 돌려주고 LLM을 다시 부르지 않는다. 한 번 고친 판을 또 고치라면 revise_limit."""
        p = _find(self.state, inp.proposal_id)
        if p is None:
            return ActionResult(changed=False, outcome="not_found", output={})
        cur = p.video_plan
        if cur is None:
            return ActionResult(changed=False, outcome="no_video_plan", output={})
        if inp.from_version < cur.version:
            out = {"outcome": "already", "video_plan": cur.model_dump(mode="json")}
            return ActionResult(changed=False, outcome="already", output=out)
        if inp.from_version > cur.version:
            return ActionResult(changed=False, outcome="stale_version", output={})
        if cur.version >= REVISE_MAX_VERSION:
            return ActionResult(changed=False, outcome="revise_limit", output={})
        writer = self._proposal_writer
        try:
            new = await video_plan.revise(
                getattr(writer, "llm", None), p, list(inp.problems), inp.from_version, _ctx(p)
            )
        except video_plan.ReviseRefused as exc:
            return ActionResult(changed=False, outcome=f"revise_{exc.code}", output={})
        pid = p.id

        async def run(s: State) -> ActionResult:
            q = _find(s, pid)
            if q is None or q.video_plan is None or q.video_plan.version != inp.from_version:
                return ActionResult(outcome="stale_version", output={})
            summary = f"{pid} 제작안 {new.version}판 (마케팅 요청, 고칠 점 {len(inp.problems)}개)"
            await self._execute(
                REVISE_ACTION, f"proposal:{pid}", f"{pid}:v{new.version}", summary
            )
            q.video_plan = new
            n = len(inp.problems)
            text = f"{pid} 제작안 {new.version}판: 마케팅 요청으로 고침 (고칠 점 {n}개)"
            act = self._act(self.clock(), _ctx(q), requester, "planning", REVISE_ACTION, "A2A",
                            text)  # fmt: skip
            act.grade = "A"
            s.activity.append(act)
            out = {"outcome": "revised", "video_plan": new.model_dump(mode="json")}
            return ActionResult(outcome="revised", output=out)

        return await self._apply(run)

    async def _company_character(self: Any, p: Proposal) -> CompanyCharacter | None:
        """의뢰에서 고른 회사 캐릭터를 마케팅 A2A로 읽는다(11/26). 번호가 없거나 못 읽으면 None
        (회사 브랜드 기본 캐릭터, 지금 동작). 이미 읽었으면 그것."""
        return (await self._company_character_why(p))[0]

    async def _company_character_why(
        self: Any, p: Proposal
    ) -> tuple[CompanyCharacter | None, str]:
        """`_company_character`와 같고, 못 쓰면 이유 코드(계약 VideoPlan.character_source_reason)
        를 같이 돌려준다. 못 읽은 경우는 warning 한 줄(제안 번호·코드만)."""
        if p.company_character is not None:
            return p.company_character, ""
        if not p.brand_character_record_id:
            return None, "no_record_id"
        read = getattr(self._marketing, "character", None)
        if read is None:
            got: Any = "no_reader"
        else:
            try:
                got = await read(_ctx(p), p.brand_character_record_id)
            except Exception as exc:  # noqa: BLE001 - 못 읽으면 회사 브랜드로 (종류만 로그)
                got = type(exc).__name__
        if isinstance(got, CompanyCharacter):
            return got, ""
        why = character_reason(got)
        log.warning("회사 캐릭터 못 읽음 → 회사 브랜드 캐릭터: %s %s", p.id, why)
        return None, why

    async def _option_sources(
        self: Any, ctx: str
    ) -> tuple[list[TrendInsight] | None, ChannelFollowersOutput | None]:
        """방향 선택지에 쓰는 트렌드 목록(데이터분석)과 팔로워 수(마케팅).
        꺼졌거나 실패하면 None."""

        async def none() -> None:
            return None

        async def short(coro: Any) -> Any:
            try:
                return await asyncio.wait_for(coro, OPTION_SOURCE_TIMEOUT)
            except Exception:  # noqa: BLE001 - 늦거나 실패하면 확인할 수 없음
                return None

        insights, marketing = self._insights, self._marketing
        trends_f = getattr(insights, "trends", None)
        fol_f = getattr(marketing, "followers", None)
        trends, fol = await asyncio.gather(
            short(trends_f(ctx)) if trends_f else none(),
            short(fol_f(ctx)) if fol_f else none(),
        )
        return (
            trends if isinstance(trends, list) else None,
            fol if isinstance(fol, ChannelFollowersOutput) else None,
        )

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
                text = f"{p.id} 성과 {inp.direction}\n{inp.summary}"
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

    async def make_plan(self: Any, proposal: str) -> ActionResult:
        """사람 버튼 "기획안으로 만들기": 영상 제작안(LLM은 잠금 밖)을 먼저 쓰고 기획안을 만든다."""
        p = _find(self.state, proposal)
        video = None
        if p is not None and p.status == "확인 대기":
            writer = self._proposal_writer  # 테스트 가짜는 llm이 없다 → 규칙 제작안
            video = p.video_plan or await video_plan.write(  # 총괄 의뢰 때 쓴 초안은 이어 쓴다
                getattr(writer, "llm", None),
                p,
                _ctx(p),
                timeout=video_plan.HUMAN_WAIT_SECONDS,  # 사람이 버튼을 누르고 기다린다
            )
        return await self._apply(self._make_plan, proposal=proposal, video=video)

    async def redraw_storyboard(
        self: Any, proposal: str, scene: int | None = None, note: str = ""
    ) -> ActionResult:
        """대화·버튼 "콘티 다시 그리기"(11/25): 영상 제작안의 SVG 콘티를 기획 LLM으로 다시 그린다.

        scene이 있으면 그 장면만, note는 사람이 고쳐 달라고 한 말(untrusted, 데이터 블록에만).
        LLM은 잠금 밖에서 부르고, 바뀐 제작안만 잠금 안에서 넣는다. 이미 마케팅에 넘긴 제안은
        마케팅 쪽 복사본이 바뀌지 않는다(다시 넘겨야 한다)."""
        p = _find(self.state, proposal)
        if p is None:
            raise ActionError("NOT_FOUND", f"{proposal} 제안이 없어요")
        if p.video_plan is None:
            raise ActionError(
                "NO_VIDEO_PLAN", "영상 제작안이 아직 없어요 (먼저 기획안으로 만들어 주세요)"
            )
        if scene is not None and scene not in {s.n for s in p.video_plan.scenes}:
            raise ActionError("INVALID_ACTION", f"{scene}번 장면이 없어요")
        llm = getattr(self._proposal_writer, "llm", None)
        if llm is None:
            raise ActionError("LLM_NOT_CONFIGURED", "기획 LLM이 없어 콘티를 그릴 수 없어요")
        drawn, changed = await storyboard_svg.draw(llm, p.video_plan, _ctx(p), note, scene)
        if not changed:
            raise ActionError("SVG_FAILED", "콘티를 다시 그리지 못했어요 (잠시 뒤 다시 해 주세요)")

        async def put(s: State) -> ActionResult | None:
            q = _find(s, proposal)
            if q is None or q.video_plan is None:
                return None
            q.video_plan = drawn
            sent = q.status in ("마케팅 전달", "진행 중", "성과 확인")
            out = {"proposal_id": proposal, "scenes": changed, "sent": sent}
            return ActionResult(changed=True, output=out)

        return await self._apply(put)

    async def _make_plan(
        self: Any, s: State, proposal: str, video: VideoPlan | None = None
    ) -> ActionResult | None:
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
        if video is not None:
            p.video_plan = video
            body += ["", video_plan.markdown(video)]
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
                problem=_background(p),  # 제안서 양식에서는 "배경"
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
            product_ids=p.product_ids,
            content_category=p.content_category, content_format=p.content_format,
            idea_angle=p.angle, trend_refs=_trend_refs(p), video_plan=p.video_plan,
            approved_by="사람", note=p.note, body_md=body_md,
            continue_campaign=_prev_campaign(self.state, p),
        )  # fmt: skip
        # 같은 제안을 보내는 중(자동 재전송 대기 포함)이면 그건 멈추고 지금 다시 보낸다
        live: dict[str, asyncio.Task] = self.__dict__.setdefault("_brief_tasks", {})
        old = live.get(p.id)
        if old is not None and not old.done():
            old.cancel()
        task = asyncio.create_task(self._send_brief(p.id, brief))
        live[p.id] = task
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

        def forget(t: asyncio.Task, pid: str = p.id) -> None:
            if live.get(pid) is t:
                live.pop(pid, None)

        task.add_done_callback(forget)

    # 마케팅이 응답하지 않으면(연결 실패·시간 초과) 이 간격(초)으로 자동으로 다시 보낸다.
    # 마케팅이 거절하면 다시 보내지 않는다. 다 실패하면 기획안으로 되돌려 사람이 다시 보낸다.
    BRIEF_RETRY_DELAYS: tuple[float, ...] = (30, 60, 120, 300, 600)

    async def _with_finance(self: Any, pid: str, brief: CampaignBriefInput) -> CampaignBriefInput:
        """영상 기획안(콘텐츠 카테고리가 있는 제안)이면 넘기기 전에 재무에 제작비를 묻는다.

        총괄을 거치지 않고 기획이 재무에 직접 묻는다 (11/18). 재무가 답하지 않으면 재무 의견 없이
        넘기고 활동 기록에 남긴다(마케팅 전달을 막지 않는다). 이미 붙였으면 다시 묻지 않는다.
        """
        finance = getattr(self, "_finance", None)
        if finance is None or not brief.content_category:
            return brief
        if any(f.source == ext.FINANCE_ADVICE for f in brief.facts):
            return brief
        out = await finance.ask(
            f"proposal:{pid}", brief.content_category, brief.content_format, pid
        )
        facts = [] if isinstance(out, str) else ext.finance_facts(out)
        text = (
            f"{pid} 재무 제작비 묻기 실패 → 재무 의견 없이 마케팅에 넘김\n{out}"
            if isinstance(out, str)
            else f"{pid} 재무에 {brief.content_category} 제작비를 물음\n{out.summary}"
        )
        now = self.clock()

        async def record(s: State) -> None:
            p = _find(s, pid)
            if p is None:
                return
            if facts:
                p.facts = [*p.facts, *_facts_of(facts)]
            s.activity.append(
                self._act(now, _ctx(p), "planning", "finance", ext.FINANCE_ADVICE, "A2A", text)
            )

        await self._apply(record)
        return brief.model_copy(update={"facts": [*brief.facts, *facts]}) if facts else brief

    async def _send_brief(self: Any, pid: str, brief: CampaignBriefInput) -> None:
        brief = await self._with_finance(pid, brief)
        delays = self.BRIEF_RETRY_DELAYS
        for n in range(len(delays) + 1):
            if n:
                await asyncio.sleep(delays[n - 1])
                p = _find(self.state, pid)
                if p is None or p.status != "마케팅 전달" or (p.handoff and p.handoff.ok):
                    return  # 지웠거나, 사람이 되돌렸거나, 이미 받았다
            out = await self._marketing.send(f"proposal:{pid}", brief, f"proposal:{pid}:brief")
            down = isinstance(out, str)  # 응답 없음 (거절은 CampaignBriefOutput)
            last = n == len(delays)

            async def record(s: State, out: Any = out, n: int = n, last: bool = last) -> None:
                p = _find(s, pid)
                if p is None:
                    return
                now = self.clock()
                if not isinstance(out, str) and out.outcome != "rejected":
                    p.handoff = ProposalHandoff(ok=True, campaign=out.campaign_id, at=now)
                    if n:
                        text = f"{p.id} 마케팅 전달 성공 (자동 재전송 {n}번째)"
                        s.activity.append(
                            self._act(now, _ctx(p), "marketing", "planning",
                                      "receive_campaign_brief", "A2A", text)
                        )  # fmt: skip
                    return
                why = out if isinstance(out, str) else f"마케팅이 거절 ({out.reason_code})"
                if isinstance(out, str) and not last:  # 상태는 그대로 두고 잠시 뒤 다시 보낸다
                    p.handoff = ProposalHandoff(ok=False, reason=why, at=now, retry=n + 1)
                    if n == 0:
                        text = f"{p.id} 마케팅 응답 없음 → 자동으로 다시 보냄\n{why}"
                        s.activity.append(
                            self._act(now, _ctx(p), "marketing", "planning",
                                      "receive_campaign_brief", "A2A", text)
                        )  # fmt: skip
                    return
                if isinstance(out, str):
                    why = f"{why} (자동 재전송 {n}번 모두 실패)"
                p.handoff = ProposalHandoff(ok=False, reason=why, at=now)
                _undo_send(s, p)  # 못 넘겼으면 기획안으로 되돌려 다시 보낼 수 있게
                text = f"{p.id} 마케팅 전달 실패 → 기획안으로 되돌림\n{why}"
                s.activity.append(
                    self._act(now, _ctx(p), "marketing", "planning", "receive_campaign_brief",
                              "A2A", text)
                )  # fmt: skip

            await self._apply(record)
            if not down:
                return

    def resume_briefs(self: Any) -> int:
        """켤 때: 마케팅에 보내다 끊긴 제안(마케팅 전달 + 못 받음)을 다시 보낸다 (같은 멱등 키)."""
        if self._marketing is None:
            return 0
        n = 0
        for p in self.state.proposals:
            if p.status == "마케팅 전달" and p.handoff is not None and not p.handoff.ok:
                sp = _plan_spec(self.state, p)
                self._spawn_brief(p, plan_markdown(sp) if sp else p.body_md)
                n += 1
        return n

    async def _reject_proposal(
        self: Any, s: State, proposal: str, reason: str
    ) -> ActionResult | None:
        p = _find(s, proposal)
        if p is None or p.status != "확인 대기":
            return None
        p.status = "폐기"
        p.note = reason
        text = f"{p.id} 폐기\n이유: {reason}"
        s.activity.append(
            self._act(self.clock(), _ctx(p), "human", "planning", "reject_proposal", "사람", text)
        )
        return ActionResult(output={"proposal_id": p.id})

    async def _delete_proposal(self: Any, s: State, proposal: str) -> ActionResult | None:
        """사람 버튼 "삭제": 잘못 들어온 제안을 지운다
        (예: 데이터분석이 나중에 참고용으로 바꾼 인사이트).
        기획안으로 만든 기획서·요청도 같이 지운다. 마케팅에 넘겼거나(전달 성공) 개발로 작업이
        나갔으면 막는다 (다른 부서로 넘어간 기획서는 지우지 않는다. 전달 실패는 지울 수 있다)."""
        p = _find(s, proposal)
        if p is None:
            return None
        if p.handoff is not None and p.handoff.ok:
            raise ActionError("PROPOSAL_SENT", f"{p.id}는 마케팅에 넘겨 지울 수 없습니다")
        sp = next((x for x in s.specs if x.id == p.spec), None) if p.spec else None
        req_ids = {r.id for r in s.requests if r.source_ref == p.id or r.id == p.request}
        spec_ids = {r.spec for r in s.requests if r.id in req_ids and r.spec} | (
            {sp.id} if sp else set()
        )
        # 폐기한 작업(dev `discard_dev_task`)만 남았으면 쓰는 중이 아니다
        mine = [t for t in s.tasks if t.spec in spec_ids]
        dropped = bool(mine) and all(t.discarded for t in mine)
        if (mine and not dropped) or (sp is not None and sp.handed_to_dev and not dropped):
            raise ActionError("PROPOSAL_IN_USE", f"{p.id}는 개발 작업이 나가 지울 수 없습니다")
        if sp is not None:
            s.specs = [x for x in s.specs if x.id != sp.id]
        s.requests = [r for r in s.requests if r.id not in req_ids]
        s.proposals = [x for x in s.proposals if x.id != p.id]
        # 일주일 동안 같은·비슷한 인사이트를 다시 받아오지 않게 흔적을 남긴다 (지난 것은 정리)
        now = self.clock()
        keep = now - timedelta(days=DELETED_DAYS)
        s.deleted_proposals = [x for x in s.deleted_proposals if x.deleted_at >= keep]
        s.deleted_proposals.append(
            DeletedProposal(id=p.id, insight_id=p.insight_id, source=p.from_, title=p.title,
                            products=p.products, deleted_at=now)
        )  # fmt: skip
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

    async def choose_angle(
        self: Any,
        proposal: str,
        angle: str,
        *,
        category: str | None = None,
        fmt: Literal["shorts", "reels"] | None = None,
    ) -> ActionResult:
        """사람 버튼 "방향 고르기" (11/18): 고른 방향으로 할 일·이유·목표를 다시 쓰고,
        재무에 그 제안의 제작비를 한 번 묻는다(이미 붙어 있으면 묻지 않음). 기획 기록만 바꾼다.

        카테고리 없이 온 영상 제안은 이때 사람이 카테고리·형식을 정할 수 있다(태윤 11/18).
        데이터분석이 채운 카테고리는 덮지 않는다. 새로 정하면 그 카테고리로 재무에 다시 묻는다."""

        def check(s: State) -> tuple[Proposal, Any]:
            p = _find(s, proposal)
            if p is None:
                raise ActionError("NOT_FOUND", f"제안 {proposal}이 없습니다")
            if p.status != "확인 대기":
                raise ActionError(
                    "PROPOSAL_CLOSED", f"{p.id}는 확인 대기가 아니라 방향을 바꿀 수 없습니다"
                )
            opt = next((o for o in p.options if o.id == angle), None)
            if opt is None or not opt.available:
                why = opt.blocked_reason if opt else "모르는 방향"
                raise ActionError("ANGLE_BLOCKED", f"고를 수 없는 방향입니다: {why}")
            return p, opt

        p, opt = check(self.state)
        busy: set[str] = self.__dict__.setdefault("_angle_busy", set())
        if p.id in busy:  # 겹친 누름: 재무를 두 번 묻지 않게 (11/18 리뷰)
            raise ActionError(
                "ANGLE_BUSY", f"{p.id} 방향을 쓰는 중입니다. 잠시 뒤 다시 눌러 주세요"
            )
        busy.add(p.id)
        try:
            return await self._choose_angle(p, opt, category, fmt)
        finally:
            busy.discard(p.id)

    async def _choose_angle(
        self: Any,
        p: Proposal,
        opt: Any,
        category: str | None,
        fmt: Literal["shorts", "reels"] | None,
    ) -> ActionResult:
        def check(s: State) -> tuple[Proposal, Any]:
            q = _find(s, p.id)
            if q is None:
                raise ActionError("NOT_FOUND", f"제안 {p.id}이 없습니다")
            if q.status != "확인 대기":
                raise ActionError(
                    "PROPOSAL_CLOSED", f"{q.id}는 확인 대기가 아니라 방향을 바꿀 수 없습니다"
                )
            o = next((x for x in q.options if x.id == opt.id and x.available), None)
            if o is None:
                raise ActionError("ANGLE_BLOCKED", "고를 수 없는 방향입니다")
            return q, o

        ctx = _ctx(p)
        finance = getattr(self, "_finance", None)
        # 데이터분석이 채운 카테고리는 그대로, 없을 때만 사람이 고른 값을 쓴다
        new_cat = category if (category and not p.content_category) else None
        cat = p.content_category or new_cat
        cfmt = p.content_format or (fmt if new_cat else None)
        had = any(f.source == ext.FINANCE_ADVICE for f in p.facts)
        asked = finance is not None and (not had or new_cat is not None)

        async def no_advice() -> None:
            return None

        draft, advice = await asyncio.gather(
            ext.write_for_angle(self._proposal_writer, p, opt, ctx),
            finance.ask(ctx, cat, cfmt, p.id) if asked else no_advice(),
        )
        fin_facts = (
            ext.finance_facts(advice) if advice is not None and not isinstance(advice, str) else []
        )
        now = self.clock()

        async def run(s: State) -> ActionResult:
            q, o = check(s)
            t = draft.target
            q.angle = o.id
            q.idea, q.why = draft.idea, draft.why
            if t is not None:
                q.target = ProposalTarget(label=t.label, value=t.value, measure=t.measure)
            q.format = draft.format or q.format
            q.written_by = draft.written_by
            if new_cat and not q.content_category:
                q.content_category, q.content_format = new_cat, cfmt
            if fin_facts and (new_cat or not any(f.source == ext.FINANCE_ADVICE for f in q.facts)):
                kept = [f for f in q.facts if f.source != ext.FINANCE_ADVICE]
                q.facts = [*kept, *_facts_of(fin_facts)]
            q.warnings = draft.warnings
            text = f"{q.id} 방향 선택: {o.label}"
            s.activity.append(
                self._act(now, ctx, "human", "planning", "choose_angle", "사람", text)
            )
            if asked:
                note = (
                    f"{q.id} 재무 제작비 묻기 실패\n{advice}"
                    if isinstance(advice, str)
                    else f"{q.id} 재무에 제작비를 물음 (방향 고를 때)"
                )
                s.activity.append(
                    self._act(now, ctx, "planning", "finance", ext.FINANCE_ADVICE, "A2A", note)
                )
            return ActionResult(output={"proposal_id": q.id, "angle": o.id})

        return await self._apply(run)

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
        if name == "make_plan":
            pid = action.get("proposal")
            if not isinstance(pid, str):
                raise ActionError("INVALID_ACTION", "proposal이 없다")
            return await self.make_plan(pid)
        if name == "redraw_storyboard":
            pid, scene, note = action.get("proposal"), action.get("scene"), action.get("note")
            if not isinstance(pid, str):
                raise ActionError("INVALID_ACTION", "proposal이 없다")
            return await self.redraw_storyboard(
                pid,
                scene if isinstance(scene, int) and not isinstance(scene, bool) else None,
                note if isinstance(note, str) else "",
            )
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
        if name == "choose_angle":
            pid, angle = action.get("proposal"), action.get("angle")
            if not isinstance(pid, str) or not isinstance(angle, str):
                raise ActionError("INVALID_ACTION", "proposal·angle이 없다")
            cat, fmt = action.get("category"), action.get("format")
            if cat is not None and (not isinstance(cat, str) or len(cat.strip()) > 40):
                raise ActionError("INVALID_ACTION", "카테고리는 40자 이하 글자여야 합니다")
            if fmt is not None and fmt not in ("shorts", "reels"):
                raise ActionError("INVALID_ACTION", "형식은 shorts·reels 중 하나입니다")
            cf: Literal["shorts", "reels"] | None = (
                "shorts" if fmt == "shorts" else "reels" if fmt == "reels" else None
            )
            return await self.choose_angle(pid, angle, category=(cat or "").strip() or None, fmt=cf)
        return None


def _trend_refs(p: Proposal) -> list[str]:
    """유행에 올라타기를 골랐으면 올라탈 후보 이름 (마케팅 브리프, 11/18)."""
    if p.angle != "ride_trend":
        return []
    opt = next((o for o in p.options if o.id == "ride_trend"), None)
    return [r.topic for r in opt.refs][:3] if opt else []


def _prev_campaign(s: State, p: Proposal) -> str | None:
    """후속 제안이면 이어 가는 앞 제안의 캠페인 id (마케팅이 같은 캠페인을 계속하게)."""
    prev = _find(s, p.follow_of) if p.follow_of else None
    if prev is not None and prev.handoff is not None and prev.handoff.campaign:
        return prev.handoff.campaign
    return p.past[0].campaign_id if p.past else None
