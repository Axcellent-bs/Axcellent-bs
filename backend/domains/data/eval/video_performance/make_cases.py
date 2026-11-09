"""video_performance 평가 케이스 생성기: 합성 조회수 곡선 → 수집기가 잰 값 → 시점별 정답.

    cd backend
    uv run python domains/data/eval/video_performance/make_cases.py   # 케이스마다 정답 출력

케이스는 파일로 두지 않고 여기 명세(`SPECS`, `MIXED`)에서 그때그때 만든다 (같은 명세면 늘 같음).

- 곡선: 조회수(h) = 총량 × (1 − e^(−h/τ)), 좋아요·댓글은 조회의 일정 비율 (정수로 내림)
- 수집기: 게시 15분 뒤부터 1시간마다 잰다. `gap`이면 그 구간은 재지 않는다 (수집 멈춤)
- 정답(이 파일이 직접 계산): 시점 H마다 [H, H + 창) 안 첫 측정값, 없으면 unknown("놓침")
  - 시간당 조회 = 조회 ÷ 게시 후 시간, 비율 = 좋아요(댓글) ÷ 조회 (소수 넷째 자리)
  - 같은 숏츠 대비 = 조회 ÷ 중앙값 (소수 셋째 자리, 모든 시점)
  - 목표 대비 = 조회 ÷ 목표 참고값 (소수 셋째 자리). 목표 참고값이 정의된 시점(7일)에만 있고
    다른 시점은 None (답에 값이 있으면 틀림)
- 섞인 시점 케이스(`MIXED`): 같은 인사이트 영상 2개, 하나는 90시간 늦게 게시. 성과 모으기는
  시점(1일·3일·7일)마다 그 시점을 잰 영상만 센다. 정답 = 7일 집계 n=1 (늦은 영상은 측정 중이라
  빠짐, low_sample), 3일 집계 n=2
- 창·시점은 skill과 따로 적는다 (같은 값이어야 한다: 1h·1d·3d·7d, 창 1·2·3·4시간)
"""

from __future__ import annotations

import json
import math
import statistics
import sys

CHECKPOINTS = {"1h": (1, 1), "1d": (24, 2), "3d": (72, 3), "7d": (168, 4)}  # 시간, 창
TARGET_AT = "7d"  # 목표 참고값이 정의된 시점
HORIZONS = ("1d", "3d", "7d")  # 성과 모으기 시점
LOW_SAMPLE = 3  # n이 이보다 작으면 low_sample
TARGET = 5000.0  # 인사이트 목표 참고값
PEER = 2000.0  # 같은 형식 숏츠 조회 중앙값
FIRST = 0.25  # 첫 측정: 게시 15분 뒤
LAST = 170.25

SPECS = {
    "good": {"total": 60000, "tau": 30.0, "like": 0.06, "comment": 0.008, "gap": None,
             "note": "잘 된 영상: 7일에 목표 참고값의 12배"},
    "average": {"total": 4000, "tau": 20.0, "like": 0.03, "comment": 0.004, "gap": None,
                "note": "평범한 영상: 7일에도 목표 참고값의 0.8배"},
    "gap": {"total": 4000, "tau": 20.0, "like": 0.03, "comment": 0.004, "gap": [20.0, 31.0],
            "note": "데이터 끊김: 20~31시간 수집 멈춤 → 1일 놓침"},
}  # fmt: skip

# 시점이 다른 영상 2개 (같은 인사이트·형식). pub = 첫 영상 게시 뒤 몇 시간에 게시했는지
MIXED = {
    "note": "시점이 다른 영상 2개: 늦게 게시한 영상은 7일이 아직 (측정 중)",
    "videos": [
        {"id": "mixedearly1", "pub": 0.0, "total": 9000, "tau": 25.0, "like": 0.05,
         "comment": 0.006, "gap": None},
        {"id": "mixedlate02", "pub": 90.0, "total": 1500, "tau": 15.0, "like": 0.02,
         "comment": 0.003, "gap": None},
    ],
}  # fmt: skip


def views_at(spec: dict, hours: float) -> int:
    return int(spec["total"] * (1 - math.exp(-hours / spec["tau"])))


def measurements(spec: dict, pub: float = 0.0) -> list[list[float]]:
    """[게시 후 시간, 조회, 좋아요, 댓글] (수집기가 잰 순서). 수집기 시계는 첫 영상 게시 기준."""
    out: list[list[float]] = []
    h = FIRST
    while h <= LAST + 1e-9:
        age = round(h - pub, 2)
        gap = spec["gap"]
        if age >= 0 and not (gap and gap[0] < age < gap[1]):
            v = views_at(spec, age)
            out.append([age, v, int(v * spec["like"]), int(v * spec["comment"])])
        h += 1.0
    return out


def expected(points: list[list[float]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, (hours, window) in CHECKPOINTS.items():
        hit = next((p for p in points if hours <= p[0] < hours + window), None)
        if hit is None:
            last = points[-1][0] if points else 0.0
            word = "측정 중" if last < hours + window else "놓침"
            out[name] = {"status": "unknown", "reason_word": word}
            continue
        t, v, likes, comments = hit[0], int(hit[1]), int(hit[2]), int(hit[3])
        out[name] = {
            "status": "known", "hours_after": t, "views": v, "likes": likes,
            "comments": comments, "views_per_hour": round(v / t, 2),
            "like_rate": round(likes / v, 4) if v else None,
            "comment_rate": round(comments / v, 4) if v else None,
            "target_ratio": round(v / TARGET, 3) if name == TARGET_AT else None,
            "peer_ratio": round(v / PEER, 3),
        }  # fmt: skip
    return out


def build(name: str) -> dict:
    spec = SPECS[name]
    points = measurements(spec)
    return {"name": name, "spec": spec, "target": TARGET, "peer": PEER, "measurements": points,
            "expected": expected(points)}  # fmt: skip


def cases() -> list[dict]:
    return [build(name) for name in SPECS]


def _median(values: list[float], digits: int) -> float | None:
    return round(statistics.median(values), digits) if values else None


def aggregate(per_video: list[dict[str, dict]]) -> dict[str, dict]:
    """시점마다 그 시점을 잰 영상만 모은다. 측정 중·놓침은 빼고 센다."""
    out: dict[str, dict] = {}
    for h in HORIZONS:
        known = [e[h] for e in per_video if e[h]["status"] == "known"]
        waiting = sum(e[h]["status"] == "unknown" and e[h]["reason_word"] == "측정 중"
                      for e in per_video)  # fmt: skip
        out[h] = {
            "videos": len(per_video), "n": len(known), "excluded": len(per_video) - len(known),
            "excluded_measuring": waiting,
            "excluded_missed": len(per_video) - len(known) - waiting,
            "low_sample": len(known) < LOW_SAMPLE,
            "median_views": _median([float(k["views"]) for k in known], 1),
            "median_peer_ratio": _median([k["peer_ratio"] for k in known], 3),
            "median_target_ratio": _median([k["target_ratio"] for k in known], 3)
            if h == TARGET_AT else None,
        }  # fmt: skip
    return out


def build_mixed() -> dict:
    videos = []
    for v in MIXED["videos"]:
        points = measurements(v, v["pub"])
        videos.append({**v, "measurements": points, "expected": expected(points)})
    return {"name": "mixed", "note": MIXED["note"], "target": TARGET, "peer": PEER,
            "videos": videos, "aggregate": aggregate([v["expected"] for v in videos])}  # fmt: skip


def aggregate_cases() -> list[dict]:
    return [build_mixed()]


# ---------------------------------------------------------------- 성과 요인 시나리오 (v3)
# 인사이트 2개, 우리 영상 5개, 같은 형식 숏츠(피어) 스냅샷. 수집기 시계는 FIRST부터 1시간마다.
# 피어는 시계 안에서 같은 시각에 잰다 (게시 시각이 달라 게시 후 시간이 다양함).
# 정답(이 파일이 직접 계산):
# - 시간 맞춤 기준: 시점 창마다 피어 첫 스냅샷 조회 중앙값 (표본 5개 미만이면 None, 배수 없음)
#   → ① 평범한 영상 7일 같은 형식 대비 ≤ 1 (시간 안 맞춘 지금 조회 중앙값 2000이면 2.0배로 틀림)
# - 신호 4개 등급: 우리 값 ÷ 피어 중앙값 ≥ 0.8 strong, ≥ 0.5 normal, 아니면 weak, 표본 < 5 unknown
#   (⑤ 강의 형식은 피어 3개라 전부 unknown)
#   first = 1시간 조회 (값 표시는 ÷ 목표), like·cmt = 마지막으로 잰 시점, grow = 3d ÷ 1d − 1
#   (③ 1일 놓침이면 unknown)
# - 판정: 7일 ÷ 목표 ≥ 1 achieved, < 1 missed, 7일이 아직이면 measuring
# - 예상치(④ 측정 중): round(3일 × 피어 7d/3d 중앙값), 목표 대비, is_estimate
# - 문장 숫자: 조회 `12,345회`, 목표 대비 `49%`, 좋아요율 `6.0%`, 댓글율 `0.80%`, 성장 `+64%`,
#   배 `10.1배`, 7일/3일 `1.05배`. 문장 말: ② 같은 인사이트 달성 영상과 비교 "주제는 같으니",
#   ③ "데이터 문제", ④ "(예상치)"

STRONG, NORMAL, MIN_BASELINE = 0.8, 0.5, 5
SCENE_INSIGHTS = [
    {"insight_id": "ins_book", "topic": "책추천", "format": "리뷰", "target": 5000.0},
    {"insight_id": "ins_py", "topic": "파이썬기초", "format": "강의", "target": 3000.0},
]  # fmt: skip
SCENE_VIDEOS = [
    {"id": "scenegood01", "name": "잘 된 영상", "insight": "ins_book", "pub": 0.0, "total": 60000,
     "tau": 30.0, "like": 0.06, "comment": 0.008, "gap": None},
    {"id": "sceneavg002", "name": "평범한 영상", "insight": "ins_book", "pub": 0.0, "total": 4000,
     "tau": 20.0, "like": 0.03, "comment": 0.004, "gap": None},
    {"id": "scenegap003", "name": "수집 멈춘 영상", "insight": "ins_book", "pub": 0.0,
     "total": 4000, "tau": 20.0, "like": 0.03, "comment": 0.004, "gap": [20.0, 31.0]},
    {"id": "scenelate04", "name": "늦게 올린 영상", "insight": "ins_book", "pub": 90.0,
     "total": 1500, "tau": 15.0, "like": 0.02, "comment": 0.003, "gap": None},
    {"id": "scenelec005", "name": "강의 영상", "insight": "ins_py", "pub": 0.0, "total": 9000,
     "tau": 25.0, "like": 0.05, "comment": 0.006, "gap": None},
]  # fmt: skip
# 같은 형식 숏츠: 리뷰 8개(앞 6개는 7일 창까지 들어옴), 강의 3개(표본 부족)
SCENE_PEERS = [
    *({"id": f"peerreview{i}", "format": "리뷰", "pub": pub, "total": total, "tau": tau,
       "like": like, "comment": cmt, "gap": None}
      for i, (pub, total, tau, like, cmt) in enumerate([
          (-0.5, 16000, 22.0, 0.035, 0.004), (0.0, 18000, 24.0, 0.038, 0.0045),
          (0.5, 20000, 26.0, 0.040, 0.005), (1.0, 22000, 28.0, 0.042, 0.0055),
          (1.5, 24000, 30.0, 0.044, 0.006), (2.0, 26000, 32.0, 0.045, 0.006),
          (40.0, 20000, 26.0, 0.040, 0.005), (80.0, 21000, 27.0, 0.041, 0.005)])),
    *({"id": f"peerlectur{i}", "format": "강의", "pub": pub, "total": 8000, "tau": 25.0,
       "like": 0.04, "comment": 0.005, "gap": None} for i, pub in enumerate([0.0, 1.0, 2.0])),
]  # fmt: skip


def _first_in_windows(points: list[list[float]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for name, (hours, window) in CHECKPOINTS.items():
        hit = next((p for p in points if hours <= p[0] < hours + window), None)
        if hit is not None:
            out[name] = hit
    return out


def _peer_samples(fmt: str) -> dict[str, dict[str, list[float]]]:
    """시점 → 피어 id → 그 창의 첫 측정 [게시 후 시간, 조회, 좋아요, 댓글]."""
    out: dict[str, dict[str, list[float]]] = {}
    for p in SCENE_PEERS:
        if p["format"] != fmt:
            continue
        for name, hit in _first_in_windows(measurements(p, p["pub"])).items():
            out.setdefault(name, {})[p["id"]] = hit
    return out


def _vals(ps: dict, name: str) -> list[list[float]]:
    return list(ps.get(name, {}).values())


def _med(values: list[float]) -> tuple[float | None, int]:
    return (statistics.median(values) if len(values) >= MIN_BASELINE else None), len(values)


def _level(ours: float | None, base: float | None, n: int) -> str:
    if ours is None or base is None or n < MIN_BASELINE or base <= 0:
        return "unknown"
    r = ours / base
    return "strong" if r >= STRONG else "normal" if r >= NORMAL else "weak"


def _pct0(x: float) -> str:
    return f"{x * 100:.0f}%"


def _count(v: float) -> str:
    return f"{round(v):,}회"


def build_scenario() -> dict:
    insight = {i["insight_id"]: i for i in SCENE_INSIGHTS}
    peers = {f: _peer_samples(f) for f in {p["format"] for p in SCENE_PEERS}}
    ours: dict[str, dict] = {}
    for v in SCENE_VIDEOS:
        ins = insight[v["insight"]]
        points = measurements(v, v["pub"])
        exp = expected(points)
        ps = peers[ins["format"]]
        for name, e in exp.items():
            if e["status"] != "known":
                continue
            base, n = _med([float(s[1]) for s in _vals(ps, name)])
            e["target_ratio"] = round(e["views"] / ins["target"], 3) if name == TARGET_AT else None
            e["baseline_n"] = n
            e["baseline_views"] = round(base, 1) if base else None
            e["peer_age_ratio"] = round(e["views"] / base, 3) if base else None
        ours[v["id"]] = {"spec": v, "insight": ins, "points": points, "expected": exp, "peers": ps}
    for o in ours.values():  # 판정
        seven = o["expected"]["7d"]
        if seven["status"] == "known":
            o["verdict"] = "achieved" if seven["views"] / o["insight"]["target"] >= 1 else "missed"
        else:
            o["verdict"] = "measuring" if seven["reason_word"] == "측정 중" else "excluded"
    videos = []
    for vid, o in ours.items():
        videos.append({"id": vid, "name": o["spec"]["name"], "insight": o["insight"]["insight_id"],
                       **_answers(o, ours)})  # fmt: skip
    return {"name": "scenario", "insights": SCENE_INSIGHTS, "videos": SCENE_VIDEOS,
            "peers": SCENE_PEERS, "peer": PEER, "answers": videos}  # fmt: skip


def _answers(o: dict, ours: dict[str, dict]) -> dict:
    e, ps, target = o["expected"], o["peers"], o["insight"]["target"]
    known = [n for n in CHECKPOINTS if e[n]["status"] == "known"]
    numbers: list[str] = []
    phrases: list[str] = []
    levels: dict[str, str] = {}
    # first
    v1 = e["1h"]["views"] if "1h" in known else None
    base1, n1 = _med([float(s[1]) for s in _vals(ps, "1h")])
    levels["first"] = _level(float(v1) if v1 is not None else None, base1, n1)
    first = v1 / target if v1 is not None else None
    # like · cmt (마지막으로 잰 시점)
    last = next((n for n in reversed(list(CHECKPOINTS)) if n in known and e[n]["views"]), None)
    like = e[last]["like_rate"] if last else None
    cmt = e[last]["comment_rate"] if last else None
    bl, nl = _med([s[2] / s[1] for s in _vals(ps, last or "") if s[1]])
    bc, nc = _med([s[3] / s[1] for s in _vals(ps, last or "") if s[1]])
    levels["like"], levels["cmt"] = _level(like, bl, nl), _level(cmt, bc, nc)
    # grow (1일·3일 중 하나라도 없으면 unknown)
    grow = None
    if "1d" in known and "3d" in known and e["1d"]["views"]:
        grow = round(e["3d"]["views"] / e["1d"]["views"] - 1, 4)
    levels["grow"] = _level(grow, *_peer_ratio(ps, "1d", "3d", minus_one=True))
    if o["verdict"] == "achieved":
        if first is not None:
            numbers.append(_pct0(first))
    else:
        if v1 is not None:
            numbers += [_count(v1), _pct0(first or 0)]
            if levels["first"] == "weak" and base1:
                numbers += [_count(base1), _pct0(v1 / base1)]
        good = [x for x in ours.values() if x["verdict"] == "achieved"
                and x["insight"]["insight_id"] == o["insight"]["insight_id"]
                and x["expected"]["1h"]["status"] == "known"]  # fmt: skip
        if good and v1:
            best = max(good, key=lambda x: x["expected"]["7d"]["views"] / x["insight"]["target"])
            gv1 = best["expected"]["1h"]["views"]
            numbers += [_count(gv1), f"{gv1 / v1:.1f}배"]
            phrases.append("주제는 같으니")
    if like is not None:
        numbers.append(f"{like * 100:.1f}%")
    if cmt is not None:
        numbers.append(f"{cmt * 100:.2f}%")
    if grow is not None:
        numbers.append(f"{grow * 100:+.0f}%")
    if any(e[n]["status"] == "unknown" and e[n]["reason_word"] == "놓침" for n in CHECKPOINTS):
        phrases.append("데이터 문제")
    projection = None
    if o["verdict"] == "measuring" and "3d" in known:
        mult, n = _peer_ratio(ps, "3d", "7d", minus_one=False)
        if mult is not None:
            views = round(e["3d"]["views"] * mult)
            projection = {"views": views, "ratio": round(views / target, 3), "is_estimate": True}
            numbers += [_count(views), _pct0(views / target), _count(e["3d"]["views"]),
                        f"{mult:.2f}배"]  # fmt: skip
            phrases.append("(예상치)")
    return {"verdict": o["verdict"], "checkpoints": e, "levels": levels, "numbers": numbers,
            "phrases": phrases, "projection": projection}  # fmt: skip


def _peer_ratio(ps: dict, a: str, b: str, *, minus_one: bool) -> tuple[float | None, int]:
    """피어마다 시점 a·b를 다 잰 것만: b ÷ a (grow면 − 1)."""
    by_a, by_b = ps.get(a, {}), ps.get(b, {})
    vals = [by_b[k][1] / by_a[k][1] - (1 if minus_one else 0)
            for k in by_a if k in by_b and by_a[k][1]]  # fmt: skip
    return _med(vals)


def scenario_cases() -> list[dict]:
    return [build_scenario()]


def main(argv: list[str]) -> int:
    for case in cases():
        sys.stdout.write(f"{case['name']}: {case['spec']['note']}\n")
        sys.stdout.write(json.dumps(case["expected"], ensure_ascii=False) + "\n")
    for case in aggregate_cases():
        sys.stdout.write(f"{case['name']}: {case['note']}\n")
        sys.stdout.write(json.dumps(case["aggregate"], ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
