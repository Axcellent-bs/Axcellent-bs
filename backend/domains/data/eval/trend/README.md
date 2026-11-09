# trend_scan 평가 케이스 (검색어 급상승)

data 에이전트 `trend_scan`(아직 없음)의 정답지와 채점기다. 계약은 `shared/contracts` `TrendScanInput`·`TrendScanOutput`, 데이터는 네이버 검색어 트렌드 커넥터(`connectors/naver_datalab.py`)가 주는 모양 그대로다.

**정의: trend_scan은 최근 56일 대비 급상승을 본다. 계절 반복 구분은 범위 밖이다.** (판정 구간 = 요청의 마지막 7일, 기준선 = 그 앞 56일)

```bash
cd backend
uv run python domains/data/eval/trend/make_cases.py            # cases/ 다시 만들기
uv run python domains/data/eval/trend/make_cases.py --holdout  # holdout/ 다시 만들기
uv run pytest -q domains/data/tests/test_trend_eval_cases.py   # 약속 검사
uv run python domains/data/eval/trend/score.py --judge <모듈>:<함수>   # 채점
```

## 폴더

| 폴더·파일 | 내용 |
| --- | --- |
| `series/` | 실제 네이버 응답에서 **날짜와 값만** 남긴 시계열 (2025-08-01~2026-11-01, 하루 단위). 원본 응답은 커밋하지 않는다(API 약관상 재배포 문제) |
| `collect_series.py` | `series/`를 받는 스크립트. **사람이 한 번만** 돌린다(네이버 호출 22번, 2026-11-02) |
| `make_cases.py` | `series/`와 seed만 읽어 `cases/`·`holdout/`을 만든다. 네이버를 부르지 않는다 |
| `cases/` | 규칙을 고치며 보는 케이스 26개 |
| `holdout/` | 처음 보는 케이스 24개 (결과 숫자만 본다) |
| `score.py` | 채점기. 판정 함수를 인자로 받는다(아래 "채점") |

평가와 케이스 생성은 저장된 값만 쓴다. 네이버를 다시 부르는 곳은 `collect_series.py` 하나뿐이고, `make_cases.py`는 네트워크 코드를 import하지 않는다(테스트가 본다).

## 케이스 하나

- 케이스 하나 = 네이버 요청 하나 = **63일 (기준선 56일 + 판정 구간 7일)**, 하루 단위. 판정은 판정 구간(`window`)에 급상승이 있는지다.
- `series`: 커넥터가 돌려줄 값(그룹 → `[날짜, 상대값]`). 실제 응답처럼 **요청 안 최댓값 = 100**이고 소수 다섯째 자리다. 빠진 날은 빠진 채로 둔다(0으로 채우지 않음).
- `expected`: 그룹마다 `status`(`rising`·`normal`·`unknown`). `rising`이면 `multiplier`(넣은 배수)와 `rising_dates`(넣은 날짜), `unknown`이면 `unknown_reason`.
- `injection`: 넣은 조건(시작일·기간·배수·모양). `source`: 어느 저장 시계열의 어느 구간인지.

## 정답을 정하는 법 (순환 논리 금지)

- **급상승(주력)은 합성 주입이다.** 실제 시계열의 정해진 날짜·기간에 정해진 배수를 곱해 넣고, **정답은 넣은 조건에서 계산한다.** 그래프를 보고 정하지 않는다. 검출기가 읽는 데이터로 정답을 정하면 검출기를 검출기로 채점하는 꼴이 된다.
- 넣은 뒤 실제 응답처럼 최댓값을 100으로 다시 맞춘다. 배수는 같은 날 주입 전 값 대비라 다시 맞춰도 정답은 바뀌지 않는다.
- **바탕 구간은 미리 정한 규칙으로만 고른다**: 주입 전 63일이 모두 있고, 최댓값 ≤ 중앙값 × 1.5. 가장 약한 주입 배수(1.5)보다 큰 자연 튐이 없는 구간이라 "아무것도 넣지 않음 = 평범"이 정의로 성립한다. 잔잔한 키워드도 1년 전체로 보면 최댓값이 중앙값의 2배쯤인 날이 있어서, 키워드가 아니라 구간 단위로 고른다.
- 잔잔한 키워드 후보 12개(`series/calm_*`)는 받기 전에 정했다(생활용품·일상어).

## 케이스 종류 (`cases/` 26개, seed `2026110201`)

| 종류 | 수 | 내용 | 정답 |
| --- | --- | --- | --- |
| `injected` | 9 | 배수 1.5·2·3 × 기간 1·3·7일, 판정 구간 안. 2배 1·3일은 구간 끝까지 이어짐(진행 중), 나머지는 끝나는 날을 seed로 | rising (넣은 날짜·배수) |
| `past_spike` | 2 | 3배 3일·2배 5일을 판정 구간 **전**(10·20일 앞)에 넣음 | normal (판정 구간엔 없음) |
| `joint` | 2 | 치약·볼펜·수건을 한 요청으로 받은 실제 응답에 한 그룹만 주입(볼펜 2배 3일, 수건 1.5배 7일). 최댓값 100은 세 그룹 전체 기준 | 넣은 그룹 rising, 나머지 normal |
| `none` | 4 | 아무것도 넣지 않은 바탕 구간 | normal |
| `seasonal` | 3 | 패딩(2025-11-15)·선크림(2026-04-30)·에어컨(2026-06-20). 계절이 오는 때를 **달력으로 미리** 정했다 | normal (정의: 계절 변화는 급상승이 아님). 어려운 케이스 |
| `unknown` | 3 | 캣북스(`data: []`), 고양이 독서대(458일 중 1일만 값), 고양이 사료 10일만(기준선 3일) | unknown (`no_data`·`too_few_points`·`too_short`) |
| `real_event` | 3 | 수능(2025-11-13)·송편/추석(2025-10-06)·떡국/설날(2026-02-17). 판정 구간 끝 = 사건 다음 날 | rising. **보조**: 정답은 외부 출처의 사건 날짜로만 정했다(메모에 링크) |

- 판정 불가 기준: 기준선 시점이 28개 미만이면 `unknown`이 정답이다(`MIN_BASELINE_POINTS`).
- `real_event`는 "그 사건에 검색이 는다"는 가정이 들어간다. 주력 지표는 합성 주입으로 보고, 실제 사례는 참고로 본다.

## 한계

- 바탕 구간은 잔잔한 구간만 골라서, 원래 출렁이는 키워드의 오탐은 재지 않는다.

## 채점 (`score.py`)

판정 함수를 인자로 받아서 skill이 없어도 돈다(테스트는 가짜 판정 함수, `tests/test_trend_eval_scorer.py`). 네이버를 부르지 않고 케이스에 저장된 값만 쓴다.

```python
judge(query: TrendScanInput, response: TrendResponse) -> TrendScanOutput   # async여도 된다
```

- `response`는 커넥터가 돌려주는 모양(`connectors.naver_datalab.TrendResponse`) 그대로라 skill을 그대로 끼울 수 있다.
- 그룹 단위로 센다. 판정 함수가 오류를 내거나 그룹을 빠뜨리면 그 그룹은 틀린 것(`missing`)이다.

| 지표 | 대상 | 계산 |
| --- | --- | --- |
| `trend.detection_rate` | 정답 rising 그룹 (`injected`, `joint`의 넣은 그룹) | rising으로 답한 비율 (높을수록 좋음) |
| `trend.false_positive_rate` | 정답 normal 그룹 (`none`, `past_spike`, `joint`의 안 넣은 그룹) | rising으로 답한 비율 (낮을수록 좋음) |
| `trend.unknown_handled_rate` | 정답 unknown 그룹 | unknown으로 답한 비율 |
| `trend.group_isolation` | `joint` 케이스 | 넣은 그룹만 rising이고 나머지는 rising이 아닌 케이스 비율 |
| `trend.seasonal_false_positive_rate` | `seasonal` 3개 (**본 지표에서 뺌**) | rising으로 답한 비율. 정의상 범위 밖이라 참고로만 본다 |
| `trend.real_detection_rate` | `real_event` 3개 (**본 지표에서 뺌**) | rising으로 답한 비율. 외부 날짜 기반 보조 지표 |
| `holdout.trend.*` | `holdout/` | 위 본 지표 4개를 holdout으로 따로. holdout에는 주입·평범만 있어 `unknown_handled_rate`·`group_isolation`은 None |

- 화면에는 `cases/`의 틀린 그룹 목록과 holdout의 **틀린 개수만** 나온다(holdout 케이스 이름·내용은 보여 주지 않는다).
- 기존 결제 평가(`eval/run.py`·`baseline.json`)에는 넣지 않는다. trend 기준값(baseline)은 skill PR에서 처음 만든다.
- 배수(`ratio`)가 맞는지는 아직 채점하지 않는다. 상태만 본다.

## holdout (처음 보는 케이스)

- seed **`2026110202`**, 24개 (급상승 주입 16 · 아무것도 넣지 않음 8). seed는 만든 날 `YYYYMMDD` + 그날 몇 번째(2자리)다.
- 무작위로 고르는 것: 잔잔한 키워드·바탕 구간(규칙을 지키는 구간 중), 배수(1.5·2·2.5·3), 기간(1~7일), 판정 구간 안에서 끝나는 날. `cases/`가 쓴 키워드의 구간과 겹치지 않는다.
- **holdout 케이스 파일 내용을 열지 않는다.** 판정 코드를 고칠 때 holdout을 보고 맞추지 않고, 결과 숫자만 본다(`eval/README.md` holdout 규칙과 같다). 테스트는 다시 만든 결과와 같은지·개수만 기계로 본다.
- 내용을 열어 분석했으면 그 seed는 더 이상 처음 보는 케이스가 아니다. 새 seed로 다시 만들고 아래 표에 이유를 적는다.

| seed | 만든 날 | 이유 |
| --- | --- | --- |
| `2026110202` | 2026-11-02 | 처음 만듦 |
