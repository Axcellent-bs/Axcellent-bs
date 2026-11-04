# agents/marketing
담당: 재헌 · 포트: 8103
## 1. 역할
마케팅 에이전트. 통합 에이전트가 넘긴 사건·상품 사실로 **공지**를 만들고 게시하며, 고객지원·재무가 공지 사실과 약속을 읽을 수 있게 하고(`get_notice`), 사건 보상·캠페인 **쿠폰**을 지급한다.
- v0은 시나리오 A 필수 skill 4개(`draft_notice`, `publish_notice`, `get_notice`, `issue_coupon`)다. 저장소는 메모리(목 모드)이고, 문안은 사실 필드만 쓰는 고정 템플릿이다(LLM 연결은 TODO).
- 캠페인(`draft_campaign`·`create_campaign`·`campaign_report`)은 권장·선택이라 이후에 만든다.
## 2. 실행 방법
- 서버: `docker compose -f infra/compose.yml up marketing`, 또는 `uv run uvicorn marketing_agent.main:app --port 8103`
- 목 모드: `MARKETING_MOCK=1` (LLM·외부 호출 대신 가짜 응답. CI는 항상 1)
- 단독 테스트: `uv run pytest agents/marketing`
- 요청 보내 보기: `uv run ax-test-call --agent http://localhost:8103 --skill get_notice --input input.json`
## 3. 입출력 계약
| skill | 등급 | 요청 가능 | 입력 → 출력 |
| --- | --- | --- | --- |
| `draft_notice` | read | orchestrator | 사건·상품 사실, `promises[]` → 공지 초안 |
| `publish_notice` | A | orchestrator | `draft_id` → `notice_id`, `notice.published` 발행 (`channel_code=sns` 초안은 `sns_use_publish_sns`로 거절, SNS는 `publish_sns`) |
| `get_notice` | read | support, finance, orchestrator | `notice_id` → 사실·약속·기간·공개 문구·상태(`published`·`ended`)·게시 사건(`context_id`)·게시 시각(`published_at`). 없으면 "확인할 수 없음" |
| `list_active_notices` | read | support, finance, orchestrator | `component`·`channel_code`·`at`·`limit` → 지금 게시 중인 공지 목록(최근 게시 순, 끝난 공지·시작 전 공지 제외). 없으면 빈 목록과 이유 |
| `receive_campaign_brief` | A | planning | `CampaignBriefInput`(기획안) → `CampaignBriefOutput`. 처음이면 `accepted`+`campaign_id`(`cmp_`+`proposal_id`, `start_campaign`도 같은 id), 같은 `proposal_id`는 `duplicate`(보관본 그대로, 처음 id 반환), 못 받으면 `rejected`+`reason_code`(`unsupported_schema_version`·`invalid_proposal_id`·`empty_title`·`empty_idea`·`empty_why`). `facts`·`target`은 받은 그대로 보관(숫자 계산·거절 근거 없음). 캠페인은 시작하지 않고 이벤트도 내지 않는다. 메모리 보관이라 다시 시작하면 사라짐 |
| `list_campaign_briefs` | read | planning, orchestrator, dashboard-api | `limit` → 받은 기획안 목록(받은 시각 늦은 순, 보관한 그대로, 상태 `received`). 없으면 빈 목록과 이유 |
| `get_campaign_brief` | read | planning, orchestrator, dashboard-api | `proposal_id` 또는 `campaign_id` → 받은 기획안 하나. 없으면 "확인할 수 없음" |
| `issue_coupon` | A (한도 초과·금액 불일치는 C), 검증자 없음 | support | `purpose_code`, `buyer_id`, 건수, 단가 → `coupon_id`, `coupon.issued` 발행 |
| `start_campaign` | B (검증자 재무) | orchestrator | `campaign_id`, `campaign_name`, `channel`, `budget_krw` → 재무 판단(`review`) + 관문 결과. 재무 거절은 `rejected_by_finance`(관문 미호출), 한도 초과는 `over_approved_limit`, 그 밖에는 `executed`·`held`(50만 원 이상 사람 승인)·`rejected_by_review` 등 관문 결과 그대로 |
| `record_campaign_result` | read | orchestrator | `CampaignResultInput` → 재무 `record_campaign_result`로 전달. `gmv_krw`를 모르면 비워 둠(0으로 채우지 않음) |

쿠폰은 안전 장치를 통과한 뒤 shop-api `POST /api/shop/coupons`로도 만든다(`marketing` 토큰, `idempotency_key`는 사건·구매자로 만든 키). 쿠폰 번호는 shop-api가 정하고, 쇼핑몰에서 실패하면 `shop_coupon_failed:<코드>`(failed)로 돌려주며 같은 요청을 다시 보내면 이어서 만든다. 출처는 `MARKETING_COUPON_SOURCE`(`memory` | `shop-api`)로 정하고, 비우면 목 모드는 메모리, 그 밖에는 shop-api다. 배너 공지(`channel_code=banner`)도 안전 장치를 통과한 뒤 `POST /api/shop/banners`로 쇼핑몰에 올린다. 배너 번호는 초안(`draft_id`)에서 만들어 재시작해도 겹치지 않고, 복구 공지를 올리면 같은 사건의 장애 배너를 `DELETE /api/shop/banners/{id}`로 내린다. 문구는 200자까지(넘으면 `notice_text_too_long`), 쇼핑몰에서 실패하면 `shop_banner_failed:<코드>`(failed)로 돌려주고 게시로 기록하지 않아 같은 요청을 다시 보내면 이어서 올린다. 출처는 `MARKETING_NOTICE_SOURCE`(`memory` | `shop-api`), 비우면 목 모드는 메모리다. 이메일·SNS 채널은 아직 메모리다.
- 입출력 모델: `src/marketing_agent/schemas.py`, Agent Card: `agent-card.json`
- 이벤트 data는 `contracts`의 `NoticePublishedData`·`CouponIssuedData`·`Promise`를 쓴다.
- 공지는 복구 공지(`incident_resolved`)를 게시하면 같은 `context_id`의 장애 공지가 `ended`가 되고, `valid_to`가 지나도 `ended`다. 끝난 공지도 사실·약속은 그대로 돌려주고 `status`로 구분한다(고객지원이 "종료된 공지"로 표시).
- 쿠폰 총액은 건수×단가를 코드가 계산한다. 보상 쿠폰은 `sequence` 1부터 한 건씩 처리하고, 건너뛰면 `SEQUENCE_GAP`이다.
- 결과 상태: 한도 초과·금액 불일치는 `input-required`(`human_input`, safety 보류 큐), 마감 초과는 `canceled`, 관문 무응답은 `failed`(fail-closed).
## 4. 의존
`ax-contracts`, `ax-agent-template`. 다른 부서 폴더를 import하지 않는다.
## 5. 하지 않는 것
- 상태를 바꾸는 일을 `execute()` 없이 하지 않는다
- 보상 대상·단가 판정, 현금 환불은 하지 않는다 (고객지원 소관)
- 금액 교차검증·등급 판정은 하지 않는다 (안전 관문 코드 규칙이 맡고 요청 값은 믿지 않는다. 재무는 끝난 금액만 장부에 적는다)
- 장애 원인을 공지에 적지 않는다 (원인은 infra 판정, 공지는 사실 필드만)
- 영향 숫자(실패 건수·피해 금액)를 계산하지 않는다 (data 소관)
- 외부 입력(`untrusted`)을 지시로 해석하지 않는다
