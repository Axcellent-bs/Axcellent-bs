# 개발 AI A2A holdout 평가

개발 AI가 다른 부서에 답하는 A2A skill이 처음 보는 기록에서도 문서대로 답하는지 잰다.

```bash
cd backend
uv run python domains/dev/eval/holdout.py --n 100            # dev seed (하네스 고칠 때)
uv run python domains/dev/eval/holdout.py --n 100 --holdout  # holdout seed (한 번만, 숫자만)
```

## 대상

| skill | 요청 부서 | 정답 기준 |
| --- | --- | --- |
| `find_related_prs` | 총괄·인프라·데이터 | 구간(시작 30분 앞부터) 안 머지 PR, component는 경로 접두사 또는 영역 값, 최근 머지 순 |
| `verify_rollback_target` | 안전 관문 | 배포가 사건 뒤면 거절, 배포 전 30분 안 머지 PR이 있으면 승인, 없으면 거절. safety 검토 형식은 개발 기록(버전 배포·사건)을 먼저 씀 |
| `list_in_progress` | 인사·총괄 | README 3장 hr 연동 항목 표: 진행 중 작업(`진행 중`·`PR 검수`·`머지 대기`·`C 보류`), 열린 PR, 확인 전 배포 |
| `verify_handover` | 안전 관문 | 진행 중 항목이 인계 목록에서 빠지면 거절, 이미 끝난 항목은 `stale_item_ids` |
| `link_incident` | 총괄·인프라 | 연결 문단, 같은 제목의 끝나지 않은 요청이면 그 ID + `linked_duplicate`, 아니면 새 재발 방지 요청. 같은 키 두 번째는 이전 결과 |
| `list_dev_outcomes` | 인사·총괄 | 계약 docstring: 작업별 결과(복잡도·사람 확인 여부·토큰)와 합계 |

뺀 것: `start_dev`·`receive_release_order`·`receive_request_pause`(기획 → 개발 흐름을 이어 가는 일이라 맞고 틀림을 숫자로 셀 수 없음), `dev_chat_*`(LLM 대화).

## 방법

- 세계(기록 묶음) 하나에 요청·기획서·작업·PR·사건·코딩 기록을 seed로 무작위로 만들고 질의 12개를 보낸다.
- 실제로 생기는 모양을 넣는다: 같은 시각 머지, 같은 버전 두 번 배포, 중단한 작업, 닫은 PR, `./` 경로, 비슷한 접두사(`shop-api/payments_old`), 떼어 온 레포 경로(`shop-web/src/pages/Cart.tsx`), 문단이 없는 작업, 끝난 요청, 중복 연결 요청.
- 개발 AI는 실서버와 같은 입구로 부른다: `dev_agent.main:app`의 `POST /a2a` → handlers → service → 규칙. 실행 관문은 시험과 같은 목 관문이다.
- 정답은 개발 코드를 쓰지 않고 문서(README 3장, 계약 docstring)대로 `holdout.py` 안에서 따로 계산한다. 시연값 표(영역 표·담당자 대응·30분·50줄·3파일)만 정책처럼 가져다 쓴다.
- 문서에 없는 순서·동점 처리는 코드 docstring을 정답으로 삼는다(`# 규칙:` 주석).

## 결과

| seed | 세계 | 질의 | 전체 | 관련 PR | 롤백 확인 | 진행 중 항목 | 인수인계 확인 | 사건 연결 | 작업 결과 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dev` | 100 | 1,200 | 95.4% | 100% | 100% | 92.5% | 80.0% | 100% | 100% |
| **`20261110_01` (holdout)** | 100 | 1,200 | **95.0%** | 100% | 100% | 89.5% | 80.5% | 100% | 100% |

- 결과 파일: `results/dev.json`, `results/holdout_20261110_01.json`
- 틀린 원인(dev seed에서만 열어 봄): `list_in_progress`·`verify_handover`가 **중단한 작업**(`중단`, 작업 중단으로 닫힘)과 **닫은 PR**(`closed`)을 진행 중으로 센다. README 표는 진행 중 작업을 `진행 중`·`PR 검수`·`머지 대기`·`C 보류`, 열린 PR을 `open`으로만 정한다. 그래서 중단한 작업이 있는 담당자의 인수인계가 "빠진 항목"으로 거절되고, 그 ID가 `stale_item_ids`에 안 나온다. dev seed 틀린 질의는 전부 이 원인이었다.
- 고치는 것은 코드 PR로 따로 한다(평가 정의와 코드를 같은 PR에서 바꾸지 않는다).

## 규칙

- 하네스는 dev seed로만 고친다. holdout seed는 한 번 돌려 숫자만 보고, 틀린 질의 내용은 열지 않는다(`--show`가 holdout에선 꺼진다).
- 평가 정의(세계 만들기·정답 계산·비교)를 바꾸는 PR과 개발 코드를 고치는 PR은 나눈다.
- holdout을 다시 만들 때는 seed를 바꾸고 이유를 위 표에 적는다. seed는 만든 날 `YYYYMMDD_nn`.

## 작업 성공률 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 개발 1순위)

"완료 기준을 모두 채운 PR/작업 수" · "정답 diff 있는 작업만" 평가. holdout.py와 달리 이건
A2A 질의·응답(규칙)이 아니라 **LLM이 실제로 코드를 쓰는 경로**(`dev_agent.coder.run`: 파일
고르기 → 쓰기 → 리뷰 → PR → 자동 머지)를 잰다. 평가 데이터가 아예 없어서 새로 만들었다(실제로는
11/24~26 운영 시험 요청이 후보지만 이 샌드박스에는 그 기록이 없어 합성 데이터로 하네스만 둔다).

- `task_success_cases.json`: 합성 코딩 작업 8건. 각 작업은 작은 목 저장소(seed 파일)·기획·완료
  기준과, 사람이 미리 써 둔 정답 최종 파일 글(golden)을 함께 둔다. 전부 `shop-web/` 경로다(기본
  자동 머지 범위가 `shop-web/`라, 다른 경로는 정책상 사람 확인으로 가서 모델 비교와 무관하게
  머지율이 떨어진다 — holdout.py seed처럼 `shop-web/`로 맞췄다)
- `task_success_eval.py`: 작업마다 `MockVcsAdapter`에 seed 파일을 심고 실제
  `DevService.create_dev_task` → `coder.run()`(production 그대로, CI는 목 모드라 검사 없는
  레포로 바로 통과) → 자동 머지까지 돌린다. 원래 글과 golden을 difflib로 비교해 "더해진 줄"을
  뽑고(정답 diff), 병합된 최종 글에 그 줄이 다 들어 있으면 완료 기준을 채운 것으로 본다(이
  "더해진 줄 다 포함" 판정은 production 코드가 아니라 이 평가 전용 근사치다 — done 문장을 사람이
  읽고 판정하는 게 아니라 정답 diff의 핵심 줄을 코드로 맞춰 보는 것)
  - 실행: `cd backend && uv run python domains/dev/eval/task_success_eval.py`
  - 모델은 `DEV_LLM_MODEL` → 없으면 `LLM_MODEL`
  - 지표: `task_success_rate`(완료 기준을 모두 채운 PR/작업 수, 전체 8건 중)·
    `merge_rate`(PR이 자동 머지된 비율)·`criteria_met_given_merged`(머지된 것 중 완료 기준을
    채운 비율 — 리뷰는 통과했지만 실제로는 안 고친 경우를 가른다)·`avg_attempts`(평균 재시도
    횟수)
- 모델 비교: `model_compare_task_success.py <라벨>` →
  `results/model_compare/task_success_<라벨>.json`,
  `model_compare_task_success_report.py <라벨1> <라벨2> ...`로 비교표

```bash
cd backend
DEV_LLM_MODEL=claude-haiku-5-5 uv run python domains/dev/eval/model_compare_task_success.py haiku
DEV_LLM_MODEL=claude-sonnet-5-5 uv run python domains/dev/eval/model_compare_task_success.py sonnet
uv run python domains/dev/eval/model_compare_task_success_report.py haiku sonnet
```

**한계**
- 골든셋이 합성·소규모다(8건, 전부 `shop-web/` 단일 파일 작업). 11/24~26 운영 시험 요청 등
  실제 코딩 기록으로 바꾸면 더 믿을 만하다 — 이 하네스와 `task_success_cases.json`의 형식은
  그대로 두고 `cases`만 교체하면 된다.
- "더해진 줄 다 포함" 판정은 공백·줄바꿈 단위 비교라, 뜻은 같지만 표현이 다른 정답(예: 같은 값을
  다른 변수명으로 씀)은 틀린 것으로 셀 수 있다. production 리뷰(LLM)의 통과·불일치와는 다른
  판정이라 둘을 나란히 본다(`by_status`에 `waiting_human`·`failed`도 같이 남는다).
- 리뷰 위험도가 낮아(`low`) LLM 리뷰를 건너뛰는 경로(스타일·문서 파일)는 이 골든셋에 없다.
- 이 세션(클라우드 샌드박스)에는 키가 없어 가짜 LLM으로 세 경로(완료 기준을 다 채우는 답·
  채우지 않는 답·LLM 실패)만 검증했다. 실제 비교 숫자는 키가 있는 로컬 PC에서 위 명령으로
  내야 한다.

## 범위 정확도 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 개발 2순위)

"기대 파일·기대 줄만 바꾼 비율" 평가. 작업 성공률(위)과 같은 production 경로를 쓰지만
다른 것을 잰다: 작업 성공률은 "목표를 이뤘나"(더 고쳐도 괜찮음, 재현율 쪽), 범위 정확도는
"그것만 고쳤나"(더 고치면 틀림, 정밀도 쪽)다. 이것도 평가 데이터가 없어 새로 만들었다.

- `scope_accuracy_cases.json`: 합성 코딩 작업 6건. `task_success_cases.json`과 같은 형식(seed
  파일·기획·완료 기준·golden)에, 이름이 비슷해 집기 쉬운 미끼 파일(`BannerOld.tsx`·
  `QtyButtonsLegacy.tsx` 등)을 저장소에 같이 둔다 — 모델이 정답 파일만 고르는지, 미끼까지
  건드리는지를 보려는 것
- `scope_accuracy_eval.py`: 작업마다 `MockVcsAdapter`에 seed(미끼 포함)를 심고 실제
  `DevService.create_dev_task` → `coder.run()` → 자동 머지까지 돌린다(task_success_eval.py와
  같은 흐름). 두 가지를 본다:
  - 파일 범위: 실제로 고친 파일 집합이 정답 파일 집합과 정확히 같은가(미끼 파일을 같이
    고쳤으면 틀림)
  - 줄 범위: 병합된 최종 글이 정답(golden) 글과 줄 단위로 정확히 같은가(정답에 없는 줄을
    더 얹었으면 틀림 — 작업 성공률의 "더해진 줄이 다 들어 있나"와 반대 방향 검사)
  - 실행: `cd backend && uv run python domains/dev/eval/scope_accuracy_eval.py`
  - 모델은 `DEV_LLM_MODEL` → 없으면 `LLM_MODEL`
  - 지표: `scope_accuracy`(파일·줄 범위를 모두 지킨 비율, 머지된 작업 중)·`merge_rate`·
    `file_scope_rate`·`line_scope_rate`·`extra_files_total`(미끼까지 고친 수)·
    `extra_lines_total`(범위 밖에 더 얹은 줄 수)
- 모델 비교: `model_compare_scope_accuracy.py <라벨>` →
  `results/model_compare/scope_accuracy_<라벨>.json`,
  `model_compare_scope_accuracy_report.py <라벨1> <라벨2> ...`로 비교표

```bash
cd backend
DEV_LLM_MODEL=claude-haiku-5-5 uv run python domains/dev/eval/model_compare_scope_accuracy.py haiku
DEV_LLM_MODEL=claude-sonnet-5-5 uv run python domains/dev/eval/model_compare_scope_accuracy.py sonnet
uv run python domains/dev/eval/model_compare_scope_accuracy_report.py haiku sonnet
```

**한계**
- 골든셋이 합성·소규모다(6건, 파일당 미끼 1~2개). task_success_cases.json과 마찬가지로
  11/24~26 운영 시험 요청 등 실제 기록으로 바꾸면 더 믿을 만하다.
- 줄 범위 판정(`final == golden`)은 글자 그대로 비교라, 뜻은 같지만 줄바꿈·공백·변수명이
  다른 정답도 "범위를 벗어남"으로 셀 수 있다. production 판정이 아닌 이 평가 전용 근사치다.
- 파일 범위는 "정답 파일 집합과 정확히 같은가"만 본다. 정답 파일 중 일부만 고치고 나머지는
  빠뜨린 경우도 틀림으로 센다(부분 일치는 따로 재지 않는다).
- 이 세션(클라우드 샌드박스)에는 키가 없어 가짜 LLM으로 세 경로(정답만 정확히 고치는 답·
  미끼까지 같이 고치는 답·정답 파일에 범위 밖 줄을 더 얹는 답)를 검증했다. 실제 비교 숫자는
  키가 있는 로컬 PC에서 위 명령으로 내야 한다.

## CI 자동 수정 성공률 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 개발 3순위)

"실패 → 한 번 고친 뒤 통과 비율" 평가. `coder._fix_ci`(CI가 실패하면 로그를 보고 한 번 고쳐
같은 PR에 다시 커밋하는 길, `MAX_CI_FIXES=1`)가 실제로 동작하는지를 본다. 일부러 깬 PR
케이스가 없어 새로 만들었다.

- `ci_fix_cases.json`: 합성 코딩 작업 8건. 각 작업은 실제 타입체크·린트 오류 형식의 CI 로그
  (`ci_log`)와, 로그가 가리키는 문제를 실제로 고치면 최종 글에 들어 있어야 할 문자열
  (`fix_must_contain`)·사라져 있어야 할 문자열(`fix_must_not_contain`)을 같이 적어 뒀다
  (예: TS 타입 오류 로그 → 올바른 필드명이 들어가고 잘못된 필드명은 없어야 함)
- `ci_fix_eval.py`: `CiFixAdapter`(`MockVcsAdapter` 상속)가 PR을 열면 첫 CI 확인은 항상
  실패시키고 정해 둔 로그를 돌려준다. `coder._fix_ci`가 그 로그를 데이터로 넣어 LLM에게
  다시 쓰게 하고 커밋하면, 그 다음 CI 확인부터는 **커밋된 글이 `fix_must_contain`·
  `fix_must_not_contain`을 채우는지 직접 보고** 통과·실패를 정한다 — 스크립트로 미리 정해
  둔 pass/fail이 아니라, 실제로 고쳤는지를 production의 CI-실패-고침 경로가 그대로 거쳐
  가며 판정하게 한 것
  - 실행: `cd backend && uv run python domains/dev/eval/ci_fix_eval.py`
  - 모델은 `DEV_LLM_MODEL` → 없으면 `LLM_MODEL`
  - 지표: `ci_fix_success_rate`(전체 중 고친 뒤 통과까지 간 비율)·`fix_attempt_rate`(로그를
    읽고 실제로 다시 커밋까지 갔는지, 고친 내용이 없으면 커밋하지 않고 포기하므로 0으로
    셈)·`success_given_attempted`(시도한 것 중 성공 비율)
- 모델 비교: `model_compare_ci_fix.py <라벨>` → `results/model_compare/ci_fix_<라벨>.json`,
  `model_compare_ci_fix_report.py <라벨1> <라벨2> ...`로 비교표

```bash
cd backend
DEV_LLM_MODEL=claude-haiku-5-5 uv run python domains/dev/eval/model_compare_ci_fix.py haiku
DEV_LLM_MODEL=claude-sonnet-5-5 uv run python domains/dev/eval/model_compare_ci_fix.py sonnet
uv run python domains/dev/eval/model_compare_ci_fix_report.py haiku sonnet
```

**한계**
- 골든셋이 합성·소규모다(8건, `shop-web/` 단일 파일 작업, 각 1개 CI 검사만). 실제 CI 실패
  기록으로 바꾸면 더 믿을 만하다.
- CI 통과·실패 판정이 "정답 문자열 포함 여부"라는 평가 전용 근사치다(실제 타입체커·린터를
  돌리지 않는다). 뜻은 같지만 다른 표현으로 고친 답은 틀린 것으로 셀 수 있다.
- `MAX_CI_FIXES=1`이라 "두 번째 시도에서 고치는" 능력은 이 평가로는 보지 못한다(production
  자체가 한 번만 시도하므로 평가도 그 정책을 그대로 따른다).
- 이 세션(클라우드 샌드박스)에는 키가 없어 가짜 LLM으로 세 경로(정답을 정확히 고치는 답·
  고쳐도 기준을 못 채우는 답·아예 고치지 않아 커밋조차 안 되는 답)를 검증했다. 실제 비교
  숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.

## 레포 고르기 정확도 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 개발 4순위)

"정답 레포를 고른 비율" 평가. `repo_routing.py`(PR을 어느 연결에 올릴지 고르는 production
모듈 — 규칙 점수가 기준선이고 LLM이 있으면 LLM 판단을 쓰며, LLM이 후보 밖 id를 내거나
오류면 규칙으로 돌아간다)를 그대로 돌린다. 연결 2개 이상 + 정답 표시 평가 데이터가 없어
새로 만들었다.

- `repo_routing_cases.json`: 합성 케이스 8건. 각 작업은 제목·문단·바뀐 경로·요청 글·영역과
  2~4개의 후보 연결(연결 id·레포 이름·설명·담당 경로)을 가지고, 정답 연결 id를 표시해 뒀다.
  그중 2건(`r03`·`r08`)은 **규칙 점수만으로는 실제로 틀리게** 설계했다 — 바뀐 경로가 어느
  후보의 담당 경로와도 안 겹치고, 낱말 겹침은 엉뚱한 후보 쪽이 더 많다(예: 정답은
  `recurring-payments`인데 설명에 "회원 안내 문자 발송"이 들어간 `billing-core`가 낱말
  점수로 앞섬). 나머지 6건은 규칙으로도 쉽게 맞는 사례라, LLM이 실제로 도움이 되는지와
  방해가 안 되는지를 같이 본다
- `repo_routing_eval.py`: production 함수 `score_rules`(규칙만)와 `decide`(규칙 + LLM, 둘 다
  그대로)를 같은 케이스에 돌려 나란히 비교한다. 새로 만든 건 합성 케이스뿐, 판정 로직은
  전부 production 그대로
  - 실행: `cd backend && uv run python domains/dev/eval/repo_routing_eval.py`
  - 모델은 `DEV_LLM_MODEL` → 없으면 `LLM_MODEL`
  - 지표: `repo_pick_accuracy`(정답 레포를 고른 비율, `decide`의 최종 결과)·
    `rule_only_accuracy`(비고: 규칙 점수만 쓸 때)·`accuracy_delta`(둘의 차이)·
    `llm_used_rate`(LLM 판단이 실제로 채택된 비율, 후보 밖 id·오류면 규칙으로 돌아가므로
    낮을수록 LLM이 신뢰를 못 받았다는 뜻)
- 모델 비교: `model_compare_repo_routing.py <라벨>` →
  `results/model_compare/repo_routing_<라벨>.json`,
  `model_compare_repo_routing_report.py <라벨1> <라벨2> ...`로 비교표

```bash
cd backend
DEV_LLM_MODEL=claude-haiku-5-5 uv run python domains/dev/eval/model_compare_repo_routing.py haiku
DEV_LLM_MODEL=claude-sonnet-5-5 uv run python domains/dev/eval/model_compare_repo_routing.py sonnet
uv run python domains/dev/eval/model_compare_repo_routing_report.py haiku sonnet
```

**한계**
- 골든셋이 합성·소규모다(8건, 연결 2~4개). 실제 연결·PR 기록으로 바꾸면 더 믿을 만하다.
- 정답은 이 평가를 만들며 사람이 미리 적어 둔 것이다(production 승인 기록이 아니다).
- 토큰(파일 내용)은 보내지 않는다는 production 설계 그대로라, 레포 이름·설명·경로·작업 글만
  보고 고른다 — 실제 코드를 읽어야만 구분되는 경우는 이 평가로 보지 못한다.
- 이 세션(클라우드 샌드박스)에는 키가 없어 가짜 LLM으로 세 경로(정답을 정확히 고르는 답·
  후보 밖 id를 내는 답·항상 오류인 답)를 검증했다 — 뒤 두 경로 모두 규칙으로 정확히 돌아감을
  확인했다. 실제 비교 숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.

## 대화 답 정확도 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 개발 5순위)

"바꿀 파일·완료 기준, CI 원인 파일·줄 맞힌 비율"(같은 질문 묶음) 평가. production 함수
`dev_chat.turn()`(한 번 보내고 답까지 기다리는 경로 — `_reply`·`_ask_llm`·`pr_ci`·
`SYSTEM_PROMPT`는 그대로)을 돌린다. 평가 데이터가 없어 새로 만들었다.

- `reply_accuracy_cases.json`: 합성 케이스 8건, 두 갈래
  - `files_done`(4건): 개발팀원이 할 일을 한 번에 또렷이 말한 메시지. 목 형상관리
    (MockVcsAdapter)에 정답 파일과 미끼 파일을 같이 넣어 대화의 파일 목록에 보이게 하고,
    `dev_chat`이 세운 계획의 `plan.paths`(바꿀 파일)·`plan.done`(완료 기준)이 정답과
    맞는지 본다. 1~2개 파일을 함께 고쳐야 하는 사례(`d02`·`d04`)도 넣었다.
  - `ci_cause`(4건): `adapter.seed()`로 PR 번호를 만들고 `adapter.set_logs()`로 실제
    타입체크·린트·테스트 실패 형식의 로그를 넣은 뒤, `pr={"number": n}`으로 그 PR을
    가리켜 "CI 왜 실패했어?"를 묻는다. 답 글에 정답 파일 이름·줄 번호가 둘 다 있는지 본다.
    한 로그에 서로 다른 파일의 오류가 두 개 있는 사례(`c04`)도 넣어 실제 원인을 짚는지 본다.
- `reply_accuracy_eval.py`: 작업당 서비스를 새로 띄우고 `dev_chat.turn()`을 한 번 부른다.
  새로 만든 건 합성 케이스와 채점 함수뿐, 대화 흐름·프롬프트·CI 데이터 조립은 전부
  production 그대로.
  - 실행: `cd backend && uv run python domains/dev/eval/reply_accuracy_eval.py`
  - 모델은 `DEV_LLM_MODEL` → 없으면 `LLM_MODEL`
  - 지표: `files_done_accuracy`(바꿀 파일·완료 기준 둘 다 맞힘, `paths_accuracy`·
    `done_accuracy`는 따로)·`ci_cause_accuracy`(원인 파일·줄 둘 다 맞힘, `ci_file_accuracy`·
    `ci_line_accuracy`는 따로)
- 모델 비교: `model_compare_reply_accuracy.py <라벨>` →
  `results/model_compare/reply_accuracy_<라벨>.json`,
  `model_compare_reply_accuracy_report.py <라벨1> <라벨2> ...`로 비교표

```bash
cd backend
DEV_LLM_MODEL=claude-haiku-5-5 uv run python domains/dev/eval/model_compare_reply_accuracy.py haiku
DEV_LLM_MODEL=claude-sonnet-5-5 uv run python domains/dev/eval/model_compare_reply_accuracy.py sonnet
uv run python domains/dev/eval/model_compare_reply_accuracy_report.py haiku sonnet
```

**한계**
- 골든셋이 합성·소규모다(8건, files_done 4 · ci_cause 4). 실제 대화 기록으로 바꾸면 더
  믿을 만하다.
- 정답(바꿀 파일·완료 기준 낱말, CI 원인 파일·줄)은 이 평가를 만들며 사람이 미리 적어 둔
  것이다.
- 채점은 평가 전용 근사치다: `plan.paths`에 정답 경로가 정확한 문자열로 있는지, `plan.done`
  글에 정답 낱말이 부분 문자열로 있는지, 답 글에 정답 파일 이름·줄 번호가 부분 문자열로
  있는지만 본다 — 완료 기준을 다른 말로 똑같이 설명했거나 파일·줄을 맞혔지만 원인 설명
  자체가 틀렸을 때를 가려내지 못한다.
- `files_done`은 "진행해" 이전, 첫 계획 턴만 본다 — 되묻기(`action: ask`)가 와서 계획이
  아직 안 섰으면 `has_plan: false`로 틀린 것으로 센다 (실제로는 되묻는 것이 맞는 사례일
  수도 있다).
- 이 세션(클라우드 샌드박스)에는 키가 없어 가짜 LLM 두 개(정답을 정확히 답하는 것·항상
  모르겠다고 답하는 것)로 검증했다 — 8/8과 0/8로 채점 함수가 맞게 가르는 것을 확인했다.
  실제 비교 숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.
