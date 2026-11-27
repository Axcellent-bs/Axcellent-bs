# 안전(safety) AI 평가

## 비슷한 과거 결정 띄우기: 글자 2-그램 검색 vs 임베딩 비교 (태윤 제안, #소통 2026-11-09 15:56, 4순위, 새 기능)

C 등급 보류 건(`ax_safety.store.Review`)을 담당자가 대시보드에서 승인·거절할 때, 비슷한 과거
결정을 같이 보여주면 더 빠르고 일관되게 판단할 수 있다는 아이디어다. **지금 코드에는 이
기능이 없다.** 태윤님 본인 메모: "측정까지만 부탁드리고, 적용 PR은 결과 보고 저랑 같이
정해요" — 그래서 이 PR은 production 코드를 바꾸지 않는다. "검색 방식을 어떻게 고를지"를
재는 측정 하네스만 만든다.

비교 대상:
- **글자 2-그램 기준선**(`similar_decision_bigram_score.py`, 표준 라이브러리만): 과거 결정
  글과 지금 요청 글을 띄어쓰기 없앤 글자 2-그램 집합으로 바꿔 자카드 유사도로 가장 비슷한
  것을 고른다. 한국어는 조사·어미가 붙어 낱말 단위 겹침이 약하므로, 낱말이 아니라 글자
  2-그램을 썼다. "검색만 할 때 임베딩 없이 할 수 있는 가장 간단한 방식"에 해당한다.
- **임베딩 유사도**(`collect_similar_decision_embed.py`/`score_similar_decision_embed.py`):
  item 1~3과 같은 수집·채점 분리 방식. 분류가 아니라 **검색**(가장 비슷한 것을 보여주는 일)
  이라 임계값을 고르지 않는다 — top-1(1위가 정답과 같은가)·top-3(정답이 상위 3개 안에 있는가)
  정확도만 잰다.

- `similar_decision_cases.json`: 과거 결정 18건(`corpus`, 환불·쿠폰·공지·인사·데이터 반출·
  정산 보류·배포 롤백 6개 갈래)과 새로 들어온 요청 글 12건(`queries`, dev 8 · holdout 4).
  과거 결정과 거의 같은 낱말을 쓴 query는 없다 — 전부 일부러 다르게 풀어 썼다(예:
  "제품 하자"→"흠집", "급여 정정"→"월급 계산이 잘못되어"). 글자 2-그램 기준선의 dev
  top-1 정확도는 0.75(8건 중 2건 틀림: `q01`·`q03`)로, 비교에 실제 여지가 있다.
- `similar_decision_bigram_score.py`: 기준선. 새 production 코드를 바꾸지 않는다(애초에
  바꿀 production 코드가 없다 — 이 기능 자체가 새 제안이다).
  - 실행: `cd backend && uv run python shared/safety/eval/similar_decision_bigram_score.py`
- `collect_similar_decision_embed.py`/`score_similar_decision_embed.py`:
  - 실행: `EMBED_MODEL=<모델> uv run python shared/safety/eval/collect_similar_decision_embed.py <라벨>`
  - 비교: `uv run python shared/safety/eval/score_similar_decision_embed.py <라벨1> <라벨2> ...`

```bash
cd backend
uv run python shared/safety/eval/similar_decision_bigram_score.py   # 기준선만 (키 필요 없음)

EMBED_MODEL=gemini-embedding-001 \
  uv run python shared/safety/eval/collect_similar_decision_embed.py gemini-embedding-001
uv run python shared/safety/eval/score_similar_decision_embed.py gemini-embedding-001
```

**한계**
- **아직 코드에 없는 기능이라 production 비교 대상이 없다.** 기준선(글자 2-그램)도 이 평가를
  위해 새로 만든 것이지, 지금 쓰이는 로직이 아니다. 적용 여부·화면 설계는 이 결과를 보고
  태윤님과 따로 정할 일이다(태윤님 본인 메모).
- 과거 결정 18건·요청 글 12건 모두 합성이다. 실제 C 등급 보류 기록(`Review.report`)으로
  바꾸면 더 믿을 만하다.
- top-1·top-3 정확도만 본다. 실제 화면에서는 "비슷한 과거 결정 몇 건을 보여줄지",
  "얼마나 비슷해야 보여줄지(최소 유사도)" 같은 설계도 필요하지만, 이 평가는 "가장 비슷한
  것을 정확히 찾는가"만 잰다.
- 비용은 이 표에 아직 없다(item 1~3과 같은 한계).
- 이 세션에는 임베딩 키가 없어 배선 확인(키 없이 멈춤)과 가짜 유사도 값(정답을 정확히
  가리키는 값·변별력 없는 값)으로 채점 함수만 검증했다. 실제 비교 숫자는 키가 있는 로컬
  PC에서 위 명령으로 내야 한다.
