"""데이터 분석 에이전트 정확도 평가: uv run python domains/data/eval/run.py

- 정답지 `cases/*.json`의 시나리오마다 쇼핑몰 데이터를 실제 shop-api 코드로 다시 만들고
  (`eval_world`), data의 실제 skill 코드로 돌려 정답과 비교한다.
  - 이상 탐지: `detect_anomalies` (shop-api 지표 시계열을 data의 `HttpMetricSource`로 읽는다)
  - 영향 분석: `analyze_impact` (실패 주문을 data의 `HttpShopClient`로 읽는다)
  - 사기 점수: `fraud_score` 핸들러. 판매자마다 shop-api 코드로 임시 DB를 만들고(`eval_sellers`)
    핸들러가 data의 `HttpSellerClient`로 읽는다. shop-api에 없는 사실(사업자 중복·유사 카드)은
    핸들러에서 "확인할 수 없음"이라 점수에서 빠진다(`fraud.precision`·`recall`). 빠진 규칙을
    모두 위험으로 본 상한 등급도 따로 잰다(`fraud.conservative_*`, `complete_rate`). 참고로 정답지
    `facts` 전부로 낸 규칙 점수(`metrics/fraud.score_seller`)도 라벨과 비교한다(`rule_*`).
  - 원인 추정: `estimate_root_cause` (다른 부서 응답은 주입한 원인으로 만든 가짜 A2A)
- LLM은 평가용 클라이언트(`eval_llm.ScriptedLLM`)라 CI에서도 결과가 같다.
- 결과: 표를 출력하고 `results/latest.json`에 저장한다(커밋하지 않음).
- `--write-baseline`: 지금 값을 기준(`baseline.json`, 커밋)으로 저장한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eval_llm  # noqa: E402
import eval_sellers  # noqa: E402
import eval_world  # noqa: E402
import httpx  # noqa: E402

from ax_contracts.data_access.shop_seller_prices import ShopSellerPricesQuery  # noqa: E402
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfileQuery  # noqa: E402
from ax_contracts.schemas import (  # noqa: E402
    TENANT_ID_DEFAULT,
    AgentRequest,
    AgentResult,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.data import (  # noqa: E402
    AnomalyDetectionInput,
    ImpactAnalysisInput,
    ImpactAnalysisOutput,
    RootCauseInput,
)
from ax_shop_api.lookups import Lookups  # noqa: E402
from data_agent import detect, handlers  # noqa: E402
from data_agent.clients.shop_client import (  # noqa: E402
    HttpMetricSource,
    HttpSellerClient,
    HttpShopClient,
    ShopApi,
    ShopUnavailable,
)
from data_agent.metrics.fraud import SellerFacts, low_price_ratio, score_seller  # noqa: E402
from data_agent.metrics.judge import Settings  # noqa: E402
from data_agent.skills.anomaly_detection import detect_anomalies  # noqa: E402
from data_agent.skills.impact_analysis import analyze_impact  # noqa: E402
from data_agent.skills.root_cause import estimate_root_cause  # noqa: E402

CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"  # 처음 보는 케이스 (make_cases.py --holdout). 결과 숫자만 본다
RESULTS = HERE / "results"
BASELINE = HERE / "baseline.json"
POLL_WINDOW = timedelta(minutes=15)  # 주기 감시가 묻는 구간 길이
POLL_STEP = timedelta(minutes=5)  # 주기 감시 간격
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
LOW_NOTE = "장애 쪽 실패 1건"  # low 기록의 이유에 이 문구가 있어야 한다
# 장애 종류별로 "잡았다"고 보는 지표. 결제 장애는 비율(실패율)과 건수(실패 주문 수) 어느 쪽으로
# 잡아도 된다: 5분 결제가 적으면 실패율은 판정하지 않고 실패 주문 수로 본다 (#55 다음 PR)
SIGNALS: dict[str, list[str]] = {
    "payment_fault": ["payment_fail_rate", "order_fail_count"],
    "order_drop": ["order_count"],
}

# 지표 이름 → (설명, 좋아지는 방향). 방향이 None이면 기준 비교에서 빼는 참고값이다
METRICS: dict[str, tuple[str, str | None]] = {
    "anomaly.detection_rate": (
        "이상 탐지 탐지율 (장애 시나리오 중 맞는 지표로 잡은 비율)",
        "higher",
    ),
    "anomaly.false_positive_rate": (
        "이상 탐지 오탐률 (장애 없는 시나리오 중 이상이라고 한 비율)",
        "lower",
    ),
    "anomaly.payment_mean_delay_minutes": ("결제 장애 탐지 지연 평균 (분, 5분 간격 감시)", "lower"),
    "anomaly.payment_max_delay_minutes": ("결제 장애 탐지 지연 최대 (분)", "lower"),
    "anomaly.payment_delay_from_first_fault_failure_max": (
        "결제 장애 탐지 지연 최대 (분, 장애 시작이 아니라 첫 장애 쪽 실패가 든 5분부터)",
        "lower",
    ),
    "anomaly.drop_mean_delay_minutes": ("주문 급감 탐지 지연 평균 (분)", "lower"),
    "anomaly.drop_max_delay_minutes": ("주문 급감 탐지 지연 최대 (분)", "lower"),
    "anomaly.unknown_handled_rate": ("빈 구간을 '확인할 수 없음'으로 답한 비율", "higher"),
    "anomaly.monitor_false_alarm_count": (
        "감시 재현: 정답이 이상 아님인 케이스에서 5분마다 감시가 이상을 낸 횟수",
        "lower",
    ),
    "anomaly.monitor_false_alarm_cases": (
        "감시 재현: 감시가 이상을 낸 이상 아님 케이스 수",
        "lower",
    ),
    "anomaly.low_recorded_rate": (
        "낮은 등급(low)으로 남겨야 하는 케이스 중 사건 없이 low로 남긴 비율",
        "higher",
    ),
    "impact.match_rate": (
        "영향 분석 일치율 (건수·고객·금액 모두 정답과 같음. 정답 = 구간 안 장애 쪽 실패 전부)",
        "higher",
    ),
    "impact.mean_abs_amount_error": ("영향 분석 금액 오차 평균 (원)", "lower"),
    "impact.excess_mean_abs_error": (
        "초과분 건수 오차 평균 (skill 초과분 vs 주입한 장애 실패, skill이 안 내면 확인할 수 없음)",
        "lower",
    ),
    "impact.mean_abs_amount_error_pct": (
        "영향 분석 금액 오차 평균 (%, 정답 0원 케이스 제외)",
        "lower",
    ),
    "fraud.skill_available": ("사기 점수 skill이 답하는지 (1=예, 0=미구현)", "higher"),
    "fraud.precision": ("사기 점수 skill 등급 정밀도 (high=사기)", "higher"),
    "fraud.recall": ("사기 점수 skill 등급 재현율", "higher"),
    "fraud.rule_precision": ("참고: 규칙 점수(metrics/fraud) 정밀도", "higher"),
    "fraud.rule_recall": ("참고: 규칙 점수 재현율", "higher"),
    "fraud.conservative_precision": (
        "사기 점수 skill 보수적 상한 등급 정밀도 (max_possible_level high=사기)",
        "higher",
    ),
    "fraud.conservative_recall": ("사기 점수 skill 보수적 상한 등급 재현율", "higher"),
    "fraud.complete_rate": ("사기 점수 skill 답 중 complete=true 비율 (빠진 규칙 없음)", "higher"),
    "llm.fabricated_number_rate": ("LLM 설명에 지어낸 숫자가 남은 비율 (최종 문장 기준)", "lower"),
    "llm.fallback_rate": ("설명이 템플릿으로 대체된 비율", None),
    "root_cause.top1_rate": ("원인 추정: 주입한 원인이 1순위 후보인 비율", "higher"),
    "root_cause.ungrounded_survived_rate": (
        "원인 추정: 일부러 넣은 근거 없는 후보가 남은 비율",
        "lower",
    ),
    "root_cause.needs_confirmation_rate": ("원인 추정: '확인 필요'로 끝난 비율", None),
}


# holdout(처음 보는 케이스)에서 따로 내는 지표. 이름 앞에 `holdout.`을 붙인다
HOLDOUT_METRICS = [
    "anomaly.detection_rate",
    "anomaly.false_positive_rate",
    "anomaly.monitor_false_alarm_count",
    "anomaly.low_recorded_rate",
    "impact.match_rate",
    "impact.excess_mean_abs_error",
    "anomaly.payment_mean_delay_minutes",
    "anomaly.payment_max_delay_minutes",
    "anomaly.drop_mean_delay_minutes",
    "anomaly.drop_max_delay_minutes",
]
for _name in HOLDOUT_METRICS:
    METRICS[f"holdout.{_name}"] = (f"holdout: {METRICS[_name][0]}", METRICS[_name][1])


def load_holdout() -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(HOLDOUT.glob("*.json"))]


def load_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scenarios = []
    for path in sorted(CASES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "sellers.json":
            sellers = data["sellers"]
        else:
            scenarios.append(data)
    return scenarios, sellers


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text)}


def _impact_allowed(got: ImpactAnalysisOutput, usual: float | None) -> set[float]:
    """영향 설명에 써도 되는 숫자. 실서버 영향 설명(explain)의 facts와 같은 값이어야 한다.

    실패 주문 수·영향 고객 수·피해 금액 + 초과분(skill 출력) + 평소 실패 수(소수 둘째 자리).
    평소 실패 수는 근거 문장을 읽지 않고 평가가 케이스의 지난 7일 결제 기록으로 직접 센다
    (`eval_world.usual_fault_failures`). 모르면(None) 넣지 않는다.
    """
    allowed = {
        float(got.failed_order_count),
        float(got.affected_customer_count),
        float(got.damage_amount),
    }
    if got.excess_failed_order_count is not None:
        allowed.add(float(got.excess_failed_order_count))
    if usual is not None:
        allowed.add(round(usual, 2))
    return allowed


def _fabricated(summary: str, allowed: set[float]) -> bool:
    """최종 설명 문장에 결과 숫자에 없는 숫자가 있는지 (평가 쪽에서 따로 확인한다)."""
    return any(all(abs(n - a) > 1e-6 for a in allowed) for n in _numbers(summary))


def _meta(context_id: str) -> RequestMeta:
    return RequestMeta(
        context_id=context_id,
        token="mock:orchestrator",
        path=["orchestrator"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _sources(case: dict[str, Any]):  # noqa: ANN202
    """주입한 원인으로 다른 부서의 A2A 응답을 만든다 (배포 → planning, 공지·캠페인 → marketing)."""
    cause = case["cause"]
    start = eval_world.parse(case["injection"].get("from") or case["window"]["from"])
    prs, notices = [], []
    if cause["type"] == "deploy":
        if "older_pr_number" in cause:  # planning은 병합 순서(오래된 것 먼저)로 준다
            older = start - timedelta(minutes=cause["older_deploy_minutes_before"])
            prs.append(
                {"pr_number": cause["older_pr_number"], "title": "상품 목록 문구 수정",
                 "merged_at": eval_world.iso(older)}
            )  # fmt: skip
        merged = start - timedelta(minutes=cause["minutes_before"])
        prs.append(
            {"pr_number": cause["pr_number"], "title": "결제 설정 변경",
             "merged_at": eval_world.iso(merged)}
        )  # fmt: skip
    if cause["type"] in ("notice", "campaign"):
        published = start - timedelta(minutes=cause["minutes_before"])
        notices.append(
            {"found": True, "notice_id": cause["notice_id"], "channel_code": "banner",
             "context_id": f"ctx_mkt_{cause['type']}", "published_at": eval_world.iso(published)}
        )  # fmt: skip
    outputs = {
        "find_related_prs": {"candidates": prs},
        "list_active_notices": {"items": notices, "total": len(notices)},
        "detect_inquiry_surge": {"evaluated_at": case["window"]["to"], "surges": []},
        "card_search": {"hits": []},
    }

    async def send(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=outputs[req.skill])

    return send


async def _delay(case: dict[str, Any], source: HttpMetricSource) -> float | None:
    """5분마다 최근 15분을 묻는 감시로 처음 잡힌 시각 − 장애를 넣은 시각 (분). 못 잡으면 None."""
    expected = case["expected"]["anomaly"]
    onset = eval_world.parse(expected["onset"])
    end = eval_world.parse(case["window"]["to"])
    moment = onset + POLL_STEP
    llm = eval_llm.ScriptedLLM("fail")  # 감시는 LLM을 쓰지 않는다
    while moment <= end:
        data = AnomalyDetectionInput(
            window_from=moment - POLL_WINDOW, window_to=moment, metrics=SIGNALS[case["kind"]]
        )
        result = await detect_anomalies(data, source, llm)
        if any(v.is_anomaly for v in result.output.verdicts):
            return (moment - onset).total_seconds() / 60
        moment += POLL_STEP
    return None


async def _monitor_replay(case: dict[str, Any], source: HttpMetricSource) -> list[dict[str, Any]]:
    """주기 감시(monitor.py) 재현: 구간 안에서 5분마다 최근 15분을 감시와 같은 판정 함수
    (`detect.judge_window`)와 같은 지표(`WATCHED_METRICS`)로 판정해, 이상이 나온 감시 시각을 센다.

    같은 장애가 이어져도 감시마다 한 번씩 센다(감시가 이상이라고 말한 횟수). 실제 감시의 이벤트
    중복 방지는 세지 않는다. 판정 설정은 기본값(`Settings()`)이다.
    """
    window_from = eval_world.parse(case["window"]["from"])
    end = eval_world.parse(case["window"]["to"])
    metrics = [name for name, _ in detect.WATCHED_METRICS]
    alarms: list[dict[str, Any]] = []
    moment = window_from + POLL_STEP
    while moment <= end:
        judged = await detect.judge_window(
            source, moment - POLL_WINDOW, moment, metrics, Settings()
        )
        flagged = [j.metric for j in judged.judgements if j.status == "anomaly"]
        if flagged:
            alarms.append({"at": eval_world.iso(moment), "metrics": flagged})
        moment += POLL_STEP
    return alarms


def _from_first_failure(case: dict[str, Any], delay: float | None) -> float | None:
    """감시가 잡은 시각 − 첫 장애 쪽 실패가 든 5분 시작 (분). 못 잡았거나 실패가 없으면 None."""
    expected = case["expected"]["anomaly"]
    first = expected.get("first_failure_bucket")
    if delay is None or not first or not expected.get("onset"):
        return None
    caught = eval_world.parse(expected["onset"]) + timedelta(minutes=delay)
    return (caught - eval_world.parse(first)).total_seconds() / 60


# 초과분: 장애 구간 실패 − 지난 7일 같은 시간대 평균 실패(반올림). 정답은 주입한 장애로 생긴 실패만.
# 영향 분석 skill 출력에 이 필드가 생기면 읽는다. 지금은 없어서 "확인할 수 없음"(None)이다
EXCESS_FIELD = "excess_failed_order_count"


def _excess(want: dict[str, Any], got: Any) -> dict[str, Any]:
    answer = None if want.get("unknown_expected") else want["injected_failed_order_count"]
    value = got if isinstance(got, int) else None
    return {"want": answer, "got": value, "unknown": value is None}


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    world = eval_world.build(case)
    truth = eval_world.truth(world)
    expected = case["expected"]
    drift = (
        truth.impact_failed_orders != expected["impact"]["failed_order_count"]
        or truth.impact_amount != expected["impact"]["damage_amount"]
        or truth.fault_failed_orders != expected["impact"]["injected_failed_order_count"]
    )
    window_from, window_to = world.window
    async with httpx.AsyncClient(transport=world.transport(), base_url="http://shop") as client:
        api = ShopApi("http://shop", token="eval", client=client)
        source, shop = HttpMetricSource(api), HttpShopClient(api)
        llm = eval_llm.ScriptedLLM(case["llm"])
        out: dict[str, Any] = {"id": case["id"], "kind": case["kind"], "answer_drift": drift}

        anomaly = await detect_anomalies(
            AnomalyDetectionInput(window_from=window_from, window_to=window_to), source, llm
        )
        flagged = [v.metric for v in anomaly.output.verdicts if v.is_anomaly]
        allowed = {
            float(x)
            for v in anomaly.output.verdicts
            for x in (v.observed, v.baseline, v.ratio)
            if x is not None
        }
        metric = expected["anomaly"]["metric"]
        signals = SIGNALS.get(case["kind"], [])
        status = getattr(anomaly.output, "status", None)  # 예전 계약에는 없다
        # low 기록: 잡아야 할 지표가 사건(이상) 없이 severity low로 남고 이유에 실패 1건을 적었는지
        low_recorded = any(
            v.metric in signals
            and v.severity == "low"
            and getattr(v, "status", None) in ("normal", "unknown")
            and LOW_NOTE in (getattr(v, "note", None) or "")
            for v in anomaly.output.verdicts
        )
        out["anomaly"] = {
            "expected": expected["anomaly"]["expected"],
            "metric": metric,
            "signals": signals,
            "flagged": flagged,
            "detected": any(m in flagged for m in signals) if signals else None,
            "low_expected": expected["anomaly"].get("low_expected", False),
            "low_recorded": low_recorded,
            "any_anomaly": anomaly.output.any_anomaly,
            "status": status,
            "summary": anomaly.output.summary,
            # 구간 상태가 있으면 그것으로, 없으면(예전 코드) 문장으로 본다
            "said_unknown": status == "unknown"
            if status is not None
            else "확인할 수 없음" in anomaly.output.summary,
            "llm_used": anomaly.output.any_anomaly,
            "fallback": anomaly.fallback,
            "fabricated": _fabricated(anomaly.output.summary, allowed)
            if anomaly.output.any_anomaly
            else False,
            "delay_minutes": (delay := await _delay(case, source) if metric else None),
            "delay_from_first_fault_failure": _from_first_failure(case, delay),
            "monitor_alarms": await _monitor_replay(case, source),
        }

        if case["kind"] in ("payment_fault", "empty"):
            want = expected["impact"]
            keys = ("failed_order_count", "affected_customer_count", "damage_amount")
            wanted = (
                {"unknown": True} if want.get("unknown_expected") else {k: want[k] for k in keys}
            )
            try:
                impact = await analyze_impact(
                    ImpactAnalysisInput(
                        window_from=window_from, window_to=window_to, scope="payment"
                    ),
                    TENANT_ID_DEFAULT,
                    llm,
                    shop=shop,
                )
            except ShopUnavailable as error:  # 숫자 없이 "확인할 수 없음"으로 답함
                out["impact"] = {
                    "want": wanted,
                    "got": {"unknown": True},
                    "excess": _excess(want, None),
                    "unknown_code": error.code,
                    "declined_in_window": want["declined_payments"],
                    "fallback": False,
                    "fabricated": False,
                }
            else:
                got = impact.output
                numbers = _impact_allowed(got, eval_world.usual_fault_failures(world))
                out["impact"] = {
                    "want": wanted,
                    "got": {k: getattr(got, k) for k in keys},
                    "excess": _excess(want, getattr(got, EXCESS_FIELD, None)),
                    "declined_in_window": want["declined_payments"],
                    "fallback": impact.fallback,
                    "fabricated": _fabricated(got.summary, numbers),
                }

        if case["kind"] in ("payment_fault", "order_drop", "sale"):
            injection = case["injection"]
            rc_llm = eval_llm.ScriptedLLM(case["llm"])
            data = RootCauseInput(
                context_id=f"ctx_inc_eval_{case['id']}",
                window_from=eval_world.parse(injection["from"]),
                window_to=eval_world.parse(injection["to"]),
                metric=metric or "order_count",
            )
            rc = await estimate_root_cause(
                data, _meta(data.context_id), rc_llm, send=_sources(case), now=window_to
            )
            facts = {f.fact_id: f for f in rc.output.facts}
            first = rc.output.hypotheses[0] if rc.output.hypotheses else None
            first_refs = [facts[i].ref for i in (first.fact_ids if first else []) if i in facts]
            survived = sum(
                1
                for h in rc.output.hypotheses
                if "f99" in h.fact_ids or h.explanation == eval_llm.MADE_UP_CAUSE_NUMBER
            )
            out["root_cause"] = {
                "injected": expected["root_cause"]["injected"],
                "want_ref": expected["root_cause"]["ref"],
                "first_refs": first_refs,
                "first_explanation": first.explanation if first else None,
                "top1": expected["root_cause"]["ref"] in first_refs
                if expected["root_cause"]["ref"]
                else None,
                "candidates": len(rc.output.hypotheses),
                "planted_ungrounded": rc_llm.planted_ungrounded,
                "ungrounded_survived": survived,
                "needs_confirmation": rc.output.needs_confirmation,
                "fallback": rc.fallback,
            }
    return out


def _facts_drift(db: Any, seller: dict[str, Any], now: datetime) -> list[str]:
    """만든 shop-api 데이터가 정답지 facts(shop-api에 넣을 수 있는 것)와 다른 항목."""
    lookups = Lookups(db, now=lambda: now)
    profile = lookups.seller_profile(ShopSellerProfileQuery(seller_id=seller["id"]))
    prices = lookups.seller_prices(ShopSellerPricesQuery(seller_id=seller["id"]))
    pairs = [(i.price, i.category_median_price) for i in prices.items]
    got = {
        "joined_days": (now - profile.joined_at).days,
        "listings_last_24h": profile.listings_last_24h,
        "low_price_ratio": low_price_ratio(pairs),
    }
    return [k for k, v in got.items() if abs(float(v) - float(seller["facts"][k])) > 1e-9]


async def run_sellers(sellers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    original = handlers.get_seller_client
    try:
        for seller in sellers:
            now = datetime.now(UTC)
            db = eval_sellers.build(seller, now)
            async with httpx.AsyncClient(
                transport=eval_sellers.transport(db), base_url="http://shop"
            ) as client:
                api = ShopApi("http://shop", token="eval", client=client)
                handlers.get_seller_client = lambda api=api: HttpSellerClient(api)
                request = AgentRequest(
                    meta=_meta("ctx_eval_fraud"),
                    skill="fraud_score",
                    input={"seller_id": seller["id"]},
                )
                result = await handlers.HANDLERS["fraud_score"](request)
            done = result.status == TaskStatus.COMPLETED
            output = result.output if done else {}
            rule = score_seller(SellerFacts(**seller["facts"]))
            rows.append(
                {
                    "id": seller["id"],
                    "label": seller["label"],
                    "facts_drift": _facts_drift(db, seller, now),
                    "skill_status": result.status.value,
                    "skill_outcome": result.outcome,
                    "skill_score": output.get("score"),
                    "skill_level": output.get("level"),
                    "unknown_rules": output.get("unknown_rules", []),
                    "skill_complete": output.get("complete"),
                    "skill_max_score": output.get("max_possible_score"),
                    "skill_max_level": output.get("max_possible_level"),
                    "rule_score": rule.score,
                    "rule_level": rule.level,
                }
            )
    finally:
        handlers.get_seller_client = original
    return rows


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def _precision_recall(rows: list[dict[str, Any]], key: str) -> tuple[float | None, float | None]:
    predicted = [r for r in rows if r[key] == "high"]
    actual = [r for r in rows if r["label"] == "fraud"]
    true_pos = [r for r in predicted if r["label"] == "fraud"]
    if not any(r[key] is not None for r in rows):
        return None, None
    return _rate(len(true_pos), len(predicted)), _rate(len(true_pos), len(actual))


def summarize(cases: list[dict[str, Any]], sellers: list[dict[str, Any]]) -> dict[str, Any]:
    faults = [c for c in cases if c["anomaly"]["expected"]]
    quiet = [c for c in cases if not c["anomaly"]["expected"]]
    empty = [c for c in cases if c["kind"] == "empty"]
    lows = [c for c in cases if c["anomaly"]["low_expected"]]

    def _delays(kind: str) -> list[float]:
        return [
            c["anomaly"]["delay_minutes"]
            for c in faults
            if c["kind"] == kind and c["anomaly"]["delay_minutes"] is not None
        ]

    pay_delays, drop_delays = _delays("payment_fault"), _delays("order_drop")
    first_fault_delays = [
        c["anomaly"]["delay_from_first_fault_failure"]
        for c in faults
        if c["kind"] == "payment_fault"
        and c["anomaly"]["delay_from_first_fault_failure"] is not None
    ]
    false_alarms = [c for c in quiet if c["anomaly"]["monitor_alarms"]]
    impacts = [c["impact"] for c in cases if "impact" in c]
    # 금액 오차는 정답·결과가 둘 다 숫자인 케이스만 (확인할 수 없음 케이스는 일치율로만 본다)
    numeric = [i for i in impacts if "damage_amount" in i["got"] and "damage_amount" in i["want"]]
    errors = [abs(i["got"]["damage_amount"] - i["want"]["damage_amount"]) for i in numeric]
    excess = [
        abs(i["excess"]["got"] - i["excess"]["want"])
        for i in impacts
        if i["excess"]["got"] is not None and i["excess"]["want"] is not None
    ]
    pct = [
        abs(i["got"]["damage_amount"] - i["want"]["damage_amount"])
        / i["want"]["damage_amount"]
        * 100
        for i in numeric
        if i["want"]["damage_amount"]
    ]
    explanations = [c["anomaly"] for c in cases if c["anomaly"]["llm_used"]] + impacts
    root = [c["root_cause"] for c in cases if "root_cause" in c]
    with_cause = [r for r in root if r["top1"] is not None]
    planted = sum(r["planted_ungrounded"] for r in root)
    skill_p, skill_r = _precision_recall(sellers, "skill_level")
    rule_p, rule_r = _precision_recall(sellers, "rule_level")
    safe_p, safe_r = _precision_recall(sellers, "skill_max_level")
    answered = [s for s in sellers if s["skill_status"] == "completed"]
    return {
        "anomaly.detection_rate": _rate(
            sum(1 for c in faults if c["anomaly"]["detected"]), len(faults)
        ),
        "anomaly.false_positive_rate": _rate(
            sum(1 for c in quiet if c["anomaly"]["any_anomaly"]), len(quiet)
        ),
        "anomaly.payment_mean_delay_minutes": _mean(pay_delays),
        "anomaly.payment_max_delay_minutes": max(pay_delays) if pay_delays else None,
        "anomaly.payment_delay_from_first_fault_failure_max": max(first_fault_delays)
        if first_fault_delays
        else None,
        "anomaly.monitor_false_alarm_count": sum(
            len(c["anomaly"]["monitor_alarms"]) for c in quiet
        ),
        "anomaly.monitor_false_alarm_cases": len(false_alarms),
        "anomaly.drop_mean_delay_minutes": _mean(drop_delays),
        "anomaly.drop_max_delay_minutes": max(drop_delays) if drop_delays else None,
        "anomaly.unknown_handled_rate": _rate(
            sum(1 for c in empty if c["anomaly"]["said_unknown"]), len(empty)
        ),
        "anomaly.low_recorded_rate": _rate(
            sum(1 for c in lows if c["anomaly"]["low_recorded"]), len(lows)
        ),
        "impact.match_rate": _rate(sum(1 for i in impacts if i["got"] == i["want"]), len(impacts)),
        "impact.mean_abs_amount_error": round(sum(errors) / len(errors), 1) if errors else None,
        "impact.mean_abs_amount_error_pct": round(sum(pct) / len(pct), 2) if pct else None,
        "impact.excess_mean_abs_error": _mean(excess),
        "fraud.skill_available": 1.0
        if any(s["skill_status"] == "completed" for s in sellers)
        else 0.0,
        "fraud.precision": skill_p,
        "fraud.recall": skill_r,
        "fraud.rule_precision": rule_p,
        "fraud.rule_recall": rule_r,
        "fraud.conservative_precision": safe_p,
        "fraud.conservative_recall": safe_r,
        "fraud.complete_rate": _rate(
            sum(1 for s in answered if s["skill_complete"] is True), len(answered)
        ),
        "llm.fabricated_number_rate": _rate(
            sum(1 for e in explanations if e["fabricated"]), len(explanations)
        ),
        "llm.fallback_rate": _rate(
            sum(1 for e in explanations if e["fallback"]), len(explanations)
        ),
        "root_cause.top1_rate": _rate(sum(1 for r in with_cause if r["top1"]), len(with_cause)),
        "root_cause.ungrounded_survived_rate": _rate(
            sum(r["ungrounded_survived"] for r in root), planted
        ),
        "root_cause.needs_confirmation_rate": _rate(
            sum(1 for r in root if r["needs_confirmation"]), len(root)
        ),
    }


def wrong_cases(cases: list[dict[str, Any]], sellers: list[dict[str, Any]]) -> list[dict[str, str]]:
    """틀린 케이스와 무엇이 틀렸는지. 원인 추정은 사람이 PR에 적는다(측정만)."""
    wrong: list[dict[str, str]] = []

    def add(cid: str, area: str, what: str) -> None:
        wrong.append({"id": cid, "area": area, "what": what})

    for c in cases:
        a = c["anomaly"]
        if c["answer_drift"]:
            add(c["id"], "정답지", "다시 만든 데이터가 정답과 다르다 (make_cases.py 다시 실행)")
        if a["expected"] and not a["detected"]:
            add(
                c["id"],
                "이상 탐지",
                f"{'·'.join(a['signals'])} 이상을 못 잡음 (잡은 지표: {a['flagged'] or '없음'})",
            )
        if a["low_expected"] and not a["low_recorded"]:
            add(
                c["id"],
                "이상 탐지",
                f"사건 없이 low로 '{LOW_NOTE}'을 남겨야 함 (잡은 지표: {a['flagged'] or '없음'})",
            )
        if not a["expected"] and a["monitor_alarms"]:
            times = ", ".join(x["at"][11:16] for x in a["monitor_alarms"])
            add(c["id"], "감시 재현", f"장애가 아닌데 감시가 이상을 냄: {times}")
        if not a["expected"] and a["any_anomaly"]:
            add(c["id"], "이상 탐지", f"장애가 아닌데 이상이라고 함: {', '.join(a['flagged'])}")
        if c["kind"] == "empty" and not a["said_unknown"]:
            add(c["id"], "이상 탐지", f"데이터가 없는데 '확인할 수 없음' 대신: {a['summary']}")
        if a["fabricated"]:
            add(c["id"], "LLM 설명", f"지어낸 숫자가 남음: {a['summary']}")
        if "impact" in c:
            i = c["impact"]
            if i["got"] != i["want"]:
                add(
                    c["id"],
                    "영향 분석",
                    f"정답 {i['want']} / 결과 {i['got']} "
                    f"(구간 안 카드 거절 {i['declined_in_window']}건)",
                )
            if i["fabricated"]:
                add(c["id"], "LLM 설명", "영향 분석 설명에 지어낸 숫자가 남음")
        if "root_cause" in c:
            r = c["root_cause"]
            if r["top1"] is False:
                add(
                    c["id"],
                    "원인 추정",
                    f"주입한 원인 {r['want_ref']}가 1순위가 아님 "
                    f"(1순위: {r['first_refs'] or '후보 없음'})",
                )
            if r["ungrounded_survived"]:
                add(c["id"], "원인 추정", f"근거 없는 후보 {r['ungrounded_survived']}개가 남음")
    for s in sellers:
        if s["facts_drift"]:
            add(
                s["id"],
                "정답지",
                f"만든 판매자 데이터가 facts와 다르다: {', '.join(s['facts_drift'])}",
            )
        if s["skill_status"] != "completed":
            add(
                s["id"], "사기 점수", f"skill이 답하지 않음 ({s['skill_outcome']}) — 확인할 수 없음"
            )
        elif (s["skill_level"] == "high") != (s["label"] == "fraud"):
            unknown = ", ".join(s["unknown_rules"]) or "없음"
            add(
                s["id"],
                "사기 점수",
                f"라벨 {s['label']} / skill 등급 {s['skill_level']} ({s['skill_score']}점, "
                f"확인 못 한 규칙: {unknown})",
            )
        if (s["rule_level"] == "high") != (s["label"] == "fraud"):
            add(
                s["id"],
                "사기 점수(규칙)",
                f"라벨 {s['label']} / 규칙 등급 {s['rule_level']} ({s['rule_score']}점)",
            )
    return wrong


async def evaluate() -> dict[str, Any]:
    scenarios, sellers = load_cases()
    cases = [await run_case(case) for case in scenarios]
    seller_rows = await run_sellers(sellers)
    holdout = load_holdout()
    holdout_rows = [await run_case(case) for case in holdout]
    held = summarize(holdout_rows, [])
    metrics = summarize(cases, seller_rows)
    metrics.update({f"holdout.{name}": held[name] for name in HOLDOUT_METRICS})
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "llm": "eval_llm.ScriptedLLM (평가용, 네트워크 없음)",
        "case_count": len(cases),
        "seller_count": len(seller_rows),
        "metrics": metrics,
        "wrong": wrong_cases(cases, seller_rows),
        "cases": cases,
        "sellers": seller_rows,
        "holdout": {
            "seed": holdout[0]["holdout"]["seed"] if holdout else None,
            "case_count": len(holdout_rows),
            "wrong": wrong_cases(holdout_rows, []),
            "conditions": {c["id"]: c["holdout"] for c in holdout},
            "cases": holdout_rows,
        },
    }


def compare(current: dict[str, Any], baseline: dict[str, Any], eps: float = 1e-6) -> list[str]:
    """기준보다 나빠진 지표. 기준에 값이 있는데 지금 값이 없어도 나빠진 것이다."""
    worse = []
    for name, (_, direction) in METRICS.items():
        if direction is None or name not in baseline:
            continue
        before, now = baseline[name], current.get(name)
        if before is None:
            continue
        if now is None:
            worse.append(f"{name}: 기준 {before} → 지금 확인할 수 없음")
        elif direction == "higher" and now < before - eps:
            worse.append(f"{name}: 기준 {before} → 지금 {now} (낮아짐)")
        elif direction == "lower" and now > before + eps:
            worse.append(f"{name}: 기준 {before} → 지금 {now} (높아짐)")
    return worse


def table(metrics: dict[str, Any]) -> str:
    rows = [f"{'지표':<40} {'값':>10}  설명"]
    for name, (desc, _) in METRICS.items():
        value = metrics.get(name)
        shown = "확인할 수 없음" if value is None else str(value)
        rows.append(f"{name:<40} {shown:>10}  {desc}")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-baseline", action="store_true", help="지금 값을 기준으로 저장")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # 윈도 콘솔(cp949)에서도 한국어가 깨지지 않게
    report = asyncio.run(evaluate())
    print(table(report["metrics"]))
    print(f"\n틀린 케이스 {len(report['wrong'])}개")
    for w in report["wrong"]:
        print(f"- [{w['area']}] {w['id']}: {w['what']}")
    # holdout은 결과 숫자만 본다 (내용을 보고 판정 규칙을 맞추지 않는다). 자세한 것은 결과 파일에
    held = report["holdout"]
    print(
        f"holdout {held['case_count']}개 (seed {held['seed']}) 중 틀린 항목 {len(held['wrong'])}개"
    )
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.write_baseline:
        baseline = {
            "generated_at": report["generated_at"],
            "case_count": report["case_count"],
            "metrics": report["metrics"],
        }
        BASELINE.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n기준 저장: {BASELINE}")
    elif BASELINE.exists():
        worse = compare(
            report["metrics"], json.loads(BASELINE.read_text(encoding="utf-8"))["metrics"]
        )
        print("\n기준 대비: " + ("나빠진 지표 없음" if not worse else "; ".join(worse)))


if __name__ == "__main__":
    main()
