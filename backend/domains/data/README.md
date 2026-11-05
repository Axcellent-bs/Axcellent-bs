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
| `trend_candidates` | 사람 목록 없이 찾은 후보 키워드와 후보마다 출처(YouTube 태그·YouTube 제목·인기 검색어)·근거·네이버 급상승 판정(trend_scan과 같은 함수). 늘 붙는 말은 쌓인 스냅샷으로 자동 제외, 같은 대상 이름은 같은 영상 + 검색 추이 상관으로 자동 묶음(가까운 쌍은 제안만). 스냅샷이 없거나 오래되면 그 출처만 unknown + 이유 | YouTube 스냅샷 DB(인기 목록·제목, `youtube_store`, 읽기 전용), 인기 검색어 스냅샷 DB(`trending_store`, 읽기 전용), 네이버 검색어 트렌드(후보마다 1번, 별칭·묶음이 있으면 더). 설정: 회사 폴더 `trends.toml` `[candidates]`·`[trending]` |
| `trend_history` | 키워드 그룹별 네이버 월 단위 값(최근 24개월, 설정)과 달마다 "작년 같은 달 대비" 배수. **판정하지 않음**(급상승·계절 판정은 평가가 없어서). 자료 없음·못 읽음은 그 그룹만 unknown | 네이버 검색어 트렌드(`timeUnit=month`, 그룹마다 1번). 기본 키워드: 회사 폴더 `trends.toml`, 개월 수 `[history] months` |
| `youtube_velocity` | YouTube 영상 지금 속도(최근 두 스냅샷 사이 시간당 조회 증가)와 그 전 구간보다 빨라졌는지, 지금 속도 상위 N개(설정)와 태그·제목·채널 이름·길이. **판정하지 않음** | YouTube 스냅샷 DB(`youtube_store.recent_snapshots`). 상위 영상에 저장된 제목·길이가 없으면(회사 키워드 검색·추적으로 들어온 영상) YouTube `videos.list`로 한 번 받아 저장(영상 50개마다 1단위), 못 받으면 비우고 요약에 수를 적음. 개수: 회사 폴더 `trends.toml` `[velocity] top` |
| `trend_popular` | "지금 인기" 주제 순위(급상승 판정과 별개): 점수(코드) = 인기 영상 수 몫 + 지금 조회 속도 몫 + 인기 검색어 순위 몫(가중치 설정), 출처별 몫·근거, 주제 분류·`brand_safe`, 오늘 판정이 급상승이면 "지금 급등 중"(`rising_now`) | YouTube 스냅샷 DB·인기 검색어 스냅샷 DB(읽기 전용), LLM(주제 분류만, 묶어서 1번·하루 캐시). **네이버를 부르지 않는다**. 설정: `trends.toml` `[popular]` |
| `trend_insight_brief` | 기획에 넘기는 근거 있는 인사이트 3~7개(insight_id·한 줄·분류·광고 재료 여부·근거·타깃 연령대·상품·숏츠 형식·한계·참고용 예시 아이디어). 데이터는 기획안을 확정하지 않는다 | 기존 skill 결과를 코드로 묶음: trend_popular·오늘 급상승 판정·trend_age(후보 상위만)·오늘 저장된 promo_concepts·product_link(임베딩 기준 통과만). LLM은 한 줄만(묶어서 1번). 하루 캐시(날짜 + 규칙 버전) `backend/.data/insight_brief.db`. 설정: `trends.toml` `[brief]` |
| `domain_profile` | 회사 도메인 프로필: 업종·상품군·고객 축·분석 질문·씨앗 키워드(근거 문장). 고객사가 바뀌어도 사람이 키워드·카테고리를 손으로 정하지 않게 | 테넌트 설정 `[profile] about` + 회사 카탈로그(이름·카테고리·설명). LLM 한 번(정해진 스키마·목록 안), 코드 검증. 저장 재사용 `backend/.data/domain_profile.db`(소개·카탈로그·규칙 해시). 덮어쓰기 `[profile.override]` |
| `data_sources` | 데이터 출처 목록: 데이터 종류·업종 태그·연령/성별·카테고리 출처·주기·한도·키 이름·상태(사용 가능/키 필요)·공식 문서·승인 | 코드 표(`skills/data_sources.py`). 키 환경변수 유무로 상태, 쇼핑인사이트는 확인 호출 1번(저장 `backend/.data/source_probe.db`). 승인: 회사 설정 `[sources] approved` |
| `source_plan` | 수집 계획: 출처·이유·카테고리(실제 목록에서 확인)·키워드·연령 여부·호출 수·할당량·키가 있으면 얻는 근거 | 도메인 프로필 + 출처 목록 → LLM 한 번(목록 안에서만), 코드 검증·할당량 자르기. 저장 `backend/.data/source_plan.db`. 상한 `trends.toml` `[plan]`. 수집은 하지 않음 |
| `domain_subtopics` | 하위 주제 넓히기: 씨앗 키워드 → YouTube 한국 숏츠 → 함께 나온 말(코드) → 프로필 하위 주제 축으로 분류(LLM) | YouTube search.list(videoDuration=short) 씨앗마다 1번, 일주일 캐시 `backend/.data/subtopics.db`, 하루 상한 `[subtopics] daily_searches` 5번(수집 계획 YouTube 할당량에 합침) |
| `trend_age` | 연령대별 상승: 주제마다 연령대(설정)별로 따로 조회해 "그 연령대에서 평소 대비 몇 배"(trend_scan과 같은 판정 함수), 가장 크게 오른 연령대 | 네이버 검색어 트렌드(`ages` 조건, 주제 하나 = 연령대 수만큼, 하루 상한 200번·3일 캐시, 브리프·카드 우선 120번을 빼고 다른 skill은 80번까지, 네이버 전체 상한에서 300번은 다른 skill용으로 남김, 호출자별 사용량은 응답 `calls_by_caller`·`limits`), trend_popular(커넥터 없이), 오늘 판정 기록, 기획안 시즌 확인·카탈로그. LLM 없음. 설정: `trends.toml` `[age]` |
| `promo_concepts` | 광고·홍보 숏츠 **예시 아이디어(참고용)** 3~5개(확정 기획은 기획팀 몫): 타깃 연령대와 근거 숫자(trend_age, 없으면 미정 + 이유)· 제목·컨셉·길이(15~60초)·콘티(길이에 맞는 컷 수)·빌려 온 숏츠 형식과 그 평균 조회수·빌려 온 트렌드(없어도 됨)·맞는 이유·등장 상품·주의사항, 코드 경고(상표·초상권 확인 필요), 버린 초안과 이유, 쓴 재료·시즌 확인 키워드 | shop-api 상품·카테고리, shorts_stats(형식별 평균 조회수), trend_popular(게임·연예·스포츠·K-pop·음악·IT만), 카탈로그 카테고리·상품 핵심어 월별 흐름(trend_history, 키워드마다 네이버 1번·상한 20·하루 캐시), LLM(초안 1번). 하루 캐시·기록 `backend/.data/promo_concepts.db`(캐시 키 = 날짜 + 규칙 버전: 재료 거르기 설정 `[promo]`·분류 목록·프롬프트의 해시. 규칙이 바뀌면 다음 요청에서 다시 만든다). 설정: `trends.toml` `[promo]` |
| `shorts_stats` | YouTube 숏츠 통계(카테고리별·형식별): 숏츠 수·평균 조회수·평균 시간당 조회 증가·조회수 상위 3개, 표본 부족은 unknown | YouTube 스냅샷 DB(길이 `video_details`), 숏츠 검색(`search.list`, 하루 1번·상한 8번, `backend/.data/shorts.db`), LLM(카테고리·형식 분류, 묶어서·저장). 설정: `trends.toml` `[shorts]` |
| `content_recommend` | 지금 만들 만한 영상 소재(급상승만, 최대 N개): 순위·점수 근거(z·배수·두 소스)·출처·주제 분류·묶음(회사 상품과 연결된 소재·일반 화제)·연결할 실제 상품(근거)·회사 관련성(상품 연결로, 표시만)·밈 형식·영상 아이디어 1~2문장·형식(숏폼·롱폼)·fallback. 추천 안 함 분류(회사 설정)는 참고용 + 이유로 따로. 콘텐츠 루프 2단계 | trend_scan(회사 기본 키워드 그룹)·trend_candidates(후보 전체)와 같은 네이버 요청(하루 캐시 같이 씀), 회사 상품 목록(shop-api `GET /api/shop/products`·`/categories`), LLM(아이디어 문장·밈 형식·주제 분류만), 임베딩(설정이 있을 때만). 설정: 회사 폴더 `trends.toml` `[recommend]` |
| `answer_planning_question` | 기획이 되묻는 근거 질문의 답(answered·needs_human·unknown). 기획만 부른다 | data가 보낸 요청의 기록(`planning_questions.py`, 프로세스 안). 아직 요청을 보내는 코드가 없어 지금은 늘 unknown. 사람 답은 `human_answer_request`로 planning `receive_planning_answer`에 보낸다 |
| `campaign_effect` | 캠페인 기간 주문 방향(늘었음·같음·줄었음)·증분(실제 − 지난 7일 같은 시간대 평균), 캠페인 쿠폰 조회 시점 누적 사용률. 매출은 매출 지표가 생기면 같은 방법(지금은 확인할 수 없음) | shop-api `GET /api/shop/metric-series`(`order_count`), `GET /api/shop/coupons?campaign_id=` |

### skill별 평가와 알려진 한계 (2026-11-03 기준)

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
| `trend_candidates` | 판정은 trend_scan과 같은 함수(위 평가). 후보 뽑기·자동 제외·자동 묶기는 평가 없음(기준값은 2026-11-02·03 실데이터로 본 값) | 제목 구는 형태소 분석이 없어 조각(`안 되는`)이 섞인다. 자동 제외는 스냅샷이 하루치라 날짜 기준이 아직 안 걸리고, 게임 카테고리가 80%라 `게임`·`치지직`은 수동 목록이 필요하다. 자동 묶기는 영문 이름(lol·minecraft)을 못 찾고 상위 후보끼리만 본다. 인기 검색어는 오늘, 네이버 판정은 어제까지라 오늘 처음 뜬 검색어는 평범·자료 없음일 수 있다. 스냅샷이 쌓이기 전 날짜로 판정하면 그 출처는 unknown |
| `trend_history` | 없음(판정하지 않음) | 그룹마다 상대값이라 그룹끼리 높이를 비교할 수 없다. 계절 반복 판정은 평가 케이스부터 만들어야 한다 |
| `youtube_velocity` | 없음(판정하지 않음) | 평소 속도 기준이 없어 빠른 순서만 보여 준다. 스냅샷 간격이 짧으면 속도가 흔들린다 |
| `trend_popular` | 없음 | 평가셋이 없어 "인기 순위가 맞는지"를 재지 않는다. 가중치(1.0·1.0·1.0)는 성과 자료가 없어 정한 값이다. 주제 분류 정확도도 재지 않는다 |
| `trend_insight_brief` | 없음 | 평가셋이 없어 인사이트가 맞는지 재지 않는다. 순서 가중치(`[brief]`)는 성과 자료가 없어 정한 값이다. 근거 숫자는 기존 skill 값 그대로이고 한 줄만 LLM(숫자 검증) |
| `trend_age` | 없음 | 평가셋이 없어 "연령대 판정이 맞는지"를 재지 않는다. 판정 함수는 trend_scan과 같다(평가셋 `eval/trend`는 연령대 없이 만든 것). 값은 요청마다 최댓값이 100인 상대값이라 연령대끼리 절대량은 비교하지 않는다. 검색량이 적은 연령대는 네이버가 값을 주지 않아 unknown |
| `promo_concepts` | 없음 | 평가셋이 없어 기획안 품질을 재지 않는다. 👍/👎 피드백이 쌓이면 그것을 평가셋으로 쓴다(피드백 저장은 safety 레지스트리 등록이 필요해 아직 없음). 코드는 상품·트렌드·숫자·형식·콘티 수만 검증한다 |
| `shorts_stats` | 없음 | 인기 영상 상위 200개 안(과 조회수 순 검색 결과)에서의 비교라 이미 뜬 영상끼리 비교한다. 숏츠 여부는 길이로 판단한다. 형식 분류 정확도는 재지 않는다 |
| `content_recommend` | 상품 연결 기준만 보정셋 `eval/product_link/`(#403, 관문 아님) | 추천 품질은 나중에 게시 성과로 잰다. 상품 연결은 임베딩 설정이 없으면 글자 일치만 본다. 임베딩 기준(1등만, 0.65)은 **임시 기준**이다. **밈 형식·주제 분류 정확도는 평가셋이 없어 재지 않는다**. 인기 검색어에서만 나온 소재는 밈 형식이 늘 unknown |
| `answer_planning_question` | 없음 | 아직 data가 요청을 보내는 코드가 없어 늘 unknown이다 |
| `campaign_effect` | 있음: `eval/campaign/`(기존·여러 날, 개발 방향 1.0, holdout 0.9167·0.8) | 매출 지표가 없어 매출은 unknown. 7일 넘는 캠페인은 unknown. 주문이 아주 적은 가게의 새벽 기록 공백은 못 잡는다 |

- 숫자는 코드가 계산한다(`metrics/`, `impact.py`, `detect.py`). LLM은 계산이 끝난 숫자로 설명 문장만 쓴다(`explain.py`)
- 주기 감시(`monitor.py`)는 `anomaly_detection`과 같은 판정 함수로 최근 구간을 보고 `metric.anomaly` 이벤트를 낸다. 사건은 열지 않는다(`context_id` 없음)
### 데이터 출처: 콘텐츠 루프 1·2단계 (2026-11-03 기준)

사람이 손으로 넣은 키워드 목록 없이 돈다. 사람 설정은 **기준값(임계값·개수)**과 **자동 결과를 덮어쓰는 선택적 수동 표**만 남겼다.

| 단계 | 출처 | 무엇을 | 어디서 | 외부 호출 |
| --- | --- | --- | --- | --- |
| 후보 | YouTube 인기 목록 태그 | 정리한 태그, `min_videos`개 이상 영상 | YouTube 스냅샷(`youtube_collect`) | 수집기만(1시간마다 약 7단위) |
| 후보 | YouTube 인기 영상 제목 | 2~3어절 구, `min_videos`개 이상 영상 | YouTube 스냅샷 `video_titles` | 같음(예전 스냅샷은 `--titles`로 채움) |
| 후보 | 인기 검색어 | Google Trends 공개 RSS 검색어(피드 순위·트래픽 표시값·관련 뉴스 제목 최대 3) | 인기 검색어 스냅샷(`trending_collect`) | 수집 1번에 피드 1번 |
| 자동 제외 | 쌓인 YouTube 스냅샷 전체 | 여러 날·여러 카테고리에 고르게 나오는 태그·구(문서 빈도) | `youtube_store.popular_history` | 없음 |
| 자동 묶기 | 같은 영상 + 네이버 일별 추이 | 함께 나온 영상 수, 로그 상관 | 스냅샷 + 이미 받은 판정 시계열 | 묶음마다 네이버 1번 |
| 급상승 판정 | 네이버 검색어 트렌드 | 최근 56일 대비 마지막 7일(trend_scan 함수) | `connectors/naver_datalab.py` | 후보마다 1번(+ 별칭·묶음) |
| 주제 분류 | 소재 이름 + 근거 글(대표 영상 제목 또는 인기 검색어 관련 뉴스 제목, 최대 3) | 정해진 9개 분류 중 하나(LLM, 목록 밖은 기타). 추천 안 함 분류는 참고용으로 | YouTube 스냅샷 `video_titles`, 인기 검색어 스냅샷 `trending_news` | 실행마다 LLM 1번(소재를 묶어서, 최대 20개씩), 주제별 하루 캐시 |
| 상품 연결 | 회사 상품 목록 | 판매 중 상품·카테고리 이름, 글자 일치 + 임베딩 유사도(1등만, 임시 기준) | shop-api `GET /api/shop/products`·`/categories` | shop-api 2번, 임베딩 1번(설정 있을 때) |
| 밈 형식 | 대표 영상 제목(최대 5) | 정해진 6개 형식 중 하나(LLM, 목록 밖은 기타) | YouTube 스냅샷 `video_titles` | 소재마다 LLM 1번(YouTube 근거가 있을 때) |
| 아이디어 문장 | 판정 숫자(배수) + 연결 상품 + 밈 형식 | 1~2문장(LLM, 숫자·상품 검증) | — | 소재마다 LLM 1번 |

**남아 있는 수동 설정(덮어쓰기용)** — 회사 `trends.toml`

| 설정 | 하는 일 | 왜 남겼나 |
| --- | --- | --- |
| `[[override.groups]]` 키워드 그룹 덮어쓰기 (비어 있어도 됨) | 있으면 trend_scan 기본 그룹을 프로필 씨앗 대신 이 그룹으로 바꾼다. trend_history·content_recommend는 이 그룹만 본다 (캣북스는 4개를 남겨 둠) | 회사가 꼭 지켜볼 말(고양이·책)을 사람이 정하고 싶을 때. 옛 이름 `[[groups]]`도 같은 덮어쓰기로 읽는다 |
| `[candidates] exclude_tags` | 늘 빼는 일반 태그 | 자동 제외가 못 잡는 말(게임·치지직: 인기 영상 80%가 게임 카테고리라 카테고리 2개에만 나옴)이 있다. 끄면 `게임`이 상위 5에 들어온다 |
| `[candidates] keep_tags` (비어 있음) | 자동 제외에서 살릴 이름 | 자동 제외가 실제 소재를 뺄 때 사람이 되살리는 칸 |
| `[candidates.aliases]` 별칭 표 | 같은 대상 이름 묶기 | 자동 묶기가 영문 이름(lol·minecraft·league of legends, 상관 0.56~0.73)을 못 찾는다 |
| `[recommend] company_keywords` | 수동 회사 키워드(겹치면 관련 있음) | 임베딩 기준이 임시라 보조로 둔다 |
| `[recommend] exclude_categories` | 추천 안 함 주제 분류(기본: 뉴스·사건·정치·사회·기부·재난·인물) | 브랜드 안전은 회사가 정할 일이다. 분류는 LLM이 정해진 목록에서만 고른다 |
| `[recommend] min_similarity`·`link_top_only`·`min_margin` | 임베딩 상품 연결 기준(0.65, 1등만, 0) | 보정셋으로 고른 **임시 기준**(아래 "상품 연결") |
| `[recommend] company_intro` | 회사 소개 한두 줄 | 아이디어 문장의 보조 정보(상품 이름은 연결된 실제 상품만 쓴다) |

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

## 기획 협업

흐름: **데이터(인사이트 + 근거) → 기획(제안 + 실행 근거 + 목표) → 사람 확인 → 마케팅 실행 → 데이터(성과 측정) → 기획(결과 기록)**. 데이터는 근거 있는 인사이트를 넘기고 성과를 잴 뿐, 제안을 확정하지 않는다.

### 기획이 부를 skill 2개

| skill | 언제 | 요청 | 응답 핵심 |
|---|---|---|---|
| `trend_insight_brief` | 제안을 만들기 전에 근거 있는 인사이트를 받을 때 | `max_insights`(3~7, 비우면 회사 설정 5), `regenerate` | `insights[]`: `insight_id`·한 줄·분류·광고 재료 여부·근거(YouTube·Google·네이버, 링크·시각)·타깃 연령대와 연령대별 배수 전체(`age_bands`)·상품(임베딩 기준 통과만)·숏츠 형식(단계·표본 수)·한계·예시 아이디어(참고용)·`fact_ids`. 기회 카드 `opportunities[]`, 숫자 목록 `facts`, `schema_version`·`snapshot_id`·`generated_at` |
| `campaign_effect` | 마케팅이 실행한 뒤 성과를 기록할 때 | `window_from`·`window_to`(필수), `campaign_id`, **`insight_id`·`proposal_id`(선택)** | 주문 증분·`incremental_orders`·판매액(`sales`)·쿠폰 누적 사용률(모르면 unknown + 이유), 숫자 목록 `facts`, `schema_version`·`generated_at`, 요청의 `insight_id`·`proposal_id` 그대로 |

둘 다 grade read(조회만). 요청 가능: `trend_insight_brief`는 planning·orchestrator·dashboard-api, `campaign_effect`는 카드의 `allowed_requesters`.

### 합의한 원칙 (기획과)

1. **숫자는 facts**: 데이터 응답에 나오는 숫자는 모두 `facts`에 있다(`fact_id`·`label`·`value`·`unit`·`source`·`link`·`period`·`sample`). 브리프는 `facts`(`DataFact`)에 모으고 인사이트·기회 카드가 `fact_ids`로 가리킨다. `campaign_effect`는 `facts`(`CampaignFact`)에 주문·쿠폰·판매액 숫자를 모두 둔다
   - 숫자는 코드가 낸다. LLM은 분류와 문장만 쓴다. 문장(한 줄·요약) 속 숫자가 facts에 없으면 고정 문장으로 바꾼다
   - 모르는 숫자는 facts에 넣지 않고 칸은 None + 이유다. 0으로 채우지 않는다
2. **기획 숫자는 target**: 목표 숫자는 기획이 정해 기획 쪽 `target`에 적는다. 데이터의 `target_views_reference`는 참고값일 뿐이다(하위 주제 숏츠 표본이 5개 미만이면 None + "표본 부족"). 기획이 정할 것은 카드의 `planning_decides`에 있다
3. **ID 사슬**: `insight_id`(날짜 없음, 테넌트 + 주제 + 카테고리) / `opportunity_id` → 기획 `proposal_id` → `campaign_effect` 요청에 둘 다 → 응답에 그대로 → 기획 결과 기록. 숫자 하나는 `fact_id`, 시점은 `generated_at`·`snapshot_id`, 쓴 수집 계획은 `plan_id`. 예전 날짜 id는 `legacy_insight_id`
4. **schema_version**: 응답 모양 버전(브리프 `trend_insight_brief.v3`, campaign_effect `campaign_effect.v2`). 칸이 늘면 올린다. 계약은 추가만 하므로 예전 칸은 그대로 읽힌다
5. **재무로 넘길 숫자**: `gmv_krw` = `sales.incremental_gross_krw`, `orders` = `incremental_orders`(둘 다 증분, 대응표는 campaign_effect 절)

### 예시 (2026-11-04 실제 데이터, 로컬 실행. facts·안정 id를 넣기 전 응답이라 `insight_id`가 날짜 형식)

`trend_insight_brief` 요청:

```json
{"skill": "trend_insight_brief", "input": {"max_insights": 5}}
```

응답 (인사이트 하나만 줄여 보임):

```json
{
  "status": "known",
  "day": "2026-11-04",
  "rules_version": "04a3a6729e42",
  "cached": false,
  "summary": "인사이트 5개 (광고 재료 가능·미정 5개, 참고용 0개). 1위: t1.",
  "insights": [
    {
      "insight_id": "ins_20261104_1ee36d7f",
      "rank": 2,
      "topic": "리그오브레전드",
      "line": "YouTube 인기 영상 27개, 지금 시간당 조회 79390, 구독자 대비 조회 최대 0.82배, 네이버 평소 대비 0.97배, 타깃 10대는 평소 대비 1.16배.",
      "line_status": "llm",
      "category": "게임",
      "ad_material": true,
      "ad_material_reason": "실존 이름·상표가 든 주제라 분위기·형식만 빌릴 것",
      "reference_only": false,
      "real_entity": true,
      "entity_names": [
        "리그오브레전드"
      ],
      "target_age": "10대",
      "target_age_ratio": 1.16,
      "target_age_status": "normal",
      "products": [],
      "products_reason": "임베딩 유사도 0.65 이상(1등만)인 상품이 없음",
      "shorts_format": null,
      "shorts_format_reason": "이 주제 근거 영상 중 숏츠가 없음",
      "example_ideas": [],
      "score": 1.58,
      "evidence": {
        "youtube_videos": 27,
        "youtube_views_per_hour": 79389.5,
        "youtube_views_per_subscriber": 0.82,
        "youtube_at": "2026-11-04T00:58:39Z",
        "google_rank": null,
        "naver_ratio": 0.97,
        "naver_status": "normal",
        "naver_until": null,
        "links": [
          "https://www.youtube.com/watch?v=qBrPgSt9mzI"
        ],
        "missing": [
          "Google: 인기 검색어에 없는 주제"
        ]
      },
      "limits": [
        "평가셋이 없어 인사이트가 맞는지 재지 않는다",
        "네이버 값은 요청마다 최댓값이 100인 상대값이다"
      ]
    },
    "… (나머지 인사이트)"
  ]
}
```

`campaign_effect` 요청 (기획이 제안에 적어 둔 `insight_id`·`proposal_id`를 같이 보냄):

```json
{
  "skill": "campaign_effect",
  "input": {
    "campaign_id": null,
    "window_from": "2026-11-03T00:00:00Z",
    "window_to": "2026-11-04T00:00:00Z",
    "insight_id": "ins_20261104_628b49d9",
    "proposal_id": "prop_example_1"
  }
}
```

응답 (줄임. 이날은 지난 7일 주문 기록에 공백이 많아 주문 증분이 unknown이고, campaign_id가 없어 쿠폰도 unknown이다. 0으로 채우지 않는다):

```json
{
  "campaign_id": null,
  "window_from": "2026-11-03T00:00:00Z",
  "window_to": "2026-11-04T00:00:00Z",
  "orders": {
    "status": "unknown",
    "unknown_reason": "기준선에 쓸 지난 구간이 없음 (1일 전: 기록 공백 의심 (288칸): 결제 시도 0인 5분 칸이 288칸…"
  },
  "revenue": {
    "status": "unknown"
  },
  "coupons": {
    "status": "unknown",
    "unknown_reason": "campaign_id가 없어 캠페인 쿠폰을 찾을 수 없음"
  },
  "summary": "…",
  "insight_id": "ins_20261104_628b49d9",
  "proposal_id": "prop_example_1"
}
```

### insight_id 사용법

1. 기획은 `trend_insight_brief` 응답에서 쓸 인사이트의 `insight_id`를 제안에 적는다. **날짜 없는 id**(`ins_<테넌트 + 정규화 주제 + 카테고리 해시>`)라 같은 주제는 날이 바뀌어도 같다. 시점은 브리프 `generated_at`·`snapshot_id`로 본다. 예전 id(날짜 + 주제, `ins_20261104_…`)는 `legacy_insight_id`로 함께 준다
2. 마케팅이 실행한 뒤 기획이 `campaign_effect`를 부를 때 그 `insight_id`와 기획 쪽 `proposal_id`를 함께 보낸다
3. 응답에 두 id가 그대로 돌아오므로, 기획은 그 결과를 자기 쪽 기록(제안·결과)에 이어 붙인다

### 데이터가 하지 않는 것

- **제안 확정**: 인사이트와 예시 아이디어(`promo_concepts`, 참고용)만 준다. 무엇을 할지는 기획이 정하고 사람이 확인한다
- **기획 쪽 저장**: `insight_id`·`proposal_id`·결과를 데이터가 저장하지 않는다. `campaign_effect`는 받은 id를 돌려줄 뿐이다(읽기 전용)
- **마케팅 실행**: 게시·발행·쿠폰 발급은 마케팅(과 safety 승인)이 한다

### 외부 호출 없이 확인하기 (#4, `connectors/replay.py`)

로컬 확인에서 네이버·YouTube 할당량을 쓰지 않으려고, 응답을 저장해 두고 다시 쓰는 모드가 있다. **단위 테스트(`uv run pytest`)는 원래 가짜 커넥터·가짜 LLM만 쓰므로 외부를 부르지 않는다.** 이 모드는 서버나 확인 스크립트를 실제 데이터 모양으로 돌릴 때 쓴다.

| `DATA_EXTERNAL_MODE` | 동작 |
|---|---|
| `live`(기본) | 실제로 부른다 |
| `record` | 실제로 부르고 응답 본문을 `DATA_REPLAY_DIR`(기본 `backend/.data/replay/`, 커밋 안 함)에 저장한다. **키·토큰은 헤더로만 가서 파일에 들어가지 않는다** |
| `replay` | 부르지 않고 저장된 응답만 쓴다. 할당량·하루 호출 수를 세지 않는다. 저장된 것이 없으면 그 호출만 `REPLAY_MISS`(unknown + 이유, 지어내지 않음) |

```bash
cd backend
# 1) 한 번만 실제로 부르며 저장 (몇 번 부르는지 아래 표로 먼저 세고 진행)
DATA_EXTERNAL_MODE=record bash scripts/run_all.sh
# 2) 그 뒤 확인은 저장된 응답으로 (할당량 0)
DATA_EXTERNAL_MODE=replay bash scripts/run_all.sh
```

저장 키는 서비스 + 주소 + 요청 본문(네이버) 또는 쿼리(YouTube)라, 같은 요청이면 같은 파일을 쓴다. 날짜가 바뀌면 요청 기간이 달라져 새로 저장해야 한다. Google 인기 검색어 RSS는 키·할당량이 없어 대상이 아니다.

**외부 호출이 필요한 확인은 몇 번 쓰는지 먼저 세고 진행한다.** skill별 최대 호출(설정 기본값):

| skill | 네이버 | YouTube |
|---|---|---|
| `trend_scan` | 그룹마다 1번 (같은 요청은 하루 캐시) | 0 |
| `trend_candidates` | 후보마다 1번 + 별칭 수 | 0 |
| `trend_age` | 주제 × 연령대(기본 10 × 5 = 50), 하루 상한 200(우선 아닌 skill은 80) | 0 |
| `trend_popular` (화면 경로) | 0 | 제목 videos.list ≤ 2번, 구독자 channels.list ≤ 2번, 보충 검색 ≤ 6번(100단위씩) |
| `shorts_stats` | 0 | 검색 하루 ≤ 8번(100단위씩, 저장 재사용), 다시 보기 videos.list 50개마다 1단위 |
| `promo_concepts` | 시즌 키워드 ≤ 20번(하루 캐시) + 연령대(예시 트렌드 수 × 5) | shorts_stats와 같다 |
| `trend_insight_brief` | trend_age(후보 × 5, 상한 공유) | trend_popular·shorts_stats와 같다 |

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
- 기본 키워드 그룹은 코드에 두지 않는다: `config/tenants/<회사>/trends.toml`(`tenant.toml`의 `trends`) → `scripts/tenant_env.py`가 `DATA_TREND_CONFIG`로 넘긴다. 기본 그룹은 도메인 프로필의 씨앗 키워드에서 온다(씨앗 하나 = 그룹 하나, 이름·키워드 = 씨앗, 상한 `[scan] max_seed_groups` 기본 5. 그룹마다 네이버 1번).
  - 우선순위: 요청 그룹 > 사람 덮어쓰기(`[[override.groups]]`) > 프로필 씨앗. 프로필을 못 얻으면(LLM 없음·씨앗 없음) 덮어쓰기가 있으면 그것을, 없으면 지어내지 않고 `unknown` + 이유(`outcome="unknown"`)
  - 코드: `resolve_groups(requested, env, seed_provider)` — 테스트는 `seed_provider`로 LLM 없이 씨앗을 준다
  - **trend_history·content_recommend도 같은 우선순위**를 쓴다. trend_history는 못 정하면 `outcome="unknown"` + 이유, content_recommend는 요청 그룹 칸이 없어 덮어쓰기 > 프로필 씨앗이고, 둘 다 없으면 그룹 판정 없이 YouTube 후보만 본다
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

- **기획 연결(#531)**: 요청에 선택 칸 `insight_id`(trend_insight_brief)·`proposal_id`(기획 제안)를 보내면 결과에 그대로 돌려준다. 기획이 결과를 기록할 때 인사이트·제안과 잇는 용도이고, 데이터는 저장하지 않는다(읽기 전용)

- 입력: `campaign_id`(선택), 캠페인 시작·끝(`window_from`·`window_to`). 출력: 주문·매출·쿠폰 각각 "앎(값·근거)" 또는 "확인할 수 없음 + 이유", 요약 문장
- 주문(`skills/campaign_effect.py`, 코드 계산): 캠페인 기간 `order_count` 합계(실제)와 기준선 지난 구간 합계의 평균(기대). **증분 = 실제 − 기대**(평가 채점기와 같은 정의). 방향은 기대를 평균으로 한 포아송 꼬리확률(`metrics/judge.py`의 `poisson_upper`·`poisson_lower`)이 0.001보다 작으면 늘었음·줄었음, 아니면 같음. 지표는 커넥터 `metric_series`로 읽는다
- 매출: 카탈로그 `revenue_metric`(회사 카탈로그 한 줄)이 있으면 같은 방법으로 합계·기대·증분을 낸다(방향은 기대 대비 ±10% 임시 기준, 평가로 정하지 않음). **캣북스는 비어 있다**: shop-api metric-series에 매출이 없어 "확인할 수 없음 + 이유(지표 없음)"가 지금 정상이다. 판매액은 아래 `sales` 칸을 본다(기존 `revenue` 칸은 campaign 평가가 채점하는 칸이라 그대로 둔다)
- **판매액 `sales`·근거 `facts`**: shop-api 주문 기록(`GET /api/shop/orders?since&until`, 커넥터 `orders`)에서 결제 완료 주문의 금액을 코드가 센다. 정의·기준선·공백·한계는 "campaign_effect 판매액" 절
- **재무 칸 대응표** (재무 `record_campaign_result` `CampaignResultInput`):

  | 재무 칸 | 뜻 (재무 계약) | data 칸 |
  |---|---|---|
  | `gmv_krw` | 캠페인 때문에 늘어난 판매액 | `sales.incremental_gross_krw` (증분 주문 금액, 할인 전). 0보다 작으면 재무 칸은 0 이상이라 넣지 않고 판단을 미룬다. 범위는 `incremental_low_krw`~`incremental_high_krw` |
  | `coupon_krw` | 끝난 회사 부담 쿠폰 금액 | `sales.period.discount_krw` (구간 결제 완료 주문의 쿠폰 할인 합). 캠페인 쿠폰만이면 쿠폰 귀속 주문의 할인 합이지만 지금 칸은 구간 전체 |
  | `orders` | 주문 수 | **확정: `incremental_orders`** (증분 주문 수 = 주문 판정의 실제 − 기대, 정수 반올림. `gmv_krw`가 증분이라 같은 뜻으로 맞춤). 주문 판정을 모르면 None이라 넣지 않는다. 음수면 재무 칸이 0 이상이라 넣지 않고 판단을 미룬다 |
  | `source` | 숫자의 출처 | `"data.campaign_effect"` |
  | (없음) | — | `sales.direct_attributed_krw` (캠페인 쿠폰 주문 판매액 합), `sales.direction`(up·down·uncertain) |
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

### campaign_effect 판매액 (`sales`, 재무 캠페인 손익용)

- **출처**: shop-api `GET /api/shop/orders?since&until`(주문 접수 시각 [since, until)). 판매자 DB를 직접 읽지 않는다
- **세는 주문**: 결제 완료 주문(성공한 결제가 하나라도 있는 주문). 결제 대기·결제 실패 주문은 세지 않는다
- **칸 정의** (원 단위 정수): 주문 금액 `gross_krw` = 상품가 × 수량 합(할인 전) · 쿠폰 할인 `discount_krw` · 결제 금액 `paid_krw` = 성공한 결제 금액 합(배송비 포함, 할인·포인트 뺀 현금) · 환불 `refund_krw`(조회 시점 누적) · 결제 완료 주문 수
- **기준선**: 주문 수 판정과 같은 구간(1일 이하 = 지난 7일 같은 시간대, 여러 날 = 7·14일 전 같은 요일·시간대)의 날 평균. 주문 판정에서 빈 구간·기록 공백으로 뺀 날은 판매액에서도 뺀다(`baseline_note`)
- **증분**: 구간 − 기준선 평균. **하한·상한**: 구간 − 기준선 날 중 가장 많은 날 / 가장 적은 날
- **최소 기준선**: 기준선 날이 `DATA_CAMPAIGN_SALES_MIN_BASELINE_DAYS`(기본 3)보다 적으면 증분·하한·상한·방향은 unknown + "기준선 부족(N일, 최소 M일)". 기간 판매액(`period`)·쿠폰 귀속은 그대로 준다. **여러 날 캠페인은 기준선이 7·14일 전 2구간뿐이라 기본값에서는 늘 기준선 부족이다**(최소를 2로 낮추면 낸다)
- **방향**: 하한 > 0이면 늘었음, 상한 < 0이면 줄었음, 범위가 0에 걸치거나 기준선 날이 1개면 확실하지 않음(`uncertain`). 주문 수 판정(포아송)을 `orders_direction`·이유에 함께 적는다
- **쿠폰 귀속** `direct_attributed_krw`: 캠페인 쿠폰(`GET /api/shop/coupons?campaign_id=`)을 쓴 결제 완료 구간 주문의 주문 금액 합. campaign_id·쿠폰이 없으면 None + 이유
- **공백**: 주문 판정이 기록 공백(결제 시도 0인 5분 칸이 이어짐, 구간 기록 없음)이면 "결제 기록 공백: …"으로 unknown(기존 기록 공백 규칙 그대로). 구간 주문이 모두 결제 대기여도 unknown. 0으로 채우지 않는다
- **근거 숫자** `facts`: 금액마다 label·value·unit(KRW·건)·source(shop-api)·period·sample
- **평가**: `eval/campaign_revenue/`(방향 정확도·증분 오차율·공백 unknown·쿠폰 귀속), 관문 `tests/test_campaign_revenue_eval_gate.py`
- **한계**: 광고 외 요인(다른 날의 행사·시즌·트래픽 변화)은 구분하지 못한다. 기준선 날이 적으면(공백으로 빠짐) 범위가 좁거나 낼 수 없다. 결제 금액은 배송비가 들어가 주문 금액보다 클 수 있다

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

- **추적 영상 줄이기**(`youtube.toml` `[tracking]`): 추적 대상은 인기 목록·검색에서 마지막으로 본 시각이 `drop_after_hours`(기본 48)시간 안인 영상이다. 예전에는 "마지막 저장 시각"으로 골라, 추적한 영상이 매번 다시 저장되며 영원히 남아 계속 늘었다(359→400개). 브리프(근거 영상·기회 카드 근거 숏츠, 최근 2일)에 쓰는 영상은 유지하고 먼저 넣는다. 상한 `max_tracked`(기본 500). 로그에 "추적에서 뺀 수"(지난 실행엔 있었고 이번에 뺀 영상)
- **보관**: `retention_days`(기본 30)일이 지난 스냅샷 줄은 한국 날짜 하루 1번 지운다(`prunes` 표에 그날 지운 줄 수, 0 = 안 지움). 제목·채널·길이·썸네일 표는 그대로

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
- `run_all.sh`가 data 서버를 띄울 때 `--every 3600`으로 같이 띄운다(로그 `.logs/trending-collect.log`, `run_all.sh stop`이 같이 끈다). 켤 때 한 번 바로 모으고 그 뒤 1시간마다

### trend_candidates (후보 → 네이버 급상승)

YouTube·인기 검색어에서 뜨는 소재가 검색에서도 뜨는지 본다. 후보 뽑기·자동 제외·자동 묶기는 **코드 규칙**이고 LLM을 쓰지 않는다.

1. 스냅샷: YouTube 스냅샷 DB(`DATA_YOUTUBE_DB`)에서 인기 목록을 받은 가장 최근 시각의 인기 영상(순위 순, 제목 포함). 파일을 읽기 전용으로 연다(없으면 만들지 않음)
   - **지난 기간**(요청에 `end_date`가 있음)은 그 판정 마지막 날(한국 날짜)이 끝나기 전(다음 날 0시 KST까지)의 가장 최근 스냅샷을 쓰고, 나이(`max_age_hours`)도 그 시각 기준으로 잰다. 그때 스냅샷이 없으면 YouTube 후보는 unknown + "그 시점(… 0시 KST) 이전 YouTube 스냅샷이 없음"이고 네이버는 부르지 않는다. `end_date`를 비우면(지금) 가장 최근 스냅샷을 지금 기준으로 본다. 인기 검색어 스냅샷도 같은 규칙이다
   - content_recommend도 같다: 지난 기간이면 그때 스냅샷만 쓰고, 없으면 그 출처만 unknown(기본 키워드 검색 판정은 그대로)
   - 2026-11-02 기준 YouTube 스냅샷은 11-02 10:45 KST부터, 인기 검색어 스냅샷은 11-02 23:54 KST부터 있다(쌓이면 풀림)
2. 태그 정리: 앞뒤 공백·`#` 제거, 영문 소문자(`T1`·`#t1 `·` t1` → `t1`). 한 영상에 같은 태그가 여러 번 붙어도 한 번만 센다
3. **YouTube 제목 구**(`[candidates] titles = true`, #382): 인기 영상 제목을 공백·문장부호로 나눠(형태소 분석 없음, 아포스트로피는 붙여 읽음) 이어진 2~3어절 구를 만들고, `min_videos`개 이상 영상 제목에 나온 구만 쓴다. 더 긴 구와 영상 집합이 같은 짧은 구는 그 조각이라 뺀다. 태그 이름과 공백만 다르면(리그 오브 레전드 = 리그오브레전드) 그 태그 후보에 합치고, 아니면 제목만으로 후보가 된다. 센 구는 `youtube.matched_phrases`
   - 제목은 `youtube_store`의 `video_titles` 표(영상마다 한 줄)에 둔다. `snapshots` 표 모양은 바꾸지 않았다(돌고 있는 예전 수집기가 깨지지 않게). **새 코드로 수집기를 다시 켜야 새 스냅샷의 제목이 쌓인다.** 그 전 스냅샷은 `youtube_collect --titles`로 채운다
   - 채널 이름·길이(초)는 `video_details` 표에 둔다(`videos.list`에 `contentDetails`를 더해 받음, 할당량은 그대로). 예전 스냅샷은 `youtube_collect --details`로 채운다. 길이는 숏츠 구분에 쓴다
4. 별칭 묶기(수동 표, 덮어쓰기용): 회사 별칭 표(`[candidates.aliases]`, `대표 이름 = [별칭…]`)로 별칭을 대표 이름으로 바꾼다(`lol` → `리그오브레전드`, `티원` → `t1`). 나온 영상 수는 **영상 기준으로 중복 없이** 합친다. 별칭 하나는 대표 이름 하나에만 쓰고(앞에 적은 것), 대표 이름은 다른 이름의 별칭이 될 수 없다. 표에는 **뜻이 하나인 이름만** 넣는다(뜻이 여럿인 줄임말 `롤`·`마크`·`메이플`을 넣으면 네이버 그룹에 다른 뜻의 검색량이 섞인다)
5. **자동 제외**(`[candidates.auto_exclude]`, #382): 쌓인 인기 목록 전체(`popular_history`, 판정 시각까지)에서 이름마다 나온 영상 비율·카테고리 수·나온 날 비율을 재서 셋 다 기준 이상이면 늘 붙는 말로 보고 뺀다(문서 빈도). 기준: `min_doc_ratio` 0.02, `min_categories` 3, `min_day_ratio` 0.5. 수동 `exclude_tags`는 그대로 늘 빼고(덮어쓰기용), `keep_tags`는 자동 제외에서 살린다. 빠진 이름은 `auto_excluded`(이번 스냅샷 영상 수·쌓인 영상 수·비율·카테고리·날짜·이유 한 줄, 수동 목록에도 있으면 같이 보인다), 잰 범위는 `exclusion_basis`
   - 2026-11-02 스냅샷(영상 275개, 1일)으로 본 값: 카테고리 3개면 스트리머·트위치만 잡힌다. 2개로 낮추면 로블록스·피파 같은 실제 소재까지, 비율 0.01이면 사람 이름(강지)까지 빠져 3·0.02로 뒀다. 날짜 기준은 스냅샷이 하루치뿐이라 아직 거르지 못한다(쌓이면 의미가 생김)
6. 후보: `min_videos`개 이상 인기 영상에 붙은(제목에 나온) 이름 중 제외(수동·자동)를 빼고, 나온 영상 수 많은 순(같으면 더 높은 순위 영상에 붙은 것 먼저)으로 최대 `max_candidates`개. 요청의 `max_candidates`가 있으면 그 값. 출처는 `origins`(youtube_tag·youtube_title)
7. 네이버: 후보마다 **대표 이름 그룹 하나에 대표 이름 + 별칭들을 키워드로**(최대 20개) 넣어 한 번씩 부르고 **trend_scan과 같은 함수(`trend_scan.judge`)**로 판정한다(기간·기준값도 같음). 판정 함수를 새로 만들지 않아 trend 평가 지표는 바뀌지 않는다
   - **이름별 판정**(#375): 별칭이 있는 후보는 대표 이름·별칭을 하나씩 따로도 판정한다(이름마다 네이버 1번). 묶음 그룹이 급상승이면 그 판정, 묶음은 평범(또는 판정 불가)인데 이름 하나라도 급상승이면 **급상승으로 올리고** 그 이름들 중 z가 가장 큰 판정을 쓴다(`naver_from: name`). 따로 판정해 급상승인 이름은 `rising_names`, 이름별 판정은 `name_verdicts`
8. **이름 자동 묶기**(`[candidates.auto_group]`, #383): 판정한 YouTube 후보 둘이 (a) 쌓인 스냅샷에서 같은 영상(태그·제목 구)에 함께 나온 적이 있고(`min_shared_videos` 1) (b) 두 이름의 네이버 일별 값 로그 상관(둘 다 값이 있는 날 `min_days` 28일 이상)이 `merge_corr`(0.95) 이상이면 같은 대상으로 묶는다. 네이버 값은 요청마다 배수만 달라 로그 상관은 따로 받은 두 요청끼리도 비교할 수 있다(추가 호출 없이 이미 받은 시계열로 잰다). 앞(영상 많은) 이름이 대표, 묶음은 7의 규칙(묶음 그룹 1번 더 + 이름별 판정, 하나라도 급상승이면 올림)으로 다시 판정한다. `suggest_corr`(0.8) 이상이면 `group_suggestions`로 **제안만** 하고 적용하지 않는다. 묶인 이름은 후보 `auto_grouped`, 쌍은 `auto_groups`(상관·날 수·함께 나온 영상 수)
   - 2026-11-03 실데이터(이미 받은 이름별 판정으로 계산): 사람 별칭 표 쌍 중 t1~티원 0.96·배틀그라운드~배그 0.99는 기준을 넘고, 배틀그라운드~pubg 0.92는 제안, 리그오브레전드~lol 0.73·마인크래프트~minecraft 0.69·league of legends 0.56은 못 찾는다(영문 이름은 검색하는 사람이 달라 모양이 다름). 상위 후보끼리(서로 다른 대상)는 가장 높은 상관이 t1~lck 0.62라 잘못 묶이지 않았다
9. **인기 검색어 출처**(`[trending]`, #381): 최신 인기 검색어 스냅샷의 검색어를 태그와 같은 규칙(정리·별칭·제외 목록)으로 거른다. YouTube 후보와 이름(공백·대소문자 무시)이 겹치면 그 후보에 근거(`trending`: 피드 순위·트래픽 표시값·스냅샷 시각)를 붙이고 `origins`에 `trending_search`를 더한다(네이버는 더 부르지 않음). 겹치지 않는 것은 피드 순위 순으로 최대 `max_candidates`(5)개를 `trending_candidates`로 같은 판정 함수에 넣는다. 인기 검색어 스냅샷이 없거나 `max_age_hours`(24)보다 오래되면 이 출처만 `trending_status: unknown` + 이유이고 YouTube 후보는 그대로다. 화면 출처 표시는 "인기 검색어"
10. 출력: 후보마다 대표 이름, 묶인 별칭(`aliases`)·자동 묶은 이름(`auto_grouped`), 출처(`origins`), YouTube 근거(나온 영상 수, 스냅샷 시각, 대표 영상 id `rep_videos`개, `matched_tags`·`matched_phrases`), 인기 검색어 근거, 네이버 판정(`TrendVerdict` 그대로). 그리고 인기 검색어 후보·상태, 자동 제외·자동 묶음·묶음 제안, 네이버 부른 수(`naver_calls`, 실패 포함)와 숫자로 만든 한 문장

- **확인할 수 없음:** YouTube 스냅샷이 없거나 `max_age_hours`보다 오래되면 YouTube 후보를 뽑지 않고 `status: unknown` + 이유(마지막 수집 시각·몇 시간 전·기준). 인기 검색어도 따로 같다. 네이버를 못 읽은 후보는 **그 후보만** `naver.status: unknown`("네이버 검색어 트렌드를 읽지 못함 [코드]")
- 설정은 회사 폴더 `trends.toml`의 `[candidates]`(`max_candidates` 5, `min_videos` 2, `max_age_hours` 24, `rep_videos` 5, `titles`, `exclude_tags`, `keep_tags`, `[candidates.aliases]`, `[candidates.auto_exclude]`, `[candidates.auto_group]`)와 `[trending]`(`geo`, `max_candidates` 5, `max_age_hours` 24). 후보마다 네이버 1번이라 작게 둔다(하루 한도 `NAVER_DATALAB_DAILY_LIMIT`). 설정이 없으면 같은 숫자에 제목·자동 제외·자동 묶기 꺼짐, 제외 목록·별칭 표 없음
- **한계:** 태그는 올린 사람이 붙인 것이라 영상 내용과 다를 수 있다. 제목 구는 형태소 분석이 없어 조각이 섞인다. 자동 묶기는 상위 후보끼리만 본다(네이버 호출을 늘리지 않으려고). 인기 검색어는 **오늘** 뜨는 검색어인데 네이버 판정은 **어제까지**(하루 늦게 채워짐)라 오늘 처음 뜬 검색어는 평범·자료 없음으로 나올 수 있다. 인기 목록(지역 `KR`)만 보고, 회사 키워드 검색 결과·추적 영상은 보지 않는다. 스냅샷 한 시각만 보며 YouTube 쪽 속도(두 시각 차이)는 아직 보지 않는다. 평가셋이 없어 자동 제외·자동 묶기 기준값은 실데이터로 본 값이다

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

### 트렌드 감지 탭 속도 (미리 계산·저장)

- **미리 계산**: 수집기(`youtube_collect`)가 새 스냅샷을 저장한 직후 `trend_precompute`가 급상승 판정(`trend_scan`: 판정판·네이버·설명 문장 캐시만 채움)·YouTube 후보(`trend_candidates`)·지금 인기(`trend_popular`)·빨리 크는 영상(`youtube_velocity`)·숏츠 통계(`shorts_stats`)를 화면과 같은 입력으로 계산해 `DATA_TREND_RESPONSES_DB`(run_all.sh: `backend/.data/trend_responses.db`)에 넣는다. 화면 요청은 저장된 결과를 읽기만 한다. 저장된 것이 없을 때만(첫 실행·수집기 꺼짐) 지금 계산하고 넣는다. SQLite라 서버를 다시 켜도 남는다(환경변수가 없으면 메모리, 테스트)
- **키** = skill + 요청 + 데이터 도장 + 규칙 버전. 도장: 마지막 YouTube·인기 검색어 스냅샷 시각, 두 스냅샷이 "오래됨" 기준을 넘었는지, 숏츠 형식·주제 분류 저장분, 숏츠 다시 조회·오늘 검색 저장분, 오늘 판정판 내용, 한국 날짜, 설정 파일 내용, LLM 설정·YouTube 키가 있는지(값은 안 봄). 규칙 버전: `RULES_VERSION` + data_agent 코드 해시(코드가 바뀌면 예전 결과를 안 씀). 같은 키면 처음 계산한 응답 그대로(올린 뒤 지난 시간처럼 지금 시각으로 세는 칸도 처음 값)
- 수집기와 서버의 환경변수가 다르면(예: 수집기를 손으로 켜 LLM 설정이 없음) 키가 달라 서버가 첫 요청에 계산한다. run_all.sh가 둘을 같은 환경으로 켠다
- 판정판(`today_board`)은 다른 프로세스가 파일에 쓰면(`PRAGMA data_version`) 다시 읽는다. 수집기가 남긴 판정을 서버가 바로 보고 같은 키가 된다
- 설명 문장 캐시(`DATA_EXPLAIN_DB`)·네이버 하루 캐시와 호출 수(`DATA_NAVER_CACHE_DB`)도 SQLite. 다시 켜도 그날 LLM·네이버를 다시 안 부른다
- 수집기 중복 실행 잠금: DB 옆 `youtube_collect.lock` (잡혀 있으면 새로 켠 수집기는 바로 끝난다)
- 최근 스냅샷(`recent_snapshots`, 지금 속도용)은 마지막 스냅샷에서 `DATA_TREND_LOOKBACK_HOURS`(기본 48)시간 안의 것만 본다. 늘 붙는 말 자동 제외(`popular_history`)는 여러 날 비율을 보므로 범위를 줄이지 않았다
- 단계별 시간: 감지 탭 skill은 요청마다 `[timing] skill=… total_ms sqlite_ms external_ms llm_ms compute_ms cache=hit|stored`를 data 로그에 한 줄 남긴다(값·키는 안 남김). 미리 계산은 수집기 로그에 `skill=trend_popular@precompute1`처럼
- `trend_scan`: 같은 숫자(facts)의 설명 문장은 다시 쓰지 않는다(`ExplainCache`, 검증 통과 문장만). 네이버는 원래 하루 캐시
- `trend_insight_brief`: 오늘 저장된 브리프가 있으면 재료(지금 인기·숏츠 통계·상품 목록)를 계산하기 전에 바로 준다
- 스냅샷 표 인덱스: `collected_at`, 인기 줄만 `collected_at`(마지막 인기 스냅샷·기간 조회)
- 측정(이 PC, 실행 중인 서버, 다시 요청): 지금 인기 0.34→0.03초, 급상승 1.87→0.08초, 숏츠 0.06→0.07초, 브리프 1.00→0.03초, 4개 동시 4.1→0.19초. 첫 요청(새 스냅샷 직후)은 새 주제 분류·설명 문장 LLM 때문에 여전히 몇 초

### trend_popular (지금 인기, 판정 없음)

"지금 많이 보이고 많이 찾는 주제"를 코드 점수로 줄 세운다. 급상승 판정(trend_scan·trend_candidates)과 별개이고 **네이버를 부르지 않는다**. "급상승"이라는 말은 trend_scan 판정에만 쓴다(화면 배지는 "지금 급등 중").

- **늘 영상으로 보기**(`fill_always = true`, 캣북스): 주제 수와 상관없이 다섯 칸(게임 · 뉴스·사건 · 연예·스포츠 · K-pop·음악 · IT) 모두 최근 조회수 높은 영상을 채우고, 화면은 그 칸에 영상만 보여 준다. 주제 순위(`items`)는 그대로 계산해 남긴다(기획안 초안 재료, 기획안은 영상 칸을 계산하지 않는다 `fill=False`)
- **영상 칸 판정(공유 분류기)**: YouTube 카테고리 번호는 참고만 한다. 칸은 `topic_category.classify_videos`가 제목·채널 이름·태그 일부(최대 5개, 데이터 블록 untrusted)로 정한다. 20개씩 묶어 LLM을 부르고, 영상 id마다 결과를 `topic_categories.db`에 저장해 보관 기간 동안 재사용한다(분류 못 함은 저장하지 않고 칸에 넣지 않는다)
  1. 최신 한국 인기 목록 스냅샷(저장된 값, 외부 호출 없음)의 최근 `fill_days`일 한국 영상을 분류해 칸을 조회수 순으로 채운다
  2. 5개(`per_category`) 미만인 칸만 그 칸의 YouTube 카테고리(`fill_youtube_categories`)를 검색해 보충한다(하루 1번·저장). 검색 결과도 같은 한국 영상 기준과 분류기를 통과해야 그 칸에 들어간다
  - **보충 검색어**(`fill_query_cells`, 캣북스: 게임): 그 칸 지금 인기 주제 앞 `fill_query_topics`(2)개 이름(사람 이름 제외)을 `|`(또는)로 묶어 검색어로 붙인다. 검색어 목록을 손으로 적지 않는다. 2026-11-03 게임 검색 50개 중 한국 영상: 검색어 없이 0개 → '리그오브레전드|t1' 37개(모두 분류기가 게임으로 판정)
  - **하루 보충 검색 상한** `fill_search_calls`(6, 칸별 YouTube 카테고리 수와 같음): 그날 검색 수가 상한에 닿으면 더 부르지 않고, 그 칸의 보충만 "확인할 수 없음(보충): 하루 보충 검색 상한(N번)에 닿아 검색하지 않음"을 남긴다(인기 목록으로 채운 영상은 그대로)
  - **주제 근거 영상**(`videos`, #512): 주제마다 `video_ids` 순서로 제목·채널·조회수·올린 시각을 붙인다. 제목은 저장된 것(`video_titles`)을 먼저 쓰고, 없는 것만 모아 `videos.list`로 한 번에 받아 저장한다(다음부터는 저장된 제목). 하루 호출 상한 `title_fill_calls`(2, 한 번 = 50개·1단위)에 닿거나 커넥터가 없으면 받지 않고 `videos_reason`에 이유. 기획안 재료(`promo_concepts`)가 부를 때는 커넥터 없이 저장된 제목만 쓴다
  - **"왜 뜨나" 근거**(#515, `skills/trend_evidence.py`, 화면 경로에서만 = YouTube 커넥터를 넘긴 요청): 숫자는 모두 코드. 영상마다 올린 뒤 지난 시간·지금 시간당 조회(두 스냅샷 차)·채널 구독자(`channels.list`, 채널당 하루 1번 받아 `channel_stats`에 저장·재사용, 하루 상한 `channel_fill_calls` 2번)와 구독자 대비 조회 배수·좋아요·댓글 비율·숏츠 여부(길이 ≤ `[shorts] max_seconds`)·숏츠 형식(shorts_stats가 저장한 분류가 있을 때만). 주제마다 인기 검색어 RSS 관련 뉴스(제목·출처·주소, `trending_news_links`, 있을 때만), 네이버 상승 시작일(오늘 판정 구간에서 평소 범위 위쪽 띠를 처음 넘은 날)과 평소 대비 배수(`rising_ratio`). 못 잰 칸은 None + 이유(`evidence_reason`·`news_reason`·`rising_reason`). **한 줄(`why_line`)**: 그 주제 근거 숫자만 데이터 블록에 넣고 LLM이 "…근거가 있어요" 한 줄(원인 단정 금지). 캐시에 없는 주제만 묶어 한 번, 하루 캐시(근거 숫자가 바뀌면 다시). 숫자 검증(`explain`)이나 단정 말(때문·덕분·원인 등)에 걸리면 코드가 만든 고정 문장(`why_status: fixed`), 근거 숫자가 없으면 `unknown` + 이유
  - 분류기 칸과 YouTube 카테고리가 가리키는 칸이 다르면 분류기를 따르고 `category_mismatch: true`(화면 "YouTube 카테고리와 다름"). 예: 2026-11-03 Ado 커버(YouTube 20 = 게임) → K-pop·음악
  - 한계: 분류기 정확도는 평가셋이 없어 재지 않는다. 인기 목록으로 5개를 채운 칸은 검색하지 않아, 검색에만 나오는 조회수 큰 영상(예: 음악 MV)은 그 칸에 안 보일 수 있다
- **모자란 칸 채우기**: 화면 카테고리 주제가 `per_category`보다 적고 `[popular] fill_youtube_categories`(화면 카테고리 → YouTube 카테고리 id, 캣북스: 뉴스·사건 → 25, IT → 28)에 있으면, 그 YouTube 카테고리의 최근 `fill_days`일 게시 영상을 조회수 순으로 `top_videos`에 채운다. **지금 인기 아님**이라 주제 순위·점수와 섞지 않는다. 검색은 카테고리마다 하루 1번(101단위)이고 `backend/.data/popular_videos.db`에 저장해 다시 쓴다. `fill_language = "ko"`면 한국 영상만(shorts_stats와 같은 판단). 2026-11-03: 뉴스 50개 중 한국 35개, IT 50개 중 한국 2개

1. 주제: 최신 YouTube 인기 스냅샷의 태그·제목 구를 trend_candidates와 같은 규칙(정리·별칭 표·수동 제외·자동 제외, `pick_names`)으로 최대 `max_youtube`(80)개, 최신 인기 검색어 스냅샷을 같은 정리 규칙(`pick_trending`)으로 최대 `max_trending`(30)개 본다(카테고리마다 5개를 채우려고 넉넉히, 네이버 호출 없음). 이름이 같으면 한 주제다
2. 자동 묶기: 네이버 일별 시계열이 필요해 여기서 새로 재지 않는다. 오늘 trend_candidates가 자동으로 묶은 이름 쌍(프로세스 안 오늘 판정 게시판 `today_board`)이 있으면 그대로 한 주제로 묶는다
3. 점수 = `video_weight` × 영상 몫 + `velocity_weight` × 조회 속도 몫 + `trending_weight` × 검색어 순위 몫(가중치 기본 1.0). **몫은 출처마다 순위로 매긴다**: 그 출처에 값이 있는 주제끼리 큰 순으로 순위를 매겨 (N − 순위 + 1) ÷ N(같은 값은 같은 순위). 값이 없는 주제는 0
   - 영상: 그 주제가 나온 인기 영상 수 / 조회 속도: 그 영상들의 지금 시간당 조회 증가 합(youtube_velocity와 같은 계산, 최근 두 스냅샷) / 검색어: 인기 검색어 순위
   - **순위 기반으로 바꾼 이유**: 예전에는 출처별 최댓값으로 나눴다. 2026-11-03 데이터에서 인기 영상 28개인 리그오브레전드 하나가 1.0이고 나머지 게임 주제(영상 5~12개)가 0.2~0.4로 눌려, 영상 수·속도 차이가 작은 주제들이 인기 검색어 4~6위 하나보다 뒤로 밀렸다. 순위 몫은 한 주제의 큰 값이 다른 주제 몫을 줄이지 않고, 세 출처를 같은 척도(0~1, 순서)로 더한다. log도 봤지만 출처마다 값의 크기(영상 수 1~28, 시간당 조회 수천~수만, 순위 1~30)가 달라 기준을 하나로 맞추기 어려웠다
   - **출처를 못 읽으면 그 몫은 unknown(None) + 이유이고 점수에서 뺀다**(0으로 채우지 않음): YouTube 스냅샷 없음·오래됨, 스냅샷 2개 미만(속도), 인기 검색어 스냅샷 없음·오래됨. 인기 영상에 나오지 않은 주제는 영상 몫·속도 몫이 0이고 속도 값은 비운다
4. 주제 분류: content_recommend와 같은 `classify_topics`(LLM 한 번에 묶어서, 주제별 하루 캐시, 근거 글은 대표 영상 제목 또는 인기 검색어 관련 뉴스 제목). 회사 "추천 안 함" 분류(`[recommend] exclude_categories`)면 `brand_safe: false`, 분류 unknown이면 None
5. "지금 급등 중" 배지(`rising_now`·`rising_ratio`): 오늘 이미 낸 네이버 판정(trend_scan·trend_candidates·content_recommend가 남긴 것)이 급상승이면 true, 평범이면 false, 오늘 판정이 없으면 None. trend_popular가 판정하려고 네이버를 부르지 않는다. 판정 기록은 `backend/.data/today_board.db`(`DATA_TODAY_BOARD_DB`, run_all.sh가 준다)에 남아 **서버를 다시 켜도 그날 배지가 유지**된다(7일 지난 기록은 지운다)
6. **카테고리별 보기**(`by_category`): 화면 카테고리(`[popular] categories`, 캣북스: 게임·뉴스·사건·연예·스포츠·IT)마다 점수 순 1~`per_category`(5)위. 모자라면 있는 만큼 + 이유(`shortage_reason`). **화면 카테고리가 아닌 분류(기부·재난·정치·사회 등)와 분류하지 못한 주제는 응답(`items` 포함)에서 뺀다**. 카테고리별로 채우려고 후보 전부를 분류한다(저장된 분류는 다시 묻지 않음)

- 출력: 주제마다 순위·이름·같이 센 이름·출처·점수와 몫·인기 영상 수·영상 id·조회 속도 합·잰 영상 수·못 잰 이유·인기 검색어 순위·트래픽 표시값·주제 분류·`brand_safe`·`rising_now`. 그리고 출처별 상태·이유·시각, 가중치, 쓴 추천 안 함 분류
- **한계:** 평가셋이 없어 "인기 순위가 맞는지"를 재지 않는다. 가중치는 성과 자료가 없어 정한 값이다. 순위 몫은 값의 크기 차이를 버린다(1위와 2위의 차이가 커도 같은 간격). 인기 검색어는 피드에 10~20개뿐이라 검색어 몫을 받는 주제가 적다

### promo_concepts (광고·홍보 숏츠 예시 아이디어, 참고용)

**이름을 바꿨다(2026-11-04):** "기획안 초안" → **"예시 아이디어(참고용)"**. 확정 기획은 기획팀 몫이다. 데이터는 기획안을 확정하지 않고, 근거 있는 인사이트와 참고용 예시만 넘긴다. skill id(`promo_concepts`)와 계약 이름은 그대로 둔다.

**타깃 연령대(#524):** 예시마다 빌린 트렌드를 `trend_age`로 연령대별로 보고, 평소 대비 가장 크게 오른 연령대(`target_age`)·배수(`target_age_ratio`)·그 연령대 판정(`target_age_status`)을 붙인다. 검색 키워드는 지금 인기 = 트렌드 이름, 시즌 = 시즌 확인 키워드(예: "무지 노트 12월 시즌" → "무지 노트"). 트렌드 없는 예시·회사 이벤트·연령대 값을 못 받은 경우는 "타깃 미정"(`target_age` 없음) + `target_age_reason`. 연령대 조회는 trend_age 하루 상한·캐시를 같이 쓰고, 실패해도 예시는 그대로 둔다.

회사 상품을 알리는 **숏츠 광고 기획안 초안** 3~5개(`[promo] concepts`, 기본 4)를 만든다. 확정이 아니라 초안이다. **재료와 검증은 코드**, LLM은 정해진 JSON 스키마로 초안 문장만 쓴다.

**흐름:** 데이터(감지·분석·초안) → 기획(확정) → 마케팅(제작·게시) → 데이터(성과 측정·다음 초안에 반영). 기획 부서 에이전트(planning)가 레포에 있어 `promo_concepts` 요청 가능 목록에 넣었다(orchestrator·marketing·dashboard-api·planning).

1. 재료(코드)
   - 상품: 회사 커넥터 상품 목록(캣북스 shop-api, 이름·카테고리·설명·가격). 못 읽으면 초안 없음 + 이유
   - **인기 숏츠 형식**: `shorts_stats` 형식별 중 표본이 충분한 것(기타·unknown 제외)의 평균 조회수 상위 `shorts_formats`(3)개. 초안마다 빌려 온 형식과 그 형식의 평균 조회수(서버 값)를 붙인다
   - **K-pop·음악 트렌드**: 곡 제목·음원·가수·그룹 이름은 쓰지 않고 형식(안무 챌린지, 커버 형식 등)만 빌리게 프롬프트로 정한다. 이 트렌드를 쓴 초안에는 코드가 주의사항 **"음원 저작권: 원곡 사용 금지, 라이선스 음원만"**을 늘 붙인다(`MUSIC_CAUTION`)
   - **트렌드**: `trend_popular` 카테고리별 인기 중 `trend_categories`(게임·연예·스포츠·K-pop·음악·IT)만, 카테고리마다 기준을 넘은 것 앞에서 `trends_per_category`(2)개. **기준: 출처(YouTube 인기 영상·인기 검색어) `trend_min_sources`(2)개 이상, 또는 YouTube 인기 영상 `trend_min_youtube_videos`(5)개 이상**(2026-11-03: 두 출처 겹침 0개, 게임 주제 영상 4~28개·중앙값 6이라 5로 정함). **사람 이름 자체인 주제(분류기의 `is_person`, 예: 윤영미·페이커)는 늘 뺀다**(초상권). 팀·게임 이름은 분위기·형식만 빌리는 조건으로 둔다. 뺀 주제와 이유는 `skipped_trends` **뉴스·사건·기부·재난·정치·사회·인물은 설정과 관계없이 늘 뺀다**(화면 "지금 인기"에는 뉴스·사건이 보여도 광고 재료로 쓰지 않는다). 실존 인물·팀·리그·상표가 들었으면(`real_entity`) `ip_risk`
   - **시즌**: 카탈로그 카테고리 이름과 **상품 이름에서 코드로 뽑은 핵심어**(숫자가 든 낱말·"세트" 뺌)를 키워드마다 trend_history(월 단위, 24개월)로 본다. 작년 같은 달·다음 달 값이 그 기간 평균의 `season_min_ratio`(1.3)배 이상이면 시즌 재료. **키워드를 손으로 나열하지 않는다.** 네이버는 키워드마다 1번, 상한 `season_max_calls`(20), 하루 캐시. 확인한 키워드와 배율은 응답 `season_checks`에 모두 남겨, 시즌 재료가 비었을 때 이유가 보이게 한다
   - 회사 이벤트 달력(`[promo] events`, 덮어쓰기용, 비어 있음): 이번 달·다음 달 이벤트만
2. LLM(한 번): 지금 인기 트렌드는 상표·인물이 아닌 **밈 형식**(선택 화면, 하이라이트 중계, 랭킹 등)으로 빌릴 수 있고, 맞는 이유가 성립하면 초안 1~2개는 지금 인기 트렌드를 쓰도록 우선한다(억지로 붙이지 않음). 하나도 쓰지 않았으면 그 이유를 `popular_trend_note`에 남긴다. 재료를 **데이터 블록(untrusted)에만** 넣는다. 칸은 제목·컨셉 한 줄·빌려 온 트렌드(재료 이름 그대로, **맞는 트렌드가 없으면 빈 문자열 = 트렌드 없이 상품 + 인기 숏츠 형식 초안**)·빌려 온 숏츠 형식·**이 트렌드·형식이 이 상품과 맞는 이유 한 줄**·등장 상품 id·**길이(15~60초)**·콘티·주의사항. 규칙: 숏츠만(롱폼 금지), 게임·작품 IP나 실존 인물은 분위기·형식만 빌릴 것, 과장 광고 금지, 숫자는 재료에 있는 것만
3. 검증(코드): 아래면 그 초안은 **버리고** 제목과 이유(`dropped`)만 남긴다
   - 길이가 `min_seconds`~`max_seconds`(15~60초) 밖(롱폼·60초 초과)
   - 콘티 컷 수가 길이 기준(`cut_bands`: 20초 이하 3컷, 40초 이하 4~5컷, 60초 이하 5~6컷)과 맞지 않음
   - 카탈로그에 없는 상품 id·등장 상품 없음, 재료 밖 트렌드, 재료 밖 숏츠 형식, 맞는 이유 없음
   - 재료에 없는 숫자(`explain` 숫자 검증). 콘티의 길이·컷 표기("0~3초", "2컷")만 빼고 본다
   - 트렌드·형식 이름은 공백·대소문자·따옴표가 달라도, 데이터 블록 번호(`트렌드N`·`숏츠형식N`)로 답해도 같은 재료로 본다
   - **한 번 더 묻기**: 통과한 초안이 `MIN_KEPT`(3, 설정 `concepts`가 더 작으면 그 수)개 미만이고 버린 것이 있으면, 버린 초안 제목·이유를 데이터 블록(`버린 초안N`)에 붙여 LLM에 **한 번만** 더 묻는다. 두 번째 답도 같은 검사를 거치고 통과한 것을 합친다. 그래도 0개면 `empty_reason`에 버린 이유를 같이 적는다(2026-11-04 08:57 실제로 한 번 0개가 났는데 이유가 남지 않아서, 서버 로그에도 버린 이유를 남긴다)
4. 경고(코드): 재료 트렌드 중 실존 인물·팀·리그·상표가 든 것(분류기의 `real_entity`)의 이름(`entity_names`·트렌드 이름)이 제목·콘티에 그대로 있으면 "상표·초상권 확인 필요". 분류(게임 등)만으로는 경고하지 않는다
5. LLM 설정이 없거나 실패하면 **빈 목록 + 이유**(지어내지 않는다). 재료가 없으면 LLM을 부르지 않는다
6. 하루 캐시: 오늘(한국 날짜) 만든 초안이 있으면 그것을 준다(`cached: true`). `regenerate: true`면 새로 만든다. 초안이 나온 실행은 모두 기록(`backend/.data/promo_concepts.db`)에 남는다

- 예전 칸 `format`은 15초 이하 "15초 숏폼", 30초 이하 "30초 숏폼", 그 밖은 비운다(롱폼은 쓰지 않는다). 길이는 `duration_seconds`
- 👍/👎 피드백 저장은 **아직 없다**. 상태 변경이라 `execute()`(안전 관문)와 safety 레지스트리 등록이 필요하고, 레지스트리는 사람만 바꾼다(#418 본문에 skill 이름·등급·레지스트리 항목 초안)
- **한계:** 평가셋이 없어 초안 품질을 재지 않는다. 👍/👎가 쌓이면 그것을 평가셋으로 쓴다. 숏츠 형식 재료는 이미 뜬 숏츠(인기·조회수 순 검색)의 평균이다. 시즌 기준(1.3배)과 컷 기준은 정한 값이다. 경고는 IP 이름이 그대로 있을 때만 단다

### trend_scan 분류 칸 (#539)

급상승 판정(`trend_scan`) 응답의 판정마다 공유 분류기 결과를 선택 칸으로 붙인다: `topic_category`·`topic_category_reason`·`real_entity`·`entity_names`·`ad_material`·`ad_material_reason`. 그룹 이름(+ 키워드)을 묶어 한 번 분류하고 저장된 분류는 다시 묻지 않는다. **판정(상태·배수)과 평가 지표는 그대로**이고, 분류가 실패해도 판정은 준다(분류 unknown + 이유, 광고 재료 None).

### trend_insight_brief (인사이트 브리프)

흐름: **데이터(인사이트 + 근거) → 기획(제안 + 실행 근거 + 목표) → 사람 확인 → 마케팅 실행 → 데이터(성과 측정) → 기획(결과 기록)**. 데이터는 기획안을 확정하지 않고 근거 있는 인사이트를 기획에 넘긴다. grade read, 요청: planning·orchestrator·dashboard-api.

1. **후보**(코드): `trend_popular` 지금 인기 주제(순위 순, 화면 경로처럼 YouTube 커넥터를 넘겨 "왜 뜨나" 근거까지) + 지금 인기에 없는 오늘 네이버 급상승 판정. 이름·별칭이 같으면 하나
2. **점수**(코드, `[brief]` 가중치): 지금 인기 `1/순위` + 네이버 `max(0, 배수−1)` + 연령대 `max(0, 최대 연령대 배수−1)` + 인기 검색어 `1/순위` + YouTube `영상 수/최대`. 1차 점수 상위 `인사이트 수 + age_topics_extra`개만 `trend_age`로 연령대를 보고(네이버 호출 아끼기, trend_age 하루 상한·캐시 공유) 다시 점수를 매겨 `max_insights`(5, 요청으로 3~7)개
3. **분류(#539)**: 브리프에 들어가는 모든 주제는 공유 분류기(`topic_category`)를 거친다. 지금 인기 주제는 이미 분류된 값(저장 재사용)을, 지금 인기에 없는 급상승 주제는 여기서 묶어 한 번 분류한다(저장된 분류는 다시 묻지 않음). **분류가 안 된 주제(unknown)는 순위·연령대 후보에 넣지 않고** 맨 아래 "참고용 + 분류 대기"(`classification_pending`, 최대 `pending_max` 2개)로 둔다
3-1. **광고 재료**: 공용 규칙(`skills/ad_material.py`, 예시 아이디어·급상승 판정과 같다). 뉴스·사건·기부·재난·정치·사회·인물 분류, 사람 이름 주제, 회사 추천 안 함 분류, `[promo] trend_categories` 밖이면 `ad_material: false` + 이유이고 **맨 아래 `reference_only`(참고용)**. 실존 이름·상표가 든 주제는 가능 + "분위기·형식만" 이유. 분류가 없는 급상승 주제는 `null` + 이유
4. **칸**: `insight_id` = `ins_<테넌트|정규화 주제|카테고리 해시>`(날짜 없음, 기획이 제안·성과 요청에 적는다. 예전 날짜 id는 `legacy_insight_id`) · 근거(YouTube 영상 수·시간당 조회·구독자 대비 최대 배수·스냅샷 시각, Google 순위·관련 뉴스·스냅샷 시각, 네이버 배수·판정·상승 시작일·판정 마지막 날, 링크, 없는 출처와 이유) · 타깃 연령대(없으면 미정 + 이유)와 **연령대별 배수 전체**(`age_bands`, trend_age 결과 그대로 설정 순서. 브리프가 이미 부른 trend_age 한 번에서 나오므로 네이버 호출이 늘지 않는다. 기획은 브리프 하나로 연령대 전체를 받는다. 연령대를 보지 않은 주제는 빈 목록이고 이유는 `target_age_reason`) · 상품(`product_link` 임베딩 기준 `[recommend] min_similarity` 이상·1등만, 이름에 시즌 확인 키워드가 들어 있으면 시즌 근거 한 줄) · 숏츠 형식(4-2) · 한계 · 예시 아이디어(오늘 저장된 `promo_concepts` 중 이 주제를 빌린 것, "참고용". 새로 만들지 않는다)
4-0-0. **회사 연결 `company_relevance`**(코드): `direct` = 연결 상품 있음, `domain` = 회사 관련 키워드(시즌·상품 핵심어)이거나 이름이 도메인 프로필 씨앗·하위 주제(묶은 이름 포함)와 겹침(2글자 이상, 공백 무시 포함 대조), `none` = 둘 다 아님 → **`reference_only=true`** + `company_relevance_reason` "회사 상품·도메인과 연결 없음", 맨 아래로. 기획은 참고용을 뺀다. 기회 카드는 늘 `domain` 이상(연결 상품이 있으면 `direct`). 도메인 키워드를 못 받으면 연결 상품·회사 관련 키워드로만 보고 `limits`에 한 줄. `promo_concepts`도 지금 인기 트렌드를 빌린 초안의 트렌드 이름이 카탈로그 상품 핵심어·카테고리 이름과 겹치지 않으면 `reference_only`(등장 상품은 LLM이 고른 것이라 연결로 보지 않음, 시즌 재료는 회사 키워드라 그대로)
4-0. **숫자 목록 `facts`**(`skills/brief_facts.py`): 응답에 나오는 숫자는 모두 `DataFact`(fact_id·label·value·unit·source·link·period·sample·about)로 브리프 `facts`에 있고, 인사이트·기회 카드는 `fact_ids`로 가리킨다(카드 숏츠 숫자의 link = 근거 숏츠 주소). 한 줄(LLM)의 숫자 검증에 쓰는 숫자도 모두 facts에 있어, 문장 속 숫자가 facts에 없으면 고정 문장이다. 응답 모양 버전 `schema_version`(`trend_insight_brief.v3`), 시점 `generated_at`·`snapshot_id`
4-0-1. **영상 주소·썸네일**: 영상이 나오는 응답(카테고리별 영상·지금 인기 주제 영상 `PopularVideo`, 지금 빨리 크는 영상, 근거 숏츠 `ShortsVideo`)은 `video_url`·`thumbnail_url`, id 목록(후보 태그 근거·하위 주제·브리프 근거·기회 카드)은 옆에 `video_links`(같은 순서). 썸네일은 이미 받은 videos.list·search 응답의 `snippet.thumbnails`(medium 먼저)를 저장한 값만 쓰고, 썸네일 때문에 YouTube를 더 부르지 않는다. 저장된 값이 없으면 None(다음 수집부터 채워짐)
4-1. **구성(#543)**
   - **카테고리 상한** `per_category_max`(2): 한 분류(예: 게임)가 브리프를 채우지 않게 점수 순으로 고르되 카테고리마다 최대 2개
   - **회사 관련 인사이트**(`company_related`): 시즌 키워드(오늘 기획안 시즌 확인, 배율 큰 순)와 카탈로그 상품 핵심어(`season_keywords`) 중 `company_pool`(4)개를 후보로, 공유 분류기와 연령대를 거쳐 **급상승 판정이 아니어도** 작년 같은 달·다음 달 배율 ≥ `company_min_season_ratio`(1.3) 또는 어느 연령대 배수 ≥ `company_min_age_ratio`(1.3)이면 "오른 것"으로 보고 `company_topics`(최대 2)개를 **따로 확보**한다(그만큼 트렌드 자리를 줄임). 고른 이유는 `company_reason`(코드 문장), 시즌 근거는 `evidence.season_*`. 회사 상품·시즌 키워드라 빌려 올 IP·인물이 없어 광고 재료 가능(분류 대기면 제외). 없으면 브리프 `limits`에 "회사 관련 인사이트 없음: …"
   - **점수에 상품·시즌**: 상품 몫 `w_product`(0.5, 임베딩 기준을 통과한 상품이 있으면) + 시즌 몫 `w_season`(0.5 × max(0, 배율−1), 연결 상품 이름에 시즌 키워드가 있으면 그 배율). **이유**: 기획이 바로 쓸 수 있는 인사이트는 회사 상품과 이어지고 시즌이 맞는 것이라, 같은 인기라면 그쪽을 앞에 둔다. 다만 상품 연결은 임베딩 기준(0.65·1등)을 통과해야 해서 드물고, 인기 몫(1/순위 ≤ 1)을 넘지 않게 0.5로 둔다(성과 자료가 쌓이면 다시 정할 값)
   - **상품 0개**: `products_reason`이 "회사 상품과 직접 연결 없음 (…이유)"
   - 순서: 순위 인사이트(점수 순) → 회사 관련 → 광고 재료 아님(참고용) → 분류 대기
4-2. **숏츠 형식 단계**: ① `topic` 이 주제 근거 숏츠(shorts_stats가 분류해 둔 것) 중 가장 많은 형식 → 없으면 ② `category` 이 주제 분류의 숏츠 통계(`by_category[].formats`, 표본이 `[shorts] min_sample` 이상일 때만)에서 가장 많은 형식 → 없으면 ③ `all` 전체 숏츠 통계(`by_format`)에서 가장 많은 형식. ②·③에서는 목록 밖 형식 "기타"와 미분류를 고르지 않는다(표본 수에는 센다). 칸: `shorts_format_source`(단계), `shorts_format_source_count`(그 단계에서 이 형식인 숏츠 수)·`shorts_format_source_total`(그 단계에서 형식을 분류한 숏츠 수), ②·③이면 `shorts_format_reason`에 앞 단계를 못 쓴 이유. 중앙값·기준 시각(`shorts_format_median_views`·`shorts_format_at`)은 어느 단계든 숏츠 통계의 그 형식 값. 한 줄 데이터 블록에도 "숏츠 형식을 고른 단계"를 넣어 이 주제 숏츠의 형식처럼 쓰지 않게 한다
5. **한 줄**: 인사이트마다 근거 숫자만 데이터 블록에 넣고 묶어서 LLM 한 번. 숫자 검증(`explain`)·단정 말 검사에 걸리면 코드가 만든 고정 문장(`line_status: fixed` + 이유)
6. **하루 캐시**: (한국 날짜, 규칙 버전 = `[brief]` 설정·프롬프트·한계 문장·칸 모양(`SHAPE`) 해시) → 브리프. `regenerate: true`면 새로 묶는다

### domain_profile (도메인 프로필)

고객사가 바뀌어도 사람이 키워드·카테고리·출처를 손으로 정하지 않게, 회사 소개와 카탈로그로 프로필을 만든다.
테넌트 설정에는 회사 소개(`[profile] about`)와 사람이 덮어쓰기(`[profile.override]`, 비어 있어도 됨)만 둔다.
`scripts/tenant_env.py`가 `DATA_TENANT_CONFIG`(= 회사 폴더 `tenant.toml`)를 넘긴다.
**회사 소개는 AI가 사실로 읽는다. 분위기 문장·별명은 쓰지 말고, 카탈로그와 맞게** 쓴다.

1. **입력**(코드): 회사 소개 + 카탈로그 상품(이름·카테고리 코드·설명 앞 120자, 최대 80개)·카테고리 이름. 데이터 블록에만 넣는다
2. **LLM 한 번**(정해진 스키마): 업종(`INDUSTRIES` 중 하나: 출판·교육·패션·식품·뷰티·디지털·가전·생활·리빙·반려동물·스포츠·레저·유아·출산·여가·취미·기타), 상품군(이름 + 카테고리 코드), 고객 축(`CUSTOMER_AXES` 중), 분석 질문(최대 5개, 숫자 없이), 씨앗 키워드(최대 12개 + 근거 문장)
3. **코드 검증**: 목록 밖 업종·고객 축은 버림. 상품군 카테고리 코드는 카탈로그에 있는 것만, 상품 수는 코드가 셈. 숫자가 든 질문은 버림. **씨앗 키워드는 근거 문장이 상품 이름·카테고리 이름·설명·회사 소개 원문에 그대로 있고 키워드가 그 근거 안에 있을 때만** 남긴다(`evidence_from`, 근거 상품 id). 회사 소개·상품 설명 문장은 낱말이 떨어져 있어도("고양이가 지키는 서점" ↔ "고양이 서점") **키워드 낱말이 모두 같은 문장 안에** 있으면 그 문장을 근거로 본다(다른 문장에 흩어지면 버림). 아니면 `dropped_keywords` + 이유
4. **사람 덮어쓰기가 먼저**: `industry`·`customer_axes`·`questions`(바꿈), `seed_keywords`(더함, `evidence_from: override`), `drop_keywords`(뺌). 덮어쓴 칸은 `overridden`
4-1. **소개와 카탈로그가 어긋남**(`intro_mismatch`, 경고만): LLM이 상품군 중 회사 소개에 나오지 않는 것을 고르고(`groups_not_in_intro`), 코드가 검증된 상품군인지·상품군 이름이나 카테고리 이름이 소개에 그대로 있지 않은지 확인한 뒤 그 상품군의 카탈로그 상품 목록을 붙인다. 카탈로그 상품은 그대로 두고, **그 상품에서 나온 씨앗 키워드(근거 상품이 모두 경고 상품군이거나 근거가 그 카테고리 이름)는 씨앗에서 빼서 `mismatch_skipped`에 이유와 함께** 둔다. 소개가 근거인 키워드는 남긴다. 사람 덮어쓰기 `seed_keywords`에 넣으면 다시 쓴다. `limits`에도 한 줄
4-2. **칸별 근거 문장**(`evidence`, `ProfileEvidence`): 상품군은 코드가 그 카테고리 코드의 카테고리 이름으로 단다. 업종·고객 축·분석 질문은 LLM이 옮긴 원문 일부를 코드가 소개·상품 이름·카테고리 이름·설명과 대조해, 원문 안에 그대로 있는 것만 원문 문장으로 남긴다(4글자 미만 조각·지금 칸에 없는 값은 버림). 근거가 없는 칸은 목록에 없다
5. **저장 재사용**: 키 = 소개·덮어쓰기·카탈로그(상품·카테고리)·프롬프트·목록·**코드 버전** 해시. 소개나 카탈로그, 코드가 바뀌면 다시 만든다. 코드 버전(`skills/code_version.py`)은 결과를 만드는 모듈 소스의 해시다(프로필: domain_profile, 수집 계획: source_plan·data_sources, 브리프·기회 카드: trend_insight_brief·domain_opportunity·domain_subtopics·domain_profile·source_plan·data_sources). 예전에는 코드가 바뀌어도 소개·카탈로그가 같으면 예전 결과를 다시 썼다(#598 뒤 캣북스 프로필). 업종이나 근거 있는 씨앗 키워드가 없으면 저장하지 않고 `unknown` + 이유

### data_sources (데이터 출처 목록)

출처마다 능력을 코드에 적고, 수집 계획(LLM)이 이 목록 안에서만 고른다. 출처는 **업종 태그로만** 구분한다(회사 이름 없음).
API 키는 AXcellent 공용으로 `backend/.env`에 한 번만 두고 고객사별로 받지 않는다. 키가 없는 출처는 구조만 있고 상태가 "키 필요"다.

| 출처 id | 데이터 종류 | 업종 | 연령·성별 | 카테고리 | 한도 (공식 문서) | 키 | 공식 문서 |
|---|---|---|---|---|---|---|---|
| `youtube` | 시청 | 전체 | 없음 | API (`videoCategories.list`) | 하루 10,000 단위, search.list 100·videos.list 1 | `YOUTUBE_API_KEY` | [quota](https://developers.google.com/youtube/v3/getting-started#quota) |
| `google_trending` | 검색 관심 | 전체 | 없음 | 없음 | 공개 RSS, 한도 미공개 | 없음 | [RSS](https://trends.google.com/trending/rss?geo=KR) |
| `naver_search_trend` | 검색 관심 | 전체 | 있음 | 없음(키워드) | 하루 1,000회, 그룹 5개·그룹당 키워드 20개 | `NAVER_HUB_CLIENT_ID`·`NAVER_HUB_CLIENT_SECRET` | [API HUB](https://api.ncloud-docs.com/docs/naver-api-hub-overview) |
| `naver_shopping` | 실제 소비(쇼핑 클릭, 판매량 아님) | 전체 | 있음 | 고정 후보(네이버쇼핑 cat_id)를 실제 호출로 확인. 목록 API 없음 | 하루 한도 미확인(429 = 하루 허용량 초과), 분야 3개·키워드 5개/요청, 2017-08-01부터 | 검색어 트렌드와 같은 API HUB 키 | [쇼핑인사이트](https://api.ncloud-docs.com/docs/naver-api-hub-shopping-insight-categories.md) |
| `data4library` | 실제 소비(공공도서관 대출) | 출판·교육 | 있음 | 고정(KDC 대분류 0~9) | 하루 한도 미확인(비공식 500회) | `DATA4LIBRARY_API_KEY` | [정보나루](https://data4library.kr/apiUtilization) |
| `kosis` | 조사 통계 | 전체 | 표마다 | API (통계목록 `statisticsList.do`) | 하루 한도 미확인, 2026 공지로 분당 제한(수치 미확인), HTTPS만 | `KOSIS_API_KEY`(요청 변수 `apiKey`) | [개발 가이드](https://kosis.kr/openapi/devGuide/devGuide_0201List.do) |

- **상태**(코드): 키가 모두 있으면 `available`, 없으면 `key_required` + 키 이름. 쇼핑인사이트는 같은 키라도 API HUB 앱에서 그 API를 켜야 해서 **실제 호출 1번**(`probe`, 패션의류 최근 30일 월 단위)이 성공해야 `available`이다. 결과는 저장하고, 실패면 하루 뒤 다시 확인한다
  - 2026-11-04 확인: 지금 키로 401 "요청한 API는 이 Application에서 활성화되어 있지 않습니다" → `key_required`
- **승인**(사람): 기존 출처 3개는 승인된 것으로 본다. 새 출처는 `tenant.toml` `[sources] approved`에 id를 적어야 수집을 시작한다(상태 변경이라 원래 승인 레지스트리가 필요하다. 지금은 설정 파일 플래그)
- **커넥터**(`connectors/naver_shopping.py`·`data4library.py`·`kosis.py`): 공식 문서의 요청·응답 형식대로. live·record·replay(`DATA_EXTERNAL_MODE`)를 지원하고 키는 로그·오류·저장 파일에 넣지 않는다. 테스트는 문서 형식으로 만든 가짜 응답(`tests/test_source_connectors.py` 안에만)으로만 돈다
- **미확인**: 쇼핑인사이트 하루 한도·분야 목록 API, 정보나루 JSON 응답 구조(공식 페이지 예시는 XML)·한도, KOSIS 오류 형식·분당 한도. 키를 등록하면 record 모드로 한 번 불러 형식을 확인한다
- **한계**: 새 업종에 맞는 출처가 목록에 없으면 커넥터를 한 번 추가해야 한다 (출처 표에 한 줄 + 커넥터 1개)

### source_plan (수집 계획)

AI가 고르고 코드가 검증한다. 수집은 하지 않는다(카테고리 확인 호출만).

1. **재료**: 도메인 프로필(저장 재사용) + 출처 목록(`data_sources`)
2. **카테고리 선택지**(코드, 실제 목록): YouTube `videoCategories.list`(regionCode=KR, 1단위) / 쇼핑인사이트는 공식 목록 API가 없어 네이버쇼핑 cat_id 후보를 주고, 사용 가능이면 고른 것을 실제 호출(`verify_categories`, 3개씩)로 확인해 데이터가 오는 것만 남김 / 정보나루 KDC 대분류 / KOSIS는 통계목록 API. **키 필요 출처는 확인을 건너뛰고 `after_key`(키 등록 후 확인)**
3. **LLM 한 번**(정해진 스키마): 출처 목록 안에서 출처·이유(숫자 없이)·카테고리 id·키워드·연령 여부
4. **코드 검증**: 목록 밖·중복 출처, 업종 태그가 회사 업종과 안 맞는 출처(`all`이 아니면), 선택지 밖·확인 호출에서 데이터가 없는 카테고리, 씨앗 키워드 밖 키워드, 키워드를 안 받는 출처의 키워드, 연령대를 못 주는 출처의 연령은 버리고 이유(`dropped`·`dropped_sources`, 버린 값의 종류 `kind`: source·category·keyword·age)
5. **할당량**(코드): 출처마다 공식 문서 요청 단위로 호출 수·단위를 센다(YouTube 카테고리·키워드마다 search.list 100 + videos.list 1, 네이버 검색어 트렌드 그룹 5개당 1번 × 연령대 5, 쇼핑 분야 3개당 1번 + 분야마다 키워드 5개당 1번 + 분야 연령 1번, 정보나루 분류마다 1번 × 연령대, KOSIS 목록 1 + 표마다 1). 상한(`[plan]`: YouTube 1,000단위, 네이버 검색어 60번, 쇼핑 40번, 정보나루 50번, KOSIS 20번, Google 2번)을 넘으면 연령대 → 키워드 → 카테고리 순으로 뒤에서 자르고 `trimmed`에 남김
6. **키 필요 출처도 계획에 남김**: `unlocks` = "<키 이름>가 있으면: <더 쓸 수 있는 근거>" (키는 있지만 API를 못 쓰면 그 이유)
6-1. **계획에 없는 출처**(`not_planned`): 출처 목록에 있지만 계획에 안 든 출처마다 이유(계획에서 뺀 이유·키 없음·승인 전·고르지 않음, 코드 문장)와 등록하면 얻는 근거(`unlocks`, 출처 정의 값. 정의에 없으면 None)
7. **승인**: 새 출처 수집은 사람 승인 후(`tenant.toml` `[sources] approved`). 원래는 상태 변경이라 승인 레지스트리가 필요하다(초안은 needs-human 이슈)
8. **저장**: 계획 id = 프로필 해시·씨앗 키워드·출처 상태·승인·상한·프롬프트 해시. 같으면 저장된 계획을 쓴다(`regenerate`로 새로)

### trend_insight_brief 기회 가설 카드 (선택 칸 `opportunities`)

도메인 프로필 → 수집 계획(저장 재사용)대로 **사용 가능하고 승인된 출처만** 불러 하위 주제(계획에 들어간 씨앗 키워드, 최대 `[opportunity] max_cards` 5)마다 카드를 만든다. 숫자는 모두 코드, 한 줄만 LLM. 카드를 못 만들어도 브리프는 그대로 주고 `opportunities_reason`에 이유.

| 칸 | 출처·계산 (코드) |
|---|---|
| 실제 소비 `consumption` | 계획의 소비 출처마다. 키가 없으면 `key_required`("키 등록 후 가능" + 얻는 근거), 승인이 없으면 `not_approved`, 둘 다 되면 실제로 부름(쇼핑: 계획 첫 분야 안 키워드 클릭을 trend_scan 판정으로 평소 대비 배수, 정보나루: 최근 30일 그 분류 인기 대출 중 키워드가 든 책의 대출 수 합) |
| 검색 관심 `search_interest` | 네이버 검색어 트렌드(계획에 있고 승인된 경우) 그룹 5개씩 한 요청, `judge_series` 평소 대비 배수. 기간(84일)·표본(값이 있는 날 수) |
| 연령대 `age_bands` | trend_age 그대로 (계획에서 연령을 고른 경우만, 하루 상한·캐시 공유). 없으면 `age_reason` |
| 숏츠 적합도 `shorts_fit` | 하위 주제를 공유 분류기로 분류 → 숏츠 통계 그 분류 조회 중앙값 ÷ 전체 숏츠 중앙값(`ShortsStatsOutput.median_views`). 표본 < `min_samples`(5)면 불확실, ≥ `high_ratio`(1.2)면 높음, ≤ `low_ratio`(0.8)면 낮음, 사이면 불확실 |
| 잘 되는 형식 `formats` | 그 분류 숏츠의 형식별 수(기타·미분류 제외, 최대 3) + 전체 숏츠 통계의 그 형식 중앙값 |
| 목표 참고값 | 하위 주제 카드: 그 하위 주제 숏츠 조회 중앙값(표본 5개 이상). **5개 미만이면 None + "표본 부족"**(분류·전체 중앙값으로 채우지 않음). 씨앗 키워드 카드: 그 분류 숏츠 조회 중앙값(표본 기준 이상), 아니면 전체 중앙값. 기준 문장 `target_reference_basis` |
| 연결 상품 | 프로필에서 그 씨앗 키워드의 근거가 된 카탈로그 상품 |
| 한계·기획이 정할 것 | 코드 고정 문장 |
| 한 줄 `line` | 근거 숫자만 데이터 블록에 넣고 묶어서 LLM 한 번. 숫자 검증·단정 금지에 걸리면 고정 문장 |

순서는 코드 점수: 검색 max(0, 배수−1) + 0.5 × max(0, 숏츠 배수−1) + 연결 상품 0.25. 브리프 칸 모양이 바뀌어 규칙 버전 `SHAPE`를 올렸다.

### 다른 업종 확인 (도메인 프로필 → 출처 목록 → 수집 계획)

`tests/test_other_industries.py`: 테스트 안에만 있는 가짜 테넌트 2개(패션 쇼핑몰·식품, 카탈로그만)로 사람 입력(덮어쓰기·키워드·카테고리·출처) 없이 돌린다. 가짜 LLM(데이터 블록만 보는 규칙 함수) + 저장된 YouTube 카테고리 응답(replay). 업종·씨앗 키워드·쇼핑 분야(패션 50000000, 식품 50000006)가 서로 다르게 나오고, 출판·교육 전용 출처(정보나루)는 코드가 업종 불일치로 버리며, 근거 없는 키워드도 버린다. 실제 고객사 설정은 만들지 않는다.

- **한계**: 새 업종에 맞는 출처가 목록에 없으면 커넥터를 한 번 추가해야 한다 (출처 표에 한 줄 + 커넥터 1개)

### domain_subtopics (하위 주제 넓히기)

씨앗 키워드(상품 이름 위주)만으로는 기회 카드가 상품 목록이 되어, 씨앗 주변에서 실제로 같이 소비되는 말을 찾는다.

1. **검색**(코드): 씨앗마다 YouTube `search.list`(q=씨앗, `videoDuration=short`, regionCode=KR, relevanceLanguage=ko, 조회수 순, 최근 `published_days` 90일, 최대 50개) + `videos.list`. 길이 `[shorts] max_seconds` 이하·한국어(`shorts_stats.is_language`)만. 씨앗마다 결과를 `cache_days` 7일 저장해 다시 쓰고, 새 검색은 하루 `daily_searches` 5번까지(넘으면 다음 날, 지난 결과가 있으면 그것을 쓰고 이유)
2. **후보**(코드): 결과 영상 태그 + 제목 구(2~3어절)를 trend_candidates와 같은 정리(`clean_tag`)·별칭 표(`canonical`)로 센다. 빼는 것: 씨앗 자체·씨앗을 품은 말, 회사 제외 목록(`exclude_tags`), **쌓인 인기 영상 문서 빈도로 찾은 흔한 말**(`common_terms`, `[candidates.auto_exclude]` 기준 그대로). `min_videos`(2)개 이상 영상에 나온 것만. 같은 대상 묶기: 공백·대소문자만 다른 이름은 하나, 같은 영상들에만 나온 더 긴 구는 짧은 이름에(`merged`). 씨앗마다 `max_per_seed` 5개, 전체 `max_total` 20개(숏츠 수 많은 순)
3. **축 분류**(LLM 한 번): 프로필의 `subtopic_axes`(프로필 LLM이 고객 축에 맞춰 2~5개 정함, 사람이 나열하지 않음. 덮어쓰기 `[profile.override] subtopic_axes`) + 기타 중에서만. 목록 밖 답은 기타
4. **수집 계획**: 오늘 새로 할 검색 수(캐시 밖, 하루 상한 안)를 `subtopic_searches`·`subtopic_units`(검색 1번 = 101단위)로 YouTube 할당량 합계에 넣는다. 상한을 넘으면 YouTube 항목 키워드를 먼저 자르고(카테고리는 하나 남김), 그래도 넘으면 하위 주제 검색 수를 줄인다
5. **기회 카드**: 하위 주제(숏츠 수 많은 순, 최대 5) 단위. 상품은 그 씨앗의 근거 상품을 "연결 상품"으로만. 하위 주제 숏츠가 표본 기준(5) 이상이면 숏츠 적합도 = 그 숏츠 조회 중앙값 ÷ 전체 숏츠 중앙값, 목표 참고값도 그 중앙값. 하위 주제가 없으면 예전처럼 씨앗 키워드 단위(한계에 적음)

### trend_age (연령대별 상승)

주제마다 연령대별로 네이버 검색어 트렌드를 따로 조회해, 그 연령대 안에서 평소 대비 몇 배인지 코드로 낸다. LLM을 쓰지 않는다.

1. **확인(2026-11-04)**: 네이버 API HUB `search-trend`는 요청 본문 `ages`(연령 코드 1~11)·`gender`(m·f)를 받는다. 실제 호출 3번 모두 200이었고, 연령대마다 다른 값이 왔다. 커넥터 `search_trend(query, ages=...)`가 `ages`를 붙인다(캐시 키도 본문이라 연령대마다 따로 캐시)
2. **대상**(코드가 고름, 손으로 나열하지 않음): 오늘 급상승 판정(`today_board`)에서 `rising_topics`(3) → 지금 인기 상위(`trend_popular`, 커넥터 없이)에서 `popular_topics`(5) → 시즌 키워드에서 `season_topics`(2). 시즌 키워드는 오늘 만든 기획안의 시즌 확인에서 기준(`season_min_ratio`)을 넘은 것이고, 없으면 카탈로그 카테고리 이름·상품 핵심어(`season_keywords`). 이름이나 별칭이 겹치면 한 주제로 보고 출처(`sources`)만 합친다. 요청에 `keywords`가 있으면 그것만
3. **연령대**: `[age.bands]` 이름 → 네이버 연령 코드 묶음(기본 10대=2, 20대=3·4, 30대=5·6, 40대=7·8, 50대 이상=9·10·11). 연령대마다 한 번씩 불러 trend_scan과 같은 `judge_series`로 판정하고, `ratio`(평소 대비 배수)·최근 값·기준선을 낸다. `top_band`는 급상승 판정 연령대가 있으면 그중, 없으면 전체에서 배수가 가장 큰 연령대
4. **상대값**: 요청마다 최댓값이 100이라 **연령대끼리 절대량은 비교하지 않는다**(응답 `note`에 같은 문장). "20대가 가장 크게 올랐다"는 각 연령대 안의 평소 대비 배수끼리의 비교다
5. **호출 상한·캐시**: 하루 네이버 호출 `daily_calls`(80). 주제 하나에 연령대 수만큼 부르는데, 남은 호출이 모자라면 그 주제만 unknown + "하루 네이버 호출 상한(N번)에 닿아 이 주제는 보지 않음". 값을 받은 주제는 하루 캐시(`backend/.data/trend_age.db`, 같은 이름·연령대 설정이면 그날 다시 부르지 않음). 검색량이 적어 빈 시계열이거나 호출이 실패한 연령대는 그 연령대만 unknown + 이유(0으로 채우지 않음)
6. 2026-11-04 실제 데이터: 주제 10개 × 연령대 5개 = 네이버 50번. 예) t1 20대 3.76배(전 연령대 급상승), 모바일게임 40대만 급상승 1.54배


### shorts_stats (숏츠 통계)

**숫자 정리(#547):**
- **대표값 = 중앙값**: 형식·카테고리마다 `median_views`(조회수 중앙값)를 대표값으로 쓰고 순서도 그것으로 정한다. 몇 개의 큰 영상이 평균을 끌어올리는 것을 막으려는 것이고, 평균(`avg_views`)은 보조 칸으로 남긴다. 시간당 조회도 `median_views_per_hour`
- **측정 중**: 두 번 본 간격이 `[shorts] min_recheck_minutes`(50분) 미만이면 시간당 조회를 잰 값으로 쓰지 않는다(`velocity_status: measuring`, 묶음의 `velocity_measuring` 수, 이유 "측정 중 N개 (다시 본 간격 50분 미만)"). 인기 숏츠는 최근 두 인기 스냅샷 간격, 검색 숏츠는 마지막 두 번 본 간격
- **같은 시점 값**: 예시 아이디어(`promo_concepts`)의 숏츠 형식 재료는 숏츠 통계의 중앙값 상위 N개이고 값마다 숏츠 통계 기준 시각(`snapshot_at`)을 붙인다. 인사이트 브리프의 숏츠 형식도 같은 호출의 숏츠 통계에서 표본 수·중앙값·기준 시각(`shorts_format_samples`·`shorts_format_median_views`·`shorts_format_at`)을 가져온다

YouTube 숏츠를 카테고리별·형식별로 센다. 숫자는 코드, 분류는 LLM이 정해진 목록 안에서만.

1. **숏츠 고르기: 길이로 판단한다.** YouTube API에는 숏츠 표시가 없어서, 길이(`contentDetails.duration`, `video_details` 표)가 `[shorts] max_seconds`(60초) 이하인 영상을 숏츠로 본다. 길이를 모르는 영상은 빼고 수(`unknown_length`)만 센다
2. 표본
   - 최신 YouTube 인기 스냅샷의 숏츠. 2026-11-03에는 인기 목록에 60초 이하 영상이 없었다(0개)
   - **표본 넓히기**: 인기 스냅샷에 많이 나온 YouTube 카테고리(데이터에서 고름) 순으로 짧은 영상 검색(`search.list`, `videoDuration=short`, `regionCode=KR`, 최근 `search_days`(7)일, 조회수 순, 50개)을 **하루 1번**, 상한 `search_calls`(8번, 검색 1번 = 100단위). 결과는 `backend/.data/shorts.db`(`DATA_SHORTS_DB`)에 남겨 같은 날은 다시 부르지 않는다. 검색 실패는 그 카테고리만 unknown + 이유, 할당량 초과면 남은 검색은 하지 않고 unknown
   - **한국 숏츠만**(`[shorts] language = "ko"`): `regionCode=KR`은 "한국에서 볼 수 있는 영상"이라 해외 영상이 대부분이었다(2026-11-03 검색 97개 중 한국 숏츠 몇 개). 그래서 검색에 `relevanceLanguage=ko`를 더하고, 인기·검색 숏츠 모두 **영상 언어(`defaultAudioLanguage`·`defaultLanguage`)가 한국어이거나 제목·채널 이름에 한글이 있는 것만** 남긴다. 뺀 수는 `foreign_excluded`. 언어 조건을 바꾸면 같은 날도 다시 검색한다
3. 분류(LLM, 묶어서 최대 20개씩, 저장해 재사용): 카테고리는 주제 분류기(`classify_topics`, 제목 + 태그), 형식은 **챌린지·상황극·스케치·리액션·정보·팁·비포애프터·브이로그·밈 패러디·기타**에서 하나(목록 밖은 기타, 실패는 unknown). 형식은 `shorts.db`에 `retention_days`(30)일 남긴다
4. 계산(코드): 카테고리별·형식별 숏츠 수, 평균 조회수, 평균 시간당 조회 증가(잰 수 `velocity_videos`), 조회수 상위 3개(제목·채널·조회수). **표본이 `min_sample`(5)개 미만이면 unknown + "표본 부족"**

5. **시간당 조회 증가**: 인기 숏츠는 최근 두 인기 스냅샷의 조회수 차이로 잰다. 검색 숏츠는 검색할 때 본 조회수를 `shorts.db` `views` 표에 남기고, **다음 실행 때 `videos.list`로 다시 조회**(영상 50개마다 1단위, 상한 `recheck_videos` 200개, 마지막으로 본 지 `recheck_min_hours` 1시간이 지난 것만, 덜 본 것부터)해 **두 번 본 영상만** 계산한다. 한 번만 본 숏츠는 unknown이고 묶음마다 `velocity_pending`·"다음 조회 대기 N개"로 센다. 다시 조회가 실패하면(할당량 초과 등) 그 부분만 `recheck_reason`에 남기고 다음 조회 대기로 둔다

- **한계:** 인기 영상 상위 200개 안(과 조회수 순 검색 결과)에서의 비교라 **이미 뜬 영상끼리 비교**한다(응답 `limitation`에도). 검색 숏츠의 속도는 두 번 본 사이 구간의 평균이라, 다시 조회하는 간격에 따라 값이 달라진다. 형식 분류 정확도는 평가셋이 없어 재지 않는다

### content_recommend (영상 소재 추천, 콘텐츠 루프 2단계)

트렌드 판정 결과로 "지금 만들 만한 영상 소재"를 순위대로 고른다. 순위·상품 연결은 **코드 규칙**, 아이디어 문장과 밈 형식 분류만 LLM이다.

1. 판정: 회사 기본 키워드 그룹(`trends.toml` `[[groups]]`)을 trend_scan과 같은 요청(그룹마다 한 번)·같은 판정 함수로, 후보(YouTube 태그·제목, 인기 검색어)를 `trend_candidates`(`find_candidates`)로 본다
2. 합치기: 기본 그룹과 후보의 이름(같이 본 이름 포함, 공백·대소문자 무시)이 겹치면 한 소재다. 그 그룹이 급상승이면 그 판정에 YouTube 근거를 붙인다. 소재가 나온 곳은 `origins`(keyword_group·youtube_tag·youtube_title·trending_search)
3. **급상승(rising)만** 후보다. 평범·판정 불가는 넣지 않는다. 급상승이 없으면 빈 목록 + 이유 `이번 기간 급상승한 소재가 없음`(네이버를 못 읽은 것이 있으면 그 이름도)이고 **억지로 채우지 않는다**
4. 점수 = `z_weight` × z + (검색 급상승 + YouTube 근거 **두 소스**면 `two_source_bonus`). 큰 순, 같으면 z 큰 순 → 이름. 최대 `max_items`개(요청의 `max_items`가 있으면 그 값). 인기 검색어 + 네이버는 한 소스로 센다(점수 규칙 그대로)
5. **주제 분류·브랜드 안전**(`skills/topic_category.py`, 다른 skill도 쓰는 `classify_topics`): 주제 목록에 **IT**가 있고, 같은 LLM 호출에서 주제와 **별도 칸**으로 실존 인물·팀·리그·상표(게임·작품 제목 포함)가 들었는지(`real_entity`)와 그 이름(`entity_names`, 입력에 실제로 나온 것만 남김)을 받는다. 주제가 게임이어도 실존 선수가 들었으면 표시가 붙는다. 분류는 `backend/.data/topic_categories.db`(`DATA_TOPIC_CATEGORY_DB`)에 남겨 **보관 기간**(`[topics] retention_days`, 기본 30일) 안에는 다시 묻지 않는다(날마다 분류가 바뀌지 않게, unknown은 남기지 않음). 사람용 덮어쓰기 표 `[topics.overrides]`(비어 있음)에 있는 소재는 LLM에 묻지 않는다. 순위 앞 소재(최대 `max_items + max_reference`개)를 **LLM 한 번에 묶어** 분류하고, 결과는 **주제별 하루 캐시**(서버 프로세스 하나, 한국 날짜 기준, unknown은 담지 않음)에 둔다. 소재마다 LLM이 **뉴스·사건·정치·사회·기부·재난·인물·게임·연예·스포츠·생활·취미·교육·책·기타** 중 하나만 고른다. 넣는 것은 소재 이름(같이 본 이름)과 근거 글 최대 3개(YouTube 대표 영상 제목, 없으면 인기 검색어 피드의 관련 뉴스 제목)이고 **데이터 블록(untrusted)에만** 넣는다. 목록 밖 답은 기타(이유만 남김), LLM 설정이 없거나 실패하면 `unknown` + 이유
   - 회사 설정 `exclude_categories`(캣북스: 뉴스·사건·정치·사회·기부·재난·인물)에 들면 **추천하지 않고** `reference_items`(참고용: 키워드·분류·이유·배수)로 따로 낸다. 그만큼 다음 순위로 채운다(분류는 최대 `max_items + max_reference`번)
   - unknown은 빼지 않는다(분류 칸에 unknown + 이유). 추천이 모두 빠지면 빈 목록 + 이유 `급상승 소재가 모두 추천 안 함 분류라 참고용으로만 보여 줌`
6. **상품 연결**(`skills/product_link.py`, #384): 회사 커넥터의 판매 중 상품·카테고리(캣북스 shop-api `GET /api/shop/products`·`/categories`)와 소재마다 최대 `max_products`(3)개를 잇는다
   - 글자 일치: 소재 이름(같이 본 이름 포함, 공백·대소문자 무시)이 상품 이름 낱말(2글자 이상)을 품거나 상품 이름이 소재 이름을 품으면 `name`, 카테고리 이름이 겹치면 `category`
   - 임베딩 유사도: 소재 글(이름 + 근거 글 최대 3개) ↔ 상품 글("이름 (카테고리)" + 설명) 코사인(`eval/product_link`의 rich 모양). **1등 상품 하나만**, 유사도 ≥ `min_similarity`(0.65)이고 1등 − 2등 ≥ `min_margin`(0)일 때 `embedding`으로 잇는다(`margin`에 1등 − 2등). agent-template `LLMClient.embed`, 모델 `DATA_EMBED_MODEL` 또는 `EMBED_MODEL`, 주소·키 `*_EMBED_BASE_URL`·`*_EMBED_API_KEY`(없으면 LLM 주소·키)
   - **임시 기준**: 보정셋 `eval/product_link/`(#403)에서 정해 둔 규칙으로 고른 값이다. 관련·무관 사이 간격이 좁다(관련 질의 정답 최저 0.606 vs 무관 소재 최고 0.573). 뜻만 가까운 질의(예: "그림책 추천")를 보정셋에 더한 뒤 다시 정한다
   - 유사도를 못 재면 `similarity_status: unknown` + 이유 + 까닭 `similarity_failure`: `not_configured`(설정 없음), `config_error`(설정 오류: 임베딩 주소가 LLM 주소와 다른데 키 없음 등), `request_failed`(요청 실패). 이때는 글자 일치만 쓴다
   - 상품을 못 읽으면 `catalog_status: unknown` + 이유(0개로 지어내지 않음)
   - 묶음: 상품과 연결된 소재는 `group: product_linked`(회사 상품과 연결된 소재), 아니면 `general`(일반 화제). 순위는 그대로이고 화면이 두 묶음으로 나눠 보여 준다
7. 회사 관련성: 상품이 연결되면 `related`. 수동 회사 키워드(기본 키워드 그룹 이름·키워드 + `company_keywords`, 덮어쓰기용)와 겹쳐도 `related`. 근거는 `relevance_from`(products·manual_keywords·both·none). **표시만** 하고 순위에서 빼지 않는다
8. **밈 형식**(`skills/meme_format.py`, #385): YouTube 근거가 있는 소재는 대표 영상 제목 최대 5개(`rep_videos`)를 데이터 블록으로 넣어 LLM이 **챌린지·패러디·밈·리액션·하이라이트·정보·리뷰·기타** 중 하나만 고른다. 목록 밖 답은 기타(이유만 남기고 답 원문은 남기지 않음). YouTube 근거 영상이 없거나 LLM 설정이 없거나 실패하면 `unknown` + 이유(기타로 채우지 않음). 권장 형식: 정보·리뷰 롱폼, 그 밖 숏폼
9. 아이디어 문장(LLM, `explain.explain_idea`): 소재마다 1~2문장 + 형식(숏폼·롱폼). 프롬프트에 넣는 숫자는 **배수 하나**("평소보다 N배")뿐이고 z·YouTube 영상 수는 넣지 않는다. 규칙: 숫자는 "평소보다 N배"만, z·점수 같은 통계 용어 금지, **상품은 연결된 실제 상품(데이터 블록 `연결 상품`)만 이름 그대로**, 연결 상품이 없으면 상품 이름을 쓰지 않고 회사 소개(`company_intro`)의 고객·분야는 보조로만. 밈 형식이 있으면 "이 형식에 맞는 영상으로(권장 형식)". 이름(외부 태그·같이 본 이름·연결 상품)은 **데이터 블록(untrusted)에만** 넣는다. 숫자 검증은 `explain`과 같은 규칙(이름 속 숫자 t1의 1은 허용). **연결되지 않은 회사 상품 이름이 나오면 버린다**(`product_invalid`). 실패하면 고정 문장("〈키워드〉 검색이 평소의 N배로 올랐습니다… 짧은 영상으로 다뤄 볼 만합니다") + 권장 형식(없으면 숏폼), `fallback: true`와 이유

- 네이버 호출: trend_scan·trend_candidates와 요청 모양이 같아 **같은 프로세스에서 오늘 이미 부른 요청은 커넥터 하루 캐시**에서 나온다. 이번 실행에서 실제로 부른 수는 `naver_calls`(커넥터 `calls_today` 전후 차이). 2026-11-03 새 프로세스 한 번: 25번(기본 그룹 4 + YouTube 후보 16 + 인기 검색어 5)
- 설정 `[recommend]`: `max_items` 5, `z_weight` 1.0, `two_source_bonus` 2.0, `max_products` 3, `min_similarity` 0.65·`link_top_only` true·`min_margin` 0(임시 기준), `use_similarity` true, `exclude_categories`(캣북스: 뉴스·사건·정치·사회·기부·재난·인물), `max_reference` 5, `company_keywords`(덮어쓰기용, 캣북스: 책·도서·출판·캣북스·반려묘·집사), `company_intro`(보조). 설정이 없으면 같은 숫자에 회사 키워드는 기본 그룹만, 회사 소개가 없으면 프롬프트에 "상품·고객을 지어내지 말 것"
- 출력: 소재마다 순위, 키워드, 같이 본 이름, 출처(`sources`·`origins`), 점수 근거, 연결 상품(`products`: id·이름·카테고리·근거·유사도), 회사 관련성·근거·겹친 수동 키워드, 밈 형식·이유·쓴 제목 수, 아이디어 문장, 형식, fallback·이유. 그리고 네이버 호출 수, 못 읽은 그룹·후보, YouTube 후보 상태, 상품 목록 상태(`catalog_*`), 유사도 상태(`similarity_*`·`similarity_failure`), 소재마다 주제 분류(`topic_category`·이유)·묶음(`group`), 참고용(`reference_items`)·쓴 추천 안 함 분류(`excluded_categories`)
- **한계:** 추천 "품질"은 **평가셋이 없어 재지 않는다**. 순위 규칙·숫자 검증·상품 연결 규칙만 테스트한다. 나중에 게시 성과(콘텐츠 루프 4·5단계, `campaign_effect`)로 평가한다. 가중치(1.0·2.0)도 성과 자료가 없어 정한 값이다. **밈 형식·주제 분류 정확도도 평가셋이 없어 재지 않는다**(영상 내용이 아니라 제목·뉴스 제목만 본다). 주제 분류는 LLM 판단이라 같은 소재가 날마다 달리 분류될 수 있다. 인기 검색어에서만 나온 소재는 YouTube 근거가 없어 밈 형식이 늘 unknown이다. 임베딩 설정이 없으면 뜻이 가까운 상품(예: 책 추천 ↔ 도서)을 글자로 겹칠 때만 잇는다. 임베딩 기준(0.65, 1등만)은 상품 8개·소재 42개 보정셋으로 고른 임시 값이다. 인기 검색어 관련 뉴스 제목은 이 기능 뒤에 받은 스냅샷부터 있다(그 전 스냅샷은 이름만)

### 설명 문장(LLM)과 템플릿 대체 이유
- 숫자는 코드가 계산하고 LLM은 문장만 쓴다(`explain.py`). 문장에 넘긴 facts에 없는 숫자가 있거나 LLM이 실패하면 고정 템플릿으로 바꾸고 `fallback=true`다.
- 바꾼 이유를 남긴다 (`AgentResult.fallback_reason`, 영향 분석·이상 탐지·KPI·업무 리포트):

| 이유 | 언제 |
| --- | --- |
| `llm_unavailable` | 모델 설정이 없음 (`DATA_LLM_MODEL`·`DATA_LLM_BASE_URL` 또는 공통 `LLM_MODEL`·`LLM_BASE_URL`) |
| `llm_error` | 호출 실패 (HTTP 오류·시간 초과·연결 실패) |
| `format_error` | 응답이 형식(스키마)에 맞지 않음 (다시 요청해도), 또는 문장이 비어 있음 |
| `number_invalid` | 문장에 계산에 없는 숫자가 있음. 걸린 숫자는 `fallback_numbers` |
| `product_invalid` | (영상 아이디어 문장만) 연결되지 않은 회사 상품 이름이 문장에 있음 |

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


### 주제 분류 목록: K-pop·음악 (2026-11-03)

- 분류 목록에 `K-pop·음악`을 더했다(노래·음원·가수·아이돌·뮤직비디오·커버·안무·음악 방송). `연예·스포츠`는 방송·예능·드라마·영화·연예인 소식·스포츠 경기로 남긴다.
- 저장된 분류 중 **연예·스포츠만** 한 번 다시 묻는다(`topic_category.RECHECK`, 분류 목록 판 `CATEGORY_VERSION` = 2). 그 주제가 다시 후보로 나올 때 묻고, 다른 분류는 그대로 재사용한다.
- 지금 인기 화면 칸: 게임, 뉴스·사건, 연예·스포츠, K-pop·음악(YouTube 카테고리 10), IT.
