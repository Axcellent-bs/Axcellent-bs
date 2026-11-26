# 고객 의뢰 카탈로그 조사 (총괄 의뢰 자동 넘김, 0단계)

고객사 담당자가 AI 대화방에서 "~ 만들어주세요 / ~ 해주세요"라고 의뢰하면 총괄이 알맞은 부서로 넘긴다.
이 문서는 총괄이 **부르는 일(skill) 목록**을 조사한 것이다. 코드를 읽어 정리했고 코드는 바꾸지 않았다.
조사일 2026-11-19, 기준 `dev`. 분류기(1단계)가 고를 "일 목록"의 재료이며, 확인하지 못한 것은 맨 아래에 모았다.

## 기준

- 등급은 부서 카드(`services/*/agent-card.json`)의 `grade`다. 실제 등급은 안전 관문
  `shared/safety/src/ax_safety/rules/registry.toml`의 `base_grade`가 우선한다.
  카드가 A여도 registry에서 B·C인 것: `publish_sns`(C), `start_campaign`(B), `generate_media`(B),
  `rollback_deployment`(B), `confirm_handover`(B).
- 총괄이 부를 수 있는 일은 카드 `allowed_requesters`에 `orchestrator`가 있는 것이다.
  `*_chat_*`는 사람 대화용, `receive_*`·`record_*`는 부서 간 기록용이라 **의뢰로 받지 않는다**.
- 읽기(read) 일은 승인 없이 바로 시켜도 된다. 상태를 바꾸는 일(A·B·C)은 안전 관문과 승인을 거친다.
  재시작 뒤 이어가기도 이 구분을 쓴다(`work_requests.py`: 읽기는 다시 시키고, 그 밖에는 사람 확인).

## 부서와 서비스

| 부서 | 서비스 id |
| --- | --- |
| 총괄 | orchestrator |
| 고객지원 | inquiry, claims, settlement, reviews |
| 마케팅 | coupon |
| 재무 | finance |
| 데이터분석 | data |
| 인사 | handover(화면에서 접힘), org |
| 아카이브 | decisions |
| 기획 | planning |
| 개발 | dev |
| 인프라 | incident (hosts는 일·카드 없음) |

## 의뢰로 바로 쓸 만한 읽기 일 (총괄 호출 가능, 상태 변경 없음)

| 부서 | 일 | 의뢰 예시 |
| --- | --- | --- |
| 데이터분석 | `sales_analysis` | "이번 달 매출 분석해 주세요" |
| | `kpi_summary`, `activity_report` | "AI 처리 성과 요약해 주세요" |
| | `anomaly_detection`, `impact_analysis`, `root_cause_hypotheses` | "어제 결제 실패가 튄 곳이 있나요" |
| | `segment_*`, `basket_pairs`, `retention`, `channel_value` | "30대 남성이 즐겨 사는 책" |
| | `event_proposal` | "12월 이벤트 제안해 주세요" |
| | 트렌드 계열(`trend_scan`, `keyword_trend`, `video_explorer` 등) | "요즘 뜨는 키워드" |
| | `campaign_effect`, `campaign_result`, `video_performance` | "지난 캠페인 효과 있었나요" |
| | `trend_report_create` (오래 걸림, 자체 큐) | "경쟁사 대비 트렌드 보고서 만들어 주세요" |
| 고객지원 | `list_inquiries`, `inquiry_overview`, `feedback_overview` | "답변 대기 문의 보여 주세요" |
| | `list_claims`, `claim_stats`, `book_issue_stats` | "이번 달 환불 클레임 몇 건?" |
| | `seller_settlement*` | "지난주 판매자 정산서" |
| | `list_at_risk_customers` | "이탈할 고객 목록" |
| 재무 | `closing_report`, `finance_insights`, `review_campaign_budget` | "이번 달 손익 정리해 주세요" |
| 마케팅 | `list_active_notices`, `list_campaign_briefs`, `draft_notice`, `draft_sns_post`, `build_campaign_link` | "점검 공지 초안 써 주세요" |
| 인프라·개발 | `diagnose_incident`, `verify_recovery`, `find_related_prs`, `list_dev_outcomes`, `list_in_progress` | "이번 주 개발 결과" |
| 아카이브·인사 | `find_similar_cases`, `list_cards`, `team_headcount` | "다음 달 팀별 인원" |

데이터분석 카드는 54개 일이 있고 거의 전부 읽기이며 `handoff`가 꺼져 있다(서비스가 꺼져 있으면 사람
할 일함으로 넘기지 않고 `service_disabled`로 거절한다).

## 상태를 바꾸므로 승인이 필요한 일 (총괄 호출 가능)

| 부서 | 일 (등급) | 의뢰 예시 |
| --- | --- | --- |
| 기획 | `create_ticket`(A), `withdraw_ticket`(A) | "회원 등급 기능 만들어 주세요" (개발·기능 요청의 기본 창구) |
| 마케팅 | `publish_notice`(A), `start_campaign`(B), `generate_media`(B), `publish_sns`(C, 항상 사람 승인) | "인스타에 올려 주세요" |
| 고객지원 | `create_claim`, `request_compensation`, `send_reply`, `set_inquiry_status`, `request_change`(A), `request_seller_settlement`(A, 지급 확정은 C로 보류될 수 있음) | "주문 환불 접수해 주세요" |
| 인프라 | `rollback_deployment`(B) | "이 배포 되돌려 주세요" |
| 데이터분석 | `event_proposal_send`(A) | "이 제안 기획에 넘겨 주세요" |

## 의뢰로 받지 않는 일

- 부서 간 기록·응답용: `receive_*`, `record_*`, `answer_planning_question`, `receive_improvement_note`
- 사람 화면 전용: 고객지원 `decide_claim`·`purge_*`·`reveal_original`, 인사 `decide_org_change` 등
- 사람 대화용: `orchestrator_chat_*`, `*_chat_*`
- 장애 대응 자동 판단용: `release_fault`, `link_incident`, 마케팅 장애 SNS 계열
- 인사 handover는 화면에서 접힌 서비스라 의뢰 대상에서 뺀다

## 의뢰 흐름에 쓸 연결 (데이터분석 → 기획 → 마케팅)

- 데이터분석 `trend_report_create`는 총괄 호출 가능(읽기). 입력은 `request_text` 또는 product·target·competitors·keywords.
  오래 걸려 자체 큐로 돌고 끝나면 `data.trend_report_ready` 이벤트를 낸다.
- 기획 `receive_insight`는 총괄 호출 가능(A). 영상 제작안(`VideoPlan`) 연결은 기획 쪽 작업이다.
- 마케팅 영상·이미지 제작은 `generate_media`(B)다. 영상 제작은 데이터분석에는 없다.

## 확인하지 못한 것

- 안전 관문(safety) 서버의 일 목록(에이전트 카드가 없다)
- `market_coverage`, `finance_insights`의 입력 모양(카드에 적힌 입력 이름이 실제와 맞지 않는다)
- 실제 부서가 A2A 긴 작업(`WORKING`·`task.progress`·`task.completed`)을 쓰는 곳: 계약과 시험용 가짜 부서에만 있고 실제 도메인에서는 찾지 못했다(전수 검색은 못 함). 데이터분석 쪽 PR이 오기 전까지는 `trend_report_create`를 이 방식으로 받을 수 없다.
- `purge_customer_data`(registry C)와 카드 등급이 일치하는지
- `services.py`는 서비스 15개를 정의하는데 주석·CLAUDE.md는 13개라고 적혀 맞지 않는다

## 일마다 부서에 보낼 입력 칸 (17번 조사, 2026-11-20)

총괄이 단독 의뢰를 부서에 시킬 때 지금은 입력을 비워(`{}`) 보낸다(`request_runner.py` `_input_of`:
앞 단계 번호가 없으면 `{}`). 아래는 일 목록 18개(`request_tasks.py`)가 부르는 skill이 실제로 받는 입력이다.
코드를 읽어 정리했고 코드는 바꾸지 않았다. 기준 `dev` c96e614d. 확인하지 못한 것은 "미확인"으로 적었다.

### 공통 사실

- 계약 모델은 `extra="forbid"`다(`shared/contracts/.../schemas/common.py`). 그래서 `input`에 모르는 칸
  (예: `request_text`)을 끼워 보내면 `invalid_input`으로 거부된다. `trend_report_create`만 `request_text` 칸이 있다.
- 부서 handler는 `request.input`만 검증한다. 고객 문장을 `untrusted`에서 읽는 skill은 조사한 20개 중 없다.
  `untrusted`는 LLM 프롬프트의 데이터 블록에 넣을 때만 쓰인다(`shared/agent-template/.../llm.py`).
  따라서 **고객 문장을 쓰려면 `input`의 알맞은 칸에 넣어야 한다**(`untrusted`에만 두면 부서가 못 본다).
- 빈 입력 `{}`의 거부 모양은 부서마다 다르다. data·support·finance·marketing·dev·planning은
  `FAILED` + `invalid_input`, hr·archive는 `REJECTED` + `INVALID_INPUT`이다.
- data의 `sales_analysis`·`segment_question`·`event_proposal`은 `input.tenant_id`(기본 `shop-demo`)가 호출
  `meta.tenant_id`와 다르면 `invalid_input`이다. `tenant_id`는 보내지 말고 기본값을 쓴다.

### 입력 없이(`{}`) 돌아가는 일

| 일 | skill | 비고 |
| --- | --- | --- |
| 매출 분석 | `sales_analysis` | 기본 최근 7일. `period`는 today·7d·30d만 |
| 문의 현황 정리 | `inquiry_overview` | handler가 입력을 검증하지 않음. 기간 필터 없음 |
| 클레임 현황 정리 | `claim_stats` | 입력을 읽지 않음. 기간 필터 없음 |
| 판매자 정산 조회 | `seller_settlement` | `period`(ISO 주) 없으면 기본 주. 판매 서버 자료가 없으면 `shop_unavailable` |
| 손익 정리 | `closing_report` | 기본 오늘 일 손익. `period`는 day·week·month |
| 트렌드 보고서 | `trend_report_create` | `{}`면 `caller="human"`이라 동기로 끝난다(아래 참고) |

### 입력이 필요한 일

| 일 | skill | 필수 입력 | 고객 문장에서 채우기 |
| --- | --- | --- | --- |
| 고객층 분석 | `segment_question` | `text`(1~200자) | 문장을 그대로 `text`에 넣는다 |
| 비슷한 과거 기록 | `find_similar_cases` | `query` | 문장을 그대로 `query`에 넣는다 |
| 트렌드 보고서 | `trend_report_create` | 없음(전부 선택) | 문장을 `request_text`(4000자)에 넣는다. 제품·타깃·형식은 부서가 LLM 1회로 추정 |
| 이벤트 제안 | `event_proposal` | `month`(YYYY-MM) | 규칙: "12월"·"다음 달"을 변환 |
| 팀별 인원 | `team_headcount` | `month`(YYYY-MM) | 규칙: "이번 달"·"다음 달"을 변환. 기록 시작 전 달이면 실패 |
| 지표 이상 점검 | `anomaly_detection` | `window_from`·`window_to`(시간대 있는 시각) | 규칙: 어제·오늘·지난 N시간을 변환. 기간이 없으면 기본 24시간 같은 정책 필요 |
| 캠페인·영상 성과 | `campaign_result` | `campaign_id` | 문장에서 못 뽑는다. 고객에게 묻거나 목록에서 고른다 |
| 개발 진행 현황 | `list_in_progress` | `owner_id`(명부의 member_id) | 못 뽑는다. 로그인 사용자나 담당자 선택 필요 |
| 공지 초안 | `draft_notice` | `kind`, `facts`(장애 시각·product_id 등) | 종류는 키워드로 가능. 시각·번호는 못 뽑는다. 결과는 고정 문안 |
| SNS 글 초안 | `draft_sns_post` | `notice_id`·`coupon`·`proposal_id` 중 정확히 하나 | 못 뽑는다. 자유 문장 주제를 받는 칸이 없다 |
| 환불·교환 접수 | `create_claim` (A) | `buyer_id`, `customer_name`, `order_id`, `type`, `reason` | type·reason은 키워드로 대충 가능. 주문·구매자는 고객에게 묻거나 로그인 정보에서 |
| 기능 개발 요청 | `create_ticket` (A) | `title`, `evidence` + `idempotency_key` | 문장을 `title`로, `evidence`는 고정 문구로 가능. 외부 문구가 `input`에 들어가는 경계는 설계 판단 필요 |

### 앞 단계 결과에서 받는 일 (홍보 영상 흐름)

| skill | 입력 | 받는 곳 |
| --- | --- | --- |
| `trend_report_insight` (읽기) | `report_id` | 앞 단계(`trend_report_create`)의 결과 번호 |
| `receive_insight` (A) | `insight_id`·`title`·`summary`·`skill`·`facts`(1~12개) 등 + `idempotency_key` | `trend_report_insight`의 `output`을 그대로 |
| `generate_media` (B) | `post_draft_id`, `media_kind`, `scene_template_id` | 앞서 만든 SNS 초안과 템플릿. 문장에서 못 뽑는다 |

### 알아둘 점

- **채우는 방법별로 나누면:** 문장을 그대로 넣는 일 3개(`segment_question`, `find_similar_cases`,
  `trend_report_create`), 기간을 규칙으로 바꾸는 일 6개(`sales_analysis`, `closing_report`, `event_proposal`,
  `team_headcount`, `seller_settlement`, `anomaly_detection`), 고객에게 되묻거나 고르게 해야 하는 일
  6개(`campaign_result`, `create_claim`, `list_in_progress`, `draft_notice`, `draft_sns_post`, `generate_media`),
  앞 단계 결과를 받는 일 2개(`trend_report_insight`, `receive_insight`)다.
- **홍보 영상 마지막 단계(`generate_media`)는 입력이 앞 흐름에 없다.** 15-3번에서 3단계까지 연결한 뒤에도
  `post_draft_id`와 `scene_template_id`는 비어 있어 이 단계는 입력 채우기(19번)나 기획 쪽 결과가 필요하다.
- **`create_ticket`의 요청자 불일치:** 부서 카드 `allowed_requesters`에는 `orchestrator`가 있지만 안전 규칙
  `registry.toml`의 `create_ticket`은 `requesters = ["planning", "dev"]`다. 총괄이 부를 때 안전 관문이
  거절하는지는 미확인이다(시험으로 확인해야 한다).
- **`create_claim` 설명 칸:** 입력 모델 독스트링은 "사유 설명은 `untrusted.description`"이라 하나 handler에서
  읽는 코드를 못 찾았다(미확인).
- **`trend_report_create`의 `caller`:** 긴 작업(`WORKING` → `task.progress` → `task.completed`)으로 받으려면
  입력에 `caller="orchestrator"`를 넣어야 한다(기본 `"human"`은 동기 응답). 총괄은 이 값을 아직 보내지 않는다.
  완료 결과는 `output.report_id`이며 `request_tasks.py`의 `ref_key="report_id"`와 맞는다. 접수 응답은
  `task_id`(`tsk_…`)와 `expected_seconds`(최소 120)이고 진행 알림은 120초마다 온다.
- **지금 입력이 비어도 돌아가는 단독 의뢰는 5개**(`sales_analysis`, `inquiry_overview`, `claim_stats`,
  `seller_settlement`, `closing_report`)다. 나머지 단독 의뢰는 입력 부족으로 부서가 거절한다. 18번(공통 입력
  채우기)과 19번(부서별 칸 연결)이 이 틈을 메운다.

## 대화방 인프라 의뢰 (2026-11-26)

- 읽기만 연다: `current_incidents`(지금 장애, DejaFix 현재 장애), `latest_deployments`(배포 상태), `diagnose_incident`(원인 진단, 원인 후보만). 모두 `infra` 부서 read 단계이고 승인 없이 돈다.
- 롤백(`rollback_deployment`, B)·장애 해제(`release_fault`)·배포 실행은 대화방 의뢰로 열지 않았다. 의뢰 문장에서 서비스·버전을 안전하게 정할 길이 없고, 승인 뒤 바로 운영 롤백이 실행되는 길이 생긴다. 필요하면 별도 설계(사건 맥락 + 전용 승인 카드)가 먼저다.
