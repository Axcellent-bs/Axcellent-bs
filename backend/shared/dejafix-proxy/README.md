# dejafix-proxy
포트: 8030 · LLM 없음, 코드만

## 1. 역할
서버(VM)의 장애 대응 탭이 DejaFix 실데이터를 보게 하는 작은 프록시. DejaFix는 모든 API에 로그인 토큰을 요구하는데 서버 nginx는 로그인·토큰 갱신을 못 하므로, 이 서비스가 대신 로그인하고 토큰이 만료되면 다시 받는다. 개발 서버 프록시(`frontend/dejafix-proxy.ts`)와 같은 동작이다.

- **GET만** 넘긴다. 그 밖의 메서드는 405 `METHOD_NOT_ALLOWED`.
- **DejaFix의 `/api/*` 조회만** 넘긴다. `/api/auth/*`(로그인 포함)와 그 밖의 경로는 404.
- 계정은 **viewer 권한**으로만 쓴다. 환경변수로만 받고 응답·로그에 싣지 않는다.

## 2. 환경변수 (`.env`, 커밋 금지)
| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `DEJAFIX_PROXY_TARGET` | `http://127.0.0.1:18000` | 서버에서 닿는 DejaFix 백엔드 주소 (고정 주소로 정해야 한다) |
| `DEJAFIX_USERNAME` | (없음) | DejaFix viewer 계정 |
| `DEJAFIX_PASSWORD` | (없음) | 그 비밀번호 |

## 3. 응답
DejaFix 응답을 상태 코드·본문 그대로 돌려준다. 이 서비스가 직접 내는 오류는 `{error: {code, message}}` 형식이다.

| 상태 | code | 경우 |
| --- | --- | --- |
| 405 | `METHOD_NOT_ALLOWED` | GET이 아님 |
| 404 | `NOT_FOUND` | `/api/*`가 아니거나 `/api/auth/*` |
| 503 | `DEJAFIX_NOT_CONFIGURED` | 계정 환경변수가 비어 있음 |
| 502 | `DEJAFIX_UNREACHABLE` | DejaFix 백엔드에 연결할 수 없음 |

`GET /healthz`는 DejaFix를 부르지 않고 `{"status": "ok"}`만 준다.

## 4. 연결
화면은 `/dejafix-api/*`를 부른다. 서버 gateway(nginx)가 `/dejafix-api/` 접두사를 떼고 `http://dejafix-proxy:8030/`으로 넘기면 된다 (예: `/dejafix-api/api/incidents?status=problem` → `/api/incidents?status=problem`). gateway 설정은 ax-frontend 레포의 `nginx/`에 있어서 이 레포에서는 바꾸지 않는다.

## 5. 실행·테스트
- 서버: `docker compose -f infra/compose.yml up dejafix-proxy`, 또는 `uv run uvicorn ax_dejafix_proxy.main:app --port 8030`
- 테스트: `uv run pytest shared/dejafix-proxy` (DejaFix는 가짜 응답으로 대체, 실제 DejaFix 불필요)
