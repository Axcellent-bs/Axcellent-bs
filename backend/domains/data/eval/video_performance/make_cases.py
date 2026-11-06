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
