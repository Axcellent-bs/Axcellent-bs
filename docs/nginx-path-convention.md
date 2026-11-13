# 새 쓰기 경로와 nginx 허용 목록

운영 서버(251)는 앞단 nginx가 허용한 경로만 백엔드로 넘긴다. 새 쓰기(POST·PUT·PATCH·DELETE) 경로를 만들었는데 허용 목록에 없으면 화면에서 403이 난다.

## 화면에서 보이는 모습

- 앞단 nginx가 막으면 본문이 JSON이 아닌 HTML이다. 화면은 이 경우를 `nginx_blocked`로 구분하고 "서버가 이 요청 경로를 허용하지 않습니다. 관리자에게 서버 허용 경로(nginx) 추가를 요청해 주세요."라고 보여 준다 (403, 405). 구현은 `frontend/src/lib/apiError.ts`의 `failureError`.
- 앱(dashboard-api 등)이 거절한 403은 `{error:{code,message}}` JSON 봉투가 있어 그 문장을 그대로 보여 준다.
- 문구에는 경로·내부 주소·토큰을 넣지 않는다.

## PR 규약

새 쓰기 경로를 만드는 PR은 PR 본문에 "nginx 추가 필요 경로" 칸을 적는다. 필요 없으면 "없음"이라고 적는다. 값(IP·토큰·내부 주소)은 적지 않는다.

```
## nginx 추가 필요 경로
| 메서드 | 경로 정규식 | 인증 방식 | 타임아웃 |
|---|---|---|---|
| POST | ^/api/dashboard/<영역>/briefs/[^/]+/<동작>$ | 사람 토큰(Authorization: Bearer) | 기본 / 길게(예: 영상은 약 1분 이상) |
```

- 메서드: 새로 열 메서드. 같은 경로에 GET과 POST가 둘 다 있으면 각각 적는다.
- 경로 정규식: 가변 부분(`{id}`)은 `[^/]+`처럼 좁게. 앞뒤 `^`·`$`로 고정한다.
- 인증 방식: 아래 "확인 방법"으로 정한다. 사람 토큰 / 공개(읽기 전용) / 별도 토큰 중 하나.
- 타임아웃: 오래 걸리는 작업(미디어 생성 등)은 기본값보다 길게 요청한다.

## 경로가 어떤 인증 방식인지 확인하는 방법

1. 프론트 호출을 찾는다: `frontend/src/lib/*.ts`에서 `fetch` 호출의 주소와 `writeAuth(...)` 사용 여부를 본다. 기본 주소 상수는 `frontend/src/lib/env.ts`.
2. 그 경로를 받는 라우트를 찾는다: `backend/shared/dashboard-api/src/ax_dashboard_api/`에서 경로 문자열을 검색한다.
3. 인증은 `auth.py`의 `require_human_for_writes`가 정한다. GET은 열려 있고, GET이 아닌 요청은 사람 토큰(`Authorization: Bearer`)이 필요하다. 저장하지 않는 계산용 POST는 같은 파일의 `READ_ONLY_POSTS`에 있으면 토큰이 필요 없다.
4. 다른 서비스(대화 창구·기획·재무 등)로 직접 가는 경로는 그 서비스의 라우트 파일에서 같은 방식으로 확인한다.
