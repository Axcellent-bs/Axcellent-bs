# 도구 연결 (공용 연결 보관함 · 배포 도구 · DejaFix 연결) — 설계

2026-11-16, 예진 님과 정함. 사이트 연결 설계(`2026-11-16-site-connect-design.md`)의 다음 단계다. 이 문서는 **1번 묶음(도구 연결)** 만 다룬다. 사이트 데이터 연결(읽기·쓰기, 변환기)은 2번 묶음으로 따로 설계한다.

## 왜

- 외부 도구 연결이 서비스마다 따로 있다. GitHub 앱은 개발 AI 파일(`PLANNING_VCS_STORE`), 노션은 기획 파일, DejaFix는 `.env` 세 군데(`DEJAFIX_*`, `INFRA_DEJAFIX_URL`, `PLANNING_DEJAFIX_API_URL`)다. DejaFix는 화면으로 연결할 수 없고 서버 재시작이 필요하다.
- 인프라 AI의 롤백은 시연용 장애 주입 해제뿐이다. 고객사 배포 도구(GitHub Actions·Jenkins)로 실제로 되돌리려면 인프라 AI가 배포 도구 연결을 써야 한다.
- 인프라 AI는 끌 수 없는 필수 서비스(#1112)이고 개발 AI는 회사가 끌 수 있다. 장애 롤백이 개발 AI에 기대면 안 된다. 그래서 연결은 한 곳(보관함)에 두고 각 AI가 필요한 것을 꺼내 쓴다.

## 화면 배치 (정함)

```
시스템 관리
├ 회사 연결        고객사 사이트 (사이트 데이터 연결 — 2번 묶음)      [관리자]
├ 서비스 고르기                                                   [관리자]
├ 회원·서버 관리   회원 · PC·서버 · 접속 허용 + DejaFix 연결           [관리자]
└ 도구 연결        형상관리 · 배포 · 문서 · AI  (기존 '연결' 탭을 옮김)  [모두]
```

- 시스템 관리 메뉴는 모두에게 보이고, 관리자가 아니면 그 아래에 **도구 연결만** 보인다. 사이드바의 기존 "연결"은 없앤다(`/connections`는 `/system/tools`로 넘긴다).
- 도구 연결 안: 형상관리·배포·문서(회사 공용)는 관리자만 바꾸고 일반 회원은 상태만 본다. AI 탭(사람별 MCP 연결 안내)은 모두 쓴다.
- 장애 감지는 도구 연결에 두지 않는다. DejaFix 연결은 회원·서버 관리의 PC·서버 탭에 둔다(감시 대상과 같은 자리).
- 새로 연결하는 창은 맨 위 하나다: 도구 고르기(select) → 그 도구에 필요한 칸만 → 연결 확인 → 저장. 그 아래에는 **연결된 것만** 목록으로 보여 준다(상태·마지막 확인·다시 확인·끊기). 빈 칸을 늘어놓지 않는다.
- 칸마다 "어디서 발급하나요" 도움말을 단다. 붙여 넣은 비밀 값은 다시 보여 주지 않는다(앞 4글자 + `••••`).
- 권한 표시는 지금처럼 화면의 `account.admin`(데모 계정)으로 나누고, 실제 막기는 서버의 사람 토큰 검사다. 진짜 로그인(IF-CUS-015)은 이 묶음 밖이다.

## 공용 연결 보관함 (`ax_agent_template.tool_connections`)

회사 연결(`company_connection.py`)과 같은 방식이다.

- 파일: `AX_TOOL_CONNECTIONS_FILE`(run_all 기본 `$DATA/tool_connections.json`). 없으면 연결 없음.
- 모양: 맨 위가 목록인 JSON `[ {id, kind, name, config, secrets, checked_at, check_ok, check_detail, updated_at, updated_by} ]`
  - `kind`: 이번 묶음은 `github_app` · `github_actions` · `jenkins` · `dejafix`. 2번 묶음에서 `site_api` · `site_db`가 붙는다.
  - `config`: 비밀이 아닌 값(주소·레포·워크플로·작업 이름·버전 입력 이름·대상 서비스 등)
  - `secrets`: 개인 키·API 토큰·비밀번호. `public()`은 `secrets` 대신 `{이름: "••••"}`만 낸다(앞글자도 내지 않는다. 목록은 회원 모두가 본다. 개인 키는 `설정됨`만).
- 쓰기: dashboard-api 한 곳(예외 하나: 아래 PR 6의 개발 AI 켤 때 옮기기). 임시 파일을 `0600`으로 만들어 `os.replace`, 바꿀 때마다 `<파일>.history.jsonl`에 한 줄(언제·누가·무엇을·어떤 연결, 비밀 값 제외). 비밀 칸을 비워 보내면 이전 값을 그대로 둔다(개발 AI 저장소와 같은 규칙).
- 읽기: `read_all()` / `find(kind, **match)`. `(경로, mtime_ns, size)`가 바뀌었을 때만 다시 읽는다 → 재시작 없이 반영. 깨진 파일은 경고 한 번 + 없는 것으로 본다. 읽을 때도 `0600`으로 다시 맞춘다.
- 주소 정책: 주소가 있는 연결(Jenkins·DejaFix·GitHub Enterprise)은 `company_connection.check_host`를 그대로 쓴다(사내 주소 또는 허용 목록+https, 링크 로컬·메타데이터 차단).
- 비밀 값은 응답·로그·A2A·오류 문구에 넣지 않는다. 오류 문구는 비밀 값을 지우고 낸다.
- 암호화 저장은 하지 않는다(지금 다른 저장소와 같은 수준: 서버 계정만 읽는 파일). 사내 설치 전제다.

### GitHub 앱 인증을 공용으로

`dev_agent/vcs/github_app.py`(`GithubAppAuth`, `load_key`, `check_id`)를 `ax_agent_template/github_app.py`로 옮긴다. 의존은 `httpx`·`cryptography`와 오류 클래스뿐이다. 개발 AI 쪽 `dev_agent/vcs/github_app.py`는 공용 것을 다시 내보내고 오류만 `VcsError`로 바꿔 기존 import와 시험이 그대로 돈다. 공용 오류는 `ToolConnectionError(code, message)`.

## dashboard-api 경로 (`/api/dashboard/tool-connections`)

| 경로 | 하는 일 |
|---|---|
| `GET /` | 연결 목록(`public()`)과 종류별 입력 칸 정의(화면이 select와 칸을 그리는 데 씀) |
| `POST /test` | 저장하지 않고 연결 확인. 항목별 통과·이유 목록 |
| `POST /` · `PUT /{id}` | 다시 확인하고 모두 통과하면 저장 |
| `POST /{id}/check` | 저장된 연결 다시 확인(결과를 `checked_at`·`check_ok`·`check_detail`에 남김) |
| `DELETE /{id}` | 지우기. 이 연결을 가리키는 다른 연결(`github_actions` → `github_app`)이 있으면 409 |

쓰기는 `require_human_for_writes`. 계약은 `contracts/openapi/dashboard-api.json`에 추가만 한다. 확인 호출은 5초 제한, 시험은 `httpx.MockTransport`.

### 종류별 입력과 연결 확인

| 종류 | 입력 | 연결 확인 |
|---|---|---|
| `github_app` | 앱 ID, 설치 ID, 개인 키(.pem), (선택) GitHub Enterprise 주소(`https://<host>/api/v3`, 비우면 api.github.com) | 설치 토큰 받기 → 설치된 레포 수·권한 목록 |
| `github_actions` | GitHub 앱 연결 고르기, 레포, 배포 워크플로 파일, 버전 입력 이름(기본 `version`), 대상 서비스, (선택) 장애 부위 | 앱 권한에 `actions: write` · 워크플로가 있음 · `workflow_dispatch` 입력에 그 이름이 있음 · `run-name`에 그 입력(`${{ inputs.<이름> }}` 또는 `${{ github.event.inputs.<이름> }}`)이 있음 · 최근 실행 읽힘 |
| `jenkins` | 주소, 사용자 이름, API 토큰, 작업 이름, 버전 매개변수 이름(기본 `version`), 대상 서비스, (선택) 장애 부위 | `GET /job/<작업>/api/json` 200 · 매개변수 정의에 그 이름이 있음 · 최근 빌드 읽힘 |
| `dejafix` | DejaFix 백엔드 주소, viewer 계정 이름·비밀번호 | 로그인 성공 · `GET /api/incidents?status=problem` 읽힘(현재 장애 n건) |

- "대상 서비스"는 이 배포 도구가 배포하는 고객사 서비스 이름이다(인프라 AI 진단·롤백의 `service`와 같은 값). 한 서비스에 배포 도구는 하나만(두 번째는 409).
- "장애 부위"(`components`, 선택, 쉼표로 여럿)는 DejaFix component 태그와 같은 이름(예: `payment`)이다. 도구 실행으로 받은 배포에 이 값을 단다. 비우면 시연 이력에서 배운 구성요소 → 그것도 없으면 서비스 이름 하나를 쓴다. 진단은 장애 부위로 배포를 고르므로, 맞지 않으면 원인 후보가 없다.
- `run-name` 확인: 실행 목록에는 디스패치 입력 값이 안 나와서 실행 제목(`display_title`)에서 버전을 읽는다. `run-name`에 버전 입력이 없으면 버전이 커밋 앞자리가 되어 롤백 다시 배포를 할 수 없으므로 확인을 실패로 두고 "워크플로에 run-name: "deploy ${{ inputs.version }}"처럼 버전을 넣어 주세요"라고 안내한다.
- `dejafix`는 하나만 둔다.
- 워크플로 파일의 `workflow_dispatch` 입력은 `GET /repos/{repo}/contents/{path}`로 받은 YAML에서 읽는다(PyYAML은 agent-template 의존성에 이미 있다).
- Argo CD는 이번에 넣지 않는다(고객사 관리자가 API 키 계정을 만들어야 해서 "고르고·누르고·붙여 넣기" 원칙에 안 맞음).
- 확인 문구는 사람 말로: "워크플로에 version 입력이 없어요. workflow_dispatch 입력에 version을 추가해 주세요"처럼 무엇을 고치면 되는지 적는다.

## 인프라 AI: 배포 이력과 롤백

- 새 모듈 `infra_agent/deploy_tools.py`: 보관함의 `github_actions`·`jenkins` 연결로 어댑터를 만든다(`DeployTool` 프로토콜: `recent(limit) -> list[Deployment]`, `redeploy(version) -> RunRef`).
  - GitHub Actions: `GET /repos/{repo}/actions/workflows/{file}/runs` → 실행마다 버전(디스패치 입력 값은 실행 목록에 안 나오므로 `display_title`·`head_sha` 앞 7자리, 롤백으로 띄운 실행은 우리가 남긴 실행 기록과 맞춤), 시각, 결과, 실행한 사람. 다시 배포는 `POST .../workflows/{file}/dispatches` `{ref: 기본 브랜치, inputs: {<버전 입력>: <버전>}}`.
  - Jenkins: `GET /job/<작업>/api/json?tree=builds[number,result,timestamp,url,actions[parameters[name,value]]]{0,20}` → 버전 매개변수 값. 다시 배포는 `POST /job/<작업>/buildWithParameters?<매개변수>=<버전>`(API 토큰 기본 인증).
- 이력 미리 받기: 켤 때와 `INFRA_DEPLOY_POLL_SECONDS`(기본 120초)마다 연결된 도구의 최근 실행을 받아 `DeploymentHistory`에 넣는다(성공한 실행만 배포로, 우리가 롤백으로 띄운 실행은 롤백 표시). 진단은 받아 둔 이력을 바로 쓴다(장애 순간에 외부 호출을 기다리지 않음). 받기 실패는 로그 + 이전 이력 유지.
- 연결이 없는 서비스는 지금처럼 `deployments.json`(시연 이력)을 쓴다. 연결이 있으면 그 서비스의 시연 이력은 쓰지 않는다.
- 진단(`diagnose_incident`) 규칙은 그대로다. 이력 출처만 바뀐다.
- 롤백(`rollback_deployment`): 승인 관문(`execute`, B/C, planning 교차검증)은 그대로 한 번만. `_apply`에서 대상 서비스에 배포 도구 연결이 있으면 `redeploy(to_version)`을 부르고 출력에 `redeploy: {tool, run_id, url}`을 더한다. 장애 주입 해제·결제 버전 되돌리기는 연결이 없는 시연 경로로 남긴다.
  - 다시 배포가 실패하면(호출 오류·권한 없음·연결 확인 실패 상태) 다른 길로 넘어가지 않는다. outcome `redeploy_failed` + 사람에게 넘길 처리 순서("<도구>에서 <작업>을 <버전>으로 다시 실행")를 출력에 넣는다.
  - 다시 배포를 띄운 뒤 끝날 때까지 기다리지 않는다. 복구 확인(`verify_recovery`)이 지금처럼 지표로 본다.
- 계약: `RollbackDeploymentParams`·출력은 추가만 한다. 멱등 키는 그대로.
- `deployments.py` `_parse()`가 `applies_to`를 읽지 않는 문제(조사 중 발견)를 같이 고친다.

## DejaFix 연결을 보관함으로

- dejafix-proxy: 요청마다 보관함의 `dejafix` 연결(주소·계정)을 읽는다(바뀌면 세션을 새로 만든다). 연결이 없으면 지금처럼 `DEJAFIX_PROXY_TARGET`·`DEJAFIX_USERNAME`·`DEJAFIX_PASSWORD`. 둘 다 없으면 503 "DejaFix 연결 없음".
- `backend/scripts/run_all.sh`: 계정이 없어도 프록시를 띄운다(보관함으로 나중에 연결할 수 있게).
- 인프라 AI·개발 AI: `INFRA_DEJAFIX_URL`·`PLANNING_DEJAFIX_API_URL`이 없고 보관함에 `dejafix`가 있으면 프록시 주소(`agent_url("dejafix-proxy")`)를 쓴다. `config.DEFAULT_PORTS`에 `dejafix-proxy: 8030`을 더한다(지금은 없음). 인프라 감시 루프는 매 회차 주소를 다시 구해 재시작 없이 따라간다.
- 고객지원 암호화 키(`SUPPORT_DEJAFIX_KEY`)는 옮기지 않는다(양쪽이 같은 비밀을 나눠 갖는 구조라 따로 보안 검토).

## 화면

- `/system/tools` (`ToolConnections`): 기존 `DevTools`의 왼쪽 메뉴를 그대로 쓰고 "배포" 항목을 더한다.
  - 형상관리: 기존 레포 연결 화면(개발 AI API)을 그대로 + 맨 위 "GitHub 앱" 칸(보관함 `github_app`, 배포가 씀).
  - 배포: 맨 위 새 연결(select: GitHub Actions · Jenkins) → 칸 → 확인 → 저장. 아래 연결된 배포 도구 목록(대상 서비스·도구·최근 배포 버전·확인 상태·다시 확인·끊기).
  - 문서·AI: 기존 그대로 옮김.
- 회원·서버 관리 › PC·서버 탭 위에 "DejaFix 연결" 카드: 안 됨이면 주소·계정 칸 + 확인·저장, 됨이면 "연결됨 · 현재 장애 n건 · 마지막 확인" + 다시 확인·끊기(관리자만).
- 인프라 AI(장애 대응) 화면은 DejaFix 연결이 없을 때 "DejaFix가 연결되지 않아 장애 목록을 받지 못해요 — 회원·서버 관리에서 연결" 안내.
- 사이드바 `SHARED_NAV`의 "연결" 제거, `AdminShell` 시스템 관리 메뉴에 "도구 연결" 추가(관리자가 아니면 이것만). 개발 화면의 연결 아이콘·링크는 `/system/tools`로. `/connections`·`/system/devtools`는 `/system/tools`로 넘김.
- 모양은 `components/connect/parts`, `card`·`field`·`btn-*`·`chip`. 데이터가 없으면 지어내지 않는다.

## 나누는 PR (순서)

1. 보관함 + GitHub 앱 인증 공용화 (shared, 개발 AI는 다시 내보내기만)
2. dashboard-api `tool-connections` 경로와 종류별 확인 + 계약
3. 인프라 AI 배포 도구 어댑터·이력 미리 받기·롤백 다시 배포
4. DejaFix 연결을 보관함으로 (프록시·인프라·개발·run_all)
5. 화면: 시스템 관리 메뉴 정리, `/system/tools`(배포 탭), DejaFix 카드
6. (2번 묶음 전에) 개발 AI 레포 연결이 보관함 `github_app`을 고를 수 있게 하고, 예전 `vcs_connections.json`의 앱 정보를 보관함으로 옮기기. — 했음
   - 레포 연결에 `app_ref`(보관함 `github_app` id). `auth=app`이고 `app_ref`가 있으면 앱 ID·설치 ID·개인 키는 보관함 것을 쓰고 레포 연결에는 두지 않는다. 부품을 꺼낼 때마다 보관함 행을 다시 보고 바뀌었으면 부품을 다시 만든다(재시작 없이 반영). 보관함에서 앱이 지워졌으면 config 오류 "연결된 GitHub 앱을 찾지 못했어요. 도구 연결 › 형상관리에서 GitHub 앱을 다시 연결해 주세요".
   - 저장 API는 `app_ref`를 받고 보관함에 있는 `github_app`인지 확인한다. 응답(`public()`)에는 `app_ref`·`app_name`과 보관함의 앱 ID·설치 ID·키 넣었는지만 낸다.
   - 옮기기(보관함 쓰기의 유일한 예외): 개발 AI가 켤 때(`load_saved`) 한 번, 앱 정보를 직접 넣어 둔 화면 연결마다 같은 앱 ID·설치 ID·주소의 `github_app`을 찾아 가리키고, 없으면 `by="migration"`으로 새로 만든 뒤 레포 연결의 개인 키를 지운다. 다시 켜도 같다. 보관함 경로가 없거나 파일이 깨졌으면 옮기지 않고(경고 한 번) 예전 값으로 계속 돈다. 환경변수 연결(`PLANNING_VCS_*`)은 그대로.
   - 화면: 레포 연결 폼의 GitHub App은 앱 ID·설치 ID·.pem 대신 보관함 GitHub 앱 고르기(없으면 맨 위 GitHub 앱 칸으로 안내). 목록에는 가리키는 앱 이름.

PR마다 그 도메인 시험만 로컬에서 돌리고(나머지는 CI), 자동 머지 예약.

## 시험

- 보관함: 저장·읽기·mtime 바뀌면 다시 읽기·깨진 파일·비밀 칸 비우면 유지·`public()`에 비밀 없음·이력 줄에 비밀 없음·0600(윈도에서는 건너뜀).
- 확인: 종류별로 `MockTransport`로 통과·실패 이유(권한 없음, 입력 없음, 401, 시간 초과).
- 인프라: 가짜 `DeployTool`로 이력 → 진단 후보, 롤백 executed → `redeploy` 호출·출력, 실패 → `redeploy_failed` + 처리 순서, 연결 없는 서비스는 기존 시연 경로 그대로.
- 프록시: 보관함이 바뀌면 새 대상으로 로그인.
- 화면: 타입 검사·빌드, 관리자 아님 → 시스템 관리에 도구 연결만, 배포 목록에 지어낸 값 없음.

## 하지 않을 것 / 나중 일

- 사이트 데이터 연결(읽기·쓰기, 변환기 IF-CUS-008, 읽기 전용 DB) — 2번 묶음
- 플랫폼(카페24 등) 연결 — 보류(사업자 등록 필요)
- Argo CD, 감시 도구 직접 연결
- DejaFix가 Zabbix 말고 다른 감시 도구를 받는지, 고객사가 Zabbix↔DejaFix를 연결하는 방식 — DejaFix 쪽 검토(나중)
- 시연용 배포 워크플로(`.github/workflows`)는 자동화 파일이라 사람이 만든다. 없어도 시험은 가짜 어댑터로 돈다.
- 비밀 값 암호화 저장, 진짜 로그인
- 보관함 `DELETE`의 "쓰는 곳 있음"(409) 확인은 `github_actions` → `github_app`만 본다. 개발 AI 레포 연결이 `github_app`을 가리키는지는 dashboard-api가 개발 AI 저장소를 읽을 수 없어 보지 않는다(알려진 빈틈). 지우면 개발 AI는 위 config 오류로 다시 연결하라고 알린다.
