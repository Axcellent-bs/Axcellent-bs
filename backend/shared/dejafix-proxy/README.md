# dejafix-proxy
포트: 8030 · LLM 없음, 코드만

## 1. 역할
서버(VM)의 장애 대응 탭이 DejaFix 실데이터를 보게 하는 작은 프록시. DejaFix는 모든 API에 로그인 토큰을 요구하는데 서버 nginx는 로그인·토큰 갱신을 못 하므로, 이 서비스가 대신 로그인하고 토큰이 만료되면 다시 받는다. 개발 서버 프록시(`frontend/dejafix-proxy.ts`)와 같은 동작이다.

- **부르는 쪽 토큰**을 먼저 확인한다(`auth.py`, 총괄·기획의 `check_caller`와 같은 규칙). `Authorization: Bearer <토큰>`
  - 에이전트 토큰: `AX_AGENT_TOKENS`(인프라·개발 AI가 `tool_connections.dejafix_proxy_headers()`로 붙임)
  - 사람 토큰: 로그인 세션 토큰(`axs1.`, `ax_agent_template.human_auth`. **관리자·일반회원 등 역할 상관없이** 통과, 장애 조회는 읽기라서)과
    공용 사람 토큰(`SAFETY_HUMAN_TOKENS`의 `이름:토큰`, `AX_ALLOW_SHARED_HUMAN_TOKEN=0`이 되기 전까지). 화면이 붙인다
  - 세션 토큰은 `AX_SESSION_SECRET`(다른 서비스와 같은 값)으로 검사한다. 만료·로그아웃·비밀번호 변경이면 401 `SESSION_EXPIRED`·`SESSION_REVOKED`·`SESSION_ENDED`
  - 목 토큰 `human:`·`mock:`은 `DEJAFIX_PROXY_MOCK=1`(테스트 전용)일 때만. 자리표시자 `data-via-nginx`는 거부
- **GET만** 넘긴다. 그 밖의 메서드는 405 `METHOD_NOT_ALLOWED`.
- 디코딩 전 경로(`raw_path`)에 `%`, `.`·`..` 구간, `//`, 역슬래시, 제어 문자가 있으면 400 `INVALID_PATH`.
- **정해 둔 조회 세 가지만** 넘긴다: `/api/incidents`(쿼리 허용), `/api/incidents/{숫자 id}`, `/api/dashboard/summary`. 그 밖(`/api/auth/*` 포함)은 404.
- 계정은 **viewer 권한**으로만 쓴다. 요청마다 공용 보관함의 `dejafix` 연결(주소·계정·비밀번호)을 먼저 보고, 없으면 환경변수를 쓴다. 대상·계정이 바뀌면 다시 로그인한다. 보관함 주소는 `company_connection.check_host` 정책을 통과해야 부른다. 응답·로그에 싣지 않는다.

## 2. 환경변수 (`.env`, 커밋 금지; 보관함에 `dejafix`가 없을 때만 쓴다)
| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `DEJAFIX_PROXY_TARGET` | `http://127.0.0.1:18000` | 서버에서 닿는 DejaFix 백엔드 주소 (고정 주소로 정해야 한다) |
| `DEJAFIX_USERNAME` | (없음) | DejaFix viewer 계정 |
| `DEJAFIX_PASSWORD` | (없음) | 그 비밀번호 |
| `AX_AGENT_TOKENS` | (없음) | 에이전트 토큰 명부. 없으면 AI 호출은 모두 401 (보관함과 상관없이 필요) |
| `AX_SESSION_SECRET` | (없음) | 로그인 세션 토큰 서명 키(32자 이상, 다른 서비스와 같은 값). 없으면 세션 토큰은 모두 401 |
| `SAFETY_HUMAN_TOKENS` | (없음) | 공용 사람 토큰 `이름:토큰,…` (로그인 세션으로 옮기는 동안) |

## 3. 응답
DejaFix 응답을 상태 코드·본문 그대로 돌려준다. 이 서비스가 직접 내는 오류는 `{error: {code, message}}` 형식이다.

| 상태 | code | 경우 |
| --- | --- | --- |
| 401 | `AUTH_REQUIRED` | 토큰 없음 |
| 401 | `TOKEN_INVALID` | 모르는 토큰(자리표시자·위조 세션 토큰 포함) |
| 401 | `SESSION_EXPIRED`·`SESSION_REVOKED`·`SESSION_ENDED` | 세션 토큰이 만료·로그아웃·비밀번호 변경으로 끊김 |
| 405 | `METHOD_NOT_ALLOWED` | GET이 아님 |
| 400 | `INVALID_PATH` | 경로 조작(인코딩·`..`·`//` 등) |
| 404 | `NOT_FOUND` | 허용한 조회 세 가지가 아님 |
| 503 | `DEJAFIX_NOT_CONNECTED` | 보관함에도 환경변수에도 연결이 없음 |
| 502 | `DEJAFIX_UNREACHABLE` | DejaFix 백엔드에 연결할 수 없음, 또는 보관함 주소가 정책에 맞지 않음 |

`GET /healthz`는 토큰 없이 열고, DejaFix를 부르지 않고 `{"status": "ok"}`만 준다.

`GET /status`는 화면의 DejaFix 연결 카드(회원·서버 관리 › PC·서버)가 읽는다. DejaFix에 실제로 로그인해 보므로 부르는 쪽 토큰을 위와 같이 확인한다(없으면 401). `{source, reachable, detail}`
- `source`: `builtin`(환경변수, AXcellent와 같이 설치된 DejaFix) · `custom`(화면에서 저장한 다른 DejaFix, 보관함) · `none`(둘 다 없음)
- `reachable`: 로그인 뒤 `GET /api/incidents?status=problem&limit=1`이 200이면 `true`, 아니면 `false`, 대상이 없으면 `null`
- 주소·계정·비밀번호·토큰은 싣지 않는다

## 4. 연결
화면은 `/dejafix-api/*`를 부른다. 서버 gateway(nginx)가 `/dejafix-api/` 접두사를 떼고 `http://dejafix-proxy:8030/`으로 넘기면 된다 (예: `/dejafix-api/api/incidents?status=problem` → `/api/incidents?status=problem`). 화면은 로그인 세션 토큰을 `Authorization`으로 붙이므로 gateway는 그 헤더를 그대로 넘긴다. 로그인 세션 없이 공용 사람 토큰 자리표시자(`human-via-nginx`)를 보내는 화면이면 다른 사람 경로처럼 gateway가 진짜 공용 토큰으로 바꿔 붙인다(세션 토큰 `axs1.`은 덮어쓰지 않는다). gateway 설정은 ax-frontend 레포의 `nginx/`에 있어서 이 레포에서는 바꾸지 않는다.

## 5. 실행·테스트
- 서버: `docker compose -f infra/compose.yml up dejafix-proxy`, 또는 `uv run uvicorn ax_dejafix_proxy.main:app --port 8030`
- 로컬: `bash scripts/run_all.sh`가 같이 띄운다. 계정이 없어도 띄운다(연결 전 503). 인프라 AI는 보관함에 `dejafix`가 있으면 이 프록시를 쓰고, 환경변수로 정하려면 `INFRA_DEJAFIX_URL=http://127.0.0.1:8030`
- 테스트: `uv run pytest shared/dejafix-proxy` (DejaFix는 가짜 응답으로 대체, 실제 DejaFix 불필요)
