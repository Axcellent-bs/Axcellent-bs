# orchestrator
담당: 준하 · 포트: 8000

## 1. 역할
통합 에이전트(총괄). 이벤트를 받아 이벤트로 열지 판단하고(`ctx_inc_` 발급), Agent Card 레지스트리로 부서를 골라 plan대로 순차·병렬로 맡기고, 끝나면 닫으면서 hr에 기록을 요청한다. 인터페이스 기준은 `docs/notes/orchestrator/interface.md`.

- **이벤트 판단** (`manager.py`, 규칙 코드): 쇼핑몰 장애(`shop.degraded`), 인프라 이상(`infra.anomaly_detected`), 심각도 high 이상의 지표 이상(`metric.anomaly`)은 이벤트로 열어 시나리오 A plan을 돌린다. 문의 처리 결과(`support.inquiry_resolved`)는 문의마다 열어 `support_resolved` plan을 돌린다. 같은 구성요소에 열린 이벤트가 있으면 합친다. 문의 접수·급증과 낮은 심각도의 지표 이상은 기록만 하고, 복구 신호(`shop.recovered`)는 해당 이벤트에 붙인다. 같은 `event_id`는 한 번만 처리한다.
- **plan 실행** (`engine.py`): 단계마다 부서와 skill을 정한 plan을 돌린다. 실패는 재시도하고(실패 2번, 마감 초과 1번), 소관이 아니면 **이관**한다. 이관은 같은 skill을 가진 다른 부서로 규칙으로 넘기고, 찾지 못하면 "이관 큐로 넘김"으로 남겨 사람이 본다(6장).
- **`input-required` 중계**: 부서끼리 물어봐야 하면 질의를 중계하고, 꺼진 서비스의 일은 사람 할 일함으로 넘어가 사람이 끝낼 때까지 기다린다(`handoff.completed`).
- **C 등급 승인 대기**: 부서가 안전 관문 보류를 돌려주면(`review_id` 포함) 승인 대기로 두고, 안전 관문의 사람 결정 이벤트(`safety.review_decided`)로 잇는다. 승인이면 같은 요청을 다시 보내 이어 가고, 거절·만료면 거절로 끝낸다. 이벤트를 믿지 않고 **안전 관문에 되물어 승인으로 기록된 경우에만** 인정하며, 마감(`ORCHESTRATOR_HOLD_TIMEOUT`) 때도 한 번 더 되묻는다. 총괄이 승인하지는 않는다.
- **저장** (`store.py`): 이벤트 기록과 처리한 `event_id`를 SQLite(`ORCHESTRATOR_DB`)에 저장한다. 다시 켜면 불러오고, 진행 중이던 이벤트는 이어 가지 않고 단계를 "재시작으로 중단됨 (사람이 확인 필요)"으로 남겨 `degraded`로 닫는다.
- **화면**: `frontend`의 총괄 화면(`/orchestrator`)이 dashboard-api를 거쳐 `/api/incidents`를 읽는다. 승인 대기에는 남은 시간을 보여 주고, 사이드바 총괄 메뉴에 승인 대기 건수 배지가 붙는다. 이 화면은 읽기 전용이다.

## 2. 실행 방법
**실제 모드(기본)**: `bash backend/scripts/run_all.sh`가 다른 서버와 함께 8000으로 띄운다(총괄만 `--only core`). 이벤트는 Redis Streams 구독(`REDIS_URL`)과 `POST /api/events`(에이전트 토큰)로 받는다. `run_all.sh`가 `ORCHESTRATOR_DB`를 `.data/orchestrator.db`로 넘긴다.

**목 모드(테스트·개발 전용)**: 가짜 부서 7곳으로 돈다. 로컬 실행과 데모에는 쓰지 않는다(레포 규칙). 총괄만 확인할 때:
```
uv run python -m ax_orchestrator
```
빈 포트를 찾아 띄우고 `http://localhost:<포트>/demo`를 연다. 거기서 장애를 주입해 이벤트 흐름을 본다. 혼합 모드(내 부서만 실제, 나머지는 가짜)는 `ORCHESTRATOR_REAL_DEPTS=finance`(쉼표로 여러 부서). 부서별로 총괄이 부르는 skill·입력은 `docs/notes/orchestrator/department-guide.md`(`scripts/make_department_guide.py`가 다시 만든다).

테스트: `cd backend && uv run pytest services/orchestrator`

| 환경변수 | 기본 | 뜻 |
|---|---|---|
| `ORCHESTRATOR_DB` | 비어 있음(메모리) | 이벤트 저장 SQLite 파일. 비우면 다시 켜면 사라진다 |
| `ORCHESTRATOR_HOLD_TIMEOUT` | 600 | C 등급 승인 대기 마감(초). 넘으면 거절로 끝낸다(마감 때 안전 관문에 한 번 되묻는다) |
| `ORCHESTRATOR_HANDOFF_TIMEOUT` | 86400 | 사람 할 일함 처리를 기다리는 시간(초) |
| `ORCHESTRATOR_MOCK` | - | `1`이면 목 모드(테스트 전용) |
| `ORCHESTRATOR_REAL_DEPTS` | - | 목 모드에서 실제로 붙일 부서 |
| `AX_SAFETY_URL` | agent-template 기본 포트 | 승인 결정을 되물을 안전 관문 주소(목 모드는 이 값을 줄 때만 부른다) |
| `REDIS_URL` | - | 이벤트 버스. 없으면 `POST /api/events`만 쓴다 |
| `AX_AGENT_TOKENS`, `SAFETY_HUMAN_TOKENS` | - | 쓰기 API 토큰(에이전트·사람) |

## 3. 입출력 계약
`ax_contracts`의 `AgentRequest/AgentResult/EventEnvelope`와 이벤트 데이터 모델(`SafetyReviewDecidedData` 등)을 그대로 쓴다. 새 계약은 없다. 총괄 자신의 `/a2a` skill은 `find_active_incident`, `answer_planning_question`, `receive_finance_opinion` 세 개다(`agent-card.json`).

HTTP 경로(`main.py`):
| 경로 | 뜻 |
|---|---|
| `GET /health` | 상태 |
| `GET /api/incidents`, `/api/incidents/{id}` | 이벤트 목록·하나(단계·로그·지표·`pending_holds`) |
| `POST /api/events` | 다른 부서가 이벤트를 보내는 입구(에이전트 토큰). `safety.review_decided`는 안전 관문 토큰만 받는다 |
| `GET /api/registry/real`, `/api/registry`, `/api/departments` | 부서 카드·skill 표, 부서별 호출 사용량 |
| `POST /api/demo/incidents`, `POST /api/incidents/{id}/holds/{step}` | 목 모드 전용(장애 주입, 승인·거절 흉내) |

## 4. 구성
- `main.py`(HTTP·이벤트 구독·기동) · `manager.py`(이벤트 수신·판단, 승인 결정 연결) · `engine.py`(plan 실행·재시도·이관·중계·승인 대기) · `store.py`(이벤트 저장) · `models.py`(이벤트·단계 상태, plan 정의)
- `plans/scenario_a.py`(쇼핑몰 장애 plan) · `plans/support_resolved.py`(문의 처리 결과 plan)
- `registry.py`(카드 수집·skill 표) · `real.py`(HTTP 카드 수집, 안전 관문 조회) · `hybrid.py`(목 모드의 부서별 실제/가짜 선택) · `guide.py`(부서별 연동 안내서)
- `a2a.py`(총괄 자신의 `/a2a` skill) · `planning_questions.py`(기획의 질문 답변) · `finance_opinions.py`(재무 의견 보관) · `auth.py`(쓰기 API 토큰)
- `recommend.py`(비슷한 과거 이벤트 추천) · `draft.py`(해결 내용 초안) · `timeline.py`(이벤트 시각) · `naming.py`(이벤트 이름) · `usage.py`(부서별 호출 사용량)
- `mock/`(가짜 부서·하네스, 테스트·목 모드) · `static/demo.html`(목 모드 화면)

## 5. 하지 않는 것
분석 문장·금액·등급을 만들지 않는다. C 등급을 승인하지 않는다(사람이 안전 관문 보류 큐에서 한다. 총괄은 그 결정을 받아 이을 뿐이다). 부서 DB를 직접 건드리지 않는다. 외부 텍스트를 지시로 해석하지 않는다.

## 6. 한계와 아직 없는 것
- **LLM 이관 선택기(`llm_pick`)는 연결하지 않았다.** `Engine`에 규칙으로 못 찾을 때 LLM이 부서를 고르는 자리(`llm_pick`)가 있지만 `main.py`가 넘기지 않아, 규칙으로 못 찾으면 "이관 큐로 넘김"으로 끝나고 사람이 본다. 이 자리는 시험(`tests/test_engine.py`)이 쓰는 확장점이라 코드는 남겼다(시험을 지우지 않는 레포 규칙). 연결하려면 레지스트리에 없는 부서 이름은 코드가 걸러야 하고(LLM이 부서를 지어내지 못하게), 평가가 먼저 필요하다.
- 진행 중이던 이벤트를 재시작 뒤 **이어 가지 않는다**(사람이 확인). 승인 대기·사람 할 일함 대기는 저장하지 않아, 재시작 중에 도착한 승인 결정은 맞출 대기가 없어 무시된다.
- 부서별 호출 사용량(`usage.py`)과 재무 의견(`finance_opinions.py`)은 메모리에만 있다(재무 의견은 재무가 원본을 갖고 다시 보낸다).
- 알림은 화면 표시(총괄 화면의 남은 시간, 사이드바 배지)뿐이다. 웹훅·푸시는 없다.
- 이상 보고·해결 보고를 접수하는 `/a2a` skill은 없다(이벤트는 이벤트 버스와 `POST /api/events`로 받는다).
