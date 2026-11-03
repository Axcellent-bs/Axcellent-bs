# services/org — 조직 변동

포트 8124 · 업무 로직 `domains/hr` · 화면 서비스 id `org`

| skill | 등급 |
| --- | --- |
| `team_headcount` | read |
| `record_org_change` | A |
| `decide_org_change` | A |
| `list_org_changes` | read |
| `set_org_roster_complete` | A |

- 실행: `uv run --package ax-svc-org uvicorn svc_org.main:app --port 8124`
  (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `hr`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
