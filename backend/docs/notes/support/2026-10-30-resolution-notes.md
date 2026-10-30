# 고객지원 해결 메모 (2026-10-30)

해결로 닫은 문의에 상담원이 원인·조치·결과를 남기는 기능이다. DejaFix의 장애 "해결 메모"(DejaFix `#153` `#154`, 작성자는 토큰에서만, 칸마다 1000자, 최신 먼저, 전부 비면 거부) 규칙을 고객지원 문의에 맞게 다시 만들었다. DejaFix 코드는 가져오지 않았다(FastAPI·`shared.db`와 A2A skill·서비스 저장소로 구조가 다르다).

## 규칙

| 항목 | 내용 |
|---|---|
| 대상 | `resolved` 문의만. 열려 있거나 이관된 문의는 409 `INVALID_STATE` |
| 칸 | 실제 원인(`root_cause`), 한 조치(`action_taken`), 결과(`result`). 하나는 있어야 하고 비운 칸은 `null` |
| 길이 | 칸마다 1000자. 넘으면 자르지 않고 거부(화면 422) |
| 작성자 | dashboard-api가 **사람 토큰**의 이름으로 채운다. 본문에 `author`를 보내도 쓰지 않는다 |
| 개인정보 | 전화번호·이메일·주민등록번호·카드·계좌는 가려서 저장한다 (`guard.mask_pii`) |
| 순서 | 최신 먼저. 메모는 쌓기만 하고 고치거나 지우지 않는다 |
| 내력 | 문의의 AI 처리 내력에 `resolution_note_added`만 남긴다(메모 글은 싣지 않는다) |
| LLM | 쓰지 않는다. 코드 규칙만 쓴다 |

## 구성

- skill `add_resolution_note`(A등급, 요청자 `dashboard-api`만, `execute()` 경유). 안전 관문 `registry.toml`에 등록했고 inquiry 서비스 카드·`services.py`에 넣었다. 메모 읽기는 `get_inquiry`가 돌려주는 `resolution_notes`다.
- 규칙 `resolution_notes.py`, skill `resolution_note_handlers.py`, 저장은 `Conversation.resolution_notes`(저장소를 쓰는 구성이면 문의 문서에 함께 저장).
- dashboard-api `POST /api/dashboard/conversations/{id}/resolution-notes` (사람 토큰 필요, 응답은 갱신된 `Conversation`).
- 화면: 상담 콘솔 오른쪽 패널의 "해결 메모" 카드(`ResolutionNotesCard.tsx`).

## 가져오지 않은 것

- "비슷한 과거 장애/문의" 카드: 문서 검색(`EMBED_MODEL`)이 비어 있어 고객지원에서는 지금 동작하지 않는다. 설정이 되면 해결 메모를 검색 대상으로 쓰는 것을 따로 검토한다.
- 서버 메모리 저장소(PG 미설정)면 서비스를 재시작할 때 메모도 비워진다. 다른 고객지원 데이터와 같다.
