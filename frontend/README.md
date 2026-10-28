# AXcellent 데모

구조 회의용 프로토타입이에요. 백엔드 없이 프론트만으로 돌아가요.

- 이 폴더(`frontend/`)에서 `npm ci` → `npm run dev`로 띄워요 (http://localhost:3090). 처음 만든 곳은 [NYejin/axcellent-demo](https://github.com/NYejin/axcellent-demo)예요.
- GitHub Pages로 올릴 때는 `npm run build:pages`가 `/Axcellent-bs/` 경로로 빌드해요. Pages 배포 워크플로는 아직 넣지 않았어요.
- 서비스 화면은 [ax-frontend](https://github.com/ax-company-hq/ax-frontend) `dashboard-web`(dev, c1eb44a)을 그대로 가져왔어요.

## 보여 주는 구조

부서는 코드에 두지 않아요. 부서 안에 있던 기능을 **서비스** 하나하나로 꺼냈고, 회사는 Adobe Creative Cloud처럼 필요한 서비스만 골라 써요. 직원 계정에는 쓸 서비스를 배정해요.

```
┌──────────── 고객사 사내망 (폐쇄) ─────────────────────────┐
│  [서비스 고르기] 회사가 쓸 서비스를 켜고 끔                  │
│  [로그인] → 내게 배정된 서비스만 메뉴에 보임                 │
│  [DejaFix] 장애 대응 · PC·서버 연결 (IP로 등록, 자빅스처럼)  │
│  [회사 서비스 연결] ── URL·API·키 ──→ 캣북스 온라인 서점     │
└───────────────────────────────────────────────────────────┘
  우리는 서버 없이 프로그램만 제공해요. 데이터는 회사 안에만 있어요.
```

| 서비스 | 만든 쪽 | 주소 |
| --- | --- | --- |
| 장애 대응 (DejaFix 화면, 메뉴는 바깥 사이드바 하위 메뉴) | DejaFix | `/infra`, `/infra/reports` … |
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

- 화면은 ax-frontend의 부서 화면을 그대로 다시 쓰고, 서비스 단위로 메뉴만 나눴어요. 서비스 목록은 `src/demo/services.ts`에 있어요.
- 오른쪽 위 **프로필 전환** 드롭다운으로 예시 직원 계정을 바꿔 가며 볼 수 있어요. 주소 뒤에 `?as=support`처럼 붙이면 그 계정으로 바로 들어가요. 계정 id는 `admin`, `infra`, `dev`, `planning`, `support`, `marketing`, `finance`, `hr`, `data`예요.
- 화면 모양은 DejaFix common-frontend(보라 사이드바, 인디고, 각진 모서리) 하나만 써요.

## 데이터는 어디서 오나

- `src/mock/fixtures.json`: ax-backend 서비스들을 목 모드(`*_MOCK=1`)로 띄우고, 화면이 부르는 GET 응답을 녹화한 거예요(2026-10-27, 79개).
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

## DejaFix 서비스 화면

인프라 AI 화면은 DejaFix 레포(`DejaFix/DejaFix`)의 frontend를 목 데이터 버전으로 빌드해 `public/dejafix/`에 넣은 거예요. 백엔드 없이 DejaFix 자체 목(MSW)으로 돌아가고, 데모 관리자로 바로 로그인돼요.

DejaFix 화면을 최신으로 바꾸려면:

```
node scripts/build-dejafix.mjs <DejaFix 레포 경로>   # 기본 origin/dev
```
