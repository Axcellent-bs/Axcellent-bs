# agents/data
담당: 재현 · 포트: 8106
## 1. 역할
쇼핑몰 데이터를 **읽어서** 숫자를 계산하고 돌려준다. skill 7개는 모두 읽기 전용(`grade: read`, `services/data/agent-card.json`)이다.

| skill | 무엇 | 읽는 것 |
| --- | --- | --- |
| `anomaly_detection` | 업무 지표(결제 실패율·주문 실패 건수·주문 수·문의량)를 5분 단위로 지난 7일 같은 시간대와 비교해 지표별 이상 여부·배수·심각도 | shop-api `GET /api/shop/metric-series`, `GET /api/shop/failed-orders`(장애 쪽 실패 코드). 이상일 때 근거로 marketing `list_active_notices`(A2A) |
| `impact_analysis` | 장애 구간의 실패 주문 수·영향 고객 수·고객 ID·피해 금액(정수 원)·카테고리 분포, 평소보다 더 실패한 초과분. 영향 숫자의 유일한 출처 | shop-api `GET /api/shop/failed-orders` (구간 + 지난 7일 같은 시간대) |
| `fraud_score` | 판매자 사기 위험 점수(0~100)·등급·규칙 근거·유사 사기 카드 | shop-api `GET /api/shop/sellers/{id}/profile`·`/prices`, hr `find_similar_fraud_cards`(A2A) |
| `kpi_summary` | 사건별 MTTR, 자동 처리율, 등급별 처리·차단 건수, 이관 건수, 1차·2차 유사 사건 처리 시간 비교 | 공통 로그 저장소(agent-template `common_log`, `AX_COMMON_LOG_DB`) |
| `root_cause_hypotheses` | 이상 구간 앞 30분~구간 끝의 사실만 근거로 원인 후보(설명·근거 사실 id·확신도) | planning `find_related_prs`, marketing `list_active_notices`, support `detect_inquiry_surge`, memory `card_search` (모두 A2A, `clients/fact_sources.py`) |
| `accuracy_report` | 정확도 평가 결과 | `eval/results/latest.json`(없으면 `eval/baseline.json`). 다시 계산하지 않는다 |
| `activity_report` | 부서별 AI 업무 리포트: 부서별 요청·결과·사람에게 넘긴 일·품질·사건 참여, 부서 간 협업, 회사 숫자, 직전 기간 비교 | 공통 로그 저장소, finance `closing_report`(A2A), shop-api `GET /api/shop/metric-series` |

- 숫자는 코드가 계산한다(`metrics/`, `impact.py`, `detect.py`). LLM은 계산이 끝난 숫자로 설명 문장만 쓴다(`explain.py`)
- 주기 감시(`monitor.py`)는 `anomaly_detection`과 같은 판정 함수로 최근 구간을 보고 `metric.anomaly` 이벤트를 낸다. 사건은 열지 않는다(`context_id` 없음)
## 2. 실행 방법
`docker compose -f infra/compose.yml up data` / 목 모드: `DATA_MOCK=1`

### 목 모드 데모 시각 정렬
- 시드는 고정 날짜다: 실패 주문 2026-10-21 05:00~05:30Z (`seed.py`), 지표 10/14~10/22 (`seed_metrics.py`).
- 요청 구간이 `seed.DEMO_ALIGN_FROM`(2026-10-23T00:00Z) 이후에 시작하면 목이 시드 장애를 데모 시각으로 옮긴다. 그 전 구간은 고정 날짜 그대로다. 기존 테스트는 이 경로를 쓴다.
- **장애 시각(anchor)은 프로세스 안에서 기억하고, impact_analysis만 정한다** (`demo_anchor.py`).
  - impact 요청 구간 시작이 저장된 anchor와 1분 이내면 같은 사건의 재조회다 (재무 손실 리포트 등). anchor를 유지한다.
  - 그 밖에는 구간이 이전 장애와 겹쳐도 새 사건이다. anchor = 요청 구간 시작이고, SCN-A에서는 사건 시각 −15분이다.
  - 지표(anomaly_detection·감시)는 anchor를 읽기만 한다. anchor가 없으면 정상 값이다.
  - 장애 = [anchor, anchor + `DATA_DEMO_INCIDENT_MINUTES`) (기본 6분, 최소 5분). 결제 실패율이 5배가 된다.
  - 안전장치: 마지막 anchor 요청 뒤 `DATA_DEMO_RESET_MINUTES`(기본 60분)가 지나면 anchor를 비운다.
- 실패 주문: 장애 6건을 [1분, 장애 길이) 안으로 비례 압축한다(6분이면 1.5~5.8분). 같은 사건 재조회가 1분 늦게 시작해도 6건이 모두 들어가고, `EXPECTED_*`(6건·4명·95,000원)가 그대로 나온다. 함정 3건은 anchor 기준 −5분·+45분·+90분이다.
- 지표: 7일 기준선 + 위 장애. anchor + 장애 길이 이후 구간은 정상이라 `verify_recovery`의 복구 확인을 통과한다.
- `MockMetricSource()`는 기본(`starts_incident=False`)으로 읽기만 한다. 그래서 감시 루프(`monitor.run_forever`)나 복구 확인이 장애를 만들지 않고, 시연이 만든 장애는 한 번만 감지한다. 지표만으로 시연·테스트할 때는 `starts_incident=True`로 지표 요청도 anchor를 정하게 한다.
- 구매자 ID는 shop-api와 같은 `buy_<4자리>`(`buy_0001`~`buy_0005`)다.

### 데모 순서 (SCN-A)
1. `/demo`에서 장애를 주입한다. 총괄이 impact_analysis를 부르면 anchor가 정해진다.
2. 장애 길이(기본 6분) 안에 롤백한다.
3. 복구를 확인한다. 롤백 뒤 구간의 anomaly_detection은 결제 실패율 정상(`any_anomaly=false`)이다.
4. 연속 리허설은 재시작 없이, 쉬지 않고 다시 주입해도 된다 (e2e `--runs 3`). 회차마다 impact 구간 시작이 달라서 새 anchor가 잡힌다. 1분 안에 다시 주입하면 같은 사건으로 남지만, 장애 주문이 1분 뒤부터 있어 결과는 같다(6건·4명·95,000원). 1분 간격·10분 간격 3회 연속은 테스트(`test_demo_consecutive.py`)로 확인한다.

### 혼합 모드 리허설 (총괄 + 실제 data)
```
# 터미널 1: data 에이전트 (목)
DATA_MOCK=1 AGENT_TEMPLATE_MOCK=1 AGENT_NAME=data uv run --package ax-agent-data uvicorn data_agent.main:app --port 8106
# 터미널 2: 총괄, data만 실제
ORCHESTRATOR_REAL_DEPTS=data uv run --package ax-orchestrator python -m ax_orchestrator
```
`/demo`에서 장애를 주입하고 사건의 `impact` 단계를 연다(또는 `GET /api/incidents/<context_id>`의 `steps[impact].output`). 6건·4명·95,000원·`buy_0001~0004`가 나오면 정상이다.
### 실제 모드 (shop-api, #59)
- `DATA_MOCK`이 1이 아니면 shop-api 읽기 전용 API를 부른다 (`clients/shop_client.py`). 주소는 `AX_SHOP_API_URL`(없으면 `http://shop-api:8010`), 토큰은 `Authorization: Bearer <DATA_TOKEN>`.
  - impact_analysis: `GET /api/shop/failed-orders` (from·to·category·limit)
  - anomaly_detection: `GET /api/shop/metric-series`. 기준선 7일 + 구간을 하루씩 나눠 읽는다(shop-api 한도 2016점). shop-api가 계산하지 않는 `inquiry_count`는 빈 시계열(판정 불가)이다.
    - 결제 실패율을 볼 때 `GET /api/shop/failed-orders`도 읽고, 장애 쪽 실패 코드(영향 분석과 같은 분류)를 5분마다 센다. 구간과 지난 7일 같은 시각 ±1시간(하루씩 따로 읽음)을 읽는다. 구간을 못 읽으면 통계 판정(7일 기준선 비교)만 한다. **확률 기준** (회사·시간대마다 건수 기준을 손으로 맞추지 않으려고):
      - 평소 실패율 λ: 지난 7일 같은 시각 ±1시간 5분 구간(주문 자료가 있는 구간, 0건 기록 포함)의 장애 쪽 실패 평균. `DATA_FAULT_LAMBDA_FLOOR`(기본 0.05)보다 작으면 그 값으로 올린다
      - 5분 구간마다 p = P(X ≥ 관측 건수 | λ) (포아송 꼬리확률). p < `DATA_FAULT_P_ANOMALY`(기본 0.01)이면 기준선과 상관없이 이상
        - 예: 평소 5분에 1건씩 나는 시간대면 2건은 p=0.26으로 정상. 평소 0건인 새벽이면(λ=하한 0.05) 2건은 p=0.0012로 이상
      - 이어진 두 구간이 모두 p < `DATA_FAULT_P_LOW`(기본 0.1)이면 두 p를 곱해 `DATA_FAULT_P_ANOMALY`와 다시 비교한다 (새벽 1건 + 1건 = 0.049 × 0.049 = 0.0024 → 이상)
        - 이어짐은 감시 구간 안에서만 본다. 앞 구간 자료는 읽지 않으므로 구간 첫 5분은 앞과 이어졌다고 보지 않는다
        - 사이에 주문 시도가 0건으로 기록된 5분 하나는 건너뛴다(새벽처럼 실패할 기회가 없던 5분). 자료가 없는 5분(조회 실패 등)은 연속을 끊는다
      - 이상이 아닌데 p < `DATA_FAULT_P_LOW`이면 통계 판정(normal, 표본 부족이면 unknown)을 그대로 두고 severity `low`와 "드물지만 이상 아님"을 남긴다. 사건은 만들지 않는다
      - 지난 7일 자료가 없어 λ를 정할 수 없으면 이 기준은 판정하지 않는다. 통계 판정(대개 unknown)을 그대로 두고, 장애 쪽 실패가 있으면 severity `low`를 남긴다
      - 사용자 쪽 실패(카드 거절 등)·처음 보는 코드는 세지 않는다
      - 기본값 0.05·0.01·0.1은 기존 평가 케이스 27개로 정했다(holdout은 보지 않음). 근거는 브랜치 `feat/data-fault-poisson-threshold` PR 본문의 후보 표
    - 예전 고정 기준 환경변수 `DATA_ANOMALY_FAULT_FAILURES`(5분 2건)·`DATA_ANOMALY_FAULT_CONSECUTIVE`(2구간 연속)는 읽기만 하고 판정에 쓰지 않는다
    - 회사별 설정: `DATA_ANOMALY_SETTINGS="tenant=min_payments:alpha[:fault_failures[:fault_consecutive]];…"` (확률 기준 3개는 회사 공통)
    - 주기 감시(`monitor.py`)도 같은 판정 함수(`detect.judge_window`)를 쓴다.
    - 지난 7일 실패 주문은 공급자마다 1시간 칸으로 캐시한다(`detect._HistoryCache`, #129). 날마다 필요한 범위를 덮는 칸 중 없는 칸만 읽고, 판정에는 그 범위 안 주문만 쓴다(캐시 없을 때와 같은 결과). 감시 1회 조회: 첫 감시 8번, 같은 공급자의 다음 감시는 구간 1번(시간 칸이 바뀔 때만 날마다 1번씩 더). 한도(5000건)만큼 받은 칸은 잘렸을 수 있어 캐시하지 않는다
  - 평가 규칙: `eval/README.md`
- shop-api 응답 없음·401·403·5xx·형식 오류면 숫자를 0으로 내지 않고 `failed`·`unavailable`과 "확인할 수 없음" 근거로 답한다.

### fraud_score (판매자 사기 위험 점수)
- **가중치: 재무 확정 (#99, 2026-10-29). 사업자 중복·유사 사기 카드 자료가 생기면 다시 잰다** (`metrics/fraud.py` `WEIGHTS`). 점수는 판단 재료이고 승인·보류는 정하지 않는다(grade read, 재무·정산이 정한다).
- 재무는 상한 high(70 이상)를 사람 확인으로 보낸다(#99). 사기 확정 아님.
- 입력: `seller_id` (`FraudScoreInput`)
- 사용하는 데이터 (shop-api 읽기 전용, 기존 `sellers`·`products` 테이블만)
  - `GET /api/shop/sellers/{seller_id}/profile` (`ShopSellerProfile`): `joined_at`(=`sellers.created_at`), `listings_last_24h`(최근 24시간에 만든 상품 수), `product_count`(판매 중 상품 수)
  - `GET /api/shop/sellers/{seller_id}/prices` (`ShopSellerPricesResult`): 판매 중 상품의 가격과 같은 카테고리 판매 중 상품 가격의 중앙값
- 규칙 (계산은 `metrics/fraud.score_seller`, LLM 없음)

| 규칙 | 가중치(재무 확정 #99) | 사실 | 지금 |
| --- | --- | --- | --- |
| NEW_ACCOUNT | 15 | 가입 7일 미만 | 확인함 |
| LOW_PRICE | 25 × 비율 | 카테고리 중앙값의 50% 미만(`LOW_PRICE_FACTOR`)인 상품 비율 | 확인함 (판매 중 상품이 없으면 확인할 수 없음) |
| BULK_LISTING | 15 | 24시간 등록 50개 이상 | 확인함 |
| DUP_BUSINESS | 20 | 같은 사업자 해시의 다른 판매자 | **확인할 수 없음**: shop-api `sellers`에 사업자 정보가 없어 `business_hash`가 null이다 (컬럼 추가는 팀 결정 필요) |
| FRAUD_SIMILAR | 25 × 유사도 | 지난 사기 판매자 카드와의 유사도 | hr `find_similar_fraud_cards`(A2A, #107). **dense일 때만 확인함**. 아래 참고 |

- 확인할 수 없는 규칙은 0점으로 셈하지 않는다. 점수에서 빼고 `unknown_rules`, `reasons[].note`, summary에 "확인할 수 없음: <규칙>"으로 남긴다. summary에는 "확인한 규칙 N개 기준 점수, 확인한 규칙으로 낼 수 있는 최대 K점"(K = 100 − 확인 못 한 규칙 가중치)을 함께 적는다.
  - 지금은 사업자 중복·유사 카드가 늘 빠져서 K가 55점이다. `score`로는 high(70점 이상)가 나오지 않는다.
- FRAUD_SIMILAR: hr `find_similar_fraud_cards`에 판매자 사실(가입 일수·저가 비율·24시간 등록·사업자 중복 여부, 개인정보 없음)을 보낸다 (`clients/hr_cards.py`, 5초 제한)
  - `method=dense`(임베딩 코사인): `max_similarity`를 규칙 값으로 쓴다. 카드 id는 `similar_card_ids`
  - `method=dice`(글자 2-gram 대체): 척도가 달라 점수에 쓰지 않는다. "확인할 수 없음: FRAUD_SIMILAR (임베딩 없음, dice 대체)". 카드 id는 참고로 `similar_card_ids`에 넣는다
  - hr 꺼짐(서비스 꺼짐 포함)·무응답·시간 초과·형식 오류: 확인할 수 없음, 이유를 남긴다
  - 확인할 수 없으면 다른 규칙처럼 `complete`·`max_possible_score`에 반영된다
  - hr이 dense를 쓰려면 hr 쪽 임베딩 설정(`MEMORY_*` 또는 `LLM_*`와 `EMBED_MODEL`)이 있어야 한다. 없으면 hr은 dice로 답한다
- 불완전 표시와 보수적 상한 (`FraudScoreOutput`)
  - `complete`: `unknown_rules`가 비어 있으면 true
  - `max_possible_score`: `score` + 확인 못 한 규칙 가중치 합 (100 상한). 빠진 규칙을 모두 위험(만점)으로 본 값이다. `complete`면 `score`와 같다
  - `max_possible_level`: `max_possible_score`의 등급 (기준은 `metrics/fraud.py` `MID_LEVEL_MIN`·`HIGH_LEVEL_MIN`)
  - summary에 "불완전: 확인 못 한 규칙을 모두 위험으로 보면 최대 M점(등급)"을 적는다
- **소비자 안내: `complete=false`이면 `score`만으로 승인하지 말 것. `max_possible_score`를 쓰거나 사람 확인으로 넘길 것.**
  - 재무 판매자 심사(`finance_agent.sources.fraud_score_from`)는 이 규칙대로 `max_possible_score`를 쓴다.
  - 세 필드가 없는 예전 응답은 알 수 없음(null)으로 읽힌다. 그때도 `score`만으로 승인하지 않는다.
- shop-api를 못 읽으면 `failed`·`unavailable`(다른 skill과 같음), 없는 판매자(404)는 `failed`·`not_found`. 둘 다 숫자를 내지 않는다.
- 목 판매자 데이터는 없다. `DATA_MOCK=1`이어도 shop-api를 부른다.
- 평가: `eval/README.md`의 판매자 케이스

### kpi_summary (DSH-04, SCN-L)
- 공통 로그 저장소를 **읽기만** 한다. `DATA_MOCK=1`이면 시드(`seed_logs.py`), 아니면 `AX_COMMON_LOG_DB`(compose는 `infra/common-log/data.yml`이 연결). 비어 있으면 `unavailable`.
- 사건 = `ctx_inc_` context (입력 `context_ids`를 주면 그 context). 숫자는 `metrics/kpi.py`가 계산한다.
  - detected_at: 사건의 첫 로그 시각
  - recovered_at: `incident.resolved` 발행 → 없으면 `verify_recovery` 완료(근거에 "복구 여부 미확인(verify 완료 기준)") → 없으면 None
  - resolved_notice_at: 두 번째로 완료된 `publish_notice`. mttr_seconds = recovered_at − detected_at
  - used_memory: `memory_referenced` 로그 유무. auto_handled_rate: `escalated` 없는 사건 비율 (사건 0건이면 null)
  - grade_counts(`execute_decided`), blocked_counts(`rejected` 사유별), escalation_count(`escalated`)
- 값을 정할 수 없으면 None·0으로 두고 `evidence.data_summary`에 "확인할 수 없음" 사유를 남긴다. 지금은 `execute_decided`·`rejected`·`escalated`를 쓰는 곳이 없어 실제 모드에서 이 셋은 0이다.
- LLM은 1차·2차 MTTR 비교 한 줄만 쓴다(`evidence.data_summary` 첫 줄). 숫자가 어긋나면 고정 문장.
- 목 정답: 1차 MTTR 9분(기억 참조 없음, 이관 1) / 2차 6분(기억 참조) → "2차 사건은 회사 기억을 참조해 1차보다 3분 빨리 복구했습니다." 등급 A3·B2·C1, 차단 3, 이관 1, 자동 처리율 0.5.

### activity_report (부서별 AI 업무 리포트)
- **AI를 거친 일만 센다.** 공통 로그에 남은 일만 세고, 사람이 직접 한 일(로그에 없는 일)은 세지 않는다. "절약한 시간" 같은 추정치는 만들지 않는다(근거 없는 숫자).
- 입력: `period`(day·week·month), `start`(한국 시간 날짜, 비우면 직전 기간: 어제·지난주 월~일·지난달), `services`(서비스 id, 비우면 전체). 직전 기간은 같은 길이의 바로 앞 기간이다.
- 부서 = `ax_agent_template.services`의 `department`(총괄·고객지원·마케팅·재무·데이터분석·인사·기획개발·인프라). 카드 `owner`는 사람 이름이라(준하 = 총괄+인사 등) 부서마다 `owners`로 함께 보여 준다.
- 비율은 분모가 3 미만이면 `rate: null` + `note: "표본 부족"`이다. 숫자는 `metrics/activity.py`가 센다.

| 칸 | 출처 | 정의 |
| --- | --- | --- |
| `company.gmv`·`commission_revenue` | finance `closing_report`(A2A) | 총거래액·판매 수수료 매출. 재무 마감은 UTC 날짜 기준이라 한국 시간 기간보다 9시간 늦다(`note`). 주간은 하루씩 7번 더한다. 받지 못하면 null. 재무 장부에 주문·상품(`orders`·`orders_partial`·`products`)이 빠졌다고 오면 재무가 0으로 계산하므로 그 날도 null. 한 요청에서 같은 날짜는 한 번만, 동시에 2개까지 부른다(재무가 호출마다 쇼핑몰 주문 전체를 5초 제한으로 다시 읽어서 더 보내면 재무 쪽 조회가 끊긴다) |
| `company.order_count` | shop-api `metric-series` `order_count` | 기간의 5분 버킷 합 |
| `company.payment_success_rate` | shop-api `payment_count`·`payment_fail_rate` | 1 − 실패/시도. 시도 3건 미만이면 null |
| `company.incident_count` | 공통 로그 (`kpi_summary`와 같은 `compute_kpi`) | 기간 안 `ctx_inc_` 사건 수 |
| `requests` | `result_sent` | 부서 서비스가 보낸 응답 1건 = 요청 1건. skill 상위 5개 |
| `outcomes` | `result_sent` + `execute_decided` + safety 사람 결정(`review_*`) | 확인할 수 없음(`unavailable`·`service_disabled`) / 실패 / 거절 / 보류(`input-required`) / 완료면 그 요청의 안전 관문 판정: executed→자동 완료, held→승인이면 사람 확인 후 실행·거절이면 거절·결정 없으면 보류, verifying(B 교차검증)→확인할 수 없음 |
| `handoffs` | `task_status` `handed_to_human`, dashboard-api `handoff_done`·`handoff_rejected` | 받을 서비스(`to_service`) 부서로 센다. 처리 시간은 같은 task_id의 넘김 기록이 기간 안에 있을 때만 |
| `quality.fallback` | `result_sent` payload `fallback`·`fallback_reason` | 완료 응답 중 템플릿 대체 비율(분모에 LLM을 안 쓰는 skill도 있다), 이유별 건수 |
| `quality.avg_response_ms` | `result_sent` payload `duration_ms`, 없으면 `request_received` ↔ `result_sent` | agent-template이 받은 직후부터 결과까지 잰 처리 시간(`duration_ms`)을 쓴다. 없는 예전 로그는 같은 (source, context, skill, requester)의 가장 먼저 받은 요청과 짝지어 시각 차이를 쓰고, 받음이 기간 앞이면 뺀다. 받음·보냄 짝은 payload `request_id`가 있으면 그것으로 정확히 맞춘다 |
| `incidents` | 부서 `result_sent`의 `ctx_inc_` context | 참여한 사건 수, 맡은 skill, 그 사건들 MTTR 중앙값(분) |
| `collaboration` | `request_received` | 요청한 부서(requester) → 받은 부서 → 횟수. 요청자가 없는 기록(토큰 거부)은 뺀다 |
| `highlights` | 위 숫자 | 직전 대비 변화율이 큰 건수·금액 항목 최대 3개. 직전 값(분모) 3 미만·변화 없음은 뺀다 |
| `summary` | `highlights` → LLM | 3줄. 숫자 검증·템플릿 대체 이유는 아래 절과 같다. 특이사항이 없으면 LLM을 부르지 않고 "특이사항 없음" |
| `unknown_sections` | — | 셀 수 없는 칸과 이유 (서비스 꺼짐, 재무·shop-api 실패, 로그 0건, 조회 한도, B 교차검증 결과 없음) |

- 꺼진 부서는 `enabled: false`이고 AI 응답이 없으니 요청 0건이다. 사람에게 넘긴 일만 센다. 재무가 꺼지면 매출은 0이 아니라 null + `unknown_sections`다.

#### 일간 저장 (`report_store.py`, `DATA_REPORT_DB`)
- 일간 리포트를 하루 한 번 미리 만들어 data 자체 SQLite 파일에 저장한다. run_all.sh는 `.data/data_reports.db`다. 비어 있으면 저장하지 않고 지금처럼 바로 계산한다. 다른 부서 DB·memory는 쓰지 않는다.
- 한국 시간 하루마다 한 줄이다: 날짜, 만든 시각, 코드 버전(`SNAPSHOT_VERSION`), 그날의 "더할 수 있는 값".
  - 비율 대신 분자·분모: 결과별 건수, 완료·템플릿 대체 수, 결제 시도·실패
  - 평균 대신 합계·개수: 응답 시간 `duration_ms`. 처리 시간은 넘김·처리 시각 목록이라 기간을 합친 뒤 task_id로 잇는다
  - 중앙값(MTTR)은 사건별 첫 기록·복구 시각 목록이다. 자정을 넘긴 사건도 합친 뒤 계산하면 바로 계산과 같다
  - 확인할 수 없던 칸은 이유와 함께 그대로 저장한다(0으로 바꾸지 않는다)
- 만들기(`monitor.snapshot_tick`, 데이터 앱이 켜질 때 5분 주기로 시작):
  - 한국 시간 00:30 이후 처음 돌 때 어제를 만든다
  - 돌 때마다 최근 N일 중 빠진 날을 **최근 날짜부터** 채운다. N은 `DATA_REPORT_BACKFILL_DAYS`(기본 14: 주간과 직전 주, 1~92). 데이터 앱이 켜지면 백그라운드로 바로 시작해, 채우는 동안에도 요청은 받는다. 하루씩 차례로 만든다(재무 호출 하루 한 번, 동시 상한 2 안)
  - 이미 있는 날은 다시 만들지 않는다. 확인할 수 없음(재무·shop-api 실패, 조회 한도)이 있던 날은 다음 날 한 번만 다시 만든다
  - 재무 마감은 UTC 날짜라 그 UTC 날짜가 끝나기 전(한국 시간 다음 날 09시 전)에 만든 날은 매출을 확인할 수 없음으로 두고, 다음 날 다시 만든다
  - 지표 감시(`monitor.run_forever`)는 지금처럼 켜지 않는다. 저장 주기만 켠다
- 리포트:
  - 일간: 저장된 날이면 저장값, 오늘이나 저장 안 된 날이면 바로 계산
  - 주간·월간: 저장된 하루치를 더한 뒤 비율·평균·중앙값을 다시 계산한다(평균의 평균이 아니다). 저장 안 된 날만 바로 계산한다
  - 출력: `snapshot_at`(쓴 저장값 중 가장 이른 만든 시각, 저장값이 없으면 계산 시각), `stored_days`·`live_days`, `snapshot_note`("저장된 날 N일 / 바로 계산 M일"). 이번·직전 기간을 합친 날 수다

### 설명 문장(LLM)과 템플릿 대체 이유
- 숫자는 코드가 계산하고 LLM은 문장만 쓴다(`explain.py`). 문장에 넘긴 facts에 없는 숫자가 있거나 LLM이 실패하면 고정 템플릿으로 바꾸고 `fallback=true`다.
- 바꾼 이유를 남긴다 (`AgentResult.fallback_reason`, 영향 분석·이상 탐지·KPI·업무 리포트):

| 이유 | 언제 |
| --- | --- |
| `llm_unavailable` | 모델 설정이 없음 (`DATA_LLM_MODEL`·`DATA_LLM_BASE_URL` 또는 공통 `LLM_MODEL`·`LLM_BASE_URL`) |
| `llm_error` | 호출 실패 (HTTP 오류·시간 초과·연결 실패) |
| `format_error` | 응답이 형식(스키마)에 맞지 않음 (다시 요청해도), 또는 문장이 비어 있음 |
| `number_invalid` | 문장에 계산에 없는 숫자가 있음. 걸린 숫자는 `fallback_numbers` |

- 남는 곳
  - skill 근거 `evidence.data_summary`: "설명 문장을 템플릿으로 대체함: number_invalid (계산에 없는 숫자: 9876543)"
  - 공통 로그 `result_sent` payload: `fallback`·`fallback_reason`·`fallback_numbers` (템플릿이 옮긴다)
  - **LLM 문장 원문은 어디에도 남기지 않는다** (걸린 숫자만 남긴다)
- `GET /health`: `{"data": {"status": "ok", "llm_configured": true|false}}`. `llm_configured`는 설명용 모델과 주소가 설정됐는지다(키 값은 보지 않는다). false면 설명은 늘 템플릿(`llm_unavailable`)이다.
- 평가 결과 파일(`eval/results/latest.json`)에 케이스별 `fallback_reason`·`fallback_numbers`가 남는다. 지표(`llm.fallback_rate` 등) 정의는 그대로다.

## 3. 입출력 계약
contracts/ 참조 (TODO)
## 4. 의존
ax-contracts, ax-agent-template
## 5. 하지 않는 것
코드에서 확인한 것만 적는다.

- **승인·보류를 정하지 않는다.** `fraud_score`는 점수와 근거만 준다. 승인·보류는 재무·정산이 정한다(`handlers.py`, `metrics/fraud.py`)
- **상태를 바꾸지 않는다.** skill은 모두 `grade: read`이고 `execute()`(안전 관문)를 부르지 않는다. 데이터를 쓰는 곳은 없다. 이 패키지가 내보내는 것은 감시의 `metric.anomaly` 이벤트뿐이다(공통 로그 기록은 agent-template이 한다)
- **숫자를 LLM이 만들지 않는다.** 설명 문장에 계산에 없는 숫자가 있으면 버리고 템플릿으로 대체한다(`explain._bad_numbers`, `number_invalid`). 같은 값의 다른 표기는 통과한다: 비율→퍼센트(0.42 → 42%), 자릿수만 줄인 정확한 반올림(2.747 → 2.75, 2.7, 3), 천 단위 쉼표, 원·건·명 단위. "5분"·"5xx"처럼 단위·이름에 붙은 숫자는 facts의 구간 길이·코드에 그대로 있을 때만. facts 값끼리 계산한 합·차·비율은 막는다. 원인 후보도 근거 사실이 없거나 사실에 없는 숫자·시각을 쓰면 버린다(`skills/root_cause.validate`). LLM이 실패하면 템플릿·시간 순서 규칙으로 대체한다
- **원인을 판정하지 않는다.** `anomaly_detection`은 이상 여부만, `root_cause_hypotheses`는 후보만 준다. 확신도 high 후보가 없으면 "확인 필요"다
- **다른 부서 데이터를 직접 읽지 않는다.** 다른 `domains` 코드를 import하지 않고 다른 부서 저장소를 열지 않는다. 다른 부서 정보는 A2A(`call_agent`)로만 묻는다(`clients/fact_sources.py`, `clients/hr_cards.py`, `clients/planning_client.py`). 쇼핑몰 데이터는 shop-api 읽기 전용 API로만 읽는다
- **읽지 못한 값을 0으로 채우지 않는다.** shop-api 응답 없음·401·403·5xx·형식 오류면 `failed`·`unavailable`과 "확인할 수 없음"으로 답한다(`ShopUnavailable`). 꺼진 서비스의 사실은 "확인할 수 없음(서비스 꺼짐)"으로 남긴다. 확인할 수 없는 사기 규칙은 0점이 아니라 점수에서 뺀다
- **외부 입력(`request.untrusted`)을 읽지 않는다**(`handlers.py`)
- **LLM 문장 원문을 남기지 않는다.** 템플릿 대체 이유와 걸린 숫자만 남긴다
