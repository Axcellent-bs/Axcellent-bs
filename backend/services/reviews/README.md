# services/reviews — 리뷰·지식

포트 8122 · 업무 로직 `domains/support` · 화면 서비스 id `reviews`

| skill | 등급 |
| --- | --- |
| `customer_signals` | read |
| `list_at_risk_customers` | read |

- 실행: `uv run --package ax-svc-reviews uvicorn svc_reviews.main:app --port 8122` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `support`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
