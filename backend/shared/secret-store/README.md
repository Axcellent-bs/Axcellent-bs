# secret-store — 비밀값 암호화 저장소 (1단계)

영상·마케팅 공급자 API 키(`MARKETING_*_API_KEY`, 커스텀 공급자 `MARKETING_CUSTOM_*`), 고객사 API 키, 에이전트
토큰(`AX_AGENT_TOKENS`)을 `.env` 대신 **암호화해 DB에** 두고 **고객사 ID(tenant) 단위**로 관리한다.
범위 밖(1단계 제외): 공용 사람 토큰(nginx 맵 파일), 세션 비밀키(`AX_SESSION_SECRET`).

## 스위치

| 환경변수 | 뜻 |
|---|---|
| `AX_SECRET_STORE` | `env`(기본) 또는 `db`. env는 지금과 완전히 같다(`os.getenv`). DB·마스터 키를 보지 않는다 |
| `AX_SECRET_MASTER_KEY` | db 모드 필수. base64 32바이트 이상. `python -m ax_secret_store keygen`으로 만든다 |
| `AX_SECRET_MASTER_KEY_ID` | 키 버전 kid(기본 `k1`). DB 레코드마다 저장된다 |
| `AX_SECRET_MASTER_KEYS_OLD` | 교체 뒤 옛 키 `kid=base64,kid=base64`(복호화 전용) |
| `AX_SECRET_DB_FILE` | db 모드 필수. sqlite 파일 경로(모든 서비스가 같은 파일) |
| `AX_SECRET_DEFAULT_TENANT` | 소비자가 tenant를 따로 주지 않을 때 쓰는 고객사 ID(기본 `default`) |
| `AX_SECRET_CACHE_TTL` | 읽기 캐시 초(기본 15). 교체 후 다른 프로세스는 이 안에 따라온다 |

소비자 코드는 `ax_secret_store.get_secret(name, tenant=None, env_fallback_name=None)` 한 곳만 쓴다.
db 모드에서 DB에 없는 이름은 비어 있는 것으로 본다(env로 되돌아가지 않는다. 롤백은 스위치를 `env`로 하는 것).

## 시작 거부

db 모드에서 서비스는 시작할 때 DB를 열고 모든 값을 복호화해 본다. 실패하면 **사유 코드만** 내고 뜨지 않는다.
`master_key_missing` · `master_key_invalid` · `master_key_mismatch`(키가 틀림) · `kid_unknown` ·
`db_path_missing` · `db_unavailable` · `mode_invalid`. 실행 중 DB 장애는 마지막으로 읽은 값으로 계속 동작하고
사유 코드만 로그에 남긴다.

## 저장 방식

- AES-256-GCM, 레코드마다 새 12바이트 nonce, AAD = `tenant|name|kid`, 키는 마스터 키에서 HKDF-SHA256으로 용도별 파생.
- DB에는 암호문·nonce·kid·**끝 글자 힌트(최대 4자)**·버전·누가/언제만 있다. 평문 컬럼은 없다.
- 평문은 화면·API 응답·로그·예외·이력·안전 서버(승인 화면) 어디에도 나오지 않는다. 승인 화면에는 값 대신 지문(HMAC 16자)과 끝 글자만 간다.
- **한 번 넣으면 다시 볼 수 없다.** 값을 돌려주는 API는 없다(교체만 가능).
- 이력(`history` 테이블): 누가·언제·동작(request_*/add/replace/delete/migrate/rewrap/retenant_*)·tenant·이름. 값 없음.

## 권한·승인 (대시보드 API `/api/dashboard/secrets`)

- 계정 프로필 `secret_editor`(켜기)와 `secret_tenants`(담당 고객사 목록)를 운영자·관리자가 정한다. 일반회원·에이전트·공용 토큰은 읽기도 쓰기도 못 한다.
- **추가**: 키 담당자가 바로(안전 관문 A `add_secret`). **교체·삭제**: 요청 → 요청자와 다른 사람이 승인 화면에서 승인(C `change_secret`) → 요청자·담당자가 '승인됨 → 적용'.

## 이전 도구 (CLI)

```bash
cd backend
# 1) 미리보기(DB에 쓰지 않음, 값은 출력되지 않는다)
uv run python -m ax_secret_store migrate --dry-run --tenant <고객사ID> --env-file /경로/.env
# 2) 이전 (이름만 출력). 기본 대상: MARKETING_*_API_KEY, AX_AGENT_TOKENS
uv run python -m ax_secret_store migrate --tenant <고객사ID> --env-file /경로/.env
#    커스텀 공급자 키 등 다른 이름은 --names 또는 AX_SECRET_MIGRATE_NAMES(쉼표, fnmatch)로
uv run python -m ax_secret_store migrate --tenant <고객사ID> --names 'MARKETING_CUSTOM_*' --env-file /경로/.env
# 3) 전후 대조: 지문이 같은지만 match/mismatch로 보인다
uv run python -m ax_secret_store verify --tenant <고객사ID> --env-file /경로/.env
```

`.env`의 값은 지우지 않는다. 스위치를 `env`로 되돌리면 롤백이다.

## tenant 옮기기 (회사 공간 나누기 1-7: `default` → `catbooks`)

암호문의 AAD·지문에 tenant가 들어 있어 DB의 tenant 칸만 바꾸면 값을 풀 수 없다. 그래서 값마다 옛 tenant로
풀고 새 tenant로 다시 암호화해 옮기는 명령을 쓴다(값은 출력하지 않는다).

```bash
cd backend
# 0) DB 파일(AX_SECRET_DB_FILE) 백업
# 1) 미리보기: 이름별 would_move / same_exists / conflict
uv run python -m ax_secret_store retenant --from default --to catbooks --dry-run
# 2) 옮기기 (새 행 쓰기 + 옛 행 지우기를 값마다 한 트랜잭션으로)
uv run python -m ax_secret_store retenant --from default --to catbooks
# 3) .env의 AX_SECRET_DEFAULT_TENANT=catbooks 확인 후 백엔드 재시작(다른 프로세스는 캐시 TTL 안에 따라온다)
```

- `same_exists`: 대상에 같은 값이 이미 있어 옛 행만 지웠다.
- `conflict`: 대상에 다른 값이 있어 둘 다 그대로 두었다(종료 코드 1). 어느 값이 맞는지 사람이 정하고 화면에서 교체한다.
- 옮길 tenant에 처리 안 된 교체·삭제 요청이 있으면(`pending_exists`) 아무것도 하지 않는다. 요청을 승인·거절한 뒤 다시 돌린다.
- 이력에는 `retenant_from_default`(새 tenant 쪽)·`retenant_drop_duplicate`가 남는다.
- 되돌리기: 같은 명령을 `--from catbooks --to default`로 돌리고 `AX_SECRET_DEFAULT_TENANT`를 되돌린다(또는 0번 백업 파일).

## OAuth 토큰 칸 (`store_oauth_token`, 연결 유형 B·유튜브)

- 사람 승인 없이 기계가 넣고 바꾸는 유일한 길이다. 이름이 `_REFRESH_TOKEN`으로 끝나는 칸만 받는다(`not_oauth_name`). 회사 AI 키·에이전트 토큰은 이 길로 못 바꾼다.
- 사람 승인은 연결 시작(플랫폼 동의 화면, 회사 관리자)과 끊기에서 받는다. 토큰은 플랫폼이 몇 시간마다 새로 주고, 카페24처럼 갱신할 때마다 갱신 토큰도 바뀌는 곳이 있어서 매번 사람을 거칠 수 없다.
- 갱신할 때는 `expect=지금 값`을 준다. 다른 곳이 먼저 갱신했으면 `stale_token`으로 거절해 새 토큰을 옛 토큰 기준으로 덮어쓰지 않는다(한 프로세스 안에서만 잠근다. 갱신은 한 서비스가 맡는다).
- 짧게 쓰는 접근 토큰(access token)은 여기 저장하지 않고 메모리에만 둔다.
- 이력에는 `oauth_store`·`oauth_rotate`와 누가만 남는다(값 없음).
- 콜백에서 어느 회사의 누가 시작한 연결인지 묶는 것은 dashboard-api `oauth_link.py`가 맡는다.

## 마스터 키 — 분실하면 복구할 수 없다

- 마스터 키를 잃으면 DB의 모든 값을 복호화할 수 없다. 복구 수단이 없다(다시 넣어야 한다).
- 만든 즉시 `.env` 밖에 **따로 백업**한다(비밀번호 관리자·금고 등, 서버 디스크·저장소와 다른 곳, 최소 2곳). DB 파일 백업과 마스터 키 백업은 따로 보관한다.
- 마스터 키는 저장소·로그·채팅에 올리지 않는다.
- 교체: 새 키를 `AX_SECRET_MASTER_KEY`, 새 `AX_SECRET_MASTER_KEY_ID`(예 `k2`)로 두고 옛 키를 `AX_SECRET_MASTER_KEYS_OLD=k1=...`에 둔 채 재시작 →
  `python -m ax_secret_store rewrap` → 확인 뒤 옛 키를 지운다.

## DB 선택 근거와 트레이드오프

sqlite 파일(표준 라이브러리, 새 의존성 없음)로 했다. 레포가 이미 `.data`의 JSON·sqlite 파일 저장소 패턴을 쓰고, 서비스들이 같은
호스트에서 돈다. `SecretRepository` 인터페이스로 분리해 postgres 등으로 바꿔 끼울 수 있다.
서버의 postgres 컨테이너(`axsupportdb-db`)는 고객 지원용이라 시크릿을 섞지 않는다(백업·접근 범위가 다르다).
트레이드오프: sqlite 파일은 여러 호스트에 나눠 뜬 서비스가 공유할 수 없다(지금은 한 호스트). 여러 호스트가 되면 저장소 구현만 교체한다.
암호화에는 이미 설치된 `cryptography`를 쓴다(새 외부 의존성 없음, 워크스페이스 패키지 `ax-secret-store` 추가).
