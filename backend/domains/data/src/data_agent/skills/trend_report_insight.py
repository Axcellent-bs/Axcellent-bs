"""트렌드 분석 보고서 → 기획 receive_insight 입력(ReceiveInsightInput) 꾸러미. 코드만, LLM 없음.

총괄 plan ②(trend_report_insight)와 사람이 누르는 기획에 보내기(trend_report_handoff)가 같이 쓴다.
같은 보고서면 늘 같은 결과다(insight_id = "trend_report:" + report_id, 시각은 보고서 끝난 시각).

- facts(최대 12): 추천 근거(재료, 옛 보고서는 콘티)가 가리키는 행 먼저, 그다음 섹션 1 상위 3
  (basis filtered만, small_base 제외), 섹션 2 상위 3, 섹션 3 상위 2(link = permalink),
  섹션 4 상위 2. 근거가 0개면 insight 없음
- 캡션 등 외부 글은 body_md·facts에 넣지 않는다(태그·계정 이름·숫자만)
- body_md의 숫자는 facts(label·value)·제목에 같은 값이 있을 때만 (기획 숫자 검사와 같은 규칙).
  11/23: "## 기획에 넘길 재료"에 "재료(해시태그): …" 줄 (시간별 장면은 기획이 짠다).
  옛 보고서(콘티만)는 "## 콘티"에 훅·전환·증거·마무리 줄. 섹션 요약·한계는 숫자 없는 말
- products·product_ids·content_category·content_format은 비운다(고객사 상품을 모름)
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

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
    if s.theme.status == "known":  # 주제 후보 상위 3 (11/20)
        for n, t in enumerate(s.theme.rows[:3], start=1):
            change = f" · 최근 7일 {_signed(t.change_pct)}" if t.change_pct is not None else ""
            out[f"theme:{n}"] = (f"주제 '{s.theme.theme}' 후보 {t.name} ({who}) 네이버 검색 관심",
                                 f"{t.level}/100{change}", None)  # fmt: skip
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
    rec = report.sections.recommendation
    # 추천 근거 먼저: 재료(11/23) · 옛 보고서는 콘티 → 주제 후보 → 섹션 상위
    beat_ids = [e.id for m in rec.materials for e in m.evidence_items]
    beat_ids += [e.id for b in rec.beats for e in b.evidence_items]
    theme_ids = [k for k in rows if k.startswith("theme:")]
    order = list(dict.fromkeys([*(i for i in beat_ids if i in rows), *theme_ids,
                                *_top(report, rows)]))  # fmt: skip
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


# 기획은 설명(body_md)의 숫자가 근거(facts label·value)·제목에 없으면 경고한다
# (11/20 PROP-12 경고 2개).
# 그래서 body_md의 숫자는 facts에 같은 값이 있을 때만 남기고, 섹션 요약·한계는 숫자 없는 말로 쓴다
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
SLOT_WORD = {"0-3": "훅", "3-15": "전환", "15-25": "증거", "25-30": "마무리"}
MATERIAL_WORD = {"hashtag": "해시태그", "format": "형식", "topic": "관심 주제", "meme": "밈",
                 "writing": "글쓰기", "timing": "올리는 때", "other": "그 밖"}  # fmt: skip
# 고정 한계 문장 → 숫자 없는 말 (보고서 화면에는 원문 그대로)
PLAIN_LIMITS = {
    "숫자는": "숫자는 전부 코드가 계산했고, AI는 결론 한 줄과 재료 문장만 숫자 없이 썼다",
    "검색·쇼핑은": "검색·쇼핑은 최근 한 주 평균을 직전 네 주 평균과 견줬다. 네이버 값은 요청 안의"
                  " 상대값이라 키워드끼리 크기는 비교하지 않는다",
    "터짐 =": "터짐은 그 계정 최근 게시물 중앙값보다 크게 뜨고 최근 한 주 안에 올린 것",
    "인스타는": "인스타는 공식 API로 프로페셔널 계정의 최근 게시물만 본다. 개인 계정·해시태그 전체"
                " 게시물은 볼 수 없다",
}
OTHER_LIMITS = "그 밖의 주의는 보고서 화면 근거와 한계 칸에서 확인"


def numbers(text: str) -> set[str]:
    """기획 숫자 검사와 같은 규칙 (쉼표를 지운 숫자 덩어리)."""
    return {m.replace(",", "") for m in _NUM.findall(text or "")}


def allowed_numbers(report: TrendReport, facts: list[InsightFact]) -> set[str]:
    title = f"트렌드 분석: {report.brief.product}" if report.brief.product else ""
    got = numbers(title)
    for f in facts:
        got |= numbers(f"{f.label} {f.value}")
    return got


def _plain_limit(text: str, allowed: set[str]) -> str | None:
    if numbers(text) <= allowed:
        return text
    return next((v for k, v in PLAIN_LIMITS.items() if text.startswith(k)), None)


def _part(done: int, total: int, unit: str) -> str:
    if total == 0 or done == 0:
        return f"{unit} 없음"
    return f"{unit} 대부분 있음" if done * 2 > total else f"{unit} 일부만 있음"


def body_md(report: TrendReport, facts: list[InsightFact] | None = None) -> str:
    """기획에 넘기는 설명. 숫자는 `facts`(label·value)·제목에 같은 값이 있을 때만 남긴다."""
    from data_agent.skills.trend_report import evidence_catalog

    s = report.sections
    rec = s.recommendation
    ok = allowed_numbers(report, pick_facts(report) if facts is None else facts)

    def safe(text: str) -> bool:
        return numbers(text) <= ok

    none = f"(결론 없음: {report.summary_reason or '이유 모름'})"
    conclusion = report.summary_line or none
    lines = ["## 결론", conclusion if safe(conclusion) else "(결론 문장은 보고서 화면에서 확인)"]
    words = {k: v[1] for k, v in evidence_catalog(report).items()}

    def evidence_lines(items: Sequence[Any]) -> None:
        for item in items:
            if safe(item.text):
                lines.append(f"  - 근거: {item.text}")
            elif (word := words.get(item.id)) and safe(word):
                lines.append(f"  - 근거: {word}")

    if rec.materials:  # 11/23: 시간별 장면은 기획이 짠다. 데이터는 재료까지만
        lines.append("## 기획에 넘길 재료")
        for m in rec.materials:
            text = m.text if safe(m.text) else "(재료 문장은 보고서 화면에서 확인)"
            lines.append(f"- 재료({MATERIAL_WORD.get(m.kind, '그 밖')}): {text}")
            evidence_lines(m.evidence_items)
        if rec.music_note and safe(rec.music_note):
            lines.append(f"- 음악: {rec.music_note}")
    elif rec.beats:  # 옛 보고서 (콘티만): 지금처럼 장면 줄로
        lines.append("## 콘티")
        for beat in rec.beats:
            text = beat.text if safe(beat.text) else "(장면 문장은 보고서 화면에서 확인)"
            lines.append(f"- {SLOT_WORD.get(beat.slot, '장면')}: {text}")
            evidence_lines(beat.evidence_items)
        if rec.music_note and safe(rec.music_note):
            lines.append(f"- 음악: {rec.music_note}")
    if s.theme.status == "known" and s.theme.rows:  # 11/20: 숫자는 facts에 있는 값만
        lines.append(f"## 주제 후보 · {s.theme.theme}")
        for t in s.theme.rows[:3]:
            line = f"- {t.name}: 네이버 검색 관심 {t.level}/100"
            lines.append(line if safe(line) else f"- {t.name}" if safe(t.name) else "- (이름 생략)")
        lines.append("- 이름은 근거로만 써요. 광고에 이름·사진·음원을 쓰려면 권리자 허락이"
                     " 필요해요")  # fmt: skip
    lines.append("## 섹션 요약")
    known = sum(1 for r in s.search.rows if r.search_change_pct is not None)
    lines.append(f"- 검색: {_part(known, len(s.search.rows), '키워드 변화율')}")
    tags = [h.tag for h in s.hashtags.rows[:5] if safe(h.tag)]
    lines.append(f"- 해시태그: {', '.join(tags) or '없음'}")
    lines.append("- 콘텐츠: " + ("터진 게시물 있음" if s.content.posts else "터진 게시물 없음")
                 + (", 같이 뜬 밈 있음" if s.content.memes else ""))  # fmt: skip
    read, failed = len(s.competitors.rows), len(s.competitors.accounts_failed)
    state = ("읽은 계정 없음" if not read else
             "일부 계정만 읽음" if failed else "고른 계정 모두 읽음")  # fmt: skip
    lines.append(f"- 경쟁사: {state}")
    groups = report.limit_groups
    limits = [*groups.method, *groups.cannot_see, *groups.this_report] or report.limits
    plain = [p for x in limits if (p := _plain_limit(x, ok))]
    if limits:
        lines.append("## 한계")
        lines += [f"- {x}" for x in dict.fromkeys(plain)]
        if len(plain) < len(limits):
            lines.append(f"- {OTHER_LIMITS}")
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
        body_md=body_md(report, facts),
    ), None
