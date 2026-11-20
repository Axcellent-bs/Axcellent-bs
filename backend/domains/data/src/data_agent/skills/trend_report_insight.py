"""트렌드 분석 보고서 → 기획 receive_insight 입력(ReceiveInsightInput) 꾸러미. 코드만, LLM 없음.

총괄 plan ②(trend_report_insight)와 사람이 누르는 기획에 보내기(trend_report_handoff)가 같이 쓴다.
같은 보고서면 늘 같은 결과다(insight_id = "trend_report:" + report_id, 시각은 보고서 끝난 시각).

- facts(최대 12): 콘티 근거가 가리키는 행 먼저, 그다음 섹션 1 상위 3(basis filtered만, small_base
  제외), 섹션 2 상위 3, 섹션 3 상위 2(link = permalink), 섹션 4 상위 2. 근거가 0개면 insight 없음
- 캡션 등 외부 글은 body_md·facts에 넣지 않는다(태그·계정 이름·숫자만)
- products·product_ids·content_category·content_format은 비운다(고객사 상품을 모름)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from ax_contracts.schemas.data import TrendReport
from ax_contracts.schemas.planning import InsightFact, ReceiveInsightInput
from data_agent.skills import trend_report_brief as tb

SKILL = "trend_report_create"
SOURCE = "trend_report_create"
FACTS_MAX = 12
LABEL_MAX, VALUE_MAX, SOURCE_MAX = 120, 80, 60
BODY_MAX = 8000
NO_REPORT = "보고서 없음"
NOT_DONE = "아직 만드는 중"
NO_FACTS = "근거로 쓸 숫자가 없음 (섹션 1~4가 모두 비었음)"
LIVE = ("queued", "running")


def clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def insight_id(report_id: str) -> str:
    return clip(f"trend_report:{report_id}", 80)


def audience(report: TrendReport) -> str | None:
    """"20~30대 여성" (나이가 이어지면 ~, 아니면 ·)."""
    b = report.brief
    ages = sorted(b.target_ages, key=int)
    if ages and all(int(y) - int(x) == 10 for x, y in zip(ages, ages[1:], strict=False)):
        age = f"{ages[0]}~{ages[-1]}대" if len(ages) > 1 else f"{ages[0]}대"
    else:
        age = "·".join(f"{a}대" for a in ages)
    parts = [p for p in (age, tb.GENDER_WORD.get(b.target_gender or "")) if p]
    return clip(" ".join(parts), 60) if parts else None


def _signed(pct: float) -> str:
    return f"{pct:+.1f}%"


def _fmt(media_product_type: str | None, media_type: str | None) -> str:
    if media_product_type == "REELS":
        return "릴스"
    return {"VIDEO": "영상", "IMAGE": "사진", "CAROUSEL_ALBUM": "캐러셀"}.get(media_type or "",
                                                                         "게시물")  # fmt: skip


Row = tuple[str, str, str | None]  # (label, value, link)


def row_facts(report: TrendReport) -> dict[str, Row]:
    """근거 id(섹션:행 번호, 콘티 evidence_items.id와 같은 규칙) → (label, value, link)."""
    s, b = report.sections, report.brief
    who = tb.target_word(b)
    names = {**b.competitor_names, **s.hashtags.account_names}
    out: dict[str, Row] = {}
    for n, r in enumerate(s.search.rows, start=1):
        if r.search_change_pct is not None and r.basis in (None, "filtered") and not r.small_base:
            out[f"search:{n}"] = (f"네이버 검색 '{r.keyword}' ({who}) 최근 7일 vs 직전 28일",
                                  _signed(r.search_change_pct), None)  # fmt: skip
        if r.shopping_change_pct is not None and not r.shopping_small_base:
            out[f"shopping:{n}"] = (f"네이버 쇼핑 클릭 '{r.keyword}' ({who}) 최근 7일 vs 직전 28일",
                                    _signed(r.shopping_change_pct), None)  # fmt: skip
    for n, h in enumerate(s.hashtags.rows, start=1):
        used = f"{h.accounts}곳 (직전 7일 {h.previous_accounts}곳)"
        out[f"hashtag:{n}"] = (f"인스타 {h.tag}를 쓴 뷰티 계정 (이번 7일)", used, None)
    for n, p in enumerate(s.content.posts, start=1):
        who_p = names.get(p.account) or p.account
        out[f"content:{n}"] = (f"{who_p} {_fmt(p.media_product_type, p.media_type)} 터짐",
                               f"평소의 {p.ratio:g}배", p.permalink)  # fmt: skip
    for n, m in enumerate(s.content.memes, start=1):
        out[f"meme:{n}"] = (f"같이 뜬 밈 {m.name}", "광고 재료 가능", None)
    for n, c in enumerate(s.competitors.rows, start=1):
        bits = []
        if c.reels_share is not None:
            bits.append(f"릴스 {round(c.reels_share * 100)}%")
        if c.posts_per_week is not None:
            bits.append(f"주당 {c.posts_per_week:g}개")
        if c.best_post is not None:
            bits.append(f"최고 {c.best_post.ratio:g}배")
        if bits:
            out[f"competitor:{n}"] = (f"경쟁사 {c.name or c.account}", " · ".join(bits), None)
    return out


def _top(report: TrendReport, rows: dict[str, Row]) -> list[str]:
    """섹션별 상위: 검색 3(변화율 큰 순) · 해시태그 3 · 콘텐츠 2 · 경쟁사 2(최고 배수 순)."""
    s = report.sections
    search = sorted((k for k in rows if k.startswith("search:")),
                    key=lambda k: -(s.search.rows[int(k.split(":")[1]) - 1].search_change_pct
                                    or 0))[:3]  # fmt: skip

    def first(prefix: str, n: int, key: Callable[[str], float] | None = None) -> list[str]:
        ids = [k for k in rows if k.startswith(prefix)]
        return (sorted(ids, key=key) if key else ids)[:n]

    def comp_key(k: str) -> float:
        best = s.competitors.rows[int(k.split(":")[1]) - 1].best_post
        return -(best.ratio if best else 0.0)

    return [*search, *first("hashtag:", 3), *first("content:", 2),
            *first("competitor:", 2, comp_key)]  # fmt: skip


def pick_facts(report: TrendReport) -> list[InsightFact]:
    rows = row_facts(report)
    beat_ids = [e.id for b in report.sections.recommendation.beats for e in b.evidence_items]
    order = list(dict.fromkeys([*(i for i in beat_ids if i in rows), *_top(report, rows)]))
    return [InsightFact(label=clip(rows[i][0], LABEL_MAX), value=clip(rows[i][1], VALUE_MAX),
                        source=clip(SOURCE, SOURCE_MAX),
                        link=rows[i][2][:500] if rows[i][2] else None)
            for i in order[:FACTS_MAX]]  # fmt: skip


def shorts_format(report: TrendReport) -> str | None:
    s = report.sections
    seen = Counter(_fmt(p.media_product_type, p.media_type) for p in s.content.posts)
    seen.update(_fmt(c.best_post.media_product_type, c.best_post.media_type)
                for c in s.competitors.rows if c.best_post is not None)  # fmt: skip
    seen.pop("게시물", None)
    return seen.most_common(1)[0][0] if seen else None


def body_md(report: TrendReport) -> str:
    s = report.sections
    rec = s.recommendation
    none = f"(결론 없음: {report.summary_reason or '이유 모름'})"
    lines = ["## 결론", report.summary_line or none]
    if rec.beats:
        lines.append("## 콘티")
        for beat in rec.beats:
            lines.append(f"- {beat.slot}초: {beat.text}")
            lines += [f"  - 근거: {e}" for e in beat.evidence]
        if rec.music_note:
            lines.append(f"- 음악: {rec.music_note}")
    lines.append("## 섹션 요약")
    known = [r for r in s.search.rows if r.search_change_pct is not None]
    lines.append(f"- 검색: 키워드 {len(s.search.rows)}개 중 변화율 {len(known)}개"
                 + (f" ({s.search.reason})" if s.search.reason else ""))  # fmt: skip
    lines.append(f"- 해시태그: {', '.join(h.tag for h in s.hashtags.rows[:5]) or '없음'}"
                 + (f" ({s.hashtags.reason})" if s.hashtags.reason else ""))  # fmt: skip
    lines.append(f"- 콘텐츠: 터진 게시물 {len(s.content.posts)}개, 밈 {len(s.content.memes)}개")
    lines.append(f"- 경쟁사: {len(s.competitors.rows)}곳 읽음, "
                 f"{len(s.competitors.accounts_failed)}곳 못 읽음")  # fmt: skip
    groups = report.limit_groups
    limits = [*groups.method, *groups.cannot_see, *groups.this_report] or report.limits
    if limits:
        lines.append("## 한계")
        lines += [f"- {x}" for x in limits]
    text = "\n".join(lines)
    return text if len(text) <= BODY_MAX else text[: BODY_MAX - 1] + "…"


def build_insight(report: TrendReport | None) -> tuple[ReceiveInsightInput | None, str | None]:
    """(기획 입력, 없을 때 이유). 같은 보고서면 같은 결과."""
    if report is None:
        return None, NO_REPORT
    if report.status in LIVE:
        return None, NOT_DONE
    facts = pick_facts(report)
    if not facts:
        return None, NO_FACTS
    title = f"트렌드 분석: {report.brief.product}" if report.brief.product else "트렌드 분석 의뢰"
    summary = report.summary_line or (
        "검색·인스타·경쟁사 근거를 모았지만 추천 결론은 없음"
        f"({report.summary_reason or '이유 모름'})")  # fmt: skip
    return ReceiveInsightInput(
        insight_id=insight_id(report.report_id),
        title=clip(title, 120),
        summary=clip(summary, 400),
        skill=SKILL,
        facts=facts,
        audience=audience(report),
        shorts_format=shorts_format(report),
        generated_at=report.finished_at or report.created_at,
        body_md=body_md(report),
    ), None
