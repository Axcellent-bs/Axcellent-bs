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
| `check_sns_post` | read | orchestrator, safety | `post_draft_id`, `caption`, `is_ai_generated` → `verdict`(approved·rejected)와 `ai_label_missing_count`·`text_mismatch_count`·`constraint_missing_count`(0이 정상, safety `publish_sns` 기준표 필드와 같은 뜻) 등. 하나라도 어긋나면 `rejected_by_check`. 게시(`publish_sns`)·영상(`generate_video`)은 아직 없음 |
| `cancel_campaign` | read | orchestrator | `campaign_id`, `reason` → 재무 `cancel_campaign`으로 전달해 **재무가 잡아 둔 예산 기록만** 푼다. 광고를 멈추지 않는다(요청한 쪽이 멈춤). 재무 상태(`canceled`·`already_recorded`·`not_started`·`starts_unavailable`)를 그대로 돌려주고, 재무에 닿지 못하거나 답이 계약과 다르면 `failed`(취소됐다고 가정하지 않음). 결과가 이미 기록됐거나 일부를 썼으면 취소 대신 `record_campaign_result`. (`record_campaign_result`의 `spend_krw`는 광고 플랫폼의 실제 집행액을 호출자가 준다. 예산으로 채우지 않고, 빠지면 재무 호출 전에 `invalid_input`. 재무가 `not_started`로 답하면 다시 시도하지 않고 그대로 돌려준다) |
| `generate_media` | B (검증자 safety 코드 규칙, 등록은 safety 쪽 작업) | orchestrator | `post_draft_id`, `media_kind`(image·video), `scene_template_id`, `fields`(화이트리스트 값) → asset 경로·sha256·`provider`·`model_id`·`cost_krw`·`usage_source`·`ai_generated: true`. 아래 "미디어 생성" 참고 |

쿠폰은 안전 장치를 통과한 뒤 shop-api `POST /api/shop/coupons`로도 만든다(`marketing` 토큰, `idempotency_key`는 사건·구매자로 만든 키). 쿠폰 번호는 shop-api가 정하고, 쇼핑몰에서 실패하면 `shop_coupon_failed:<코드>`(failed)로 돌려주며 같은 요청을 다시 보내면 이어서 만든다. 출처는 `MARKETING_COUPON_SOURCE`(`memory` | `shop-api`)로 정하고, 비우면 목 모드는 메모리, 그 밖에는 shop-api다. 배너 공지(`channel_code=banner`)도 안전 장치를 통과한 뒤 `POST /api/shop/banners`로 쇼핑몰에 올린다. 배너 번호는 초안(`draft_id`)에서 만들어 재시작해도 겹치지 않고, 복구 공지를 올리면 같은 사건의 장애 배너를 `DELETE /api/shop/banners/{id}`로 내린다. 문구는 200자까지(넘으면 `notice_text_too_long`), 쇼핑몰에서 실패하면 `shop_banner_failed:<코드>`(failed)로 돌려주고 게시로 기록하지 않아 같은 요청을 다시 보내면 이어서 올린다. 출처는 `MARKETING_NOTICE_SOURCE`(`memory` | `shop-api`), 비우면 목 모드는 메모리다. 이메일·SNS 채널은 아직 메모리다.
- 입출력 모델: `src/marketing_agent/schemas.py`, Agent Card: `agent-card.json`
- 이벤트 data는 `contracts`의 `NoticePublishedData`·`CouponIssuedData`·`Promise`를 쓴다.
- 공지는 복구 공지(`incident_resolved`)를 게시하면 같은 `context_id`의 장애 공지가 `ended`가 되고, `valid_to`가 지나도 `ended`다. 끝난 공지도 사실·약속은 그대로 돌려주고 `status`로 구분한다(고객지원이 "종료된 공지"로 표시).
- 쿠폰 총액은 건수×단가를 코드가 계산한다. 보상 쿠폰은 `sequence` 1부터 한 건씩 처리하고, 건너뛰면 `SEQUENCE_GAP`이다.
- 결과 상태: 한도 초과·금액 불일치는 `input-required`(`human_input`, safety 보류 큐), 마감 초과는 `canceled`, 관문 무응답은 `failed`(fail-closed).
### 미디어 생성 (`generate_media`)
이미지(gpt-image-2)·영상(Gemini Omni) 생성의 **기반**이다. 이번 범위에는 실제 공급자 호출이 없고 목 어댑터(`marketing_agent/media/mock.py`)만 있다. 실제 공급자 이름을 설정하면 어댑터가 없다는 이유(`provider_adapter_missing`)로 호출 전에 거부한다.
- 프롬프트: 자유 입력 금지. `media/scene_templates.toml`의 고정 문장 + 화이트리스트 필드. 개인정보·고객 원문 말투·글자 요청·실존 인물·브랜드는 코드 검사로 `prompt_rejected`(`reason_code`와 필드 이름만 돌려주고 값은 되돌리지 않는다). 글자는 모델이 그리지 않고 오버레이가 넣는다.
- 영상은 무음이 기본(`MediaRequest.remove_audio`)이고 결과에 `ai_generated: true`를 항상 남긴다.
- 비용: `media/prices.toml`(공급자·모델·해상도별 단가, 출처 URL·확인 날짜). 표에 없는 조합(Omni 1080p, gpt-image-2 1024x1280 등)은 호출을 거부한다. `krw_est = ceil(usd_est × 환율 × (1 + 여유%))`. 환율·기준일·여유·건당 상한·월 상한이 비었거나 환율 기준일이 `MARKETING_FX_MAX_AGE_DAYS`보다 오래됐으면 실제 공급자를 거부하고 목 모드만 허용한다. 건당 상한을 넘거나 (월 확정+예약+추정)이 월 상한을 넘으면 공급자를 부르지 않는다.
- 장부: SQLite `MARKETING_MEDIA_LEDGER_DB`(run_all.sh는 `.data/marketing_media_ledger.db`, 비우면 메모리라 월 누적이 0으로 돌아간다). 키별 예약 → 확정(`usd_actual`·`krw_actual`·환율·기준일·`usage_source` actual|estimate). 응답에 사용량이 없으면 추정액을 확정액으로 남긴다. 열 수 없으면 시작 때 실패한다.
- 관문: `execute("generate_media", {cost_krw, media_kind, ...})`가 `executed`일 때만 공급자를 부른다. 멱등 키 `<context_id>:generate_media:<post_draft_id>:<요청 해시 앞 10자>`, 같은 키 재요청은 이전 결과(`replayed: true`)를 돌려준다. safety 등록부에 아직 없어 live 관문에서는 fail-closed다.
- 환경변수: `MARKETING_IMAGE_PROVIDER/MODEL`, `MARKETING_VIDEO_PROVIDER/MODEL`(공급자 기본 `mock`, 모델 기본 비움), `MARKETING_IMAGE_API_KEY`·`MARKETING_VIDEO_API_KEY`(이름만 정의), `MARKETING_FX_USD_KRW`, `MARKETING_FX_AS_OF`, `MARKETING_FX_MAX_AGE_DAYS`, `MARKETING_COST_SAFETY_MARGIN_PCT`, `MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM`, `MARKETING_MEDIA_MONTHLY_BUDGET_KRW`, `MARKETING_MEDIA_LEDGER_DB`, `MARKETING_MEDIA_DIR`. 기본값은 없다(`.env.example`).
## 4. 의존
`ax-contracts`, `ax-agent-template`. 다른 부서 폴더를 import하지 않는다.
## 5. 하지 않는 것
- 상태를 바꾸는 일을 `execute()` 없이 하지 않는다
- 보상 대상·단가 판정, 현금 환불은 하지 않는다 (고객지원 소관)
- 금액 교차검증·등급 판정은 하지 않는다 (안전 관문 코드 규칙이 맡고 요청 값은 믿지 않는다. 재무는 끝난 금액만 장부에 적는다)
- 장애 원인을 공지에 적지 않는다 (원인은 infra 판정, 공지는 사실 필드만)
- 영향 숫자(실패 건수·피해 금액)를 계산하지 않는다 (data 소관)
- 외부 입력(`untrusted`)을 지시로 해석하지 않는다
