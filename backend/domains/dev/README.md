# domains/dev (`dev_agent`)
담당: 예진 · 포트: 8111 · 서비스: `services/dev`

## 1. 역할
개발·배포 에이전트. 기획 서비스(`domains/planning`, 8101)가 넘긴 확정 기획서(`start_dev`)로 PR을 만들고 검수·머지·배포까지 잇고, 결과를 기획에 돌려준다(`receive_dev_result`). 2026-11-02 기획·개발 분리 5단계에서 `planning_agent`에서 나왔다.

- 넘겨받은 기획서는 사람 클릭 없이 PR 만들기 → 검수 → 불일치면 수정 후 재검수 → 자동 머지 검사까지 간다. 규칙에 걸리면 `auto_review` 대기열(교차검증 대기·사람 확인).
- 자동 처리 범위(`auto_policy`)의 주인이다. 바뀌면 `receive_dev_result`에 실어 기획에 맞춘다.
- 코딩 에이전트(`coder.py`)·개발 AI 대화(`dev_chat.py`)·개발자 본인 Claude 일감(`work_queue.py`·`work_api.py`, MCP)·저장소 작업 공간(`repo_api.py`)·형상관리 연결(`vcs/`·`vcs_bridge.py`·`repo_routing.py`)·배포 기록(`deploy_store.py`·`deploy_signals.py`·`deploy.py`)·머지 교차검증 근거(`cross_check.py`)가 여기 있다.
- 장애 사건에 관련 PR 후보(`find_related_prs`)·롤백 대상 확인(`verify_rollback_target`)·사건 연결(`link_incident`), 인사 인수인계용 진행 중 항목(`list_in_progress`)·인계 확인(`verify_handover`)을 맡는다.

## 2. 구성
| 모듈 | 내용 |
|---|---|
| `service.py` | `DevService(PlanningCore)`. 공용 뼈대는 `ax_workflow.base` |
| `api.py` | 화면 REST `/api/planning/*` (공용 라우트 `ax_workflow.api` + 개발 라우트) |
| `handlers.py` | A2A skill 6개 (`DEV_HANDLERS`) |
| `main.py`·`runtime.py` | 서버 진입점, 프로세스 안 서비스 하나 |

- 기획 도메인(`planning_agent`)은 import하지 않는다(`.importlinter`). 둘이 같이 쓰는 상태 모델·규칙·넘기기는 `shared/workflow-core`(`ax_workflow`).
- 환경 변수 이름은 그대로 `PLANNING_*`(LLM·형상관리·상태 파일). 카드는 `DEV_AGENT_CARD`(옛 `PLANNING_AGENT_CARD`도 읽음).
- 시험: `domains/dev/tests`. 기획·개발을 함께 도는 시험 도우미는 `shared/workflow-testing`(`ax_workflow_testing`).

## 3. 입출력 계약
### A2A (`POST /a2a`, Agent Card `agent-card.json`)
입출력은 `shared/contracts/src/ax_contracts/schemas/planning.py`.

| skill | 등급 | 요청 가능 | 입력 → 출력 |
| --- | --- | --- | --- |
| `find_related_prs` | read | orchestrator, infra, data | `FindRelatedPrsInput` → `FindRelatedPrsOutput`. 구간 안 머지 PR(component는 `changed_paths` 접두사), 최근 머지 순, `spec_id`·`para_id` 포함. 원인 판정 없음 |
| `link_incident` | A | orchestrator, infra | `LinkIncidentInput` → `LinkIncidentOutput`. `linked_paras`는 `"SPEC-5:R3"` 형식. 같은 제목 요청이 있으면 그 ID + outcome `linked_duplicate`. `idempotency_key` 필수, 같은 키 두 번째는 이전 결과 |
| `verify_rollback_target` | read | safety | `VerifyRollbackTargetInput` → `VerifyRollbackTargetOutput`. 배포가 사건 뒤 → `deploy_after_incident`, 배포 전 30분(시연값, AS-B 미결) 안 머지 PR 있음 → 승인 `deploy_before_incident`, 없음 → `no_deploy_in_window`. 거절도 `completed` + outcome `rejected_by_review` |
| `list_in_progress` | read | hr, orchestrator | `ListInProgressInput` → `ListInProgressOutput`. 담당자(`owner_id` = 명부 `member_id`)·영역(`area`)의 `deployment`·`ticket`·`pr` 항목. 다른 담당자·영역 항목은 넣지 않는다. 우선순위 recommended |
| `verify_handover` | read | safety | `VerifyHandoverInput` 또는 safety 교차검증 형식(`execution_id`·`action`=`confirm_handover`·`requester`·`params{handover_id·owner_id·area·item_ids·completeness_percent…}`, #187) → `VerifyHandoverOutput`. hr `confirm_handover`(B)의 교차검증. 담당자·영역을 모르면 승인하지 않고 `failed` + `invalid_input`(safety는 보류). 빠진 진행 중 항목 0건 → 승인 `complete`, 있으면 거절 `missing_items` + `missing`. 이미 끝난 항목은 `stale_item_ids`(판정에 안 씀). 거절도 `completed` + outcome `rejected_by_review` |
| `start_dev` | A | planning | `DevHandoffInput` → `DevHandoffOutput`. 기획이 확정한 기획서·작업을 받아 PR까지 잇는다 (flow) |

hr 연동 항목 규칙 (`domain/handover.py`, 시연값):

| kind | 대상 | item_id | status |
| --- | --- | --- | --- |
| `ticket` | 끝나지 않은 작업 | `tkt_<작업 ID>` (`tkt_T-11`) | 작업 상태 그대로 (`진행 중`·`PR 검수`·`머지 대기`·`C 보류`) |
| `pr` | 머지하지 않은 PR | `pr_<번호>` (`pr_31`) | `open` |
| `deployment` | dev에 머지됐지만 확인이 끝나지 않은 PR | `dep_pr_<번호>` (`dep_pr_28`) | `incident_follow_up_open`(연결된 사건의 재발 방지 요청이 열림) · `merged_unconfirmed`(걸린 요청이 아직 완료 아님) |

- 담당자: 작업 `owner` 라벨 → `member_id` (`백엔드` → `emp_01`, `프론트` → `emp_02`). 대응이 없는 작업(에이전트가 혼자 끝낸 자동 처리)은 사람 인계 대상이 아니라 넣지 않는다.
- 영역: 값은 명부 `area`(`payment`·`search`·`catalog`·`support-desk`·`infra-ops`·`inventory`)만 쓴다(#179). PR 바뀐 경로 디렉터리(`shop-api/payment`·`shop-api/cart`·`shop-web/checkout` → `payment`, `shop-web/support` → `support-desk`, `infra` → `infra-ops` 등, 경로 단위 비교) → 없으면 기획서 문단·제목 낱말(`결제`·`장바구니` → `payment` 등). 둘 다 안 맞으면 `area` 없음. 입력 `area`가 명부 값이 아니면 지어내지 않고 빈 목록 + `collected_at`.
- `verify_handover`는 `tkt_`·`pr_`·`dep_pr_`로 시작하는 ID만 비교한다. 결정 카드(`card_`)·문서 ID는 보지 않는다. planning에 없는 planning 형식 ID는 `evidence.data_summary`에 "확인할 수 없음"으로만 적는다.

화면 REST·쓰기 토큰 규칙은 기획과 같다 (`ax_workflow.api`·`ax_workflow.auth`). 개발만의 경로는 `/vcs*`·`/deployments*`·`/dev-tasks*`·`/dev-chat*`·`/repo/*`·`/work/*`·`/mcp`.

## 4. 기능
### 형상관리 연결 (FR-AG-011, NFR-EXT-003)
- 공통 인터페이스 `vcs/base.py` `VcsAdapter`: ① `find_changes(since, until, path_prefix)` 구간 안 병합 변경 ② `get_change(number)` 바뀐 파일·줄(추가·삭제 수, 새 줄 번호) ③ `open_change(branch, title, body, base, files)` 브랜치·커밋·병합 요청 ④ `merge_change(number, method="squash")` ⑤ `get_trace_meta(number)` 제목·본문의 `REQ-n`·`SPEC-n`·`SPEC-n:R3`/`R3` + `get_checks(number)` CI 상태(passed·failed·pending, 검사 0개는 pending). `capabilities.read_only`면 ③④를 하지 않는다(FR-AG-013).
- 부품: `vcs/mock.py`(기본, 메모리) · `vcs/github.py`(REST v3, git data API로 커밋). 프로그램 이름은 `vcs/` 안에만 둔다.
- 고르기: `PLANNING_VCS_KIND`(`mock` 기본·`github`) · `PLANNING_VCS_REPO`(`owner/name`) · `PLANNING_VCS_TOKEN` · `PLANNING_VCS_BASE_URL`(기본 공개 API, 사내 설치형은 `https://<호스트>/api/v3`) · `PLANNING_VCS_BASE_BRANCH`(기본 `dev`). 토큰 권한은 대상 레포 하나에 contents·pull requests 읽기·쓰기, commit statuses 읽기(개인용 토큰에는 checks 권한이 없어 커밋 상태로 판단, GitHub App이면 checks 읽기도).
- **GitHub App (회사 레포)**: 개인 토큰은 그 사람이 가진 레포에만 닿고 사람에 묶여서, 조직 레포는 조직 관리자가 설치한 GitHub App으로 붙인다(`auth: app`). 앱 개인 키로 JWT(RS256, 9분)를 만들어 `POST /app/installations/{설치 ID}/access_tokens`로 설치 토큰(1시간)을 받고, 끝나기 5분 전에 다시 받는다(`vcs/github_app.py`). 환경변수는 `PLANNING_VCS_AUTH=app` · `PLANNING_VCS_APP_ID` · `PLANNING_VCS_INSTALLATION_ID` · `PLANNING_VCS_PRIVATE_KEY_FILE`(.pem 경로). 개인 키·설치 토큰은 화면·응답·로그·오류 문구에 넣지 않는다(화면에는 `key_set`만). 앱 권한: Contents·Pull requests·Issues 읽기·쓰기, Checks·Commit statuses 읽기.
- 도메인 연결은 `vcs_bridge.py`. `mock`이면 아무것도 하지 않아 상태·활동이 지금과 같다. 그 밖이면 `generate_pr`·`review_pr` 관문 뒤에 `gen/` 브랜치로 PR을 올리고(본문에 요청·기획서·문단 ID, 커밋 파일은 `docs/generated/<spec>/<task>.md` 작업 기록 하나) PR에 `vcs_number`·`url`을 적는다. `merge_pr` 관문 뒤에 실제 병합한다.
- 부품이 실패하면 활동(`vcs.open_failed`·`vcs.merge_failed`) 한 줄만 남기고 PR은 병합하지 않는다. 설정이 잘못돼도 같다(fail-closed). 사람 머지 버튼으로 다시 시도할 수 있고, 이미 병합된 변경은 성공으로 본다.
- 테스트: `tests/test_vcs_adapter.py`가 같은 계약 묶음을 목·GitHub(가짜 서버 `httpx.MockTransport`, 공개·사내 주소)에 돌린다. 실제 API는 부르지 않는다.
- 연결 여러 개(레포마다 하나): 화면 연결은 `POST /vcs/connections`(`{id?, kind, repo, base_url?, base_branch?, token?, description?, paths?, auth?, app_id?, installation_id?, private_key?}`, id가 없으면 `conn-n`, 토큰을 비우면 그 연결의 토큰 유지, 틀리면 400 `INVALID_VCS`·이전 값 유지) · `GET /vcs/connections` · `POST /vcs/connections/{id}/delete`(없으면 404) · `POST /vcs/connections/{id}/test`(최근 하루 병합 읽기). 연결 모양 `{id, kind, repo, base_url, base_branch, description, paths, token_set, token_hint, source, auth, app_id, installation_id, key_set}`(토큰·개인 키 값은 안 줌). 환경변수 설정은 종류가 `mock`이 아니면 id `env`·source `env` 연결이 된다(`PLANNING_VCS_DESCRIPTION`·`PLANNING_VCS_PATHS` 쉼표 구분도 읽음, 화면에서 못 바꿈). 예전 `GET/POST /vcs`·`/vcs/test`는 첫 연결을 본다. 화면 연결은 종류가 `mock`이어도 켜진 연결이다(메모리 레포).
- 레포 고르기(`repo_routing.py`): PR을 만들 때(`_generate_prs`·간단 요청 `_auto_build`, 관문 뒤) 규칙 점수(바꿀 경로가 담당 경로 안 +3, 작업·요청 낱말이 레포 이름·설명에 +1, 확신 high·mid·low)로 고르고, planning LLM(`PLANNING_LLM_*`·`LLM_*`)이 있으면 후보 id 하나와 근거 3줄(`AI: `)을 받는다(후보 밖 id·오류면 규칙). 결과는 PR `target`(`connection_id, repo, status, confidence, reasons, candidates, decided_by, decided_at`). 연결이 하나면 바로 승인("자동 (연결 1개)")하고 올린다. 여럿이면 `proposed`로 두고 올리지 않는다(활동 `vcs.repo_proposed`, 머지 대기 `vcs.repo_pending`, 사람 머지 버튼은 400 `REPO_NOT_APPROVED`).
- 사람 판단: `POST /actions` `{"type": "approveRepo", "pr": "PR #31", "connection"?: "conn-2"}`(관문 `approve_repo` A 뒤 그 연결에 올리고 자동 머지 규칙대로 이어 감) · `{"type": "rejectRepo", "pr": "PR #31"}`(관문 `reject_repo` A, 아무것도 안 올림, 나중에 연결을 골라 승인). 나눠 띄우면 PR은 `dev` 서비스에 있으니 `dev`로 보낸다(레포 제안은 `receive_dev_result`로 planning에 넘어가지 않는다).
- 코딩 에이전트용 인터페이스(목·GitHub 둘 다, 같은 계약 테스트): `list_files(prefix, limit=2000)` 기준 브랜치 파일 경로(GitHub는 git trees 재귀) · `read_file(path, ref=None)` 파일 글(GitHub는 contents API base64, 100KB 넘는 파일·바이너리·`..` 경로는 `invalid`) · `update_change(number, files, message)` 열린 PR 브랜치에 수정 커밋 더하기(GitHub는 커밋 만들고 ref PATCH). 목은 `seed_files()`로 기준 브랜치 파일을 넣고, 병합하면 그 파일이 기준 브랜치에 들어간다.
- 아직: `find_related_prs`·역추적은 여전히 메모리 PR을 본다(①②⑤ 연결 전, 연결이 여럿이어도 같다). 간단 요청을 레포 승인 뒤 머지하면 자동 처리 이력·승급 연속 건수에는 넣지 않는다.

### 레포 작업 공간 (개발 화면 작업·PR 탭, `repo_api.py`)
연결된 레포의 PR·브랜치를 GitHub를 열지 않고 다룬다. 사람이 직접 올린 PR도 모두 보인다.

- 부품 인터페이스(목·GitHub 둘 다, `tests/test_repo_workspace.py`): `list_changes(state, query, limit)`(open·merged·closed·all, 제목·브랜치·번호·작성자 찾기, 최근 갱신 순, 열린 PR 20개까지 CI 점) · `describe_change` · `get_diff`(파일별 unified diff, 파일 300개·파일당 2만 자·합 30만 자에서 자르고 `truncated`) · `list_commits` · `list_comments`(대화 코멘트) · `add_comment` · `reopen_change` · `revert_change`(GitHub는 GraphQL `revertPullRequest` = 화면의 Revert 버튼, 목은 병합 직전 기준 파일로 되돌리는 변경) · `list_branches`(기본·보호, GitHub는 30개까지 compare로 ahead/behind·마지막 커밋 시각) · `delete_branch`(기준·기본 브랜치는 `invalid`)
- REST (`?connection=<id>`, 없으면 첫 연결, 연결이 없으면 409 `NO_VCS`, 모르는 연결 404):

| 메서드 | 경로 | 내용 |
| --- | --- | --- |
| GET | `/repo/prs?state=&q=&limit=` | PR 목록(최대 100, 본문 뺌). 내부 PR과 번호가 이어진 것은 `internal{id, task, merged, closed}` |
| GET | `/repo/prs/{n}` | 머리(`pr`)·`diff`·`commits`·`comments`·`checks`·`internal` |
| GET | `/repo/branches` | 브랜치(기준·기본 먼저) + 그 브랜치의 PR(열린 것 우선) |
| GET | `/repo/issues?state=open·closed·all&q=&limit=` | 이슈 목록(PR 뺌, 본문 뺌, 라벨·담당자·코멘트 수). 이슈 칸이 없는 연결(`issues: false`)은 409 `NO_ISSUES` |
| GET | `/repo/issues/{n}` | 이슈 머리(`issue`)·`comments` |
| POST (사람) | `/repo/prs/{n}/merge` `{method: merge(기본)·squash·rebase}` | AI PR이면 내부 `mergePr` 흐름 그대로(`via: internal`), 아니면 관문 `merge_pr`(같은 크기·경로 params) 뒤 병합 |
| POST (사람) | `/repo/prs/{n}/close` · `/reopen` | AI 코딩 작업 PR 닫기는 작업 중단(`cancel_dev_task`). 그 밖은 관문 `repo_close_pr`·`repo_reopen_pr`(A). 중단한 AI 작업은 다시 열지 않음(409 `INTERNAL_FLOW`) |
| POST (사람) | `/repo/prs/{n}/comment` `{body}` | 관문 `repo_comment`(A) |
| POST (사람) | `/repo/issues/{n}/comment` `{body}` | 이슈 코멘트. PR 코멘트와 같은 관문 `repo_comment`(A) |
| POST (사람) | `/repo/prs/{n}/revert` `{title?}` | 병합된 PR만. 관문 `repo_revert_pr`(B, merge_pr과 같은 크기·경로·보호 경로 규칙 `revert_*`) 뒤 되돌리기 PR을 연다. 새 PR은 보통 PR처럼 머지한다 |
| POST (사람) | `/repo/branches/delete` `{name}` | 기준·기본·보호 브랜치 400 `PROTECTED_BRANCH`, 열린 PR 브랜치 409 `OPEN_PR`. 관문 `repo_delete_branch`(A) |

- 부품 오류는 `VCS_<코드>`(not_found 404·conflict 409·invalid 400·rate_limited 429·그 밖 502), 읽기 전용 연결 쓰기는 409 `READ_ONLY`, 관문 거부 409 `GATE_REJECTED`. 쓰기마다 활동(`from` human, point = 액션 이름)을 남기고 실패하면 `vcs.<액션>_failed`.
- 아직: 줄 코멘트·리뷰 승인, 충돌 해결, 브랜치 만들기는 없다. 목록 코멘트 수는 이슈 목록 한 쪽(100개)에서만 읽는다.

### 코딩 에이전트 (개발 역할, `coder.py`)
개발팀원이 개발 화면에 자기 기획을 쓰면 에이전트가 연결된 레포에서 실제로 구현한다.

- 받기: `POST /dev-tasks` `{title(필수, 200자), plan(필수, 8000자), done?(완료 기준, 한 줄에 하나), paths?(경로 힌트 20개), connection?(연결 id)}` → `data: {request_id, spec_id, task_id, status: "queued"}`. 요청(`from` dev, `via` dev_team, `작업 중`)·확정 기획서(`origin` dev_team, `handed_to_dev`, R1 = 제목·`paths` 힌트, `problem` = 기획 글, C1.. = 완료 기준 줄, 없으면 기본 한 줄)·작업 하나를 관문 `dev_task_create`(A) 뒤에 만든다. id는 planning 번호와 겹치지 않게 `REQ-D1`·`SPEC-D1`·`T-D1`, 사건은 `ctx_dev_0001`.
- 오류: 400 `INVALID_INPUT`(형식, 모르는 연결, 연결이 여럿인데 `connection` 없음) · 409 `NO_VCS`(연결 없음) · 409 `LLM_NOT_CONFIGURED`(모델·주소 없음, 아무것도 만들지 않음) · 409 `WRONG_ROLE`(역할 planning 서비스) · 409 `GATE_REJECTED`.
- 다시 시도: `POST /dev-tasks/{task_id}/retry` `{connection?}` → 같은 모양(`status: queued`). `failed`·`waiting_human`이고 PR을 아직 안 만든 작업만(관문 `dev_task_retry` A). 없으면 404 `NOT_FOUND`, 진행 중·끝남·PR 있음은 409 `NOT_RETRYABLE`(PR이 있으면 PR 화면 머지 버튼으로 판단).
- 구독형 일감: `POST /dev-tasks`에 `mode: "subscription"`을 넣으면 서버 에이전트를 띄우지 않고 대기열에 둔다. 개발자 본인 Claude Code가 MCP(`POST /mcp`, 도구 `list_tasks`·`claim_task`·`report_progress`·`report_pr`·`release_task`)나 REST(`/work/tasks/*`)로 가져가 PR을 올린다 (사람 토큰, 임대 `PLANNING_LEASE_MINUTES` 기본 30분, 관문 `dev_task_claim`·`dev_task_release` A, PR 잇기는 `generate_pr` B). 사용법 `backend/docs/dev-mcp.md`, 코드 `work_queue.py`·`work_api.py`.
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
