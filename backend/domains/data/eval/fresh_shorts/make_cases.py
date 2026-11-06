"""새 숏츠 추적(fresh_shorts) 평가 케이스 생성기: 가짜 YouTube 세상 → 찾기·추적·기준 표본 정답.

    cd backend
    uv run python domains/data/eval/fresh_shorts/make_cases.py

규칙 (이 파일이 직접 계산, skill과 따로 적음):
- 시계: 한국 0시에서 시작해 1시간마다 한 번(tick). 하루 사용량은 한국 날짜마다 새로 센다
  (`used` 정답은 가장 많이 쓴 날의 단위). 찾기는 6시간마다(tick 0, 6, 12, …) 형식마다 1번:
  게시 시각이 (지금 − 6시간, 지금]인 짧은 영상을 최신순으로. 4분 미만이면 검색에 나오지만
  60초 넘는 영상은 추적하지 않는다. 형식당 추적 상한 40개
- 추적: 찾은 tick부터 매 tick 스냅샷 (source "fresh_track"). 게시 후 8일(192시간)이 지나면 끝
- 기준 표본: 형식·시점(1h·1d·3d·7d, 창 1·2·3·4시간)마다 fresh_track 스냅샷 중 창 안 첫 값.
  인기 목록에서 따로 본 스냅샷(source "popular")은 세지 않는다
- 하루 상한(이 작업 전용 `cap`): 찾기는 쓴 단위 + 101 ≤ cap − 100(추적 몫)일 때만,
  추적은 쓴 단위 + 묶음 수 ≤ cap일 때. 검색 100 + 결과가 있으면 영상 조회 1, 추적은 50개당 1
- 기준 표본 5개 미만이면 신호 문구 "기준 모으는 중 (n/5)"
"""

from __future__ import annotations

import json
import math
import statistics
import sys

CHECKPOINTS = {"1h": (1, 1), "1d": (24, 2), "3d": (72, 3), "7d": (168, 4)}
FIND_EVERY, LOOKBACK, TRACK_HOURS, MAX_SECONDS, PER_FORMAT = 6, 6, 192, 60, 40
SEARCH_UNITS, RESERVE, NEED = 100, 100, 5
FORMATS = ("정보·팁", "챌린지")


def _videos() -> list[dict]:
    """형식마다: 다양한 게시 시각, 하나는 90초(추적 안 함). 인기 목록 스냅샷도 따로 있다."""
    out = []
    pubs = [-2.0, -0.5, 0.5, 3.0, 5.5, 7.0, 11.5, 12.5, 17.0, 23.5, 30.0, 47.5, 60.0, 95.0]
    for f_i, fmt in enumerate(FORMATS):
        for i, pub in enumerate(pubs):
            out.append({"id": f"fresh{f_i}{i:05d}"[:11], "format": fmt, "pub": pub + f_i * 0.25,
                        "seconds": 90 if i == 4 else 30 + i, "total": 3000 + 700 * i + 400 * f_i,
                        "tau": 20.0 + i, "like": 0.04, "comment": 0.005})  # fmt: skip
    return out


def views_at(v: dict, age: float) -> int:
    return int(v["total"] * (1 - math.exp(-max(age, 0) / v["tau"])))


def simulate(cap: int, ticks: int) -> dict:
    """찾기·추적을 규칙대로 돌린 결과: 영상별 스냅샷 (게시 후 시간), 찾은 수, 쓴 단위."""
    videos = _videos()
    tracked: dict[str, float] = {}  # id → 찾은 tick
    snaps: dict[str, list[float]] = {}
    used, finds, skipped_finds, track_ticks, peak = 0, 0, 0, 0, 0
    for t in range(ticks + 1):
        if t % 24 == 0:  # 시계는 한국 0시에서 시작: 하루 사용량은 날마다 새로
            peak, used = max(peak, used), 0
        if t % FIND_EVERY == 0:
            for fmt in FORMATS:
                if used + SEARCH_UNITS + 1 > cap - RESERVE:
                    skipped_finds += 1
                    continue
                hits = sorted((v for v in videos if v["format"] == fmt
                               and t - LOOKBACK < v["pub"] <= t),
                              key=lambda v: -v["pub"])  # fmt: skip
                used += SEARCH_UNITS + (1 if hits else 0)
                finds += 1
                active = sum(1 for i in tracked if _fmt(videos, i) == fmt)
                for v in hits:
                    if v["seconds"] > MAX_SECONDS or v["id"] in tracked or active >= PER_FORMAT:
                        continue
                    tracked[v["id"]] = t
                    active += 1
        live = [i for i in tracked if t - _pub(videos, i) <= TRACK_HOURS]
        calls = math.ceil(len(live) / 50)
        if live and used + calls <= cap:
            used += calls
            track_ticks += 1
            for i in live:
                snaps.setdefault(i, []).append(round(t - _pub(videos, i), 2))
    return {"snaps": snaps, "used": max(peak, used), "finds": finds,
            "skipped_finds": skipped_finds, "track_ticks": track_ticks,
            "tracked": sorted(tracked)}  # fmt: skip


def _pub(videos: list[dict], vid: str) -> float:
    return next(v["pub"] for v in videos if v["id"] == vid)


def _fmt(videos: list[dict], vid: str) -> str:
    return next(v["format"] for v in videos if v["id"] == vid)


def baseline(snaps: dict[str, list[float]]) -> dict[str, dict[str, dict]]:
    """형식 → 시점 → {n, median}: 창 안 첫 fresh_track 스냅샷의 조회 중앙값 (5개 미만이면 None)."""
    videos = {v["id"]: v for v in _videos()}
    out: dict[str, dict[str, dict]] = {f: {} for f in FORMATS}
    for fmt in FORMATS:
        for name, (hours, window) in CHECKPOINTS.items():
            vals = []
            for vid, ages in snaps.items():
                v = videos[vid]
                if v["format"] != fmt:
                    continue
                hit = next((a for a in sorted(ages) if hours <= a < hours + window), None)
                if hit is not None:
                    vals.append(float(views_at(v, hit)))
            median = statistics.median(vals) if len(vals) >= NEED else None
            out[fmt][name] = {"n": len(vals), "median": median}
    return out


def popular_noise() -> list[dict]:
    """인기 목록에서 본 스냅샷 (source popular): 기준에 세면 틀림. 같은 형식, 창 안 값."""
    return [{"id": f"popul{i:06d}", "format": FORMATS[i % 2], "pub": -1.0,
             "ages": [1.5, 24.5, 72.5], "views": 999_999} for i in range(6)]  # fmt: skip


def cases() -> list[dict]:
    main = simulate(cap=2500, ticks=200)
    capped = simulate(cap=450, ticks=24)
    labels = [{"n": n, "want": f"기준 모으는 중 ({n}/{NEED})"} for n in range(NEED)]
    return [
        {"name": "window", "cap": 2500, "ticks": 200, "videos": _videos(),
         "popular": popular_noise(),
         "expected": {"snap_counts": {k: len(v) for k, v in main["snaps"].items()},
                      "baseline": baseline(main["snaps"]), "used": main["used"]}},
        {"name": "cap", "cap": 450, "ticks": 24, "videos": _videos(), "popular": [],
         "expected": {"finds": capped["finds"], "skipped_finds": capped["skipped_finds"],
                      "track_ticks": capped["track_ticks"], "used": capped["used"]}},
        {"name": "label", "labels": labels},
    ]  # fmt: skip


def main(argv: list[str]) -> int:
    for case in cases():
        sys.stdout.write(json.dumps({k: v for k, v in case.items() if k not in ("videos",)},
                                    ensure_ascii=False)[:1500] + "\n")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
