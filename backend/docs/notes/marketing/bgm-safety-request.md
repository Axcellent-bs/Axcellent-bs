# [요청 마케팅→safety] BGM(audio) 생성을 generate_media 관문에 허용하는 규칙 변경 초안

> 상태: **초안(미등록)**. 이슈로 올릴 때 아래 "이슈 본문"을 쓴다. 등록 전에 열린 `[요청 …→safety]` 이슈 중 같은 내용이 있으면 댓글로 단다.
> 요청 대상: 이태윤 님(안전 담당). 요청자: 재헌(marketing).
> 형식: `sns-safety-request.md`를 따른다(상황·요청·묻는 것·완료 기준).
> 기준: 이 브랜치(`feat/marketing-bgm-wiring`)의 `backend/shared/safety/src/ax_safety/rules/`를 읽기만 해서 확인했다. **값은 모두 제안이고 최종 등급·기준은 safety가 정한다.** 마케팅은 `shared/safety/`를 고치지 않는다.

**제목**: `[요청 마케팅→safety] generate_media에 audio(BGM) 종류 허용과 비용 기준`

---

## 사실로 확인한 것 (파일·줄 근거)

| 항목 | 확인 내용 | 위치 |
|---|---|---|
| 등록 | `generate_media`는 owner marketing, base_grade B, verifier safety, 금액은 `cost_krw`(+`claimed_cost_krw`) | `registry.toml` 56~61줄 (주석 53~55줄이 `media_kind(image \| video)`라고 적음) |
| 종류 규칙 | `media_kind_known`은 `param_in`으로 `media_kind`가 `["image", "video"]` 안이어야 통과. 밖이면 C 보류 | `thresholds.toml` 549~554줄 (주석 547~548줄) |
| 영상 건당 | `video_cost_single`: `when media_kind=video`, amount `>= 2000` | `thresholds.toml` 557~563줄 |
| 이미지 건당 | `image_cost_single`: `when media_kind=image`, amount `>= 400` | `thresholds.toml` 566~572줄 |
| 일일 누적 | `media_cost_daily`: 종류 구분 없이 `daily_total >= 10000`(주석: 이미지·영상 합산) | `thresholds.toml` 575~580줄 |
| 0원 | `media_cost_zero`: amount `< 1` | `thresholds.toml` 583~588줄 |
| 영상 길이 | `video_duration`: `when media_kind=video`, `duration_s > 8`. `when`이 video라 audio에는 적용되지 않는다 | `thresholds.toml` 591~597줄 |
| 기존 시험 | `test_unknown_media_kind_is_held`가 `media_kind`를 `[None, "audio"]`로 돌려 `media_kind_known` 보류를 기대한다 | `shared/safety/tests/test_marketing_sns_video.py` 198~202줄 |
| 합산 시험 | `test_daily_total_adds_images_and_videos`가 일일 합산을 검증 | 같은 파일 205~215줄 |
| 지금 audio를 보내면 | `media_kind="audio"`는 `media_kind_known` 하나로 C 보류. 건당 상한은 audio 몫이 없어 `media_cost_daily`·`media_cost_zero`만 적용 | 위 규칙 기준(관문을 실제로 돌려 보지는 않았다) |
| 마케팅 쪽 | `MARKETING_AUDIO_MODE`가 `bgm_overlay`일 때만 영상→BGM→후처리를 하고, BGM도 `execute("generate_media")`를 거친다. 관문이 보류하면 BGM 공급자와 ffmpeg를 부르지 않고 `outcome=bgm_held`를 돌려준다(fail-closed, 시험 있음) | `domains/marketing/.../media/bgm_flow.py`, `tests/test_media_bgm_flow.py` |
| 곡 단가 | `lyria-3-clip-preview` 곡당 0.04 USD(30초 클립), `lyria-3.5` 곡당 0.08 USD(풀 곡). 공식 문서 2026-11-11 확인 | `domains/marketing/.../media/prices.toml` 끝부분 |
| KRW 환산 | 영상·이미지와 같다: `ceil(usd × 환율 × (1 + 여유%))`. 환율 1,500·여유 10%를 예로 들면 0.04 USD → 66원, 0.08 USD → 132원(계산 예시이며 실제 값은 설정 환경변수) | `pricing.krw_estimate` |

## 요청: 규칙 변경 제안

### 1. `media_kind_known`에 audio 추가 (필수)
- 파일·줄: `thresholds.toml` 554줄 `allowed = ["image", "video"]` → `["image", "video", "audio"]` 제안. 547~548줄 주석과 `registry.toml` 55줄 주석(`image | video`)도 맞춰 주길 요청.
- 이유: 이게 없으면 BGM 호출이 항상 C로 보류돼 BGM 연결이 실제로 동작하지 않는다(마케팅 쪽은 보류 시 호출하지 않고 멈춘다).

### 2. audio 건당 상한 규칙 신설 (제안)
- 제안: `audio_cost_single` — `when = { media_kind = "audio" }`, `metric = "amount"`, `op = ">="`, `value = (TODO: 추후결정)`. 이미지·영상 규칙과 같은 모양이고, 건당 상한을 audio 몫으로 따로 두려는 것이다. 위치는 `image_cost_single`(572줄) 다음 573줄 부근.
- 근거: 곡당 0.04·0.08 USD는 환율 1,500·여유 10% 예시로 66원·132원이다. 이미지 상한 400원(`image_cost_single`)처럼 "단가표·환율이 이상할 때 사람이 보는" 기준으로 쓰려면 최대 곡 단가(132원)의 2~3배 범위가 후보다. 값은 `(TODO: 추후결정)`.
- 이 규칙이 없으면: audio는 건당 상한 없이 `media_cost_daily`와 마케팅 코드의 건당·월 상한(`MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM`, `MARKETING_MEDIA_MONTHLY_BUDGET_KRW`)만 막는다. `cost_krw`를 관문이 믿는 구조라(위 sns 답변) 건당 규칙을 두는 편이 안전하다고 본다.

### 3. 비용 금액·환산
- 새 필드는 필요 없다. 기존 `cost_krw`(정수 원, 호출 전 추정액)와 `claimed_cost_krw`(다르면 `amount_mismatch`)를 그대로 쓴다. 환산식도 `media_cost`와 같다(위 표).
- `media_cost_zero`(583~588줄)는 그대로 적용된다. 마케팅이 audio 비용이 0이 되게 보내지 않는다(단가표에 곡당 가격만 있음).

### 4. 영상 길이 8초 규칙과의 관계
- `video_duration`(591~597줄)은 `when media_kind=video`라 audio에 적용되지 않는다. **변경 요청 없음.**
- 마케팅은 audio params에 `duration_s`를 보내지 않는다(클립 길이는 모델이 정하고, 8초로 맞추는 것은 ffmpeg 후처리가 한다. 후처리 쪽에도 8초 상한이 있다). audio에 길이 규칙을 따로 두자는 제안은 하지 않는다.
- [확인 필요] `lyria-3.5`(풀 곡 약 2분)를 쓸 경우 단가표는 곡당 0.08 USD로 같지만 길이가 길다. 길이 상한을 관문에서 볼지는 safety 판단.

### 5. 등급 제안: B (영상과 같은 방식)
- 제안: 등록은 지금 `generate_media`(B, verifier safety)를 그대로 쓰고 audio를 같은 액션의 한 종류로 둔다. 별도 액션 `generate_audio`를 새로 만들지 않는다.
- 이유: (a) 돈이 나가지만 되돌릴 수 있고(생성물을 쓰지 않으면 된다), (b) 외부에 공개되는 단계가 아니라 SNS 게시(`publish_sns`, C)가 따로 있으며, (c) 영상과 같은 `cost_krw` 검증 구조라 코드 규칙(verifier safety)으로 충분하다. 별도 액션을 만들면 `registry.toml` 항목과 시험이 늘고 일일 합산(`media_cost_daily`)이 액션별로 갈라진다.
- 대안(safety 판단): 별도 액션 `generate_audio`로 분리하면 일일·건수 한도를 따로 둘 수 있다. `(TODO: 추후결정)`

### 6. 일일·월 상한
- 일일: `media_cost_daily`(575~580줄)는 이미 종류 구분 없이 합산하므로 audio도 자동으로 합쳐진다. 곡당 66~132원이라 영향은 작다. **별도 audio 일일 상한은 요청하지 않는다**(필요하면 safety가 판단 `(TODO: 추후결정)`).
- 월: 관문에 월 상한은 없고 마케팅 장부가 월 예산(`MARKETING_MEDIA_MONTHLY_BUDGET_KRW`)으로 막는다. 변경 요청 없음.
- [확인 필요] 영상 1건당 BGM 1곡을 쓰면 영상 1건의 비용이 영상+BGM이 된다. 영상 건당 상한(2,000원)과 합산해서 볼 필요는 없다고 보지만(각각 별도 호출) 일일 합산 10,000원에는 둘 다 들어간다.

## 기존 시험에 미칠 영향 (safety 쪽에서 판단·수정)
- `shared/safety/tests/test_marketing_sns_video.py` 198~202줄 `test_unknown_media_kind_is_held`는 `kind`가 `[None, "audio"]`다. 규칙 1을 적용하면 `"audio"` 케이스는 더 이상 `media_kind_known`으로 보류되지 않는다. 이 케이스를 `[None, "unknown"]` 등 허용 목록 밖 값으로 바꾸고, audio가 통과하는 시험과 `audio_cost_single` 시험을 더하는 것이 필요하다. 이 시험을 약하게 하지 말고 **다른 알 수 없는 값으로 바꿔 보류 검증은 유지**해 달라는 요청이다.
- `test_daily_total_adds_images_and_videos`(205~215줄)는 audio를 쓰지 않아 영향 없다고 읽었다. [확인 필요: 실제로 돌려 보지는 않았다]
- 마케팅 쪽 시험은 가짜 관문을 쓰므로 이 변경과 무관하다.

## 마케팅이 보낼 params (audio, 제안)
| 필드 | 값 |
|---|---|
| `media_kind` | `"audio"` |
| `resolution` | 템플릿 값(`"30s"` 클립 또는 `"full-song"`) |
| `provider`, `scene_template_id`, `post_draft_id`, `request_sha256` | 영상과 같다 |
| `cost_krw` | 곡당 단가표로 계산한 정수 원 |
| `claimed_cost_krw`(선택) | 요청 쪽 값. 다르면 `amount_mismatch` |
| `ai_generated` | 항상 `true` |
| `duration_s` | 보내지 않는다 |

## 정해 주세요 (질문)
1. `media_kind_known`에 audio를 추가해도 되는지, 아니면 별도 액션 `generate_audio`로 분리할지. `(TODO: 추후결정)`
2. audio 건당 상한 규칙(`audio_cost_single`)을 둘지, 둔다면 값. `(TODO: 추후결정)`
3. 풀 곡(`lyria-3.5`)을 허용할지(허용하면 길이 상한을 어떻게 볼지). `(TODO: 추후결정)`
4. 기존 시험 `test_unknown_media_kind_is_held`의 audio 케이스 처리 방식.

## 하지 않는 것
- 마케팅이 등급을 정하거나 `shared/safety/`를 직접 고치지 않는다.
- `execute()`가 `executed`를 돌려주기 전에 BGM 공급자·ffmpeg를 부르지 않는다. 규칙이 바뀌기 전에는 BGM 모드를 켜도 audio가 보류돼 영상만 생성된 채 `bgm_held`로 멈춘다(영상 비용은 장부에 남고, 같은 요청을 다시 보내면 영상은 재생된다).
- 영상 자체의 오디오는 쓰지 않는다(구글 약관이 상업 이용을 명시하지 않음). AI가 만든 연주곡 BGM만 입히고, AI 생성 표시는 기존 정책을 따른다.

## 완료 기준
- [ ] `media_kind_known`에 audio 허용(또는 별도 액션) 결정
- [ ] audio 건당 상한 규칙과 값 결정
- [ ] 기존 시험 조정과 새 시험
- [ ] 마케팅에서 `MARKETING_AUDIO_MODE=bgm_overlay`로 관문 통과 확인(실제 공급자 호출 전, 사람이 키를 넣는 단계)

## 관련 문서
- `backend/docs/notes/marketing/sns-safety-request.md`, `sns-design.md`
- `backend/domains/marketing/src/marketing_agent/media/bgm_flow.py`, `audio_mix.py`, `gemini_audio.py`
