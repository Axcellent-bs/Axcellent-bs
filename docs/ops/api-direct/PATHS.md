# API 직접 호스트(api-app) 허용 경로 초안

목표: 화면(axcellent.cloud, Cloudflare)은 그대로 두고, 읽기 API 만 별도 호스트(`https://api-app.axcellent.cloud`, 별도 nginx)로 직접 보내 응답 지연(경로)을 줄인다.
이 문서는 초안이다. 서버 작업(nginx·DNS·인증서)은 이 PR 범위 밖이고, 구체 값(내부 주소·포트·토큰)은 이 문서에 적지 않는다. 서버 작업 요청은 `docs/nginx-path-convention.md`의 절차(비공개 채널)를 따른다.

## 1. 한눈에

| 항목 | 내용 |
| --- | --- |
| 화면 쪽 스위치 | `VITE_API_DIRECT_BASE`(출처만) + `VITE_API_DIRECT_SERVICES`(`dashboard,orch,safety,planning,dev` 중 고른 것). 둘 중 하나라도 비면 꺼짐 = 지금과 같음 |
| 화면이 보내는 것 | GET 만, 아래 1차 표의 경로만. 쓰기·관리자 경로는 같은 출처 nginx 그대로 |
| 직접 호스트 주소 모양 | 같은 접두를 유지한다: `https://api-app.axcellent.cloud/dashboard-api/api/dashboard/...` (nginx 안에서 접두를 떼고 업스트림에 넘긴다. 접두를 떼고 싶으면 `apiDirect.ts`의 `DIRECT_PREFIX`만 바꾼다) |
| 인증 | 쿠키 없음. `Authorization: Bearer <로그인 세션 토큰 axs1.…>` 헤더. 직접 호스트는 토큰을 주입하지 않는다 |
| CORS | 허용 출처 `https://axcellent.cloud` 하나, 메서드 GET·HEAD·OPTIONS, credentials 불필요 |
| 실패 시 | 직접 호스트에 닿지 못하면(네트워크·CORS 실패) 화면이 같은 출처로 다시 보내고 60초 쉰다. 서버가 늦게 준비돼도 화면은 안 깨진다 |

## 2. 인증 방식 조사 결과

- 화면은 쿠키를 쓰지 않는다. `fetch`는 기본 `credentials: same-origin`이라 교차 출처로는 쿠키가 가지 않는다.
- 사람 토큰 출처: 로그인(`POST /api/dashboard/auth/login`) 응답의 세션 토큰(`axs1.…`)을 `sessionStorage`(탭 단위, `frontend/src/lib/humanSession.ts`)에 둔다. 세션이 있으면 `SAFETY_HUMAN_TOKEN`이 그 값이고, 없으면 번들의 자리표시자(공유 서버에서 nginx 가 진짜 공용 토큰으로 바꿔 붙인다).
- nginx 주입(`docs/superpowers/specs/2026-11-19-human-session-tokens-design.md`): 쓰기 경로는 화면이 보낸 `Authorization`을 그대로 넘기고(4단계), 읽기와 A2A 중계 경로는 공용 토큰 주입을 둔다. 직접 호스트는 주입이 없으므로 **읽기에도 화면이 세션 토큰을 실어야 한다**.
- 그래서 화면은 세션 토큰이 없으면 직접 호스트로 보내지 않는다(자리표시자는 직접 호스트에서 401). 로그인 전 공개 두 경로(`auth/status`, `auth/company`)만 토큰 없이 직접 간다.
- 백엔드 확인: dashboard-api 는 `PUBLIC_ROUTES`(health, auth/status, auth/login, auth/company) 밖의 GET 에 모두 사람 토큰을 요구한다(`auth.py require_human`). 총괄 `GET /api/incidents`·`/api/departments`, safety `GET /api/safety/executions`, 기획·개발 `GET /api/planning/state`는 서버가 토큰을 보지 않는다(코드 확인). 이 읽기들은 화면도 `Authorization`을 보내지 않으므로 단순 요청이 되어 OPTIONS 사전 요청이 없다.
- 주의: 토큰을 안 보는 읽기를 직접 호스트로 열면, 같은 출처로 이미 열려 있는 것과 노출 수준은 같지만 CORS 때문에 다른 사이트 스크립트가 읽을 수는 없게 된다(Origin 고정). 서버가 막는 것은 아니므로(CORS 는 브라우저만 지킨다) 민감도는 별도로 판단한다(위험 4절).

## 3. 1차: 로그인 직후 첫 화면 읽기 (우선순위순)

첫 화면은 관리자는 `/agents/chat`(AI 대화방), 회사 연결 전이면 `/system/connect`(`demo/session.tsx homeOf`). 일반회원은 배정된 첫 서비스. 사이드바(`AdminShell`)는 모든 화면에 있다. 호출 횟수는 코드 기준 추정이다(실측은 서버 로그·브라우저 네트워크 탭으로 확인할 것).

| 순서 | 경로(접두 뒤 포함) | 서비스·포트 성격 | 인증 | 읽기/쓰기 | 호출 패턴(추정) |
| --- | --- | --- | --- | --- | --- |
| 1 | `GET /dashboard-api/api/dashboard/auth/status` | dashboard-api(8020) | 없음(공개) | 읽기 | 로그인 전 1회 |
| 1 | `GET /dashboard-api/api/dashboard/auth/company` | dashboard-api | 없음(공개) | 읽기 | 로그인 전·후 각 1회. 응답 작음(이름·주소) |
| 2 | `GET /dashboard-api/api/dashboard/auth/me` | dashboard-api | 세션 | 읽기 | 세션 확인 1회(+주기) |
| 3 | `GET /dashboard-api/api/dashboard/connection-keys` | dashboard-api | 세션 | 읽기 | 로그인 직후 1회. Guarded 가 이 응답을 기다려야 첫 화면이 열린다(`App.tsx`) |
| 3 | `GET /dashboard-api/api/dashboard/company-connection` | dashboard-api | 세션 | 읽기 | 위와 같은 순간 1회. 응답 작음 |
| 4 | `GET /orch/api/incidents` | 총괄(8000), `/orch` | 없음 | 읽기 | AI 대화방 3초 폴링. 이슈마다 steps·events 포함이라 응답이 크다(가장 무거움) |
| 4 | `GET /safety-api/api/safety/executions` | safety(8001) | 없음 | 읽기 | 위와 같이 3초 폴링. 기록이 쌓일수록 커진다 |
| 4 | `GET /dashboard-api/api/dashboard/communications` | dashboard-api | 세션 | 읽기 | 위와 같이 3초 폴링 |
| 5 | `GET /dashboard-api/api/dashboard/office-room` | dashboard-api | 세션 | 읽기 | 4초 폴링(단톡방) |
| 6 | `GET /dashboard-api/api/dashboard/handoffs?status=open` | dashboard-api | 세션 | 읽기 | 사이드바 숫자, 30초 |
| 6 | `GET /planning-api/api/planning/state`, `GET /dev-api/api/planning/state` | 기획(8101)·개발(8111) | 없음 | 읽기 | 사이드바 숫자, 30초마다 각 1회. state 전체라 응답이 크다 |
| 6 | `GET /orch/api/incidents` | 총괄 | 없음 | 읽기 | 사이드바 총괄 승인 대기 숫자, 30초 (4번과 같은 경로. 11/23부터 캣북스 경유 중계를 쓰지 않는다) |
| 7 | `GET /dashboard-api/api/dashboard/shop/banners`, `.../shop/faults` | dashboard-api 중계 | 세션 | 읽기 | 대화방·마케팅 화면에서 필요할 때 |

폴링이 3~4초 간격이라 요청 수의 대부분은 4번 묶음이 만든다. 직접 호스트의 이득도 여기서 가장 크다(Cloudflare 경유 왕복을 건너뜀).

## 4. 2차: 나머지 읽기 (이 PR 은 화면 코드에 넣지 않았다)

서비스별 화면이 열릴 때 부른다. 필요해지면 `DIRECT_ROUTES`와 서버 허용 목록에 같이 추가한다. 후보: `GET /dashboard-api/api/dashboard/{claims,claims/stats,conversations,queue-entries,kpis,feedback,orders,settlements,executions,incidents,...}`(고객지원·재무), `GET /finance-api/...`(재무 AI), `GET /data-a2a/health`, `GET /orch/api/{finance-opinions,agent-diagnoses,incidents/<id>}`, `GET /planning-api/api/planning/<목록류>`, `GET /dev-api/api/planning/<목록류>`.
조건: 응답에 고객 원문·개인정보가 들어가는 경로(`conversations`, `claims`, `privacy/purge-preview` 등)는 세션 토큰이 반드시 필요한 경로만 2차에 넣는다(총괄·safety 처럼 토큰을 안 보는 경로에 고객 글이 있는지 먼저 확인).

## 5. 쓰기 경로는 1차에서 제외

- 제외 이유: (1) 쓰기는 세션 토큰이 서버 검증의 핵심이고(공용 토큰 주입을 이미 뺐다) 직접 호스트로 옮겨도 지연 이득이 작다(사용자 한 번 클릭). (2) POST·PUT·DELETE 는 CORS 사전 요청이 매번 필요하다. (3) 승인·정산·환불·연결 키처럼 사고 비용이 큰 경로를 새 호스트에 넓히지 않는다. (4) 서버 쪽 쓰기 허용은 `ax-gate` 정책(제안·승인 2인)으로 관리되고 있어 별도 호스트에서 정책을 두 벌로 나누지 않는다.
- 로그인 `POST /auth/login`도 1차에서 제외(같은 출처 유지, 사전 요청 회피).
- 화면 코드는 GET 이 아니면 어떤 경우에도 직접 호스트로 보내지 않는다(테스트로 고정).

## 6. 외부용 nginx location 허용 목록 (초안)

원칙: 기본 거부(`location / { return 404; }`), 읽기만(`limit_except GET HEAD OPTIONS`), 정규식은 `^…$`로 고정하고 가변 조각은 쓰지 않는다(1차에는 id 경로가 없다). 업스트림 주소·포트는 비공개 정책 파일 값을 쓴다(아래는 자리표시). 접두를 떼고(`rewrite`) 업스트림에 넘긴다.

```nginx
# 응답에 업스트림의 CORS 헤더가 섞이면 중복되므로 숨기고 nginx 가 한 번만 단다
map $http_origin $api_direct_origin {
    default                  "";
    "https://axcellent.cloud" "https://axcellent.cloud";
}

# 허용 외 전부 거부
location / { return 404; }

# 1) dashboard-api 공개 두 개 (토큰 없이)
location ~ ^/dashboard-api/api/dashboard/auth/(status|company)$ {
    limit_except GET HEAD OPTIONS { deny all; }
    include snippets/api-direct-cors.conf;      # 아래 7절
    rewrite ^/dashboard-api/(.*)$ /$1 break;
    proxy_pass http://<dashboard-api 업스트림>;
    include snippets/api-direct-proxy.conf;     # proxy_hide_header Access-Control-*, Host, 타임아웃
}

# 2) dashboard-api 세션 필요 읽기. Authorization 을 지우지 않고 그대로 넘긴다(공용 토큰 주입 금지)
location ~ ^/dashboard-api/api/dashboard/(auth/me|communications|office-room|handoffs|connection-keys|company-connection)$ {
    limit_except GET HEAD OPTIONS { deny all; }
    include snippets/api-direct-cors.conf;
    rewrite ^/dashboard-api/(.*)$ /$1 break;
    proxy_pass http://<dashboard-api 업스트림>;
    include snippets/api-direct-proxy.conf;
}
location ~ ^/dashboard-api/api/dashboard/shop/(banners|faults)$ {
    limit_except GET HEAD OPTIONS { deny all; }
    include snippets/api-direct-cors.conf;
    rewrite ^/dashboard-api/(.*)$ /$1 break;
    proxy_pass http://<dashboard-api 업스트림>;
    include snippets/api-direct-proxy.conf;
}

# 3) 총괄·safety·기획·개발: 토큰을 안 보는 읽기
location ~ ^/orch/api/incidents$ {
    limit_except GET HEAD OPTIONS { deny all; }
    include snippets/api-direct-cors.conf;
    rewrite ^/orch/(.*)$ /$1 break;
    proxy_pass http://<총괄 업스트림>;
    include snippets/api-direct-proxy.conf;
}
location ~ ^/safety-api/api/safety/executions$ {
    limit_except GET HEAD OPTIONS { deny all; }
    include snippets/api-direct-cors.conf;
    rewrite ^/safety-api/(.*)$ /$1 break;
    proxy_pass http://<safety 업스트림>;
    include snippets/api-direct-proxy.conf;
}
location ~ ^/planning-api/api/planning/state$ {
    limit_except GET HEAD OPTIONS { deny all; }
    include snippets/api-direct-cors.conf;
    rewrite ^/planning-api/(.*)$ /$1 break;
    proxy_pass http://<기획 업스트림>;
    include snippets/api-direct-proxy.conf;
}
location ~ ^/dev-api/api/planning/state$ {
    limit_except GET HEAD OPTIONS { deny all; }
    include snippets/api-direct-cors.conf;
    rewrite ^/dev-api/(.*)$ /$1 break;
    proxy_pass http://<개발 업스트림>;
    include snippets/api-direct-proxy.conf;
}
```

참고: nginx 의 `limit_except` 안 `deny all`은 OPTIONS 를 허용 목록에 넣어도 CORS 스니펫이 `OPTIONS`에 직접 답해야(204) 하므로 스니펫에서 처리한다. 쿼리 문자열은 location 정규식에 안 들어가므로(경로만 비교) `?dept=…` 같은 필터는 그대로 통과한다. `/health`는 모니터링용으로 따로 여는 것은 서버 책임자가 정한다.
정규식 검증은 `docs/ops/ssh-gate/nginx_location_check.py` 모양(`^…$` 고정, 안전 문자 묶음)과 같은 규칙을 따랐다. 단 게이트는 쓰기(POST…)만 연다고 되어 있어 이 읽기용 호스트는 게이트로 못 열고 서버 책임자 요청 경로를 쓴다(`docs/nginx-path-convention.md` 3번).

## 7. CORS 초안

```nginx
# snippets/api-direct-cors.conf (location 안에서 include)
if ($api_direct_origin = "") { return 403; }          # axcellent.cloud 가 아닌 Origin 이 붙은 브라우저 요청은 거부 (Origin 없는 서버 간 호출은 위 map 이 아닌 별도 판단)
add_header Access-Control-Allow-Origin  $api_direct_origin always;
add_header Vary Origin always;
add_header Access-Control-Allow-Methods "GET, HEAD, OPTIONS" always;
add_header Access-Control-Allow-Headers "Authorization, Content-Type" always;
add_header Access-Control-Max-Age 86400 always;
if ($request_method = OPTIONS) { return 204; }
```

- 허용 출처: `https://axcellent.cloud` 하나(와일드카드 금지). 개발 중 확인은 `http://localhost:3090`을 임시로 map 에 더하되 운영에는 두지 않는다. Origin 헤더가 없는 호출(curl·서버 간)은 `$http_origin`이 비어 위 `if`에 걸려 403 이 되므로, 서버 간 호출·헬스 체크를 허용해야 하면 `if` 대신 map 결과가 빈 값일 때 CORS 헤더만 안 다는 방식으로 바꿀지 서버 책임자가 정한다(질문 1).
- 허용 메서드: GET, HEAD, OPTIONS 뿐.
- 허용 헤더: `Authorization`(세션 토큰), `Content-Type`(화면 일부 읽기가 `application/json`을 단다). 둘 다 안전 목록 밖이라 사전 요청이 생긴다.
- `Access-Control-Max-Age`: 86400 을 적어도 Chrome 은 7200초(2시간), Firefox 는 86400초로 자른다. 사전 요청 캐시는 **URL 별**이라 같은 경로(쿼리 포함)를 폴링하면 이득이 크지만 처음 부르는 주소마다 사전 요청 1회가 든다.
- credentials: **불필요**(`Access-Control-Allow-Credentials`를 달지 않는다). 인증은 헤더 토큰이고 쿠키를 안 쓴다. 달면 와일드카드 금지 등 제약만 늘고, 쿠키가 교차 출처로 가는 길을 열 뿐이다. 화면 `fetch`도 `credentials` 기본값(same-origin)이다.
- OPTIONS 사전 요청이 생기는 조건: 교차 출처 + (`Authorization` 헤더 또는 `Content-Type: application/json` 등 비단순 헤더, 또는 GET·HEAD·POST 이외 메서드). 이 화면에서는 세션 토큰을 싣는 dashboard-api 읽기는 항상 생기고, 헤더를 안 다는 총괄·safety·기획·개발 읽기(`fetch(url,{cache:'no-store'})`)는 단순 요청이라 안 생긴다. 총괄·safety 를 먼저 직접으로 돌리면 사전 요청 없이 이득만 얻는다.
- 업스트림(FastAPI 서비스)도 자기 CORS 미들웨어를 갖고 있다(dashboard-api·safety·planning). nginx 가 `proxy_hide_header Access-Control-Allow-Origin` 등으로 가리지 않으면 헤더가 중복돼 브라우저가 거부한다.

## 8. 위험과 주의

1. 쓰기·관리자 경로 노출 금지: location 은 정규식 전체 고정(`^…$`)만 쓰고 `location /dashboard-api/ { }` 같은 접두 통과를 만들지 않는다. 관리자·운영 경로(`auth/accounts`, `secret*`, `cost-limits`, `cost-policy`, `connection-keys/issue|revoke`, `company-connection/save|clear`, `demo/*`)는 읽기도 허용 목록에 없다. `connection-keys` GET 은 가린 키만 오지만 회사 연결 정보라 세션 토큰이 있어야 한다.
2. 공용 사람 토큰 주입 금지: 직접 호스트의 nginx 에 주입 설정을 복사해 오면 안 된다. 주입하면 인증 없는 누구나 `/api/dashboard/*`를 사람으로 읽는다. 이 호스트의 의미가 "토큰 없으면 거절"이다.
3. 세션 토큰 노출면: 토큰이 다른 출처(api-app)로 간다. Origin 고정, HTTPS 전용, 로그에 `Authorization` 값 안 남김(`log_format`에서 제외), 쿼리에 토큰을 넣지 않는다(화면도 안 넣는다).
4. 토큰을 안 보는 읽기(총괄 이슈·safety 실행·기획/개발 state)는 지금도 같은 출처 nginx 뒤에서 열려 있다. 새 호스트를 인터넷에 노출하면 CORS 와 무관하게 서버 간 호출로는 읽힌다. 이 응답에 고객 글·개인정보가 있는지 확인하거나, 직접 호스트에서 이 경로를 뺄지(화면은 서비스 목록에서 `orch`,`safety`,`planning`,`dev`를 빼면 된다) 정해야 한다(질문 2).
5. Cloudflare: api-app 이 프록시(주황 구름)면 Cloudflare 경유라 지연 이득이 줄어든다. 직접(DNS only)이면 원본 IP 가 드러난다. 어느 쪽인지는 서버 책임자 결정(질문 3).
6. 레이트·크기: 3초 폴링이 몰리는 경로가 있으므로 `limit_req`(IP·토큰 기준 완만하게)와 gzip 을 건다. 총괄 이슈·기획 state 는 응답이 커서 gzip 이득이 크다.
7. 롤백: 화면은 `VITE_API_DIRECT_SERVICES`를 비워 재배포하면 즉시 같은 출처로 돌아간다. 또한 직접 호스트 장애 시 자동으로 같은 출처 재시도가 들어 있다(실패 한 번 + 60초 휴식).

## 9. 화면에서 묶거나 병렬화하면 왕복이 줄어드는 곳 (후보, 코드 미변경)

- 로그인 직후: `connection-keys`(`refreshCompany`)와 `company-connection`, `auth/company`는 `Promise.all`로 이미 병렬이다. 하지만 `auth/me`, `auth/status`는 `SessionProvider`에서 별도 순서로 불려 첫 화면이 열리는 시간이 이들의 합이 될 수 있다(실측 필요).
- AI 대화방 3초 폴링 `refresh`: `orchestrator.incidents`·`finance.executions`·`api.communications`는 `Promise.allSettled`로 병렬이다(좋다). 다만 대화방(3초)·단톡방(`office-room`, 4초)·사이드바(30초 ×4)가 서로 다른 타이머라 요청이 흩어진다. 한 폴링 주기에 묶는 서버 합성 읽기(`GET /api/dashboard/room-snapshot` 같은 새 경로)는 백엔드 변경이라 이 PR 범위 밖.
- 사이드바 `useNavCounts`: `planning`·`dev` state 를 순서대로 `await`한다(`navCounts.ts refresh`는 `await count(planning)` 다음 `await count(dev)` 다음 `await heldCount()`). `Promise.all`로 바꾸면 30초마다 3번의 직렬 왕복이 1번 몫으로 줄어든다. 전체 state 대신 개수만 주는 읽기를 서버에 두면 응답 크기도 준다.
- `refreshOpenCount`(할 일 개수)는 목록 전체를 받아 `.length`만 쓴다. 개수 전용 읽기가 있으면 응답이 작아진다.
- 같은 URL 동시 요청은 `api.ts`의 `shareInflight`가 이미 합친다. `orchestrator.ts`·`finance.ts`·`officeRoom.ts`·`handoffs.ts`는 이 합치기를 안 쓴다(AI 대화방과 AI 오피스가 동시에 열리면 같은 읽기가 중복될 수 있다).

## 10. 열린 질문 (재헌 님이 정할 것)

1. Origin 없는 서버 간 호출·헬스 체크를 직접 호스트에서 허용할지.
2. 토큰을 안 보는 읽기(`orch`, `safety`, `planning`, `dev`)를 직접 호스트에 열지, dashboard 만 열지. 지연 이득은 `orch`·`safety`가 가장 크다.
3. api-app 을 Cloudflare 프록시 뒤에 둘지(지연 이득과 보호의 맞교환).
4. 직접 호스트 경로의 접두(`/dashboard-api/…` 유지 vs 제거). 지금 코드는 유지 전제.
5. 2차 경로를 어디까지 열지(고객 글이 든 읽기 포함 여부).
