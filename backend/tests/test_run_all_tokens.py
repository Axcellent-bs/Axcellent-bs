"""run_all.sh 에이전트 토큰 명부: shop-api가 들어가고, 저장된 값은 다시 켜도 그대로 쓴다.

판매 서버(shop-api)가 마케팅을 A2A로 부를 때 명부에 shop-api가 없으면 UNAUTHORIZED로 거부된다.
run_all.sh의 NAMES 줄과 토큰 생성 파이썬 조각을 그대로 뽑아 돌린다.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" / "run_all.sh").read_text(
    encoding="utf-8"
)
BLOCK = re.search(
    r'\n(?:read -r AX_AGENT_TOKENS[^\n]*\("\$PY" -c ")\n(.*?)\n" "\$NAMES"', SCRIPT, re.S
)
NAMES_LINE = re.search(r'^NAMES="\$\(echo "\$APPS".*?\) (.*)"$', SCRIPT, re.M)
SERVICE_NAMES = ["support", "marketing", "hr", "archive", "infra", "admin"]


def _run(tmp_path: Path, names: list[str]) -> dict[str, str]:
    assert BLOCK, "run_all.sh의 토큰 생성부를 찾지 못했다"
    out = subprocess.run(
        [sys.executable, "-c", BLOCK.group(1), " ".join(names), str(tmp_path / "tokens.env"), ""],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    return dict(p.split("=", 1) for p in out[0].split(","))


def test_names_line_includes_shop_api() -> None:
    assert NAMES_LINE, "run_all.sh의 NAMES 줄을 찾지 못했다"
    assert "shop-api" in NAMES_LINE.group(1).split()


def test_shop_api_is_in_the_registry(tmp_path: Path) -> None:
    assert NAMES_LINE
    registry = _run(tmp_path, ["data", *NAMES_LINE.group(1).split()])
    assert registry["shop-api"]
    assert set(SERVICE_NAMES) <= set(registry)


def test_saved_shop_api_token_is_kept(tmp_path: Path) -> None:
    assert NAMES_LINE
    names = NAMES_LINE.group(1).split()
    (tmp_path / "tokens.env").write_text(
        "AX_AGENT_TOKENS=shop-api=sales-server-token-1234\n", encoding="utf-8"
    )
    assert _run(tmp_path, names)["shop-api"] == "sales-server-token-1234"
    # 한 번 더 켜도 그대로
    assert _run(tmp_path, names)["shop-api"] == "sales-server-token-1234"
