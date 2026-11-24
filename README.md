# AXcellent

부서마다 AI 에이전트를 두고, 요청 접수부터 분석·기획·개발·마케팅까지 에이전트끼리 일을 넘겨 처리하는 업무 자동화 솔루션이에요. 사람은 결정과 승인만 하고, 돈이나 외부 게시가 걸린 일은 승인 관문을 거쳐야 실행돼요.

## 레포 구성

| 폴더 | 내용 | 자세히 |
|---|---|---|
| `frontend/` | AXcellent 화면 (React 19 + react-router 7 + Vite + Tailwind, TypeScript) | [frontend/README.md](frontend/README.md) |
| `backend/` | AI 에이전트 서버 (Python 3.12, uv workspace, FastAPI + a2a-sdk, 서비스마다 서버 1개) | [backend/README.md](backend/README.md) |
| `docs/` | 설계 문서·계획·운영 메모 | `docs/superpowers/specs/` |

## 핵심 구조

- **에이전트끼리는 A2A로만**: 부서 도메인(`backend/domains/`)은 서로 import하지 않고 `call_agent`로 부른다
- **상태 변경은 승인 관문으로만**: 에이전트가 무언가를 바꾸는 일은 agent-template의 `execute()` → safety 규칙(등급 A·B·C)을 거친다. 돈·외부 게시는 사람 승인 뒤에만 실행된다
- **계약은 한곳에, 추가만**: 에이전트 사이 입출력 모양은 `backend/shared/contracts`에만 두고, 지우거나 이름·타입을 바꾸지 않는다
- **여러 회사, 서버 하나**: 회사마다 연결·키·설정 한 줄을 두고, 화면은 로그인한 회사, 에이전트는 요청의 회사(`meta.tenant_id`) 기준으로 읽고 쓴다
- **고객사 데이터는 받기만**: 고객사 시스템의 화면을 가져오지 않고 API로 데이터만 받아 AXcellent가 그린다. 자체 API를 가진 고객사는 사이트 변환기(짝 파일)로 우리 규격에 잇는다

## 로컬 실행

```bash
cd frontend
npm ci
npm run typecheck
npx vite build

cd ../backend
uv sync --all-packages
uv run pytest -q          # 단위 테스트는 목 스위치(테스트 전용)로 돈다
bash scripts/run_all.sh   # 백엔드 서버 전체를 실제 모드로 띄우기 (끄기: bash scripts/run_all.sh stop)
bash scripts/run_web.sh   # 화면을 http://localhost:3090 에 띄우기
```

비밀값은 `.env`에만 두고 커밋하지 않아요.

## 작업 규칙

- 기본 브랜치는 `dev`. 직접 올리지 않고 PR로만, CI(`ci`·`backend`) 통과 + 충돌 없음이면 머지
- 브랜치 이름 `<종류>/<이름>`(`feat` `fix` `refactor` `chore` `docs` `ci` `revert`), 커밋은 Conventional Commits
- 자세한 규칙은 [CLAUDE.md](CLAUDE.md)

## 관련 레포

- [Catbooks-demo](https://github.com/Catbooks-demo): 가상 고객사 사이트(캣북스 서점·누비투어·스터디온에듀)
- [DejaFix](https://github.com/DejaFix/DejaFix): 장애 대응. AXcellent 장애 대응 탭은 DejaFix API로 현재 장애만 받아 그린다
