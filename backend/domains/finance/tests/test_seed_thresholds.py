"""목 장부의 기준표가 safety `thresholds.toml`과 같은지 확인한다.

재무 목 모드는 safety 서버 없이 돌기 때문에 기준표를 시드에 옮겨 둔다. safety 값이 바뀌었는데
시드가 그대로면 목 화면이 예전 기준으로 판정하므로, 어긋나면 여기서 실패한다.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from finance_agent import books as bk

THRESHOLDS = (
    Path(__file__).resolve().parents[3] / "shared/safety/src/ax_safety/rules/thresholds.toml"
)
FIELDS = ("action", "metric", "op", "value")


def test_seed_thresholds_match_safety_table() -> None:
    table = tomllib.loads(THRESHOLDS.read_text(encoding="utf-8"))
    safety = {r["key"]: r for r in table["rules"]}
    seed = bk.read_seed()["thresholds"]
    assert seed["version"] == table["version"]
    assert seed["rules"]
    for rule in seed["rules"]:
        assert rule["key"] in safety, f"safety에 없는 규칙: {rule['key']}"
        expected = {f: safety[rule["key"]].get(f) for f in FIELDS}
        assert {f: rule.get(f) for f in FIELDS} == expected, rule["key"]
