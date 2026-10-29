"""root_cause_hypotheses skill: 모은 사실로 원인 후보를 고른다.

- 사실은 코드가 모은다(`clients/fact_sources.py`). 숫자·시각은 사실에서만 나온다.
- LLM에는 사실 목록만 주고, 후보마다 근거 사실 id를 고르게 한다.
- 출력 검증: 근거 id가 사실 목록에 없거나(또는 확인할 수 없음·이상 구간만 가리키거나),
  설명에 사실에 없는 숫자·시각이 있으면 그 후보를 버린다.
- LLM이 실패하거나 남는 후보가 없으면 시간 순서 규칙으로 대체한다(fallback=True):
  이상 직전 배포가 1순위(mid), 그 밖의 사실은 low.
- 확신도 high 후보가 없으면 "확인 필요"(needs_confirmation)다.
- 근거가 될 사실이 하나도 없으면 후보 없이 "확인할 수 없음"이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
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


def _summary(hypotheses: list[CauseHypothesis], facts: list[CauseFact], fallback: bool) -> str:
    if not _evidence_ids(facts):
        return NO_FACTS
    usable = len(_evidence_ids(facts))
    head = f"원인 후보 {len(hypotheses)}개 (근거 사실 {usable}개)"
    if fallback:
        head += " · 시간 순서 규칙으로 고름"
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
    raw = await fact_sources.collect(data, meta, send=send, now=now)
    facts = _number([_anomaly_fact(data), *raw])

    fallback = False
    hypotheses: list[CauseHypothesis] = []
    if _evidence_ids(facts):
        try:
            hypotheses = validate(await _ask_llm(facts, llm, data.context_id), facts)
        except Exception:  # LLM 미설정·타임아웃·검증 실패는 규칙으로 대체한다
            hypotheses = []
            fallback = True
        if not hypotheses:
            hypotheses = rule_based(facts, data)
            fallback = True
    hypotheses = sorted(hypotheses, key=lambda h: _RANK[h.confidence])[:MAX_HYPOTHESES]

    summary = _summary(hypotheses, facts, fallback)
    output = RootCauseOutput(
        hypotheses=hypotheses,
        needs_confirmation=not any(h.confidence == "high" for h in hypotheses),
        facts=facts,
        summary=summary,
    )
    evidence = Evidence(
        queries=sorted({f.source for f in facts if f.source != "input"}),
        data_summary=summary,
        collected_at=datetime.now(UTC),
        ref_ids=[f.ref for f in facts if f.ref and f.kind not in ("anomaly", "unavailable")],
    )
    return RootCauseResult(output=output, evidence=evidence, fallback=fallback)
