# 재무 시뮬레이션 가정과 출처

실행: `cd backend && uv run python -m finance_agent.simulation` (결과: `.data/simulation/`)
시나리오 파일: `domains/finance/src/finance_agent/simulation/scenarios/` (파일만 바꾸면 값이 바뀐다)

## 범위

- **실제 코드는 재무뿐이다.** 마케팅·데이터분석·기획·개발·총괄·인사는 로그에 `대역`으로 표시한다. 정해진 규칙으로 반응할 뿐 실제 에이전트가 아니다.
- 실제 장부, 서버, 외부 API에 닿지 않는다. 운영 화면에도 나오지 않는다.
- 안전 관문 경로(`start_campaign`)는 이 시뮬레이션이 아니라 안전 시험과 실제 모드 호출로 확인한다.

## 시나리오에 쓴 값

도서 업종 전용 공개 수치는 찾지 못했다. 그래서 전체 업종 수치를 기준으로 삼고, 차이는 가정으로 적는다.

| 값 | A: 네이버 브랜드 검색 | B: 인스타그램 릴스 | 근거 |
|---|---|---|---|
| CPC | 150~180원 | 1,050원 | A: 파워링크 평균 530원, 최소 70원 사이에서 브랜드 키워드라 낮게 가정. B: Meta 트래픽 캠페인 $0.70 × 1,500원 |
| CTR | 6% | 1% | A: 검색광고 전체 평균 6.84%. B: Meta 0.90~1.80% |
| 전환율 | 9% | 1.5% | **가정.** 검색 전체 평균 5.71%, 브랜드 키워드는 더 높다는 설명만 있고 수치 없음. 릴스는 구매 의도가 낮다고 보아 낮게 잡음. 공개된 Meta 전체 평균 9.21%는 쓰지 않음 |
| 주문당 금액 | 32,000원 | 25,000원 | **가정.** 책 1~2권 |
| 회사 부담 쿠폰 | 없음 | 주문당 3,000원 | **가정** |
| 환율 | | 1,500원 | **가정.** 찾은 최근 값이 2026년 7월의 약 1,460~1,480원 |
| 앞으로 4주 순현금 | 3,000,000원 | 3,000,000원 | **가정** |
| 월 캠페인 예산 | 1,000,000원 | 1,000,000원 | **임시값.** `finance_rules.toml`에서 바꾼다 |

수수료 10%, PG 수수료 3.1%는 `finance_policy.toml`의 현재 설정이다.

## 결과를 읽을 때 주의

- **손익분기 ROAS가 14.49배로 높다.** 회사 몫이 판매액의 6.9%(수수료 10% − PG 3.1%)뿐이어서다. 설정값에서 나온 사실이고, 일반 쇼핑몰 ROAS와 바로 비교하면 안 된다.
- **재구매를 넣지 않았다.** 단기 손익만 본다. 실제로는 첫 구매 뒤 재구매로 회수하는 광고가 있을 수 있어, 이 기준은 광고에 불리하다.
- **증분 판매액을 광고 판매액 전체로 뒀다.** 실제로는 광고가 없어도 팔렸을 주문이 있고, 특히 브랜드 검색에서 크다. 그래서 시나리오 A는 낙관적이다.
- 개발·기획 인건비는 넣지 않았다.

## 출처

- 네이버 파워링크 평균 CPC 530원, 최소 70원: [데이터로 보는 네이버 파워링크 광고](https://inside.ampm.co.kr/insight/10257) (2024 네이버 리포트 인용, 2차 자료)
- 검색광고 CTR 6.84%, 전환율 5.71%, Meta 트래픽 CPC $0.70: [Google Ads Benchmarks 2026](https://www.clickminded.com/google-ads-benchmarks/), [PPC Benchmarks 2026](https://whatagraph.com/blog/articles/ppc-benchmarks) (글로벌)
- Meta CTR 0.90%, 전환율 9.21%: [Meta ad spend by industry 2025](https://www.trendtrack.io/blog-post/meta-ad-spend-by-industry)
- Meta CTR 1.80%: [Meta Ads Benchmarks](https://madgicx.com/blog/meta-ads-benchmarking)
- 환율: [marketlog USD/KRW](https://www.marketlog.com/symbol/usdkrw), [Trading Economics](https://tradingeconomics.com/south-korea/currency)

위 수치는 검색 결과 요약으로 확인했고 원문 전체를 대조하지는 않았다. 실제 캠페인을 돌리면 실제 값으로 시나리오를 바꾼다.
