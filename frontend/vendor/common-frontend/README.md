# common-frontend

여러 프로젝트에서 반복해 쓰던 UI 컴포넌트와 디자인 규칙을 모은 **프론트엔드 공용 템플릿**.
유일한 출처는 [DejaFix/DejaFix](https://github.com/DejaFix/DejaFix) 프론트엔드(GitHub `dev` 브랜치)다. 디자인(인디고 팔레트, 어두운 사이드바, 각진 모양)과 클래스·마크업을 그대로 옮겼다.
DejaFix에 없는 것은 Modal과 Vite 데모 페이지뿐이다(출처 "신규").
디자인 토큰(CSS 변수), 순수 CSS 클래스, React + TypeScript 컴포넌트로 되어 있다.

- 컴포넌트 문서: [docs/components/](docs/components/README.md)
- 토큰 문서: [docs/tokens.md](docs/tokens.md)
- 한 장 요약(Notion 원본): [docs/notion-summary.md](docs/notion-summary.md)
- 팀 지침서 사본: [docs/DejaFix-common-guidelines.md](docs/DejaFix-common-guidelines.md)

## 1. 역할

- 새 프로젝트가 DejaFix와 같은 화면 뼈대(AppShell)와 기본 컴포넌트 23종을 바로 가져다 쓰게 한다.
- 색·글꼴·간격·라운드·그림자를 토큰 하나로 맞추고, 라이트/다크를 자동으로 지원한다.
- CSS는 클래스만으로 동작하므로 React를 쓰지 않는 화면(서버 템플릿 등)에서도 같은 모양을 낼 수 있다.

## 2. 실행 방법

Node 22 이상, npm.

```bash
npm ci            # 의존성 설치
npm run dev       # 데모 페이지 (http://localhost:5173) — DejaFix 현황·알림 화면 구성, 컴포넌트 모음, 라이트/다크
npm run check     # 타입 검사(tsc) + 린트(eslint)
npm run build     # 데모 빌드 (tsc + vite build)
npm run format    # prettier
```

### 다른 프로젝트에서 쓰기

방법 A — git 의존성 (Vite + TS 프로젝트):

```bash
npm install github:DejaFix/common-frontend
```

```tsx
// main.tsx
import '@dejafix/common-frontend/styles.css' // tokens → global → theme → shell 순서로 한 번에
import { AppShell, Button, ToastProvider } from '@dejafix/common-frontend'
import { EChart } from '@dejafix/common-frontend/charts' // echarts 설치 시에만
```

빌드된 파일이 아니라 TypeScript 소스를 그대로 내보내므로, 쓰는 쪽의 tsc가 이 소스도 자기 tsconfig로 검사한다(React 19 + TS 5 이상, `jsx: react-jsx` 기준으로 작성).

방법 B — 복사: `src/styles/`, 필요한 `src/components/*`, `src/hooks/`, `src/lib/`를 프로젝트에 복사한다.
React가 없는 프로젝트는 `src/styles/` 만 복사해 DejaFix와 같은 클래스(`btn`, `card`, `chip`, `note`, `kpi-card`, `list-row` …)를 쓴다.

## 3. 입출력 계약

이 repo는 백엔드 API·데이터 스키마에 의존하지 않으므로 `contracts/`가 없다. 외부와의 약속은 아래 네 가지뿐이다.

| 구분   | 내용                                                                                                                                                           |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 입력   | 컴포넌트 props (각 문서의 Props 표). 데이터는 쓰는 쪽이 가공해 넘긴다.                                                                                         |
| 출력   | 화면(DOM)과 콜백 호출(`onClick`, `onChange`, `onSubmit`, `onClose` 등). 네트워크 호출·저장소 쓰기는 하지 않는다.*                                              |
| 스타일 | 색·모서리 CSS 변수 이름(`--primary`, `--surface`, `--radius-sm` …)과 클래스 이름. 바꾸면 쓰는 프로젝트가 깨질 수 있으므로 이름 변경은 주 버전 변경으로 다룬다. |
| 테마   | `<html data-theme="light\|dark">` 강제 전환, 없으면 OS 설정.                                                                                                   |

\* 예외: `AppShell`에 `storageKey`를 주면 사이드바 접힘 상태 하나만 `localStorage`에 기억한다(토큰·개인정보 아님).

## 4. 의존하는 다른 repo

없음. 이 repo는 다른 repo를 부르지 않는다.
쓰는 쪽(예: 각 프로젝트의 frontend repo)이 이 repo에 의존한다.

외부 패키지: `react`·`react-dom` 19 이상(필수 peer), `echarts` 5 이상(차트를 쓸 때만, 선택 peer).
글꼴 Pretendard는 쓰는 쪽 `index.html`에서 CDN으로 불러온다(데모 `index.html` 참고).

## 5. 하지 않는 것

- 특정 백엔드 API 호출, 인증, 데이터 불러오기(react-query·fetch 래퍼 등)
- 특정 프로젝트 전용 화면·컴포넌트 (예: 업무 전용 상세 카드, 등급 칩, 외부 시스템 연결 상태, 채팅 기능)
- 특정 프로젝트의 도메인 규칙·설정 묶음·외부 시스템 연동 코드와 문서
- 라우팅 — 메뉴 링크는 `renderLink`로 쓰는 쪽 라우터에 맡긴다
- 분석·판정 문장 생성 — 화면은 받은 값을 표시만 하고, 없는 값은 '확인할 수 없음'·'확인 불가'로 표시한다
- npm 레지스트리 배포 (git 의존성 또는 복사로 쓴다)
