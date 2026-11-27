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
