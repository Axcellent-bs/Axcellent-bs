"""root_cause_hypotheses skill: 모은 사실로 원인 후보를 고른다.

- 사실은 코드가 모은다(`clients/fact_sources.py`). 숫자·시각은 사실에서만 나온다.
- LLM에는 사실 목록만 주고, 후보마다 근거 사실 id를 고르게 한다.
- 출력 검증: 근거 id가 사실 목록에 없거나(또는 확인할 수 없음·이상 구간만 가리키거나),
  설명에 사실에 없는 숫자·시각이 있으면 그 후보를 버린다.
- LLM이 실패하거나 남는 후보가 없으면 시간 순서 규칙으로 대체한다(fallback=True):
  이상 직전 배포가 1순위(mid), 그 밖의 사실은 low.
- LLM에는 마감까지 남은 시간만 준다(`llm_deadline`): 요청 마감(meta.deadline) − 여유
  `DATA_ROOT_CAUSE_MARGIN_S`(기본 5초). 사실을 모은 뒤 남은 시간이 3초 미만이면 부르지 않고,
  넘기면 끊고 규칙으로 대체한다(요약에 "LLM 응답 지연"). 총괄 단계 마감(30초)보다 LLM 대기
  (60초·재시도 1번)가 길어 결과가 아예 남지 않던 것을 막는다.
- 확신도 high 후보가 없으면 "확인 필요"(needs_confirmation)다.
- 근거가 될 사실이 하나도 없으면 후보 없이 "확인할 수 없음"이다.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence, RequestMeta
from ax_contracts.schemas.data import (
    CauseFact,
    CauseHypothesis,
    RootCauseInput,
    RootCauseOutput,
)
from data_agent.clients import fact_sources
from data_agent.clients.fact_sources import RawFact, SendFn

MAX_HYPOTHESES = 3
# LLM 마감: 요청 마감(meta.deadline)에서 이만큼 앞. 응답을 만들어 돌려보낼 여유
MARGIN_ENV = "DATA_ROOT_CAUSE_MARGIN_S"
DEFAULT_MARGIN_S = 5.0
# 요청 마감이 없을 때 LLM 마감 = 지금 + 이 값
DEADLINE_ENV = "DATA_ROOT_CAUSE_DEADLINE_S"
DEFAULT_DEADLINE_S = 25.0
# 남은 시간이 이보다 짧으면 LLM을 부르지 않는다(답을 받을 수 없어서)
MIN_LLM_SECONDS = 3.0
NO_FACTS = (
    "확인할 수 없음 (원인 후보를 고를 사실이 없음: "
    "배포·공지·문의 급증·지난 사건 모두 없거나 확인할 수 없음)"
)
_RANK = {"high": 0, "mid": 1, "low": 2}
# 시각(10:05, 10:05:00)은 통째로, 나머지 숫자(날짜 2026-10-29는 2026·10·29)는 하나씩 확인한다
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::\d{2})?")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

_PROMPT = (
    "이상 지표가 생긴 원인 후보를 골라라. 데이터 블록의 facts만 근거로 쓴다.\n"
    "규칙:\n"
    "- 후보마다 근거 사실의 fact_id를 fact_ids에 하나 이상 적는다. facts에 없는 id는 쓰지 않는다.\n"
    "- explanation은 한국어 한두 문장. 근거로 고른 사실에 있는 숫자·시각(UTC)만 그대로 쓰고,"
    " 새 숫자나 시각을 만들지 않는다.\n"
    "- 원인을 단정하지 말고 '~일 수 있음'으로 쓴다.\n"
    "- confidence: 근거가 직접적이고 시간이 맞으면 high, 정황만 있으면 mid, 약하면 low.\n"
    f"- 후보는 최대 {MAX_HYPOTHESES}개. 근거가 없으면 빈 목록으로 답한다."
)


class _Candidate(BaseModel):
    explanation: str
    fact_ids: list[str]
    confidence: Literal["high", "mid", "low"]


class _Candidates(BaseModel):
    hypotheses: list[_Candidate]


@dataclass(frozen=True)
class RootCauseResult:
    output: RootCauseOutput
    evidence: Evidence
    fallback: bool


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds(env: Mapping[str, str], name: str, default: float, *, positive: bool) -> float:
    """설정값(초). 없거나 숫자가 아니거나 음수(마감은 0 이하)이면 기본값."""
    try:
        value = float(env.get(name) or default)
    except ValueError:
        return default
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        return default
    return value


def llm_deadline(
    meta: RequestMeta | object, now: datetime, env: Mapping[str, str] = os.environ
) -> datetime:
    """LLM 답을 기다릴 마지막 시각 (실제 시계 기준).

    요청 마감(meta.deadline)이 있으면 거기서 `DATA_ROOT_CAUSE_MARGIN_S`(기본 5초) 앞,
    없으면 `now` + `DATA_ROOT_CAUSE_DEADLINE_S`(기본 25초).
    """
    deadline = getattr(meta, "deadline", None)
    if deadline is None:
        wait = _seconds(env, DEADLINE_ENV, DEFAULT_DEADLINE_S, positive=True)
        return now + timedelta(seconds=wait)
    margin = _seconds(env, MARGIN_ENV, DEFAULT_MARGIN_S, positive=False)
    return deadline - timedelta(seconds=margin)


def _times(text: str) -> set[tuple[int, int]]:
    return {(int(h), int(m)) for h, m in _TIME_RE.findall(text)}


def _numbers(text: str) -> set[float]:
    return {float(n) for n in _NUMBER_RE.findall(_TIME_RE.sub(" ", text))}


def number_grounded(text: str, cited: list[CauseFact]) -> bool:
    """설명 속 숫자·시각이 모두 근거로 고른 사실에 있는지.

    시각(HH:MM)은 쪼개지 않고 통째로 비교한다.
    그래서 다른 사실 시각의 분(09:35의 35)이 "35%"를 통과시키지 않는다.
    """
    times: set[tuple[int, int]] = set()
    numbers: set[float] = set()
    for fact in cited:
        source = " ".join(p for p in (fact.summary, fact.ref, fact.at and _iso(fact.at)) if p)
        times |= _times(source)
        numbers |= _numbers(source)
    if not _times(text) <= times:
        return False
    return all(any(abs(n - a) < 1e-9 for a in numbers) for n in _numbers(text))


def _number(raw: list[RawFact]) -> list[CauseFact]:
    return [
        CauseFact(
            fact_id=f"f{i}", kind=r.kind, at=r.at, source=r.source, ref=r.ref, summary=r.summary
        )
        for i, r in enumerate(raw, start=1)
    ]


def _anomaly_fact(data: RootCauseInput) -> RawFact:
    summary = f"{data.metric} 이상 {_iso(data.window_from)} ~ {_iso(data.window_to)}"
    return RawFact("anomaly", data.window_from, "input", data.context_id, summary)


def _evidence_ids(facts: list[CauseFact]) -> set[str]:
    """후보의 근거가 될 수 있는 사실 (이상 구간 자체와 확인할 수 없음은 근거가 아니다)."""
    return {f.fact_id for f in facts if f.kind not in ("anomaly", "unavailable")}


def validate(candidates: list[_Candidate], facts: list[CauseFact]) -> list[CauseHypothesis]:
    """근거 없는 후보와 지어낸 숫자·시각이 있는 후보를 버린다."""
    known = {f.fact_id for f in facts}
    usable = _evidence_ids(facts)
    kept: list[CauseHypothesis] = []
    for c in candidates:
        ids = list(dict.fromkeys(c.fact_ids))
        explanation = c.explanation.strip()
        if not ids or not set(ids) <= known or not set(ids) & usable:
            continue
        if any(f.kind == "unavailable" for f in facts if f.fact_id in ids):
            continue
        cited = [f for f in facts if f.fact_id in ids]
        if not explanation or not number_grounded(explanation, cited):
            continue
        kept.append(CauseHypothesis(explanation=explanation, fact_ids=ids, confidence=c.confidence))
    return kept


def rule_based(facts: list[CauseFact], data: RootCauseInput) -> list[CauseHypothesis]:
    """시간 순서 규칙. 이상 시작 직전(앞 30분 안) 배포가 1순위다.

    규칙만으로는 high를 주지 않는다.
    """
    anomaly = [f.fact_id for f in facts if f.kind == "anomaly"]
    start = data.window_from
    before = sorted(
        (f for f in facts if f.kind == "deploy" and f.at and f.at <= start),
        key=lambda f: f.at or start,
        reverse=True,
    )
    during = [f for f in facts if f.kind == "deploy" and f.at and f.at > start]
    out: list[CauseHypothesis] = []
    for i, fact in enumerate(before):
        out.append(
            CauseHypothesis(
                explanation=f"이상 시작 직전 배포가 원인일 수 있음 — {fact.summary}",
                fact_ids=[fact.fact_id, *anomaly],
                confidence="mid" if i == 0 else "low",
            )
        )
    for fact in during:
        out.append(
            CauseHypothesis(
                explanation=f"이상 구간 중 배포와 관련 있을 수 있음 — {fact.summary}",
                fact_ids=[fact.fact_id, *anomaly],
                confidence="low",
            )
        )
    for fact in facts:
        if fact.kind == "notice" and fact.at and fact.at <= start:
            text = f"이상 직전 게시 중이던 공지와 관련 있을 수 있음 — {fact.summary}"
        elif fact.kind == "past_incident":
            text = f"지난 비슷한 사건과 같은 원인일 수 있음 — {fact.summary}"
        else:
            continue
        out.append(CauseHypothesis(explanation=text, fact_ids=[fact.fact_id], confidence="low"))
    return out[:MAX_HYPOTHESES]


async def _ask_llm(facts: list[CauseFact], llm: LLMClient, context_id: str) -> list[_Candidate]:
    shown = [
        {
            "fact_id": f.fact_id,
            "kind": f.kind,
            "at": _iso(f.at) if f.at else None,
            "summary": f.summary,
        }
        for f in facts
        if f.kind != "unavailable"
    ]
    result = await llm.structured(
        _PROMPT, _Candidates, untrusted={"facts": shown}, context_id=context_id
    )
    return result.hypotheses


ORDER_RULE = (
    "후보 정렬: 확신도 높은 순, 같은 확신도 안에서는 근거 사실 시각이 이상 시작과 가까운 순"
    " (LLM이 준 순서는 쓰지 않음, 시각 없는 근거는 뒤)"
)


def _distance(hypothesis: CauseHypothesis, facts: dict[str, CauseFact], start: datetime) -> float:
    """근거 사실 중 이상 시작과 가장 가까운 시각까지의 거리(초). 시각 있는 근거가 없으면 무한대."""
    times = [
        abs((facts[i].at - start).total_seconds())
        for i in hypothesis.fact_ids
        if i in facts and facts[i].at is not None and facts[i].kind != "anomaly"
    ]
    return min(times, default=float("inf"))


def order_by_proximity(
    hypotheses: list[CauseHypothesis], facts: list[CauseFact], start: datetime
) -> list[CauseHypothesis]:
    """확신도 높은 순, 같은 확신도 안에서는 이상 시작과 가까운 근거 순 (`ORDER_RULE`).

    LLM이 준 순서는 쓰지 않는다. 거리가 같으면 원래 순서를 지킨다(안정 정렬).
    """
    by_id = {f.fact_id: f for f in facts}
    return sorted(hypotheses, key=lambda h: (_RANK[h.confidence], _distance(h, by_id, start)))


def _summary(
    hypotheses: list[CauseHypothesis],
    facts: list[CauseFact],
    fallback: bool,
    late: bool = False,
) -> str:
    if not _evidence_ids(facts):
        return NO_FACTS
    usable = len(_evidence_ids(facts))
    head = f"원인 후보 {len(hypotheses)}개 (근거 사실 {usable}개)"
    if fallback:
        reason = "LLM 응답 지연으로 " if late else ""
        head += f" · {reason}시간 순서 규칙으로 고름"
    if not any(h.confidence == "high" for h in hypotheses):
        head += " · 사람 확인 필요"
    return head


async def estimate_root_cause(
    data: RootCauseInput,
    meta: RequestMeta,
    llm: LLMClient,
    *,
    send: SendFn | None = None,
    now: datetime | None = None,
) -> RootCauseResult:
    # 마감은 실제 시계로 잰다(`now`는 사실을 모을 때 쓰는 시연 시계일 수 있다)
    deadline = llm_deadline(meta, datetime.now(UTC))
    raw = await fact_sources.collect(data, meta, send=send, now=now)
    facts = _number([_anomaly_fact(data), *raw])

    fallback = False
    late = False
    llm_note = ""
    hypotheses: list[CauseHypothesis] = []
    if _evidence_ids(facts):
        left = (deadline - datetime.now(UTC)).total_seconds()
        if left < MIN_LLM_SECONDS:
            late = True
            llm_note = f"LLM 마감까지 {max(left, 0):.1f}초뿐이라 부르지 않음"
        else:
            try:
                candidates = await asyncio.wait_for(
                    _ask_llm(facts, llm, data.context_id), timeout=left
                )
                hypotheses = validate(candidates, facts)
            except TimeoutError:  # 마감을 넘김: 끊고(HTTP 연결은 LLM 클라이언트가 닫는다) 대체
                late = True
                llm_note = f"LLM이 {left:.1f}초 안에 답하지 않아 끊음"
            except Exception:  # LLM 미설정·오류·검증 실패는 규칙으로 대체한다
                hypotheses = []
        if not hypotheses:
            hypotheses = rule_based(facts, data)
            fallback = True
    hypotheses = order_by_proximity(hypotheses, facts, data.window_from)[:MAX_HYPOTHESES]

    summary = _summary(hypotheses, facts, fallback, late)
    output = RootCauseOutput(
        hypotheses=hypotheses,
        needs_confirmation=not any(h.confidence == "high" for h in hypotheses),
        facts=facts,
        summary=summary,
        method=("rule" if fallback else "llm") if hypotheses else None,
    )
    evidence = Evidence(
        queries=sorted({f.source for f in facts if f.source != "input"}),
        data_summary=f"{summary}\n{ORDER_RULE} (이상 시작 {_iso(data.window_from)})"
        + (f"\n{llm_note}" if llm_note else ""),
        collected_at=datetime.now(UTC),
        ref_ids=[f.ref for f in facts if f.ref and f.kind not in ("anomaly", "unavailable")],
    )
    return RootCauseResult(output=output, evidence=evidence, fallback=fallback)
