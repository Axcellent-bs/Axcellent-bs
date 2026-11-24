# 장면별로 만들어 이어 붙이기 설계 (초안)

상태: **설계 초안, 코드는 아직 바꾸지 않는다.** 작성: 2026-11-25. 마케팅 영상 만들기 담당(태윤 님) 사후 리뷰용.
관련: 자막·엔드카드 얹기(`media/caption_overlay.py`, 야간 3번), 참조 여러 장(야간 6번), 영상 고르는 규칙(약속 B), 영상 요약(약속 F).

## 1. 왜 하나

지금은 영상 1편을 AI에 한 번에 시킨다(`creative_render.request_video` → `_render_video`, 길이는 기획 길이를 `plan_seconds`로 줄인 값, 모델 상한 안). 15초짜리 한 번에 시키면 이런 일이 생긴다.

- 중간 장면 하나만 마음에 안 들어도 **15초 전체 크레딧을 다시** 쓴다.
- 한 번에 길게 만들수록 뒤쪽에서 얼굴·옷·배경이 슬슬 바뀐다.
- 모델 상한(길이)에 막힌다. 모델마다 최대 길이가 달라 `seconds_clamped_from`으로 줄여 만든다.

장면 3~4개를 따로 만들어 ffmpeg로 이어 붙이면, 마음에 안 드는 장면만 다시 만들 수 있고 장면마다 짧아서 흔들림도 적다. 대신 장면 사이 이음새와 크레딧 계산이 복잡해진다. 이 문서는 그 길을 어떻게 낼지 정한다.

한 줄 흐름: **장면 N개 각각 생성 → ffmpeg로 이어 붙이기(concat) → 자막·엔드카드 얹기(3번) → (나중에) BGM**.

## 2. 크레딧

- 단가: 영상 초당 **1.5 크레딧**(지금 코드에 단가표는 없다. 예상 크레딧은 Higgsfield `estimate_video_cost`가 알려 주는 값이고 `credits_estimate`로 쓴다. 아래 숫자는 1.5/초를 가정한 계산이다).
- 모델 **최소 길이 3초**: 장면이 3초보다 짧아도 3초 값으로 계산된다고 본다. 모델마다 최소가 다를 수 있으니 값은 코드에 박지 않고, 5번 "만들기 설정"의 `models.video[].min_s`가 있으면 그것, 모르면 3초로 가정하고 예상 호출(`estimate_video_cost`)의 값을 따른다. (확인 필요: 실제 최소 길이는 모델별로 호출해 봐야 안다.)

예시 (기획 길이 15초):

| 방식 | 구성 | 합계 초 | 크레딧 |
|---|---|---|---|
| 지금(1편) | 15초 1편 | 15 | 22.5 |
| 장면 분할 | 4+4+4+3 초 4장면 | 15 | 22.5 (같다) |
| 장면 분할, 짧은 기획 10초 | 2.5초 4장면 → 모두 3초로 올림 | 12 | 18 (기획 10초 1편 15 크레딧보다 3 비쌈) |
| 장면 분할, 실패 1장면 재생성 | 위 15초 안 중 4초 장면 하나 | +4 | +6 |
| 지금(1편), 실패해 다시 | 15초 1편 | +15 | +22.5 |

읽는 법:

- 길이가 같으면 처음 만들 때 비용은 **똑같다**(초당 계산이므로). 손해는 장면이 3초보다 짧을 때만 생긴다. 그래서 장면 하나는 **최소 3초(모델 최소) 이상**으로만 나눈다. 기획 길이가 모델 최소 × 장면 수보다 짧으면 장면 수를 줄인다(예: 6초면 2장면, 3초면 분할하지 않고 지금처럼 1편).
- 이득은 **다시 만들 때**다. 마음에 안 드는 장면 하나만 다시 만들면 되기 때문이다.
- 이어 붙이기·자막·썸네일 자르기는 ffmpeg라 크레딧 0.
- 크레딧 표시: 승인 화면에 `장면별 초 × 1.5 = 합계`와 "최소 3초 올림 때문에 +N" 부분을 따로 보여 준다(약속 F의 `credits_estimate`는 합계 하나, 장면별 값은 아래 `scenes[].credits_estimate`).

## 3. 장면 나누기

- 입력: 기록의 `storyboard.shots`(칸 3~12개, 칸마다 `start_s`·`end_s` 선택, `prompt_en`) + 기획 길이 `plan_duration_s`.
- 나누기 규칙(코드가 정한다, LLM 호출 추가 없음):
  1. 장면 수 N = 3~4. 기획 길이 ÷ 모델 최소 길이가 N보다 작으면 N을 줄이고, 1이면 분할 안 함(지금 경로).
  2. 칸을 순서대로 N묶음으로 나눈다. 칸에 `start_s`·`end_s`가 있으면 시간 합이 비슷하도록, 없으면 칸 수를 균등하게.
  3. 장면 길이 = 그 묶음의 시간 합, 모델 최소 이상 · 모델 최대 이하로 고르고 합이 기획 길이를 넘지 않게 한다(넘으면 `seconds_clamped_from` 규칙 그대로 마지막 장면부터 줄임).
- 장면 프롬프트: 지금 `storyboard_video_prompt`(스타일 + `script_flow`)를 **그 장면 칸들만** 넣도록 재사용한다. 첫 장면에는 훅, 마지막 장면에는 마무리(CTA 장면은 3번의 엔드카드가 글자를 얹으므로 AI는 글자를 그리지 않는다)를 넣는다.

## 4. 장면별 생성과 이어 붙이기

- 장면은 **차례로** 만든다(첫 장면 → 다음…)가 기본. 이유는 인물 일관성(7절)과 중간 실패 시 이후 크레딧을 아낄 수 있어서다. 동시에 시키면 빠르지만 실패 시 이미 쓴 크레딧이 늘어난다(추천하지 않음).
- 장면 파일: 회사 폴더의 기록 폴더 안 `scene_1.mp4`~`scene_4.mp4`(지금 `asset_path`가 `video.mp4`를 두는 곳과 같은 폴더).
- 이어 붙이기(새 모듈 `media/scene_concat.py`, `audio_mix.py`와 같은 방식):
  - `MARKETING_FFMPEG_BIN`, 인자 리스트만(셸 금지), 실행 파일 없으면 `ffmpeg_not_found`, 시간 제한, 시험은 가짜 러너.
  - 장면마다 크기·프레임·코덱이 다를 수 있으니 **다시 인코딩하는 concat**(`-filter_complex concat`)을 쓴다. 파일을 그대로 복사하는 방식은 쓰지 않는다.
  - 결과는 지금 `video.mp4` 자리(`render.video.asset = "video.mp4"`)에 둔다. 그러면 BGM(`bgm_flow`)·자막(3번)·썸네일·유튜브·인스타는 **고칠 곳이 없다**(약속 B의 "최신 영상" 규칙이 그대로 적용).
  - 이음새는 자르기·붙이기 한 번(컷)이다. 장면 사이 크로스페이드는 처음에는 넣지 않는다(길이가 줄어 자막 시각이 어긋난다).
  - 합계 길이가 `MAX_VIDEO_S`(15초, `audio_mix`와 safety `video_duration` 기준)를 넘으면 이어 붙이지 않고 `scene_total_too_long`으로 멈춘다. 지금 한 편 상한과 같은 안전 기준을 지킨다.
- 이어 붙인 뒤 `render.video`에 장면 목록을 남긴다(5절). 3번의 자막 시각은 이어 붙인 영상 기준이므로 장면 길이를 알면 칸의 `start_s`를 그대로 쓸 수 있다.

## 5. 장부 키와 실패한 장면만 다시

지금: 영상 1편은 장부(`MediaLedger.reserve`) 키 하나 `"{context_id}:generate_creative_video:{record_id}"`. 예약은 `request_video`에서 `usage=UsageAmount("seconds", seconds)` 한 번.

바꿀 점: 키를 **장면별**로 한다.

- 장면 키: `"{context_id}:generate_creative_video:{record_id}:s{n}"` (n = 1부터).
- 예약: 승인 요청 때 **모든 장면 키를 한꺼번에 예약**(합계 초). 월 예산이 모자라면 하나도 예약하지 않고 `quota_exceeded`(일부만 예약하는 상태를 만들지 않는다).
- 완료: 장면마다 만든 뒤 그 키만 `settle`, 실패하면 그 키만 `mark_failed`. 다른 장면 키는 건드리지 않는다.
- 이미 확정(settled)된 키는 `reserve`가 그대로 돌려주므로(지금 동작) **같은 승인으로 다시 불러도 성공한 장면은 다시 돈이 나가지 않는다**.
- 다시 만들기(실패한 장면만 / 마음에 안 드는 장면):
  - 실패한 장면: 같은 키로 `mark_failed`가 이미 되어 있으니 새 예약이 가능하다. 사람이 [실패한 장면만 다시]를 누르면 그 장면 몫(예: 4초 = 6 크레딧)만 새 승인으로 올린다. 키 뒤에 시도 번호를 붙인다(`:s2:r2`)(이미 settled된 키와 겹쳐 막히지 않도록).
  - 마음에 안 드는 장면 바꾸기: 이미 settled인 장면을 사람이 고르면 **새 승인 + 새 키 `:s2:r2`**. 이어 붙이기는 장면 파일 N개를 다시 이어 붙인다(크레딧 0). 이전 장면 파일은 `scene_2.prev.mp4`로 남기지 않고 덮는다(저장 공간 절약, 필요하면 나중에).
- 기록 칸(안 지우고 추가): `render.video.scenes = [{n, seconds, status: "pending|running|done|failed", key, seq, request_id, asset, sha256, error, credits_estimate|None, attempt}]`. `render.video.status`는 지금 값(`awaiting_approval|running|done|failed|blocked|rejected`)을 그대로 쓴다. 일부 장면만 실패하면 `status="failed"`, `error="scene_failed"`, 실패한 장면 번호는 `scenes[].status`에 있다. 화면은 `scenes`가 없으면(옛 기록·분할 안 함) 지금처럼 보인다.

## 6. 승인 관문

선택지 두 개:

- **(추천) 한 번에 승인**: 승인 화면에 "4장면 · 합계 N초 · 약 M 크레딧"을 보여 주고 사람이 한 번 누르면 4장면을 차례로 만든다. 안전 작업 `generate_creative_video`를 그대로 쓰고 `params`에 `scene_count`·`scenes[{n, seconds}]`·`usage_amount`(합계 초)·`credits_estimate`(합계)만 더한다. 요약 문장에도 모델·장면 수·총 길이·예상 크레딧을 넣는다(약속 F `summary`에 `scene_count`·`scene_seconds` 추가).
- 장면마다 승인: 클릭이 4번, 중간에 승인을 기다리느라 영상이 늦다. 안전 관문 시험도 늘어난다. 추천하지 않는다.

**다시 만들기만 장면 단위 승인**이다(5절). 사람이 장면 하나를 고르면 그 장면 몫의 크레딧을 보여 주고 따로 승인한다. 승인 관문(safety)의 임계·규칙은 바꾸지 않는다(registry.toml 등은 사람 전용). 합계 초가 `video_duration` 한도 안인지 확인하는 일은 지금처럼 `params`로 한다.

## 7. 인물 일관성

장면을 따로 만들면 장면마다 얼굴이 달라질 위험이 지금보다 커진다. 쓸 수 있는 수단:

1. **참조 여러 장**(야간 6번): `image_references`에 대표 + `references` 순서로 모든 장면에 **같은 장수·같은 순서**로 넣는다. `kling_4_0_flash`는 이 방식을 받는다(`VIDEO_MODELS`의 `image_references: True`). 장면 N개가 같은 참조를 쓰면 얼굴이 맞을 가능성이 높다.
2. **같은 seed**: 지금 `creative_mcp.video_params`는 `model·prompt·aspect_ratio·duration·use_unlim`만 보내고 **seed를 보내지 않는다**. Higgsfield MCP `generate_video_batch`가 seed를 받는지는 **확인 필요**(`tools/list` 입력 형식을 봐야 한다). 받는다면 기록당 seed 하나를 정해 모든 장면에 보내고 기록(`scenes[].seed`)에 남긴다. 받지 않으면 이 수단은 쓰지 않는다. 지어내지 않는다.
3. **앞 장면 끝 프레임을 다음 장면 첫 그림으로**: `start_image` 방식 모델(`image_references`를 못 받는 모델)에서 쓴다. ffmpeg로 앞 장면 마지막 프레임을 뽑아 올린다(크레딧 0, `creative_thumbnails.ffmpeg_first_frame`과 같은 방식). 이 방식은 이음새가 부드러운 대신 **장면을 차례로** 만들어야 하고 얼굴이 조금씩 흘러갈 수 있다. `image_references` 모델에서는 1번이 낫고 이 방식은 쓰지 않는다(참조는 원본을 유지).
4. 장면 프롬프트마다 같은 인물 설명(`character_en`/시트 설명) 한 줄을 앞에 붙인다(LLM 호출 없음, 지금 `style_en` 붙이는 방식).

모델이 위 수단을 하나도 못 쓰면(참조도 seed도 없는 모델) **분할하지 않고** 지금처럼 1편으로 만든다. 사람이 "그래도 나눠서"를 고르는 길은 만들지 않는다(얼굴이 장면마다 바뀐 영상이 나가는 것을 막기 위해).

## 8. 지금 코드에서 바뀌는 곳

모두 `backend/domains/marketing/src/marketing_agent/` 안 (계약은 `backend/shared/contracts`).

| 곳 | 바꿈 |
|---|---|
| `creative_render.py` `request_video` | 분할이면 장면 계획(3절)을 세우고 `plan_seconds`를 **장면마다** 부른다. 장부 `ledger.reserve`를 장면 키(5절)로 N번. 하나라도 `over_budget`이면 전부 취소(`ledger.release`). `params`에 `scene_count`·`scenes`·합계 `usage_amount`. 기록 `render.video.scenes`·`summary`(약속 F 확장). 분할이 아니면 지금 코드 그대로 |
| `creative_render.py` `_render_video` | 장면 N번 반복(차례로): 장면 프롬프트·참조 → `_mcp_render` → 장면 키 `settle`/`mark_failed`. 끝나면 `scene_concat`으로 `video.mp4` 만들기. 일부 실패 시 `status="failed", error="scene_failed"`(성공한 장면 파일은 지우지 않는다) |
| `creative_render.py` `_video_mcp_params`·`_video_prompt` | 장면 번호를 받아 그 장면 칸만 프롬프트에 넣는다. 합계가 아니라 장면 초를 `duration`으로 |
| `creative_render.py` `check_video`·`expire_stale` | 승인 뒤 장면 반복으로 들어가고, 오래된 `running` 장면은 그 장면 키만 `mark_failed` |
| `creative_render.py` `plan_seconds`·`PLAN_DURATION_KEY` | 합계 길이 → 장면 길이 배분 함수(새 `plan_scenes`)를 옆에 둔다. 모델 최소·최대, 서버 `VIDEO_DURATION_ENV` 상한 규칙은 재사용 |
| `creative_render.py` `video_reference` | 장면마다 같은 참조 목록(6번의 `image_references` 여러 장) 또는 앞 장면 끝 프레임을 돌려주도록 확장 |
| `creative_render.py` `FILES`·`PARTS` | 장면 파일 이름 `scene_{n}.mp4`(칸 이름은 `video` 안 `scenes`에 두고 새 최상위 칸을 늘리지 않는다) |
| `creative_mcp.py` `VIDEO_MODELS` | 모델별 `min_s`(아는 값만) 추가. `video_min_seconds(model)` 함수 추가(`video_max_seconds`와 같은 모양) |
| `creative_mcp.py` `video_params` | seed를 받는다고 확인되면 선택 인자 `seed`를 추가(보내지 않으면 지금과 같다). 확인 전에는 건드리지 않는다 |
| `creative_mcp.py` `with_media` | `image_references` 여러 개를 받도록 `medias`를 목록으로(6번과 같은 변경, 먼저 머지된 쪽을 따른다) |
| `request_video.py` | 게시 흐름은 바꾸지 않는다. `video.mp4`가 합쳐진 한 편이므로 `attach_label_after_generate`·`_attach`는 그대로. 장면 파일이 후보로 잡히지 않는지만 확인 |
| `media/bgm_flow.py` `generate_media_flow`·`media/audio_mix.py` `overlay_bgm` | 바꾸지 않는다. 이어 붙인 `video.mp4`를 입력으로 받는다. `duration_s`는 합계 길이를 쓴다(`MAX_VIDEO_S` 15초 안) |
| `media/scene_concat.py` (**새**) | ffmpeg concat(4절) |
| `media/caption_overlay.py` (3번에서 새로 생김) | 바꾸지 않는다. 합쳐진 영상 길이와 칸의 `start_s`·`end_s`를 그대로 쓴다. 분할이면 칸의 시간이 이어 붙인 영상 기준과 맞는지 시험 하나 추가 |
| `creative_thumbnails.py` `source_video`·`SOURCES` | 바꾸지 않는다. 최신 영상(약속 B)이 합쳐진 한 편이다. 장면 경계 시각을 알면 `moment` 시각 고르기에 쓸 수 있으나 이번 범위 밖 |
| `shared/contracts` | 9절 |
| `shared/dashboard-api` | 장면 다시 만들기 POST 하나(10절). 승인 목록 화면은 `scenes`·합계를 읽기만 |

## 9. 계약 초안 (추가만)

기존 칸의 삭제·이름 변경·타입 변경은 하지 않는다. 값이 없으면 지금과 같다.

```
# 기록 render.video 에 추가(없으면 분할 안 한 지금 영상)
render.video.scene_count: int | None        # 분할이면 N(3~4), 아니면 없음
render.video.scenes: list[VideoScene] | None

VideoScene:
  n: int                      # 1부터
  seconds: int                # 모델 최소~최대 안
  shot_nos: list[int]         # 이 장면에 넣은 storyboard 칸 번호
  status: "pending" | "running" | "done" | "failed"
  key: str                    # 장부 키 ("...:s{n}" 또는 "...:s{n}:r{k}")
  seq: int | None             # 장부 행 번호
  request_id: str | None      # Higgsfield job
  asset: str | None           # "scene_{n}.mp4"
  sha256: str | None
  error: str | None
  credits_estimate: float | None
  attempt: int                # 1부터. 다시 만들 때마다 +1
  seed: int | None            # MCP가 seed를 받는다고 확인된 뒤에만 채움 (확인 필요)

# 약속 F 요약에 추가
render.video.summary.scene_count: int | None
render.video.summary.scene_seconds: list[int] | None

# 안전 작업 generate_creative_video params 에 추가 (선택)
scene_count, scenes[{n, seconds}], usage_amount = 합계 초
```

파이썬은 `shared/contracts`에 pydantic 모델 `VideoScene`을 새로 두고 기존 모델은 건드리지 않는다. 화면은 `scenes`가 없으면 지금처럼 보여야 한다.

## 10. 새 POST 경로 (재헌 님 필요)

- 장면 다시 만들기: `POST /api/dashboard/marketing/creative-records/{id}/video/scenes/{n}/redo` → 새 승인(해당 장면 몫만). **nginx 허용 필요**. 이어 붙이기만 다시(크레딧 0)는 장면 다시 만들기의 승인이 끝난 뒤 서버가 자동으로 한다. 따로 경로를 만들지 않는다.
- 새 서버 설정은 없다(`MARKETING_FFMPEG_BIN` 그대로). 필요하면 `MARKETING_SCENE_SPLIT`(분할 켜기) 같은 스위치 하나를 먼저 단계별 출시용으로 둘 수 있으나, 그 경우 **회사별 만들기 설정**(야간 5번)에 같은 칸을 두는 것이 낫다(서버 설정은 늘리지 않는다).

## 11. 단계별 PR 나누기

한 PR에 한 가지. 앞 PR이 머지돼야 다음 PR을 올린다.

1. **계획 함수만**: `plan_scenes`(장면 수·초 배분)·`video_min_seconds`·시험. 호출하는 곳 없음(죽은 코드로 두지 않으려면 2와 합칠 수 있다). 영향 0.
2. **이어 붙이기 모듈**: `media/scene_concat.py` + 가짜 러너 시험(인자 리스트·ffmpeg 없음·시간 제한·길이 상한).
3. **계약 추가**: `VideoScene`·`summary` 칸. 읽기 시험(옛 기록 = 장면 없음).
4. **장부 장면 키 + 승인 한 번**: `request_video`·`_render_video`에 분할 길(스위치 꺼짐 기본). 시험: 합계 예약·하나라도 over_budget이면 전부 취소·장면 하나 실패 시 그 키만 failed·다시 불러도 성공 장면 재과금 없음.
5. **인물 일관성**: 장면마다 같은 참조(6번 머지 뒤), 앞 장면 끝 프레임 방식, seed(확인되면).
6. **장면 다시 만들기**: dashboard-api POST + 새 승인 + 이어 붙이기 다시. nginx 허용.
7. **켜기**: 회사별 설정에서 분할 선택 + 화면. 프론트 세션과 맞춘 뒤.

단계 4부터는 영상 흐름과 계약을 바꾸므로 자동 머지를 예약하지 않고 사람이 머지한다(이 문서 PR만 자동 머지 대상).

## 12. 위험과 확인할 것

| 위험 | 이유 | 대응 |
|---|---|---|
| 장면 사이 얼굴·옷·조명이 달라 보임 | 장면마다 따로 만든다 | 7절 수단. 쓸 수 있는 수단이 없는 모델은 분할하지 않음. 켜기 전에 한 회사에서 실제 한 편으로 눈으로 확인(사람) |
| 크레딧이 처음 계획보다 더 듦 | 3초 올림, 다시 만들기 | 승인 화면에 올림 몫과 다시 만들기 몫을 따로 표시. 다시 만들기는 항상 새 승인 |
| 일부 장면만 만들고 멈춘 상태 | 중간 실패·서버 재시작 | 장면 키별 장부, `expire_stale`이 오래된 `running` 장면만 정리, 성공 장면 파일은 유지 |
| 이어 붙인 영상 길이가 15초 초과 | 장면 합계 | 4절에서 막고 `scene_total_too_long`. safety `video_duration` 기준 그대로 |
| 자막 시각 어긋남 | 장면 길이와 칸의 `start_s`가 다름 | 칸 시각을 장면 길이로 다시 계산하는 함수 하나로 모으고 시험. 시각이 없으면 지금처럼 균등 분할 |
| 이음새가 눈에 띔 | 컷 이어 붙임 | 처음에는 컷만. 크로스페이드는 나중 |
| seed·최소 길이·`image_references` 장수 상한을 MCP가 받는지 불확실 | 코드에서 확인된 값이 없음 | **확인 필요**. 확인 전에는 추측 값을 코드에 넣지 않는다(`VIDEO_MODELS`에는 아는 것만) |
| 수동 승인 클릭 수 | 다시 만들기는 장면마다 승인 | 다시 만들기만 그렇다. 처음 만들기는 한 번 |
| 디스크 | 장면 파일 N개 + 합친 영상 | 합친 뒤 장면 파일은 유지(다시 만들기에 필요). 정리는 기록 삭제 때 같이(`creative_records` 지우는 길에 한 줄) |

## 13. 이 문서가 정하지 않은 것

- 장면 수 기본값(3 또는 4)을 화면에서 사람이 고르게 할지. 처음에는 코드가 정한다.
- 장면마다 모델을 다르게 쓸지(안 쓴다).
- BGM을 장면마다 다르게 입힐지(안 쓴다. 합쳐진 영상 한 편에 지금처럼).
