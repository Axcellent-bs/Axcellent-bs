# data 에이전트 정확도 평가

정답지(`cases/`)로 이상 탐지·영향 분석·원인 추정·사기 점수·LLM 설명을 재고, 기준값(`baseline.json`)보다 나빠졌는지 본다.

```bash
cd backend
uv run python domains/data/eval/run.py   # 결과: eval/results/latest.json
```

## 규칙

- **평가 정의 변경은 코드 수정과 별도 PR로 한다.** 평가 정의는 정답지(`cases/`, `make_cases.py`), 채점 방식(`run.py`의 `SIGNALS`·지표 계산), 데이터 생성(`eval_world.py`)이다. 같은 PR에서 둘 다 바꾸면 숫자가 좋아진 것이 코드 덕분인지 정의 덕분인지 가릴 수 없다.
- 기준값(`baseline.json`)은 나빠진 지표가 없을 때만 갱신한다. 나빠졌으면 갱신하지 않고 PR 본문에 원인을 적는다.
- PR 본문에는 전·후 숫자를 적는다. 평균에 묻히지 않게 바뀐 케이스도 함께 적는다.
