# domains/finance
담당: 태윤 · 포트: 8105
## 1. 역할
**재무팀** 에이전트 (재무 재설계 2026-10-23, `docs/notes/finance/2026-10-23-finance-redesign.md`).
마켓운영(판매자 입점 심사 `seller_review`·정산 보류 `settlement_hold`)은 재무 일이 아니라 2026-10-30에 없앴다.
결산·리포트, 매출·PG 대사, 팀 예산, 세무 지원, 현금 흐름을 한다.
**판매자 정산(정산금 계산·지급 승인 요청·묶음 상태·지급 기한 점검)은 고객지원이 한다(2026-11-12).**
재무는 고객지원이 준 정산을 받아 화면에 보여 주고, 회계(판매자 미지급금·13주 현금 흐름·은행 출금 대조·마감 점검·재무 대화)에만 쓴다. 정산금을 계산하거나 지급 승인을 요청하지 않고, 지급을 승인하지도 않는다. 지급 승인 요청은 고객지원 AI가 하고 승인은 고객지원 담당이 한다(2026-11-12).
**재무는 쿠폰·환불의 끝난 금액을 장부에 기록만 한다.**
건별 검사는 안전 관문 코드 규칙(금액 계산, `claimed_fields` 불일치, 기준표 한도)이 하고, 환불 여부는 고객지원, 쿠폰은 마케팅이 정한다.
안전 장치가 보류한 환불은 고객지원 화면, 쿠폰은 마케팅 화면에서 사람이 결정한다.
숫자는 전부 코드로 계산하고, LLM은 결산 설명 문장만 쓴다(목 모드·미설정·실패 때는 템플릿 문장).

| 모듈 | 파일 | 하는 일 |
| --- | --- | --- |
| 장부 자료 | `books.py` | 실제: shop-api 주문·상품·판매자·쿠폰 + safety 실행 기록. 목: `finance_books_seed.json`(오늘 기준 2주치). 토스 정산 형식 목 PG 정산(데모 불일치 포함). 실제 모드 장부는 30초 동안 다시 쓰고(동시 요청은 한 번 모은 결과를 같이 씀), 주문·쿠폰을 못 받은 장부는 캐시하지 않는다 |
| ① 결산·리포트 | `closing.py` | 일·주·월 손익, 분개 미리보기, 설명 문장. 주문·쿠폰 자료를 못 받으면 그 숫자로 계산하는 줄은 0이 아니라 `null` |
| ② 매출·PG 대사 | `reconciliation.py` | `matched`·`mismatch`·`missing_in_pg`·`pending`·`fee_anomaly` |
| ③ 판매자 정산 받기 | `settlement_feed.py` | 고객지원 skill을 A2A로 읽는다: `seller_settlement`(내역서)·`seller_settlement_weeks`(여러 주)·`seller_settlement_batches`(묶음 상태)·`seller_settlement_deadlines`(지급 기한). 못 받으면 `null`이고 지어내지 않는다. 30초 기억(실패는 10초) |
| ④ 팀 예산 | `team_budget.py` | 팀 예산 = 그달 1일 팀 인원(인사 `team_headcount`) × 인당 월 단가. 확정은 인사 AI(사람), 재무는 A2A `record_team_budget`으로 기록만 |
| 사건 보상 장부 | `ledger.py` | 쿠폰 업무는 재무가 맡지 않는다(마케팅·고객지원 합의). 지급은 마케팅(`issue_coupon`), 판정은 고객지원이 하고 재무는 **끝난 금액**만 사건별로 적는다(보류·거절 줄과 등급은 장부에 없다): safety 실행 기록(`safety_executions`)이 있으면 그것이 원본이라 이벤트를 더하지 않고, 없는 사건만 이벤트(`events`)로 적는다. 목 모드는 시드(`seed`). 둘 다 없으면 null |
| 사건별 화면 | `incidents.py` | 사건 목록·상세: data 영향 그대로 + 보상 장부 + 환불 금액(`refunds`: 사건 구간·영향 고객으로 귀속, 금액·건수만) + 빠져나간 돈 합계(`money_out_total` = 보상 + 환불) (재설계 문서 12-1). 재무 단계는 #415로 없앴다 |
| 설정 | `finance_policy.toml` | 수수료율(기본 10%, 문구 8%), 주 정산, PG 3.1%, 인당 월 단가, 보류 추이 기준 |

| skill | 등급 | 요청자 | 우선순위 | 하는 일 |
| --- | --- | --- | --- | --- |
| `closing_report` | read | orchestrator, data | optional | 기간 손익 요약 (`ClosingReportInput`·`ClosingReportOutput`) |
| `record_support_cost` | read | orchestrator | recommended | 고객지원이 끝낸 처리 결과(환불 금액·쿠폰)를 받아 주문 배송비와 함께 장부에 적음(환불 여부는 확인하지 않음). 경비를 쓰지 않은 문의도 처리 결과를 기록, 같은 문의는 한 번만 (`SupportCostInput`·`SupportCostOutput`) |
| `answer_planning_question` | read | planning | recommended | 기획이 근거를 되물으면 재무가 보낸 요청(`POST /api/finance/planning-requests`)의 근거로만 답한다. 기록에 없으면 `unknown` |

없앤 것: `loss_aggregation`(손실 집계 skill), `/api/finance/loss-reports`. 장애 비용은 평상시 장부 → `closing_report`에서 드러난다. 총괄은 고객지원이 문의를 닫으면 재무를 불러(`record_support_cost`) 처리 결과를 장부에 기록하게 한다.
화면 호환으로 한 단계 남겨 뒀던 키(사건 상세 `steps`, 목록 `loss_total`)도 지웠다 — 화면 정리(ax-company-hq/ax-frontend#61)가 끝나 읽는 쪽이 없다.
재무 일이 아니라 없앤 것(2026-10-26): 쇼핑몰 클레임 연동(`catbooks.py`, 환불·교환 신청을 건별로 다시 확인 → 건별 검사는 안전 관문의 일, `/api/finance/catbooks/*`, `FINANCE_CATBOOKS_*`),
`review_threshold_change`(AI가 기준 변경을 판단 → 사람이 기준표 PR로 바꾼다, `/api/finance/threshold-reviews`).

## 2. 실행 방법
`docker compose -f infra/compose.yml up finance` / 목 모드: `FINANCE_MOCK=1` (장부 시드 `src/finance_agent/finance_books_seed.json`, 영향 분석·심사 시드 `mock_seed.json`)

혼자 띄우기(목 모드, 레포 루트): `FINANCE_MOCK=1 uv run uvicorn finance_agent.main:app --port 8105`

테스트: `uv run pytest domains/finance` (`backend/`에서)

```bash
# 결산 리포트 (목 모드, orchestrator로 요청)
echo '{"period":"day"}' > in.json
uv run ax-test-call --agent http://localhost:8105 --skill closing_report --input in.json \
  --context-id ctx_inc_0001
```

### 가짜 데이터로 돌려 보기 (목 모드)
safety·finance를 목 모드로 띄운 뒤 `uv run python domains/finance/demo/seed_demo.py`를 실행하면 마케팅·고객지원·통합 에이전트인 척 가짜 요청을 보낸다(공지·기록, 보상 쿠폰, 환불, 정산 확정). 결과는 AXcellent 화면 `http://localhost:3090/finance/daily`에서 본다. 가짜 데이터는 `src/finance_agent/mock_seed.json`이다.

관리자 사용법(화면 보는 법, 결정 기준): `docs/notes/finance/admin-guide.md`

## 3. 입출력 계약
- 입출력 모델: `src/finance_agent/schemas.py` (Agent Card의 `input_schema`·`output_schema`).
- 재무 대시보드 API (`{data, meta}`, `meta.mock`·`collected_at`·`source`·`unavailable`, 형식은 재설계 문서 11장, 구현 `finance_api.py`):
  `GET /api/finance/overview`, `GET /api/finance/closing?period=day|week|month&date=`, `POST /api/finance/closing/confirm`(사람 토큰, 확인할 수 없는 숫자가 있으면 409 `books_unavailable`),
  `GET /api/finance/reconciliation?date=`, `POST /api/finance/reconciliation/{payment_id}/note`(사람 토큰),
  `GET /api/finance/settlements?period=YYYY-Www`·`GET /api/finance/settlements/deadlines`(고객지원 결과 그대로, `meta.provider`=`support`. 고객지원에 닿지 않으면 503 `support_unavailable`, 기간이 틀리면 400). 묶음 지급 요청(`POST /api/finance/settlements/batch`)은 없앴다 — 고객지원 `request_seller_settlement`가 한다,
  `GET /api/finance/team-budget?month=`
- 사건별 API (시나리오 A, 재설계 문서 12-1, `incidents.py`): `GET /api/finance/incidents`(`meta.ledger_source`), `GET /api/finance/incidents/{context_id}`(없으면 404 `not_found`). 목 시드: `ctx_inc_scn_a_0001`(data 목과 같은 6건·4명·95,000원 + 보상 2,000원 × 4장)
- 장애 구간(`window`)은 총괄 사건 목록 `GET /api/incidents`(8000)의 `opened_at`·`closed_at`을 **읽기 전용으로** 읽어서 안다 (계약 `contracts/src/ax_contracts/data_access/orchestrator_incidents.py`, [협업] #417 제안 A). 사건 **중**에 총괄을 부르는 것이 아니라 평상시 사건 조회에서만 읽는다. 주소는 `AX_ORCHESTRATOR_URL`(기본 `http://orchestrator:8000`). 못 읽거나 계약과 다르거나 아직 열린 사건이면 구간·영향 모두 `null`이고 data에 묻지 않는다(fail-closed). 목 모드는 장부 시드로 구간을 안다
- 재무제표: `GET /api/finance/statements?period=month|quarter|year&date=&format=json|csv|xlsx` (K-IFRS 기능별 손익계산서, 당기·전기, `csv`·`xlsx`는 내려받기). 장부에 없는 항목은 `null`(자료 없음)
- 지금 사람이 할 일(`GET /api/finance/overview`의 `todo`): 항목마다 `action`(결산 확정, 대사 메모)과 건별 `items`가 붙어 화면이 그 자리에서 결정 버튼을 띄운다. 쿠폰·환불은 할 일을 만들지 않는다. 판매자 정산은 지급 요청·승인 할 일이 없고, 고객지원이 기한을 넘길 판매자를 알려 주면 알림(`settlement_deadline`, `action.type`=`open_tab`, 처리는 고객지원)만 띄운다
- 화면 예시 데이터: 재무 화면 [실제 데이터 | 예시 데이터] 전환의 예시 쪽은 `demo/export_example.py`가 목 시드로 만든 `frontend/src/lib/financeExample.json`이다. 시드나 계산이 바뀌면 다시 만든다
- 기존 조회 API: `/api/finance/ledger`(사건별 보상 지급·주문별 환불 누적), `/api/finance/self-test`(재무 AI 테스트 버튼: 가짜 자료로 계산 규칙 점검 + 안전 장치·쇼핑몰·데이터 AI 연결 확인, 상태를 바꾸지 않음), `/api/finance/self-test/last`(마지막 결과, `data.trigger`=`auto`|`manual`, `meta.interval_seconds`·`meta.next_run_at`)
- 재무 AI 테스트 자동 실행: 서버가 켜지고 5초 뒤 한 번, 그다음 `FINANCE_SELF_TEST_SECONDS`(기본 600초, 0이면 끔)마다 서버 안에서 돌려 `/self-test/last`에 남긴다. 테스트(`tests/conftest.py`)는 0으로 끈다
- 매일 결산 자동 실행(①): 서버가 켜지고 10초 뒤 한 번, 그다음 `FINANCE_DAILY_CLOSING_SECONDS`(기본 86400초=하루, 0이면 끔)마다 **전날** 하루 결산을 계산해 `GET /api/finance/closing/last`에 남긴다(`data.date`·`data.report`·`data.trigger`=`auto`|`manual`·`data.ran_at`, `meta.interval_seconds`·`meta.next_run_at`). 조회만 한다 — 확정(`closing/confirm`)은 사람이 대시보드에서 누른다. 테스트는 0으로 끈다
- 사람 토큰: 안전 관문과 같은 형식 `Authorization: Bearer human:<이름>`(목 모드) 또는 `FINANCE_HUMAN_TOKENS`/`SAFETY_HUMAN_TOKENS`의 토큰 (`human.py`)
- CORS: `FINANCE_CORS_ORIGINS`(`run_all.sh`가 화면 주소로 넣는다. 설정이 없을 때 코드 기본값은 옛 대시보드 `http://localhost:3001`). 사람 결정 경로 3개만 POST·Authorization 허용, 나머지는 GET만
- 구독 이벤트: `coupon.issued`(보상 쿠폰 지급 누적), `payment.refunded`(환불 누적). 실제 모드는 `REDIS_URL`이 있어야 받는다. 보상 쿠폰 장부는 safety `GET /api/safety/executions`(읽기)에 기록이 있으면 그것을 먼저 쓴다
- 부르는 것: data `impact_analysis`(`failed_order_count`·`affected_customer_count`·`damage_amount` + 영향 고객 ID `affected_customer_ids`), data `fraud_score`(`risk_score` 0~100), 고객지원 판매자 정산 조회 skill 4개(읽기), safety `execute()`(재무 개선 요청 `finance_request_change`), shop-api·safety 조회 API(장부)

## 4. 의존
ax-contracts, ax-agent-template, data 에이전트(`impact_analysis`, `fraud_score`), 인사(`team_headcount`), 고객지원(판매자 정산 조회), safety

## 5. 하지 않는 것
- 영향 숫자(실패 주문·영향 고객·피해 금액)를 다시 계산하지 않는다 (원천은 data)
- 실제 송금·정산 확정을 하지 않는다. 판매자 정산금을 계산하거나 지급 승인을 요청하지 않는다 (고객지원 AI가 `confirm_settlement`로 요청하고 safety가 항상 C로 보류하며, 고객지원 담당이 승인한다)
- 판매 사이트 DB에 직접 붙지 않는다 (shop-api·data를 거친다)
- 판매자 소개·구매자 메모 같은 `untrusted` 문장을 금액·심사 판단에 쓰지 않는다
- 기준표를 읽거나 바꾸지 않는다 (사람이 safety 레포 설정을 PR로)
- 쿠폰·환불은 끝난 금액을 장부에 기록만 한다

## 규칙·캠페인 재무

- 규칙과 근거: `finance_rules.toml`. 값을 바꾸면 version을 올린다. 결과마다 `applied_rules`가 붙는다
- 캠페인: `record_campaign_result`, `review_campaign_budget`, `verify_campaign_budget`, `finance_insights`. 쿠폰·환불은 판단하지 않는다
  - `verify_campaign_budget`은 지금 아무도 부르지 않는다. 캠페인 시작(`start_campaign`)의 교차검증자는 안전 장치 자체이고(기준표), 재무는 비용을 승인·반려하지 않고 의견만 낸다(BRK-005). 검증자로 쓰지 않기로 한 상태다
  - `finance_insights`는 총괄이 부를 수 있게 열어 두었지만 아직 부르는 곳이 없다
- 기획 개선 요청: 재무 화면 탭 밖의 "기획에 개선 요청" 섹션에서 사람이 바꿀 내용과 근거를 적어 보내면 안전 관문(`finance_request_change`, A)을 거쳐 기획 `create_ticket`으로 간다. 보낸 근거는 재무 기록에 남아(`FINANCE_STATE_FILE`) 다시 켜도 기획의 되묻기에 답한다
- 시뮬레이션: `uv run python -m finance_agent.simulation`. 가정은 `backend/docs/notes/finance/simulation-assumptions.md`
- 파일 자동화 환경변수: `FINANCE_EXPORT_DIR`, `FINANCE_BANK_INBOX`, `FINANCE_EXPORT_SECONDS`
- 은행 거래내역: 재무 담당자가 화면(② 매출·PG 대사 → 은행 입금 대조)에서 은행 파일(CSV·xlsx)을 그대로 올린다. 읽은 기록·잘못 올린 기록 빼기도 같은 탭(은행 거래내역 읽은 기록)에 있다
  (`POST /api/finance/bank/file`). `FINANCE_BANK_INBOX` 폴더에 넣은 파일도 같은 규칙(`bank_file.py`)으로 읽는다.
  UTF-8·CP949, 맨 위 안내 줄, 은행마다 다른 칸 이름(찾으신금액·맡기신금액·입금(원) 등)을 코드가 맞춘다. 옛 엑셀(.xls)은 읽지 않는다
