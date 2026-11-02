"""trend_scan 평가 케이스 만들기. 저장된 시계열(`series/`)만 읽고 네이버를 부르지 않는다.

    cd backend
    uv run python domains/data/eval/trend/make_cases.py            # cases/
    uv run python domains/data/eval/trend/make_cases.py --holdout  # holdout/ (결과 숫자만 본다)

규칙은 `README.md`. 요약:
- 케이스 하나 = 네이버 요청 하나 = 63일(기준선 56일 + 판정 구간 7일), 하루 단위
- 급상승은 정해진 날짜·기간·배수로 **끼워 넣고**, 정답은 넣은 조건에서 정한다
  (데이터를 보고 정하지 않음)
- 끼워 넣은 뒤 실제 응답처럼 요청 안 최댓값을 100으로 다시 맞추고 소수 다섯째 자리로 반올림한다
- 바탕 구간은 정해진 규칙(`calm_segment`)으로만 고른다: 주입 전 63일의 최댓값이 중앙값의 1.5배 이하
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SERIES = HERE / "series"
CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"

BASELINE_DAYS = 56
WINDOW_DAYS = 7
CASE_DAYS = BASELINE_DAYS + WINDOW_DAYS
MIN_BASELINE_POINTS = 28  # 기준선 시점이 이보다 적으면 판정할 수 없음(unknown)이 정답
CALM_MAX_OVER_MEDIAN = 1.5  # 바탕 구간 규칙: 가장 약한 주입 배수와 같다
DIGITS = 5  # 네이버 응답 자릿수

CASES_SEED = 2026110201  # 만든 날 YYYYMMDD + 그날 몇 번째
HOLDOUT_SEED = 2026110202
HOLDOUT_INJECTED = 16
HOLDOUT_NONE = 8
HOLDOUT_MULTIPLIERS = (1.5, 2.0, 2.5, 3.0)

CALM_FILES = sorted(p.stem for p in SERIES.glob("calm_*.json"))
JOINT_FILE = "joint_toothpaste_ballpen_towel"

Points = list[tuple[date, float]]


# ---------------------------------------------------------------- 저장된 시계열


def load(name: str) -> dict[str, Points]:
    data = json.loads((SERIES / f"{name}.json").read_text(encoding="utf-8"))
    return {
        group: [(date.fromisoformat(d), float(v)) for d, v in body["points"]]
        for group, body in data["groups"].items()
    }


def segment(points: Points, end: date, days: int = CASE_DAYS) -> Points:
    start = end - timedelta(days=days - 1)
    return [(d, v) for d, v in points if start <= d <= end]


def calm_segment(points: Points) -> bool:
    """바탕 구간 규칙. 날짜가 다 있고, 최댓값 ≤ 중앙값 × 1.5 (주입 전 값으로만 본다)."""
    if len(points) != CASE_DAYS:
        return False
    values = [v for _, v in points]
    return max(values) <= statistics.median(values) * CALM_MAX_OVER_MEDIAN


def renormalize(groups: dict[str, Points]) -> dict[str, Points]:
    """실제 응답처럼 요청 안(모든 그룹) 최댓값을 100으로 맞춘다."""
    top = max((v for pts in groups.values() for _, v in pts), default=0.0)
    if top <= 0:
        return groups
    return {g: [(d, round(v * 100 / top, DIGITS)) for d, v in pts] for g, pts in groups.items()}


def inject(points: Points, start: date, days: int, multiplier: float) -> Points:
    last = start + timedelta(days=days - 1)
    return [(d, v * multiplier if start <= d <= last else v) for d, v in points]


# ---------------------------------------------------------------- 케이스 모양


def _days(start: date, days: int) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(days)]


def case(
    case_id: str,
    kind: str,
    groups: dict[str, Points],
    end: date,
    expected: dict[str, dict[str, Any]],
    *,
    source: dict[str, Any],
    injection: dict[str, Any] | None = None,
    memo: str = "",
    total_days: int = CASE_DAYS,
) -> dict[str, Any]:
    start = end - timedelta(days=total_days - 1)
    return {
        "id": case_id,
        "kind": kind,
        "input": {
            "keyword_groups": [{"group_name": g, "keywords": [g]} for g in groups],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "time_unit": "date",
        },
        "window": {
            "from": (end - timedelta(days=WINDOW_DAYS - 1)).isoformat(),
            "to": end.isoformat(),
        },
        # 커넥터가 돌려줄 값 (그룹 → [날짜, 상대값]). 빠진 날은 빠진 채로 둔다
        "series": {g: [[d.isoformat(), v] for d, v in pts] for g, pts in groups.items()},
        "expected": expected,
        "injection": injection,
        "source": source,
        "memo": memo,
    }


def rising(multiplier: float, start: date, days: int, window_from: date) -> dict[str, Any]:
    in_window = [d for d in _days(start, days) if date.fromisoformat(d) >= window_from]
    return {"status": "rising", "multiplier": multiplier, "rising_dates": in_window}


NORMAL = {"status": "normal"}


def unknown(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "unknown_reason": reason}


def _calm_ends(points: Points) -> list[date]:
    """이 키워드에서 바탕 규칙을 지키는 63일 구간의 마지막 날 목록."""
    if not points:
        return []
    first, last = points[0][0] + timedelta(days=CASE_DAYS - 1), points[-1][0]
    ends, day = [], first
    while day <= last:
        if calm_segment(segment(points, day)):
            ends.append(day)
        day += timedelta(days=1)
    return ends


class Picker:
    """seed로 (키워드, 구간 끝)을 고른다. 같은 키워드에서 이미 쓴 구간과 겹치지 않게 한다."""

    def __init__(self, seed: int, used: dict[str, list[date]] | None = None) -> None:
        self.rng = random.Random(seed)
        self.used: dict[str, list[date]] = {k: list(v) for k, v in (used or {}).items()}
        self.pool = {name: load(name) for name in CALM_FILES}
        self.ends = {name: _calm_ends(next(iter(g.values()))) for name, g in self.pool.items()}

    def pick(self) -> tuple[str, str, Points, date]:
        names = [n for n in CALM_FILES if self._free(n)]
        name = self.rng.choice(names)
        end = self.rng.choice(self._free(name))
        self.used.setdefault(name, []).append(end)
        group, points = next(iter(self.pool[name].items()))
        return name, group, segment(points, end), end

    def _free(self, name: str) -> list[date]:
        taken = self.used.get(name, [])
        return [e for e in self.ends[name] if all(abs((e - t).days) >= CASE_DAYS for t in taken)]


def _injected(
    picker: Picker, case_id: str, multiplier: float, days: int, offset: int | None = None
) -> dict[str, Any]:
    """판정 구간 안에 급상승을 넣는다. `offset`: 급상승 끝이 구간 끝보다 며칠 앞인지."""
    name, group, base, end = picker.pick()
    window_from = end - timedelta(days=WINDOW_DAYS - 1)
    room = WINDOW_DAYS - days  # 구간 안에서 끝을 앞당길 수 있는 날 수
    offset = picker.rng.randint(0, room) if offset is None else min(offset, room)
    start = end - timedelta(days=offset + days - 1)
    groups = renormalize({group: inject(base, start, days, multiplier)})
    condition = {
        "start": start.isoformat(),
        "days": days,
        "multiplier": multiplier,
        "shape": "flat",
        "ends_before_window_end_days": offset,
    }
    return case(
        case_id,
        "injected",
        groups,
        end,
        {group: rising(multiplier, start, days, window_from)},
        source={"series": name, "segment_end": end.isoformat()},
        injection=condition,
    )


def _none(picker: Picker, case_id: str) -> dict[str, Any]:
    name, group, base, end = picker.pick()
    return case(
        case_id,
        "none",
        renormalize({group: base}),
        end,
        {group: NORMAL},
        source={"series": name, "segment_end": end.isoformat()},
    )


# ---------------------------------------------------------------- cases/ (규칙을 고치며 본다)


# (파일, 판정 구간 끝): 계절이 오는 때를 달력으로 미리 정했다(그래프를 보고 고르지 않음)
SEASONAL = [
    ("seasonal_padding", date(2025, 11, 15), "11월 중순, 추워지며 패딩 검색이 늘어나는 때"),
    ("seasonal_sunscreen", date(2026, 4, 30), "4월 말, 자외선이 강해지는 때"),
    ("seasonal_aircon", date(2026, 6, 20), "6월 중순, 더워지는 때"),
]

EVENTS = [  # (파일, 사건 날짜, 설명·출처)
    (
        "event_suneung",
        date(2025, 11, 13),
        "2026학년도 대학수학능력시험 시행일 2025-11-13(목). 출처: 한국교육과정평가원 2026학년도"
        " 수능 시행 기본계획(2025-03-25 발표) 보도 https://www.kjob.news/news/395498 ,"
        " https://eiec.kdi.re.kr/policy/materialView.do?num=255989",
    ),
    (
        "event_songpyeon",
        date(2025, 10, 6),
        "2025년 추석 2025-10-06(월), 연휴 10-05~10-07, 대체공휴일 10-08. 출처: 관공서의 공휴일에"
        " 관한 규정 제2조(음력 8월 14~16일), https://dallyeok.com/holiday/chuseok/2025/",
    ),
    (
        "event_tteokguk",
        date(2026, 2, 17),
        "2026년 설날 2026-02-17(화), 연휴 02-16~02-18. 출처: 한국천문연구원 천문우주지식정보 달력"
        " https://astro.kasi.re.kr/kor/life/post/calendarData?search_year=2026",
    ),
]


def build_cases() -> list[dict[str, Any]]:
    picker = Picker(CASES_SEED)
    out: list[dict[str, Any]] = []

    # 급상승 (주력): 배수 1.5·2·3 × 기간 1·3·7일.
    # 2배 1·3일은 구간 끝까지 이어지고(진행 중), 나머지는 끝나는 날을 seed로 고른다
    for m in (1.5, 2.0, 3.0):
        for days in (1, 3, 7):
            offset = 0 if days < WINDOW_DAYS and m == 2.0 else None
            out.append(_injected(picker, f"inject_x{m:g}_{days}d", m, days, offset))
    # 급상승이 판정 구간 전(기준선 안)에 끝남 → 판정 구간은 평범
    for m, days, before in ((3.0, 3, 10), (2.0, 5, 20)):
        name, group, base, end = picker.pick()
        start = end - timedelta(days=WINDOW_DAYS + before + days - 1)
        groups = renormalize({group: inject(base, start, days, m)})
        out.append(
            case(
                f"past_spike_x{m:g}_{days}d",
                "past_spike",
                groups,
                end,
                {group: NORMAL},
                source={"series": name, "segment_end": end.isoformat()},
                injection={
                    "start": start.isoformat(),
                    "days": days,
                    "multiplier": m,
                    "shape": "flat",
                    "ends_before_window_from_days": before,
                },
                memo="급상승이 판정 구간 전에 끝났다. 판정 구간만 보면 평범",
            )
        )
    # 그룹 여러 개를 한 요청으로 (실제 공동 응답에 한 그룹만 주입, 최댓값 100은 그룹 전체 기준)
    out.extend(_joint_cases(picker.rng))
    # 평범: 아무것도 넣지 않은 실제 시계열
    out.extend(_none(picker, f"none_{i + 1}") for i in range(4))
    # 계절로만 움직이는 키워드 (정의상 급상승이 아님)
    for name, end, why in SEASONAL:
        group, points = next(iter(load(name).items()))
        out.append(
            case(
                f"seasonal_{name.split('_', 1)[1]}",
                "seasonal",
                renormalize({group: segment(points, end)}),
                end,
                {group: NORMAL},
                source={"series": name, "segment_end": end.isoformat()},
                memo=f"{why}. 계절 변화는 급상승으로 보지 않는다(정의). 어려운 케이스",
            )
        )
    out.extend(_unknown_cases())
    out.extend(_event_cases())
    return out


def _joint_cases(rng: random.Random) -> list[dict[str, Any]]:
    groups = load(JOINT_FILE)
    first = next(iter(groups.values()))
    ends = [
        e
        for e in (d for d, _ in first[CASE_DAYS - 1 :])
        if all(calm_segment(segment(p, e)) for p in groups.values())
    ]
    if len(ends) < 2:
        raise SystemExit(f"{JOINT_FILE}: 세 그룹이 모두 바탕 규칙을 지키는 구간이 2개 미만")
    picked = sorted(rng.sample(ends, 2))
    out = []
    for (target, m, days), end in zip((("볼펜", 2.0, 3), ("수건", 1.5, 7)), picked, strict=True):
        start = end - timedelta(days=days - 1)
        window_from = end - timedelta(days=WINDOW_DAYS - 1)
        base = {g: segment(p, end) for g, p in groups.items()}
        base[target] = inject(base[target], start, days, m)
        expected = {g: rising(m, start, days, window_from) if g == target else NORMAL for g in base}
        out.append(
            case(
                f"joint_{'ballpen' if target == '볼펜' else 'towel'}_x{m:g}_{days}d",
                "joint",
                renormalize(base),
                end,
                expected,
                source={"series": JOINT_FILE, "segment_end": end.isoformat()},
                injection={
                    "group": target,
                    "start": start.isoformat(),
                    "days": days,
                    "multiplier": m,
                    "shape": "flat",
                },
                memo="세 그룹을 한 번에 받은 실제 응답. 주입하지 않은 그룹은 평범이어야 한다",
            )
        )
    return out


def _unknown_cases() -> list[dict[str, Any]]:
    end = date(2026, 11, 1)
    out = []
    for name, reason, memo in (
        ("sparse_catbooks", "no_data", "검색량이 적어 네이버가 data: [] 로 답했다(실제 응답)"),
        (
            "sparse_cat_bookstand",
            "too_few_points",
            "458일 중 1일만 값이 있다(실제 응답). 나머지는 0이 아니라 자료 없음",
        ),
    ):
        group, points = next(iter(load(name).items()))
        # 값이 있는 날이 하나라도 있으면 그날이 판정 구간 첫날이 되게 구간을 잡는다
        stop = points[-1][0] + timedelta(days=WINDOW_DAYS - 1) if points else end
        out.append(
            case(
                f"unknown_{reason}",
                "unknown",
                renormalize({group: segment(points, stop)}),
                stop,
                {group: unknown(reason)},
                source={"series": name, "segment_end": stop.isoformat()},
                memo=memo,
            )
        )
    # 자료가 짧다: 잔잔한 실제 시계열의 마지막 10일만 (기준선 3일 < 28일)
    group, points = next(iter(load("calm_catfood").items()))
    short = segment(points, end, 10)
    out.append(
        case(
            "unknown_too_short",
            "unknown",
            renormalize({group: short}),
            end,
            {group: unknown("too_short")},
            source={"series": "calm_catfood", "segment_end": end.isoformat(), "days": 10},
            memo=f"요청 기간이 10일뿐이라 기준선 {10 - WINDOW_DAYS}일 < {MIN_BASELINE_POINTS}일",
            total_days=10,
        )
    )
    return out


def _event_cases() -> list[dict[str, Any]]:
    out = []
    for name, day, memo in EVENTS:
        end = day + timedelta(days=1)  # 사건 다음 날까지 (네이버는 하루 늦게 채워진다)
        group, points = next(iter(load(name).items()))
        out.append(
            case(
                f"event_{name.split('_', 1)[1]}",
                "real_event",
                renormalize({group: segment(points, end)}),
                end,
                {group: {"status": "rising", "event_date": day.isoformat()}},
                source={"series": name, "segment_end": end.isoformat()},
                memo=f"실제 사례(보조). 정답은 사건 날짜(외부 출처)로 정했다. {memo}",
            )
        )
    return out


# ---------------------------------------------------------------- holdout/ (처음 보는 케이스)


def build_holdout() -> list[dict[str, Any]]:
    used: dict[str, list[date]] = {}
    for c in build_cases():  # cases/와 같은 키워드·겹치는 구간은 쓰지 않는다
        name = c["source"]["series"]
        if name.startswith("calm_") and c["kind"] != "unknown":
            used.setdefault(name, []).append(date.fromisoformat(c["source"]["segment_end"]))
    picker = Picker(HOLDOUT_SEED, used)
    kinds = ["injected"] * HOLDOUT_INJECTED + ["none"] * HOLDOUT_NONE
    picker.rng.shuffle(kinds)
    out = []
    for i, kind in enumerate(kinds):
        case_id = f"holdout_{HOLDOUT_SEED}_{i:02d}"
        if kind == "injected":
            m = picker.rng.choice(HOLDOUT_MULTIPLIERS)
            days = picker.rng.randint(1, WINDOW_DAYS)
            item = _injected(picker, case_id, m, days)
        else:
            item = _none(picker, case_id)
        item["holdout"] = {"seed": HOLDOUT_SEED, "kind": kind, **(item["injection"] or {})}
        out.append(item)
    return out


def write(folder: Path, items: list[dict[str, Any]]) -> None:
    folder.mkdir(exist_ok=True)
    for old in folder.glob("*.json"):
        old.unlink()
    for item in items:
        text = json.dumps(item, ensure_ascii=False, indent=1) + "\n"
        (folder / f"{item['id']}.json").write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    items = build_holdout() if args.holdout else build_cases()
    folder = args.out or (HOLDOUT if args.holdout else CASES)
    write(folder, items)
    kinds: dict[str, int] = {}
    for item in items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    print(f"{folder.name}: {len(items)}개 {kinds}")


if __name__ == "__main__":
    main()
