"""일별 집계 (3단계 3-1, 설계 docs/superpowers/specs/2026-11-24-daily-aggregate-design.md).

고객 원본(주문·가명 구매자)을 AXcellent에 쌓지 않으려고, "회사·날짜·차원 → 숫자"만 `aggregates.db`에
남긴다. 이 PR(3-1a 앞부분)은 저장소·일별 계산(매출·품질)·첫 채우기·보관이다.

- `store`: `aggregates.db` (회사는 `tenant_id` 칸으로 나눈다. 설계 결정 필요 7의 추천)
- `build_day`: 한국 날짜 하루의 일별 매출 행·품질 행. 지금 매출 분석(`sales_sql.tally`)과 같은 정의
- `backfill`: `sales.db`에 있는 날짜 전체로 일별 행을 채우고 보관 규칙(13개월 → 월 행)을 적용
- 명령: `python -m data_agent.aggregate --backfill` (데이터 AI가 시작할 때 자동으로 돌리지 않는다)

기간 스냅샷(구매자 수·코호트·떠날 위험·함께 산 상품 등, k 숨김을 저장할 때 적용)은 다음 PR이다.
"""
