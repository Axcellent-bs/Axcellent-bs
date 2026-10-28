# AXcellent 데모

구조 회의용 프로토타입이에요. 백엔드 없이 프론트만으로 돌아가요.

- 이 폴더(`frontend/`)에서 `npm ci` → `npm run dev`로 띄워요 (http://localhost:3090). 처음 만든 곳은 [NYejin/axcellent-demo](https://github.com/NYejin/axcellent-demo)예요.
- GitHub Pages로 올릴 때는 `npm run build:pages`가 `/Axcellent-bs/` 경로로 빌드해요. Pages 배포 워크플로는 아직 넣지 않았어요.
- AI 화면은 [ax-frontend](https://github.com/ax-company-hq/ax-frontend) `dashboard-web`(캣북스 대시보드, dev `c1eb44a`)에서 옮겨 왔어요. 캣북스 대시보드는 없앨 예정이고, AI 7개는 여기서 관리해요 (아래 "AI 7개").

## 보여 주는 구조

부서는 코드에 두지 않아요. 부서 안에 있던 기능을 **서비스** 하나하나로 꺼냈고, 회사는 Adobe Creative Cloud처럼 필요한 서비스만 골라 써요. 고객사 계정은 **시스템 관리자 하나**이고, 회사가 켠 서비스가 모두 메뉴에 보여요.

```
┌──────────── 고객사 사내망 (폐쇄) ─────────────────────────┐
│  [서비스 고르기] 회사가 쓸 서비스를 켜고 끔                  │
│  [로그인] → 회사가 켠 서비스가 모두 메뉴에 보임              │
│  [DejaFix] 현재 장애(데이터만) · PC·서버 연결 (IP로 등록)   │
│  [회사 서비스 연결] ── URL·API·키 ──→ 캣북스 온라인 서점     │
└───────────────────────────────────────────────────────────┘
  우리는 서버 없이 프로그램만 제공해요. 데이터는 회사 안에만 있어요.
```

| 서비스 | 만든 쪽 | 주소 |
| --- | --- | --- |
| 장애 대응 (현재 장애만, DejaFix 데이터) | DejaFix | `/infra` |
| PC·서버 연결 | DejaFix | `/hosts` |
| 고객 문의 | AXcellent | `/inquiry`, `/console`, `/support` |
| 클레임·보상 | AXcellent | `/claims`, `/queue` |
| 리뷰·지식 | AXcellent | `/reviews`, `/kb` |
| 공지·쿠폰 | AXcellent | `/marketing` |
| 재무 관리 | AXcellent | `/finance` |
| 데이터 분석 | AXcellent | `/data` |
| 인수인계 | AXcellent | `/hr` |
| 결정 기록 | AXcellent | `/ops` |
| 요청·기획서 | AXcellent | `/planning` |
| 개발·배포 | AXcellent | `/dev` |
| 총괄·사건 | AXcellent | `/orchestrator` |
| 기본 포함: AI 대화방·AI 오피스 | AXcellent | `/agents/chat`, `/agents/office` |
| 시스템 관리: 서비스 고르기·회사 서비스 연결 | AXcellent | `/system/services`, `/system/integrations` |
| 메인(로그인 전 서비스 카탈로그) | — | `/` → 로그인 버튼 → `/login` |
| 관제 센터(AXcellent 운영팀 전용): 전체 현황·고객사·**서비스 관리**(버전·배포·카탈로그 공개)·장애·사건·AI 품질·비용·사용량·**연결 키 발급** | AXcellent | `/control/login` → `/control/…` (계정 `?as=axc`) |
| 처음 설정(설치 직후 한 번): 연결 키 → 시스템 관리자 계정 → 회사 정보 → 연결 | AXcellent | `/setup` (`/login?company=none`에서 시작) |
| AXcellent 연결(고객사 시스템 관리자): 연결된 회사 정보 보기·고치기·끊기, 다른 키로 다시 연결 | AXcellent | `/system/connect` |
| 사내 PC 연결(고객사 시스템 관리자): 에이전트 설치 명령 → Zabbix 등록 → DejaFix 인식 | DejaFix | `/system/hosts` |

- 서비스 목록과 서비스마다 맡은 AI는 `src/demo/services.ts`에 있어요.
- 연결 흐름: AXcellent 운영팀이 관제 센터에서 연결 키를 발급(키에는 회사 정보 없음) → 고객사가 처음 설정에서 키 확인 → 시스템 관리자 계정 만들기 → 회사 정보 입력 → 연결. 회사 연결 전에는 시스템 관리자만 AXcellent 연결 화면을 열 수 있어요. 상단 "AXcellent with ○○"가 연결된 회사를 따라가고, 끊으면 회사 이름이 빠져요.
- 관제 센터는 우리 팀원만 쓰는 화면이라 고객사 로그인 화면에는 운영팀 계정이 나오지 않아요.
- 고객사 계정은 **시스템 관리자**(`admin`) 하나예요. 직무별 예시 계정(인프라·CS·재무 등)은 없앴고, 예전 `?as=infra` 같은 링크는 시스템 관리자로 들어가요. 오른쪽 위 계정 메뉴에는 로그아웃만 있어요.
- 화면 모양은 DejaFix common-frontend(보라 사이드바, 인디고, 각진 모서리) 하나만 써요.

## AI 7개

예전 캣북스 대시보드에 있던 AI를 전부 AXcellent로 옮겼어요. 캣북스에는 쇼핑몰(shop-web)만 남아요. 안전 관문·회사 기억은 화면 없이 뒤에서 도는 서버라 넣지 않았고, 인프라는 DejaFix가 맡아요.

| AI | AXcellent 서비스 | 부르는 서버 (ax-backend) |
| --- | --- | --- |
| 총괄 | 총괄·사건 | orchestrator 8000, shop-api 관리 중계 8010 |
| 고객지원 AI | 고객 문의 / 클레임·보상 / 리뷰·지식 | dashboard-api 8020 |
| 재무 AI | 재무 관리 | finance 8105, safety 8001 |
| 데이터분석 AI | 데이터 분석 | data 8106 |
| 인사 AI | 인수인계 / 결정 기록 | dashboard-api 8020 |
| 마케팅 AI | 공지·쿠폰 | 아직 없음 (화면 안 시연 데이터) |
| 기획개발 AI | 요청·기획서 / 개발·배포 | planning 8101, dashboard-api 8020 |

왼쪽 메뉴는 **AI가 상위 탭**이고, 그 AI가 맡은 서비스가 하위 탭이에요 (예: 고객지원 AI → 고객 문의 → 상담 콘솔). 회사가 켠 서비스는 모두 보여요(서비스 고르기에서 끈 서비스만 빠져요). 맨 위는 시스템 관리 메뉴이고, AI가 맡지 않은 장애 대응·PC·서버 연결과 AI 대화방·AI 오피스는 구분선 아래에 있어요.

관리자 계정의 **서비스 고르기**(`/system/services?as=admin`) 아래 "관리하는 AI" 표에서도 볼 수 있어요.

## 데이터는 어디서 오나

- `src/mock/fixtures.json`: ax-backend 서비스들을 목 모드(`*_MOCK=1`)로 띄우고, 화면이 부르는 GET 응답을 녹화한 거예요(2026-10-27, 79개).
  - `GET dejafix /api/incidents` 1개는 DejaFix 레포(`DejaFix/DejaFix` dev `15d0477`) `frontend/src/mocks`가 `status=problem`에 돌려주는 응답 그대로예요.
  - 재무 AI 자체 점검 결과는 바꿔 뒀어요. 녹화할 때 서비스끼리 연결하지 않아서 연결 실패 5개가 나왔는데, 이 항목만 "연결됨"으로 고쳤어요.
- `src/mock/install.ts`: `fetch`와 `EventSource`를 가로채서 녹화본을 돌려줘요.
  - 쓰기 요청(POST)은 성공 응답만 돌려줘요. 그래서 버튼을 눌러도 화면 데이터는 바뀌지 않아요.
- 서비스 고르기, PC·서버 연결, 회사 서비스 연결 화면의 데이터는 브라우저(localStorage)에만 저장돼요.

## 실행

```bash
npm install
npm run dev          # http://localhost:3090
npm run build:pages  # GitHub Pages용 빌드 (dist/, 404.html 포함)
```

## 실제 AI 백엔드에 붙이기

기본은 녹화본으로 도는 데모예요. `.env.example`을 `.env`로 복사해서 `VITE_MOCK=off`로 바꾸면 녹화본을 끄고 ax-backend·DejaFix를 직접 불러요. 주소·토큰은 같은 파일에 있어요.

- 총괄·데이터 AI·DejaFix는 CORS가 없어서 개발 서버 프록시(`/orch`, `/data-a2a`, `/dejafix-api`)로 불러요 (`vite.config.ts`).
- 총괄과 DejaFix 백엔드는 둘 다 기본 포트가 8000이라, 같이 띄울 때는 한쪽 포트를 바꾸고 `ORCHESTRATOR_PROXY_TARGET`이나 `DEJAFIX_PROXY_TARGET`을 맞춰요.

## 데이터 분석 화면 확인 방법

데이터 분석 화면(`/data`)은 녹화본 없이 실제 모드 백엔드(`backend/`)의 값만 보여 줘요. 사건이 없으면 빈 상태 안내가 나오고, 예시 숫자는 만들지 않아요.

1. 백엔드를 실제 모드로 켜요 (Docker 필요. LLM 설정은 `backend/.env`나 레포 맨 위 `.env`. 없으면 LLM을 쓰는 skill이 규칙 기반 대체 답을 내요. 데이터 분석 숫자는 LLM 없이 계산돼요)
   ```bash
   cd backend
   uv sync --all-packages
   bash scripts/run_all.sh        # mock 인자 없이. 끄기: bash scripts/run_all.sh stop
   ```
   - 포트 8000~8122를 써요. 시작할 때 이 포트를 쥔 프로세스를 끄기 때문에, 8000에 DejaFix를 띄워 뒀다면 DejaFix를 다른 포트(예: 18000)로 옮겨 두세요
   - 쇼핑몰 고객 주문 트래픽(`scripts/shop_traffic.py`, 3초마다 1건)도 같이 켜져요
2. 화면을 켜요. `backend/.data/web.env`의 `VITE_MOCK=off`와 토큰을 읽어 개발 서버(3090)를 켜요. 3090에 다른 개발 서버가 떠 있으면 먼저 꺼요
   ```bash
   bash scripts/run_web.sh        # backend/ 에서
   ```
3. 장애 사건을 만들어요. 쇼핑몰에 결제 장애를 주입하면 주문 결제가 5xx로 실패하고, 쇼핑몰 감시가 20초 넘게 이어진 실패를 재서 `shop.degraded`를 내요. 총괄이 그걸 받아 사건을 열고 데이터 AI가 영향 분석·복구 확인·처리 시간 요약을 해요
   ```bash
   # backend/ 에서. 주입은 admin 토큰만 돼요 (web.env의 VITE_SHOP_ADMIN_TOKEN, 값은 출력하지 않음)
   export SHOP_FAULT_TOKEN="$(sed -n 's/^VITE_SHOP_ADMIN_TOKEN=//p' .data/web.env)"
   .venv/Scripts/python.exe infra/fault-injection/payment_fault.py inject   # macOS·Linux: .venv/bin/python
   # 30초쯤 기다린 뒤 해제하면 shop.recovered로 복구 확인이 이어져요
   .venv/Scripts/python.exe infra/fault-injection/payment_fault.py clear
   ```
   - 감지 시간은 `SHOP_DEGRADED_SUSTAIN_S`(run_all.sh 기본 20초)와 `SHOP_MONITOR_INTERVAL_S`(5초)로 정해져요
4. http://localhost:3090/data 를 열어요. "장애 사건 데이터"에 사건이, "평상시 지표"(`/data#daily`)에 복구 시간·자동 처리율이 나와요. 사건을 하나 더 만들면 1차·2차 복구 시간이 비교돼요

화면이 부르는 주소:

| 화면 부분 | 화면이 부르는 주소 | 실제 서버 |
| --- | --- | --- |
| 총괄 켜짐/꺼짐 칩 | `GET /orch/health` | 총괄 orchestrator 8000 `/health` |
| 장애 사건 목록 | `GET /orch/api/incidents` | 총괄 8000 `/api/incidents` |
| 고른 사건 상세 | `GET /orch/api/incidents/{context_id}` | 총괄 8000 `/api/incidents/{context_id}` |
| 데이터 AI 켜짐/꺼짐 칩 | `GET /data-a2a/health` | 데이터 AI data 8106 `/health` |
| 평상시 지표 | `POST /data-a2a/a2a` (skill `kpi_summary`, 토큰 `VITE_DATA_A2A_TOKEN`) | 데이터 AI 8106 `/a2a` |

`/orch`, `/data-a2a`는 개발 서버 프록시예요 (`vite.config.ts`, `ORCHESTRATOR_PROXY_TARGET`·`DATA_PROXY_TARGET`).

## 서비스 실서버 연결 체크리스트

녹화본으로 만든 서비스 화면을 실제 백엔드에 붙일 때 이 순서로 확인해요. 데이터 분석(`/data`)을 예로 들어요.

1. **화면이 부르는 주소 표**: 화면 코드(`src/pages/<화면>.tsx`, `src/lib/<서비스>.ts`, `src/components/<서비스>/`)에서 `fetch`하는 주소를 전부 찾아 표로 적고, 실제 모드에서 각 주소가 응답하는지 확인해요
   - 예: 위 "화면이 부르는 주소" 표 (총괄 3개, 데이터 AI 2개)
2. **진짜 모드에서 데이터가 생기는 방법**: 화면에 값이 채워지려면 백엔드에서 무슨 일이 있어야 하는지 찾아 적어요. 시드나 녹화본이 아니라 실제 흐름으로 만들어요
   - 예: 결제 장애 주입(`backend/infra/fault-injection/payment_fault.py`) → 쇼핑몰 감시 `shop.degraded` → 총괄 사건 → 데이터 AI 단계
3. **회사 이름·고유 용어 제거**: 특정 회사 이름(예: 캣북스)을 화면에 박지 않아요. 연결된 회사 이름(`src/demo/company.ts`의 `useCompany()`)을 쓰고, 연결 전이면 이름 없이 써요. 문서 번호(SCN-L, 시나리오 A 등)도 화면 문구에서 빼요
   - 예: "데이터분석팀입니다" 앞에 연결된 회사 이름을 붙이고, 없으면 이름 없이
4. **빈 상태·서버 꺼짐 안내**: 값이 없을 때 예시 숫자를 만들지 않고 무엇을 하면 채워지는지 안내해요. "예시 데이터"처럼 녹화본을 전제한 표시는 빼요. 서버가 꺼지면 어느 서버가 꺼졌는지 이름과 포트로 보여 줘요
   - 예: "아직 장애 사건이 없어요…", "데이터 AI 서버(8106)에 연결할 수 없어요", "총괄 서버(8000)에 연결할 수 없어요"
5. **화면 확인 경로**: 실서버로 직접 열어 본 주소를 PR 본문에 적어요
   - 예: http://localhost:3090/data, http://localhost:3090/data#daily, 사건 지정은 `/data?incident=<context_id>`

## 할 일함 확인 방법

할 일함(`/handoffs`)은 회사가 켜지 않은 서비스로 가던 일을 AI가 맡은 부서(사람)에게 넘긴 목록이에요. 어느 서비스를 샀든 보이는 기본 메뉴이고, 열린 일이 있으면 메뉴에 개수가 붙어요 (30초마다 다시 셈). 백엔드 흐름은 `backend/README.md` "업무 요청 라우터"에 있어요. 위 체크리스트 순서로 적었어요.

1. **화면이 부르는 주소 표**: 녹화본이 없어요. 데모 모드(`VITE_MOCK` 기본값)에서도 이 주소는 가로채지 않고 실제 dashboard-api를 불러요 (`src/mock/install.ts`)

   | 화면 부분 | 화면이 부르는 주소 | 실제 서버 |
   | --- | --- | --- |
   | 목록·메뉴 개수 | `GET /api/dashboard/handoffs?status=<open\|done\|rejected>&unit=<부서>` | dashboard-api 8020 |
   | 끝내기·반려 | `POST /api/dashboard/handoffs/{task_id}/complete` `{"result", "status": "done"\|"rejected"}` (사람 토큰 `VITE_SAFETY_HUMAN_TOKEN`) | dashboard-api 8020 |

   코드: `src/pages/Handoffs.tsx`, `src/lib/handoffs.ts`, 업무 이름(skill)을 읽는 말로 바꾸는 표는 `src/lib/labels.ts`의 `SKILL_KO`
2. **진짜 모드에서 데이터가 생기는 방법**: 서비스 일부만 켜고, 꺼진 서비스로 가는 요청을 만들어요
   - `backend/config/org.example.yaml`을 `config/org.yaml`로 복사해 부서 이름을 고쳐요 (조직도에 없는 업무는 할 일함에 안 들어가고 `no_assignee`로 실패해요)
   - `backend/.env`에 `AX_ENABLED_SERVICES=<켤 서비스 id, 쉼표>`를 적고 `bash scripts/run_all.sh`로 켜요. 예: 재무(`finance`)를 빼고 켜면 다른 AI가 재무에 맡기는 일(정산 보류 등)이 재무 담당 부서 할 일함에 쌓여요
   - 그다음 "데이터 분석 화면 확인 방법" 3번처럼 결제 장애를 주입하면, 사건 처리 중 꺼 둔 서비스로 가는 일이 할 일함에 들어와요
3. **회사 이름·고유 용어 제거**: 부서·담당자 이름은 조직도(`org.yaml`)에서 온 API 값을 그대로 보여 줘요. 화면 코드에 회사·부서 이름이 없어요. 서비스 이름은 `src/demo/services.ts`의 이름을 써요
4. **빈 상태·서버 꺼짐 안내**
   - 열린 일이 없으면: "넘겨받은 일이 없어요. 회사가 켜지 않은 서비스로 가는 일이 여기 쌓여요."
   - 서버가 꺼지면: "dashboard-api(8020)에 연결할 수 없어요" (메뉴 개수는 숨겨요)
   - 사람 토큰이 비어 있으면 끝내기·반려 버튼을 끄고 이유를 보여 줘요. 외부에서 들어온 내용(`untrusted`)은 글자 그대로만 보여 주고 HTML로 해석하지 않아요
   - 이벤트 버스가 없어 `handoff.completed`를 못 보내면 서버가 503으로 일을 열어 둬요. 화면은 서버 메시지를 그대로 보여 줘요
5. **화면 확인 경로**: http://localhost:3090/handoffs (열린 일), `/handoffs?status=done`, `/handoffs?status=`(전체), `/handoffs?unit=<부서>`, 한 건 지정은 `/handoffs?id=<task_id>`

## 장애 대응 (DejaFix)

DejaFix 화면은 띄우지 않아요. DejaFix API 계약(`DejaFix/DejaFix` `contracts/api.md` #10)의 `GET /api/incidents?status=problem`으로 **지금 진행 중인 장애만** 받아서 AXcellent가 그려요 (`src/pages/Incidents.tsx`, `src/lib/dejafix.ts`).

- 기본은 **데모 녹화본**(DejaFix 레포 목 데이터)이라 화면 제목 옆에 "데모 녹화본"이 붙어요. **실제 DejaFix 데이터**를 보려면 `frontend/.env`에 아래를 넣고 개발 서버를 다시 켜요. 그러면 "실시간"으로 바뀌어요.

  ```
  VITE_DEJAFIX_LIVE=on                          # 다른 AI는 녹화본 그대로, 장애 대응만 실제 DejaFix
  DEJAFIX_PROXY_TARGET=http://127.0.0.1:18000   # DejaFix 백엔드 주소
  DEJAFIX_USERNAME=admin                        # DejaFix 계정 (viewer 이상)
  DEJAFIX_PASSWORD=<DejaFix .env 의 DEMO_ADMIN_PASSWORD>
  ```
  DejaFix는 모든 API에 로그인 토큰을 요구해요. 개발 서버(`dejafix-proxy.ts`)가 이 계정으로 대신 로그인해서 부르기 때문에 토큰과 비밀번호는 화면 코드에 실리지 않아요. 읽기(GET)만 넘겨요.
- 30초마다 다시 받아요. 응답의 `meta.stale`이 true면 "수집 멈춤" 안내를 띄워요.
- 예전에 `public/dejafix/`에 넣어 두던 DejaFix 빌드본과 `scripts/build-dejafix.mjs`는 지웠어요.
