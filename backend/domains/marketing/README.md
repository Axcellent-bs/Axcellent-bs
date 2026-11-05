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
| `receive_campaign_brief` | A | planning | `CampaignBriefInput`(기획안) → `CampaignBriefOutput`. 검증·중복 확인 뒤 처음 받는 제안만 safety 관문 `execute("receive_campaign_brief")`로 넘기고 `executed`일 때만 저장(보류·거절·무응답은 저장 안 함). 처음이면 `accepted`+`campaign_id`(`cmp_`+`proposal_id`, `start_campaign`도 같은 id), 같은 `proposal_id`는 `duplicate`(보관본 그대로, 처음 id 반환), 못 받으면 `rejected`+`reason_code`(`unsupported_schema_version`·`invalid_proposal_id`·`empty_title`·`empty_idea`·`empty_why`). `facts`·`target`은 받은 그대로 보관(숫자 계산·거절 근거 없음). 캠페인은 시작하지 않고 이벤트도 내지 않는다. 보관은 SQLite이고 파일은 `MARKETING_BRIEFS_DB`(run_all.sh는 `.data/marketing_briefs.db`)로 정한다. 비우면 메모리라 다시 시작할 때 사라져 기획 쪽 `handoff.ok`와 어긋날 수 있다. 파일은 다시 시작해도 남고, 저장을 못 하면 `storage_error`(failed)로 돌려주며 같은 요청을 다시 보내면 이어서 받는다. 계약이 바뀌어 읽을 수 없는 줄은 건너뛴다 |
| `list_campaign_briefs` | read | planning, orchestrator, dashboard-api, shop-api | `limit` → 받은 기획안 목록(받은 시각 늦은 순, 보관한 그대로, 상태 `received`). 없으면 빈 목록과 이유 |
| `get_campaign_brief` | read | planning, orchestrator, dashboard-api, shop-api | `proposal_id` 또는 `campaign_id` → 받은 기획안 하나. 없으면 "확인할 수 없음" |
| `build_campaign_link` | read | planning, orchestrator, dashboard-api, shop-api | `base_url`, `campaign_id`(`cmp_PROP-n`), `source`, `medium` → UTM 꼬리표(`utm_source`·`utm_medium`·`utm_campaign`)가 붙은 `url`. `source`는 `banner`·`notice`·`instagram`·`google`·`youtube`, `medium`은 `onsite`·`social`·`cpc`이며 조합표(banner·notice=onsite, instagram=social, google=cpc, youtube=social·cpc)에 맞아야 한다. `base_url`은 http/https만(사용자 정보 `user:pw@` 거절), 기존 쿼리·프래그먼트는 보존하고 같은 `utm_source`·`utm_medium`·`utm_campaign`은 덮어쓴다(`utm_term` 등 다른 키는 그대로). 어긋나면 `invalid_input`(failed). 순수 계산이라 저장·발행 없음, `execute()` 없음. 순수 함수는 `links.build_campaign_link` |
| `issue_coupon` | A (한도 초과·금액 불일치는 C), 검증자 없음 | support | `purpose_code`, `buyer_id`, 건수, 단가 → `coupon_id`, `coupon.issued` 발행 |
| `start_campaign` | B (검증자 재무) | orchestrator | `campaign_id`, `campaign_name`, `channel`, `budget_krw` → 재무 판단(`review`) + 관문 결과. 재무 거절은 `rejected_by_finance`(관문 미호출), 한도 초과는 `over_approved_limit`, 그 밖에는 `executed`·`held`(50만 원 이상 사람 승인)·`rejected_by_review` 등 관문 결과 그대로 |
| `record_campaign_result` | read | orchestrator | `CampaignResultInput` → 재무 `record_campaign_result`로 전달. `gmv_krw`를 모르면 비워 둠(0으로 채우지 않음) |
| `draft_sns_post` | read | orchestrator | `notice_id`(게시 중인 공지) 또는 `coupon`(공개 쿠폰 사실, 제약 사항 7항목 필수) → Instagram 이미지 캡션 초안(`post_draft_id`). 고정 템플릿에 AI 생성·시연용·약관 문구를 넣고 원인·영향 숫자는 적지 않음. 쿠폰 제약 누락은 `rejected_constraint_missing`, 공지가 없으면 `notice_not_found`, 끝났으면 `notice_not_active` |
| `check_sns_post` | read | orchestrator, safety | `post_draft_id`, `caption`, `is_ai_generated` → `verdict`(approved·rejected)와 `ai_label_missing_count`·`text_mismatch_count`·`constraint_missing_count`(0이 정상, safety `publish_sns` 기준표 필드와 같은 뜻) 등. 하나라도 어긋나면 `rejected_by_check`. 영상(`generate_video`)은 아직 없음 |
| `publish_sns` | C (항상 사람 승인) | orchestrator | `post_draft_id`, `asset_ref`, `asset_sha256`, `caption`, `media_kind`(image·reel), `share_to_feed`, `is_ai_generated`(항상 true) → 멱등 키 `{context_id}:publish_sns:{post_draft_id}:{content_sha[:12]}`(`content_sha`는 캡션·자산 해시·종류·옵션의 sha256)로 `execute()`. `held`면 `input-required`(`review_id`)이고 승인 뒤 자동으로 게시한다. 같은 요청을 다시 보내면 지금 상태를 돌려준다(`held`·`publishing`·`published`+`permalink`·`rejected_by_review`·실패 코드). 실제 어댑터가 없어 목 어댑터만 쓰고, `MARKETING_SNS_PROVIDER`가 `mock`이 아니면 `adapter_unavailable`(관문 호출 전) |
| `cancel_campaign` | read | orchestrator | `campaign_id`, `reason` → 재무 `cancel_campaign`으로 전달해 **재무가 잡아 둔 예산 기록만** 푼다. 광고를 멈추지 않는다(요청한 쪽이 멈춤). 재무 상태(`canceled`·`already_recorded`·`not_started`·`starts_unavailable`)를 그대로 돌려주고, 재무에 닿지 못하거나 답이 계약과 다르면 `failed`(취소됐다고 가정하지 않음). 결과가 이미 기록됐거나 일부를 썼으면 취소 대신 `record_campaign_result`. (`record_campaign_result`의 `spend_krw`는 광고 플랫폼의 실제 집행액을 호출자가 준다. 예산으로 채우지 않고, 빠지면 재무 호출 전에 `invalid_input`. 재무가 `not_started`로 답하면 다시 시도하지 않고 그대로 돌려준다) |

쿠폰은 안전 장치를 통과한 뒤 shop-api `POST /api/shop/coupons`로도 만든다(`marketing` 토큰, `idempotency_key`는 사건·구매자로 만든 키). 쿠폰 번호는 shop-api가 정하고, 쇼핑몰에서 실패하면 `shop_coupon_failed:<코드>`(failed)로 돌려주며 같은 요청을 다시 보내면 이어서 만든다. 출처는 `MARKETING_COUPON_SOURCE`(`memory` | `shop-api`)로 정하고, 비우면 목 모드는 메모리, 그 밖에는 shop-api다. 배너 공지(`channel_code=banner`)도 안전 장치를 통과한 뒤 `POST /api/shop/banners`로 쇼핑몰에 올린다. 배너 번호는 초안(`draft_id`)에서 만들어 재시작해도 겹치지 않고, 복구 공지를 올리면 같은 사건의 장애 배너를 `DELETE /api/shop/banners/{id}`로 내린다. 문구는 200자까지(넘으면 `notice_text_too_long`), 쇼핑몰에서 실패하면 `shop_banner_failed:<코드>`(failed)로 돌려주고 게시로 기록하지 않아 같은 요청을 다시 보내면 이어서 올린다. 출처는 `MARKETING_NOTICE_SOURCE`(`memory` | `shop-api`), 비우면 목 모드는 메모리다. 이메일·SNS 채널은 아직 메모리다.
- 입출력 모델: `src/marketing_agent/schemas.py`, Agent Card: `agent-card.json`
- 이벤트 data는 `contracts`의 `NoticePublishedData`·`CouponIssuedData`·`Promise`를 쓴다.
- 공지는 복구 공지(`incident_resolved`)를 게시하면 같은 `context_id`의 장애 공지가 `ended`가 되고, `valid_to`가 지나도 `ended`다. 끝난 공지도 사실·약속은 그대로 돌려주고 `status`로 구분한다(고객지원이 "종료된 공지"로 표시).
- 쿠폰 총액은 건수×단가를 코드가 계산한다. 보상 쿠폰은 `sequence` 1부터 한 건씩 처리하고, 건너뛰면 `SEQUENCE_GAP`이다.
- 결과 상태: 한도 초과·금액 불일치는 `input-required`(`human_input`, safety 보류 큐), 마감 초과는 `canceled`, 관문 무응답은 `failed`(fail-closed).
### `publish_sns` 게시 흐름 (설계 문서 6절, 분석 A-3)
- 상태 기계(게시 대기 저장소, SQLite): `held → approved → publishing → published | failed`, 거절·만료는 `held → rejected`. 전이는 compare-and-swap 한 번이라 이벤트가 두 번 와도, 이벤트와 폴링이 겹쳐도 `publishing`에 들어가는 쪽은 하나뿐이다. `publishing` 이상은 재진입하지 않는다.
- 승인 알림은 `safety.review_decided` 구독(action=`publish_sns`, 요청자=`marketing`만)이 1차다. `REDIS_URL`이 없으면 구독자는 꺼지고(로그 `sns_subscriber_disabled`, `runtime_status()`), `MARKETING_SNS_REVIEW_POLL_S`마다 대기 건의 `GET /api/safety/reviews/{review_id}`를 확인하는 폴링이 이어 준다. 마케팅이 스스로 승인하는 경로는 없다(결정 API는 사람 토큰만).
- 승인을 받으면 내용 해시를 다시 계산해 키와 같은지 보고(다르면 `content_changed`, 새 승인 필요), 같으면 **같은 키로** `execute()`를 다시 불러 `executed`(멱등 재생)를 받은 뒤에만 어댑터를 부른다.
- 어댑터 흐름: 컨테이너 생성(`is_ai_generated=true`, `share_to_feed` 항상 명시) → 상태 조회(`IN_PROGRESS`→`FINISHED`) → `media_publish`(성공 1회) → 미디어 조회(`permalink`·`timestamp`·`media_product_type`·`is_ai_generated` 응답값 기록). 응답을 잃으면 다시 게시하지 않고 컨테이너 `status_code`가 `PUBLISHED`인지로 판정하고, 확인하지 못하면 `publish_unconfirmed`로 닫는다.
- 실패 분류(분석 A-1 표): 일시 오류(`24/2207008`, `-2/2207003`, `-1/2207001·2207032·2207053`, `9007/2207027`)만 백오프(상한 `MARKETING_SNS_MAX_RETRIES`), `24/2207006`·`-2/2207020`은 새 컨테이너 1회, 한도(`9/2207042`)·스팸(`4/2207051`)·계정 제한(`25/2207050`)·URL(`9004/2207052`)·형식(`352/2207026`)·토큰(`190`)·모르는 코드는 즉시 `failed`. 재시도는 같은 `execution_id` 아래에서만 하고, `failed`가 된 키는 다시 열리지 않는다(내용을 바꾸면 새 키·새 승인).
- 환경변수(값은 모두 선택): `MARKETING_SNS_PROVIDER`(`mock`, 기본), `MARKETING_SNS_PENDING_DB`(SQLite 파일, 비우면 메모리라 재시작하면 대기 건이 사라짐. `run_all.sh`는 `.data/marketing_sns_pending.db`), `MARKETING_SNS_REVIEW_POLL_S`(15, 0이면 폴링 끔), `MARKETING_SNS_MAX_RETRIES`(3), `MARKETING_SNS_RETRY_BASE_S`(2), `MARKETING_SNS_STATUS_POLL_S`(5), `MARKETING_SNS_STATUS_MAX_POLLS`(60). 토큰·앱 시크릿 이름 `MARKETING_IG_ACCESS_TOKEN`·`MARKETING_IG_USER_ID`·`MARKETING_IG_APP_SECRET`은 이름만 정했고 코드는 값을 읽지도 출력하지도 않는다(실제 어댑터는 아직 없음).
- 로그: `execute_decided`·`result_sent`(공통 로그), `sns_publish_attempt`·`sns_container_status`(표준 로거 `ax.event`만. 계약 `LogKind`에 없다). 토큰·서명 URL·고객 원문은 남기지 않는다.
- Facebook 로그인 경로의 삭제(`DELETE /{media-id}`)는 어댑터 인터페이스(`delete_media`)에 자리만 있고 쓰지 않는다. 쓰려면 별도 액션(`unpublish_sns`, C 제안)으로 `execute()`를 거친다.

## 4. 의존
`ax-contracts`, `ax-agent-template`. 다른 부서 폴더를 import하지 않는다.
## 5. 하지 않는 것
- 상태를 바꾸는 일을 `execute()` 없이 하지 않는다
- 보상 대상·단가 판정, 현금 환불은 하지 않는다 (고객지원 소관)
- 금액 교차검증·등급 판정은 하지 않는다 (안전 관문 코드 규칙이 맡고 요청 값은 믿지 않는다. 재무는 끝난 금액만 장부에 적는다)
- 장애 원인을 공지에 적지 않는다 (원인은 infra 판정, 공지는 사실 필드만)
- 영향 숫자(실패 건수·피해 금액)를 계산하지 않는다 (data 소관)
- 외부 입력(`untrusted`)을 지시로 해석하지 않는다
