"""사이트 변환기: 고객사 자기 API를 AXcellent 규격(shop-api) 모양으로 바꿔 AI들에게 준다.

회사 연결 파일이 `kind=adapter`면 AI들의 shop-api 주소가 dashboard-api `/site-adapter`가 된다.
설계: `docs/superpowers/specs/2026-11-18-site-adapter-design.md`.

- `mapping`: 짝 파일(`<AX_SITE_ADAPTER_DIR>/mapping.json`) 모델과 항목 꺼내기
- `upstream`: 고객사 API 호출(인증·페이지·오류)
- `resources`: 원본 행 → shop-api 모양 (주문·상품·분류·고객·판매 합계)
- `bookkeeping`: 고객지원 접수 동기화 기록 (사이트가 아니라 AXcellent 쪽 장부)
- `writes`: ③-쓰기 판정·쓰기 장부(`writes.json`)·할 일함 넣기 (W1·W2: 모든 쓰기가 할 일함)
- `gate_check`: 판정 2, 돈이 걸린 쓰기의 safety 실행 기록 확인 (W2)
- `router`: `/site-adapter` 주소
"""
