"""accuracy_report skill: 정확도 평가 결과를 읽어 준다 (화면 "에이전트 정확도" 칸).

평가는 `domains/data/eval/run.py`가 돌린다. 이 skill은 파일을 읽기만 하고 다시 계산하지 않는다.
- 최근 실행 결과(`eval/results/latest.json`, 커밋하지 않음)가 있으면 그것을 준다.
- 없으면 커밋된 기준(`eval/baseline.json`)을 준다. 틀린 케이스 목록은 없다.
- 둘 다 없으면 source "none"과 "확인할 수 없음"이다(지어내지 않음).
평가 폴더는 `DATA_EVAL_DIR`로 바꿀 수 있다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import AccuracyMiss, AccuracyReportOutput

MAX_MISSES = 50
DEFAULT_EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"


def eval_dir(env: Mapping[str, str] = os.environ) -> Path:
    override = env.get("DATA_EVAL_DIR")
    return Path(override) if override else DEFAULT_EVAL_DIR


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("metrics"), dict) else None


def _metrics(raw: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for name, value in raw.items():
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        out[str(name)] = float(value) if ok else None
    return out


def load_report(folder: Path) -> AccuracyReportOutput:
    for path, source, label in (
        (folder / "results" / "latest.json", "latest", "최근 평가"),
        (folder / "baseline.json", "baseline", "기준값(커밋된 평가)"),
    ):
        data = _read(path)
        if data is None:
            continue
        misses = [
            AccuracyMiss(case_id=str(w.get("id")), area=str(w.get("area")), what=str(w.get("what")))
            for w in (data.get("wrong") or [])[:MAX_MISSES]
            if isinstance(w, dict)
        ]
        generated = data.get("generated_at")
        at = datetime.fromisoformat(generated.replace("Z", "+00:00")) if generated else None
        count = data.get("case_count")
        wrong = f" · 틀린 케이스 {len(data.get('wrong') or [])}개" if source == "latest" else ""
        return AccuracyReportOutput(
            source=source,  # type: ignore[arg-type]
            generated_at=at,
            case_count=count if isinstance(count, int) else None,
            metrics=_metrics(data["metrics"]),
            misses=misses,
            summary=f"{label} {generated or '시각 모름'}{wrong}",
        )
    return AccuracyReportOutput(
        source="none",
        summary="확인할 수 없음: 평가 결과가 없음 (domains/data/eval/run.py로 만든다)",
    )
