# 고객지원 LLM 연결·영수증 대조 로컬 테스트 (2026-10-29)

로컬(`bash scripts/run_all.sh` + `run_web.sh`, 3090)에서 고객지원 AI의 답변 초안과 영수증 대조를 실제 모드로 확인한 기록이다. 비밀값은 적지 않는다.

## 결과 요약

| 항목 | 결과 |
|---|---|
| 새 문의 → 분류·근거 검색·초안 3개·가드 | 정상 (LLM 답변). 첫 초안까지 `gemini-3.1-flash-lite` 3.7~4.2초, `gemini-3.5-flash` 15.2초 |
| 초안 선택 → 답변 보내기 | 정상. 가드 통과, 초안 채택률 100% |
| 고객 후속 메시지 | 정상. 문의 분석과 초안이 새로 만들어지고 상태가 "답변 대기"로 돌아온다 |
| 해결로 종료 | 정상 |
| 영수증 사진 분석 (합친 모드) | 정상. 일치 영수증 `consistent`, 총액·날짜를 바꾼 영수증 `mismatch` (`RECEIPT_AMOUNT_MISMATCH`, `RECEIPT_DATE_MISMATCH`) |
| 영수증 분석 (분리 서비스) | 처음엔 불가(문의 저장소가 서비스마다 따로). `analyze_receipt`를 inquiry 서비스로 옮기고 상담 콘솔 화면(영수증 대조 카드)에 연결해 해결. 아래 "후속 변경" 참고 |

## 답변이 템플릿(고정 문구)으로 나올 때 확인할 순서

답변이 "문의가 접수되었습니다. 확인 후 안내드리겠습니다." 같은 고정 문구면 LLM 호출이 실패해 폴백(`fallback=True`)으로 대체된 것이다. 원인은 `backend/.logs/inquiry.log`에 `LLM API가 <코드>를 돌려줬다`로 남는다. 응답 본문은 개인정보가 섞일 수 있어 상태 코드만 남는다.

1. **`.env`를 바꿨으면 서버를 다시 띄운다.** `run_all.sh`는 시작할 때만 `.env`를 읽는다. `bash scripts/run_all.sh stop` 후 다시 실행한다.
2. **재시작하면 사람·관리자 토큰이 새로 만들어진다.** 화면(3090)도 `run_web.sh`로 다시 띄워야 한다. 안 그러면 문의 접수가 "사용 토큰을 검증할 수 없다"로 거부된다. 3090 포트 프로세스만 끝낸다(`node.exe` 전체 종료 금지, 다른 개발 서버가 같이 죽는다).
3. **`.env`의 `KEY=값`에서 `=` 뒤에 공백을 두지 않는다.** `run_all.sh`가 bash로 읽어서 `LLM_MODEL= gemini-3.5-flash`는 빈 값 + 명령 실행이 된다(`command not found`, "LLM_MODEL 가 없습니다" 경고).
4. **HTTP 400 `credit balance is too low`**: Anthropic API는 Console 크레딧으로 청구된다. Max 요금제(claude.ai·Claude Code)와 별개다.
5. **HTTP 404 `no longer available to new users`**: 모델 이름이 막힌 것이다. 목록은 `GET <LLM_BASE_URL>/models`로 확인한다.

## Gemini(OpenAI 호환) 설정과 모델 선택

`LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`, `LLM_MODEL`은 아래 중 고른다 (같은 구조화 JSON 요청, 무료 키 기준).

| 모델 | 결과 |
|---|---|
| `gemini-3.1-flash-lite` | 정상, 약 4~5초. 분류와 초안을 연달아 부르는 구조에 맞아 로컬 기본으로 썼다 |
| `gemini-3.5-flash` | 정상, 약 14~15초 |
| `gemini-3.8-flash` | 60초 안에 응답 없음(래퍼 제한 시간 초과) |
| `gemini-2.5-flash` | 404 (신규 사용자에게 막힘) |
| `gemini-flash-latest` | 503 (수요 폭주, 일시적) |

`EMBED_MODEL`이 비어 있으면 회사 기억(memory) 검색이 실패한다. 고객지원 답변에는 영향이 없다 (예: `gemini-embedding-001`).

## 영수증 사진 분석

- 분석기(`receipt_vision.connect()`)는 `support_agent/main.py`가 불러올 때 꽂힌다. **목 모드가 아니고 `LLM_*`(또는 `SUPPORT_LLM_*`)가 있으면 켜진다.** 로컬은 `SUPPORT_VISION`을 따로 줄 필요가 없다. 목 모드 서버(`SUPPORT_MOCK=1`)에서는 `SUPPORT_VISION=on` 패치가 필요하다.
- 합친 모드 확인 방법: `support_agent.main`을 불러 분석기를 연결하고, 연결된 문의(주문 `ord_0216`: 상품 24,000원 + 배송비 3,000원)에 `analyze_receipt`를 호출했다. 사진은 주문과 같은 값의 영수증과 금액·날짜를 바꾼 영수증 두 장을 썼다.
- 금액이 다른 영수증은 바꾼 항목(총액·날짜)만 불일치로 잡았고, 배송비·주문번호·품목은 일치로 처리했다.

## 알려진 한계 (후속 과제)

- 문의 저장소는 `SUPPORT_PG_DSN`이 없으면 서비스(프로세스)마다 메모리에 따로 있다. 그래서 **문의를 읽는 skill은 문의를 가진 inquiry 서비스에 둬야 한다.** `analyze_claim_request`도 claims에 있어 같은 이유로 문의를 못 찾을 수 있다(아직 옮기지 않았다).

## 후속 변경: 상담 콘솔 화면에 연결

- `analyze_receipt`를 claims에서 inquiry 서비스로 옮겼다 (`ax_agent_template/services.py`, 두 서비스의 `agent-card.json`·README). `scripts/split_services.py`는 지금 커밋된 카드와 어긋난 결과를 내서(다른 서비스까지 바뀜) 쓰지 않고 해당 카드만 직접 고쳤다.
- dashboard-api에 `POST /conversations/{id}/receipt-analysis`를 더했다 (README 표 참고). 사진·글은 `untrusted`로만 넘기고, 주문이 없는 문의는 409 `NO_ORDER`다.
- 상담 콘솔 오른쪽 패널에 **영수증 대조 카드**(사진 선택 → 분석 → 영수증/주문 값 나란히, 어긋난 항목 강조, 읽지 못한 항목 안내)와 **근거 정책 부족 안내**(가장 가까운 정책 유사도 0.3 미만이거나 근거 없음, 임시 기준)를 넣었다.
- 새 문의 화면이 주문을 숫자 ID(`216`)로 보내 실제 주문(`ord_0216`)이 연결되지 않던 문제를 고쳤다. 이제 shop-api 주문 ID 문자열을 보낸다.
- 이번 영수증 검증은 안전 승인(`execute()`)을 거치지 않은 단일 프로세스 확인이다. 접수부터 대조까지의 통합 경로는 아직 시험하지 않았다.
- 단계형 초안이 사이즈 불일치를 단순 변심으로 단정하는 경우가 있었다. 보내기 전에 상담원이 검토해야 한다.
