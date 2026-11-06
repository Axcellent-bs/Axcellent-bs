"""영상 성과 판정·성과 요인·개선 방향 (규칙 기반, LLM 안 씀). 숫자는 모두 코드 계산.

- 판정: 7일 조회수 ÷ 목표. 1배 이상 achieved, 미만 missed, 7일이 아직 measuring,
  시험 영상·목표 없음·7일 놓침·연결 대기는 excluded
- 시간 맞춤 비교: 시점마다 같은 형식 숏츠의 그 시점 창 조회 중앙값(`video_baseline`)
- 신호 4개와 등급 (기준의 80% 이상 strong, 50~80% normal, 50% 미만 weak, 기준 표본 5개 미만은
  unknown):
  - first: 첫 1시간 조회 ÷ 목표 (등급은 같은 형식 1시간 조회 중앙값과 비교)
  - like·cmt: 마지막으로 잰 시점의 좋아요율·댓글율 (같은 시점 창의 중앙값과 비교)
  - grow: 3d ÷ 1d − 1 (1일·3일 중 하나라도 없으면 unknown)
- 7일 예상치(측정 중만): 3일 조회 × 같은 형식 7일/3일 중앙값. 늘 "(예상치)"로 적는다
- 문장 숫자 형식 (평가 생성기와 같은 규칙): 조회 `12,345회`, 목표 대비 `49%`(정수 %),
  좋아요율 `6.0%`, 댓글율 `0.80%`, 성장 `+64%`, 배 `10.1배`
- 원인을 지어내지 않는다: 시청 지속률·썸네일 클릭률·유입 경로는 공개 API로 안 나온다 (한계 문구)
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from ax_contracts.schemas.data import (
    SignalLevel,
    VideoCheckpoint,
    VideoChip,
    VideoInsightCard,
    VideoPerformanceOutput,
    VideoProjection,
    VideoSignal,
    VideoSignals,
    VideoVerdict,
    VideoVerdictTally,
)
from data_agent.skills.video_baseline import MIN_BASELINE, FormatBaseline

NAMES = {"1h": "1시간", "1d": "1일", "3d": "3일", "7d": "7일"}
STRONG, NORMAL = 0.8, 0.5
COLLECTING = "기준 모으는 중"  # 같은 형식 기준 표본 5개 미만 (새 숏츠 추적이 모으는 중)
DATA_GAP = "놓침: 그 시점에 재지 못함 (데이터 끊김)"
LATE_REGISTERED = "놓침: 등록 전에 그 시점이 지남"
ANALYTICS_LIMIT = (
    "시청 지속률·썸네일 클릭률·유입 경로는 공개 API로 안 나옵니다. 채널 주인 권한의 "
    "YouTube Analytics API가 필요합니다(마케팅팀 채널 연결 시 추가)."
)
SPLIT_NOTE = (
    "같은 주제 안에서 결과가 갈렸습니다. 주제는 통하고, 영상마다 첫 반응에서 차이가 났습니다."
)


# ---------------------------------------------------------------- 숫자 형식


def count(v: float) -> str:
    return f"{round(v):,}회"


def pct0(x: float) -> str:
    return f"{x * 100:.0f}%"


def pct1(x: float) -> str:
    return f"{x * 100:.1f}%"


def pct2(x: float) -> str:
    return f"{x * 100:.2f}%"


def signed(x: float) -> str:
    return f"{x * 100:+.0f}%"


def times(x: float) -> str:
    return f"{x:.1f}배"


# ---------------------------------------------------------------- 시간 맞춤 비교


def age_match(cps: Sequence[VideoCheckpoint], base: FormatBaseline | None) -> None:
    """잰 시점마다 같은 형식 숏츠의 같은 시점 창 조회 중앙값과 배수 (표본 5개 미만은 기준 없음)."""
    for c in cps:
        if c.status != "known" or c.views is None:
            continue
        median, n = base.views(c.checkpoint) if base else (None, 0)
        c.baseline_basis, c.baseline_n = "age_matched", n
        if median:
            c.baseline_views = round(median, 1)
            c.peer_age_ratio = round(c.views / median, 3)
        else:
            c.baseline_reason = f"기준 없음(표본 {n})"


def line_of(c: VideoCheckpoint) -> str:
    """화면 한 줄: "1일 · 33,263회 · 같은 형식 대비 1.66배" (시간 맞춤). 기준이 없으면 그 이유."""
    if c.status != "known" or c.views is None:
        return f"{NAMES[c.checkpoint]} · {c.reason or '측정 중'}"
    parts = [NAMES[c.checkpoint], count(c.views)]
    if c.target_ratio is not None:
        parts.append(f"목표 대비 {c.target_ratio:.2f}배")
    if c.peer_age_ratio is not None:
        parts.append(f"같은 형식 대비 {c.peer_age_ratio:.2f}배")
    elif c.baseline_reason:
        parts.append(c.baseline_reason)
    return " · ".join(parts)


# ---------------------------------------------------------------- 판정·신호


def _by(cps: Sequence[VideoCheckpoint]) -> dict[str, VideoCheckpoint]:
    return {c.checkpoint: c for c in cps}


def _known(c: VideoCheckpoint | None) -> bool:
    return c is not None and c.status == "known" and c.views is not None


def verdict_of(out: VideoPerformanceOutput) -> tuple[VideoVerdict, str | None]:
    if out.test:
        return "excluded", "시험 영상 (집계 제외)"
    if out.platform != "youtube" or not out.tracking:
        return "excluded", out.reason or "추적하지 않음"
    if not out.target_views_reference:
        return "excluded", out.target_reference_basis or "목표 참고값 없음"
    seven = _by(out.checkpoints).get("7d")
    if _known(seven) and seven is not None and seven.target_ratio is not None:
        ok = seven.target_ratio >= 1
        return ("achieved" if ok else "missed"), f"7일 {count(seven.views or 0)} ÷ 목표"
    reason = seven.reason if seven else None
    if reason and reason.startswith("측정 중"):
        return "measuring", reason
    return "excluded", f"7일 {reason or '값 없음'}"


def level_of(
    ours: float | None, base: float | None, n: int, what: str
) -> tuple[SignalLevel, str | None]:
    if ours is None:
        return "unknown", f"{what} 값 없음"
    if base is None or n < MIN_BASELINE:  # 새 숏츠 추적이 같은 형식 표본을 모으는 중
        return "unknown", f"{COLLECTING} ({n}/{MIN_BASELINE})"
    if base <= 0:
        return "unknown", f"기준 {what}이 0 이하"
    r = ours / base
    return ("strong" if r >= STRONG else "normal" if r >= NORMAL else "weak"), None


def signals_of(out: VideoPerformanceOutput, base: FormatBaseline | None) -> VideoSignals:
    by = _by(out.checkpoints)
    target = out.target_views_reference
    sig = VideoSignals()
    h1 = by.get("1h")
    if _known(h1) and h1 is not None:
        median, n = base.views("1h") if base else (None, 0)
        value = round((h1.views or 0) / target, 4) if target else None
        level, why = level_of(float(h1.views or 0), median, n, "1시간 조회")
        sig.first = VideoSignal(
            value=value,
            display=pct0(value) if value is not None else None,
            compared=float(h1.views or 0),
            baseline=median,
            baseline_n=n,
            checkpoint="1h",
            level=level,
            reason=why,
        )
    else:
        sig.first = VideoSignal(
            checkpoint="1h", reason=(h1.reason if h1 else None) or "1시간 값 없음"
        )
    last = next((c for c in reversed(out.checkpoints) if _known(c) and c.views), None)
    for name, rate, fmt in (("like", "like_rate", pct1), ("cmt", "comment_rate", pct2)):
        what = "좋아요율" if name == "like" else "댓글율"
        if last is None or getattr(last, rate) is None:
            setattr(sig, name, VideoSignal(reason=f"{what} 값 없음"))
            continue
        ours = getattr(last, rate)
        median, n = (
            (base.like_rate if name == "like" else base.comment_rate)(last.checkpoint)
            if base
            else (None, 0)
        )
        level, why = level_of(ours, median, n, f"{NAMES[last.checkpoint]} {what}")
        setattr(
            sig,
            name,
            VideoSignal(
                value=ours,
                display=fmt(ours),
                compared=ours,
                baseline=median,
                baseline_n=n,
                checkpoint=last.checkpoint,
                level=level,
                reason=why,
            ),
        )
    d1, d3 = by.get("1d"), by.get("3d")
    if _known(d1) and _known(d3) and d1 is not None and d3 is not None and d1.views:
        ours = round((d3.views or 0) / d1.views - 1, 4)
        median, n = base.growth() if base else (None, 0)
        level, why = level_of(ours, median, n, "1일→3일 성장")
        sig.grow = VideoSignal(
            value=ours,
            display=signed(ours),
            compared=ours,
            baseline=median,
            baseline_n=n,
            checkpoint="3d",
            level=level,
            reason=why,
        )
    else:
        missing = next((c for c in (d1, d3) if c is not None and not _known(c)), None)
        why = "판단 불가: 1일·3일 값 없음"
        if missing is not None and "놓침" in (missing.reason or ""):
            why = f"판단 불가: {NAMES[missing.checkpoint]} 시점 놓침 (수집 멈춤)"
        elif missing is not None:
            why = f"판단 불가: {NAMES[missing.checkpoint]} {missing.reason or '값 없음'}"
        sig.grow = VideoSignal(reason=why)
    for s in (sig.first, sig.like, sig.cmt, sig.grow):  # 기준 표본 5개 미만 = 기준 모으는 중
        s.collecting = s.level == "unknown" and (s.reason or "").startswith(COLLECTING)
    return sig


def projection_of(out: VideoPerformanceOutput, base: FormatBaseline | None) -> VideoProjection:
    """측정 중 영상의 7일 예상치: 3일 조회 × 같은 형식 7일/3일 중앙값 (늘 예상치)."""
    d3 = _by(out.checkpoints).get("3d")
    if not _known(d3) or d3 is None:
        return VideoProjection(reason="3일 값이 아직 없음")
    mult, n = base.ratio_7d_3d() if base else (None, 0)
    if mult is None:
        return VideoProjection(baseline_n=n, reason=f"기준 없음(같은 형식 3일·7일 표본 {n}개)")
    views = round((d3.views or 0) * mult)
    target = out.target_views_reference
    return VideoProjection(
        views=views,
        ratio=round(views / target, 3) if target else None,
        multiplier=round(mult, 4),
        baseline_n=n,
    )


def assess(out: VideoPerformanceOutput, base: FormatBaseline | None) -> None:
    """한 영상의 시간 맞춤 비교·판정·신호·예상치 (문장은 explain에서, 같은 인사이트 영상을 보고)."""
    age_match(out.checkpoints, base)
    for c in out.checkpoints:
        c.line = line_of(c)
    out.baseline_basis = "age_matched"
    out.verdict, out.verdict_reason = verdict_of(out)
    if out.verdict == "excluded" and out.test:
        return
    if out.tracking:
        out.signals = signals_of(out, base)
    if out.verdict == "measuring":
        out.projection = projection_of(out, base)


# ---------------------------------------------------------------- 문장


def name_of(out: VideoPerformanceOutput) -> str:
    return out.title or out.video_id or "이름 없는 영상"


def _v(out: VideoPerformanceOutput, checkpoint: str) -> int | None:
    c = _by(out.checkpoints).get(checkpoint)
    return c.views if _known(c) and c is not None else None


def comparator(
    out: VideoPerformanceOutput, peers: Sequence[VideoPerformanceOutput]
) -> VideoPerformanceOutput | None:
    """같은 인사이트·같은 형식의 달성 영상 중 7일 목표 대비가 가장 큰 것 (1시간 값이 있는 것)."""
    best = [
        p
        for p in peers
        if p is not out
        and not p.test
        and p.verdict == "achieved"
        and p.insight_id
        and p.insight_id == out.insight_id
        and p.shorts_format == out.shorts_format
        and _v(p, "1h")
    ]
    seven = lambda p: _by(p.checkpoints)["7d"].target_ratio or 0  # noqa: E731
    return max(best, key=seven) if best else None


def explain(out: VideoPerformanceOutput, peers: Sequence[VideoPerformanceOutput]) -> None:
    """성과 요인·개선 방향 문장. 시험 영상·연결 대기는 문장 없음."""
    if out.test or out.signals is None:
        return
    s, factors, actions = out.signals, [], []
    v1 = _v(out, "1h")
    other = comparator(out, peers) if out.verdict in ("missed", "measuring") else None
    rates = [
        f"좋아요율 {s.like.display}" if s.like.display else "",
        f"댓글율 {s.cmt.display}" if s.cmt.display else "",
    ]
    rate_text = ", ".join(r for r in rates if r)
    if out.verdict == "achieved":
        if s.first.value is not None:
            factors.append(f"첫 1시간에 이미 목표의 {pct0(s.first.value)}를 채웠습니다.")
        if rate_text:
            factors.append(f"{rate_text}로 본 사람이 반응까지 했습니다.")
        if s.grow.display:
            factors.append(f"1일 이후 성장 {s.grow.display}로 하루 만에 꺼지지 않았습니다.")
    else:
        if v1 is not None and s.first.value is not None:
            text = f"첫 1시간 조회가 {count(v1)}(목표의 {pct0(s.first.value)})로 시작했습니다."
            if s.first.level == "weak" and s.first.baseline:
                text += (
                    f" 같은 형식 숏츠 1시간 중앙값 {count(s.first.baseline)}의 "
                    f"{pct0(v1 / s.first.baseline)}입니다."
                )
            factors.append(text)
        if other is not None and v1:
            ov1 = _v(other, "1h") or 0
            label = f"{out.topic or out.insight_id} · {out.shorts_format}"
            factors.append(
                f"같은 '{label}'인 {name_of(other)}은 첫 1시간 {count(ov1)}로 "
                f"{times(ov1 / v1)} 차이입니다. 주제는 같으니 영상 자체(첫 장면·제목·"
                "썸네일) 차이로 봅니다."
            )
        if rate_text:
            factors.append(f"{rate_text}입니다.")
        if s.grow.display:
            factors.append(f"1일 이후 성장은 {s.grow.display}입니다.")
        p = out.projection
        if out.verdict == "measuring" and p is not None:
            if p.views is not None and p.ratio is not None:
                factors.append(
                    f"7일 예상 {count(p.views)}(예상치)로 목표의 {pct0(p.ratio)}입니다. "
                    f"3일 {count(_v(out, '3d') or 0)} × 같은 형식 숏츠 7일/3일 중앙값 "
                    f"{p.multiplier:.2f}배로 계산했습니다."
                )
            else:
                factors.append(f"7일 예상치를 내지 못했습니다(예상치 없음): {p.reason}.")
    gap = [c for c in out.checkpoints if c.reason == DATA_GAP]
    for c in gap:
        factors.append(
            f"{NAMES[c.checkpoint]} 시점은 수집이 멈춰 못 쟀습니다. "
            "영상 문제가 아니라 데이터 문제입니다."
        )
    for c in out.checkpoints:
        if c.reason == LATE_REGISTERED:
            factors.append(f"{NAMES[c.checkpoint]} 시점은 영상 등록 전에 지나 재지 못했습니다.")
    # 개선 방향
    if out.verdict == "achieved":
        wins = sum(
            1
            for p in peers
            if not p.test
            and p.verdict == "achieved"
            and p.insight_id == out.insight_id
            and p.shorts_format == out.shorts_format
        )
        actions.append(
            f"같은 주제·형식으로 2편 더 올려 우연인지 확인 (지금은 성공 {max(wins, 1)}개)"
        )
        ratio = _by(out.checkpoints)["7d"].target_ratio or 0
        if ratio >= 3:
            actions.append(
                f"목표가 낮았을 수 있음 → 다음 목표를 기획팀과 상향 검토 (이번 {ratio:.1f}배)"
            )
    else:
        if other is not None:
            actions.append(
                f"첫 2초 장면과 제목을 {name_of(other)}({other.video_id})과 나란히 놓고 비교"
            )
        if s.first.level == "weak":
            actions.append("같은 내용으로 첫 장면만 바꾼 버전을 다시 올려 첫 1시간 조회 비교 (A/B)")
        p = out.projection
        if out.verdict == "measuring" and p is not None and p.ratio is not None and p.ratio < 1:
            actions.append("7일까지 기다리지 말고 지금 첫 장면·제목 점검")
            actions.append("3일 안에 후속편 준비")
    if s.cmt.level in ("normal", "weak"):
        actions.append("끝 장면에 질문을 넣어 댓글 유도")
    if gap:
        actions.append("수집기 상시 실행 — 영상과 별개로 우리가 고칠 것")
    out.factors, out.actions = factors, actions


# ---------------------------------------------------------------- 모으기 (화면)


def tally_of(videos: Sequence[VideoPerformanceOutput]) -> VideoVerdictTally:
    real = [v for v in videos if not v.test]
    pick = lambda name: sum(1 for v in real if v.verdict == name)  # noqa: E731
    return VideoVerdictTally(
        videos=len(real),
        achieved=pick("achieved"),
        missed=pick("missed"),
        measuring=pick("measuring"),
        excluded=pick("excluded"),
    )


def insight_cards(videos: Sequence[VideoPerformanceOutput]) -> list[VideoInsightCard]:
    """인사이트 묶음만 (시험 영상 제외). 끝난 영상 3개 미만이면 결론 이름, 중앙값은 3개 이상만."""
    groups: dict[str, list[VideoPerformanceOutput]] = {}
    for v in videos:
        if not v.test and v.insight_id:
            groups.setdefault(v.insight_id, []).append(v)
    cards = []
    for insight_id, vs in sorted(groups.items()):
        won = [v for v in vs if v.verdict == "achieved"]
        lost = [v for v in vs if v.verdict == "missed"]
        waiting = [v for v in vs if v.verdict == "measuring"]
        finished = len(won) + len(lost)
        ratios = [_by(v.checkpoints)["7d"].target_ratio or 0.0 for v in won + lost]
        notes = []
        if finished < 3:
            notes.append(f"7일 결과가 {finished}개뿐이라 결론은 이릅니다")
        if won and lost:
            notes.append(SPLIT_NOTE)
        chips = [
            VideoChip(
                video_id=v.video_id or "",
                name=name_of(v),
                verdict=v.verdict or "excluded",
                target_ratio=_by(v.checkpoints)["7d"].target_ratio
                if v.verdict in ("achieved", "missed")
                else None,
                projected_ratio=v.projection.ratio if v.projection else None,
            )
            for v in vs
        ]
        cards.append(
            VideoInsightCard(
                insight_id=insight_id,
                topic=next((v.topic for v in vs if v.topic), None),
                finished=finished,
                achieved=len(won),
                missed=len(lost),
                measuring=len(waiting),
                line=f"끝난 {finished}개 중 {len(won)}개 달성 · {len(waiting)}개 측정 중",
                chips=chips,
                notes=notes,
                median_target_ratio=round(statistics.median(ratios), 3) if finished >= 3 else None,
            )
        )
    return cards
