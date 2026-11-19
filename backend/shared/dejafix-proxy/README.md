# dejafix-proxy
포트: 8030 · LLM 없음, 코드만

## 1. 역할
서버(VM)의 장애 대응 탭이 DejaFix 실데이터를 보게 하는 작은 프록시. DejaFix는 모든 API에 로그인 토큰을 요구하는데 서버 nginx는 로그인·토큰 갱신을 못 하므로, 이 서비스가 대신 로그인하고 토큰이 만료되면 다시 받는다. 개발 서버 프록시(`frontend/dejafix-proxy.ts`)와 같은 동작이다.

- **부르는 쪽 토큰**을 먼저 확인한다(`auth.py`, 총괄·기획의 `check_caller`와 같은 규칙). `Authorization: Bearer <토큰>`
  - 에이전트 토큰: `AX_AGENT_TOKENS`(인프라·개발 AI가 `tool_connections.dejafix_proxy_headers()`로 붙임)
  - 사람 토큰: `SAFETY_HUMAN_TOKENS`의 `이름:토큰`(화면이 붙임. 서버에서는 gateway nginx가 바꿔 붙임)
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
| `SAFETY_HUMAN_TOKENS` | (없음) | 사람 토큰 `이름:토큰,…`. 없으면 화면 호출은 모두 401 (보관함과 상관없이 필요) |

## 3. 응답
DejaFix 응답을 상태 코드·본문 그대로 돌려준다. 이 서비스가 직접 내는 오류는 `{error: {code, message}}` 형식이다.

| 상태 | code | 경우 |
| --- | --- | --- |
| 401 | `AUTH_REQUIRED` | 토큰 없음 |
| 401 | `TOKEN_INVALID` | 모르는 토큰(자리표시자 포함) |
| 405 | `METHOD_NOT_ALLOWED` | GET이 아님 |
| 400 | `INVALID_PATH` | 경로 조작(인코딩·`..`·`//` 등) |
| 404 | `NOT_FOUND` | 허용한 조회 세 가지가 아님 |
| 503 | `DEJAFIX_NOT_CONNECTED` | 보관함에도 환경변수에도 연결이 없음 |
| 502 | `DEJAFIX_UNREACHABLE` | DejaFix 백엔드에 연결할 수 없음, 또는 보관함 주소가 정책에 맞지 않음 |

`GET /healthz`는 DejaFix를 부르지 않고 `{"status": "ok"}`만 준다.

## 4. 연결
화면은 `/dejafix-api/*`를 부른다. 서버 gateway(nginx)가 `/dejafix-api/` 접두사를 떼고 `http://dejafix-proxy:8030/`으로 넘기면 된다 (예: `/dejafix-api/api/incidents?status=problem` → `/api/incidents?status=problem`). gateway는 그때 공용 사람 토큰을 `proxy_set_header Authorization "Bearer <토큰>"`으로 붙여야 한다(서버 화면 번들에는 실제 토큰을 넣지 않는다). gateway 설정은 ax-frontend 레포의 `nginx/`에 있어서 이 레포에서는 바꾸지 않는다.

## 5. 실행·테스트
- 서버: `docker compose -f infra/compose.yml up dejafix-proxy`, 또는 `uv run uvicorn ax_dejafix_proxy.main:app --port 8030`
- 로컬: `bash scripts/run_all.sh`가 같이 띄운다. 계정이 없어도 띄운다(연결 전 503). 인프라 AI는 보관함에 `dejafix`가 있으면 이 프록시를 쓰고, 환경변수로 정하려면 `INFRA_DEJAFIX_URL=http://127.0.0.1:8030`
- 테스트: `uv run pytest shared/dejafix-proxy` (DejaFix는 가짜 응답으로 대체, 실제 DejaFix 불필요)
