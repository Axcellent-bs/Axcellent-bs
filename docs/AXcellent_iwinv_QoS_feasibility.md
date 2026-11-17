# AXcellent iwinv 이관: QoS 적용 타당성 및 트래픽 최적화 전수조사

- 작성일: 2026-11-17 / 성격: READ-ONLY 조사 (코드·설정·서버 변경 없음, 운영 API 호출 없음, 실제 tc/nft 실행 없음)
- 조사 대상: `C:\projects\Axcellent-bs\Axcellent-bs` 현재 체크아웃 (`backend/`)
- 표기: **[코드]** = 파일·줄에서 직접 확인한 사실, **[문서]** = 공식 문서에서 확인, **[추정]** = 측정하지 않은 수치·가정, **[미확인]** = 확인하지 못한 항목
- 경로는 모두 `backend/` 기준 상대경로이며 줄 번호는 조사 시점 체크아웃 기준이다.

---

## 1. Executive Summary

1. **QoS만으로는 계약 위험이 해결되지 않는다.** 속도 제한(예: 40Mbps)은 "순간 대역폭 50Mbps 초과" 위험만 줄인다. "인바운드가 아웃바운드보다 많음" 조건과 월 총량(2.18TB)은 그대로다. iwinv 공식 문서에서는 이 조건 자체를 찾지 못했고(전화 구두 안내뿐) 서면 확인이 필요하다.
2. **지시서의 "10분마다 전량 재조회"는 코드상 타이머가 아니라 "전량 읽기를 허용하는 최소 간격(상한)"이다.** finance·settlement 모두 평소에는 최근 3일 주문만 `since`로 받고, 600초가 지난 뒤 *어떤 읽기가 시작될 때* 전체를 읽는다(`finance_agent/books.py:56-58,542-546`, `support_agent/settlement_books.py:41-42,386-392`). 하루 144회×2는 모든 트리거가 매번 전량으로 이어질 때의 **상한**이고, finance는 화면 요청·사람 결정에 따라 달라진다(§2, §3).
3. **지시서 수치(30만 건, 0.84MB/1000건, 252MB, 월 2.18TB)는 산술적으로 서로 맞는다**(300×0.84=252MB, 252MB×288×30=2.18TB). 다만 코드가 말하는 페이지 크기·전량 읽기 구조와 달리, (a) 코드 주석의 실측은 33,850건 22MB(=0.65MB/1000건)로 0.84와 다르고, (b) 주문이 계속 쌓이면 전량 크기가 매일 커지는 구조라 2.18TB는 **고정값이 아니라 현 시점 스냅숏**이다 [추정].
4. **QoS보다 효과가 큰 것은 증분 동기화다.** 기존 `GET /api/shop/sales-export`(`updated_at,order_id` 커서)가 이미 있고 data 서비스가 5분 주기로 쓰고 있다(`domains/data/src/data_agent/sales_collect.py:17,48-51`). 그러나 **재무·정산 용도로는 그대로 쓸 수 없다**: 결제 상세·`delivered_at`·품목 `seller_id`가 응답에 없고, `mark_delivered/mark_shipped`가 `updated_at`을 갱신하지 않는다(`shared/shop-api/src/ax_shop_api/service.py:869,891`). 정산 확정 기준인 `delivered_at` 변경이 증분에서 누락되는 것이 가장 큰 설계 함정이다(§8).
5. **판정: Conditional Go.** 권고 조합은 S7(공통 캐시 + 증분 + 일일 재조정 + 보수적 QoS)이며, QoS는 보조 안전장치다. 계약 불확실성은 서면 확인 전까지 남는다(§11, §16).

---

## 2. 현재 실제 네트워크 호출 구조

### 2.1 주문 전량 조회 경로 (두 군데에 같은 구조가 복제돼 있음)

| 항목 | finance (재무) | settlement (판매자 정산, support 도메인) |
|---|---|---|
| 조회 함수 | `_get_orders` — `domains/finance/src/finance_agent/books.py:333-365` | `_get_orders` — `domains/support/src/support_agent/settlement_books.py:345-373` |
| 엔드포인트 | `GET {shop}/api/shop/orders` | 동일 |
| 페이지 크기 | `ORDERS_PAGE = 1000` (books.py:50) | `ORDERS_PAGE = 1000` (settlement_books.py:39) |
| 페이지 방식 | `meta.next_cursor`(마지막 order_id) 커서 (books.py:361-365) | 동일 (settlement_books.py:369-373) |
| 서버 최대 | `ORDER_PAGE_MAX=1000`, 기본 200 (`shared/shop-api/src/ax_shop_api/service.py:49-50`) | 동일 |
| 읽기 타임아웃 | 페이지당 `ORDERS_CALL_SECONDS = 30.0` (books.py:48,347) | 30.0 (settlement_books.py:38,355) |
| 그 밖 호출 타임아웃 | `CALL_SECONDS = 5.0` (books.py:45) | `CALL_SECONDS = 10.0` (settlement_books.py:37) |
| 재시도·백오프 | **없음**. 한 페이지라도 실패하면 `None` 반환 → 전체 "못 받음" (books.py:349-352) | 동일(settlement_books.py:357-360) |
| 압축 | 클라이언트는 `httpx.AsyncClient()` 기본값 [일반 지식: httpx는 gzip/deflate Accept-Encoding을 기본 전송 — 코드에서 별도 설정 없음]. **서버 쪽 압축 없음**: `backend/**/*.py`에서 `GZipMiddleware`·`gzip`·`brotli` 검색 결과 0건 | 동일 |
| 응답 방식 | shop-api `list_orders`는 동기 핸들러가 dict를 반환(`shared/shop-api/src/ax_shop_api/main.py:575-585`) → FastAPI가 한 페이지 전체를 JSON으로 직렬화해 한 번에 전송. `StreamingResponse` 미사용(backend 전체에서 workflow-core SSE만 사용) | 동일 |
| 장부 적재 | 모든 페이지를 `orders.extend(data)`로 **전량 메모리에 누적** 후 `OrderStore.merge`로 정규화 (books.py:360, 548-567) | 동일 (settlement_books.py:368, 394-416) |
| 갱신 방식 | `since` 없음=전체 교체, `since` 있음=최근 구간 통째 교체 (books.py:560-566) | 동일 + 판매자별 환불 나눔도 같은 구간만 재계산 (settlement_books.py:406-416) |
| 서버 프로세스 | `uvicorn` 단일 프로세스 기동 (`backend/scripts/run_all.sh:435`, `--workers` 없음) | 동일 |

- `since`는 **접수 시각(created_at)** 기준이다: `created_at >= ?` (`shared/shop-api/src/ax_shop_api/service.py:300,311-313`). 즉 `since`는 "최근 N일에 *생긴* 주문"이지 "최근 *바뀐* 주문"이 아니다. 그래서 오래된 주문의 환불·배송완료는 전량 읽기로만 맞춘다(books.py:13-14,54-58 주석).
- 한 번의 장부 읽기는 주문 외에 `products`, `sellers`, `coupons`(+finance는 `safety/executions`)도 매번 함께 읽는다(books.py:601-609, settlement_books.py:459-462). 이 응답 크기는 **[미확인]**.
- 주문 전량 1회 = 30만 건 ÷ 1000 = **300페이지**(요청 300회). 두 서비스 합 600요청/주기(상한).

### 2.2 "600초"의 정체

- finance: `FULL_REFRESH_SECONDS = 600.0` (books.py:58). 타이머가 아니라 `OrderStore.since()`의 임계값이다(books.py:542-546): 마지막 전량 읽기가 600초 넘었고 *읽기가 시작되면* 전량, 아니면 최근 3일(`RECENT_DAYS=3`, books.py:56).
- finance의 읽기 트리거는 요청 구동이다(`cached_live_books`, books.py:721-743): 캐시 30초 초과(`LIVE_CACHE_SECONDS`) 후 *조회가 오면* 뒤에서 새로 읽는다. 주기 타이머는 없고, 켤 때 1회(`screen_cache.prime`, `main.py:288`, `screen_cache.py:125-135`)와 최근 5분 안에 본 화면 미리 만들기(`WARM_WINDOW_SECONDS=300`, screen_cache.py:71, `on_new_books`) 이후의 화면 요청이 트리거다. 600초 자동 테스트(`FINANCE_SELF_TEST_SECONDS` 기본 600, `main.py:55-57`)는 `_tiny_books()`를 쓰므로(`selftest.py:59,116`) 판매 서버를 읽지 **않는다**.
- finance 사람 결정 경로 6곳은 **600초와 무관하게 전량**을 읽는다(`handlers.py:140-146` `fresh_books` → `load_books(fresh=True)` → `_collect_live(full=True)`; 호출 위치 `finance_api.py:175,575,650,736,779,868`). 이것이 지시서의 계산에서 빠진 추가 전량 트래픽 원천이다.
- settlement: 타이머가 실제로 존재한다. `SUPPORT_SETTLEMENT_AUTO_REQUEST_SECONDS` 기본 600초(`settlement_auto.py:37-38,54-59`), 첫 실행 30초 지연(`:39,126`), 매 회차 `sb.load_books()`(`:91`) → 캐시 30초 초과면 `_start(recent=False)`(`settlement_books.py:598-602`) → `since()`가 600초 경과 여부로 전량/최근을 가른다(`:386-392`). 회차 간격이 600초+소요시간이라, 이전 전량이 끝난 시각(`full_at`)부터 다음 트리거까지가 600초 미만이면 증분이고 다음 회차에 전량이 되는 식으로 **전량이 600~1200초마다** 나올 가능성이 있다 [추정, 코드 논리상의 추론].
- 지급 요청(`fresh=True`)은 전량이 아니라 최근 주문만 읽는다(`settlement_books.py:12-14,594-597`).

### 2.3 시작 시각·병렬성·중복

- 두 서비스는 **서로 조율하지 않는다**(공유 캐시·락 없음, 락은 서비스 내부 `OrderStore.lock`뿐: books.py:540,587 / settlement_books.py:384,454). 서비스 내부 병렬은 없다(한 번에 읽기 하나, 페이지는 직렬).
- 기동 시각: 둘 다 서버 기동 즉시 첫 전량을 읽는다 — finance `screen_cache.prime()`(`main.py:288`), settlement `settlement_books.prime()`(`support/src/support_agent/main.py:113`, `settlement_books.py:568-572`). 같은 시각에 재배포·재시작하면 **두 전량 읽기가 동시에 겹친다**(재시작 시 `_STORE`가 비므로 매번 전량).
- 같은 이유로, 재시작이 잦으면(재배포·장애 복구) 월 총량이 올라간다. 인메모리 장부(`OrderStore`)에는 디스크 영속화가 없다 [코드: books.py:527-540 인메모리 dict만].
- 세 번째 소비자: data 서비스가 `sales-export`를 5분 주기·증분으로 읽는다(`data_agent/sales_collect.py:17,48-51`). support의 `fetch_sold_qty`는 최대 30페이지(`shop_client.py:240-241,257-306`).

### 2.4 코드와 지시서 수치 비교

| 항목 | 지시서 | 코드/재계산 | 차이 |
|---|---|---|---|
| 주문 수 | 약 30만 | 코드에 상수 없음. 주석에 "18만 건"(2026-11-06 서버, books.py:15-16), "33,850건 22MB"(books.py:47) | 30만은 [미확인], 18만→30만 증가는 주석과 정합 |
| 1000건 응답 | 0.84MB | 주석 실측은 22MB/33,850건=0.65MB/1000건 | **약 23% 차이** (품목 수 증가 등 가능) |
| 전량 1회 | 252MB | 300×0.84=252MB (일치), 0.65 기준이면 195MB | 값은 산술상 일치, 근거 실측은 불일치 |
| 주기 | 600초, 하루 144회/서비스 | settlement만 타이머(상한 144), finance는 요청 구동 + 사람 결정 시 전량 | **구조가 다름** |
| 월 총량 | 2.18TB | 252MB×288×30 = 2,177GB 일치 | 상한 가정이며 고정값 아님 |

월 2.18TB의 의미: 평균 **6.7Mbps**(2.177e12×8 ÷ 2.592e6초) [재계산]. 이 평균은 속도 제한과 무관하게 나오는 값이라 5Mbps 제한으로는 월 최대 1.62TB(5Mbps×30일)밖에 못 보내므로 **적체**가 발생한다(§6).

---

## 3. finance와 settlement 전량 조회 원인

1. **구조적 원인 — `since`가 created_at 기준**. 환불·배송완료·취소는 주문의 `created_at`을 바꾸지 않으므로 "최근 3일" 필터로는 오래된 주문의 변경을 알 수 없다. 코드는 정합성을 위해 600초마다 전량으로 맞춘다(books.py:13-14, settlement_books.py:7-9). 변경 감지용 `updated_at` 기반 API는 있으나(sales-export) 재무·정산이 요구하는 필드가 없다(§8).
2. **원인 — 정합성 우선(fail-closed)**: "빠진 장부로 정산금을 내지 않는다"(settlement_books.py:5-6). 한 페이지 실패 시 전체 폐기라 중간 재개가 불가능.
3. **원인 — 장부가 서비스 두 개에 복제**: 2026-11-12 정산이 finance 코드를 "옮겨" 복제(settlement_books.py:3). 같은 데이터를 두 번 받는다.
4. **원인 — 인메모리·무영속**: 재시작마다 전량.
5. **원인 — 사람 결정 경로의 `fresh=True`**(finance 6곳)가 600초 규칙을 건너뛴다.
6. **원인 — 증분 구간 자체가 큼**: 최근 3일 읽기도 주문이 많이 쌓이면 크다. 데모 주문 생성이 분당 20건(3초마다 1건, `docs/ops/shop-fault-test.md` §1)이고 이를 지속한다고 가정하면 하루 28,800건, 3일 86,400건 ≈ 72MB(0.84MB/1000 기준) [추정]. 즉 "증분"이라 불러도 한 번에 72MB급일 수 있고, 캐시 30초 규칙상 요청이 잦으면 더 자주 발생한다. 30만 건이 약 10.4일치 주문이라는 계산은 이 가정에서 나온 것이며 실제 주문 속도는 **[미확인]**.

---

## 4. QoS 적용 가능 위치

데이터 방향: 판매 서버(온프레미스, 응답 본문 송신) → iwinv VM(수신). 클라우드 **인바운드 = 온프레미스 송신(egress)** 이다.

| 안 | 위치 | 가능성 | 핵심 한계 |
|---|---|---|---|
| A. 온프레미스 송신 QoS | 판매 서버 호스트 또는 온프레 게이트웨이의 송신 NIC/WG 인터페이스 | **가장 적합**. HTB는 송신 shaping 용도[문서]. 필터로 목적지(iwinv VM IP 또는 WG 피어 /32)만 클래스에 넣고 나머지는 default 클래스(무제한)로 둬 물리 NIC 전체 제한을 피할 수 있다 | 온프레미스 구조(Catbooks가 VirtualBox 안인지, 게이트웨이 유무)는 **[미확인]**. 필터 정책은 호스트 root 권한 필요 |
| B. WireGuard 터널 QoS | `wg0` egress(내부 패킷 기준) | 가능. 전용 WG 인터페이스(주문 동기화 전용 피어/서브넷)를 쓰면 다른 서비스와 섞이지 않는다 | 내부 패킷 속도 기준이라 외부 UDP 헤더 오버헤드(약 +4~5%)는 제한값에 안 잡힘 → 상한 대비 여유를 둘 것 [추정, WG 오버헤드 수치는 일반 지식, WG quickstart에는 tc 설명이 없음] |
| C. 클라우드 수신 QoS | iwinv VM tc ingress/IFB/policing | **계약 목적에는 부적합**. 이미 WAN을 지나 VM에 들어온 뒤 제어하므로 iwinv의 측정 지점이 VM 앞단이면 그 바이트는 이미 계수됐다 [추정: iwinv 측정 지점 **[미확인]**]. policing은 드롭→TCP 재전송으로 오히려 중복 전송이 생길 수 있음 | 송신 측 shaping 대비 열세 |
| D. nginx·애플리케이션 계층 | 판매 서버 앞 nginx `limit_rate`, FastAPI 클라이언트 동시성 | `limit_rate`는 **요청당** 제한이고 연결 두 개면 합이 두 배[문서]. 동시 요청이 직렬(서비스당 1)인 현 구조에서는 단순하지만, nginx가 실제 데이터 경로(판매 서버 앞)에 있는지는 **[미확인]**. 앱 측은 페이지 사이 sleep으로 제한 가능 | 평균 제한만 가능, 헤더 오버헤드 미반영 |
| E. 조회 주기·분산 | 앱 코드 | 가능. 두 서비스 읽기 시각 분리·단일 로더. 총량은 안 줄고 **피크 중첩만** 제거 | 총량 불변 |

결론: 송신 측(A 또는 B)이 유일하게 의미 있는 QoS이고, 이것도 "순간 대역폭"만 다룬다. Cloudflare Tunnel이 AX↔판매 서버 경로에 끼는 경우 터널 트래픽은 엣지와의 공용 연결이라 선별 shaping이 어렵다 **[미확인: 실제 경로]**.

---

## 5. Linux tc 및 네트워크 계층 비교

| 기법 | 방향 | 역할 | 이번 용도 적합성 |
|---|---|---|---|
| HTB | egress | rate/ceil로 클래스별 대역폭 분할, 클래스별 leaf qdisc[문서: man7 tc-htb] | 적합 (주문 동기화 클래스 ceil 설정 + default 클래스 무제한) |
| TBF | egress | 단일 토큰 버킷 | 인터페이스 전체를 한 번에 제한할 때만 적합. 선별 제한엔 부적합 [일반 지식] |
| fq_codel | egress/ingress | 흐름 공정 큐 + 큐 지연 관리, **속도 제한 기능은 없음**[문서: man7 tc-fq_codel, 기본 limit 10240패킷·target 5ms·interval 100ms] | HTB 리프로 붙여 지연 억제용. 단독으로 50Mbps를 못 막음 |
| IFB + ingress redirect | ingress를 가상 장치로 돌려 egress처럼 shaping | 공식 페이지 접근 실패(403) → **[미확인]**, 일반 지식으로는 가능하나 §4C의 한계 |
| ingress policing | ingress | 초과분 드롭 | 드롭 → 재전송 → 같은 바이트 재수신 위험 |
| nginx `limit_rate` | 앱 L7 | 요청당 응답 속도[문서: nginx.org core module] | 데이터 경로에 nginx가 있을 때만 |

설계 시 주의 [추정]: ① 순간값(1초 평균)과 5분 평균이 다르므로 HTB `burst/cburst`를 작게 둔다. ② WG는 PMTU·MTU 1420 전후에서 단편화가 생기면 효율이 떨어진다. ③ iwinv가 보는 값은 아마 VM NIC의 외부 패킷 기준이라 제한값을 계약 기준에서 5~10% 낮게 잡는다.

---

## 6. 5~50Mbps 속도별 정량 계산표

가정: 1회 전량 = 252MB = **2,016Mbit**(MB=10^6B, 8bit). 이론 최솟값 = 2016 ÷ 속도. 오버헤드(TCP/TLS/HTTP/WG) 포함값은 **효율 90% 가정 [추정]**(÷0.9). 두 서비스 동시 = 4,032Mbit.

| 속도(Mbps) | 1서비스 이론 최솟값(초) | 1서비스 오버헤드 포함 [추정] | 2서비스 동시 이론(초) | 2서비스 동시 포함 [추정] | 600초 내 완료 | 월 최대 전송 가능량(속도×30일) |
|---|---|---|---|---|---|---|
| 5 | 403 | 448 | 806 | 896 | 1서비스만 가능, 동시 **불가** | 1.62TB (<2.18TB → 적체) |
| 10 | 202 | 224 | 403 | 448 | 가능(여유 25%) | 3.24TB |
| 20 | 101 | 112 | 202 | 224 | 가능 | 6.5TB |
| 30 | 67 | 75 | 134 | 149 | 가능 | 9.7TB |
| 40 | 50 | 56 | 101 | 112 | 가능 | 13.0TB |
| 45 | 45 | 50 | 90 | 100 | 가능 | 14.6TB |
| 50 | 40 | 45 | 81 | 90 | 가능 | 16.2TB |
| 제한 없음 | 업로드 100Mbps 가정 시 20초(미실측) | ≈22초 [추정] | 40초 | ≈45초 | 가능 | 이론 32TB |

항목별 평가 (지시서 5절 9개 항목):

| 속도 | 타임아웃 위험 | 재시도 시 중복 | CPU/RAM | 다른 API 지연 | 월 총량 | 50Mbps 계약과의 관계 |
|---|---|---|---|---|---|---|
| 5 | 페이지 단위 0.84MB는 1.3초라 `30초` 읽기 타임아웃엔 안전. 그러나 전량이 600초를 넘어 `FULL_REFRESH` 중첩, 읽기 겹침 | 실패 시 전체 재다운로드(§7). 적체로 누적 | 읽기가 길어져 메모리 보유 시간 증가(전량 누적 구조) | 같은 링크를 쓰는 다른 트래픽이 밀림 | **불변**, 처리 못 해 지연 | 순간 5Mbps로 안전하지만 서비스 불가 수준 |
| 10 | 낮음 | 동시 시 75% 점유로 재시도 흡수 여유 작음 | 보유 시간 약 4분 | 중간 | 불변 | 순간 위험 해소 |
| 20 | 낮음 | 재시도 1회는 흡수 | 보유 약 2분 | 낮음 | 불변 | 해소 |
| 30 | 낮음 | 흡수 | 약 1~2분 | 낮음 | 불변 | 해소(여유 20Mbps) |
| 40 | 낮음 | 흡수 | 약 1~2분 | 낮음. 단 같은 링크 점유 시간 16.8% | 불변 | 해소, 단 다른 인바운드(LLM 응답·미디어·패키지)와 합산해 50Mbps에 근접 가능 [추정] |
| 45~50 | 낮음 | 흡수 | 짧음 | 오버헤드 포함 시 50 초과 가능 | 불변 | **여유 없음**, 외부 패킷 기준이면 초과 위험 |
| 제한 없음 | 낮음 | — | 짧음, 메모리 피크는 동일 | 링크 포화 가능 | 불변 | 100Mbps 가정 회선에서는 **순간 50Mbps 초과** 가능 [추정] |

점유율 (40Mbps 기준 [추정]): 전량 1회 약 50~56초/600초 = 약 8.4~9.3%, 두 서비스 겹치면 약 17~19%.

핵심: **속도를 어떻게 정하든 월 총량(2.18TB)은 변하지 않는다.** 속도는 피크만 바꾼다.

---

## 7. HTTP 및 Python 타임아웃 영향

- 페이지 요청 읽기 타임아웃은 30초(books.py:48, settlement_books.py:38). httpx에서 숫자 타임아웃은 connect/read/write/pool 각각에 적용되는 값으로, 총 응답 시간이 아니라 **무수신 구간** 기준이다 [일반 지식]. 0.84MB 페이지는 5Mbps에서도 약 1.3~1.5초라 속도 제한 자체로 타임아웃이 나기는 어렵다. 위험은 (a) shop-api의 동기 SQLite 읽기가 길어질 때(books.py:47 주석 "22MB에 8초"), (b) 제한이 너무 낮아 페이지가 30초를 넘기는 5Mbps 미만, (c) WAN 지연·재전송.
- **재시도·백오프가 없다.** 실패 시:
  - finance: `unavailable_core()`이면 캐시를 `None`으로 비우고(books.py:702-703) 다음 요청이 처음부터 전량(또는 최근)으로 다시 시작한다. 지수 백오프가 없어 장애 중 재요청 폭주 시 같은 253MB가 반복될 수 있다 [코드 근거 + 추정].
  - settlement: 직전 장부를 유지하고 다음 트리거(캐시 30초 초과 + 요청)에 다시 읽는다(settlement_books.py:540-551). 부분 재개는 없다.
- 한 페이지(300페이지 중)에서 실패하면 지금까지의 최대 251MB가 버려진다. **재시도 중복 비용이 가장 큰 곳이 이 지점이다.** 해결은 커서 재개(checkpoint)이며 증분 동기화에서 자연스럽게 해결된다.
- `ORDERS_CALL_SECONDS=30`은 shop-api가 느릴 때 오히려 중단을 만들 수 있으므로, 속도 제한 도입 시 페이지 사이 지연을 넣어도 이 값은 건드릴 필요가 없다.

---

## 8. 트래픽 압축 및 증분 조회 가능성

### 8.1 압축
- 서버 측 압축 코드 **없음**(§2.1). `GZipMiddleware`(FastAPI/Starlette) 한 줄로 켤 수 있는 구조이며, 클라이언트(httpx)는 자동 해제한다 [일반 지식].
- JSON 주문 응답은 키 반복이 많아 압축률이 높다. 5~12배 압축을 가정하면 [추정, 미측정] 월 180~435GB 수준.
- 비용: CPU(판매 서버 gzip 압축), 한 페이지 0.84MB는 작아 지연 영향 작음. Brotli는 의존성 추가 필요(`uv.lock` 규칙, CLAUDE.md).
- 가장 중요한 선행 확인: 판매 서버 앞에 Cloudflare/nginx가 있어 이미 압축하는지 **[미확인]**.

### 8.2 기존 증분 API: `sales-export`

`GET /api/shop/sales-export`(`shared/shop-api/src/ax_shop_api/main.py:1265-1274`, `sales_export.py`): `(updated_at, order_id)` 커서, 5초 SETTLE 지연(`sales_export.py:SETTLE`), 환불 시 `updated_at` 갱신(`refunds.py:74-78`), 인덱스 `idx_orders_updated_at`(`db.py:141`).

**재무·정산이 그대로 쓰기 어려운 이유 (코드 확인):**

| 부족한 점 | 근거 | 영향 |
|---|---|---|
| `delivered_at`·`shipped_at`가 응답에 없음 | `contracts/data_access/shop_sales.py` `SalesOrderRow` 필드 목록에 없음 | 판매자 정산 확정 기준(`books.py:143-156` `confirmed_between`)에 필요 |
| 결제 상세(payment_id, amount, created_at, status)가 없음. `payment_status` 한 글자 요약만 | 같은 파일 | `Books._payments`, PG 대사(books.py:114-124,775-832)가 필요 |
| 품목 `seller_id`, `paid_amount` 없음 | `SalesOrderLine` 필드 | 판매자별 환불 분배(`_split_refund`, books.py:439-448) |
| **`mark_delivered`/`mark_shipped`가 `updated_at`을 갱신하지 않음** | `service.py:869-870`, `891-892` (`UPDATE orders SET delivered_at/shipped_at`만) | **증분이 배송완료·발송 변경을 영영 못 받는다 → 정산 누락의 직접 원인** |
| 삭제 이벤트 없음 | `retention.py:1-10`(보유기간 5년 후 CLI 삭제, 기본 dry-run), DELETE는 retention과 반품 동기화(`refunds.py:190`)뿐 | 삭제된 주문이 장부에 영원히 남음 |
| 계약 변경 제약 | CLAUDE.md "계약은 추가만" | 필드 추가만 가능, 가능은 함 |

반면 `status='cancelled'`(`service.py:914`), 결제 성공/실패 확정(`service.py:730,967,988`), 환불(`refunds.py:76`)은 `updated_at`을 갱신해 증분에 잡힌다.

### 8.3 누락 없는 증분 설계안 (제안)

```
[온프레 shop-api]
  orders.updated_at: 발송·도착·취소·환불·결제 변경 시 모두 갱신  ← (1) mark_shipped/mark_delivered 수정
  GET /api/shop/orders-changes?cursor=<updated_at|order_id>&limit=1000
        └ 응답: 기존 `_order_view`와 동일 모양(payments, items.seller_id, paid_amount, delivered_at, refunds)  ← (2) 필드 부족 해소
  order_tombstones(order_id, deleted_at)  ← (3) retention 삭제·관리자 삭제 시 기록
  GET /api/shop/orders-tombstones?since=
  GET /api/shop/orders-digest?bucket=day  → 날짜별 {count, max(updated_at), 해시}  ← (4) 일일 재조정용

[iwinv 단일 로더 "shop-mirror"] (finance·settlement가 공유, 로컬 호출)
  - 커서 영속화(디스크). 재시작해도 전량 불필요
  - 매 사이클: cursor 에서 `overlap=10분` 만큼 되감아 조회 → order_id 기준 upsert(멱등)
  - tombstone 반영(삭제)
  - 일일 재조정(저트래픽 시간대 1회): digest 비교 → 어긋난 날짜만 `since/until` 로 재수신. 최후 수단으로 전량 1회 + 건수 검증
  - 페이지마다 커서 저장(중간 실패 시 이어받기), 지수 백오프+지터
  - gzip 요청
```

누락 시나리오별 방어:

| 변경 | 증분에서 잡히는 조건 | 보강 |
|---|---|---|
| 취소 | `updated_at` 갱신됨(service.py:914) | OK |
| 환불 | 갱신됨(refunds.py:76). 단 주문 `updated_at`이 환불 시각보다 이후면 갱신 안 하는 가드(`updated_at < stamp`) | 환불은 주문과 함께 응답에 포함(`refunds` 배열) |
| 주문 수정/금액 변경 | 쓰기 경로 전수 확인 필요 | **[미확인]**(상품 수정은 `products.updated_at`이라 주문과 무관) → 일일 digest가 안전망 |
| 오래된 주문 상태 변경 | `updated_at` 커서는 *얼마나 오래됐든* 최근에 바뀐 주문을 잡는다 | (1) 적용 필수 |
| 삭제 | 증분으로는 불가 | (3) tombstone + 일일 digest의 `count` 비교 |
| 같은 초 늦게 저장 | SETTLE 5초가 이미 처리 | overlap 10분이 추가 안전장치 |
| 타임존/시계 오차 | `updated_at` UTC 문자열 | overlap |

이 설계는 서버 쪽(shop-api) 변경 2~4개와 클라이언트(로더) 신설이 필요하다. 온프레 판매 서버가 이 저장소의 `shop-api`와 같은 코드인지는 **[미확인]**(저장소 안의 shop-api는 "고객사 쇼핑몰 시뮬"로 설명됨: `backend/README.md:10`).

### 8.4 대안 비교표

월 트래픽 감소는 S0 기준 2,177GB 대비 [추정]. 개발 난이도 낮음/중/높음.

| 대안 | 월 트래픽 감소 | 메모리 | CPU | 정합성 위험 | 지연 허용 | 난이도 | 장애 복구 | 기존 API·테스트 호환 |
|---|---|---|---|---|---|---|---|---|
| gzip/Brotli | 80~90% [추정] | 변화 없음(해제 후 동일) | 판매 서버 +, 수신 + | 없음 | 변화 없음 | 낮음 | 끄면 원복 | 완전 호환 |
| 페이지 크기 조정 | 거의 0(총량 불변) | 피크 소폭 | 소폭 | 없음 | 변화 없음 | 낮음 | 쉬움 | 호환(max 1000 이미 사용) |
| `updated_at` 증분 | 99%+ [추정] | 대폭 감소(전량 raw 누적 제거) | 감소 | (1)(3) 선행 안 하면 **높음** | 수초~수분 | 중~높음 | 커서 되돌리기 | 신규 엔드포인트 추가라 호환 |
| 변경분 동기화 | 위와 동일 | 위 | 위 | 위 | 위 | 위 | 위 | 위 |
| ETag/If-Modified-Since | 낮음(주문 전체가 항상 바뀜) | 0 | 소폭 | 낮음 | 분 | 중 | 쉬움 | 호환. 목록 단위라 효과 작음 |
| 공통 캐시(공유) | 50%(2→1 복사본) | 서비스 두 개분 → 한 개분 | 감소 | 낮음 | 분 | 중 | 캐시 재구축 | `domains` 간 import 금지(CLAUDE.md) → shared 패키지/내부 API 필요 |
| 공통 로더 | 위와 동일 | 위 | 위 | 낮음 | 분 | 중 | 위 | 위 |
| 최근 3일 + 일일 전체 재조정 | 약 98~99% [추정] | 전량 보유 시간 하루 1회로 단축 | 감소 | 일일 재조정 사이의 오래된 주문 변경은 하루 지연 | **최대 24시간** | 낮음 | 쉬움 | 현 구조에서 `FULL_REFRESH_SECONDS`만 86400으로(코드 1줄) — 단, 지급 판단에 하루 지연이 허용되는지 정책 확인 필요 |
| 이벤트/웹훅 | 99%+ | 감소 | 감소 | 유실 시 digest 필요 | 초 | 높음(양방향 인증·재전송) | 재전송 | 신규 |
| 로컬 저장 재사용 | 재시작 시 전량 제거 | 디스크로 이동 | 감소 | 낮음 | 변화 없음 | 중 | 스냅숏 복원 | 호환 |

가장 싼 즉효 후보: `FULL_REFRESH_SECONDS` 600→더 길게 (settlement_books.py:42, books.py:58). 코드 상수 1개로 144→약 1~24회/일. 단 정산 지급 판단의 최신성 요건은 **[미확인]**(정책 결정 필요).

---

## 9. 월 데이터 전송량 절감 시나리오

가정 [모두 추정]: 주문 28,800건/일(데모 분당 20건 유지), 건당 0.84KB, 변경 1건당 평균 3회 재송신(결제·발송·도착), 압축률 8:1, 일일 재조정 전량 1회.

| 시나리오 | 계산 | 월 인바운드(GB) |
|---|---|---|
| S0 현재 상한 | 252MB×288×30 | **2,177** |
| S0 현실 하한 가정(settlement 격회 전량, finance 화면 구동) | (72회 전량 + 72회 3일분72MB)×30 + finance 최소 | 약 700~2,200 [추정] |
| S3 압축 | 2,177÷8 (5~12배: 180~435) | **약 270** |
| S4 증분(서비스별 독립) | 증분 2×~2.2 + 일일 전량 2×7.6 | **약 20** (전량을 압축하면 약 5) |
| S5 공통 캐시+증분 | 증분 2.2~7 + 일일 전량 1×7.6 | **약 10~15** (압축 시 약 2) |

일일 재조정을 전량 대신 digest 비교(어긋난 날짜만)로 하면 위보다 더 작다. 아주 보수적으로 증분 크기를 3배로 봐도 S5는 월 50GB 미만이다 [추정].

---

## 10. RAM 및 CPU 영향

[코드로 확인한 구조]
- 전량 읽기는 **raw 응답 전체를 `orders.extend(data)`로 누적**한 뒤 정규화한다(books.py:360, 553-559). 정규화는 품목·결제 dict를 복사(`i | {...}`, `p | {...}`)한다(books.py:384-394). `Books`에도 리스트로 한 번 더 들고 있고, `memo`가 `len(orders)`를 키로 결과를 붙여 둔다(books.py:81-92).
- finance는 장부 생성 직후 `warm`으로 정렬·결제·PG·대사를 스레드에서 미리 계산한다(books.py:681-691, 699-712).
- 2026-11-06 서버 장애 기록: 주문 18만 건에서 `/health`가 19초 걸렸다(books.py:15-16). 이후 스레드 이관으로 완화.

[추정, 미측정]
- JSON 252MB의 Python dict/list 표현은 대략 5~10배 → 서비스당 1.3~2.5GB 일시 피크, 정규화 사본 포함 시 그 이상. 두 서비스가 겹치면 3~6GB 이상. 16GB VM에서 다른 18~22개 서비스와 함께라면 부담이다.
- 속도 제한은 이 누적 구조를 **오히려 악화**한다: 느릴수록 raw가 메모리에 오래 머문다(§6 표).
- 증분 + 영속 로더는 raw 누적을 없애 RAM을 크게 줄인다. CPU도 전량 정규화·정렬·PG 계산 반복이 사라져 감소한다.

---

## 11. iwinv 별도 대역폭 계약 위험 평가

확인된 공식 문서 [문서, 확인일 2026-11-17]:
- 요금 정책: "인바운드 트래픽은 과금하지 않으며, 아웃바운드 트래픽 기준으로 과금" — https://docs.iwinv.kr/docs/billing/iwinv_price_policy
- 트래픽 확인: 수신/송신을 구분해 GB 단위로 측정, 서버 계열 기본 제공 일 20GB·월 620GB(사양별 상이), 요금 기준은 송신 그래프 — https://docs.iwinv.kr/service/compute/common-console-feature-guide/traffic-check
- 위 두 페이지와 검색에서 **"인바운드>아웃바운드" 또는 "인바운드 50Mbps 초과 시 별도 계약" 문구를 찾지 못했다**(지시서 2절의 전화 안내와 별개). 검색 결과 중 요금 계산기·IDC 정책 페이지도 이 조건을 직접 언급하지 않았다. → **[미확인] 서면 근거 없음.**

질문별 답:

**질문 1. 40Mbps 제한은 순간 대역폭 기준 위험을 줄이는가?**
→ 줄인다. 단 조건: (a) 제한이 iwinv 측정 지점 기준 외부 패킷 속도에 적용될 것(WG 오버헤드 +4~5% [추정]), (b) 주문 동기화 외 다른 인바운드(LLM·미디어 API 응답, 패키지·이미지 풀, 모니터링)와 합산해도 50 미만일 것, (c) 50Mbps가 평균이 아닌 순간 기준일 때. 40은 여유가 10Mbps뿐이므로 주문 스트림은 20~30으로 두는 편이 안전하다 [추정].

**질문 2. 40Mbps로 제한해도 인바운드>아웃바운드 조건에 해당하는가?**
→ **해당한다.** 속도 제한은 총량·방향 비율을 바꾸지 않는다. 오히려 아웃바운드는 요청(GET)·ACK·LLM 호출 위주라 인바운드가 훨씬 크다(2.18TB 대 소량) [추정]. 이 조건의 판단 기준(비율/절대량/지속 시간)이 서면 확인 전이다.

**질문 3. 월 2.18TB 유지 + QoS만으로 별도 계약을 피할 근거가 되는가?**
→ **아니다.** QoS는 계약상 어떤 조건(순간 속도, 비율, 월 총량, 지속성)을 충족하는지에 대한 근거를 만들지 않는다. 오직 순간 속도 조건에만 유효하다. 또 "모니터링 후 과도하다고 판단하면 계약 요청"이라는 구두 안내와 상충하지 않는다.

**질문 4. 증분으로 월 트래픽을 크게 줄이면 계약상 위험이 감소하는가?**
→ **감소 가능성이 높으나 조건부.** 위험이 (a) 총량·지속 사용량·평균 대역폭에 기반하면 2.18TB→10~20GB(약 99% 감소 [추정])로 크게 감소. (b) 순간 최댓값 기반이면 QoS와 병행해야 함. (c) 단순 "인바운드>아웃바운드" 비율 기반이면 절대량이 작아져도 비율은 남을 수 있어 감소 보장 불가. 따라서 어느 기준이든 **서면 확인이 필요**하고, 증분은 가장 많은 기준에서 위험을 줄이는 수단이다.

**질문 5. 위험을 없애려면 iwinv에서 받아야 할 서면 확인** → §16 참조.

---

## 12. 추천 설계와 우선순위

시나리오 비교 (수치는 §6, §9와 동일 가정, 모두 [추정]):

| 시나리오 | 월 인바운드(GB) | 순간 최대 | RAM | CPU | 최신성 | 개발 비용/위험 | 서비스 중단 가능성 | 계약 불확실성 | 순위 |
|---|---|---|---|---|---|---|---|---|---|
| S0 변경 없음 | 2,177 | 업로드 한계(100Mbps 가정, 미실측)까지 | 기준 | 기준 | 현행 | 0 | 이관 후 계약 요청 시 서비스 정지 위험 | 가장 높음 | 8 |
| S1 QoS만 | 2,177 | 설정값(예: 30) | 약간 악화(보유 시간 증가) | 기준 | 약간 지연 | 낮음, tc 권한·운영 위험 | 5Mbps 미만이면 적체 | **높음**(총량·비율 불변) | 7 |
| S2 조회 시각 분산 | 2,177 | 절반 수준(동시 겹침 제거) | 피크 감소 | 기준 | 현행 | 낮음 | 낮음 | 높음 | 6 |
| S3 gzip | ≈270 | 비례 감소(≈1/8) | 변화 없음 | 압축 비용 + | 현행 | 낮음 | 낮음 | 중 | 4 |
| S4 증분(서비스별) | ≈20 | <1Mbps 평균, 일일 재조정 때 순간만 | 대폭 감소 | 감소 | 수초~수분 | 중~높음(필드·updated_at 수정, 정합성 검증) | 중(복구 설계 필요) | 낮음 | 3 |
| S5 공통 캐시+증분 | ≈10~15 | 동일 | 최소 | 최소 | 수초~수분 | 높음(신규 로더, shared 구조) | 중 | 낮음 | 2 |
| S6 QoS+gzip+분산 | ≈270 | ≤설정값 | 변화 없음 | + | 현행 | 낮음 | 낮음 | 중(총량 270GB) | 5 |
| S7 QoS+공통캐시+증분 | ≈10~15 | ≤설정값(권장 20~30) | 최소 | 최소 | 수초~수분 | 높음 | 중 | **가장 낮음**(그래도 서면 확인 필요) | **1** |

추천 순서:
1. **즉시(코드 최소 변경)**: 서버 응답 gzip 켜기(판매 서버 쪽 확인 후), 두 서비스 기동 시각 분리(finance `screen_cache.prime` vs settlement `prime`의 지연 추가), `FULL_REFRESH_SECONDS` 상향 검토(정책 허용 시).
2. **선행 조건(서면)**: iwinv 서면 확인(§16). 이것이 안 오면 설계 선택이 달라진다.
3. **본 공사**: shop-api `mark_shipped/mark_delivered` `updated_at` 갱신, `orders-changes`·tombstone·digest 엔드포인트, 공통 로더(shared), 일일 재조정.
4. **보조**: 송신 측 HTB(A 또는 B)로 주문 클래스를 20~30Mbps ceil, fq_codel 리프. 이관 직후 안전장치로 먼저 적용해도 된다.

---

## 13. 코드 수정이 필요한 파일 및 함수 목록 (제안, 이번 조사에서는 수정하지 않음)

| 파일 | 함수·위치 | 변경 |
|---|---|---|
| `shared/shop-api/src/ax_shop_api/service.py` | `mark_delivered`(869), `mark_shipped`(891) | `updated_at` 갱신 추가 (증분 누락의 핵심) |
| `shared/shop-api/src/ax_shop_api/service.py` | `list_orders`(292-326), `_order_view`(401-441) | `updated_at` 기준 변경 목록 조회(`orders-changes`) 추가, `_order_view`는 재사용 |
| `shared/shop-api/src/ax_shop_api/main.py` | `list_orders`(575-585) 근처 | 신규 라우트 + 선택적 `GZipMiddleware` |
| `shared/shop-api/src/ax_shop_api/db.py` | 스키마 | `order_tombstones` 테이블, 필요 시 `created_at` 인덱스 확인 [미확인] |
| `shared/shop-api/src/ax_shop_api/retention.py` | 삭제 단계(`:246`) | 삭제 시 tombstone 기록 |
| `shared/shop-api/src/ax_shop_api/sales_export.py`, `contracts/.../shop_sales.py` | `SalesOrderRow` | (대안) `delivered_at`, 결제 상세, `seller_id` 추가(추가만 허용) |
| `domains/finance/src/finance_agent/books.py` | `OrderStore`(527-567), `_get_orders`(333-365), `live_books`(573-588) | 커서 증분 + upsert + 영속, 페이지 체크포인트, 백오프 |
| `domains/support/src/support_agent/settlement_books.py` | `OrderStore`(376-421), `_get_orders`(345-373), `_collect`(540-551) | 동일 |
| shared 신규 패키지(예: shop-mirror) | — | 로더 일원화(`domains` 간 import 금지 규칙 때문에 shared 또는 서비스 간 API로). 신규 패키지는 `pyproject.toml`/`uv.lock` 변경 필요 |
| `domains/finance/src/finance_agent/handlers.py` | `fresh_books`(140-146) | 사람 결정 시 전량 읽기 대신 "증분 + 최근 N일 검증"으로 |
| `domains/support/src/support_agent/main.py:113`, `finance_agent/main.py:288` | 기동 시 prime | 시각 분리, 스냅숏 복원 우선 |
| 서버 설정 | nginx/uvicorn/OS tc | gzip, 속도 제한 (코드 밖) |

---

## 14. 운영 적용 전 테스트 계획

운영 서버에는 부하 시험을 하지 않는다(지시서 0절). 아래는 **승인 후 격리 환경(스테이징)**에서 할 항목이다.

1. 계측 선행: 각 서비스에 페이지 수·바이트·소요시간·재시도 횟수를 로그/메트릭으로 남겨 **실제** 일/월 인바운드를 먼저 측정(현재 수치는 모두 추정이므로).
2. 단위·계약 테스트: 증분 커서 경계(같은 초, SETTLE 5초, overlap), 취소·환불·배송완료·삭제의 누락 여부를 케이스별로. `sales-export` 기존 테스트(`shared/shop-api/tests/test_sales_export.py`) 유지 확인.
3. 정합성 비교: 같은 시점의 전량 장부와 증분 장부의 주문 수·금액 합·정산 결과 diff(두 장부를 병행 운영 후 일치율 100% 확인).
4. 속도 제한 시험(스테이징 두 VM 사이): HTB 10/20/30/40Mbps에서 `iperf3` 등으로 순간·1분·5분 평균을 측정하고, WG 외부 패킷 기준 속도가 제한값 대비 몇 %인지 측정.
5. 장애 주입: 페이지 중간 실패, 판매 서버 정지 후 복구, 두 서비스 동시 기동 시 피크.
6. 메모리 측정: 전량 읽기 중 RSS 피크, 증분 후 RSS.
7. 정산 지급 판단 시나리오: 배송완료 반영 시각, 지급 요청이 최신 장부 기준인지.

---

## 15. 롤백 및 장애 대응 계획

- 증분 로더는 **기능 스위치**(환경변수)로 두고, 끄면 현행 전량 경로로 즉시 복귀(두 경로 병행 기간 확보). 코드 배포는 별도 승인.
- QoS는 `tc qdisc del` 한 줄로 해제 가능하도록 해제 명령을 문서화하되, 이 문서에서 실행하지 않는다. 적용 전 `tc` 현재 상태를 백업하고, 제한 오류로 서비스가 느릴 때를 대비해 SSH가 같은 경로를 쓰지 않게 default 클래스에 관리 트래픽을 둔다.
- 데이터 이상 시: 커서를 과거로 되돌려 재수신, 일일 digest 불일치 시 해당 날짜만 재수신, 최후엔 전량 1회(압축·저속 시간대).
- 계약 통지(별도 계약 요청) 수신 시: 증분 전환 완료 전이면 일시적으로 `FULL_REFRESH_SECONDS` 상향 + 압축으로 즉시 총량을 낮추는 완화책(공개 계약 조건을 준수하는 방법에 한함).
- 사용량·모니터링을 우회하는 방법은 이 설계에 포함하지 않는다.

---

## 16. iwinv에 추가 문의할 질문 (서면 회신 요청)

1. "인바운드가 아웃바운드보다 많은 경우" 조건의 정확한 정의: 비율인지, 절대량(GB)인지, 측정 단위(일/월)인지, 초과 임계치.
2. "인바운드 50Mbps 초과" 기준: 순간 최대/1분·5분 평균/지속 시간(몇 분 이상)/백분위(예: 95th) 중 무엇인지, 측정 지점(VM NIC, 스위치, 업링크), 외부 패킷 기준인지 페이로드 기준인지.
3. 두 조건이 gna 및 우리가 쓰려는 상품(서버 계열/사양)에 **동일하게** 적용되는지, 공식 매뉴얼의 어느 조항인지.
4. 별도 계약의 추가 비용(월 요금 또는 Mbps당 단가), 최소 계약 기간, 신청 시점과 소급 적용 여부.
5. 모니터링 후 "과도하다" 판단 기준과 통지 절차(사전 통지 기간, 통지 수단, 이의·유예 기간, 서비스 제한 여부).
6. 특정 월 인바운드 상한 이하(예: 월 100GB)면 별도 계약 대상이 아님을 명시해 줄 수 있는지.
7. 고객이 송신 측에서 속도 제한(shaping)을 건 경우 그 상태를 측정에 어떻게 반영하는지(예: 40Mbps 이하 유지 시 순간 조건 충족으로 간주하는지).
8. WireGuard 등 VPN 터널 트래픽의 측정 방식(외부 UDP 기준).
9. 아웃바운드 계산에 포함되는 트래픽 범위(ACK 제외 여부, 같은 IDC 내부 트래픽 처리).
10. 위 답변을 **서면(이메일 또는 계약 문서)** 으로 받을 수 있는지.

---

## 17. 최종 Go / Conditional Go / No-Go 판정

### 세 문장에 대한 답

- **QoS만으로 iwinv 계약 위험을 실질적으로 해결할 수 있는가?** → **아니오.** QoS는 순간 대역폭(50Mbps) 조건에만 효과가 있고, 인바운드>아웃바운드 조건과 월 2.18TB 총량은 그대로다.
- **서비스 장애 없이 적용할 수 있는 현실적인 속도 제한은 얼마인가?** → 현재 구조(전량 읽기 유지)에서는 두 서비스가 겹쳐도 600초 안에 끝나도록 **최소 10Mbps 이상**(여유 없음)이고, 다른 인바운드 트래픽과 오버헤드를 감안한 권장은 **주문 동기화 스트림 20~30Mbps**다. 5Mbps는 월 최대 1.62TB라 2.18TB를 못 보내 적체가 발생한다. 증분 구조로 바뀐 뒤에는 5~10Mbps로도 충분하다.
- **QoS 대신 또는 함께 무엇을 수정해야 월 트래픽과 메모리를 동시에 줄일 수 있는가?** → **`updated_at` 기반 증분 동기화 + 단일 공통 로더 + 일일 재조정**(shop-api `mark_shipped/mark_delivered`의 `updated_at` 갱신, 변경·삭제(tombstone)·digest 엔드포인트, finance·settlement의 raw 전량 누적 제거)이며, 응답 gzip을 병행한다.

### 판정: **Conditional Go**

조건:
1. iwinv의 서면 확인(§16 1~7)을 받기 전에는 "계약 위험 없음"이라고 선언하지 않는다.
2. 이관 전에 현재 실제 트래픽을 계측해 본 문서의 [추정] 수치를 실측으로 교체한다(주문 수·페이지 바이트·실제 전량 빈도).
3. 최소한 (a) 응답 gzip, (b) 기동 시각 분리, (c) 증분 로더의 선행 조건(`mark_*`의 `updated_at` 갱신)이 반영된 상태로 이관한다. 증분이 준비되지 않으면 QoS를 20~30Mbps로 두고 서면 확인을 먼저 받는 경우에만 임시 운영한다.
4. QoS는 보조 안전장치로만 쓰고 계약 회피 수단으로 취급하지 않는다.

### 확인하지 못한 항목 요약

- 온프레미스 Catbooks의 실제 코드가 이 저장소 `shared/shop-api`와 동일한지, 경로에 Cloudflare Tunnel·nginx·WireGuard가 실제로 끼는지
- 실제 주문 수·생성 속도·응답 크기(0.84MB/1000건은 코드에서 재현 불가, 주석 실측은 0.65MB)
- finance 장부 읽기의 실제 일일 호출 횟수(요청 구동이라 로그 필요), 서버 로그·메트릭
- products/sellers/coupons 응답 크기
- iwinv 50Mbps·인바운드>아웃바운드 조건의 문서 근거와 측정 방식
- 온프레미스 업로드 회선(100Mbps)과 단일 TCP 실효 속도
- IFB 공식 문서(403으로 접근 실패)

### 참고 출처 (확인일 2026-11-17)

- iwinv 요금 정책: https://docs.iwinv.kr/docs/billing/iwinv_price_policy
- iwinv 트래픽 확인: https://docs.iwinv.kr/service/compute/common-console-feature-guide/traffic-check
- nginx `limit_rate`: https://nginx.org/en/docs/http/ngx_http_core_module.html
- tc-htb: https://man7.org/linux/man-pages/man8/tc-htb.8.html
- tc-fq_codel: https://man7.org/linux/man-pages/man8/tc-fq_codel.8.html
- WireGuard quickstart(tc 언급 없음 확인): https://www.wireguard.com/quickstart/
- IFB: https://wiki.linuxfoundation.org/networking/ifb (403, 열람 실패)
