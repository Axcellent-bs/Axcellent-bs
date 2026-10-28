# AXcellent 데모

구조 회의용 프로토타입이에요. 백엔드 없이 프론트만으로 돌아가요.

- 이 폴더(`frontend/`)에서 `npm ci` → `npm run dev`로 띄워요 (http://localhost:3090). 처음 만든 곳은 [NYejin/axcellent-demo](https://github.com/NYejin/axcellent-demo)예요.
- GitHub Pages로 올릴 때는 `npm run build:pages`가 `/Axcellent-bs/` 경로로 빌드해요. Pages 배포 워크플로는 아직 넣지 않았어요.
- AI 화면은 [ax-frontend](https://github.com/ax-company-hq/ax-frontend) `dashboard-web`(캣북스 대시보드, dev `c1eb44a`)에서 옮겨 왔어요. 캣북스 대시보드는 없앨 예정이고, AI 7개는 여기서 관리해요 (아래 "AI 7개").

## 보여 주는 구조

부서는 코드에 두지 않아요. 부서 안에 있던 기능을 **서비스** 하나하나로 꺼냈고, 회사는 Adobe Creative Cloud처럼 필요한 서비스만 골라 써요. 직원 계정에는 쓸 서비스를 배정해요.

```
┌──────────── 고객사 사내망 (폐쇄) ─────────────────────────┐
│  [서비스 고르기] 회사가 쓸 서비스를 켜고 끔                  │
│  [로그인] → 내게 배정된 서비스만 메뉴에 보임                 │
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
- 연결 흐름: AXcellent 운영팀이 관제 센터에서 연결 키를 발급(키에는 회사 정보 없음) → 고객사가 처음 설정에서 키 확인 → 시스템 관리자 계정 만들기 → 회사 정보 입력 → 연결. 회사 연결 전에는 직원 계정이 로그인할 수 없어요. 상단 "AXcellent with ○○"가 연결된 회사를 따라가고, 끊으면 회사 이름이 빠져요.
- 관제 센터는 우리 팀원만 쓰는 화면이라 고객사 로그인·프로필 전환에는 운영팀 계정이 나오지 않아요.
- 오른쪽 위 **프로필 전환** 드롭다운으로 예시 직원 계정을 바꿔 가며 볼 수 있어요. 주소 뒤에 `?as=support`처럼 붙이면 그 계정으로 바로 들어가요. 계정 id는 `admin`, `infra`, `dev`, `planning`, `support`, `marketing`, `finance`, `hr`, `data`예요.
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

## 장애 대응 (DejaFix)

DejaFix 화면은 띄우지 않아요. DejaFix API 계약(`DejaFix/DejaFix` `contracts/api.md` #10)의 `GET /api/incidents?status=problem`으로 **지금 진행 중인 장애만** 받아서 AXcellent가 그려요 (`src/pages/Incidents.tsx`, `src/lib/dejafix.ts`).

- 30초마다 다시 받아요. 응답의 `meta.stale`이 true면 "수집 멈춤" 안내를 띄워요.
- 원인 분석·리포트·변경 계획은 DejaFix 화면에서 봐요. 장애마다 "DejaFix에서 보기 ↗"가 `VITE_DEJAFIX_WEB_URL/incidents/<id>`를 열어요.
- 예전에 `public/dejafix/`에 넣어 두던 DejaFix 빌드본과 `scripts/build-dejafix.mjs`는 지웠어요.
