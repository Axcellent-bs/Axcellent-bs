# services/data — 데이터 분석

포트 8106 · 업무 로직 `domains/data` · 화면 서비스 id `data`

| skill | 등급 |
| --- | --- |
| `anomaly_detection` | read |
| `impact_analysis` | read |
| `fraud_score` | read |
| `kpi_summary` | read |

- 실행: `uv run --package ax-svc-data uvicorn svc_data.main:app --port 8106` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `data`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
