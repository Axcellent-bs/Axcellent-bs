# 개발자 본인 Claude로 일감 받기 (MCP 일감 서버)

개발 서비스(dev, 8111)에 쌓인 코딩 일감을 **내 PC의 Claude Code**가 MCP로 가져가 구현하고, 커밋·PR을 올린 뒤 번호를 알려 주는 방식입니다. 코딩 토큰은 서버 API 키가 아니라 각자 Claude 구독에서 나갑니다.

> **꼭 지켜 주세요**
> - 내 PC에서, 내 Claude 계정으로 로그인해서만 돌립니다.
> - 개인 구독을 서버에 모아 돌리거나 다른 사람과 계정·토큰을 나눠 쓰지 않습니다. 구독 약관 위반이고, 누가 한 작업인지도 남지 않습니다.
> - 일감 서버 토큰은 사람(개발자) 토큰이고, 토큰 이름이 "가져간 사람"으로 남습니다. 저장소에 커밋하지 않습니다.

## 흐름

| 단계 | MCP 도구 | 상태 |
|---|---|---|
| 일감 보기 | `list_tasks` | 대기 중(`queued`) + 내가 쥔 일감 |
| 가져가기 | `claim_task` | `claimed` (임대 30분 시작) |
| 진행 알리기 | `report_progress` | `writing` (임대 다시 30분) |
| PR 알리기 | `report_pr` | `opened` → 사람 머지 또는 GitHub에서 머지하면 `merged` |
| 내려놓기 | `release_task` | 다시 `queued` |

- 임대 시간(기본 30분, 서버 `PLANNING_LEASE_MINUTES`) 동안 진행 보고가 없으면 일감은 대기열로 돌아갑니다. 오래 걸리면 중간중간 `report_progress`를 부르면 됩니다.
- `claim_task`가 주는 명세: 기획(`plan`), 완료 기준(`done`), 경로 힌트(`paths`), 레포(`repo`), 브랜치(`branch`, 예: `gen/t-d3`), PR 제목(`pr_title`), 커밋 규칙(`commit_rule`).
- **커밋 메시지와 PR 제목에 작업 id(예: `T-D3`)를 넣습니다.** PR 제목에 id가 없으면 `report_pr`가 잇지 않습니다.
- PR 크기·경로는 서버가 GitHub에서 직접 읽어 실행 관문(`generate_pr`)을 지납니다. 규칙에 걸리면 PR은 기록되고 사람 확인으로 넘어갑니다.

일감은 개발 화면에서 만들거나 `POST /api/planning/dev-tasks`에 `"mode": "subscription"`을 넣어 만듭니다 (넣지 않으면 지금처럼 서버 코딩 에이전트가 돕니다).

## Claude Code에 붙이기

운영자에게 내 사람 토큰을 받습니다 (서버 `SAFETY_HUMAN_TOKENS`의 `이름:토큰`, 데모 목 모드는 `human:<내 이름>`).

```bash
claude mcp add --transport http axcellent-dev http://<개발 서비스 주소>:8111/api/planning/mcp \
  --header "Authorization: Bearer <내 토큰>"
```

- 기본 범위(local)로 넣으면 내 PC 설정(`~/.claude.json`)에만 저장됩니다. 토큰이 든 `.mcp.json`을 레포에 커밋하지 않습니다.
- 확인: `claude mcp list`, Claude Code 안에서 `/mcp`.
- 작업할 레포는 미리 clone 해 두고, `gh auth login`으로 내 GitHub 계정에 로그인해 둡니다.

## 대화로 쓰기

레포 폴더에서 `claude`를 켜고 이렇게 말하면 됩니다.

```
axcellent-dev에서 일감 목록 보여 줘. T-D3 가져가서 명세대로 구현하고 PR 올려 줘.
```

## 자동으로 돌리기 (`claude -p`)

내 PC에서 켜 둔 동안만 도는 반복입니다. 프롬프트 예시 (`work.txt`):

```
너는 Axcellent 개발 일감을 처리한다. 순서대로 한다.
1. mcp__axcellent-dev__list_tasks로 일감을 본다. 내가 쥔 일감(mine: true)이 있으면 그것부터, 없으면 대기 중 하나를 claim_task로 가져간다. 일감이 없으면 "일감 없음"만 말하고 끝낸다.
2. 기준 브랜치(repo.base_branch)를 최신으로 받고 명세의 branch로 새 브랜치를 만든다.
3. plan과 done을 모두 만족하게 구현한다. paths는 먼저 볼 곳이다. 단계마다 report_progress로 한 줄 알린다.
4. 레포의 테스트·린트를 돌려 통과시킨다. 못 하면 release_task에 이유를 적고 끝낸다.
5. 커밋 메시지와 PR 제목에 작업 id를 넣어 커밋하고 gh pr create로 base_branch에 PR을 올린다 (PR 제목은 pr_title).
6. report_pr에 PR 번호를 알린다.
```

실행 (레포 폴더에서):

```bash
claude -p "$(cat work.txt)" \
  --allowedTools "mcp__axcellent-dev__*" "Read" "Edit" "Write" "Bash(git:*)" "Bash(gh pr:*)" "Bash(uv run:*)" "Bash(npm run:*)"
```

반복은 내 PC의 스케줄러로 겁니다. 예: 10분마다 (bash)

```bash
while true; do claude -p "$(cat work.txt)" --allowedTools "mcp__axcellent-dev__*" "Read" "Edit" "Write" "Bash(git:*)" "Bash(gh pr:*)"; sleep 600; done
```

- `--allowedTools`는 레포의 테스트 명령에 맞게 줄이거나 늘립니다. `--dangerously-skip-permissions`는 쓰지 않습니다.
- 한 번에 일감 하나만 처리합니다. 끝나지 않은 일감은 다음 차례에 `mine: true`로 이어서 받습니다.

## REST로 직접 부르기

MCP 없이도 같은 일을 할 수 있습니다 (모두 `Authorization: Bearer <내 토큰>`).

| 메서드 | 경로 | 본문 |
|---|---|---|
| GET | `/api/planning/work/tasks` | |
| POST | `/api/planning/work/tasks/{task_id}/claim` | |
| POST | `/api/planning/work/tasks/{task_id}/progress` | `{text}` |
| POST | `/api/planning/work/tasks/{task_id}/pr` | `{number, url?}` |
| POST | `/api/planning/work/tasks/{task_id}/release` | `{reason?}` |

오류는 `{error: {code, message}}`: 이미 다른 사람이 가져감 `409 ALREADY_CLAIMED`, 내가 가져간 게 아님 `403 NOT_CLAIMANT`, 임대가 끝남 `409 NOT_CLAIMED`(다시 claim), PR 번호를 못 읽음 `404 PR_NOT_FOUND`.
