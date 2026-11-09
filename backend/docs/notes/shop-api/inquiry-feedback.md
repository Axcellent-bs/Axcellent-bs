# 쇼핑몰 문의 읽기·해결 결과 평가 API (2026-10-30)

해결된 문의에 고객이 별점·해결 여부·한 줄 후기를 남기는 경로다. 고객지원에는 이미 `submit_feedback`(후기 관리·만족도, #117)이 있었고, 쇼핑몰 쪽 경로만 없었다. 문의 목록·상태를 읽는 두 경로(아래 "내 문의 읽기")도 함께 있다. 이 문서는 **쇼핑몰 화면(`ax-company-hq/ax-frontend`의 shop-web) 담당에게 넘기는 API 설명**이다. 화면은 이 레포에서 만들지 않는다.

## 내 문의 읽기

`GET /api/shop/inquiries?buyer_id=buy_0007&limit=50` — 내 문의 목록(최근 순)

```json
{ "data": [ { "id": 11, "subject": "책이 찢어졌어요", "status": "resolved", "order_id": "ord_0002",
              "updated_at": "2026-10-30T05:42:11Z", "rated": false, "can_rate": true, "preview": "표지가 찢어져 왔어요" } ],
  "meta": { "total": 1 } }
```

`GET /api/shop/inquiries/{inquiry_id}?buyer_id=buy_0007` — 문의 하나. 위 키에 `messages: [{id, role("customer"|"agent"), text, created_at}]`가 더해진다. 남의 문의와 없는 문의는 같은 404 `NOT_FOUND`.

| 필드 | 뜻 |
|---|---|
| `status` | `open`(접수·처리 중), `resolved`(해결), `escalated`(담당자에게 넘김). 화면에는 "처리 중"·"해결됨" 두 가지로 보이게 하는 것을 제안한다(`escalated`는 "처리 중") |
| `rated` | 이미 평가했는지. 별점·글·공개 여부는 나가지 않는다 |
| `can_rate` | **평가 버튼을 보일지**. 해결됐고 아직 평가하지 않은 문의만 `true` |

내부 분석·AI 초안·분류·고객 이름·상담원 해결 메모·평가 내용은 응답에 없다(고객지원이 줄이고 shop-api가 허용한 키만 남기는 이중 확인).

### 본인 확인에 대해 (결정 필요)
이 읽기는 주문 조회(`GET /api/shop/orders?buyer_id=`)와 같은 방식이다. **구매자 ID(`buyer_id`)만 받고 로그인 토큰은 확인하지 않는다.** 그래서 `buy_0001` 같은 번호를 아는 사람은 그 구매자의 문의 제목·상태·메시지를 볼 수 있다(주문 조회는 이미 같은 상태). 쇼핑몰이 구매자 로그인 토큰을 발급·검증하게 되면 이 읽기와 쓰기(문의 접수·평가)에 함께 붙여야 한다. 지금은 시연용 구매자(시드) 환경이라 그대로 두었다.

## 요청

`POST /api/shop/inquiries/{inquiry_id}/feedback` (토큰 없음, 문의 접수와 같은 방식)

```json
{ "buyer_id": "buy_0007", "rating": 4, "resolved": true, "comment": "빨리 해결해 주셨어요" }
```

| 필드 | 규칙 |
|---|---|
| `inquiry_id`(경로) | 1 이상 정수. 문의 접수 응답의 `data.id` |
| `buyer_id` | `buy_` + 숫자 4~9자리. 형식이 다르면 400 `INVALID_BUYER_ID` |
| `rating` | 1~5 정수 |
| `resolved` | 고객이 "문제가 해결됐다"고 답했는지(true/false) |
| `comment` | 선택, 300자까지. 개인정보는 서버가 가려서 저장하고, 부적절한 표현이 있으면 관리자가 확인할 때까지 공개하지 않는다 |
| 그 밖의 키 | 422 (`source`·`user_id` 같은 값은 보낼 수 없다) |

## 응답

- 201 `{ "data": { "inquiry_id": 11, "rated": true, "rating": 4 }, "meta": { "context_id": "ctx_shop_…" } }`
- 공개·숨김, 확인 대기, 선제 연락 대상 같은 내부 판단은 응답에 없다. 화면에는 "평가해 주셔서 감사합니다" 정도만 보이면 된다.

## 오류

| 상태 | 코드 | 언제 | 화면 문구 제안 |
|---|---|---|---|
| 400 | `INVALID_BUYER_ID` | 구매자 ID 형식 오류 | 다시 로그인 |
| 404 | `NOT_FOUND` | 없는 문의이거나 **남의 문의**(둘을 구분하지 않는다) | 문의를 찾을 수 없습니다 |
| 409 | `INVALID_STATE` | 해결되지 않은 문의이거나 이미 평가를 남김 | 이미 평가하셨거나 아직 해결되지 않은 문의입니다 |
| 422 | (검증 오류) | 별점 범위·글자 수·필수 값 | 입력을 확인해 주세요 |
| 429 | `RATE_LIMITED` | 짧은 시간에 너무 많이 보냄 | 잠시 후 다시 시도해 주세요 |
| 403·502·504 | `UPSTREAM_*` | 고객지원 서버 문제 | 잠시 후 다시 시도해 주세요 |

## 화면에서 지켜야 할 것

- 평가 버튼은 `can_rate`가 `true`인 문의에만 보인다(위 "내 문의 읽기").
- 문의당 한 번만 된다. 응답을 못 받고 다시 누르면 409가 올 수 있으니, 409는 "이미 접수됨"으로 안내한다.
- 후기 글을 화면 로그·오류 메시지에 남기지 않는다.

## 구현 위치

`backend/shared/shop-api/src/ax_shop_api/main.py` (`submit_inquiry_feedback`), 테스트 `tests/test_inquiry_feedback.py`. 속도 제한은 문의 접수와 같은 환경변수(`SHOP_INQUIRY_RATE_PER_BUYER`·`SHOP_INQUIRY_RATE_GLOBAL`)를 쓰되 한도를 따로 센다.

## 가져오지 않은 것

- 해결 메모는 상담원 내부용이라 쇼핑몰에 노출하지 않는다.
- 영수증 대조는 이번 범위가 아니다(영수증 사진의 외부 LLM 전송은 팀 결정이 먼저).
- `contracts/openapi/shop-api.json`은 dev에서 29줄짜리 자리표시 스냅샷이라 이번에 다시 만들지 않았다(만들면 무관한 2천 줄이 섞인다).
