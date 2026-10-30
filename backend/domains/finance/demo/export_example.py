"""재무 화면 "예시 데이터" 묶음 만들기 (목 모드 시드 → 프론트 JSON).

재무 화면의 [실제 데이터 | 예시 데이터] 전환에서 예시 쪽이 보여 주는 응답이다. 서버를 띄우지 않고
목 모드(`AGENT_TEMPLATE_MOCK=1`) 재무 앱을 그대로 불러 GET 응답을 모은다. 숫자는 재무 코드가 시드
(`finance_books_seed.json`·`mock_seed.json`)로 계산한 값이고 화면에서 지어내지 않는다.

    cd backend
    uv run python domains/finance/demo/export_example.py

결과: `frontend/src/lib/financeExample.json` (경로 → 응답 본문). 시드나 계산이 바뀌면 다시 만든다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["AGENT_TEMPLATE_MOCK"] = "1"
os.environ["FINANCE_SELF_TEST_SECONDS"] = "0"
os.environ.pop("FINANCE_STATE_FILE", None)

from fastapi.testclient import TestClient  # noqa: E402

from finance_agent import handlers  # noqa: E402
from finance_agent.main import app  # noqa: E402
from finance_agent.sources import MockSources  # noqa: E402

OUT = Path(__file__).resolve().parents[4] / "frontend" / "src" / "lib" / "financeExample.json"

PATHS = [
    "/api/finance/overview",
    "/api/finance/closing?period=day",
    "/api/finance/closing?period=month",
    "/api/finance/statements?period=month",
    "/api/finance/statements?period=quarter",
    "/api/finance/statements?period=year",
    "/api/finance/reconciliation",
    "/api/finance/settlements",
    "/api/finance/policy",
    "/api/finance/settlements/deadlines",
    "/api/finance/payables",
    "/api/finance/variance",
    "/api/finance/bank",
    "/api/finance/cashflow",
    "/api/finance/close-checklist",
    "/api/finance/audit?days=7",
    "/api/finance/audit?days=14",
    "/api/finance/audit?days=30",
    "/api/finance/anomalies",
    "/api/finance/incidents",
    "/api/finance/support-costs",
    "/api/finance/ledger",
]


def main() -> None:
    handlers.reset_state(MockSources())
    client = TestClient(app)
    out: dict[str, object] = {}
    for path in PATHS:
        res = client.get(path)
        if res.status_code != 200:
            sys.exit(f"{path}: {res.status_code} {res.text[:200]}")
        out[path] = res.json()
    for todo in out["/api/finance/overview"]["data"]["todo"]:  # type: ignore[index]
        action = todo.get("action") or {}
        if action.get("type") == "closing_confirm":  # 할 일에서 바로 확정할 날의 결산
            path = f"/api/finance/closing?period={action['period']}&date={action['date']}"
            out[path] = client.get(path).json()
    for row in out["/api/finance/incidents"]["data"]:  # type: ignore[index]
        path = f"/api/finance/incidents/{row['context_id']}"
        out[path] = client.get(path).json()
    # 재무 AI 테스트는 다른 서버 연결까지 보므로 예시에는 결과를 넣지 않는다 (가짜 "정상" 없음)
    out["/api/finance/self-test/last"] = {"data": None, "meta": {"next_run_at": None}}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT} ({len(out)}개 경로)")


if __name__ == "__main__":
    main()
