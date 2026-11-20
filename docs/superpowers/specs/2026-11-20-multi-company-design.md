# 사이트 하나에 여러 회사 — 설계

2026-11-20 · 합의: 예진·태윤·재현(11/20). 재헌 님 의견은 아직 · 단계안: 태윤 님
이 문서는 설계안이다. 코드는 바꾸지 않았다.

바꾸는 문서: `2026-11-19-install-and-connection-architecture.md`(#1381·#1385)의 "고객사 설치(회사마다 한 벌)"와 7장 "하지 않는 것: 한 설치에 여러 고객사".
그대로 쓰는 문서: `2026-11-19-human-session-tokens-design.md`(사람별 토큰), 암호화 키 저장소(#1468, `backend/shared/secret-store`).
참조만: 2단계(#1457, 별도 문서).

## 1. 목표와 원칙

목표
1. 사이트 하나(app.axcellent.cloud)에서 계정 → 회사 → 그 회사가 연결한 서버 순으로 일한다. 누비투어·스터디온에듀를 설치를 따로 띄우지 않고 붙인다
2. 회사 A 계정은 회사 B의 문의·보고서·키·설정을 어떤 경로로도 읽거나 쓰지 못한다(없는 것처럼 404)
3. 고객 원본(주문·고객·문의 원문)을 AXcellent에 쌓지 않는다. 남기는 것은 회사 ID를 붙인 비식별 일별 집계와 처리 기록(번호·상태·답변)뿐

원칙
- **회사 ID 하나**: 모든 저장 줄, 모든 A2A 요청, 모든 세션에 같은 회사 ID가 붙는다. 회사 ID가 없으면 쓰지도 읽지도 않는다(fail-closed, 4단계부터)
- **회사 ID는 사람이 보낸 값을 믿지 않는다**: 화면 요청의 회사는 세션 토큰에서, A2A 요청의 회사는 보낸 쪽 메타에서(보낸 쪽은 자기가 받은 요청이나 세션에서) 꺼낸다. 본문·쿼리의 회사 값은 무시한다
- **계약은 추가만**: 이미 있는 `RequestMeta.tenant_id`를 회사 ID 칸으로 쓴다. 새 이름(`company_id`)을 따로 만들지 않는다
- **고객사 데이터 운영 안 함**(팀 결정)은 그대로다. 원본은 회사 서버에서 그때그때 읽고, 우리 쪽에는 집계와 처리 기록만 둔다
- 1단계는 동작이 바뀌지 않는다(회사가 캣북스 하나). 막는 동작은 4단계부터

## 2. 지금 회사가 어디서 오는가 (조사)

회사를 가리키는 값이 세 개로 갈라져 있다.

| 값 | 지금 값 | 어디서 | 쓰는 곳 |
| --- | --- | --- | --- |
| `tenant_id` | `shop-demo` | `ax_contracts.schemas.common.TENANT_ID_DEFAULT`. dashboard-api는 `os.getenv("TENANT_ID", 기본값)`(`envelope.py:64`, `handoffs.py:155`) | A2A `RequestMeta`·`Envelope`·`HandoffTask`·`ExecuteRequest`, 응답 `meta.tenant_id`(auth/status 등), support PG 표, sales.db 표, 공통 로그 |
| 회사 설정 폴더 id | `catbooks` | `AX_TENANT` → 회사 연결 파일의 `tenant` → 없음 (`ax_agent_template/tenant.py`) | 조직도·지표 카탈로그·트렌드 설정(`config/tenants/<id>/tenant.toml`) |
| 키 저장소 tenant | `default` | `AX_SECRET_DEFAULT_TENANT` (`ax_secret_store/resolver.py`) | `secrets`·`pending`·`history` 표의 `tenant` 칸 |

그 밖에 회사 하나를 가정하는 곳
- `agent-template/server.py:244`: 요청에 `tenant_id`가 없으면 `shop-demo`로 보고 통과한다(사실상 검사 없음)
- `call_agent`(`client.py`): `req.meta`를 그대로 복사해 보낸다. 회사 ID를 실을 자리는 이미 있고, 받은 요청의 값을 이어 주기만 하면 된다
- `company_connection.py`: 설치에 연결 파일 하나(`AX_COMPANY_CONNECTION_FILE`). `config.agent_url("shop-api")`가 회사 구분 없이 이 파일의 주소를 준다
- `site_adapter/`: 변환기 폴더 하나(`AX_SITE_ADAPTER_DIR`, 기본 `.data/site_adapter/`)에 `mapping.json`·`bookkeeping.jsonl`. 변환기 경로 `/site-adapter`에 회사가 없다
- `shop_events.py`: 회사 키 `AX_COMPANY_KEYS="<tenant_id>:<키>"`와 봉투 `tenant_id` 대조 — **이미 여러 회사 모양**이다. 이 방식을 넓힌다
- `accounts.py`: 계정에 회사가 없다. 역할(`operator`·`admin`·`member`)이 설치 전체 단위. 다만 키 담당 `secret_tenants`(담당 고객사 목록)는 이미 회사 목록 모양
- `human_auth.py`: 세션 토큰 내용 = 계정 id·이름·역할·세대·만료. 회사가 없다
- 수집기·주기 작업(`sales_collect`, `youtube_collect`, 일간 리포트 저장, `campaign_update`): 회사 하나를 전제로 돈다

## 3. 저장소 전수 조사

조사 방법: `backend/`에서 `.data` 경로 상수, `*_DB`·`*_FILE`·`*_DIR`·`*_STORE` 환경변수, `sqlite3.connect`, `*.jsonl`, `run_all.sh`의 `export` 줄을 grep. `.env*`·`backend/.data/*` 파일은 열지 않았다(경로 문자열만 봄). eval 스크립트(`domains/*/eval`)와 테스트는 뺐다.

"원본" 칸: **예** = 고객사 고객의 주문·고객·문의 원문을 담는다 / 일부 = 구매자 가명 id나 문의 발췌가 섞일 수 있다 / 아니오 = 회사 내부 작업·설정·공개 데이터.
"붙이는 방법": **칸** = SQLite·PG 표에 `tenant_id` 칸(이미 있으면 "있음") / **폴더** = 파일을 `.data/companies/<회사 ID>/` 아래로 옮긴다 / **키 저장소** = `ax-secret-store`의 `tenant`.

### 3.1 공통층 (shared)

| 서비스 | 파일(환경변수) | 무엇 | 원본 | 붙이는 방법 |
| --- | --- | --- | --- | --- |
| dashboard-api | `accounts.json`(`AX_ACCOUNTS_FILE`) | 계정·역할·비밀번호 해시 | 아니오 | 계정은 회사 밖(전역), 줄에 `memberships[{company_id, role}]` 추가 (5장) |
| dashboard-api | `connection_keys.json`(`AX_CONNECTION_KEYS_FILE`) | 연결 키(설치 키) | 아니오 | 줄에 `company_id` 추가 (키 하나 = 회사 하나) |
| dashboard-api | `handoffs.db`(`AX_HANDOFF_DB`) | 사람 할 일함 | 일부(문의 발췌) | 칸 있음. 조회의 회사 값을 env → 세션으로 |
| dashboard-api | `office_room.db`(`AX_OFFICE_ROOM_DB`) | AI 오피스 대화방 | 일부 | 칸 |
| dashboard-api | `site_adapter/mapping.json`(`AX_SITE_ADAPTER_DIR`) | 회사 API ↔ 규격 매핑 | 아니오 | 폴더 |
| dashboard-api | `site_adapter/bookkeeping.jsonl` | 접수 동기화 장부(항목 이름만) | 아니오 | 폴더 |
| agent-template | `company_connection.json` + `.history.jsonl`(`AX_COMPANY_CONNECTION_FILE`) | 회사 서버 주소·종류·설정 폴더 id | 아니오 | 폴더 (6장) |
| agent-template | `tool_connections.json`(`AX_TOOL_CONNECTIONS_FILE`) | 도구 연결(토큰 포함) | 아니오(비밀) | 폴더, 토큰은 키 저장소로 |
| agent-template | `enabled_services.json`(`AX_ENABLED_SERVICES_FILE`) | 켠 서비스 목록 | 아니오 | 폴더 (회사마다 켠 서비스가 다름) |
| agent-template | `common_log.db`(`AX_COMMON_LOG_DB`) | 공통 로그 | 일부 | 칸 있음(계약 `common_log.tenant_id`) |
| agent-template | `llm_usage.db`(`AX_LLM_USAGE_DB`) | LLM 사용량 | 아니오 | 칸 있음(계약 `llm_usage.tenant_id`) |
| agent-template | `notes/<서비스>.db`(`AX_NOTES_DIR`) | 개선 노트 | 아니오 | 칸 |
| agent-template | `revoked_sessions.json`·`session_generations.json` | 끊은 세션·세대 | 아니오 | 전역 그대로(계정 단위) |
| safety | `safety_state.db`(`SAFETY_STATE_DB`), `cost_policy.json`, `safety_chat.db` | 승인 보류·실행 기록·비용 정책·대화 | 일부(보류 건 입력) | 칸. 비용 정책은 회사마다 → 폴더 |
| safety | `connection_holds.json`(`SAFETY_CONNECTION_HOLDS_FILE`) | 연결 보류 | 아니오 | 폴더 |
| secret-store | `AX_SECRET_DB_FILE` | 암호화 비밀값 | 아니오(비밀) | 칸 있음(`tenant`). 값을 회사 ID로 맞춘다 |
| memory | Postgres+pgvector(`PgCardStore`) | 회사 기억 카드 | 일부 | 칸 있음(`tenant` 24곳). 조회 필터가 빠짐없는지 5단계에서 확인 |
| workflow-core | `PLANNING_STATE_DIR` 아래 파일 | 업무 흐름 상태 | 아니오 | 폴더 |
| Redis | 이벤트 버스 | 이벤트 봉투 | 일부 | 봉투 `tenant_id` 있음. 구독 쪽 필터 확인 |

### 3.2 부서 AI (domains·services)

| 서비스 | 파일(환경변수) | 무엇 | 원본 | 붙이는 방법 |
| --- | --- | --- | --- | --- |
| **data** | **`sales.db`(`DATA_SALES_DB`)** + `sales_collect` 5분 복사 | **주문·주문 줄·환불·고객(가명 buyer_id·가입 시각·속성)** | **예** | 칸 있음. **3단계에서 없앤다** → 일별 집계로 |
| data | `data_reports.db`(`DATA_REPORT_DB`) | 부서별 일간 리포트(더할 수 있는 값) | 아니오(집계) | 칸 있음 |
| data | `today_board.db`, `brief_lines.db`, `insight_brief.db`, `trend_reports.db`, `trend_responses.db`, `explain_cache.db`, `data_chat.db`, `event_proposals.db`, `idea_reports`(`DATA_IDEA_REPORTS_DB`), `promo_concepts.db`, `campaign_sends.db`, `data_llm_budget.db` | 회사 화면·보고서·대화·캐시 | 아니오(일부 캐시에 매출 숫자) | 칸 (회사 설정·지표가 달라 섞이면 안 됨) |
| data | `keyword_pool.db`, `domain_profile.db`, `subtopics.db`, `source_plan.db`, `source_probe.db`, `topic_categories.db`, `trend_age.db`, `trend_memes.db`, `video_performance.db`, `video_explorer.db`, `fresh_shorts.db`, `shorts.db`, `popular_videos.db`, `trending_snapshots.db`, `youtube_snapshots.db`, `youtube_quota.db`, `ig_*.db`, 네이버 캐시 3개, `replay/` | 공개 트렌드·키워드 수집과 캐시 | 아니오(공개) | 회사 키워드로 만든 것(`keyword_pool`·`domain_profile`·`subtopics`·`source_plan`·`topic_categories`)은 칸. 공개 수집 캐시(유튜브·네이버·인스타·할당량)는 공유 그대로 |
| **support** (inquiry·claims·reviews·coupon·settlement 서비스) | **Postgres(`SUPPORT_PG_DSN`) `inquiries`·`draft_events`·`privacy_audits`·`support_chats`** | **문의 대화(메시지 원문)·초안·개인정보 점검** | **예** | 칸 있음. **3단계: 원문 칸을 비우고 번호·상태·답변 기록만** |
| support | PG `claims`·`incident_compensation_items`·`compensation_sequences` | 클레임·보상(주문 id·buyer_id) | 일부 | 칸 있음 |
| support | PG `change_requests`·`proposal_decisions`·`planning_questions`·`ideas`·`inquiry_results` | 처리 기록 | 아니오 | 칸 있음 |
| support | `support_settlement.json`(`SUPPORT_SETTLEMENT_FILE`) | 정산 묶음 | 아니오(판매자 단위) | 폴더 |
| support | 메모리 `OrderStore`(`settlement_books.py`) | 주문 캐시 | 일부 | 메모리 키에 회사 ID |
| finance | `finance_state.json`(`FINANCE_STATE_FILE`), `finance_exports/`, `finance_chat.db` | 쿠폰·환불 장부·결산·내보내기·대화 | 일부(buyer_id) | 폴더 |
| finance | 메모리 `_STORE = OrderStore()`(`books.py`) | 주문 모음 | 일부 | 메모리 키에 회사 ID |
| marketing | `marketing_briefs.db`, `marketing_media_ledger.db`, `marketing_sns_pending.db`, `marketing_sns_drafts.db`, `marketing_chat.db`, `MARKETING_CAMPAIGNS_DB`, `MARKETING_MEDIA_DIR`, `MARKETING_COST_LIMITS_FILE`, `MARKETING_VIDEO_PROVIDERS_FILE` | 브리프·미디어·SNS 초안·비용 한도 | 아니오 | DB는 칸, 미디어 폴더·설정 파일은 폴더 |
| planning | `PLANNING_STATE_DIR`(대화·노션 연결 `notion_store`) | 기획 대화·노션 토큰 | 아니오(토큰은 비밀) | 폴더, 토큰은 키 저장소로 |
| dev | `llm_key.json`(`DEV_LLM_KEY_STORE`) | **고객사 LLM 키** | 아니오(비밀) | **키 저장소로 옮김** (7장) |
| dev | `vcs_connections.json`(`PLANNING_VCS_STORE`) | 형상관리 연결(토큰) | 아니오(비밀) | 폴더, 토큰은 키 저장소로 |
| infra-dejafix | `infra_chat.db`, `INFRA_DEPLOYMENTS_FILE` | 장애 대화·배포 기록 | 아니오 | 칸 / 폴더 |
| hr | `HR_DB_PATH` SQLite | 인사 기록(고객사 직원) | 아니오(회사 내부지만 민감) | 칸 있음(15곳) |
| archive | `ARCHIVE_DB_PATH` + `*_vectors.db` | 사내 문서·벡터 | 아니오 | 칸 있음(6곳). 벡터 파일도 칸 |
| orchestrator | `orchestrator.db`(`ORCHESTRATOR_DB`), `ORCHESTRATOR_CHAT_DB` | 의뢰·장애 기록·총괄 대화 | 일부(고객 문장) | 칸 (`IncidentStore`엔 아직 없음, 계약 `orchestrator_incidents.tenant_id`는 있음) |

### 3.3 고객 원본을 쌓는 곳 (3단계 대상)

1. data `sales.db` — 주문·줄·환불·고객 속성 전체를 5분마다 복사(`sales_collect`)
2. support PG `inquiries`(+ `draft_events`·`privacy_audits`·`support_chats`의 본문) — 문의 원문
3. 섞일 수 있는 곳(원본 저장이 목적은 아니지만 발췌가 들어감): `handoffs.db`, `office_room.db`, `orchestrator.db`, `safety_state.db`(보류 건 입력), `common_log.db`, memory 카드, finance·support 메모리 `OrderStore`, `finance_state.json`의 buyer_id

## 4. 회사 ID와 계약에 더할 칸 (추가만)

- **회사 ID = 지금의 `tenant_id` 칸의 값.** 새 칸 이름을 만들지 않는다. 계약 `RequestMeta.tenant_id`·`Envelope.tenant_id`·`HandoffTask.tenant_id`·`ExecuteRequest.tenant_id`·`data_access/*`의 `tenant_id`가 이미 있다
- 값 규칙: 영어 소문자·숫자·`-`, 3~40자(예: `catbooks`, `nubi`, `studyon`). 회사 설정 폴더 id·키 저장소 tenant와 **같은 값**으로 맞춘다
- 캣북스의 값: 옮기는 동안 `shop-demo`를 캣북스의 별칭으로 둔다(열린 질문 1)
- `TENANT_ID_DEFAULT`는 지우지도 바꾸지도 않는다(계약 규칙). 4단계부터 서버가 "기본값으로 채워진 요청"을 거절해서 기본값이 실제로 쓰이지 않게 한다(`model_fields_set`에 `tenant_id`가 없으면 거절)

더할 것
| 어디 | 더할 칸 | 왜 |
| --- | --- | --- |
| `schemas/common.py` | `TENANT_ID_PATTERN`(상수), `TenantId`(Annotated 형식) | 값 규칙을 한 곳에서. 기존 칸 타입은 그대로 `str` |
| `schemas/message.py` `ErrorCode` | `TENANT_REQUIRED`, `TENANT_MISMATCH` | 회사 ID 없음 / 토큰의 회사와 요청 회사가 다름 (기존 `MISSING_CONTEXT`는 그대로 둔다) |
| 새 `schemas/company.py` | `Company`(company_id·이름·업종·사이트 주소·만든 시각), `Membership`(company_id·role) | 회사 목록·계정의 회사 응답 |
| `data_access/` 새 파일 | 일별 집계 계약 — 모양은 재현 님 설계를 따른다. 이 문서는 "모든 줄에 `tenant_id`, 원본 행 없음"만 정한다 | 3단계 |
| `HandoffCreate`, `common_log`, `llm_usage` 등 기존 | 바꾸지 않음 | 이미 `tenant_id`가 있다 |

## 5. 세션·계정·역할

### 5.1 계정
- 계정은 회사 밖(전역)에 산다. **한 계정은 한 회사에만 소속된다**(11/20 예진 결정: 진짜 고객사처럼. 가상 고객사 데모도 회사마다 계정을 따로 두고 로그인한다). `memberships`는 목록 모양이지만 항목은 하나
- `accounts.json` 줄에 `memberships: [{company_id, role}]` 추가. `role`은 회사 단위 `admin`·`member`
- 기존 `role` 칸: `operator`(본사 운영자)는 전역 역할로 남긴다. `admin`·`member`는 옮기는 동안 "캣북스의 그 역할"로 읽는다(1단계 이전 코드). 칸은 지우지 않는다
- `secret_tenants`(키 담당 고객사 목록)는 `memberships` 안에서만 의미 있게 줄인다(담당 회사가 자기 회사 밖이면 무시)

### 5.2 세션 토큰
- 로그인 응답에 `companies: [Membership]`. 소속 회사 하나로 바로 서명한다
- 회사 바꾸기 API(`POST /api/dashboard/auth/company`)는 만들지 않는다(11/20, #1543 닫음). 같은 경로의 `GET`은 로그인 전 화면용 회사 공개 정보(#1397) 그대로
- 토큰 내용에 `cid`(회사 ID)·`crole`(회사 역할) 추가. 토큰 형식은 `axs1` 그대로(칸 추가만, 옛 토큰은 `cid` 없음 → 1단계에서는 캣북스, 4단계부터 거절)
- `human_auth.Human`에 `company_id`·`company_role` 추가. `require_company_role("admin")` 의존성 추가
- operator는 회사 토큰이 없다. 고객사 화면에 들어가려면 고객사 관리자가 연 **지원 접근**(기한 있음)을 써야 한다(#1381 4.6 그대로). 지원 접근도 `cid`가 박힌 토큰으로 준다

### 5.3 화면 (11/19 결정 그대로)
- 관리자·일반회원이 다른 화면은 둘뿐: 시스템 관리 › **회사 연결**, **회원·서버 관리**. 둘 다 `require_company_role("admin")`
- 나머지 화면은 회사 안 누구나 같다. 화면은 머리글에 지금 회사(이름·파비콘, #1397). 회사 고르기는 없다
- 화면은 요청에 회사 값을 보내지 않는다. 서버가 토큰에서 꺼낸다

### 5.4 dashboard-api → 부서 AI
- dashboard-api가 A2A를 만들 때 `meta.tenant_id = session.company_id`. 지금의 `os.getenv("TENANT_ID")`(`envelope.py`, `handoffs.py`)를 세션 값으로 바꾼다
- 응답 `meta.tenant_id`(auth/status 등)도 세션의 회사. 로그인 전 응답에는 넣지 않는다(4단계)

## 6. A2A와 회사 연결

### 6.1 A2A에 회사 ID 싣기
- 자리: `RequestMeta.tenant_id`(이미 있음). `call_agent`는 `req.meta`를 복사하므로, 부서 AI가 자기 받은 요청의 `meta`로 새 요청을 만들면 자동으로 이어진다. **새 요청을 `TENANT_ID_DEFAULT`로 만드는 곳**을 찾아 받은 meta에서 이어 주게 고친다(1단계에서 grep 목록을 만든다)
- 주기 작업(수집기·일간 저장·캠페인 결과 보내기)은 받은 요청이 없다 → 회사 목록(`companies`)을 돌며 회사마다 meta를 만든다
- 받는 쪽(`agent-template/server.py`) 검사, 4단계부터
  1. `tenant_id`가 요청에 명시돼 있지 않으면 `rejected` + `TENANT_REQUIRED`
  2. 회사 목록에 없는 값이면 `rejected` + `TENANT_REQUIRED`
  3. 사람 쓰기(`execute()` 관문)는 사람 토큰의 `cid`와 `meta.tenant_id`가 다르면 `TENANT_MISMATCH`
- 에이전트 토큰(`AX_AGENT_TOKENS`)은 회사와 묶지 않는다(서버는 하나, 회사는 요청마다). 대신 이어 주기를 지키는지 5단계 시험으로 본다(열린 질문 3 참고)
- 안전 장치(`ax_safety`)·이벤트 봉투도 `tenant_id`를 이어 받는다. 승인 대기 목록은 세션의 회사로만 보인다

### 6.2 회사 연결(`company_connection.py`)
- 파일 하나 → 회사마다 하나: `.data/companies/<회사 ID>/company_connection.json`(+ history). `load(company_id)`로 바꾼다
- `config.agent_url("shop-api")` → `shop_api_url(company_id)`. AI가 회사 서버를 부를 때 지금 요청의 `meta.tenant_id`로 주소를 고른다. 회사 ID 없이 부르면 주소를 주지 않는다(4단계)
- 회사 서버에 보내는 토큰: 지금은 AI 공용 토큰. 회사마다 다른 토큰을 키 저장소(`tenant=회사 ID`, 예: `SHOP_API_TOKEN`)에 둔다
- `AX_TENANT`·연결 파일의 `tenant` → 회사 설정 폴더는 `config/tenants/<회사 ID>`(템플릿에서 복사한 것)로. `tenant.setting(이름)`에 `company_id` 인자 추가. 환경변수 우선 규칙은 설치 하나일 때만 의미가 있어 4단계에서 끈다
- `AX_COMPANY_SHOP_ALLOWED_HOSTS`는 설치 운영자 값으로 전역 유지

### 6.3 변환기(`site_adapter/`)
- 폴더 `.data/companies/<회사 ID>/site_adapter/`
- AI가 부르는 경로 `/site-adapter/...`에 회사가 없다. 요청 헤더 `X-AX-Tenant`(에이전트 토큰과 같이) 또는 경로 `/site-adapter/<회사 ID>/...` 중 하나 — 경로 쪽을 추천(로그·nginx에서 보이고 헤더 누락이 없다). 받은 회사와 에이전트 요청의 meta가 다르면 거절
- 회사 API 주소(`adapter.base_url`)·인증 값은 회사 연결 파일과 키 저장소에서

## 7. AI 키·모델 (회사당 하나)

- 키: 키 저장소 `tenant=회사 ID`, 이름 `LLM_API_KEY`(공급자 이름 칸은 비밀이 아닌 회사 설정). dev의 `llm_key.json`(`DEV_LLM_KEY_STORE`)은 옮기고 읽기만 남긴다(옮긴 뒤 끔)
- 모델: 회사 설정(`tenant.toml` 또는 회사 폴더 설정)의 `llm.model`. 코드에 모델 이름을 적지 않는다(규칙)
- 부서 AI가 LLM을 부를 때 `get_secret("LLM_API_KEY", tenant=meta.tenant_id)`. 키 저장소 db 모드가 전제(`AX_SECRET_STORE=db`)
- 회사 키가 없으면 그 회사 AI 기능은 "키 없음"으로 멈춘다. 다른 회사 키나 설치 기본 키로 넘어가지 않는다(넘어가면 비용이 다른 회사로 간다)
- `llm_usage.db`·데이터 LLM 예산은 회사별로 센다(칸 있음)

## 8. 단계별 계획

담당 칸은 제안이다(도메인 담당 표가 레포에 없어 확인 필요). 재현 = 데이터 설계·일별 집계, 태윤 = 보안·단계안·서버, 재헌 = 서버·인프라, 예진 = 화면·기획.

### 1단계 — 모든 저장소에 회사 ID, 계정 → 회사 (동작 그대로)
바뀌는 것: 회사 목록이 생기고(캣북스 하나), 모든 저장 줄·파일이 회사 ID를 갖는다. 읽기·쓰기는 아직 "지금 회사 = 캣북스"로 채워서 동작은 같다.

| PR | 내용 | 영향 | 담당(제안) |
| --- | --- | --- | --- |
| 1-1 | 계약 추가(4장 표: `TenantId`, 오류 코드 2개, `Company`·`Membership`) | contracts | 태윤 |
| 1-2 | 회사 목록 `companies.json`(dashboard-api) + `company_path(company_id, 이름)` 공용 함수(agent-template). 캣북스 하나, 별칭 `shop-demo` | agent-template, dashboard-api | 태윤 |
| 1-3 | 계정 `memberships`, 로그인 응답 `companies`, 토큰 `cid`·`crole`, `Human.company_id`. 옛 토큰은 캣북스로 | dashboard-api, agent-template(`human_auth`) | 태윤 |
| 1-4 | 공통층 파일을 회사 폴더로 옮기기(회사 연결·도구 연결·켠 서비스·변환기·비용 정책). 옛 경로가 있으면 처음 켤 때 캣북스 폴더로 옮기는 이관 함수 | agent-template, dashboard-api, safety | 재헌 |
| 1-5 | data 저장소에 칸(3.2 data 줄). 기존 줄은 캣북스로 채움 | data | 재현 |
| 1-6 | support·orchestrator·marketing·finance·planning·dev·infra 저장소에 칸·폴더 | 각 도메인 | 도메인 담당 |
| 1-7 | 키 저장소 tenant `default` → `catbooks` 이관, dev LLM 키를 키 저장소로 | secret-store, dev | 태윤 |
| 1-8 | ~~화면: 머리글 지금 회사, 회사 고르기~~ 하지 않음(11/20): 한 계정은 한 회사. 머리글 지금 회사는 #1397에 이미 있음 | - | - |

### 2단계 — #1457에서 다룬다 (이 문서는 참조만)

### 3단계 — 일별 집계가 돌면 원본 저장 끄기
전제: 재현 님 일별 집계 설계가 들어가 캣북스에서 하루 이상 돌고, 매출 분석·리포트가 집계만 읽는 것을 확인.

| PR | 내용 | 영향 | 담당(제안) |
| --- | --- | --- | --- |
| 3-1 | 일별 집계 저장소·계약(회사 ID 붙임, 원본 행 없음). 경계: 집계는 "회사·날짜·차원 → 숫자"만, 가명 id·주문 id·문의 원문 없음. 작은 셀(예: 구매자 수 < k)은 숨김 여부를 재현 님이 정한다 | data, contracts | 재현 |
| 3-2 | 매출 분석·고객 분석이 `sales.db` 대신 집계를 읽기, 집계에 없는 질문은 회사 서버를 그때그때 조회 | data | 재현 |
| 3-3 | `sales_collect` 끄기(run_all·스케줄에서 뺌), `sales.db`는 지우지 않고 보관 후 사람이 지운다 | data, scripts | 재현·재헌 |
| 3-4 | `InquiryStore`: 문의 원문(메시지 본문·초안 본문)을 저장하지 않고 번호·상태·분류·답변 기록만. 화면·AI가 원문이 필요하면 회사 서버(또는 변환기)에서 그때 읽는다. 옛 줄의 원문 칸은 비우는 이관 | support | 지원 담당 |
| 3-5 | 섞일 수 있는 곳(3.3의 3) 점검: 발췌를 남기지 않거나 기한 뒤 지움 | dashboard-api, orchestrator, safety, memory | 태윤 |

### 4단계 — 회사 확인을 서버가 강제
| PR | 내용 | 영향 | 담당(제안) |
| --- | --- | --- | --- |
| 4-1 | dashboard-api: 모든 회사 경로에 세션 회사 필수, 응답 `meta.tenant_id` = 세션 회사, env `TENANT_ID` 읽기 제거 | dashboard-api | 태윤 |
| 4-2 | agent-template 서버: `tenant_id` 명시 안 됨·모르는 회사면 `TENANT_REQUIRED`, 사람 쓰기에서 `cid` ≠ meta면 `TENANT_MISMATCH` | agent-template | 태윤 |
| 4-3 | 새 요청을 기본값으로 만드는 곳 전부를 받은 meta 잇기로, 주기 작업은 회사 목록 돌기 | 전 도메인 | 도메인 담당 |
| 4-4 | 회사 연결·변환기·설정이 회사 ID 없이는 값을 주지 않음, `AX_TENANT` 우선 끄기 | agent-template, dashboard-api | 재헌 |
| 4-5 | 옛 토큰(`cid` 없음)·공용 사람 토큰 거절 | agent-template | 태윤 |
스위치: `AX_REQUIRE_TENANT=1`로 켠다. 문제가 나면 `0`으로 되돌린다(아래 10장).

### 5단계 — 격리 시험, CI에 넣기 (9장)

### 6단계 — 누비투어·스터디온에듀 연결
| PR | 내용 | 영향 | 담당(제안) |
| --- | --- | --- | --- |
| 6-1 | 업종 템플릿(여행·교육)에서 회사 설정 만들기, 회사 만들기 화면(운영자가 연결 키 발급 → 고객사 관리자가 `/setup`) | dashboard-api, frontend | 예진 |
| 6-2 | 누비투어: 변환기 연결(이미 됨, 읽기만)을 회사 폴더로, 회사 LLM 키 넣기 | dashboard-api | 예진·재헌 |
| 6-3 | 스터디온에듀 연결 | 연결 방식에 따라 | 예진 |
| 6-4 | 캣북스 흔적(소스 36곳) 중 다른 회사에 보이는 문구부터 회사 설정 값으로 | 각 도메인 | 도메인 담당 |

## 9. 격리 시험 목록 (5단계)

시험 회사 A·B 두 개, 계정 a(A 관리자)·b(B 관리자)·m(A 일반회원)을 만든다. 목 스위치로 돌아가는 단위 시험(`uv run pytest`) + CI `backend` 작업. 결과는 모두 "없는 것처럼 404"(존재를 알리지 않는다).

| # | 시험 | 기대 |
| --- | --- | --- |
| 1 | a 토큰으로 B 문의 번호 조회·답변 쓰기 | 404 |
| 2 | a 토큰으로 B 보고서(일간 리포트·트렌드 보고서·분석 의뢰 PDF) 조회 | 404 |
| 3 | a 토큰으로 B 키 목록·키 값·키 바꾸기 요청 | 404, B 키 이력에 줄 없음 |
| 4 | a 토큰으로 B 회사 연결·변환기 매핑 읽기·쓰기 | 404 |
| 5 | a 토큰으로 B 할 일함·승인 대기·AI 오피스 대화 | 목록에 B 것 0건, 단건 404 |
| 6 | m(A 일반회원) 토큰으로 B 데이터 | 404 (회사 바꾸기 길은 없다) |
| 7 | 요청 본문·쿼리에 `tenant_id=B`를 넣은 a의 요청 | 무시하고 A로 처리(B 것 안 보임) |
| 8 | A2A: `tenant_id` 없는 요청 | `rejected` + `TENANT_REQUIRED` |
| 9 | A2A: 모르는 회사 ID | `rejected` + `TENANT_REQUIRED` |
| 10 | A2A: a의 사람 쓰기 토큰 + `meta.tenant_id=B` | `TENANT_MISMATCH` |
| 11 | A에서 시작한 의뢰가 부서 셋을 거쳐도 끝까지 `tenant_id=A` | 모든 단계 로그가 A |
| 12 | 주기 작업(집계·일간 저장)이 A·B를 따로 저장 | 서로 다른 줄, 섞임 0 |
| 13 | A 회사 LLM 키만 있고 B는 없을 때 B AI 호출 | "키 없음", A 키 쓰지 않음 |
| 14 | A 회사 서버 주소로 B 요청이 가지 않음(`shop_api_url(B)`) | B 주소 또는 연결 없음 |
| 15 | memory 카드 검색: A 질의에 B 카드 안 섞임 | 0건 |
| 16 | 옛 토큰(`cid` 없음), 공용 사람 토큰 | 4단계 뒤 401 |

CI 넣기: 위 시험을 `tests/isolation/`(공통층)과 각 도메인 `tests/test_tenant_isolation.py`로 두고 `backend` 작업에서 같이 돈다. 새 저장소가 생기면 이 시험에 줄을 더하는 것을 리뷰 체크로 둔다.

## 10. 위험과 되돌리기

| 위험 | 막기 | 되돌리기 |
| --- | --- | --- |
| 1-4 파일 옮기기에서 연결·설정이 사라짐(11/18 같은 일) | 옮기기 전 `.data` 통째 백업, 옛 파일은 지우지 않고 `.moved` 이름으로 둠 | 새 폴더를 지우고 `.moved`를 원래 이름으로 |
| 별칭 `shop-demo`↔`catbooks`가 섞여 같은 회사가 둘로 보임 | 저장할 때 늘 정식 id로 바꿔 씀, 읽을 때만 별칭 허용 | 별칭 표 한 줄 |
| 4단계 켜자 기본값으로 만든 요청이 줄줄이 거절 | 4단계 전 1~2주 "거절 대신 경고 로그"(`AX_REQUIRE_TENANT=warn`)로 돌려 목록을 비움 | `AX_REQUIRE_TENANT=0` |
| 3단계 원본 끈 뒤 집계로 못 하는 질문이 생김 | 하루 이상 병행, 매출 분석 결과를 원본·집계로 비교 | `sales_collect` 다시 켜기(`sales.db` 보관해 둠) |
| 문의 원문을 그때그때 읽다 회사 서버가 느리거나 꺼짐 | 화면에 "원문을 불러오지 못함"과 번호·상태는 표시 | 없음(원문을 다시 쌓지 않는다는 결정) |
| 한 설치에 여러 회사라 하나의 실수가 전체로 샘(#1381이 피하려던 것) | 5단계 시험을 CI 필수로, 저장소 추가 시 시험 줄 의무 | — |
| 회사 하나의 부하·비용이 다른 회사를 느리게 함 | 회사별 LLM 키·예산, 수집기 회사별 차례 | 회사 끄기(`enabled_services`를 회사 폴더에서) |

## 11. 열린 질문

1. **캣북스 회사 ID 값**: `catbooks`로 바꾸고 `shop-demo`를 별칭으로(추천) / `shop-demo`를 그대로 정식 id로. 바꾸면 support PG·sales.db·공통 로그의 기존 줄을 이관해야 한다
2. **#1381의 "본사 관제 / 고객사 설치" 두 층은 어떻게 되나** (재헌 님 의견 필요): 사이트 하나에 회사가 모이면 관제와 고객사가 다시 한 서버다. 추천: 관제(`/control`, 설치 키·회사 목록·하트비트)는 그대로 Lighthouse에 따로 두고, app.axcellent.cloud는 "여러 회사를 담은 설치 하나"로 본다. 누비투어용으로 설치 중인 Zabbix VM 두 번째 설치는 멈추거나 시험용으로만
3. **에이전트 토큰을 회사와 묶을지**: 묶지 않음(추천, 서버 하나·회사는 요청마다, 시험 11로 지킴) / 회사마다 에이전트 토큰. 묶으면 회사 수 × 서비스 수만큼 토큰이 생긴다
4. 운영자(operator)의 고객사 화면 접근: 지원 접근(기한·고객사 관리자 승인)만(추천, #1381 4.6) / 운영자는 늘 읽기 가능
5. 공개 트렌드 캐시(유튜브·네이버·인스타)를 회사끼리 공유: 공유(추천, 공개 데이터·할당량 절약) / 회사별
6. 일별 집계의 작은 셀 숨김 기준(k값)과 보관 기간 — 재현 님
7. 문의 원문을 회사 서버에서 읽을 때 회사 서버(캣북스·변환기)가 "문의 하나 읽기"를 지원하는지 — 캣북스는 catbooks-demo 세션에 확인, 변환기는 매핑 추가
8. 서버 쪽(재헌 님): 회사별 폴더 백업, nginx에서 `/site-adapter/<회사 ID>` 경로 열기, Postgres 하나에 회사 여럿(지금 칸 방식) vs 회사별 스키마
