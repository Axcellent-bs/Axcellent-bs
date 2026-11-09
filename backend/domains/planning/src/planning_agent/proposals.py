"""기획 제안의 바깥 연결과 기획 판단 초안 (2026-11-04, 데이터분석 재현·기획 예진 합의).

흐름: 데이터분석(인사이트 + 근거 수치) → 기획(할 일·이유·목표) → 사람 확정·폐기
      → 마케팅 실행 또는 개발(요청) → 데이터분석 성과 측정 → 기획에 기록

- 받아오기(`DataInsights.pull`): 데이터분석 `promo_concepts`(read)를 불러 초안마다 인사이트
  한 건으로 바꾼다 (데이터분석이 `receive_insight`로 보내기 전까지 쓰는 길).
  숫자는 응답 값 그대로 옮긴다.
- 판단 초안(`ProposalWriter`): LLM이 할 일·이유·목표치를 쓴다. 숫자 검사는 코드가 한다
  (`number_warnings`: 근거 facts·목표 target에 없는 숫자를 경고). LLM이 없으면 규칙 초안.
- 넘기기(`MarketingBrief.send`): 사람이 확정한 제안을 마케팅 `receive_campaign_brief`(A)에
  보낸다. 마케팅에 아직 그 skill이 없거나 응답이 없으면 지어내지 않고 실패 이유를 돌려준다.
- 목 모드 기본값(`default_*`)은 다른 부서·LLM을 부르지 않는다 (`None`).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ax_agent_template import config as template_config
from ax_agent_template.client import call_agent
from ax_agent_template.llm import LLMClient, LLMSettings
from ax_contracts.schemas import TENANT_ID_DEFAULT, AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    CampaignEffectOutput,
    CampaignProductMeasure,
    CampaignProductsEffect,
    PromoConcept,
    PromoConceptsOutput,
    TrendInsight,
    TrendInsightBriefOutput,
)
from ax_contracts.schemas.planning import (
    CampaignBriefInput,
    CampaignBriefOutput,
    InsightFact,
    InsightPast,
    ProposalTarget,
    ReceiveInsightInput,
)
from ax_workflow.hr_link import AgentCaller

TIMEOUT_SECONDS = 60.0  # promo_concepts는 LLM을 써서 느리다 (하루 캐시가 있으면 빠름)
WRITE_TIMEOUT_SECONDS = 30.0
MAX_LEN = 200


def _meta(ctx: str, tenant_id: str, timeout: float, key: str | None = None) -> RequestMeta:
    return RequestMeta(
        context_id=ctx,
        tenant_id=tenant_id,
        token="replaced-by-call_agent",
        path=[],
        deadline=datetime.now(UTC) + timedelta(seconds=timeout),
        idempotency_key=key,
    )


async def _ask(call: AgentCaller, target: str, req: AgentRequest, timeout: float):
    """다른 부서에 묻는다. 못 받으면 까닭(문자열)을 돌려준다."""
    try:
        out = await asyncio.wait_for(call(target, req), timeout)
    except TimeoutError:
        return f"{int(timeout)}초 안에 응답 없음"
    except Exception as exc:  # 서비스 꺼짐·연결 실패
        return f"호출 실패 ({type(exc).__name__})"
    if out.status != TaskStatus.COMPLETED:
        return f"{out.status.value} {out.outcome or ''}".strip()[:200]
    return out


# ---------------------------------------------------------------- 숫자 검사 (원칙 1·2·3)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _NUM.findall(text or "")}


def number_warnings(
    texts: dict[str, str],
    facts: list[InsightFact],
    target: ProposalTarget | None,
    title: str = "",
) -> list[str]:
    """글(할 일·이유·설명)에 나온 숫자가 근거(facts)·목표(target)에 없으면 경고 한 줄씩.
    데이터분석이 붙인 제목의 숫자는 데이터분석 값이라 통과시킨다."""
    allowed: set[str] = _numbers(title)
    for f in facts:
        allowed |= _numbers(f"{f.label} {f.value}")
    if target is not None:
        allowed |= _numbers(f"{target.label} {target.value}")
    out: list[str] = []
    for name, text in texts.items():
        extra = sorted(_numbers(text) - allowed)
        if extra:
            out.append(f"{name}에 근거·목표에 없는 숫자가 있음: {', '.join(extra)} (확인 필요)")
    return out


# ---------------------------------------------------------------- 기획 판단 초안

WRITE_PROMPT = """너는 고객사(온라인 서점) 사내 기획 담당 AI다.
데이터분석이 보낸 인사이트 한 건과 데이터 근거(facts)를 보고 기획 제안을 쓴다.

규칙:
- idea: 무엇을 할지 한 문장. 마케팅이 바로 만들 수 있게 구체적으로 (형식·대상·핵심 장면)
- why: 왜 해야 하는지 한두 문장. facts의 숫자를 그대로 인용하고 새 숫자를 만들지 않는다
- target: 목표치 하나 (label: 무엇을, value: 얼마나, measure: 잴 방법 campaign_effect).
  기획이 넣는 숫자는 target 하나뿐이다. 정하기 어려우면 null
- format: 숏폼 15초·숏폼 30초·배너·공지 중 하나 또는 null
- 누가 봐도 광고처럼 보이는 안은 피한다. 실존 인물·상표를 새로 끌어오지 않는다
- 존댓말 대신 "~한다" 문장, 각 문장 200자 이하
"""


class _WriteOut(BaseModel):
    idea: str = Field(min_length=1)
    why: str = Field(min_length=1)
    target: ProposalTarget | None = None
    format: str | None = None


@dataclass
class ProposalDraft:
    idea: str
    why: str
    target: ProposalTarget | None
    format: str | None
    written_by: str  # llm | rule
    warnings: list[str] = field(default_factory=list)


def rule_draft(inp: ReceiveInsightInput) -> ProposalDraft:
    """LLM이 없을 때: 인사이트 제목을 할 일로, 근거 첫 두 줄을 이유로 인용한다 (목표 없음)."""
    why = " · ".join(f"{f.label} {f.value}" for f in inp.facts[:2])
    return ProposalDraft(inp.title, why, None, None, "rule")


class ProposalWriter:
    """LLM으로 할 일·이유·목표를 쓴다. `llm`은 `structured()`를 가진 클라이언트."""

    def __init__(self, llm: LLMClient, timeout: float = WRITE_TIMEOUT_SECONDS):
        self._llm = llm
        self.timeout = timeout

    async def draft(self, inp: ReceiveInsightInput, context_id: str) -> ProposalDraft | None:
        data = {
            "title": inp.title,
            "summary": inp.summary,
            "facts": [f.model_dump() for f in inp.facts],
            "products": inp.products,
            "body_md": inp.body_md or "",
        }
        try:
            got = await asyncio.wait_for(
                self._llm.structured(
                    WRITE_PROMPT, _WriteOut, untrusted=data, context_id=context_id
                ),
                self.timeout,
            )
        except (TimeoutError, ValidationError, Exception):  # noqa: BLE001 - 규칙 초안으로 대신
            return None
        idea = " ".join(got.idea.split())[:MAX_LEN]
        why = " ".join(got.why.split())[:MAX_LEN]
        if not (idea and why):
            return None
        return ProposalDraft(idea, why, got.target, got.format, "llm")


async def write(writer: ProposalWriter | None, inp: ReceiveInsightInput, ctx: str) -> ProposalDraft:
    """초안을 쓰고 숫자를 검사한다. 본문(body_md) 숫자도 근거·목표와 대조한다 (원칙 3)."""
    d = (await writer.draft(inp, ctx) if writer else None) or rule_draft(inp)
    texts = {"할 일": d.idea, "이유": d.why, "설명": inp.body_md or ""}
    d.warnings = number_warnings(texts, inp.facts, d.target, inp.title)
    return d


# ---------------------------------------------------------------- 데이터분석에서 받아오기


def _views(n: float) -> str:
    return f"{n / 10000:.1f}만" if n >= 10000 else f"{int(n):,}"


def from_promo(out: PromoConceptsOutput) -> list[ReceiveInsightInput]:
    """promo_concepts 응답 → 인사이트. 숫자는 응답 값만 쓴다 (근거가 없으면 그 초안은 뺀다).
    데이터분석 초안의 컨셉·콘티·주의는 설명(body_md)에 참고로만 붙인다."""
    trends = {t.name: t for t in out.trends}
    seasons = [c for c in out.season_checks if c.status == "known" and c.same_month_ratio]
    items: list[ReceiveInsightInput] = []
    for c in out.concepts:
        # 회사 상품·도메인과 연결 없는 트렌드는 참고용이라 뺀다 (brief 경로와 같게)
        if c.reference_only:
            continue
        facts = _facts(c, trends, seasons)
        if not facts:
            continue
        summary = c.fit_reason or c.concept or f"재료 트렌드 {c.borrowed_trend}"
        body = ["## 데이터분석 참고 초안", f"- 컨셉: {c.concept or c.title}"]
        body += [f"- 콘티: {x}" for x in c.storyboard]
        body += [f"- 주의: {x}" for x in [*c.warnings, *c.cautions]]
        items.append(
            ReceiveInsightInput(
                # concept_id는 내용 해시(#743): 날이 바뀌어도 같은 초안은 같은 id라 카드가 안 쌓인다
                insight_id=f"promo:{c.concept_id}"[:80],
                title=c.title[:120],
                summary=summary[:400],
                skill="promo_concepts",
                facts=facts[:12],
                products=[p.name for p in c.products][:10],
                body_md="\n".join(body)[:8000],
            )
        )
    return items


def _facts(c: PromoConcept, trends: dict, seasons: list) -> list[InsightFact]:
    facts: list[InsightFact] = []
    t = trends.get(c.borrowed_trend)
    if t is not None and t.detail:
        src = "trend_popular" if t.kind == "popular" else "trend_history"
        facts.append(InsightFact(label=f"트렌드 {t.name}"[:120], value=t.detail[:80], source=src))
    if c.shorts_format and c.shorts_format_avg_views is not None:
        label = f'"{c.shorts_format}" 숏츠 평균 조회'[:120]
        views = _views(c.shorts_format_avg_views)
        facts.append(InsightFact(label=label, value=views, source="shorts_stats"))
    names = {p.name for p in c.products}
    for s in seasons:
        if s.keyword in names or any(s.keyword in n for n in names):
            label = f'"{s.keyword}" 검색 작년 같은 달'[:120]
            value = f"평균의 {s.same_month_ratio:.1f}배"
            facts.append(InsightFact(label=label, value=value, source="trend_history"))
    for p in c.products:
        if p.price is not None:
            label = f"{p.name} 판매가"[:120]
            facts.append(InsightFact(label=label, value=f"{p.price:,}원", source="shop-api"))
    return facts


class DataInsights:
    """데이터분석 `promo_concepts`를 불러 인사이트로 바꾼다."""

    def __init__(
        self,
        caller: AgentCaller | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        timeout: float = TIMEOUT_SECONDS,
    ):
        self._call = caller or (lambda target, req: call_agent(target, req))
        self.tenant_id = tenant_id
        self.timeout = timeout

    async def pull(self, ctx: str) -> list[ReceiveInsightInput] | str:
        """`trend_insight_brief`(데이터분석이 만드는 중)를 먼저 부르고, 없거나 실패하면
        `promo_concepts`로 대신한다."""
        brief = await self._brief(ctx)
        if not isinstance(brief, str):
            return brief
        meta = _meta(ctx, self.tenant_id, self.timeout)
        req = AgentRequest(meta=meta, skill="promo_concepts", input={})
        out = await _ask(self._call, "data", req, self.timeout)
        if isinstance(out, str):
            return f"인사이트 요약: {brief}\n숏츠 아이디어: {out}"
        try:
            parsed = PromoConceptsOutput.model_validate(out.output or {})
        except ValidationError:
            return "promo_concepts 출력이 계약과 다름"
        if not parsed.concepts:
            return parsed.empty_reason or "기획안 초안 없음"
        return from_promo(parsed)

    async def _brief(self, ctx: str) -> list[ReceiveInsightInput] | str:
        meta = _meta(ctx, self.tenant_id, self.timeout)
        req = AgentRequest(meta=meta, skill="trend_insight_brief", input={})
        out = await _ask(self._call, "data", req, self.timeout)
        if isinstance(out, str):
            return out
        try:
            parsed = TrendInsightBriefOutput.model_validate(out.output or {})
        except ValidationError:
            return "trend_insight_brief 출력이 계약과 다름"
        if parsed.status != "known":
            return parsed.reason or "인사이트를 확인할 수 없음"
        return from_brief(parsed) or "근거가 있는 인사이트 없음"

    async def effect(
        self,
        ctx: str,
        campaign_id: str,
        window_from: datetime,
        window_to: datetime,
        insight_id: str | None,
        proposal_id: str,
        products: list[str] | None = None,
    ) -> CampaignEffectOutput | str:
        """`campaign_effect`로 성과를 잰다. 어떤 인사이트가 잘 됐는지 이어 볼 수 있게
        insight_id·proposal_id를, 제안 관련 상품(`products`, #646)을 같이 보낸다. 데이터분석 계약에
        그 칸이 없어 거부되면 칸을 빼고 한 번 더 묻는다 (칸이 있으면 첫 요청으로 끝난다)."""
        base = {
            "campaign_id": campaign_id,
            "window_from": window_from.isoformat(),
            "window_to": window_to.isoformat(),
        }
        ids = {"insight_id": insight_id, "proposal_id": proposal_id}
        goods = {"products": products[:10]} if products else {}
        tries = [{**base, **ids, **goods}, base]
        out: Any = "부르지 않음"
        for inp in tries:
            req = AgentRequest(
                meta=_meta(ctx, self.tenant_id, self.timeout), skill="campaign_effect", input=inp
            )
            out = await _ask(self._call, "data", req, self.timeout)
            if not isinstance(out, str):
                break
        if isinstance(out, str):
            return out
        try:
            return CampaignEffectOutput.model_validate(out.output or {})
        except ValidationError:
            return "campaign_effect 출력이 계약과 다름"


def _num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def brief_facts(t: TrendInsight) -> list[InsightFact]:
    """데이터분석 인사이트 근거(구조화된 칸) → 근거 줄. 값은 응답 숫자를 옮겨 적기만 한다."""
    e = t.evidence
    link = e.links[0] if e.links else None
    src = "trend_insight_brief"
    out: list[InsightFact] = []

    def add(label: str, value: str, source: str, url: str | None = None) -> None:
        out.append(InsightFact(label=label[:120], value=value[:80], source=source, link=url))

    if e.naver_ratio is not None:
        add(f'"{t.topic}" 네이버 검색 평소 대비', f"{_num(e.naver_ratio)}배", f"{src}:naver")
    if e.google_rank is not None:
        news = next((n.url for n in e.google_news if n.url), None)
        add(f'"{t.topic}" 구글 인기 검색어', f"{e.google_rank}위", f"{src}:google", news)
    if e.youtube_videos is not None:
        add(f'"{t.topic}" 나온 YouTube 인기 영상', f"{e.youtube_videos}개", f"{src}:youtube", link)
    if e.youtube_views_per_hour is not None:
        add("그 영상들 시간당 조회 합", f"{e.youtube_views_per_hour:,.0f}회", f"{src}:youtube")
    if e.season_ratio is not None and e.season_keyword:
        add(
            f'"{e.season_keyword}" 작년 같은 시기',
            f"평균의 {_num(e.season_ratio)}배",
            f"{src}:season",
        )
    if t.target_age and t.target_age_ratio is not None:
        add(f"{t.target_age} 검색 평소 대비", f"{_num(t.target_age_ratio)}배", f"{src}:age")
    if t.shorts_format and t.shorts_format_median_views is not None:
        views = _views(t.shorts_format_median_views)
        add(f'"{t.shorts_format}" 숏츠 중앙값 조회', views, f"{src}:shorts")
    return out[:12]


def from_brief(out: TrendInsightBriefOutput) -> list[ReceiveInsightInput]:
    """trend_insight_brief 응답 → 기획 입력. 참고용·분류 대기·근거 없는 인사이트는 뺀다.
    한 줄 인사이트(line)는 데이터분석 문장 그대로,
    예시 아이디어·한계·상품 근거는 설명(body_md)에."""
    items: list[ReceiveInsightInput] = []
    for t in out.insights:
        if t.reference_only or t.classification_pending:
            continue
        facts = brief_facts(t)
        if not facts:
            continue
        body = [f"## 데이터분석 인사이트 #{t.rank}", t.line]
        if t.company_reason:
            body.append(f"- 회사 관련: {t.company_reason}")
        body += [f"- 상품 {p.name}: {p.reason}" for p in t.products]
        if t.example_ideas:
            body += ["", "## 예시 아이디어 (참고)", *[f"- {x}" for x in t.example_ideas]]
        if t.limits or out.limits:
            body += ["", "## 한계", *[f"- {x}" for x in [*t.limits, *out.limits]]]
        if t.real_entity and t.entity_names:
            body += ["", f"> 실존 이름 주의: {', '.join(t.entity_names)}"]
        items.append(
            ReceiveInsightInput(
                insight_id=t.insight_id[:80],
                title=t.topic[:120],
                summary=(t.line or t.topic)[:400],
                skill="trend_insight_brief",
                facts=facts,
                products=[p.name for p in t.products][:10],
                audience=t.target_age,
                shorts_format=t.shorts_format,
                generated_at=out.generated_at,
                body_md="\n".join(body)[:8000],
                past=[_past(r) for r in getattr(t, "past_results", [])][:3],
            )
        )
    return items


def _past(r) -> InsightPast:
    """데이터분석 InsightPastResult → 기획이 쓰는 칸만 (문장은 그대로)."""
    note = (r.note_line or "")[:300] or None
    return InsightPast(
        campaign_id=r.campaign_id[:80],
        views_label=(r.views_label or "")[:40],
        sales_label=(r.sales_label or "")[:80],
        band=str(r.band or "gray")[:20],
        summary_line=(r.summary_line or "")[:300],
        note_line=note,
        actions=[x[:300] for x in r.actions][:5],
    )


# 성과 근거의 구분 (#646). 화면이 `source`로 나눠 보여 준다. 같은 주문이 겹칠 수 있어 더하지 않는다
EFFECT_PRODUCTS = "campaign_effect · 상품 기준"
EFFECT_ATTRIBUTION = "campaign_effect · 유입 기준"  # 캠페인 링크로 들어온 주문 (#648, 직접 귀속)
EFFECT_COUPON = "campaign_effect · 쿠폰 귀속"
EFFECT_STORE = "campaign_effect · 가게 전체"


def _measure(m: CampaignProductMeasure | None, unit: str) -> str | None:
    """상품 기준 한 칸 → "+12건 (기준 30건, 범위 +5~+20)". 모르면 None."""
    if m is None or m.incremental is None or m.baseline is None:
        return None

    def n(x: float, sign: bool = False) -> str:
        return f"{x:+,.0f}" if sign else f"{x:,.0f}"

    text = f"{n(m.incremental, True)}{unit} (기준 {n(m.baseline)}{unit}"
    if m.incremental_low is not None and m.incremental_high is not None:
        text += f", 범위 {n(m.incremental_low, True)}~{n(m.incremental_high, True)}"
    return text + ")"


def _products_direction(pe: CampaignProductsEffect) -> str:
    m = pe.orders
    if pe.status != "known" or m is None or m.direction is None:
        return "확인 불가"
    if m.direction == "uncertain":  # 증분 범위가 0에 걸침 = 뚜렷한 변화 없음, 기준선 부족은 모름
        low, high = m.incremental_low, m.incremental_high
        return "같음" if low is not None and high is not None and low < high else "확인 불가"
    return {"up": "늘었음", "down": "줄었음"}[m.direction]


def effect_result(out: CampaignEffectOutput) -> tuple[str, str, list[InsightFact]]:
    """campaign_effect 응답 → (방향, 요약, 성과 근거). 숫자는 응답 값을 옮겨 적기만 한다.

    방향은 상품 기준(`products_effect`)을 쓴다. 상품을 보내지 않은 옛 제안만 가게 전체 주문으로
    본다. 유입 기준은 기준선 없는 직접 귀속이라 방향에 쓰지 않는다.
    근거는 상품 기준 · 유입 기준 · 쿠폰 귀속 · 가게 전체로 `source`를 나눠 적는다."""
    facts: list[InsightFact] = []

    def add(label: str, value: str | None, source: str) -> None:
        if value:
            facts.append(InsightFact(label=label, value=value[:80], source=source))

    pe = out.products_effect
    if pe is not None:
        direction = _products_direction(pe)
        if pe.status != "known":
            add("상품 기준 확인 불가", pe.unknown_reason or "이유 없음", EFFECT_PRODUCTS)
        add("관련 상품 주문 증분", _measure(pe.orders, "건"), EFFECT_PRODUCTS)
        add("관련 상품 판매 수량 증분", _measure(pe.units, "개"), EFFECT_PRODUCTS)
        add("관련 상품 판매액 증분", _measure(pe.gross_krw, "원"), EFFECT_PRODUCTS)
        add("못 찾은 상품", ", ".join(pe.unmatched_products), EFFECT_PRODUCTS)
    else:
        o = out.orders
        direction = {"up": "늘었음", "none": "같음", "down": "줄었음"}.get(
            o.direction or "", "확인 불가"
        )
        if o.status != "known":
            direction = "확인 불가"
    _attribution(out, add)
    c = out.coupons
    if c.status == "known" and c.use_rate is not None:
        add("캠페인 쿠폰 사용률", f"{c.use_rate * 100:.0f}%", EFFECT_COUPON)
    s = out.sales
    if s is not None and s.direct_attributed_krw is not None:
        orders = f" ({s.direct_orders}건)" if s.direct_orders is not None else ""
        add("쿠폰 귀속 판매액", f"{s.direct_attributed_krw:,}원{orders}", EFFECT_COUPON)
    o = out.orders
    if o.status == "known" and o.excess is not None and o.expected is not None:
        add("가게 전체 주문 증분", f"{o.excess:+.0f}건 (기대 {o.expected:.0f}건)", EFFECT_STORE)
    if s is not None and s.status == "known" and s.incremental_gross_krw is not None:
        add("가게 전체 판매액 증분", f"{s.incremental_gross_krw:+,}원", EFFECT_STORE)
    return direction, out.summary[:200], facts[:16]


def _attribution(out: CampaignEffectOutput, add: Callable[[str, str | None, str], None]) -> None:
    """유입 기준 근거: 캠페인 링크(`src_campaign`)로 들어온 결제 완료 주문, 출처별, 관련 상품."""
    a = out.attribution_effect
    if a is None:
        return
    if a.status != "known":
        add("유입 기준 확인 불가", a.unknown_reason or "이유 없음", EFFECT_ATTRIBUTION)
        return
    add("캠페인 링크로 온 주문", f"{a.orders:,}건 · {a.gross_krw:,}원", EFFECT_ATTRIBUTION)
    for row in a.by_source[:3]:
        add(f"출처 {row.source}", f"{row.orders:,}건 · {row.gross_krw:,}원", EFFECT_ATTRIBUTION)
    ap = out.attribution_and_products
    if ap is not None and ap.status == "known":
        add("그중 관련 상품 주문", f"{ap.orders:,}건 · {ap.gross_krw:,}원", EFFECT_ATTRIBUTION)
    if a.tagged_share is not None:
        add("유입 표시가 있는 주문 비율", f"{a.tagged_share * 100:.0f}%", EFFECT_ATTRIBUTION)


class MarketingBrief:
    """마케팅 `receive_campaign_brief`에 확정된 제안을 보낸다."""

    def __init__(
        self,
        caller: AgentCaller | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        timeout: float = 30.0,
    ):
        self._call = caller or (lambda target, req: call_agent(target, req))
        self.tenant_id = tenant_id
        self.timeout = timeout

    async def send(
        self, ctx: str, brief: CampaignBriefInput, key: str
    ) -> CampaignBriefOutput | str:
        req = AgentRequest(
            meta=_meta(ctx, self.tenant_id, self.timeout, key),
            skill="receive_campaign_brief",
            input=brief.model_dump(mode="json"),
        )
        out = await _ask(self._call, "marketing", req, self.timeout)
        if isinstance(out, str):
            return out
        try:
            return CampaignBriefOutput.model_validate(out.output or {})
        except ValidationError:
            return "receive_campaign_brief 출력이 계약과 다름"


def default_insights() -> DataInsights | None:
    return None if template_config.is_mock("planning") else DataInsights()


def default_marketing() -> MarketingBrief | None:
    return None if template_config.is_mock("planning") else MarketingBrief()


def default_writer() -> ProposalWriter | None:
    """목 모드거나 기획 LLM 설정이 없으면 None (규칙 초안)."""
    if template_config.is_mock("planning"):
        return None
    settings = LLMSettings.from_env("planning")
    if not (settings.model and settings.base_url):
        return None
    return ProposalWriter(LLMClient("planning", settings=settings))
