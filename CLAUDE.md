# Axcellent-bs 규약

AXcellent 데모 프론트. 백엔드 없이 녹화 응답으로 돈다. 자세한 구조는 `frontend/README.md`.

- 스택: React 19 + react-router 7 + Vite + Tailwind, TypeScript. 코드는 전부 `frontend/`
- 관련 레포: ax-company-hq/ax-frontend(캣북스 쇼핑몰·대시보드), ax-company-hq/ax-backend(AI 에이전트), DejaFix/DejaFix(장애 대응). 이 레포에서 그 레포들에 쓰지 않는다

## 지금 방향

- 캣북스 대시보드(ax-frontend `dashboard-web`)는 없앤다. 거기 있던 AI 7개(총괄·고객지원·재무·데이터분석·인사·마케팅·기획개발)는 AXcellent에서 관리하고 디자인도 AXcellent로 맞춘다
- DejaFix는 그대로 둔다. AXcellent 장애 대응 탭은 DejaFix API(`GET /api/incidents?status=problem`)로 현재 장애만 받아 AXcellent가 그린다
- 원칙: 다른 시스템의 화면(iframe·DOM 긁기)을 가져오지 않는다. 데이터만 받고 화면은 AXcellent가 그린다

## 작업 규칙

1. 고치는 곳은 `frontend/`(와 `docs/`). `node_modules/`, `dist/`(생성물)는 손으로 고치지 않는다
2. 자동화 파일(`.claude/`, `.github/`, `CLAUDE.md`)은 사람만 바꾼다
3. 녹화본(`src/mock/fixtures.json`)을 지어내지 않는다. 새 응답은 계약 문서나 실제 녹화가 출처
   - 출처가 아직 없는 화면은 임시 목 데이터를 `src/mock/draft-<이름>.ts`에 둘 수 있다 (첫 줄에 `// draft:` 표시, 그 화면에서만 import, `fixtures.json`에는 넣지 않음)
4. 디자인은 AXcellent 토큰과 공용 클래스(`card`, `chip`, `btn-primary`, `btn-ghost`, `field` 등)를 쓴다
5. 타입 검사를 우회해서 통과시키지 않는다
6. 비밀값은 `.env`에만. 커밋 금지

## 브랜치와 머지

- 기본 브랜치 `dev`. dev·main에 직접 올리지 않고 PR로만
- 브랜치 이름: `<종류>/<이름>`, 영어 소문자와 `-`. 종류는 `feat` `fix` `refactor` `chore` `docs` `ci` `revert`
  - 루프가 이슈를 처리할 때: `feat/<이슈번호>-<짧은이름>`
- 머지 조건: `ci` 체크 초록 + 작성자 아닌 팀원 승인 1개. 조건이 맞으면 자동 머지(`gh pr merge <번호> --auto --squash`)
- 커밋: Conventional Commits (`feat(frontend): ...`)

## 로컬 훅 (`.claude/hooks/`)

- `guard-folder.mjs`: 파일 쓰기를 `frontend/`·`docs/`로 제한. 자동화 파일·생성물·`.env` 차단
- `guard-bash.mjs`: 원격 업로드는 `origin <종류>/<이름>` 한 줄만(dev·main·force 차단), PR 머지는 자동 머지 예약 한 줄만
  - 명령에 `git`과 업로드 단어가 같이 있으면 그 한 줄 형태가 아닌 한 막힌다. 커밋 메시지·PR 본문에 그 단어가 들어가면 오탐이 나므로 긴 글은 파일로 써서 `git commit -F <파일>`, `gh pr create --body-file <파일>`로 넘긴다
- 사람이 자동화 파일을 직접 고칠 때만 `.claude/settings.local.json`에 `{"env": {"AX_HUMAN": "1"}}` (예시: `settings.local.example.json`, 커밋하지 않음)

## 확인 방법

```bash
cd frontend
npm ci
npm run typecheck
npx vite build
npm run dev   # http://localhost:3090, ?as=<계정 id>로 바로 로그인
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
- 사람이 이슈에 `loop-ok` 라벨을 붙여야 루프가 그 이슈를 작업한다. 처음에는 **화면만 만드는 이슈**부터 붙이는 걸 권한다 (데이터 출처가 없으면 임시 목 데이터로 화면만 만든다)
- 루프가 새 패키지를 추가해야 하는 이슈는 이슈 본문에 패키지 이름을 적는다. 적힌 패키지 추가만 사람 승인 없이 리뷰·머지되고, 그 밖의 의존성 변경은 `needs-human`
- 루프를 돌리기 전 레포 설정(사람): auto-merge 허용, `dev` 브랜치 보호(`ci` 필수 체크 + 승인 1개), 라벨 7개(`loop-ok` `needs-human` `in-progress` `waiting` `needs-split` `external` `plan`). 안 하면 자동 머지 예약이 실패한다
