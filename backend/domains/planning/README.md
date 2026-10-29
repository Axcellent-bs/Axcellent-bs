# agents/planning
담당: 예진 · 포트: 8101
## 1. 역할
기획개발 에이전트. 다른 부서가 보낸 변경 요청을 기획서 → 작업 → PR → 머지로 잇고, 장애 사건이 나면 관련 PR·기획 문단 후보를 회신하고 사건을 문단에 연결한다.
인사(hr)에는 인수인계용 진행 중 항목을 알려 주고(`list_in_progress`), 인계 목록 누락을 교차검증하고(`verify_handover`), 기각한 안을 `record_case`(kind `decision_rejected`)로 보낸다.
프로토타입(`ax-frontend/planning-web/src/data.ts`·`trace.ts`·`projects.tsx`)의 리듀서·초기 상태·간단 요청 판정·자동 처리 범위·역추적·프로젝트 보기를 그대로 백엔드로 옮겼다.

- 간단 요청(화면 표시만 고치는 작은 요청)은 사람 클릭 없이 기획서(자동 확정) → 작업 → PR(`execute(generate_pr)` B) → 검수 → 머지(`execute(merge_pr)` B) → 회신까지 간다.
- 일반 요청·개선 초안도 접수에서 멈추지 않는다(`request.flow`). 액션이 끝날 때마다 기획서 초안(개선 초안은 채택 먼저) → 자동 확정 검사 → PR 만들기 → 검수 불일치면 수정 후 재검수 → 자동 머지 검사를 더 할 일이 없을 때까지 이어 간다. 멈추는 곳은 규칙에 걸린 건(`auto_review` 대기열)·검수 불일치 2회·safety 보류/거절뿐이고, L0(자동 처리 중지)이면 접수에서 멈춘다. 시드 요청은 건드리지 않는다.
- 기획서·PR은 사람 사전 승인 대신 규칙 교차검증 + 사후 감사로 처리한다 (10/23 결정, `domain/autocheck.py`).
  - 기획서: 질문 0·경고 0·근거 1 이상·되돌릴 수 없음 0이면 자동 확정(`execute(confirm_spec)`) → 작업 분할. 아니면 확정하지 않고 `auto_review.state` = `답 대기`(질문 남음) 또는 `교차검증 대기`. 질문에 답하면(`answerQuestion`)·`checkSpec`으로 다시 본다.
  - PR: 마지막 검수 통과 + `merge_pr` 규칙에 안 걸리면 자동 머지(`execute(merge_pr)` B). 걸리면 `교차검증 대기`, 되돌릴 수 없는 영역이면 `사람 확인`. safety가 보류·거절해도 `교차검증 대기`.
  - 자동 확정·머지는 활동(`audit.auto_confirmed`·`execute(merge_pr)`)과 `record_case`(`decision_reason`에 규칙 값)로 남겨 사후 감사한다. 대기 건 판단은 기존 사람 버튼(`confirmSpec`·`mergePr`)으로 한다.
  - 실제 교차검증자 연결·학습(한 번 학습, 백테스트, 시험 3건)은 safety 협의(#166 3번) 뒤. 지금은 대기열 상태와 `GET /review-queue`만 있다.
- 자동 처리 범위(L0~L3, 시연값): 연속 정상 3건이면 승급을 제안(`auto_policy_propose` C)하고 승인은 사람이 한다. 자동 머지 PR이 장애에 연결되면 승인 없이 한 단계 줄인다.
- 같은 제목 요청은 새로 만들지 않고 연결한다(`linked_duplicate`).
- 숫자(지표 6종·중복·간단 요청 판정·PR 크기)는 모두 코드로 계산한다. LLM은 레포 제안과 코딩 에이전트(파일 고르기·쓰기·리뷰, 3장 「코딩 에이전트」)에만 쓴다.

코드 구성 (`src/planning_agent/`):

| 파일 | 내용 |
| --- | --- |
| `domain/models.py` | 상태 모델 (Request, Spec, Task, PR, Thread, Doc, Decision, Incident, Activity, OutMsg, AutoPolicy, State) |
| `domain/seed.py` | 초기 데이터 (프로토타입 `initialState`) |
| `domain/service.py` | 액션 전부 (`inbound` 10종, 확정·질문 답·PR 생성·검수·머지·초안 채택·승급 승인/보류·소통·문서·창구) |
| `domain/autocheck.py` | 기획서 자동 확정·PR 자동 머지 판정, #171 규칙 키 params 계산, 되돌릴 수 없는 영역 경로 접두사 |
| `domain/rules.py` | 간단 요청 판정, 자동 처리 단계, 중복 찾기, 구간 안 PR, 롤백 교차검증 |
| `domain/handover.py` | 인수인계용 진행 중 항목 계산 (담당자·영역 대응, 인계 목록 비교) |
| `domain/trace.py` · `projects.py` · `metrics.py` · `docs.py` | 역추적, 프로젝트 6단계, 지표, 정리 문서 블록 |
| `domain/clock.py` | 주입하는 시계 (목 기본값은 프로토타입처럼 10-22 10:42 KST부터 1분씩) |
| `coder.py` | 코딩 에이전트 (개발팀 기획 → 파일 고르기·쓰기·리뷰, LLM 응답 모양, 한도) |
| `handlers.py` | A2A skill 5개 (시나리오 A 3개 + hr 연동 2개) |
| `api.py` | 화면용 REST·SSE |
| `runtime.py` | 프로세스 메모리 상태 저장소 (A2A와 REST가 같이 본다) |

## 2. 실행 방법
- 서버: `docker compose -f infra/compose.yml up planning`, 또는 레포 루트에서 `uv run uvicorn planning_agent.main:app --port 8101`
- 목 모드: `PLANNING_MOCK=1` (또는 템플릿 공통 `AGENT_TEMPLATE_MOCK=1`). CI는 항상 1.
  - 상태는 프로세스 메모리에만 있다. 재시작하거나 `POST /api/planning/reset`이면 초기 데이터로 돌아간다.
  - 실행 관문 `execute()`는 템플릿 목(모두 `executed`)을 쓴다. 목 모드가 아니면 템플릿의 safety 호출이 아직 TODO라서 관문이 거부하고, 상태를 바꾸는 액션은 모두 실행되지 않는다(fail-closed).
  - 시뮬레이터 액션(`inbound`, `deptChat`)은 목 모드에서만 받는다.
  - 외부 네트워크(GitHub·hr·safety)를 부르지 않는다. hr `record_case`·GitHub PR은 활동 로그에만 남긴다.
- 단독 테스트: `uv run pytest agents/planning`
- 요청 보내 보기: `uv run ax-test-call --agent http://localhost:8101 --skill find_related_prs --input input.json --as infra`

## 3. 입출력 계약
### A2A (`POST /a2a`, Agent Card `agent-card.json` → `/.well-known/agent-card.json`)
입출력은 `contracts/src/ax_contracts/schemas/planning.py` 그대로, 요청·결과 공통 필드는 `message.py`.

| skill | 등급 | 요청 가능 | 입력 → 출력 |
| --- | --- | --- | --- |
| `create_ticket` | A | support, marketing, hr, data, orchestrator | `CreateTicketInput` → `CreateTicketOutput` (#174). 같은 제목 요청이 있으면 새 요청을 원래 요청에 묶고 outcome `linked_duplicate` + `duplicate_of`(원래 REQ), `context_id`는 원래 요청의 `ctx_req_`. 없으면 outcome `created` + 새 `REQ-n`·`ctx_req_`(planning 발급). `source_ref`(예: `inq_42`)·`evidence_ids`는 저장만 하고 판정에 안 쓴다. `untrusted`(고객 원문)는 읽지 않는다. 간단 요청이면 자동 처리 흐름을 탄다. 결과 통지는 같은 `context_id`에 `path`를 비운 새 요청으로 보낸다(요청 부서 수신 skill 전까지는 활동 기록만). `idempotency_key` 필수 |
| `find_related_prs` | read | orchestrator, infra, data | `FindRelatedPrsInput` → `FindRelatedPrsOutput`. 구간 안 머지 PR(component는 `changed_paths` 접두사), 최근 머지 순, `spec_id`·`para_id` 포함. 원인 판정 없음 |
| `link_incident` | A | orchestrator, infra | `LinkIncidentInput` → `LinkIncidentOutput`. `linked_paras`는 `"SPEC-5:R3"` 형식. 같은 제목 요청이 있으면 그 ID + outcome `linked_duplicate`. `idempotency_key` 필수, 같은 키 두 번째는 이전 결과 |
| `verify_rollback_target` | read | safety | `VerifyRollbackTargetInput` → `VerifyRollbackTargetOutput`. 배포가 사건 뒤 → `deploy_after_incident`, 배포 전 30분(시연값, AS-B 미결) 안 머지 PR 있음 → 승인 `deploy_before_incident`, 없음 → `no_deploy_in_window`. 거절도 `completed` + outcome `rejected_by_review` |
| `list_in_progress` | read | hr, orchestrator | `ListInProgressInput` → `ListInProgressOutput`. 담당자(`owner_id` = 명부 `member_id`)·영역(`area`)의 `deployment`·`ticket`·`pr` 항목. 다른 담당자·영역 항목은 넣지 않는다. 우선순위 recommended |
| `verify_handover` | read | safety | `VerifyHandoverInput` 또는 safety 교차검증 형식(`execution_id`·`action`=`confirm_handover`·`requester`·`params{handover_id·owner_id·area·item_ids·completeness_percent…}`, #187) → `VerifyHandoverOutput`. hr `confirm_handover`(B)의 교차검증. 담당자·영역을 모르면 승인하지 않고 `failed` + `invalid_input`(safety는 보류). 빠진 진행 중 항목 0건 → 승인 `complete`, 있으면 거절 `missing_items` + `missing`. 이미 끝난 항목은 `stale_item_ids`(판정에 안 씀). 거절도 `completed` + outcome `rejected_by_review` |

hr 연동 항목 규칙 (`domain/handover.py`, 시연값):

| kind | 대상 | item_id | status |
| --- | --- | --- | --- |
| `ticket` | 끝나지 않은 작업 | `tkt_<작업 ID>` (`tkt_T-11`) | 작업 상태 그대로 (`진행 중`·`PR 검수`·`머지 대기`·`C 보류`) |
| `pr` | 머지하지 않은 PR | `pr_<번호>` (`pr_31`) | `open` |
| `deployment` | dev에 머지됐지만 확인이 끝나지 않은 PR | `dep_pr_<번호>` (`dep_pr_28`) | `incident_follow_up_open`(연결된 사건의 재발 방지 요청이 열림) · `merged_unconfirmed`(걸린 요청이 아직 완료 아님) |

- 담당자: 작업 `owner` 라벨 → `member_id` (`백엔드` → `emp_01`, `프론트` → `emp_02`). 대응이 없는 작업(에이전트가 혼자 끝낸 자동 처리)은 사람 인계 대상이 아니라 넣지 않는다.
- 영역: 값은 명부 `area`(`payment`·`search`·`catalog`·`support-desk`·`infra-ops`·`inventory`)만 쓴다(#179). PR 바뀐 경로 디렉터리(`shop-api/payment`·`shop-api/cart`·`shop-web/checkout` → `payment`, `shop-web/support` → `support-desk`, `infra` → `infra-ops` 등, 경로 단위 비교) → 없으면 기획서 문단·제목 낱말(`결제`·`장바구니` → `payment` 등). 둘 다 안 맞으면 `area` 없음. 입력 `area`가 명부 값이 아니면 지어내지 않고 빈 목록 + `collected_at`.
- `verify_handover`는 `tkt_`·`pr_`·`dep_pr_`로 시작하는 ID만 비교한다. 결정 카드(`card_`)·문서 ID는 보지 않는다. planning에 없는 planning 형식 ID는 `evidence.data_summary`에 "확인할 수 없음"으로만 적는다.

`evidence.ref_ids`는 `pr:28`, `para:SPEC-5:R3`, `req:REQ-102` 형식이다. 근거가 없으면 `data_summary`가 "확인할 수 없음"으로 시작한다.
입력이 계약과 다르면 `failed` + outcome `invalid_input`, 관문이 거부하면 `failed` + outcome `gate_rejected`.

### 화면 REST (`/api/planning`, 성공 `{data, meta}` / 실패 `{error: {code, message}}`)
| 메서드 | 경로 | 내용 |
| --- | --- | --- |
| GET | `/state` | 전체 상태 (화면 store 한 번에 채우기) |
| GET | `/requests?state=` · `/specs` · `/tasks` · `/pull-requests` · `/threads` · `/decisions` · `/incident-links` · `/docs` | 목록 (`meta.count`) |
| GET | `/metrics` | 지표 6종 + 계산 원자료 |
| GET | `/projects` · `/projects/{id}` | 프로젝트 목록(진행 중 먼저)·상세(6단계, 모은 항목) |
| GET | `/trace/{pr}` | 역추적 (`28`, `PR-28`, `PR%20%2328` 모두 받음) |
| GET | `/review-queue?state=` | 자동 확정·머지를 멈춘 건 (`kind` spec/pr, `state` 답 대기·교차검증 대기·사람 확인, 걸린 `rules`, 계산한 `params`, safety `gate_status`·`review_id`, 판단 버튼 `decide_with`) |
| GET | `/auto-policy` | 자동 처리 범위 (단계표, 현재 기준, 승급 기준) |
| POST | `/actions` | 프로토타입 Action 그대로 (`{"type": "confirmSpec", "spec": "SPEC-7"}`, snake_case 이름도 받음). 응답 `data`는 새 상태, `meta.changed`·`outcome`·`result` |
| POST | `/requests/{id}/reject` | 개선 초안 기각. 본문 `{decision_reason, alternatives_considered}` 둘 다 필수. `/actions` + `rejectDraft`와 같은 길. 요청 상태 `기각`, hr `record_case` kind `decision_rejected` |
| POST | `/reset` | 초기 데이터로 |
| GET | `/events` | SSE `text/event-stream`. 연결 즉시·상태가 바뀔 때마다 `event: state` + 전체 상태 JSON |

기각 액션: `rejectDraft`(`request`, `decision_reason`, `alternatives_considered`) · `rejectDecision`(`spec`, `text`, `decision_reason`, `alternatives_considered`). 이유나 대안이 비면 hr에 보내지 않고 400 `MISSING_DECISION_REASON`.

execute params (safety #171 `thresholds.toml` 규칙 키, 모두 도메인 상태에서 코드로 계산. 요청 본문의 숫자는 받지 않는다):

| 액션 | params | 계산 |
| --- | --- | --- |
| `confirm_spec` | `open_questions`·`warnings`·`basis_count`·`irreversible_count` (+ `auto`, 경고가 있으면 `warn_ignored`) | 남은 질문 수, 인사 경고가 붙은 문단 수, 근거 수, 바꿀 경로가 되돌릴 수 없는 영역에 닿는 요구사항 문단 수 |
| `merge_pr` | `changed_lines`·`changed_files`·`changed_paths`·`review_mismatch_count`·`linked_incident_count`·`irreversible_count` (+ `auto`) | PR 크기·경로, 검수 불일치 횟수, 이 PR 문단 또는 같은 파일을 바꾼 PR 문단 중 장애 표시 수, 되돌릴 수 없는 영역 경로 수 |

되돌릴 수 없는 영역(제안 목록, 확정은 safety·사람): `shop-api/payment/migrations/`(결제 데이터 삭제·변경), `shop-api/accounts/pii/`(개인정보 필드), `shop-api/settlement/payout/`(실제 송금·정산 연동). 허용 경로는 safety와 같은 `shop-api/`·`shop-web/`·`docs/`(#200). 머지 위험 경로 `shop-web/checkout/`·`shop-web/payment/`·`shop-web/account/`와 safety 보호 경로(#203 `merge_protected_paths`와 같은 값)는 `merge_pr`의 `irreversible_count`에만 더해 항상 사람 확인(C)으로 보낸다(기획서 확정에는 안 셈). 문단에 `paths`가 있으면 그 경로로, 없으면 역추적 규칙으로 정한다.

경고 무시 확정 (FR-SPEC-006): 인사 경고(`warn`)가 남은 문단을 둔 채 `confirmSpec`을 하면 "warn 무시 진행"을 세 곳에 남긴다. `execute(confirm_spec)` params `warn_ignored`, `record_case`(kind `spec_confirmed`) params의 `record_case.summary`·`source_refs`와 `warn_ignored`, 활동 로그(point `audit.warn_ignored`, hr로 가는 `record_case` 활동).

오류 코드: `INVALID_ACTION`·`UNKNOWN_ACTION`·`MISSING_DECISION_REASON`(400), `SIMULATOR_DISABLED`(403), `NOT_FOUND`(404), `GATE_REJECTED`(409, 관문 거부로 실행 안 함).
사람 버튼(`confirmSpec`·`mergePr`·`approvePromotion`)은 REST로만 받고 A2A skill로는 열지 않는다.

화면 모델과 다른 점(화면 쪽 변환 함수가 맞춘다): 필드는 snake_case(`duplicateOf` → `duplicate_of`, `autoPolicy` → `auto_policy`, `ctx` → `context_id`), 시각은 UTC ISO8601(`t` → `at`, `updated` → `updated_at`, `mergedAt` → `merged_at`, 사건 `at` → `opened_at`), PR에 `number`·`merged_at`·`changed_paths`가 더 있다. `from` 키는 그대로다.

### 형상관리 연결 (FR-AG-011, NFR-EXT-003)
- 공통 인터페이스 `vcs/base.py` `VcsAdapter`: ① `find_changes(since, until, path_prefix)` 구간 안 병합 변경 ② `get_change(number)` 바뀐 파일·줄(추가·삭제 수, 새 줄 번호) ③ `open_change(branch, title, body, base, files)` 브랜치·커밋·병합 요청 ④ `merge_change(number, method="squash")` ⑤ `get_trace_meta(number)` 제목·본문의 `REQ-n`·`SPEC-n`·`SPEC-n:R3`/`R3` + `get_checks(number)` CI 상태(passed·failed·pending, 검사 0개는 pending). `capabilities.read_only`면 ③④를 하지 않는다(FR-AG-013).
- 부품: `vcs/mock.py`(기본, 메모리) · `vcs/github.py`(REST v3, git data API로 커밋). 프로그램 이름은 `vcs/` 안에만 둔다.
- 고르기: `PLANNING_VCS_KIND`(`mock` 기본·`github`) · `PLANNING_VCS_REPO`(`owner/name`) · `PLANNING_VCS_TOKEN` · `PLANNING_VCS_BASE_URL`(기본 공개 API, 사내 설치형은 `https://<호스트>/api/v3`) · `PLANNING_VCS_BASE_BRANCH`(기본 `dev`). 토큰 권한은 대상 레포 하나에 contents·pull requests 읽기·쓰기, commit statuses 읽기(개인용 토큰에는 checks 권한이 없어 커밋 상태로 판단, GitHub App이면 checks 읽기도).
- 도메인 연결은 `vcs_bridge.py`. `mock`이면 아무것도 하지 않아 상태·활동이 지금과 같다. 그 밖이면 `generate_pr`·`review_pr` 관문 뒤에 `gen/` 브랜치로 PR을 올리고(본문에 요청·기획서·문단 ID, 커밋 파일은 `docs/generated/<spec>/<task>.md` 작업 기록 하나) PR에 `vcs_number`·`url`을 적는다. `merge_pr` 관문 뒤에 실제 병합한다.
- 부품이 실패하면 활동(`vcs.open_failed`·`vcs.merge_failed`) 한 줄만 남기고 PR은 병합하지 않는다. 설정이 잘못돼도 같다(fail-closed). 사람 머지 버튼으로 다시 시도할 수 있고, 이미 병합된 변경은 성공으로 본다.
- 테스트: `tests/test_vcs_adapter.py`가 같은 계약 묶음을 목·GitHub(가짜 서버 `httpx.MockTransport`, 공개·사내 주소)에 돌린다. 실제 API는 부르지 않는다.
- 연결 여러 개(레포마다 하나): 화면 연결은 `POST /vcs/connections`(`{id?, kind, repo, base_url?, base_branch?, token?, description?, paths?}`, id가 없으면 `conn-n`, 토큰을 비우면 그 연결의 토큰 유지, 틀리면 400 `INVALID_VCS`·이전 값 유지) · `GET /vcs/connections` · `POST /vcs/connections/{id}/delete`(없으면 404) · `POST /vcs/connections/{id}/test`(최근 하루 병합 읽기). 연결 모양 `{id, kind, repo, base_url, base_branch, description, paths, token_set, token_hint, source}`(토큰 값은 안 줌). 환경변수 설정은 종류가 `mock`이 아니면 id `env`·source `env` 연결이 된다(`PLANNING_VCS_DESCRIPTION`·`PLANNING_VCS_PATHS` 쉼표 구분도 읽음, 화면에서 못 바꿈). 예전 `GET/POST /vcs`·`/vcs/test`는 첫 연결을 본다. 화면 연결은 종류가 `mock`이어도 켜진 연결이다(메모리 레포).
- 레포 고르기(`repo_routing.py`): PR을 만들 때(`_generate_prs`·간단 요청 `_auto_build`, 관문 뒤) 규칙 점수(바꿀 경로가 담당 경로 안 +3, 작업·요청 낱말이 레포 이름·설명에 +1, 확신 high·mid·low)로 고르고, planning LLM(`PLANNING_LLM_*`·`LLM_*`)이 있으면 후보 id 하나와 근거 3줄(`AI: `)을 받는다(후보 밖 id·오류면 규칙). 결과는 PR `target`(`connection_id, repo, status, confidence, reasons, candidates, decided_by, decided_at`). 연결이 하나면 바로 승인("자동 (연결 1개)")하고 올린다. 여럿이면 `proposed`로 두고 올리지 않는다(활동 `vcs.repo_proposed`, 머지 대기 `vcs.repo_pending`, 사람 머지 버튼은 400 `REPO_NOT_APPROVED`).
- 사람 판단: `POST /actions` `{"type": "approveRepo", "pr": "PR #31", "connection"?: "conn-2"}`(관문 `approve_repo` A 뒤 그 연결에 올리고 자동 머지 규칙대로 이어 감) · `{"type": "rejectRepo", "pr": "PR #31"}`(관문 `reject_repo` A, 아무것도 안 올림, 나중에 연결을 골라 승인). 나눠 띄우면 PR은 `dev` 서비스에 있으니 `dev`로 보낸다(레포 제안은 `receive_dev_result`로 planning에 넘어가지 않는다).
- 코딩 에이전트용 인터페이스(목·GitHub 둘 다, 같은 계약 테스트): `list_files(prefix, limit=2000)` 기준 브랜치 파일 경로(GitHub는 git trees 재귀) · `read_file(path, ref=None)` 파일 글(GitHub는 contents API base64, 100KB 넘는 파일·바이너리·`..` 경로는 `invalid`) · `update_change(number, files, message)` 열린 PR 브랜치에 수정 커밋 더하기(GitHub는 커밋 만들고 ref PATCH). 목은 `seed_files()`로 기준 브랜치 파일을 넣고, 병합하면 그 파일이 기준 브랜치에 들어간다.
- 아직: `find_related_prs`·역추적은 여전히 메모리 PR을 본다(①②⑤ 연결 전, 연결이 여럿이어도 같다). 간단 요청을 레포 승인 뒤 머지하면 자동 처리 이력·승급 연속 건수에는 넣지 않는다.

### 코딩 에이전트 (개발 역할, `coder.py`)
개발팀원이 개발 화면에 자기 기획을 쓰면 에이전트가 연결된 레포에서 실제로 구현한다.

- 받기: `POST /dev-tasks` `{title(필수, 200자), plan(필수, 8000자), done?(완료 기준, 한 줄에 하나), paths?(경로 힌트 20개), connection?(연결 id)}` → `data: {request_id, spec_id, task_id, status: "queued"}`. 요청(`from` dev, `via` dev_team, `작업 중`)·확정 기획서(`origin` dev_team, `handed_to_dev`, R1 = 제목·`paths` 힌트, `problem` = 기획 글, C1.. = 완료 기준 줄, 없으면 기본 한 줄)·작업 하나를 관문 `dev_task_create`(A) 뒤에 만든다. id는 planning 번호와 겹치지 않게 `REQ-D1`·`SPEC-D1`·`T-D1`, 사건은 `ctx_dev_0001`.
- 오류: 400 `INVALID_INPUT`(형식, 모르는 연결, 연결이 여럿인데 `connection` 없음) · 409 `NO_VCS`(연결 없음) · 409 `LLM_NOT_CONFIGURED`(모델·주소 없음, 아무것도 만들지 않음) · 409 `WRONG_ROLE`(역할 planning 서비스) · 409 `GATE_REJECTED`.
- 다시 시도: `POST /dev-tasks/{task_id}/retry` `{connection?}` → 같은 모양(`status: queued`). `failed`·`waiting_human`이고 PR을 아직 안 만든 작업만(관문 `dev_task_retry` A). 없으면 404 `NOT_FOUND`, 진행 중·끝남·PR 있음은 409 `NOT_RETRYABLE`(PR이 있으면 PR 화면 머지 버튼으로 판단).
- 올릴 레포(v1): 코딩 작업에는 레포 제안·`approveRepo`를 쓰지 않는다. `connection`을 주거나 연결이 하나일 때만 받는다. 고른 연결은 PR `target`에 승인 상태(`decided_by` 개발팀)로 적는다.
- 진행(작업 `coding`, SSE 상태로 나간다): `{status, model, files: [{path, additions, deletions, reason}], summary, review: {verdict, notes} | null, error, attempts, updated_at, connection, pr}`. status는 `queued → reading → writing → reviewing → opened → merged`, 멈추면 `waiting_human`·`failed`.
- 흐름 (상태 잠금 밖에서 돌고, 단계마다 작은 액션으로 상태를 적는다):
  1. 읽기: `list_files()` → 잠금 파일·`node_modules`·`dist`·이미지 등을 빼고 힌트 경로 안을 앞에 둬 400개까지. 활동 `code.read` "파일 n개 읽음".
  2. 고르기(LLM): 기획·완료 기준·힌트·목록 → 파일 5개 이하 `{path, action: modify|create, reason}`. `..`·절대 경로, 목록에 없는 modify는 뺀다. 보호 경로는 그대로 두고 머지 규칙이 사람 확인으로 보낸다. 활동 `code.plan` "고칠 파일 n개 선택".
  3. 쓰기(LLM, 파일마다 한 번): 지금 글(데이터 블록) → 파일 전체 새 글 + 이유 한 줄. 줄 수는 difflib로 센다. 활동 `code.write` "n개 파일 · +a −d줄".
  4. 리뷰(LLM, PR 전): diff와 완료 기준 → `통과`·`불일치` + 노트. 불일치면 노트를 넣어 짚은 파일만 한 번 다시 쓰고(짚은 새 파일은 더한다) 다시 리뷰. 커밋은 아직 없다.
  5. 커밋·PR: 관문 `generate_pr`(B, 실제 줄·파일·경로) 뒤 `gen/<작업>-<실행 번호>` 브랜치에 마지막 결과만 커밋 하나로 올리고 PR을 연다(본문에 기획·완료 기준·파일별 이유, `요청`·`기획서`·`문단` 줄). 리뷰 결과를 관문 `review_pr` 뒤 PR `reviews`에 남긴다(PR 전 불일치도 앞에 붙인다). 활동 `code.review`. 두 번 다 불일치면 `waiting_human`(대기열 `pr_review_mismatch`).
  6. 머지: 통과면 기존 자동 머지 규칙(`merge_pr` 크기·보호 경로·장애 연결 등). 걸리면 `waiting_human`, 사람이 머지 버튼을 누르면 `merged`. 형상관리에는 merge commit으로 합친다.
- 사람 확인 대기(열린 PR) 조치: 화면 코딩 카드의 **수정 지시**(`POST /dev-tasks/{id}/revise` `{note}`, 관문 `dev_task_revise` A) → 개발 AI가 PR 브랜치 글 위에서 지시대로 고치고 리뷰한 뒤 **같은 PR에 커밋**(관문 `code_write` A) → 리뷰 기록·자동 머지 규칙. **머지**는 누구나(`mergePr`, 감사 기록 "사람이 머지"). 대화에서 대기 중인 작업에 이어 말해도 수정 지시가 된다.
- 교차검증 대기가 `PLANNING_CROSS_CHECK_TIMEOUT_MIN`(기본 10분) 넘으면 사람 확인으로 넘긴다(화면 SSE가 열려 있는 동안 확인, 활동 `auto_review`).
- 브랜치가 이미 있으면 제목·바꾼 파일이 같은 앞선 시도일 때만 이어받고, 아니면(서버를 다시 켜 작업 번호가 겹침 등) `gen/<작업>-<실행>-<6자리>` 새 브랜치로 올린다.
- 한도: 파일 5개, 바뀐 줄 합 1500줄, 고칠 파일 600줄(넘으면 PR 없이 `waiting_human`, 작업 `C 보류`). LLM 오류·시간 초과(180초)·형상관리 오류는 `failed` + 짧은 이유(예외 내용·비밀 값 없음), 활동 `code.failed`.
- LLM 설정: `PLANNING_LLM_MODEL`·`PLANNING_LLM_BASE_URL`·`PLANNING_LLM_API_KEY`(없으면 공통 `LLM_*`). 레포 이름·파일 글·기획 글만 보내고 토큰은 보내지 않는다. 테스트는 `coder.set_llm()`으로 가짜를 끼운다(`tests/test_coder.py`).
- 아직: 실패 뒤 이미 PR이 있는 작업(리뷰 LLM 오류 등)은 다시 시도하지 않는다(PR 화면에서 머지·판단). 파일 하나를 통째로 다시 쓰므로 600줄 넘는 파일은 사람 몫이다.

### 개발 AI와 대화 (개발 화면, `dev_chat.py`)
개발팀원은 양식 대신 개발 AI와 대화한다. 요청이 흐리면 AI가 되묻고, 계획이 서면 코딩 작업(`create_dev_task`, 위 흐름 그대로)을 스스로 띄워 PR까지 간다. `POST /dev-tasks`는 그대로 둔다(안에서 다시 쓴다).

- `GET /dev-chat` → 대화 목록(최근 바뀐 것 먼저, 20개) · `GET /dev-chat/{thread_id}` → 대화 하나(없으면 404).
- `POST /dev-chat` `{thread_id?, message(1~4000자), connection?}` → 사용자 메시지를 적고 AI 답 하나를 받은 뒤 바뀐 대화를 준다. `thread_id`가 없으면 새 대화(`chat-1`, `chat-2`…, 제목 = 첫 메시지 40자).
- 대화 `{id, title, status: talking|started|blocked, messages, task_id, connection, created_at, updated_at}`, 메시지 `{id(m1..), role: user|assistant, text, at, kind: text|question|plan|started|error, plan: {title, plan, done[], paths[]}|null, task_id}`.
- 상태 `chats`에도 들어가 SSE로 나간다. 사용자 메시지는 LLM을 부르기 전에 먼저 적어 화면에 바로 보이고, 작업을 띄운 뒤 진행은 작업 `coding`으로 본다. 초기화하면 비운다.
- 한 번 주고받기: LLM(`{reply, action: ask|start, plan?, connection?}`)에 대화(최근 20개, 데이터 블록)·연결 목록(id·레포·설명·담당 경로)·실제 파일 경로(coder와 같은 걸러내기, 합 300개)를 준다. 되묻기는 2번까지(그 뒤에도 묻는데 계획이 있으면 띄운다). "진행해"·"만들어 줘"·"PR 올려" 같은 말이면 이번 계획 또는 지난 계획으로 띄운다. 계획은 다듬는다(제목 200자, `..` 경로 빼기, 완료 기준 20줄).
- 올릴 연결: 대화에 고른 것(`connection`) → LLM이 고른 id → 메시지에 나온 연결 id·레포 이름 → 연결이 하나면 그것. 여럿인데 못 정하면 어느 레포인지 묻는다(`question`, 계획을 붙여 둬서 연결을 골라 "진행해"면 바로 띄운다).
- 작업을 띄우면 `started` 메시지(`task_id`)와 대화 `status: started`. 그 대화에 또 쓰면 LLM 없이 작업 진행 상태만 답한다(다른 변경은 새 대화).
- 연결이 없으면 요청을 실패시키지 않고 200 + `error` 메시지("연결된 레포가 없어…")와 `status: blocked`를 준다. 연결을 붙인 뒤 다시 보내면 이어 간다. LLM 오류·시간 초과(60초)도 `error` 메시지 한 줄(예외 종류만, 내용·비밀 값 없음).
- 오류: 409 `LLM_NOT_CONFIGURED`(가장 먼저 본다, 아무것도 안 적음) · 400 `INVALID_INPUT`(형식, 모르는 연결) · 404 `NOT_FOUND`(대화 없음) · 409 `WRONG_ROLE`(planning 서비스) · 409 `GATE_REJECTED`.
- 메시지 하나를 적을 때마다 관문 `dev_chat`(A, 대상 `chat-1:m2`, 사건 `ctx_chat_1`). LLM은 `coder`와 같은 설정, 테스트는 `dev_chat.set_llm()`(`tests/test_dev_chat.py`).

## 4. 의존
`ax-contracts`, `ax-agent-template`, `fastapi`, `uvicorn`, `httpx`. 다른 부서 폴더를 import하지 않는다.
- hr `record_case` 입력은 계약 `RecordCase`(`record.py`)로 만들어 `execute(record_case)` params의 `record_case`에 넣는다 (`decision_rejected`·`spec_confirmed`).
- 실행 관문: `ax_agent_template.execute.execute()` (safety). 상태를 바꾸는 단계마다 부른다: `create_ticket`·`write_spec`·`confirm_spec`·`split_tasks`·`generate_pr`(B)·`review_pr`·`merge_pr`(B)·`record_case`·`extract_decision`·`review_ticket_draft`·`link_incident`·`send_message`·`auto_policy_update`·`auto_policy_propose`(C, 보류가 정상)·`approve_repo`·`reject_repo`(올릴 레포 사람 판단)·`dev_task_create`·`dev_task_retry`·`code_write`(코딩 에이전트, A)·`dev_chat`(개발 AI 대화, A). 멱등성 키는 `context_id:액션:대상`.

## 5. 하지 않는 것
- 원인 판정을 하지 않는다 (infra 몫). 후보와 시간 관계만 회신한다.
- 판정 문장(기각 여부 등)을 만들지 않는다 (hr 몫). hr이 준 경고를 그대로 붙인다.
- 배포·롤백을 실행하지 않는다 (infra가 한다). 롤백 대상 교차검증만 한다.
- `execute()` 없이 상태를 바꾸지 않는다. 관문이 거부·무응답이면 실행하지 않는다.
- 외부 입력(`untrusted`)을 읽거나 지시로 해석하지 않는다.
- 목 설정(`PLANNING_VCS_KIND=mock`, 기본)에서는 실제 PR 생성·hr 호출·DB 저장을 하지 않는다 (목 데이터, 프로세스 메모리).
