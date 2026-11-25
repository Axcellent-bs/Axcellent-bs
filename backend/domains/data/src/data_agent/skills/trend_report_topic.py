"""분석 의뢰 "의뢰 주제로 찾은 근거" (11/26). 주제가 있는 의뢰만, 주제가 없으면 아무것도 안 한다.

11/25 증거: 최근 보고서 5개의 근거가 의뢰와 상관없이 거의 같았다(소설·에세이·한강·#책추천·현대카드·
쿠팡플레이). 원인(B 1단계): 빈 키워드 칸을 회사 기본 키워드가 채우고, 인스타 계정이 회사·레이더
업종 계정으로 고정이고, 근거가 배수 큰 순이고, 감지 탭(급상승 검색어) 신호를 읽지 않았다.

1. 주제 낱말 = 주제 + 주제를 띄어 쓴 낱말(2자 이상) + 주제 확장어(`related_terms`, 의뢰문 LLM이
   같은 추출 호출에서 낸 것). 연결 = 근거 이름에 주제 낱말이 들었거나 그 반대(공백·대소문자 무시)
2. 감지 탭 저장분(keyword_trend 응답 캐시, 외부 호출 없음)의 급상승 중 주제 낱말과 연결된 것을
   끌어온다. 지금 급상승이면 근거(`scan:N`), 끝난 급상승(faded)이면 참고 행 + 뺀 근거(faded)
3. 섹션 1~3 근거 중 주제 낱말과 연결 낱말이 없는 것은 결론·핵심 숫자에서 뺀다 → `excluded`
   (off_topic). 경쟁사·주제 후보 행은 주제 근거가 아니라 그대로 둔다
4. 근거 목록(`trend_report.evidence_catalog`)은 `excluded`의 id를 빼고 `scan:N`을 더한다. 그래서
   결론·재료·인사이트·기획 넘김이 같은 목록을 쓴다
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ax_contracts.schemas.data import (
    TrendExcludedEvidence,
    TrendReport,
    TrendTopicEvidenceRow,
)
from data_agent.keyword_pool import compact
from data_agent.skills.notice_ad import is_notice_ad

SCAN_SOURCE = "감지 탭에서 가져옴 · 네이버 데이터랩"
FADED_SOURCE = "결론 근거에서 뺌 · 참고로만"
SEARCH_SOURCE = "네이버 검색어 트렌드 · 최근 7일 ÷ 직전 28일"
HASHTAG_SOURCE = "인스타 의뢰 계정 캡션 해시태그"
SHORTS_SOURCE = "YouTube Data API · 제목만"
CONTENT_SOURCE = "인스타 의뢰 계정 · 터진 게시물"
MAX_SCAN = 5  # 감지 탭에서 끌어오는 급상승 수
# 주제를 띄어 쓴 낱말 중 혼자서는 주제가 아닌 말 ("시험 기간"의 "기간"이 "기간 한정 할인"에
# 붙지 않게)
GENERIC_PARTS = frozenset({"기간", "시즌", "요즘", "트렌드", "관련", "이벤트", "추천", "콘텐츠",
                           "영상", "릴스", "광고", "홍보", "마케팅", "인기", "유행"})  # fmt: skip


def topic_words(theme: str | None, related: Sequence[str]) -> list[str]:
    """주제 + 띄어 쓴 낱말(2자 이상, 일반 낱말 제외) + 확장어. 같은 말은 하나로.

    주제가 없으면 []."""
    if not theme:
        return []
    parts = [p for p in re.split(r"[\s·,/]+", theme) if len(p) >= 2 and p not in GENERIC_PARTS]
    out: list[str] = []
    for word in [theme, *parts, *related]:
        if compact(word) and compact(word) not in {compact(x) for x in out}:
            out.append(word)
    return out


def connected(text: str | None, words: Sequence[str]) -> bool:
    """근거 이름이 주제 낱말과 이어지는지 (어느 쪽이 다른 쪽에 들어가도). 빈 글은 False."""
    key = compact(text or "").lstrip("#")
    if len(key) < 2:
        return False
    return any((w := compact(x)) and (w in key or key in w) for x in words)


def _output(v: Any) -> Mapping[str, Any]:
    """응답 캐시 값([상태, 출력, …]) 또는 출력 dict."""
    if isinstance(v, list) and len(v) > 1 and isinstance(v[1], Mapping):
        return v[1]
    return v if isinstance(v, Mapping) else {}


def _times(ratio: Any) -> str:
    return f"평소의 {float(ratio):g}배" if isinstance(ratio, int | float) else "배수 모름"


def scan_hits(stored: Any, words: Sequence[str]) -> list[Mapping[str, Any]]:
    """감지 탭 급상승(rising) 중 주제 낱말과 연결된 것 (저장 순서 = 지금 급상승 먼저)."""
    rows = _output(stored).get("rising") or []
    hits = [r for r in rows if isinstance(r, Mapping) and r.get("status") == "rising"
            and connected(str(r.get("keyword") or ""), words)]  # fmt: skip
    return hits[: MAX_SCAN * 2]


def apply(report: TrendReport, scan_stored: Any = None) -> None:
    """주제가 있는 의뢰면 `topic_evidence`·`excluded`를 채운다 (근거 목록이 이 값을 읽는다).

    11/26 C: 브랜드 공지·광고 게시물(notice_ad)은 주제와 상관없이 늘 뺀다 (excluded notice_ad)."""
    report.excluded = _merge(notice_ads(report), report.excluded)
    b = report.brief
    if not b.theme:
        return
    words = topic_words(b.theme, report.related_terms)
    rows: list[TrendTopicEvidenceRow] = []
    excluded: list[TrendExcludedEvidence] = []
    # 1) 감지 탭 급상승 (주제와 연결된 것만)
    n = 0
    for r in scan_hits(scan_stored, words):
        kw = str(r.get("keyword"))
        if r.get("faded"):
            times = _times(r.get("ratio")).removeprefix("평소의 ")
            rows.append(TrendTopicEvidenceRow(kind="scan", label=f"{kw} 검색", value=times,
                                              source=FADED_SOURCE, reference=True,
                                              peak_end=r.get("peak_end")))  # fmt: skip
            excluded.append(TrendExcludedEvidence(label=f"{kw} 검색", value=times,
                                                  reason="faded"))  # fmt: skip
        elif n < MAX_SCAN:
            n += 1
            rows.append(TrendTopicEvidenceRow(kind="scan", label=f"{kw} 검색", value=_times(
                r.get("ratio")), source=SCAN_SOURCE, evidence_id=f"scan:{n}"))  # fmt: skip
    s = report.sections

    def drop(eid: str, label: str, value: str) -> None:
        excluded.append(TrendExcludedEvidence(label=label, value=value, reason="off_topic",
                                              evidence_id=eid))  # fmt: skip

    # 2) 검색·쇼핑 (키워드 칸에 주제·확장어가 들어가 있다)
    for i, r in enumerate(s.search.rows, start=1):
        on = connected(r.keyword, words)
        for kind, pct in (("search", r.search_change_pct), ("shopping", r.shopping_change_pct)):
            if pct is None:
                continue
            what = "검색" if kind == "search" else "쇼핑 클릭"
            if on:
                rows.append(TrendTopicEvidenceRow(kind=kind, label=f"{r.keyword} {what}",  # type: ignore[arg-type]
                                                  value=f"{pct:+.0f}%", source=SEARCH_SOURCE,
                                                  evidence_id=f"{kind}:{i}"))  # fmt: skip
            else:
                drop(f"{kind}:{i}", f"{r.keyword} {what}", f"{pct:+.0f}%")
    # 3) 해시태그
    for i, h in enumerate(s.hashtags.rows, start=1):
        if connected(h.tag, words):
            rows.append(TrendTopicEvidenceRow(kind="hashtag", label=f"{h.tag} 해시태그",
                                              value=f"{h.accounts}곳 사용", source=HASHTAG_SOURCE,
                                              evidence_id=f"hashtag:{i}"))  # fmt: skip
        else:
            drop(f"hashtag:{i}", h.tag, f"{h.accounts}곳 사용")
    # 4) 콘텐츠: 터진 게시물(캡션 앞부분)·조회 천만·쇼츠(찾은 검색어·제목)·밈
    for i, p in enumerate(s.content.posts, start=1):
        if p.ad_excluded or is_notice_ad(p):  # 공지·광고는 위에서 이미 뺐다
            continue
        if connected(p.caption_excerpt, words):
            rows.append(TrendTopicEvidenceRow(kind="content", label=f"{p.account} 게시물",
                                              value=f"평소의 {p.ratio:g}배",
                                              source=CONTENT_SOURCE,
                                              evidence_id=f"content:{i}"))  # fmt: skip
        else:
            drop(f"content:{i}", f"{p.account} 게시물", f"{p.ratio:g}배")
    for i, v in enumerate(s.content.mega, start=1):
        if v.ad_excluded:
            continue
        if not connected(v.title, words):
            drop(f"mega:{i}", f"{v.account} 영상", f"조회 {v.views // 10_000:,}만")
    by_kw: dict[str, int] = {}
    for i, v in enumerate(s.content.shorts, start=1):
        if v.ad_excluded:
            continue
        if connected(v.keyword, words) or connected(v.title, words):
            by_kw[v.keyword] = by_kw.get(v.keyword, 0) + 1
        else:
            views = f"조회 {v.views:,}" if v.views is not None else "조회 모름"
            drop(f"shorts:{i}", f"'{v.keyword}' 쇼츠", views)
    for kw, count in by_kw.items():
        rows.append(TrendTopicEvidenceRow(kind="shorts", label=f"'{kw}' 쇼츠 검색 결과 상위",
                                          value=f"{count}편", source=SHORTS_SOURCE))  # fmt: skip
    for i, m in enumerate(s.content.memes, start=1):
        if not connected(m.name, words) and not connected(m.description, words):
            drop(f"meme:{i}", f"밈 {m.name}", "광고 재료 가능")
    report.topic_evidence = rows
    report.excluded = _merge(report.excluded, excluded)


def _merge(before: Sequence[TrendExcludedEvidence],
           extra: Sequence[TrendExcludedEvidence]) -> list[TrendExcludedEvidence]:  # fmt: skip
    """같은 id(또는 id 없는 같은 이름·이유)는 먼저 들어온 것 하나만 (notice_ad가 먼저면 그대로)."""
    out: list[TrendExcludedEvidence] = []
    seen: set[tuple[str, str]] = set()
    for e in [*before, *extra]:
        key = ("id", e.evidence_id) if e.evidence_id else (e.reason, e.label)
        if key not in seen:
            seen.add(key)  # type: ignore[arg-type]
            out.append(e)
    return out


def notice_ads(report: TrendReport) -> list[TrendExcludedEvidence]:
    """콘텐츠 칸 게시물 중 브랜드 공지·광고 → 뺀 근거 (id = content:N, 원자료 표에는 남는다)."""
    out = []
    for i, p in enumerate(report.sections.content.posts, start=1):
        if not p.ad_excluded and is_notice_ad(p):
            out.append(TrendExcludedEvidence(label=f"{p.account} 게시물", value=f"{p.ratio:g}배",
                                             reason="notice_ad",
                                             evidence_id=f"content:{i}"))  # fmt: skip
    return out


def excluded_ids(report: TrendReport) -> set[str]:
    return {e.evidence_id for e in report.excluded if e.evidence_id}


def scan_catalog(report: TrendReport) -> dict[str, tuple[str, str, str]]:
    """감지 탭에서 끌어온 지금 급상승 → 근거 목록 항목 (참고 행은 넣지 않는다)."""
    out: dict[str, tuple[str, str, str]] = {}
    for r in report.topic_evidence:
        if r.kind != "scan" or r.reference or not r.evidence_id:
            continue
        kw = r.label.removesuffix(" 검색")
        out[r.evidence_id] = (f"네이버 검색어 '{kw}' 급상승 ({SCAN_SOURCE}): {r.value}",
                              f"'{kw}' 검색이 평소보다 크게 오르는 중",
                              f"{kw} 검색 급상승"[:24])  # fmt: skip
    return out
