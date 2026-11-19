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
