"""시나리오 실행. 재무 단계는 실제 skill 코드를 부르고, 다른 부서는 `대역`으로 기록한다.

실제 장부·서버·외부 API에 닿지 않는다. 숫자는 시나리오 파일의 가정에서 나온다.
흐름: 마케팅 → (재무 예산 판단) → 집행 → 데이터분석 → 기획 → 개발 → 재무 → 총괄 → 인사
"""

from __future__ import annotations

import os
import tomllib
from datetime import UTC, date, datetime
from importlib import resources
from typing import Any

from ax_contracts.schemas.finance import CampaignBudgetReviewInput, CampaignResultInput
from finance_agent import campaign as cp
from finance_agent import finance_config as fc
from finance_agent import handlers
from finance_agent.rules import load_rules
from finance_agent.sources import MockSources

SCENARIOS = {"a": "a_success.toml", "b": "b_failure.toml"}
REAL = "실제 코드"
STAND_IN = "대역"


def load_scenario(name: str) -> dict[str, Any]:
    package = resources.files("finance_agent.simulation").joinpath("scenarios", SCENARIOS[name])
    return tomllib.loads(package.read_text("utf-8"))


def funnel(round_: dict[str, Any], spend: int) -> dict[str, int]:
    """광고비 → 클릭 → 주문 → 판매액. 소수는 버린다."""
    clicks = spend // round_["cpc_krw"]
    orders = int(clicks * round_["cvr"])
    return {
        "impressions": int(clicks / round_["ctr"]),
        "clicks": clicks,
        "orders": orders,
        "gmv_krw": orders * round_["aov_krw"],
        "coupon_krw": orders * round_.get("coupon_per_order_krw", 0),
    }


class Log:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def add(
        self,
        actor: str,
        kind: str,
        received: str,
        decided: str,
        sent_to: str,
        sent: str,
        **extra: Any,
    ) -> None:
        self.steps.append(
            {
                "step": len(self.steps) + 1,
                "actor": actor,
                "kind": kind,
                "received": received,
                "decided": decided,
                "sent_to": sent_to,
                "sent": sent,
                **extra,
            }
        )


def _planning_reaction(record: dict[str, Any], numbers: dict[str, int]) -> tuple[str, str]:
    if numbers["orders"] and record["roas"] and record["roas"] >= (record["break_even_roas"] or 0):
        return "성과가 나온 소재를 다음 기획전으로 확장", "랜딩 페이지 확장 작업 요청"
    return "클릭 대비 주문이 적어 전환 원인 분석이 우선", "랜딩 전환 개선 작업 요청"


async def run_scenario(name: str) -> dict[str, Any]:
    """시나리오 하나를 처음부터 돌린다. 끝나면 재무 상태와 시각을 되돌린다."""
    scenario = load_scenario(name)
    today = date.fromisoformat(scenario["today"])
    log = Log()
    budget = load_rules().brake.monthly_budget_krw
    context = f"ctx_sim_{name}"
    previous_env = os.environ.get("AGENT_TEMPLATE_MOCK")
    previous_now, previous_state = fc.now, handlers.STATE
    os.environ["AGENT_TEMPLATE_MOCK"] = "1"
    fc.now = lambda: datetime(today.year, today.month, today.day, 3, 0, tzinfo=UTC)
    handlers.reset_state(MockSources())
    state = handlers.state()
    state.cash_override = scenario["cash_net_krw"]
    received: list[dict[str, Any]] = []

    async def capture(opinion: dict[str, Any], meta: Any) -> None:
        received.append(opinion)
        return None

    state.opinion_sender = capture
    rounds: list[dict[str, Any]] = []
    try:
        for round_ in scenario["rounds"]:
            ask = {
                "campaign_id": round_["campaign_id"],
                "campaign_name": round_["campaign_name"],
                "channel": round_["channel"],
                "requested_krw": round_["requested_krw"],
            }
            log.add(
                "마케팅",
                STAND_IN,
                "캠페인 계획",
                f"{round_['campaign_name']} 집행 필요",
                "재무",
                f"예산 요청 {fc.won(round_['requested_krw'])}",
            )
            compare = cp.compare_with_history(
                ask,
                campaigns=state.records.campaigns,
                reviews=state.records.budget_reviews,
                lessons=state.records.lessons,
                cash_net_krw=scenario["cash_net_krw"],
                today=today,
            )
            seen = len(received)
            review = await handlers.review_budget(CampaignBudgetReviewInput(**ask), context)
            decision_ko = cp.DECISION_KO[review["decision"]]
            log.add(
                "재무",
                REAL,
                f"예산 요청 {fc.won(round_['requested_krw'])}",
                f"{decision_ko}, 한도 {fc.won(review['approved_limit_krw'])}",
                "마케팅, 총괄" if review["decision"] != "approved" else "마케팅",
                "; ".join(review["reasons"]),
                applied_rules=[r["rule_id"] for r in review["applied_rules"]],
                before_history=compare["before"]["decision"],
            )
            row: dict[str, Any] = {
                "campaign_id": round_["campaign_id"],
                "campaign_name": round_["campaign_name"],
                "channel": round_["channel"],
                "requested_krw": round_["requested_krw"],
                "decision": review["decision"],
                "approved_limit_krw": review["approved_limit_krw"],
                "reasons": review["reasons"],
                "remaining_before_krw": review["remaining_budget_krw"],
                "brake": review["decision"] == "rejected",
                "compare": {
                    "before": {
                        "decision": compare["before"]["decision"],
                        "limit": compare["before"]["approved_limit_krw"],
                    },
                    "after": {
                        "decision": compare["after"]["decision"],
                        "limit": compare["after"]["approved_limit_krw"],
                    },
                    "changed": compare["changed"],
                },
                "rules": [
                    {
                        "rule_id": r["rule_id"],
                        "basis": r["basis"],
                        "detail": r["detail"] or r["title"],
                    }
                    for r in review["applied_rules"]
                ],
            }
            rounds.append(row)
            for opinion in received[seen:]:
                log.add(
                    "총괄",
                    STAND_IN,
                    f"재무 의견: {opinion['campaign_name']} {opinion['verdict']}",
                    "재무 브레이크 내용을 확인",
                    "마케팅",
                    "다음 단계 조정 지시",
                )
            if review["decision"] == "rejected":
                log.add(
                    "마케팅",
                    STAND_IN,
                    "재무 거절과 이유",
                    "집행하지 않고 계획을 다시 세움",
                    "기획",
                    "재검토 요청",
                )
                continue

            spend = review["approved_limit_krw"]
            numbers = funnel(round_, spend)
            log.add(
                "마케팅",
                STAND_IN,
                f"{decision_ko} {fc.won(spend)}",
                "그 금액으로 집행",
                "데이터분석",
                f"광고비 {fc.won(spend)} 집행 결과 분석 요청",
            )
            log.add(
                "데이터분석",
                STAND_IN,
                "집행 결과",
                (
                    f"클릭 {numbers['clicks']:,}, 주문 {numbers['orders']:,}, "
                    f"증분 판매액 {fc.won(numbers['gmv_krw'])}"
                ),
                "기획, 재무",
                "성과 데이터",
            )
            result = CampaignResultInput(
                campaign_id=round_["campaign_id"],
                campaign_name=round_["campaign_name"],
                channel=round_["channel"],
                period_from=round_["period_from"],
                period_to=round_["period_to"],
                spend_krw=spend,
                coupon_krw=numbers["coupon_krw"],
                gmv_krw=numbers["gmv_krw"],
                impressions=numbers["impressions"],
                clicks=numbers["clicks"],
                orders=numbers["orders"],
                source=f"simulation:{name}",
            )
            preview = cp.compute_result(result.model_dump())
            plan, ticket = _planning_reaction(preview, numbers)
            log.add("기획", STAND_IN, "성과 데이터", plan, "개발", ticket)
            log.add(
                "개발",
                STAND_IN,
                ticket,
                "다음 스프린트에 넣음. 이번 시뮬레이션에서 코드를 바꾸지 않음",
                "재무",
                "작업 비용 없음 (이번 손익에 넣지 않음)",
            )
            seen = len(received)
            record = await handlers.record_result(result, context)
            log.add(
                "재무",
                REAL,
                f"성과 데이터 (광고비 {fc.won(spend)}, 판매액 {fc.won(numbers['gmv_krw'])})",
                (
                    f"매출 {fc.won(record['revenue_krw'])}, 손익 {fc.won(record['profit_krw'])}, "
                    f"ROI {record['roi']:.1%}, {cp.VERDICT_KO[record['verdict']]}"
                ),
                "총괄",
                "의견 제출",
                applied_rules=[r["rule_id"] for r in record["applied_rules"]],
            )
            for opinion in received[seen:]:
                what = (
                    opinion["verdict"] if opinion["kind"] == "insight" else opinion["campaign_name"]
                )
                log.add(
                    "총괄",
                    STAND_IN,
                    f"재무 의견({opinion['kind']}): {what}",
                    "경영 판단 자료로 보관",
                    "인사",
                    "사례 기록 요청",
                )
            lesson_text = {"added": "실패 교훈 추가", "closed": "교훈 해제"}.get(
                record["lesson"], ""
            )
            log.add(
                "인사",
                STAND_IN,
                "캠페인 결과와 재무 의견",
                "실패 사례로 회사 기억에 기록"
                if record["verdict"] == "bad"
                else "사례로 회사 기억에 기록",
                "전 부서",
                lesson_text or "기록 완료",
            )
            row |= {
                "spend_krw": spend,
                "gmv_krw": numbers["gmv_krw"],
                "coupon_krw": numbers["coupon_krw"],
                "revenue_krw": record["revenue_krw"],
                "profit_krw": record["profit_krw"],
                "roi": record["roi"],
                "roas": record["roas"],
                "verdict": record["verdict"],
                "lesson": record["lesson"],
            }
        records = state.records
        spent = sum(c["spend_krw"] for c in records.campaigns.values())
        return {
            "id": scenario["id"],
            "title": scenario["title"],
            "note": "재무만 실제 코드, 다른 부서는 대역. 숫자는 가정이고 실제 장부가 아니다",
            "today": scenario["today"],
            "rules_version": load_rules().version,
            "rounds": rounds,
            "log": log.steps,
            "summary": {
                "monthly_budget_krw": budget,
                "spent_krw": spent,
                "remaining_krw": budget - spent,
                "total_profit_krw": sum(c["profit_krw"] for c in records.campaigns.values()),
                "brake_count": sum(1 for r in rounds if r["brake"]),
                "conditional_count": sum(1 for r in rounds if r["decision"] == "conditional"),
                "open_lessons": sum(1 for x in records.lessons if x["status"] == "open"),
            },
            "insights": await handlers.current_insights(),
            "opinions": received,
        }
    finally:
        fc.now = previous_now
        handlers.STATE = previous_state
        if previous_env is None:
            os.environ.pop("AGENT_TEMPLATE_MOCK", None)
        else:
            os.environ["AGENT_TEMPLATE_MOCK"] = previous_env


def _roi(row: dict[str, Any]) -> str:
    return "-" if row.get("roi") is None else f"{row['roi']:.1%}"


def _money(value: int | None) -> str:
    return "-" if value is None else fc.won(value)


def render_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "# 재무 시뮬레이션 결과",
        "",
        "재무만 실제 코드이고 다른 부서는 대역입니다. 숫자는 가정이며 실제 장부에 없습니다.",
        "가정과 출처: `backend/docs/notes/finance/simulation-assumptions.md`",
    ]
    for result in results:
        s = result["summary"]
        lines += [
            "",
            f"## 시나리오 {result['id'].upper()}: {result['title']}",
            "",
            f"- 손익 합계: **{_money(s['total_profit_krw'])}**",
            f"- 예산: {_money(s['monthly_budget_krw'])} 중 {_money(s['spent_krw'])} 사용, "
            f"{_money(s['remaining_krw'])} 남음",
            f"- 브레이크(거절): **{s['brake_count']}회**, 조건부 승인: {s['conditional_count']}회, "
            f"남은 실패 교훈: {s['open_lessons']}건",
            "",
            "| 캠페인 | 요청 | 판단 | 집행 | 판매액 | 손익 | ROI | 효율 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in result["rounds"]:
            verdict = cp.VERDICT_KO.get(r.get("verdict"), "-")
            lines.append(
                f"| {r['campaign_name']} | {_money(r['requested_krw'])} "
                f"| {cp.DECISION_KO[r['decision']]} | {_money(r.get('spend_krw'))} "
                f"| {_money(r.get('gmv_krw'))} | {_money(r.get('profit_krw'))} "
                f"| {_roi(r)} | {verdict} |"
            )
        changed = [r for r in result["rounds"] if r["compare"]["changed"]]
        if changed:
            lines += [
                "",
                "### 실패 이력 전/후 비교 (같은 요청)",
                "",
                "| 캠페인 | 실패 이력 없을 때 | 지금 |",
                "|---|---|---|",
            ]
            for r in changed:
                b, a = r["compare"]["before"], r["compare"]["after"]
                lines.append(
                    f"| {r['campaign_name']} "
                    f"| {cp.DECISION_KO[b['decision']]} {_money(b['limit'])} "
                    f"| {cp.DECISION_KO[a['decision']]} {_money(a['limit'])} |"
                )
        lines += ["", "### 판단 근거", ""]
        for r in result["rounds"]:
            lines.append(f"**{r['campaign_name']}: {cp.DECISION_KO[r['decision']]}**")
            lines.append("")
            lines += [f"- 이유: {why}" for why in r["reasons"]]
            lines += [
                f"- `{x['rule_id']}` {x['detail'] or ''} (근거: {x['basis']})" for x in r["rules"]
            ]
            lines.append("")
        if result["insights"]:
            lines += ["### 재무 인사이트", ""]
            for item in result["insights"]:
                lines.append(f"**{item['title']}**")
                lines.append("")
                lines.append(item["message"])
                lines += [f"- {e['label']}: {e['value']}" for e in item["evidence"]]
                lines.append(f"- 권고: {item['recommendation']}")
                lines.append("")
        lines += [
            "### 단계별 로그",
            "",
            "| # | 에이전트 | 구분 | 받은 것 | 판단 | 넘긴 곳 | 넘긴 것 |",
            "|---|---|---|---|---|---|---|",
        ]
        for step in result["log"]:
            cells = [
                step["step"],
                step["actor"],
                step["kind"],
                step["received"],
                step["decided"],
                step["sent_to"],
                step["sent"],
            ]
            lines.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    return "\n".join(lines) + "\n"
