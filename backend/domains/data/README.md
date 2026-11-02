# agents/data
담당: 재현 · 포트: 8106
## 1. 역할
쇼핑몰 데이터와 외부 트렌드(네이버 검색어 트렌드·YouTube 스냅샷)를 **읽어서** 숫자를 계산하고 돌려준다. skill 14개는 모두 읽기 전용(`grade: read`, `services/data/agent-card.json`)이다.

| skill | 무엇 | 읽는 것 |
| --- | --- | --- |
| `anomaly_detection` | 업무 지표(결제 실패율·주문 실패 건수·주문 수·문의량)를 5분 단위로 지난 7일 같은 시간대와 비교해 지표별 이상 여부·배수·심각도 | shop-api `GET /api/shop/metric-series`, `GET /api/shop/failed-orders`(장애 쪽 실패 코드). 이상일 때 근거로 marketing `list_active_notices`(A2A) |
| `impact_analysis` | 장애 구간의 실패 주문 수·영향 고객 수·고객 ID·피해 금액(정수 원)·카테고리 분포, 평소보다 더 실패한 초과분. 영향 숫자의 유일한 출처 | shop-api `GET /api/shop/failed-orders` (구간 + 지난 7일 같은 시간대) |
| `fraud_score` | 판매자 사기 위험 점수(0~100)·등급·규칙 근거·유사 사기 카드 | shop-api `GET /api/shop/sellers/{id}/profile`·`/prices`, hr `find_similar_fraud_cards`(A2A) |
| `kpi_summary` | 사건별 MTTR, 자동 처리율, 등급별 처리·차단 건수, 이관 건수, 1차·2차 유사 사건 처리 시간 비교 | 공통 로그 저장소(agent-template `common_log`, `AX_COMMON_LOG_DB`) |
| `root_cause_hypotheses` | 이상 구간 앞 30분~구간 끝의 사실만 근거로 원인 후보(설명·근거 사실 id·확신도) | planning `find_related_prs`, marketing `list_active_notices`, support `detect_inquiry_surge`, memory `card_search` (모두 A2A, `clients/fact_sources.py`) |
| `accuracy_report` | 정확도 평가 결과 | `eval/results/latest.json`(없으면 `eval/baseline.json`). 다시 계산하지 않는다 |
| `activity_report` | 부서별 AI 업무 리포트: 부서별 요청·결과·사람에게 넘긴 일·품질·사건 참여, 부서 간 협업, 회사 숫자, 직전 기간 비교 | 공통 로그 저장소, finance `closing_report`(A2A), shop-api `GET /api/shop/metric-series` |
| `trend_scan` | 키워드 그룹별 검색어 급상승(rising·normal·unknown), 기준선 대비 배수, 근거 숫자(z·기준선·흔들림), 판정 불가 이유. 최근 56일 대비 마지막 7일 | 네이버 검색어 트렌드(`connectors/naver_datalab.py`, 그룹마다 1번). 기본 키워드: 회사 폴더 `trends.toml` |
| `trend_candidates` | YouTube 인기 영상 태그에서 뽑은 후보 키워드(최대 N개, 회사 별칭 표로 같은 대상을 묶음)와 후보마다 YouTube 근거(나온 영상 수·스냅샷 시각·대표 영상 id)·네이버 급상승 판정(trend_scan과 같은 함수). 스냅샷이 없거나 오래되면 unknown + 이유 | YouTube 스냅샷 DB(`youtube_store.latest_popular`, 읽기 전용), 네이버 검색어 트렌드(후보마다 1번). 설정: 회사 폴더 `trends.toml` `[candidates]` |
| `trend_history` | 키워드 그룹별 네이버 월 단위 값(최근 24개월, 설정)과 달마다 "작년 같은 달 대비" 배수. **판정하지 않음**(급상승·계절 판정은 평가가 없어서). 자료 없음·못 읽음은 그 그룹만 unknown | 네이버 검색어 트렌드(`timeUnit=month`, 그룹마다 1번). 기본 키워드: 회사 폴더 `trends.toml`, 개월 수 `[history] months` |
| `youtube_velocity` | YouTube 영상 지금 속도(최근 두 스냅샷 사이 시간당 조회 증가)와 그 전 구간보다 빨라졌는지, 지금 속도 상위 N개(설정)와 태그. **판정하지 않음** | YouTube 스냅샷 DB(`youtube_store.recent_snapshots`, 읽기 전용, 외부 호출 없음). 개수: 회사 폴더 `trends.toml` `[velocity] top` |
| `content_recommend` | 지금 만들 만한 영상 소재(급상승만, 최대 N개): 순위·점수 근거(z·배수·두 소스)·회사 관련성(표시만)·영상 아이디어 1~2문장·형식(숏폼·롱폼)·fallback. 콘텐츠 루프 2단계 | trend_scan(회사 기본 키워드 그룹)·trend_candidates(YouTube 후보)와 같은 네이버 요청(하루 캐시 같이 씀), LLM(아이디어 문장만). 설정: 회사 폴더 `trends.toml` `[recommend]` |
| `answer_planning_question` | 기획이 되묻는 근거 질문의 답(answered·needs_human·unknown). 기획만 부른다 | data가 보낸 요청의 기록(`planning_questions.py`, 프로세스 안). 아직 요청을 보내는 코드가 없어 지금은 늘 unknown. 사람 답은 `human_answer_request`로 planning `receive_planning_answer`에 보낸다 |
| `campaign_effect` | 캠페인 기간 주문 방향(늘었음·같음·줄었음)·증분(실제 − 지난 7일 같은 시간대 평균), 캠페인 쿠폰 조회 시점 누적 사용률. 매출은 매출 지표가 생기면 같은 방법(지금은 확인할 수 없음) | shop-api `GET /api/shop/metric-series`(`order_count`), `GET /api/shop/coupons?campaign_id=` |

### skill별 평가와 알려진 한계 (2026-11-02 기준)

평가는 정답지로 숫자를 재는 것이다(`eval/README.md`). "없음"은 단위 테스트만 있고 정확도를 재는 평가셋이 없다는 뜻이다.

| skill | 평가 | 알려진 한계 |
| --- | --- | --- |
| `anomaly_detection` | 있음: `eval/` 기본(`anomaly.*` 탐지율·오탐·지연, holdout) | 지난 7일 같은 시간대와만 비교한다. 비교할 기록이 없으면 판정하지 않고 unknown |
| `impact_analysis` | 있음: `eval/` 기본(`impact.*` 일치율·금액 오차, holdout) | shop-api 실패 주문 기록만 센다(기록에 없는 실패는 모른다) |
| `fraud_score` | 있음: `eval/` 기본(`fraud.*` 정밀도·재현율) | 확인할 수 없는 규칙은 점수에서 빼고 `unknown_rules`로만 알린다. 승인·보류는 정하지 않는다 |
| `kpi_summary` | 없음 | 공통 로그에 남은 일만 센다 |
| `root_cause_hypotheses` | 있음: `eval/` 기본(`root_cause.*` 1순위 적중·근거 없는 후보) | 다른 부서 조회가 실패하면 그 근거 없이 고른다. memory "지난 비슷한 사건" 카드는 아직 쓰는 쪽이 없어 비어 있다(#304) |
| `accuracy_report` | 해당 없음(평가 결과를 보여 줄 뿐) | 다시 계산하지 않는다(`eval/results/latest.json`, 없으면 baseline) |
| `activity_report` | 없음 | AI를 거친 일만 센다. 일간 저장분은 일부 서버만 켜진 때 만들어지면 "확인할 수 없음"으로 남고 다음 날 한 번만 다시 만든다 |
| `trend_scan` | 있음: `eval/trend/`(개발 탐지 0.909·오탐 0, holdout 탐지 0.8125·오탐 0) | 계절 반복을 구분하지 않는다(계절 오탐 0.667). 요일 영향이 큰 하루짜리 작은 급상승은 놓친다(요일 보정은 비교했고 오탐이 생겨 쓰지 않음) |
| `trend_candidates` | 판정은 trend_scan과 같은 함수(위 평가). 후보 뽑기·별칭 묶기는 평가 없음 | 별칭 표에 없는 이름은 묶지 않는다. 별칭이 많을수록 네이버 호출이 는다(이름별 판정). 스냅샷이 쌓이기 전 날짜로 판정하면 YouTube 후보는 unknown |
| `trend_history` | 없음(판정하지 않음) | 그룹마다 상대값이라 그룹끼리 높이를 비교할 수 없다. 계절 반복 판정은 평가 케이스부터 만들어야 한다 |
| `youtube_velocity` | 없음(판정하지 않음) | 평소 속도 기준이 없어 빠른 순서만 보여 준다. 스냅샷 간격이 짧으면 속도가 흔들린다 |
| `content_recommend` | 없음(순위 규칙·숫자 검증만 테스트) | 추천 품질은 나중에 게시 성과로 잰다. 회사와 거리가 먼 소재도 회사 상품에 잇는 문장이 나온다(관련성 low를 같이 본다) |
| `answer_planning_question` | 없음 | 아직 data가 요청을 보내는 코드가 없어 늘 unknown이다 |
| `campaign_effect` | 있음: `eval/campaign/`(기존·여러 날, 개발 방향 1.0, holdout 0.9167·0.8) | 매출 지표가 없어 매출은 unknown. 7일 넘는 캠페인은 unknown. 주문이 아주 적은 가게의 새벽 기록 공백은 못 잡는다 |

- 숫자는 코드가 계산한다(`metrics/`, `impact.py`, `detect.py`). LLM은 계산이 끝난 숫자로 설명 문장만 쓴다(`explain.py`)
- 주기 감시(`monitor.py`)는 `anomaly_detection`과 같은 판정 함수로 최근 구간을 보고 `metric.anomaly` 이벤트를 낸다. 사건은 열지 않는다(`context_id` 없음)
### 새 회사를 붙일 때 필요한 것: 지표 카탈로그 + 커넥터
회사 한 곳을 붙이는 순서(카탈로그·커넥터·조직도를 묶는 `config/tenants/<회사>/tenant.toml`)는 `backend/README.md` "새 회사를 붙일 때"에 있다. 여기는 data 쪽 두 가지다.

회사마다 다른 것은 두 가지다. **무엇을 볼지**는 지표 카탈로그 파일, **어디서 읽을지**는 커넥터다.

#### 1) 지표 카탈로그
data 엔진에서 회사마다 다른 것은 **지표 카탈로그** 파일 하나에 모았다. 형식은 계약 `ax_contracts.schemas.data.MetricCatalog`이다.

- 파일: `src/data_agent/catalogs/<회사>.toml` (캣북스는 `catbooks.toml`). 고르는 환경변수는 `DATA_TENANT_CATALOG`(기본 `catbooks`, `.toml` 경로도 받는다).
- 들어가는 것:
  - `metrics[]`: 감시할 지표. 순서대로 판정한다.
    - `id`·`label`(한국어)·`bad_direction`(`up`|`down`)·`unit`
    - `fault_codes`: 장애 쪽 실패 코드. 있으면 확률 기준도 본다
    - `aggregate`(`sum`|`mean`)·`denominator`(비율 지표의 분모)·`denominator_label`(분모를 부르는 말, 캣북스 "결제")·`scale_with_volume`(거래량만큼 기준 보정)
  - `volume_metric`·`volume_label`: 거래량 지표와 그 이름(빈 구간 판단·기준 보정). 없으면 쓰지 않는다
  - `impact`: `unit_label`(예: 주문·출고), `customer_label`(예: 고객·거래처), `money_field`, `money_unit`. 영향 설명 문장의 단위다
  - `categories`: 카테고리 코드 → 한국어 이름
- 카탈로그에서 읽는 곳(이번에 옮긴 것):
  - 이상 탐지의 지표 목록·나쁜 방향·비율 지표·장애 쪽 코드(`detect`·`metrics/judge`·`impact`)
  - 주기 감시의 지표 목록(`monitor`)
  - 설명 문장의 지표 이름·영향 단위(`explain`·`anomaly_detection`)
  - 리포트의 "회사 주문 수" 이름(`activity_report`)
- 테스트 전용 가짜 회사 `tests/catalogs/coldchain.toml`(냉장 물류: 지표 2개, 단위 "출고", 고객 "거래처")로 판정·설명·리포트 이름이 캣북스 단어 없이 나오는지 본다(`tests/test_metric_catalog.py`).
#### 2) 커넥터 (`src/data_agent/connectors/`)
엔진(이상 탐지·영향 분석·주기 감시·리포트의 주문 수)은 커넥터만 쓴다. 회사 시스템 응답을 표준 형식으로 바꾸는 일은 커넥터 안에서만 한다.

- 고르는 곳: 카탈로그 `connector`(캣북스 `shop-api`). 이름 → 커넥터 표는 `clients/shop_client.py`의 `CONNECTORS`다.
- 구현할 메서드 3개 (`connectors.Connector`, 읽기만 한다. 못 읽으면 `ConnectorUnavailable`이고 0으로 채우지 않는다):

| 메서드 | 돌려줄 것 | 엔진이 쓰는 곳 |
| --- | --- | --- |
| `metric_series(metric_id, from, to, bucket="5m")` | 카탈로그 지표의 5분 [(시각, 값)]. 모르는 지표는 빈 목록 | 이상 탐지·감시·리포트 거래 수 |
| `failure_events(from, to, *, category, limit, tenant_id)` | `FailureEvents`(`FailureEvent`: 시각 `at`, 원인 쪽 코드 `code`, 고객 `customer_id`, 금액 `amount`, 분류 `category`, `event_id`)와 잘림 여부 | 장애 쪽 실패 확률 기준, 영향 분석 |
| `transactions_volume(from, to)` | 5분 거래 시도 수 [(시각, 값)]. 주지 않으면 None | 비율 지표의 분모, 영향 분석의 "평소 실패" |

- 거래량 보정(`scale_with_volume`)은 카탈로그 `volume_metric`(캣북스 주문 수)을 `metric_series`로 읽는다.
- 예시 (테스트 전용 메모리 커넥터, `tests/test_connector.py`):

```python
class MemoryConnector:
    async def metric_series(self, metric_id, window_from, window_to, bucket="5m"):
        return [
            (ts, value) for ts, value in self.series[metric_id] if window_from <= ts < window_to
        ]

    async def failure_events(
        self, window_from, window_to, *, category=None, limit=5000, tenant_id="shop-demo"
    ):
        events = [e for e in self.events if window_from <= e.at <= window_to]
        return FailureEvents(events[:limit], truncated=len(events) > limit)

    async def transactions_volume(self, window_from, window_to):
        return [(ts, n) for ts, n in self.attempts if window_from <= ts < window_to]
```

- 캣북스: `connectors/shop_api.py`의 `ShopApiConnector`다(`/api/shop/metric-series`·`/failed-orders`, 거래 시도 = `payment_count`). 예전 이름 `HttpMetricSource`·`HttpShopClient`는 이 커넥터의 하위 클래스다. 정확도 평가도 이 커넥터로 읽는다(`eval/run.py`). 평가 데이터 생성은 지금처럼 shop-api 코드로 한다(평가는 캣북스 전용).
- 목 모드(`DATA_MOCK`, 테스트 전용)의 시드 공급자는 예전 모양 그대로이고, 엔진이 `connectors.as_connector`로 감싸 읽는다.
- 판매자 정보·가격(사기 점수 전용)은 아직 커넥터에 넣지 않았다(`clients.shop_client.HttpSellerClient`).

#### 외부 트렌드 커넥터: 네이버 검색어 트렌드 (`connectors/naver_datalab.py`)
회사 시스템이 아니라 외부 검색 트렌드를 읽는다. 5분 지표·실패 이벤트가 아니어서 위 `Connector` 인터페이스를 구현하지 않고 `NaverDatalabConnector.search_trend(TrendScanInput) -> TrendResponse` 하나만 있다. 급상승 판정(`TrendScanOutput`)과 skill 연결은 아직 없다.

- 주소: NAVER API HUB `https://naverapihub.apigw.ntruss.com/search-trend/v1/search` (POST). 예전 `openapi.naver.com/v1/datalab/search`가 아니다. `AX_NAVER_DATALAB_URL`로 바꿀 수 있다.
- 키: `NAVER_HUB_CLIENT_ID`·`NAVER_HUB_CLIENT_SECRET`(`.env`)를 헤더 `X-NCP-APIGW-API-KEY-ID`·`X-NCP-APIGW-API-KEY`로 보낸다. 키 값은 로그·오류 문장·`repr`에 넣지 않는다. 키가 없으면 부르지 않고 `UNAUTHORIZED`다.
- 값: 검색 건수가 아니라 **그 요청 안 최댓값 = 100인 상대값**이다. 같은 키워드도 어떤 그룹과 같이 묻느냐에 따라 일정한 배수만큼 달라진다(실제 호출: 고양이 단독 62.0 → 날씨와 함께 0.0907, 모든 날 683.5배). 그래서 기준선과 판정 구간은 한 요청으로 받아 그 안에서만 비교한다.
- 한도(실제 호출로 확인): 그룹 5개, 그룹당 키워드 20개, 가장 작은 단위 하루(`hour`는 400), 하루 늦게 채워진다. `TrendScanInput`이 같은 한도를 검사한다.
- 못 읽으면 `ConnectorUnavailable`: 연결 실패·5xx `UNAVAILABLE`, 400 `BAD_REQUEST`, 401·403 `UNAUTHORIZED`, 429·하루 한도 `RATE_LIMITED`, JSON 아님·형식 다름·요청과 다른 그룹 `BAD_RESPONSE`.
- 0으로 채우지 않는다: 네이버가 자료를 주지 않은 그룹(검색량이 적은 키워드)은 빈 시계열, 빠진 날은 빠진 채로 둔다. 판정 쪽이 "확인할 수 없음"으로 다룬다.
- 호출 한도: 같은 요청은 한국 날짜가 바뀔 때까지 메모리 캐시한다(실패는 캐시하지 않는다). 하루 호출 수가 `NAVER_DATALAB_DAILY_LIMIT`(기본 1000)에 닿으면 부르지 않고 `RATE_LIMITED`다. 캐시는 프로세스 메모리라 서버를 다시 띄우면 비워진다.
- 테스트: `tests/test_naver_datalab.py` (httpx MockTransport 메모리 응답, 실제 API를 부르지 않는다). 계약은 `shared/contracts/tests/test_trend_scan.py`.

- 엔진 문장의 회사 단어도 카탈로그에서 온다: 지표 이름(`label`), 영향 단위(`impact.unit_label`·`customer_label`·`money_unit`), 거래량(`volume_label`), 비율 지표 분모(`denominator_label`, 없으면 "거래"), 사용자 쪽 실패 코드 이름(`user_codes`, 캣북스 카드 거절 등). 테스트 가짜 회사로 이상 탐지·영향 분석·리포트를 돌려 "결제"·"주문"·"판매자"가 나오면 실패한다(`tests/test_catalog_wording.py`).
- 다른 회사 지표는 `anomaly_detection` 출력의 `metric_verdicts`·`unknown_metric_ids`에 담긴다(#221·#227). 캣북스 지표는 예전 `verdicts`에도 들어간다.

#### 캣북스 전용으로 남긴 것
엔진이 아니라 캣북스에 묶인 기능이라 카탈로그로 옮기지 않았다. 다른 회사는 이 경로를 쓰지 않는다.

| 남긴 것 | 위치 | 이유 |
| --- | --- | --- |
| "문의 급증" 사실·문장 | `clients/fact_sources.py`, `skills/root_cause.py` | 고객지원 부서 skill(`detect_inquiry_surge`)의 이름이다 |
| 판매자 사기 점수(판매자 정보·가격, "판매자 … 사기 위험 점수") | `skills/fraud_score.py`, `metrics/fraud.py`, `handlers.py`(`fraud_score`), `clients/shop_client.py`(`HttpSellerClient`) | 판매자 전용 skill이다(마켓운영 담당이 정해지면 연결) |
| shop-api 응답 오류 문장("실패 주문 응답 형식이 계약과 다르다" 등) | `connectors/shop_api.py` | 캣북스 커넥터 안 문장이다. 다른 회사는 자기 커넥터를 쓴다 |
| skill 한국어 이름표("결제 문제 진단" 등) | `labels.py` | 부서 업무 이름이다(화면 `SKILL_KO`와 같은 말) |
| 목 시드(`seed.py`·`seed_metrics.py`·`seed_logs.py`) | | 테스트 전용 |

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
- **현재 호출하는 부서 없음. 마켓운영 담당이 정해지면 연결.** 재무에서 마켓운영(판매자 점검)이 빠져 `allowed_requesters`는 총괄(`orchestrator`)만 남겼다. skill과 평가(판매자 24명)는 그대로다.
- **가중치: 재무 확정 (#99, 2026-10-29). 사업자 중복·유사 사기 카드 자료가 생기면 다시 잰다** (`metrics/fraud.py` `WEIGHTS`). 점수는 판단 재료이고 승인·보류는 정하지 않는다(grade read, 재무·정산이 정한다).
- 상한 high(70 이상)는 사람 확인으로 보낸다는 규칙은 #99 때 재무가 정했다(그때 부르던 쪽). 사기 확정 아님.
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
- 부서 = `ax_agent_template.services`의 `department`(총괄·고객지원·마케팅·재무·데이터분석·인사·기획·개발·인프라). 카드 `owner`는 사람 이름이라(준하 = 총괄+인사 등) 부서마다 `owners`로 함께 보여 준다.
- **센 범위 = 이 회사가 켠(AI를 쓰는) 서비스.** 백엔드 `AX_ENABLED_SERVICES` 기준이다(비어 있으면 전부, `routing.is_enabled`).
  - 화면의 서비스 고르기(`/system/services`, 브라우저 localStorage)와는 아직 연결돼 있지 않다.
  - 켜지 않은 서비스의 부서는 카드·특이사항·협업에 넣지 않는다. 입력 `services`에 켜지 않은 서비스만 있으면 `invalid_input`이다.
  - **AI를 쓰지 않는 부서로 넘긴 일**(`unused_handoffs`): 켠 AI가 켜지 않은 서비스로 보낸 일(사람 할 일함으로 넘어감)을 부서별 건수와 업무(skill 한국어 이름) 상위 3개로만 보여 준다. "도입하면 자동 처리" 같은 예측 문구·숫자는 넣지 않는다.
  - **재무 AI를 쓰지 않으면 `closing_report`를 부르지 않는다.** 꺼진 서비스로 가는 요청은 조직도가 있으면 사람 할 일함에 쌓이기 때문이다(재무 카드의 `closing_report`가 조회로 표시돼 있지 않음). 매출 칸은 "재무 AI를 쓰지 않아 확인할 수 없음"이다. 일간 저장도 같다.
  - 저장된 일간에는 그날 켠 서비스(`enabled_services`)를 같이 저장하고, 넘긴 일도 그날 기준으로 나눠 둔다. 그래서 나중에 서비스를 더 켜도 지난 리포트가 바뀌지 않는다. 리포트의 부서 범위는 저장된 날은 그날 켠 서비스, 바로 계산하는 날은 지금 켠 서비스의 합이다(출력 `enabled_services`).
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
- 한국 시간 하루마다 한 줄이다: 날짜, 만든 시각, 코드 버전(`SNAPSHOT_VERSION`, 지금 2), 그날 켠 서비스, 그날의 "더할 수 있는 값". 버전이 다른 예전 줄은 채우기 범위 안에서 다시 만든다.
  - 비율 대신 분자·분모: 결과별 건수, 완료·템플릿 대체 수, 결제 시도·실패
  - 평균 대신 합계·개수: 응답 시간 `duration_ms`. 처리 시간은 넘김·처리 시각 목록이라 기간을 합친 뒤 task_id로 잇는다
  - 중앙값(MTTR)은 사건별 첫 기록·복구 시각 목록이다. 자정을 넘긴 사건도 합친 뒤 계산하면 바로 계산과 같다
  - 확인할 수 없던 칸은 이유와 함께 그대로 저장한다(0으로 바꾸지 않는다)
- 만들기(`monitor.snapshot_tick`, 데이터 앱이 켜질 때 5분 주기로 시작):
  - 한국 시간 00:30 이후 처음 돌 때 어제를 만든다
  - 돌 때마다 최근 N일 중 빠진 날을 **최근 날짜부터** 채운다. N은 `DATA_REPORT_BACKFILL_DAYS`(기본 20, 1~92). 20일이면 주간 기본(지난주 월~일)과 그 전 주가 요일과 상관없이 들어간다(일요일이면 그 전 주 월요일이 20일 전). 데이터 앱이 켜지면 백그라운드로 바로 시작해, 채우는 동안에도 요청은 받는다. 하루씩 차례로 만든다(재무 호출 하루 한 번, 동시 상한 2 안)
  - 이미 있는 날은 다시 만들지 않는다. 확인할 수 없음(재무·shop-api 실패, 조회 한도)이 있던 날은 다음 날 한 번만 다시 만든다
  - 재무 마감은 UTC 날짜라 그 UTC 날짜가 끝나기 전(한국 시간 다음 날 09시 전)에 만든 날은 매출을 확인할 수 없음으로 두고, 다음 날 다시 만든다
  - 지표 감시(`monitor.run_forever`)는 지금처럼 켜지 않는다. 저장 주기만 켠다
- 리포트:
  - 일간: 저장된 날이면 저장값, 오늘이나 저장 안 된 날이면 바로 계산
  - 주간·월간: 저장된 하루치를 더한 뒤 비율·평균·중앙값을 다시 계산한다(평균의 평균이 아니다). 저장 안 된 날만 바로 계산한다
  - 출력: `snapshot_at`(쓴 저장값 중 가장 이른 만든 시각, 저장값이 없으면 계산 시각), `stored_days`·`live_days`, `snapshot_note`("저장된 날 N일 / 바로 계산 M일"). 이번·직전 기간을 합친 날 수다

### trend_scan (검색어 급상승)

- 정의: **최근 56일 대비 급상승을 본다. 계절 반복 구분은 범위 밖이다.** 판정 구간 = `end_date`(없으면 한국 날짜 어제)까지 7일, 기준선 = 그 앞 56일. 하락은 보지 않는다.
- 기준선과 판정 구간은 **한 응답 안에서만** 비교한다. 네이버 값은 요청마다 배수가 달라 요청 사이 비교는 하지 않는다.
- 키워드 그룹은 **한 요청에 하나씩** 보낸다. 여러 그룹을 한 요청에 넣으면 큰 키워드에 작은 키워드가 눌린다(실제 응답: 고양이 62.0 → 날씨와 함께 0.0907). 그룹마다 네이버 호출 1번이다.
- 판정(`skills/trend_scan.py`, 코드 계산): 로그 강건 z. 기준선 log 값의 중앙값 m, 중앙 절대 편차 MAD로 σ = 1.4826 × max(MAD, 0.05). 판정 구간 안 이어진 1~7일 구간마다 z = (구간 log 평균 − m) ÷ (σ ÷ √일수)를 구해 가장 큰 z가 3.2 이상이면 rising. 배수 = exp(그 구간 log 평균 − m)
- 판정 불가(normal이 아니라 unknown + 이유): 자료 없음, 기준선 56일 중 값이 있는 날 28일 미만(요청이 짧거나 빈 날·0이 많음), 기준선 중앙값 0.01 미만(요청 최댓값 100 기준), 판정 구간에 값 없음
- 일부 그룹만 네이버를 못 읽으면 그 그룹은 unknown("읽지 못함 [코드]"), 모든 그룹을 못 읽으면 숫자 없이 `failed`·`unavailable`
- 기본 키워드 그룹은 코드에 두지 않는다: `config/tenants/<회사>/trends.toml`(`tenant.toml`의 `trends`) → `scripts/tenant_env.py`가 `DATA_TREND_CONFIG`로 넘긴다. 입력에 그룹도 없고 설정도 없으면 `invalid_input`
  - Docker(`services/data/compose.yml`)는 `DATA_TREND_CONFIG`를 `/app/config/tenants/${AX_TENANT:-catbooks}/trends.toml`로 넘긴다. 바꾸려면 셸 환경변수로 준다(`.env` 값은 이 줄에 덮인다)
  - **주의:** `services/*/compose.yml`·`agent-card.json`은 `scripts/split_services.py`가 만드는 파일이다. 이 줄은 손으로 넣어서 스크립트를 다시 돌리면 빠진다. 그 스크립트는 `--help` 같은 옵션을 읽지 않고 **바로 실행돼** 지금 dev에서도 `services/` 파일 66개를 다시 쓴다(줄바꿈 등). 확인하려고 옵션을 붙여 돌리지 말 것
- 설명 문장은 LLM이 쓰고 `explain.py` 숫자 검증을 그대로 거친다(템플릿 `trend`)
- 화면(`/data#trend`)용으로 그룹마다 받은 날짜별 값(`series`, 빠진 날은 빠진 채로), 판정 구간 시작일(`window_from`), 평소 범위 띠(`evidence.band_low`·`band_high` = exp(m ± σ)), 급상승 그룹의 설명 문장(`summary`, 그 그룹 숫자만으로, 템플릿 `trend_group`)도 준다
- 요청 가능: orchestrator, marketing, dashboard-api

#### 기준값 (평가 개발 케이스로 정함)

`eval/trend/cases/` 결과만 보고 정했다. holdout은 고른 뒤 결과 숫자만 봤고, 그 숫자로 다시 맞추지 않았다. 기준값 파일은 `eval/trend/baseline.json`.

| 기준 | 값 | 이유 |
| --- | --- | --- |
| 연속 구간 | 1~7일 | 하루만 보는 것과 본 지표가 같고(탐지 0.909·오탐 0), 평범과 급상승의 z 틈이 더 넓다(평범 최대 2.86 / 잡은 급상승 최소 3.47, 하루만 보면 2.57 / 3.0) |
| z 기준 | 3.2 | 위 틈의 가운데. 3.0은 평범 최대에, 3.5는 잡은 급상승 최소에 가깝다 |
| MAD 하한 | 0.05 | 0.03~0.1에서 개발 케이스 결과가 같아 중간값. 너무 잔잔한 키워드의 작은 흔들림 방지 |
| 기준선 최소 일수 | 28일 | 평가 정의(`eval/trend/README.md`)와 같다 |
| 기준선 최소 값 | 0.01 | 개발 케이스로 재지 않은 안전장치(자릿수가 모자라는 값) |

#### 요일 보정 (비교했고 쓰지 않음, 기본 `weekday = "none"`)

놓친 개발 케이스 `inject_x1.5_1d`(공책)는 1.5배를 넣은 날이 토요일이라 원래 값이 평소의 0.74배였고, 넣은 뒤에도 평소의 1.11배라 못 잡았다. 요일 보정 세 가지를 개발 케이스로 비교했다(`Settings.weekday`).

- (c) `none`: 지금 방식 (56일 전체 log 중앙값·MAD)
- (b) `ratio`: 요일별 평균 차이(같은 요일 8개의 중앙값 − 전체 중앙값)를 빼고 56일 전체로 비교
- (a) `same_weekday`: 같은 요일 8개끼리만 중앙값·MAD

| 방식 | z 기준 | 탐지 | 오탐 | 판정 불가 | 그룹 분리 | 계절 오탐 | 실제 사례 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **(c) 지금** | **3.2** | **0.909** | **0** | 1.0 | 1.0 | 0.667 | 1.0 |
| (b) 요일 비율 | 2.8~3.5 | 0.909 | **0.1** | 1.0 | 1.0 | 0.667~1.0 | 1.0 |
| (b) 요일 비율 | 4.0 | 0.909 | 0 | 1.0 | 1.0 | 0.333 | 1.0 |
| (a) 같은 요일만 | 2.8~4.0 | 0.909 | **0.1** | 1.0 | 1.0 | 0.333~1.0 | 1.0 |

- 요일 보정을 해도 `inject_x1.5_1d`의 z는 0.66 → 1.8~2.0(보정 배수 1.32)이라 여전히 못 잡는다. 하루짜리 1.5배는 날마다 ±10% 흔들리는 키워드에서 기준(3.2)을 넘기 어렵다
- 대신 평범 케이스 `none_1`의 z가 2.86 → (b) 3.89·(a) 4.9로 올라 **오탐이 생긴다**(요일 효과를 빼면 흔들림이 작아지고, 요일당 8개로 정한 요일 효과가 흔들린다). 오탐이 0에서 올라가는 후보는 쓰지 않는다
- (b) z 4.0만 오탐 0이지만 탐지는 지금과 같고 평범 최대 z(3.89)와의 여유가 0.11뿐이다(지금은 2.86 vs 3.2)
- 그래서 **(c)를 유지한다**. 보정 코드는 다시 평가할 수 있게 설정으로만 남겼다(기본 끔)
- holdout(결과 숫자만, 기준을 맞추지 않음): (c) 탐지 0.8125·오탐 0, (b) z 3.2·4.0 탐지 0.9375·오탐 0, (a) z 3.2 오탐 0.125·z 4.0 탐지 0.9375·오탐 0. holdout은 (b)가 낫게 나오지만 개발 케이스로 정한다는 규칙대로 쓰지 않았다. (b)를 다시 보려면 요일 효과가 뚜렷한 개발 케이스를 먼저 늘리고 holdout은 새 seed로 다시 만든다

### campaign_effect (캠페인 효과)

- 입력: `campaign_id`(선택), 캠페인 시작·끝(`window_from`·`window_to`). 출력: 주문·매출·쿠폰 각각 "앎(값·근거)" 또는 "확인할 수 없음 + 이유", 요약 문장
- 주문(`skills/campaign_effect.py`, 코드 계산): 캠페인 기간 `order_count` 합계(실제)와 기준선 지난 구간 합계의 평균(기대). **증분 = 실제 − 기대**(평가 채점기와 같은 정의). 방향은 기대를 평균으로 한 포아송 꼬리확률(`metrics/judge.py`의 `poisson_upper`·`poisson_lower`)이 0.001보다 작으면 늘었음·줄었음, 아니면 같음. 지표는 커넥터 `metric_series`로 읽는다
- 매출: 카탈로그 `revenue_metric`(회사 카탈로그 한 줄)이 있으면 같은 방법으로 합계·기대·증분을 낸다(방향은 기대 대비 ±10% 임시 기준, 평가로 정하지 않음). **캣북스는 비어 있다**: shop-api metric-series에 매출이 없어 "확인할 수 없음 + 이유(지표 없음)"가 지금 정상이다
- 쿠폰: `GET /api/shop/coupons?campaign_id=`(커넥터 `campaign_coupons`, data는 `COUPON_READERS`에 있다)의 발급(`count`)·사용(`used_units`) 장수 합으로 누적 사용률 = 사용 ÷ 발급. 설명은 LLM이 아니라 고정 문장 "조회 시점 기준 누적 사용률이며, 캠페인 뒤 사용분도 섞여 있다"(평가 채점기가 이 글자를 본다)
- **기준선** (`baseline_rule`로 어느 것을 썼는지 답한다):
  - 하루 이하 캠페인: 지난 7일 같은 시간대 (예전과 같음)
  - 하루 넘는 캠페인(7일 이하): **7일 전 같은 구간 + 14일 전 같은 구간의 평균** = 시작 전 14일의 같은 요일·시간대 (평가 `eval/campaign/multiday` 기대값 정의와 같음). 7일보다 길면 7일 전 구간이 캠페인과 겹쳐 확인할 수 없음
  - 시작 전 기록이 14일보다 짧으면 확인할 수 없음 "시작 전 기록 부족 (N일): …". 15일 전까지 읽어서, 14일 전 구간만 통째로 빈 경우(그 주 서버 꺼짐)와 기록이 짧은 경우를 구분한다
  - **기준선에 쓰는 지난 구간이 빈 구간·기록 공백 규칙(아래)에 걸리면 그 구간은 뺀다**(0건으로 넣어 기대값을 낮추지 않음). 뺀 구간과 이유는 `baseline_note`, 남은 구간이 없으면 확인할 수 없음 "기준선에 쓸 지난 구간이 없음 (…)". 지난 구간끼리는 나머지 지난 구간과 비교한다
  - 요일·날짜 경계는 **회사 시간대**로 센다: `config/tenants/<회사>/tenant.toml`의 `timezone`(캣북스 `Asia/Seoul`) → `scripts/tenant_env.py`가 `DATA_TENANT_TIMEZONE`으로 넘긴다. 환경변수가 없으면 회사 폴더 `tenant.toml`을 직접 읽고, 그것도 없으면 UTC. 지난 구간은 회사 시간대 벽시계로 7·14일 전이다
  - **한계: 기준선 두 주에 명절·다른 캠페인이 끼면 기준선이 오염된다**(그 주가 평소보다 높거나 낮아도 지금은 걸러 내지 않는다)
- 확인할 수 없음(0으로 채우지 않음): 기준선 주문이 0건, 시작 전 기록 부족, 기준선에 쓸 지난 구간이 없음, 캠페인이 7일보다 김, campaign_id 없음, 그 캠페인 쿠폰 없음, 쿠폰을 읽지 못함
- **기록 공백은 줄었음이 아니다.** shop-api는 주문이 없는 5분을 0으로 줘서, 서버가 꺼졌던 시간도 0건으로 보인다. 주문 판정 전에 본다(주문·매출 확인할 수 없음, 쿠폰은 따로):
  - 구간에 주문·결제 기록이 하나도 없음 → 이상 탐지 빈 구간과 같은 함수(`metrics/judge.py` `empty_window`)·문장(`detect.empty_reason`)
  - 결제 시도 0인 5분 칸이 **12칸(1시간) 이상 이어지고**, 비교 구간(기준선 지난 구간)에서 **가장 길게 이어진 것의 2배 이상** → "기록 공백 의심 (N칸): … M칸 이어짐 …". 통째로 빈 지난 구간은 비교에서 뺀다(그 구간도 공백). 서버가 꺼지면 빈 칸이 한 덩어리로 이어지고, 주문이 적은 가게의 실제 감소는 빈 칸이 흩어진다
  - 예전 규칙(#306, 0칸 비율 5%p·12칸)은 주문이 적은 가게의 실제 감소를 공백으로 봤다(평가 `multiday/cases/down_x0_6_2d_mon`: 0칸 62% → 76%, 438칸). 이어진 길이 규칙으로 바꿨다
  - 기준 근거 (평가 생성기로 만든 구간, holdout 안 봄. 잡은 수/전체):

    | 규칙 | 평소 새벽 1시간 | 평소 24시간 | 바쁜 가게 3시간 공백 낮·새벽 | 적은 가게 평소 24시간·2~3일 | 적은 가게 감소 0.6배 24시간·2~3일 | 적은 가게 감소 0.5배 | 적은 가게 3시간 공백 낮·새벽 | 개발 케이스 |
    | --- | --- | --- | --- | --- | --- | --- | --- | --- |
    | 예전: 비율 5%p·12칸 | 0/30 | 0/12 | 4/4·4/4 | 1/8·1/8 | 8/8·8/8 | 8/8 | 4/6·1/6 | 1/14 |
    | **지금: 이어짐 ≥12칸·평소 최장 2배** | 0/30 | 0/12 | 4/4·4/4 | 0/8·0/8 | 0/8·0/8 | 2/8 | 1/6·2/6 | 0/14 |
    | 이어짐 ≥12칸·평소 최장 3배 | 0/30 | 0/12 | 4/4·4/4 | 0/8·0/8 | 0/8·0/8 | 1/8 | 0/6·0/6 | 0/14 |
    | 이어짐 ≥ 평소 최장 + 12칸 | 0/30 | 0/12 | 4/4·4/4 | 0/8·2/8 | 0/8·2/8 | 4/8 | 2/6·4/6 | 1/14 |

    바쁜 가게(5분 5건)는 3시간 공백의 이어짐이 36칸으로 평소 최장(2~8칸)의 6배 이상이라 잘 갈린다. 평소 구간은 최장이 비교 구간 최장의 1.81배 이하라 2배로 정했다
  - **한계:** 주문이 아주 적은 가게(5분에 1건 미만)는 평소에도 새벽에 빈 칸이 30~40칸씩 이어져, 3시간 공백과 큰 감소(0.5배)를 이어진 길이로도 구분하기 어렵다. 그런 가게의 3시간 공백은 대부분 못 잡고(3/12), 0.5배 감소는 가끔(2/8) 공백으로 본다. 1시간보다 짧은 공백은 잡지 않는다
  - **확률 규칙(이어짐 규칙과 함께, 둘 중 하나면 공백):** 그 시간대(시각별) 평소 5분 결제 시도 수 λ로, 12칸 이상 이어진 0칸 L칸이 우연히 나올 확률 e^(−Σλ)(시간대마다 λ가 달라 칸별 λ의 합)이 **10⁻⁹ 미만**이면 "기록 공백 의심 (N칸): … M칸 이어짐, 그 시간대 평소 주문으로는 우연히 그럴 확률 …"
    - 왜: 비교한 지난날들도 부분적으로 꺼져 있으면 "평소 최장 이어짐"이 길어져 2배 규칙이 공백을 못 잡는다(로컬 24시간 측정이 주문 139건 vs 기준선 6,129건 → −5,990건 감소로 나옴)
    - λ는 **공백이 아닌 지난 구간으로만** 구한다. 지난 구간은 먼저 나머지 지난 구간 전부와 비교해 공백 같은 구간을 찾고, 다시 볼 때 그 구간을 뺀 정상 구간으로 λ를 구한다(부분적으로 꺼진 날이 λ를 낮추지 않게). 이어짐 규칙은 #313 그대로 나머지 지난 구간 전부와 비교한다
    - λ를 구할 정상 구간(결제 시도 기록이 있는 지난 구간)이 없으면: 기준선이 하나도 안 남으면 "기준선에 쓸 지난 구간이 없음 (…)", 지난 구간에 결제 시도 기록이 없는데 캠페인 구간에 12칸 이상 0칸이 이어지면 "기록 공백인지 판단할 수 없음 …" (둘 다 확인할 수 없음)
    - 기준 근거 (시각별 주문 모양을 넣어 만든 구간, holdout·평가 케이스 안 봄. 공백으로 본 수/40. 바쁜 가게 = 5분 평균 5건, 적은 가게 = 0.5건, 지난날 꺼짐 = 지난 7일 중 4일이 6~14시간 꺼짐):

      | 정답 | 구간 | 이어짐만(#313) | +10⁻³ | +10⁻⁴ | +10⁻⁶ | **+10⁻⁹** | +10⁻¹² |
      | --- | --- | --- | --- | --- | --- | --- | --- |
      | 공백 | 바쁜 3시간 공백 낮·새벽 | 40·40 | 40·40 | 40·40 | 40·40 | **40·40** | 40·40 |
      | 공백 | 바쁜 지난날 꺼짐 + 20시간 꺼짐 | 4 | 40 | 40 | 40 | **40** | 40 |
      | 공백 | 바쁜 지난날 꺼짐 + 3시간 공백 | 0 | 40 | 40 | 40 | **40** | 40 |
      | 공백 | 적은 3시간 공백 낮·새벽 | 0·1 | 40·4 | 40·2 | 40·1 | **40·1** | 5·1 |
      | 공백 | 적은 지난날 꺼짐 + 20시간 꺼짐 | 2 | 40 | 40 | 40 | **40** | 40 |
      | 아님 | 바쁜 평소 · 적은 평소 · 적은 평소 2~3일 · 아주 적은(0.2) 평소 | 0·0·0·0 | 0·2·5·1 | 0·0·0·0 | 0·0·0·0 | **0·0·0·0** | 0·0·0·0 |
      | 아님 | 바쁜·적은 지난날 꺼짐 + 평소 | 0·0 | 1·2 | 0·0 | 0·0 | **0·0** | 0·0 |
      | 아님 | 바쁜 감소 0.5 | 3 | 10 | 5 | 3 | **3** | 3 |
      | 아님 | 적은 감소 0.6 · 0.6 2~3일 · 0.5 | 0·3·0 | 12·30·24 | 6·17·11 | 0·4·3 | **0·3·0** | 0·3·0 |
      | 아님 | 아주 적은(0.2) 감소 0.6 | 0 | 16 | 5 | 0 | **0** | 0 |

      10⁻⁹는 새로 잡을 공백(지난날도 꺼짐, 적은 가게 낮 공백)을 다 잡고, 공백이 아닌 구간(평소·실제 감소)은 이어짐 규칙만 쓸 때보다 하나도 더 잡지 않는다. 10⁻⁶부터 적은 가게의 실제 감소를 공백으로 보기 시작하고(0.5배 3/40), 10⁻¹²는 적은 가게 낮 공백을 놓친다(5/40). 한 구간 안에서 시작점을 여러 번 보는 셈이라 아주 작은 값을 쓴다
    - 한계는 그대로: 적은 가게의 새벽 공백은 평소 λ도 작아 우연과 구분되지 않는다(1/40). 이어짐 규칙이 이미 잡는 바쁜 가게 감소 0.5배(3/40)는 바뀌지 않는다
  - 결제 시도를 주지 않는 커넥터(`transactions_volume`이 None)는 빈 구간만 보고 비율은 보지 않는다
  - 이상 탐지(anomaly_detection)의 동작은 그대로다(같은 함수를 부를 뿐)
- 요약 문장: 방향이 같음인데 증분이 0이 아니면 "증가(감소) 추정 ±N건, 아직 통계적으로 확실하지 않음"(화면 #294와 같은 규칙)
- 주문 지표를 읽지 못하면(shop-api 꺼짐) 숫자 없이 `failed`·`unavailable`
- LLM은 전체 요약 문장만 쓰고 `explain.py` 숫자 검증을 그대로 거친다(템플릿 `campaign`)
- 요청 가능: orchestrator, marketing, dashboard-api

#### 기준값 (평가 개발 케이스로 정함)

`eval/campaign/cases/` 결과만 보고 정했다. holdout은 고른 뒤 결과 숫자만 봤고, 그 숫자로 다시 맞추지 않았다. 기준값 파일은 `eval/campaign/baseline.json`.

| 방향 유의수준 | 방향 | 증분 오차 | 판정 불가 | 쿠폰 |
| --- | --- | --- | --- | --- |
| 0.05 · 0.01 · **0.001** · 0.0001 · 0.000001 | 모두 1.0 | 모두 0 | 모두 1.0 | 모두 1.0 |

- 개발 케이스로는 후보가 갈리지 않는다: 변화 없는 케이스 p ≈ 0.25·0.27, 늘거나 준 케이스 p ≤ 1.5×10⁻¹¹
- **0.001**을 고른 이유: 이상 탐지(`judge.py` `DEFAULT_ALPHA`)와 같은 값이고, 실제 주문은 포아송보다 더 흔들려 0.05면 평범한 날을 늘었음·줄었음으로 말하기 쉽다
- 대가: 작은 효과는 같음으로 나온다. 예: 기대 150건에 +20%(180건)면 p ≈ 0.009 → 같음

### YouTube 영상 통계 스냅샷 (`youtube_collect`, 판정 전 단계)

**왜 스냅샷이 필요한가:** YouTube Data API는 영상의 **지금 누적값**(조회수·좋아요·댓글)만 주고 기간별 추이를 주지 않는다. 한 번 받은 값으로는 "게시 후 평균 속도"(조회수 ÷ 게시 후 시간)밖에 못 구하는데, 이 값은 지금 속도와 크게 다르다. 2026-11-02 실제 값(약 1.2시간 간격 두 번 수집):

| 영상 | 게시 후 평균 속도 | 두 스냅샷으로 구한 지금 속도 |
| --- | --- | --- |
| 인기 영상 A (게시 40시간) | 81,788/시간 | 약 13,000/시간 (3,277,193 → 3,292,791) |
| 인기 영상 B (게시 25시간) | 90,628/시간 | 약 1,200/시간 (2,282,272 → 2,283,722) |

그래서 정해진 간격으로 받아 쌓아 두고, 두 시각의 차이로 지금 속도를 구한다. 급상승 판정·태그 정리는 스냅샷이 쌓인 뒤 별도 PR이다.

```bash
cd backend
uv run python -m data_agent.youtube_collect              # 한 번
uv run python -m data_agent.youtube_collect --every 3600 # 1시간마다 (최소 600초, Ctrl+C로 멈춤)
```

- 커넥터 `connectors/youtube.py`: 인기 목록(mostPopular, 50개씩 4쪽 = 200개, 순위 붙임), 추적 영상 통계(id 50개씩), 키워드 검색(검색 결과 통계는 videos.list로 한 번 더). 키 `YOUTUBE_API_KEY`는 URL이 아니라 헤더(`X-Goog-Api-Key`)로 보낸다. 주소는 `AX_YOUTUBE_API_URL`로 바꿀 수 있다
- 숫자 칸이 응답에 없으면(좋아요 숨김·댓글 막음) None/NULL이다. 지워졌거나 비공개가 된 영상은 결과에 없다. 0으로 채우지 않는다
- 못 읽으면 `ConnectorUnavailable`(403 할당량 초과는 `QUOTA_EXCEEDED`). 할당량 초과가 나면 그 실행의 남은 호출을 멈추고 받은 것까지 저장한다
- 저장: `DATA_YOUTUBE_DB`(없으면 `backend/.data/youtube_snapshots.db`, 커밋 안 함). `snapshots`는 (수집 시각, 영상 id)가 기본 키라 같은 시각 같은 영상은 한 줄이다. 칸: 수집 시각, 영상 id, 조회수·좋아요·댓글, 인기 순위, 게시 시각, 카테고리, 채널 id, 태그(JSON), 어디서 받았는지. `runs`에 실행마다 쓴 할당량 단위·개수·실패 이유
- 회사 키워드: `config/tenants/<회사>/youtube.toml`(`tenant.toml`의 `youtube` → `scripts/tenant_env.py`가 `DATA_YOUTUBE_CONFIG`로). 없으면 `config/tenants/<AX_TENANT, 기본 catbooks>/youtube.toml`. 코드에 키워드를 두지 않는다
- 키는 환경변수가 먼저고, 없으면 `backend/.env`에서 읽는다(값은 출력하지 않는다)
- `run_all.sh`에는 아직 넣지 않았다

**하루 예상 할당량** (1시간마다, 기본 한도 10,000단위/일, 미국 태평양시 자정에 초기화):

| 항목 | 계산 | 하루 |
| --- | --- | --- |
| 인기 목록 | 4쪽 × 24번 | 96 |
| 추적 영상 | 최대 500개 = 10단위 × 24번 | 최대 240 |
| 회사 키워드 검색 (한국 날짜 하루 1번) | 키워드 2개 × (검색 100 + 통계 1) | 202 |
| **합계** | | **최대 약 538 (한도의 약 5%)** |

키워드를 하나 늘리면 하루 101단위가 는다. 실행마다 쓴 단위는 로그 한 줄(`[youtube] … 할당량 N단위`)과 `runs` 표에 남는다.

### 인기 검색어 스냅샷 (`trending_collect`, 후보 출처)

Google Trends **공개 RSS** `https://trends.google.com/trending/rss?geo=KR`(키·로그인 없음)을 읽어 쌓는다. 2026-11-02 확인: 이 주소는 200·항목 10개, 예전 일별 주소(`/trends/trendingsearches/daily/rss`)는 404라 이 주소 하나만 쓴다.

```bash
cd backend
uv run python -m data_agent.trending_collect              # 한 번
uv run python -m data_agent.trending_collect --every 3600 # 1시간마다 (최소 600초, Ctrl+C로 멈춤)
```

- 커넥터 `connectors/google_trends.py`: 항목마다 검색어, 피드 안 순서(순위), 피드의 트래픽 표시값(`ht:approx_traffic`, 예: "500+". 검색 건수가 아니라 글자 그대로 둔다), 올라온 시각. DTD가 든 응답은 읽지 않는다. 주소는 `AX_GOOGLE_TRENDS_RSS_URL`로 바꿀 수 있다
- 저장: `DATA_TRENDING_DB`(없으면 YouTube 스냅샷 DB와 같은 폴더의 `trending_snapshots.db`, 기본 `backend/.data/`, 커밋 안 함). (수집 시각, 지역, 순위)가 기본 키. 실패한 실행은 `runs`에만 남고 빈 스냅샷을 만들지 않는다
- 지역: 회사 `trends.toml` `[trending] geo`(기본 KR)
- `run_all.sh`에는 넣지 않았다

### trend_candidates (YouTube 후보 → 네이버 급상승)

YouTube에서 뜨는 소재가 검색에서도 뜨는지 본다. 후보 뽑기는 **코드 규칙**이고 LLM을 쓰지 않는다.

1. 스냅샷: YouTube 스냅샷 DB(`DATA_YOUTUBE_DB`)에서 인기 목록을 받은 가장 최근 시각의 인기 영상(순위 순). 파일을 읽기 전용으로 연다(없으면 만들지 않음)
   - **지난 기간**(요청에 `end_date`가 있음)은 그 판정 마지막 날(한국 날짜)이 끝나기 전(다음 날 0시 KST까지)의 가장 최근 스냅샷을 쓰고, 나이(`max_age_hours`)도 그 시각 기준으로 잰다. 그때 스냅샷이 없으면 YouTube 후보는 unknown + "그 시점(… 0시 KST) 이전 YouTube 스냅샷이 없음"이고 네이버는 부르지 않는다. `end_date`를 비우면(지금) 가장 최근 스냅샷을 지금 기준으로 본다
   - content_recommend도 같다: 지난 기간이면 그때 스냅샷의 태그만 쓰고, 없으면 YouTube 후보만 unknown(기본 키워드 검색 판정은 그대로)
   - 2026-11-02 기준 스냅샷은 11-02 10:45 KST부터 있어, 그 전 날짜로 판정하면 YouTube 후보는 모두 unknown이다(쌓이면 풀림)
2. 태그 정리: 앞뒤 공백·`#` 제거, 영문 소문자(`T1`·`#t1 `·` t1` → `t1`). 한 영상에 같은 태그가 여러 번 붙어도 한 번만 센다
3. 별칭 묶기: 회사 별칭 표(`[candidates.aliases]`, `대표 이름 = [별칭…]`)로 별칭을 대표 이름으로 바꾼다(`lol` → `리그오브레전드`, `티원` → `t1`). 나온 영상 수는 **영상 기준으로 중복 없이** 합친다(한 영상에 `lol`·`리그오브레전드`가 같이 있어도 1개). 표에 없는 이름은 묶지 않는다(코드 규칙, LLM 안 씀). 별칭 하나는 대표 이름 하나에만 쓰고(앞에 적은 것), 대표 이름은 다른 이름의 별칭이 될 수 없다
4. 후보: `min_videos`개 이상 인기 영상에 붙은 이름 중 회사 제외 목록(`exclude_tags`, 정리한 태그·대표 이름과 비교)을 빼고, 나온 영상 수 많은 순(같으면 더 높은 순위 영상에 붙은 것 먼저)으로 최대 `max_candidates`개. 요청의 `max_candidates`가 있으면 그 값
5. 네이버: 후보마다 **대표 이름 그룹 하나에 대표 이름 + 별칭들을 키워드로**(최대 20개) 넣어 한 번씩 부르고 **trend_scan과 같은 함수(`trend_scan.judge`)**로 판정한다(기간·기준값도 같음). 판정 함수를 새로 만들지 않아 trend 평가 지표는 바뀌지 않는다
   - **이름별 판정**: 별칭이 있는 후보는 대표 이름·별칭을 하나씩 따로도 판정한다(이름마다 네이버 1번). 묶음 그룹이 급상승이면 그 판정, 묶음은 평범(또는 판정 불가)인데 이름 하나라도 급상승이면 **급상승으로 올리고** 그 이름들 중 z가 가장 큰 판정을 쓴다(`naver_from: name`). 따로 판정해 급상승인 이름은 `rising_names`, 이름별 판정은 `name_verdicts`. 별칭이 없는 후보는 더 부르지 않는다
   - 2026-11-02 실데이터: `lol`이 묶음(1.01배 평범)에 묻혔다가 이름별로 1.27배 급상승 → `리그오브레전드` 후보가 급상승(올라간 이름 `lol`). 10-25 기준도 같다(lol 1.73배). 네이버 요청은 실행마다 5번 → 16번(별칭이 있는 후보 4개 × 이름 수)
6. 출력: 후보마다 대표 이름, 묶인 별칭(`aliases`, 네이버 그룹에 함께 넣은 것), YouTube 근거(나온 영상 수, 스냅샷 시각, 대표 영상 id `rep_videos`개, 스냅샷에 실제로 나온 태그 `matched_tags`), 네이버 판정(`TrendVerdict` 그대로). 그리고 네이버 부른 수(`naver_calls`, 실패 포함)와 숫자로 만든 한 문장

8. **YouTube 제목 구**(`[candidates] titles = true`): 인기 영상 제목을 공백·문장부호로 나눠(형태소 분석 없음, 아포스트로피는 붙여 읽음) 이어진 2~3어절 구를 만들고, `min_videos`개 이상 영상 제목에 나온 구만 쓴다. 더 긴 구와 영상 집합이 같은 짧은 구는 그 조각이라 뺀다. 태그 이름과 공백만 다르면(리그 오브 레전드 = 리그오브레전드) 그 태그 후보에 합치고, 아니면 제목만으로 후보가 된다. 출처는 `origins`(youtube_tag·youtube_title), 센 구는 `youtube.matched_phrases`
   - 제목은 `youtube_store`의 `video_titles` 표(영상마다 한 줄)에 둔다. `snapshots` 표 모양은 바꾸지 않았다(돌고 있는 예전 수집기가 깨지지 않게). **새 코드로 수집기를 다시 켜야 새 스냅샷의 제목이 쌓인다.** 그 전 스냅샷은 `youtube_collect --titles`로 채운다(2026-11-03: 275개, 6단위)
9. **자동 제외**(`[candidates.auto_exclude]`): 쌓인 인기 목록 전체(`popular_history`, 판정 시각까지)에서 이름마다 나온 영상 비율·카테고리 수·나온 날 비율을 재서 셋 다 기준 이상이면 늘 붙는 말로 보고 뺀다(문서 빈도). 기준: `min_doc_ratio` 0.02, `min_categories` 3, `min_day_ratio` 0.5. 수동 `exclude_tags`는 그대로 늘 빼고(덮어쓰기용), `keep_tags`는 자동 제외에서 살린다. 빠진 이름은 `auto_excluded`(이번 스냅샷 영상 수·쌓인 영상 수·비율·카테고리·날짜·이유 한 줄), 잰 범위는 `exclusion_basis`
   - 2026-11-02 스냅샷(영상 275개, 1일)으로 본 값: 카테고리 3개면 스트리머·트위치만 잡힌다. 2개로 낮추면 로블록스·피파 같은 실제 소재까지 빠져 3개로 뒀다. 인기 영상의 80%가 게임 카테고리라 `게임`·`치지직`은 카테고리 2개에만 나와 자동으로 못 잡는다 → 수동 목록이 아직 필요하다(수동 목록을 끄면 `게임`이 상위 5에 들어온다). 날짜 기준은 스냅샷이 하루치뿐이라 아직 거르지 못한다(쌓이면 의미가 생김)
7. **인기 검색어 출처**(`[trending]`): 최신 인기 검색어 스냅샷의 검색어를 태그와 같은 규칙(정리·별칭·제외 목록)으로 거른다. YouTube 후보와 이름(공백·대소문자 무시)이 겹치면 그 후보에 근거(`trending`: 피드 순위·트래픽 표시값·스냅샷 시각)를 붙이고 `origins`에 `trending_search`를 더한다(네이버는 더 부르지 않음). 겹치지 않는 것은 피드 순위 순으로 최대 `max_candidates`(5)개를 `trending_candidates`로 같은 판정 함수에 넣는다(후보마다 네이버 1번, 별칭이 있으면 이름별도). 인기 검색어 스냅샷이 없거나 `max_age_hours`(24)보다 오래되면 이 출처만 `trending_status: unknown` + 이유이고 YouTube 후보는 그대로다. 화면 출처 표시는 "인기 검색어"
   - 한계: 피드는 **오늘** 뜨는 검색어인데 네이버 판정은 **어제까지**(하루 늦게 채워짐)라, 오늘 처음 뜬 검색어는 평범·자료 없음으로 나올 수 있다. 추천 점수의 두 소스 가산점은 검색 급상승 + YouTube일 때만이다(인기 검색어 + 네이버는 한 소스로 센다, 점수 규칙 그대로)

- **확인할 수 없음:** 스냅샷이 없거나(인기 영상 기록 없음) `max_age_hours`보다 오래되면 후보를 뽑지 않고 `status: unknown` + 이유(마지막 수집 시각·몇 시간 전·기준). 네이버는 부르지 않는다. 네이버를 못 읽은 후보는 **그 후보만** `naver.status: unknown`("네이버 검색어 트렌드를 읽지 못함 [코드]")
- 설정은 회사 폴더 `trends.toml`의 `[candidates]`(`max_candidates` 5, `min_videos` 2, `max_age_hours` 24, `rep_videos` 3, `exclude_tags`). 후보마다 네이버 1번이라 작게 둔다(하루 한도 `NAVER_DATALAB_DAILY_LIMIT`). 설정이 없으면 같은 숫자에 제외 목록·별칭 표 없음(별칭 표가 없으면 묶지 않고 예전과 같다). 일반 태그 목록·별칭은 코드에 두지 않는다
- 별칭 표에는 **뜻이 하나인 이름만** 넣는다. 뜻이 여럿인 줄임말(`롤`·`마크`·`메이플`)을 넣으면 네이버 그룹에 다른 뜻의 검색량(롤케이크·mark·단풍나무)이 섞인다
- **한계:** 별칭 표에 없는 이름은 묶지 않는다(자동으로 찾지 않음). 묶음 그룹만 보면 작은 이름의 급상승이 큰 이름에 묻혀서 이름별로도 판정한다(위). 대신 별칭이 많을수록 네이버 호출이 는다. 태그는 올린 사람이 붙인 것이라 영상 내용과 다를 수 있다. 인기 목록(지역 `KR`) 태그만 보고, 회사 키워드 검색 결과·추적 영상은 보지 않는다. 스냅샷 한 시각만 보며 YouTube 쪽 속도(두 시각 차이)는 아직 보지 않는다

### trend_history (월별 흐름, 판정 없음)

- 키워드 그룹(입력이 없으면 회사 기본 키워드 그룹)마다 네이버 검색어 트렌드를 **월 단위**(`time_unit = "month"`)로 한 번씩 부른다(작은 키워드가 큰 키워드에 눌리지 않게). 기간은 최근 `months`개월(`trends.toml` `[history] months`, 기본 24, 13~60)이고 마지막 달은 다 채워진 지난달(한국 날짜)이다. `end_month`로 바꿀 수 있다
- **판정하지 않는다.** 급상승·계절 반복 판정은 평가 케이스가 없어서 하지 않고, 달마다 값과 "작년 같은 달 대비" 배수(값 ÷ 작년 같은 달 값, 소수 2자리)만 코드로 계산한다. 작년 같은 달 값이 없거나 0이면 배수는 비운다(0으로 채우지 않음). 그룹마다 마지막 달의 배수(`latest_vs_last_year`)
- 값은 그 그룹 요청 안 상대값(최댓값 = 100)이라 그룹끼리 높이를 비교하지 않는다
- 네이버가 값을 주지 않은 그룹은 그 그룹만 unknown "네이버가 이 기간 값을 주지 않음", 못 읽으면 그 그룹만 unknown "네이버 검색어 트렌드를 읽지 못함 [코드]". 모든 그룹을 못 읽으면 `failed`·`unavailable`
- 2026-11-02 실데이터(기본 그룹 4개, 네이버 4번): 2026년 10월이 작년 10월보다 고양이 0.66배, 고양이 책 0.66배, 책 추천 0.83배, 그림책 1.02배
- 다음: 작년 같은 시기와 비교한 **계절 반복 판정**은 평가 케이스를 먼저 만들고(평가 정의 PR) 그다음 판정 코드

### youtube_velocity (YouTube 지금 속도, 판정 없음)

- 스냅샷 DB의 최근 수집 시각 3개를 읽는다. **지금 속도** = 최근 두 시각 사이 조회수 차이 ÷ 시간(시간당 조회 증가, 소수 1자리). 셋째 시각이 있으면 그 전 구간 속도와 비교해 `speed_change`(지금 − 그 전)·`faster`
- 지금 속도 큰 순(같으면 영상 id 순)으로 상위 `top`개(`trends.toml` `[velocity] top`, 기본 10, 요청으로 1~50)와 각 영상의 태그·조회·좋아요·댓글·인기 순위
- **판정하지 않는다.** 평소 속도 기준이 없어서 "급상승" 같은 판정 말을 쓰지 않는다. **스냅샷이 며칠 쌓이면 평소 속도와 비교한 판정을 붙일 예정**이다(그 전에 평가 케이스부터)
- 조회수가 빠진 영상은 속도를 비우고 순위에 넣지 않는다(0으로 채우지 않음, `missing_views`로 센다). 좋아요·댓글이 숨겨진 영상은 그 칸만 비운다. 최근 스냅샷에 없는 영상(지워짐·비공개·추적 끝)과 앞 스냅샷이 없는 새 영상은 뺀다
- 스냅샷이 2개 미만이면 unknown + 이유("YouTube 스냅샷이 2개 미만이라 지금 속도를 잴 수 없음 (지금 N개 …)")
- 2026-11-02 실데이터(17:55·16:55·15:55 KST 스냅샷): 두 시각 모두에 있는 영상 270개, 상위 1위 시간당 조회 약 3.76만(그 전 구간 3.82만보다 느려짐)

### content_recommend (영상 소재 추천, 콘텐츠 루프 2단계)

트렌드 판정 결과로 "지금 만들 만한 영상 소재"를 순위대로 고른다. 순위는 **코드 규칙**, 아이디어 문장만 LLM이다.

1. 판정: 회사 기본 키워드 그룹(`trends.toml` `[[groups]]`)을 trend_scan과 같은 요청(그룹마다 한 번)·같은 판정 함수로, YouTube 인기 태그 후보를 `trend_candidates`(`find_candidates`)로 본다
2. 합치기: 기본 그룹과 YouTube 후보의 이름(같이 본 이름 포함, 공백·대소문자 무시)이 겹치면 한 소재다. 그 그룹이 급상승이면 그 판정에 YouTube 근거를 붙인다
3. **급상승(rising)만** 후보다. 평범·판정 불가는 넣지 않는다. 급상승이 없으면 빈 목록 + 이유 `이번 기간 급상승한 소재가 없음`(네이버를 못 읽은 것이 있으면 그 이름도)이고 **억지로 채우지 않는다**
4. 점수 = `z_weight` × z + (검색 급상승 + YouTube 근거 **두 소스**면 `two_source_bonus`). 큰 순, 같으면 z 큰 순 → 이름. 최대 `max_items`개(요청의 `max_items`가 있으면 그 값)
5. 회사 관련성: 소재 이름(같이 본 이름 포함)이 회사 키워드(기본 키워드 그룹 이름·키워드 + `company_keywords`)를 품거나 그 안에 들면 `related`, 아니면 `low`. **표시만** 하고 순위에서 빼지 않는다
6. 아이디어 문장(LLM, `explain.explain_idea`): 소재마다 1~2문장 + 형식(숏폼·롱폼). 프롬프트에 넣는 숫자는 **배수 하나**("평소보다 N배")뿐이고 z·YouTube 영상 수는 넣지 않는다(z는 순위 근거로만, 영상 수는 고정 문장에만). 규칙: 숫자는 "평소보다 N배"만, z·점수 같은 통계 용어 금지, "우리 회사 제품" 같은 막연한 말 대신 **회사 소개(`company_intro`)**의 상품·고객으로 구체적으로, 회사 소개에 없는 상품은 지어내지 않기. 근거가 두 소스면 숫자 없이 "검색 급상승 + YouTube 인기 영상"으로만 알린다. 이름(외부 태그·같이 본 이름)은 **데이터 블록(untrusted)에만** 넣는다. 숫자 검증은 `explain`과 같은 규칙(이름 속 숫자 t1의 1은 허용). 실패하면 고정 문장("〈키워드〉 검색이 평소의 N배로 올랐습니다… 짧은 영상으로 다뤄 볼 만합니다") + 숏폼, `fallback: true`와 이유

- 네이버 호출: trend_scan·trend_candidates와 요청 모양이 같아 **같은 프로세스에서 오늘 이미 부른 요청은 커넥터 하루 캐시**에서 나온다(데이터 AI 서버 안에서는 화면이 트렌드 탭을 연 뒤라면 추가 호출이 거의 없다). 이번 실행에서 실제로 부른 수는 `naver_calls`(커넥터 `calls_today` 전후 차이). 기본 그룹과 YouTube 후보의 요청이 같으면(예: 그림책) 한 번만 부른다
- 설정 `[recommend]`: `max_items` 5, `z_weight` 1.0, `two_source_bonus` 2.0, `company_keywords`(캣북스: 책·도서·출판·캣북스·반려묘·집사), `company_intro`(회사 소개 한두 줄: 무엇을 파는지·고객. 캣북스는 쇼핑몰에 있는 상품만 적었다). 설정이 없으면 같은 숫자에 회사 키워드는 기본 그룹만, 회사 소개가 없으면 프롬프트에 "상품·고객을 지어내지 말 것"
- 프롬프트를 다듬은 결과(2026-11-02, 실제 데이터 오늘·10-25 기준 5번씩, 문장 15개): 고정 문장으로 바뀐 비율 13%(2/15, 지어낸 숫자) → 0%(0/15). z·영상 수를 쓴 문장 13/13 → 0/15. "우리 회사 제품" 대신 노트·젤펜·독서대·에세이 같은 실제 상품으로 쓴다
- 출력: 소재마다 순위, 키워드, 같이 본 이름, 출처(`naver`·`youtube`), 점수 근거(점수·z·배수·두 소스·YouTube 영상 수), 회사 관련성·겹친 회사 키워드, 아이디어 문장, 형식, fallback·이유. 그리고 네이버 호출 수, 못 읽은 그룹·후보, YouTube 후보 상태(스냅샷이 없거나 오래되면 unknown + 이유, 이때는 기본 그룹만 본다)
- **한계:** 추천 "품질"(실제로 만들 만한지, 영상이 잘 됐는지)은 **평가셋이 없어 재지 않는다**. 순위 규칙과 숫자 검증만 테스트한다. 나중에 게시 성과(콘텐츠 루프 4·5단계, `campaign_effect`)로 평가한다. 가중치(1.0·2.0)도 성과 자료가 없어 정한 값이다. 회사 관련성은 이름 겹침만 보며 뜻(예: 롤 = 게임/케이크)은 모른다. 한 글자 회사 키워드(`책`)는 넓게 겹칠 수 있다. 회사와 거리가 먼 소재(예: t1)도 회사 상품에 억지로 잇는 문장이 나온다(관련성 low 표시를 같이 본다)

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
