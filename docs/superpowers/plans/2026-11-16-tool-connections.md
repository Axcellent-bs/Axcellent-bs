# 도구 연결 (1번 묶음) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 외부 도구 연결을 공용 보관함 하나로 모으고, 인프라 AI 롤백이 GitHub Actions·Jenkins로 실제 다시 배포하며, DejaFix·배포 도구를 화면(시스템 관리)에서 연결한다.

**Architecture:** `ax_agent_template.tool_connections`(파일 보관함, mtime 다시 읽기)를 dashboard-api만 쓰고 인프라·개발·dejafix-proxy가 읽는다. 연결 확인은 dashboard-api가 종류별로 한다. 인프라 AI는 보관함의 배포 도구로 이력을 미리 받고 승인된 롤백에서 버전을 넣어 다시 배포한다.

**Tech Stack:** Python 3.12 · FastAPI · httpx(MockTransport 시험) · cryptography · PyYAML / React 19 · TypeScript · Tailwind

**Spec:** `docs/superpowers/specs/2026-11-16-tool-connections-design.md`

## Global Constraints

- 고치는 곳은 `frontend/`·`backend/`·`docs/`. `.github/`·`.claude/`·`CLAUDE.md`·`uv.lock` 손대지 않음(`pyproject.toml`을 안 바꾸면 lock도 안 바뀜)
- 계약(`shared/contracts`, `openapi/dashboard-api.json`)은 추가만
- `domains`끼리 import 금지. 공용 코드는 `shared/agent-template`
- 비밀 값은 응답·로그·오류 문구·이력 줄·A2A에 넣지 않음. 화면에는 `앞4글자••••`, 개인 키는 `설정됨`
- 보관함 파일 쓰기는 `0600` 임시 파일 + `os.replace`
- 주소가 있는 연결은 `company_connection.check_host` 통과해야 저장
- 외부 확인 호출 제한 5초
- 화면은 데이터를 지어내지 않음. 공용 클래스(`card`·`field`·`btn-*`·`chip`)와 `components/connect/parts`
- 브랜치 `feat/<이름>`, PR마다 자동 머지 예약, 로컬 시험은 고친 도메인만

## Review Focus

1. 확인 실패 오류 문구에 토큰·비밀번호가 섞여 나옴(httpx 예외 문구, 401 본문) → 문구에 비밀 값이 없어야 함 (Task 2.2 시험)
2. `github_actions`가 가리키는 `github_app`을 지우려 함 → 409, 둘 다 남음 (Task 2.1 시험)
3. 보관함 파일이 깨졌거나 사라진 동안 인프라 이력 받기 → 이전 이력 유지, 진단 계속 (Task 3.2 시험)
4. 버전 값에 공백·`&`·한글 → Jenkins 쿼리·GitHub 입력에 안전히 들어감(인코딩) (Task 3.1 시험)
5. 비밀 칸을 비우고 다시 저장(이름만 바꿈) → 이전 비밀 유지, 다시 확인도 이전 비밀로 (Task 1.1·2.1 시험)

---

## PR 1 — 보관함 + GitHub 앱 인증 공용화 (브랜치 `feat/tool-connections-vault`)

### Task 1.1: 공용 연결 보관함

**Files:**
- Create: `backend/shared/agent-template/src/ax_agent_template/tool_connections.py`
- Test: `backend/shared/agent-template/tests/test_tool_connections.py`

**Interfaces:**
- Produces:
  - `FILE_ENV = "AX_TOOL_CONNECTIONS_FILE"`, `KINDS = ("github_app", "github_actions", "jenkins", "dejafix")`
  - `class ToolConnectionError(Exception)`: `code: str`, `message: str`
  - `@dataclass(frozen=True) class ToolConnection`: `id: str, kind: str, name: str, config: dict[str, Any], secrets: dict[str, str] (repr=False), checked_at: str | None, check_ok: bool | None, check_detail: str | None, updated_at: str | None, updated_by: str | None`; `public() -> dict` (secrets 대신 `secrets_set: {이름: 마스크}`), `secret_values() -> list[str]`
  - `mask(value: str, *, key: bool = False) -> str` (`key`면 `"설정됨"`, 4글자 미만이면 `"••••"`, 아니면 `앞4 + "••••"`)
  - `read_all() -> list[ToolConnection]` (mtime·size 캐시, 깨진 파일 경고 한 번 + `[]`, 읽을 때 0600으로 맞춤, 윈도는 chmod 실패 무시)
  - `get(conn_id: str) -> ToolConnection | None`, `find(kind: str, **config_match: Any) -> ToolConnection | None`
  - `upsert(kind, name, config, secrets, *, conn_id: str | None, by: str) -> ToolConnection` (빈 비밀 값은 이전 값 유지, 새 id는 `<kind>-<n>`)
  - `record_check(conn_id, ok: bool, detail: str) -> ToolConnection`
  - `delete(conn_id, *, by: str) -> bool`
  - 이력: `<파일>.history.jsonl` 한 줄 `{at, by, action, id, kind, name}` (비밀·config 없음)

- [ ] **Step 1: 실패하는 시험 쓰기** — `test_upsert_then_read_all`, `test_blank_secret_keeps_previous`, `test_public_has_no_secret_values`(public 직렬화 문자열에 비밀 값 없음), `test_history_line_has_no_secret`, `test_reload_when_file_changes`, `test_broken_file_reads_as_empty`, `test_no_env_means_empty`, `test_written_file_is_0600`(`skipif(os.name == "nt")`). 경로는 `monkeypatch.setenv(FILE_ENV, tmp_path/...)`.
- [ ] **Step 2: 실패 확인** — `cd backend && uv run pytest -q shared/agent-template/tests/test_tool_connections.py` → ImportError
- [ ] **Step 3: 구현** — `company_connection.py`의 캐시·이력 방식을 따르고, 0600 쓰기는 `dev_agent/vcs/__init__.py` `_save()`와 같은 `os.open(..., 0o600)` + `os.replace`.
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `feat(agent-template): 공용 도구 연결 보관함 tool_connections`

### Task 1.2: GitHub 앱 인증을 공용으로

**Files:**
- Create: `backend/shared/agent-template/src/ax_agent_template/github_app.py` (`dev_agent/vcs/github_app.py` 내용 이동, 오류는 `ToolConnectionError("config", ...)`)
- Modify: `backend/domains/dev/src/dev_agent/vcs/github_app.py` → 공용 것을 다시 내보내고 `ToolConnectionError`를 `VcsError(code, message)`로 바꿔 던지는 얇은 감싸기
- Test: `backend/shared/agent-template/tests/test_github_app.py` (개발 쪽 시험은 그대로 둠)

**Interfaces:**
- Produces: `ax_agent_template.github_app.GithubAppAuth(app_id, installation_id, private_key, *, clock=None)`, `.jwt() -> str`, `async .token(client: httpx.AsyncClient, base_url: str) -> str`, `.secrets() -> list[str]`, `.permissions: dict[str, str]`; `load_key(pem)`; `check_id(raw, what) -> str`

- [ ] **Step 1: 공용 시험 쓰기** — `test_bad_pem_raises_config`, `test_token_cached_until_refresh_window`, `test_permissions_filled_after_token`(MockTransport)
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 옮기기와 개발 쪽 감싸기**
- [ ] **Step 4: 두 쪽 시험 통과** — `uv run pytest -q shared/agent-template domains/dev`
- [ ] **Step 5: 커밋** — `refactor(agent-template): GitHub 앱 인증을 공용으로 옮기고 개발 AI는 다시 내보낸다`

PR 1 마무리: `ruff check`·`ruff format --check`(고친 파일)·`mypy`(고친 패키지) → PR → 자동 머지 예약

---

## PR 2 — dashboard-api `tool-connections` (브랜치 `feat/tool-connections-api`)

### Task 2.1: 경로

**Files:**
- Create: `backend/shared/dashboard-api/src/ax_dashboard_api/tool_connections.py`
- Modify: `backend/shared/dashboard-api/src/ax_dashboard_api/main.py`, `backend/shared/contracts/openapi/dashboard-api.json`
- Test: `backend/shared/dashboard-api/tests/test_tool_connections_routes.py`

**Interfaces:**
- Consumes: Task 1.1, Task 2.2 `run_checks`
- Produces: `router` (prefix `/api/dashboard/tool-connections`, `Depends(require_human_for_writes)`); `FIELDS: dict[kind, list[{key, label, secret: bool, required: bool, help: str}]]`
  - `GET ""` → `ok({"connections": [public...], "fields": FIELDS})`
  - `POST "/test"` body `{kind, name, config, secrets, id?}` → `ok({"ok": bool, "checks": [...]})` (저장 안 함, `id`가 있으면 빈 비밀은 저장된 값으로 채워 확인)
  - `POST ""` / `PUT "/{id}"` → 확인 모두 통과하면 `upsert` + `record_check`, 아니면 422 `CHECK_FAILED` + checks
  - `POST "/{id}/check"` → `record_check` 결과
  - `DELETE "/{id}"` → 참조 중이면 409 `IN_USE`, 없으면 404
  - `github_actions`·`jenkins`는 `config.service` 하나에 하나(409 `SERVICE_TAKEN`), `dejafix`는 하나(409 `ALREADY_CONNECTED`), 주소 칸은 `check_host` 실패 시 422 `INVALID_URL`

- [ ] **Step 1: 시험 쓰기** — `test_list_has_fields_and_no_secrets`, `test_create_runs_checks_and_saves`, `test_failed_check_does_not_save`, `test_update_with_blank_secret_uses_saved_secret`, `test_delete_app_in_use_is_409`, `test_second_deploy_tool_for_same_service_is_409`, `test_write_needs_human_token`, `test_private_url_policy_applies_to_jenkins`. `run_checks`는 monkeypatch로 바꿔 끼움.
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — `company_connection.py` 라우터 모양(`ApiError`, `ok`, `request.state.human`)을 따름
- [ ] **Step 4: 통과 확인** + `shared/dashboard-api` 전체 시험(계약 시험 포함)
- [ ] **Step 5: 커밋**

### Task 2.2: 종류별 연결 확인

**Files:**
- Create: `backend/shared/dashboard-api/src/ax_dashboard_api/tool_checks.py`
- Test: `backend/shared/dashboard-api/tests/test_tool_checks.py`

**Interfaces:**
- Produces: `TIMEOUT_SECONDS = 5.0`, `TRANSPORT: httpx.AsyncBaseTransport | None = None`; `Check`은 `company_connection.Check` 재사용; `async run_checks(kind: str, config: dict, secrets: dict, *, lookup: Callable[[str], ToolConnection | None]) -> list[Check]`
  - `github_app`: 설치 토큰 → `GET /installation/repositories?per_page=1`(총 수) → 권한 목록
  - `github_actions`: `lookup(config["app"])`로 앱 → 권한 `actions` = `write` → `GET /repos/{repo}/contents/{workflow_path}`(base64 YAML) → `workflow_dispatch.inputs`에 `config["version_input"]` → `GET /repos/{repo}/actions/workflows/{file}/runs?per_page=1`
  - `jenkins`: 기본 인증 `GET {url}/job/{job}/api/json?tree=property[parameterDefinitions[name]],builds[number]{0,1}` → 매개변수에 `config["version_param"]`
  - `dejafix`: dejafix-proxy `DejaFixSession`과 같은 로그인 경로·본문 → `GET /api/incidents?status=problem` → detail `현재 장애 n건`
  - 모든 detail은 입력·저장 비밀 값을 `••••`로 바꾼 뒤 냄

- [ ] **Step 1: 시험 쓰기** (MockTransport) — 종류마다 통과 1개 + `test_actions_missing_version_input_says_how_to_fix`(detail에 `workflow_dispatch`·`version`), `test_actions_without_actions_write_permission`, `test_jenkins_401`, `test_jenkins_missing_param`, `test_dejafix_login_fails`, `test_timeout_detail`, `test_detail_never_contains_secret`
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — YAML은 `yaml.safe_load`, 키 `on`은 PyYAML이 `True`로 읽으니 둘 다 봄
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — PR 2 마무리(Task 2.1·2.2 한 PR)

---

## PR 3 — 인프라 AI 배포 도구 (브랜치 `feat/infra-deploy-tools`)

### Task 3.1: 배포 도구 어댑터

**Files:**
- Create: `backend/domains/infra-dejafix/src/infra_agent/deploy_tools.py`
- Test: `backend/domains/infra-dejafix/tests/test_deploy_tools.py`

**Interfaces:**
- Consumes: `ax_agent_template.tool_connections.find/get/read_all`, `ax_agent_template.github_app.GithubAppAuth`
- Produces:
  - `@dataclass(frozen=True) class ToolRun`: `version: str, at: datetime, ok: bool, actor: str | None, url: str | None, run_id: str`
  - `@dataclass(frozen=True) class RunRef`: `tool: str, run_id: str | None, url: str | None`
  - `class DeployTool(Protocol)`: `service: str`, `tool: str`, `async recent(limit: int = 20) -> list[ToolRun]`, `async redeploy(version: str) -> RunRef`, `describe(version: str) -> str`
  - `GithubActionsTool`, `JenkinsTool` (둘 다 `transport: httpx.AsyncBaseTransport | None` 받음)
  - `tool_for(service: str) -> DeployTool | None`, `all_tools() -> list[DeployTool]`
- [ ] **Step 1: 시험 쓰기** (MockTransport) — `test_actions_recent_maps_runs`, `test_actions_redeploy_posts_dispatch_with_input`(본문 `{"ref": <기본 브랜치>, "inputs": {"version": "v2.4.0"}}`), `test_jenkins_recent_reads_param`, `test_jenkins_redeploy_encodes_version`(버전 `"v1 & 한글"`), `test_tool_for_unknown_service_is_none`, `test_redeploy_error_has_no_secret`
- [ ] **Step 2–4: 실패 확인 → 구현 → 통과** (`uv run pytest -q domains/infra-dejafix/tests/test_deploy_tools.py`). GitHub 기본 브랜치는 `GET /repos/{repo}` `default_branch`
- [ ] **Step 5: 커밋**

### Task 3.2: 이력 미리 받기와 진단 연결

**Files:**
- Modify: `backend/domains/infra-dejafix/src/infra_agent/deployments.py` (`_parse`가 `applies_to` 읽기, `replace_service(service, runs: list[ToolRun])` 추가 — 그 서비스의 시연 이력을 도구 이력으로 바꿈, 롤백 표시는 `run_id`로 유지)
- Create: `backend/domains/infra-dejafix/src/infra_agent/deploy_poll.py` (`POLL_ENV = "INFRA_DEPLOY_POLL_SECONDS"`, 기본 120; `async poll_once(history, tools: list[DeployTool]) -> None`; `async run_forever(history) -> None`)
- Modify: `backend/domains/infra-dejafix/src/infra_agent/main.py` (lifespan에서 `run_forever`, 목 모드에서는 안 띄움 — 템플릿 앱은 `on_startup`이 안 도니 lifespan 감싸기)
- Test: `backend/domains/infra-dejafix/tests/test_deploy_poll.py`, 기존 deployments 시험에 `applies_to` 추가

- [ ] **Step 1: 시험 쓰기** — `test_poll_replaces_demo_rows_for_connected_service`, `test_poll_keeps_previous_rows_on_error`, `test_poll_with_broken_vault_keeps_rows`, `test_diagnose_uses_polled_rows`, `test_parse_reads_applies_to`, `test_poll_really_starts_with_server`(TestClient lifespan)
- [ ] **Step 2–4**
- [ ] **Step 5: 커밋**

### Task 3.3: 롤백에서 다시 배포

**Files:**
- Modify: `backend/domains/infra-dejafix/src/infra_agent/handlers.py` (`_apply`: `deploy_tools.tool_for(service)`가 있으면 `redeploy(to_version)` → 출력 `redeploy: {tool, run_id, url}`, `history.mark_rolled_back`; 실패면 outcome `redeploy_failed` + 출력 `handoff_steps: [describe(to_version)]`. 도구가 없으면 지금 경로 그대로)
- Modify: `backend/domains/infra-dejafix/README.md` (`5. 하지 않는 것`의 배포 도구 문장 갱신)
- Test: `backend/domains/infra-dejafix/tests/test_rollback_redeploy.py`

- [ ] **Step 1: 시험 쓰기** — `test_executed_rollback_redeploys_previous_good`, `test_held_rollback_does_not_redeploy`, `test_redeploy_failure_returns_handoff_steps`(fault 해제 안 부름), `test_service_without_tool_keeps_fault_release_path`, `test_idempotent_retry_does_not_redeploy_twice`
- [ ] **Step 2–4** (`uv run pytest -q domains/infra-dejafix`)
- [ ] **Step 5: 커밋** — PR 3 마무리

---

## PR 4 — DejaFix 연결을 보관함으로 (브랜치 `feat/dejafix-from-vault`)

### Task 4.1: 프록시와 주소 찾기

**Files:**
- Modify: `backend/shared/dejafix-proxy/src/ax_dejafix_proxy/main.py` (요청마다 `_target()`: 보관함 `dejafix` → 없으면 환경 변수 → 없으면 503 `DEJAFIX_NOT_CONNECTED`; 대상·계정이 바뀌면 세션 새로 만듦)
- Modify: `backend/shared/agent-template/src/ax_agent_template/config.py` (`DEFAULT_PORTS["dejafix-proxy"] = 8030`)
- Modify: `backend/domains/infra-dejafix/src/infra_agent/dejafix_watch.py` (매 회차 `_url()`: `INFRA_DEJAFIX_URL` → 보관함에 `dejafix` 있으면 `agent_url("dejafix-proxy")` → 없으면 이번 회차 건너뜀; 시작 조건도 이 규칙), `chat/facts.py`의 `configured`도 같은 함수
- Modify: `backend/domains/dev/src/dev_agent/deploy_signals.py` (`PLANNING_DEJAFIX_API_URL` → 보관함 → 프록시 주소)
- Modify: `backend/scripts/run_all.sh` (계정 없어도 dejafix-proxy 띄움), `backend/shared/dejafix-proxy/README.md`, `backend/README.md` 해당 줄
- Test: `backend/shared/dejafix-proxy/tests/test_vault_target.py`, infra `test_dejafix_watch.py`(추가), dev deploy_signals 시험(추가)

- [ ] **Step 1: 시험 쓰기** — `test_proxy_uses_vault_target`, `test_proxy_relogins_when_vault_changes`, `test_proxy_503_without_any_connection`, `test_watch_starts_when_vault_gets_dejafix`, `test_env_url_wins_over_vault`, `test_dev_signals_use_proxy_when_vault_has_dejafix`
- [ ] **Step 2–4** (고친 패키지 시험만)
- [ ] **Step 5: 커밋** — PR 4 마무리

---

## PR 5 — 화면 (브랜치 `feat/frontend-tool-connections`)

### Task 5.1: API 클라이언트와 메뉴·경로

**Files:**
- Create: `frontend/src/lib/toolConnections.ts` (`type ToolKind = 'github_app' | 'github_actions' | 'jenkins' | 'dejafix'`; `listTools()`, `testTool(body)`, `saveTool(body, id?)`, `checkTool(id)`, `deleteTool(id)` — `companyConnection.ts`의 호출 방식 그대로)
- Modify: `frontend/src/components/admin/AdminShell.tsx` (`SYSTEM_MENU`에 `{ href: "/system/tools", label: "도구 연결" }`; 관리자가 아니면 시스템 관리 아래 이것만; `SHARED_NAV`의 `/connections` 제거), `frontend/src/demo/services.ts`·`frontend/src/demo/session.tsx` (`/system/tools`는 모두 열 수 있게), `frontend/src/App.tsx` (`/system/tools` → `ToolConnections`; `/connections`·`/system/devtools` → `/system/tools`), `frontend/src/pages/Dev.tsx`·`frontend/src/components/dev/coding.tsx`의 `/connections` 링크
- Test: 메뉴 함수 단위 시험(관리자 아님 → 시스템 관리 아래 `/system/tools`만, 관리자 → 네 개)

- [ ] **Step 1–4: 시험 → 구현 → `npm run typecheck` · `npx vite build` · `npx vitest run <고친 시험>`**
- [ ] **Step 5: 커밋**

### Task 5.2: 도구 연결 화면과 배포 탭

**Files:**
- Create: `frontend/src/demo/ToolConnections.tsx` (기존 `DevTools.tsx`의 왼쪽 메뉴를 쓰고 그룹 `배포` › `deploy` 추가; 형상관리 위에 `GithubAppCard`; 회사 공용 탭은 관리자가 아니면 읽기 전용)
- Create: `frontend/src/components/connect/DeployTools.tsx` (맨 위: select(GitHub Actions·Jenkins) → `fields[kind]`대로 칸(`github_actions`의 앱은 연결된 `github_app` 중 select) → "연결 확인" 결과 목록 → "저장". 아래: 연결된 배포 도구만(대상 서비스·도구·마지막 확인·상태 chip·다시 확인·끊기 확인 단계). 없으면 빈 상태 문구)
- Create: `frontend/src/components/connect/GithubAppCard.tsx`
- Modify: `frontend/src/demo/DevTools.tsx` → `ToolConnections`로 합치고 중복 없앰
- Test: `frontend/src/components/connect/DeployTools.test.tsx` — 연결 없음 → 빈 상태, select 바꾸면 칸이 바뀜, 확인 실패 detail 표시, 저장 후 비밀 칸 비워짐, 관리자 아님 → 저장 버튼 없음

- [ ] **Step 1–4**
- [ ] **Step 5: 커밋**

### Task 5.3: DejaFix 카드와 장애 대응 안내

**Files:**
- Create: `frontend/src/components/connect/DejafixConnect.tsx` (안 됨: 주소·계정 칸 + 확인·저장 / 됨: "연결됨 · 현재 장애 n건 · 마지막 확인" + 다시 확인·끊기, 관리자만; n건은 확인 결과 detail에서만)
- Modify: `frontend/src/demo/MembersPage.tsx` (`pcs` 탭 위에 카드)
- Modify: 인프라 AI 장애 목록 화면(DejaFix 미연결이면 안내 + `/system/members?tab=pcs` 링크)
- Test: `DejafixConnect.test.tsx`

- [ ] **Step 1–4**
- [ ] **Step 5: 커밋** — PR 5 마무리

---

## PR 6 (다음 계획)

개발 AI 레포 연결이 보관함 `github_app`을 고르게 하고 `vcs_connections.json`의 앱 정보를 옮기기. PR 5까지 끝난 뒤 따로 계획을 쓴다.
