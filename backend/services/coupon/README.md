# services/coupon — 공지·쿠폰

포트 8103 · 업무 로직 `domains/marketing` · 화면 서비스 id `coupon`

| skill | 등급 |
| --- | --- |
| `draft_notice` | read |
| `publish_notice` | A |
| `get_notice` | read |
| `list_active_notices` | read |
| `receive_campaign_brief` | A |
| `list_campaign_briefs` | read |
| `get_campaign_brief` | read |
| `marketing_chat_send` | read |
| `marketing_chat_list` | read |
| `marketing_chat_get` | read |
| `marketing_chat_close` | read |
| `build_campaign_link` | read |
| `issue_coupon` | A |
| `draft_sns_post` | read |
| `check_sns_post` | read |
| `publish_sns` | C |

- 실행: `uv run --package ax-svc-coupon uvicorn svc_coupon.main:app --port 8103` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `marketing`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
