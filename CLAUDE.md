# Axcellent-bs 규약

`frontend/`(화면)와 `backend/`(AI 에이전트 서버)가 한 레포에 있다. 화면 구조는 `frontend/README.md`, 백엔드 구조는 `backend/README.md`.

- 스택(프론트): React 19 + react-router 7 + Vite + Tailwind, TypeScript. `frontend/`
- 스택(백엔드): Python 3.12, uv workspace, FastAPI + a2a-sdk, 서비스마다 서버 1개. `backend/`
- 관련 레포: ax-company-hq/ax-frontend(캣북스 쇼핑몰·대시보드), DejaFix/DejaFix(장애 대응). ax-company-hq/ax-backend는 `backend/`로 가져왔고 원본은 참고만 한다. 이 레포에서 다른 레포에 쓰지 않는다

## 지금 방향

- 캣북스 대시보드(ax-frontend `dashboard-web`)는 없앤다. 거기 있던 AI 7개(총괄·고객지원·재무·데이터분석·인사·마케팅·기획개발)는 AXcellent에서 관리하고 디자인도 AXcellent로 맞춘다
- DejaFix는 그대로 둔다. AXcellent 장애 대응 탭은 DejaFix API(`GET /api/incidents?status=problem`)로 현재 장애만 받아 AXcellent가 그린다
- 원칙: 다른 시스템의 화면(iframe·DOM 긁기)을 가져오지 않는다. 데이터만 받고 화면은 AXcellent가 그린다

## 작업 규칙

1. 고치는 곳은 `frontend/`, `backend/`, `docs/`. 생성물(`node_modules/`, `dist/`, `.venv/`, `.logs/`, `__pycache__/`)은 손으로 고치지 않는다
2. 자동화 파일(`.claude/`, `.github/`, `CLAUDE.md`)은 사람만 바꾼다
3. 화면은 실제 모드 백엔드만 부른다. 녹화본·데모 모드는 없다. 데이터를 지어내지 않는다
   - 출처가 아직 없는 화면은 빈 상태 + "준비 중" 표시(`src/components/Preparing.tsx`)로 둔다
   - 백엔드 목 스위치(`AGENT_TEMPLATE_MOCK`, `<서비스>_MOCK`)·테스트용 가짜 부서·dashboard-api `mock_only` 응답은 **테스트 전용**이다. 로컬 실행·데모에 쓰지 않는다
4. 디자인은 AXcellent 토큰과 공용 클래스(`card`, `chip`, `btn-primary`, `btn-ghost`, `field` 등)를 쓴다
5. 타입 검사를 우회해서 통과시키지 않는다
6. 비밀값은 `.env`에만. 커밋 금지

## 백엔드 규칙 (`backend/`)

- 구조: `shared/`(contracts, agent-template, safety, memory, dashboard-api, shop-api) / `domains/`(업무 로직) / `services/`(서비스 13개: 카드·포트·실행 설정)
- 계약은 `shared/contracts`에만 둔다. 추가만 하고 삭제·이름 변경·타입 변경은 하지 않는다
- `domains`끼리 import하지 않는다. 서로는 A2A(`call_agent`)로만 부른다
- 상태 변경은 agent-template의 `execute()`로만 한다 (safety 승인 관문)
- 서비스 id는 `frontend/src/demo/services.ts`의 `SERVICES` id와 같게 한다
- 테스트를 지우거나 약하게 만들지 않는다
- 비밀값은 `.env`에만 둔다. LLM 모델 이름을 하드코딩하지 않는다
- `uv.lock`은 손으로 고치지 않는다. `pyproject.toml`을 바꾼 PR에서만 바뀐다

## 브랜치와 머지

- 기본 브랜치 `dev`. dev·main에 직접 올리지 않고 PR로만
- 브랜치 이름: `<종류>/<이름>`, 영어 소문자와 `-`. 종류는 `feat` `fix` `refactor` `chore` `docs` `ci` `revert`
  - 루프가 이슈를 처리할 때: `feat/<이슈번호>-<짧은이름>`
- 머지 조건: CI(`ci`, `backend`) 초록 + 충돌 없음. 승인은 필요 없다. PR을 올리면 바로 자동 머지 예약(`gh pr merge <번호> --auto --squash`)
- 리뷰는 선택이다. 문제를 보면 코멘트로 남기고, 막아야 하면 PR에 `needs-human` 라벨을 붙인다
- 커밋: Conventional Commits (`feat(frontend): ...`)

## 로컬 훅 (`.claude/hooks/`)

- `guard-folder.mjs`: 파일 쓰기를 `frontend/`·`backend/`·`docs/`로 제한. 자동화 파일·생성물·`.env` 차단
- `guard-bash.mjs`: 원격 업로드는 `origin <종류>/<이름>` 한 줄만(dev·main·force 차단), PR 머지는 자동 머지 예약 한 줄만
  - 명령에 `git`과 업로드 단어가 같이 있으면 그 한 줄 형태가 아닌 한 막힌다. 커밋 메시지·PR 본문에 그 단어가 들어가면 오탐이 나므로 긴 글은 파일로 써서 `git commit -F <파일>`, `gh pr create --body-file <파일>`로 넘긴다
- 사람이 자동화 파일을 직접 고칠 때만 `.claude/settings.local.json`에 `{"env": {"AX_HUMAN": "1"}}` (예시: `settings.local.example.json`, 커밋하지 않음)

## 확인 방법

```bash
cd frontend
npm ci
npm run typecheck
npx vite build

cd ../backend
uv sync --all-packages
uv run pytest -q          # 단위 테스트는 목 스위치(테스트 전용)로 돈다
bash scripts/run_all.sh   # 백엔드 서버 전체를 실제 모드로 띄우기 (끄기: bash scripts/run_all.sh stop)
bash scripts/run_web.sh   # 화면을 실제 모드 토큰과 함께 http://localhost:3090 에 띄우기 (?as=<계정 id>로 바로 로그인)
```

## 루프

- 루프는 **루프 전용 worktree**에서만 돌린다. 루프가 브랜치를 바꾸고 빌드·dev 서버를 띄우기 때문에, 데모를 띄우는 폴더에서 돌리면 데모가 죽는다
  ```bash
  # 원래 클론에서 한 번만
  git fetch origin
  git worktree add --detach ../Axcellent-bs-loop origin/dev
  ```
  그 폴더(`../Axcellent-bs-loop`)를 Claude Code로 열고 `/loop 30m /collab-loop` (닥터: `/loop 30m /collab-loop 닥터`). 원래 클론에서 돌리면 루프가 "루프 전용 worktree 필요"만 보고하고 멈춘다
- 데모·개발은 원래 클론에서 3090 포트. 루프의 dev 서버는 3190 포트만 쓴다
- 루프는 `run_all.sh`로 백엔드 서버를 띄우지 않는다 (포트 8000~8122를 사람의 데모와 같이 써서 충돌). 백엔드 확인은 `uv run pytest -q`까지만
- 사람이 이슈에 `loop-ok` 라벨을 붙여야 루프가 그 이슈를 작업한다. 처음에는 **화면만 만드는 이슈**부터 붙이는 걸 권한다 (데이터 출처가 없으면 빈 상태 + "준비 중" 표시로 화면만 만든다)
- 루프가 새 패키지를 추가해야 하는 이슈는 이슈 본문에 패키지 이름을 적는다. 적힌 패키지 추가만 사람 확인 없이 머지되고, 그 밖의 의존성 변경은 `needs-human`
- 루프를 돌리기 전 레포 설정(사람): auto-merge 허용, `dev` 브랜치 보호(`ci` 필수 체크, 승인 0개), 라벨 7개(`loop-ok` `needs-human` `in-progress` `waiting` `needs-split` `external` `plan`). 안 하면 자동 머지 예약이 실패한다
