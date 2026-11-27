# 임베딩 모델 비교 (주말 목표, 담당: 병환)

데이터 출처: `domains/data/eval/product_link/`의 기존 보정셋(42개 질의, 정답은 "질의를
만든 방식"에서만 정해서 유사도를 보고 정하지 않았다 — 순환 논리 없음). 그 폴더는 읽기만
하고 아무것도 고치지 않는다. 비교 결과는 이 폴더(`support` 담당 notes)에만 쓴다.

## 왜 이 방법이 객관적인가

- 정답(golden set)이 임베딩 모델과 무관하게 이미 고정돼 있다 → 모델마다 다른 기준으로
  채점할 위험이 없다.
- `score.py`와 완전히 같은 채점 로직(연결 규칙·지표·기준 고르는 법)을 그대로 옮겨 썼다
  (`score_variant.py`) → data 부서가 2026-11-03에 쓴 방법과 1:1 비교 가능.
- basic 글 모양(소재=이름, 상품="이름 (카테고리)")만 쓴다. rich는 근거 글(뉴스·유튜브
  제목)이 저장돼 있지 않아 재수집해야 하는데, 그러면 모델마다 다른 날 데이터를 쓰게 돼
  비교가 깨진다. basic은 완전히 고정값이라 주말 안에 돌리기에 적합하다.

## 이미 한 것

- 스크립트 작성·검증 완료: `collect_variant.py`(모델별 임베딩 수집), `score_variant.py`(채점).
- **검증**: data 부서가 2026-11-03에 이미 모은 gemini-embedding-001 결과(`sims/basic.json`)를
  그대로 가져와 `score_variant.py`로 돌려본 결과, data 부서 README의 공식 기준표(재현율 1.00,
  정밀도 1.00, holdout 재현율 0.86)와 **정확히 일치**했다. 로직이 맞다는 뜻.
- `sims/basic_gemini-embedding-001.json` — 베이스라인(현재 모델) 결과, 이미 있음.

## 이 세션에서 못 한 것

이 작업 환경에는 OpenAI·Upstage 같은 외부 임베딩 API 키가 없고 로컬에서 모델을 띄울
수도 없어서(`.env`는 커밋되지 않고, 이 세션에는 실제 키도 GPU/모델 다운로드 환경도
없다), 아래 3개 모델은 **실제 호출을 하지 못했다**. 로컬 PC(키 발급·모델 다운로드가
가능한 환경, `run_all.sh` 쓰는 곳)에서 아래 명령을 그대로 실행하면 된다. API 모델
하나당 호출은 42+8=50개 텍스트를 1번(50개 이하라 청크 1개)만 묶어서 보내는 정도라
몇 초~몇십 초면 끝난다.

## 주말까지 실행할 4개 모델 (OpenAI·Upstage 키는 병환 개인 충전분, 비용은 최대 $5 수준)

```bash
cd backend

# 1) 베이스라인 (이미 돼 있음, 재확인만 하려면)
EMBED_MODEL=gemini-embedding-001 \
  uv run python docs/notes/support/embed-compare/collect_variant.py gemini-embedding-001

# 2) OpenAI 상용 최상위
EMBED_MODEL=text-embedding-3-large EMBED_BASE_URL=https://api.openai.com/v1 EMBED_API_KEY=sk-... \
  uv run python docs/notes/support/embed-compare/collect_variant.py text-embedding-3-large

# 3) 한국어 특화 (Upstage, OpenAI 호환)
EMBED_MODEL=solar-embedding-1-large EMBED_BASE_URL=https://api.upstage.ai/v1/solar EMBED_API_KEY=up_... \
  uv run python docs/notes/support/embed-compare/collect_variant.py solar-embedding-1-large

# 4) EmbeddingGemma 2 (Gemma 4 기반, 자체호스팅 — 무료, 별도 터미널에서 서버 먼저 실행)
#    pip install sentence-transformers
#    python docs/notes/support/embed-compare/local_embeddinggemma_server.py
EMBED_MODEL=embeddinggemma-2 EMBED_BASE_URL=http://127.0.0.1:8500 EMBED_API_KEY=local-dummy \
  uv run python docs/notes/support/embed-compare/collect_variant.py embeddinggemma-2

# 한 번에 비교표 뽑기
uv run python docs/notes/support/embed-compare/score_variant.py \
  gemini-embedding-001 text-embedding-3-large solar-embedding-1-large embeddinggemma-2
```

결과는 markdown 표로 바로 나온다 (모델 / 고른 설정 / dev 재현율·정밀도·오연결률 /
holdout 재현율·정밀도·오연결률). 그대로 스프레드시트 "부서별테스트결과" 시트나
소통방에 붙여 넣으면 된다.

`text-embedding-3-small`은 이번 4개에서 뺐다(대신 EmbeddingGemma 2를 넣음). 필요하면
2)번과 같은 모양으로 `EMBED_MODEL=text-embedding-3-small`만 바꿔 추가로 돌리면 된다.

## EmbeddingGemma 2 준비 메모

- `google/embeddinggemma-2`: Apache-2.0, 게이팅 없음(승인 대기 불필요), 740M, 768차원,
  text·code·image·video·audio 통합 임베딩 공간. 다운로드·추론 모두 무료(API 과금 없음).
  CPU에서도 돌아가지만 bfloat16·float32를 쓸 것(float16 금지).
- `local_embeddinggemma_server.py`가 `collect_variant.py`·`LLMClient.embed()`가 기대하는
  OpenAI 호환 `/embeddings` 계약만 흉내 내는 얇은 래퍼다. 이 서버를 먼저 띄운 채로 위
  4)번 명령을 돌리면 된다.

## 다음 라운드 (시간 되면)

bge-m3·multilingual-e5-large 등 나머지 자체호스팅 후보는 이번 주말 범위 밖이다. 같은
방식(`local_embeddinggemma_server.py`를 모델만 바꿔 재사용하거나 TEI로 띄우고
`EMBED_BASE_URL=http://localhost:<port>`)으로 다음에 추가하면 된다.

## 다른 부서 적용 요청 (태윤 제안, #소통 2026-11-27 14:13)

고객지원(상품-문의 연결) 결과를 공유한 뒤, 태윤이 같은 방식(`collect_variant.py` /
`score_variant.py` / EmbeddingGemma 로컬 서버 그대로 재사용)으로 비교해볼 만한 후보
4건을 정리해 왔다. 세 영역(마케팅·재무·안전 로그) 다 태윤 담당이고, 적용 PR은 결과
보고 같이 정하기로 했다. **지금 단계는 "낱말이 들어 있는지만 보는 규칙"이라서,
고객지원 때처럼 같은 골든셋·같은 기준으로 견줘 보면 좋겠다는 제안이고, 아직 데이터
수집·golden set 제작은 시작 전이다.**

1. **마케팅: 금지 표현·참여 유도 판정 (1순위)**
   - 지금: `sns.py`의 `FORBIDDEN_PHRASES`·`ENGAGEMENT_WORDS`, `notice_text.py` 공지
     금지 패턴이 낱말 포함으로만 막음
   - 문제: 설명 문장에 "참여"만 있어도 막혀서 #1667에서 규칙을 좁힘. 오탐·누락 둘 다 있음
   - 부탁: 막아야 할 글 / 통과해야 할 글로 골든셋 → 지금 낱말 규칙 vs 임베딩 유사도(대표
     금지 문장과 비교) 비교
   - 좋다/나쁘다 기준: 정밀도(멀쩡한 글을 막지 않음) 우선, 그다음 재현율
2. **마케팅: 기획안 검토 낯말 판정**
   - 지금: `plan_rules`의 `OTHER_PERSON_WORDS`·`CROWD_WORDS`·`WIDE_WORDS`로 다른 인물·
     군중·넓은 장면 판정
   - 문제: 낯말을 계속 손으로 넣고 빼는 중 (#1911·#1944 군중 낯말, #1888 외형 낯말 예외)
   - 부탁: 1번과 같은 방식, 장면 설명 문장으로 비교
   - 기준: 재현율(빠뜨리면 영상에 다른 인물이 나옴) 우선
3. **재무: 은행 입금 ↔ PG 정산 맞추기**
   - 지금: `bank.py`가 적요에 `pg_memo` 글자가 그대로 있는 입금만 PG 입금으로 봄
   - 문제: 은행·약칭마다 적요 표기가 다르면 놓침
   - 부탁: 적요 샘플에 "PG 입금 맞음/아님" 정답 → 글자 포함 vs 임베딩 비교. 금액·날짜
     판정은 지금 규칙 그대로 두고 **적요 판단만** 비교
   - 기준: 오연결률 0 (돈이라 잘못 붙는 것보다 놓치는 게 나음)
   - 참고: **실제 은행 적요 샘플이 필요함 — 지금은 임시 예시 데이터만 있음**
4. **안전 로그: 비슷한 과거 결정 찾기 (제안 단계)**
   - 지금: 승인 카드에 보류 사유(`hold_reason`)·보고 사유는 있는데, 과거 비슷한 건의
     결정은 안 보임
   - 부탁: 지난 승인·거절 기록에서 사유가 비슷한 건을 찾을 수 있는지 **검색 정확도만**
     먼저 측정 (지금 코드에 없는 새 기능이라 화면은 결과 보고 결정)

**공통 방법**: `embed-compare`의 `collect_variant.py` / `score_variant.py`,
EmbeddingGemma 로컬 서버를 그대로 쓴다. 결과는 이번 고객지원 비교처럼 표 하나로
(모델 · 비용 · 재현율 · 정밀도 · 오연결률) 정리해서 공유한다.

**다음 단계**: 4건 모두 아직 골든셋이 없다. 데이터(1·2번: 글/장면 설명 예시, 4번은
우선 보류) 먼저 모아야 실행 가능 — 이번 주말 4개 모델 비교가 끝난 뒤 순서를 정한다.

## 다른 부서 적용 요청 2 (준하 제안, #소통 2026-11-27 15:43)

고객지원 공유 표 형식 그대로 두고, 같은 모양의 **별도 표**로 총괄·아카이브·인사 쪽
후보를 정리해 왔다 (세 영역 다 준하 담당). 고객지원과 달리 이쪽은 임베딩뿐 아니라
LLM 판정 항목도 섞여 있어서 항목마다 평가 데이터·지표가 다르다.

**지금 쓰는 모델** (dev 코드 기준)
- 임베딩: 아카이브만 사용, `gemini-embedding-001`
- LLM: 세 AI(총괄·아카이브·인사) 모두 `claude-haiku-5-5`(11/19 확인, 이후 변경은 미확인)
- 직접 학습한 딥러닝 모델은 없음
- 비교 후보: 임베딩은 공유 표의 모델 7개, LLM은 `claude-haiku-5-5`(현재)·`claude-sonnet-5-5`(추가 후보)

**항목별 평가 데이터·지표·우선순위**

| 순위 | 항목 | 평가 데이터 | 지표 | 현재 값 |
| --- | --- | --- | --- | --- |
| 1 | 아카이브 카드 검색(임베딩) | 질의 dev 33·test 67, 카드 620 | 재현율@1·@5, MRR, 오연결률 | 0.94·0.98, 0.95, 0.23 |
| 1 | 아카이브 중복 결정 판단(임베딩) | 문장 쌍 dev 76·test 32 | 정확도, 정밀도, 재현율, 잘못 병합 0 | 0.97, 1.00, 0.95, 0건 |
| 2 | 총괄 의뢰 분류(LLM) | 30문장 | 정답률, 엉뚱하게 보냄 0, 위험 거절 100% | 30/30 |
| 3 | 아카이브 같은 결정 판정(LLM) | 19쌍 | 3종 판정 정확도, 잘못 병합 0 | 19/19 |
| 3 | 인사 발령 문장 정리(LLM) | 38문장 | 정확 85%↑, 경고 없는 오류 0, 주입 0, 거짓 경고 10%↓ | 22/22, 0, 0, 0% |
| 보류 | 아카이브 관련 카드 검증(LLM, 기본 꺼짐) | holdout 40 | 1위 정답, 정답 없을 때 무응답 | 20/20, 19/20 |
| 평가만 | 설명 문장 생성(아카이브·인사) | 경고 22, 설명문 24 | 틀린 숫자 0, 기본 문장 대체율 | 대체 0, 인사 틀린 숫자 0 |
| 평가만 | 대화 답변(세 AI) | 없음 | 근거 밖 숫자 0 | 미측정 |

**돌릴 때** (`backend` 폴더, `domains/...`·`scripts/...`)
- 검색: `.env`의 `MEMORY_EMBED_MODEL`(주소·키는 `MEMORY_EMBED_BASE_URL`·`_API_KEY`)을
  바꾸고 `uv run python domains/archive/eval/run_eval.py dense 620`. `.env` 값이
  환경변수보다 우선
- 중복·판정: `domains/archive/eval/link_eval_v2.py`
- 분류: `scripts/eval_request_classifier.py --env-file <.env>`. 샘플은 일 18개일 때
  것(지금 24개)이라 어려운 문장 추가가 필요
- 인사: `domains/hr/eval/org_change_eval.py`는 아카이브 분리 뒤 import 경로가 안 맞아
  수정 후 실행해야 함
- `results/` 파일을 덮어쓰니 먼저 백업. 비용·응답 시간은 모델별로 새로 기록

**주의**
- 현재 값은 모두 합성 데이터, 정답은 Claude 표시(사람 확인 전)
- 모델을 바꾸면 기준값은 dev에서 다시 정하고 test는 한 번만
- 파인튜닝 학습 데이터는 아직 없음. 평가 데이터는 학습에 쓰지 않고, 최종 확인용 새
  holdout 권장
- 파인튜닝은 자체 실행 공개 모델(예: EmbeddingGemma 2) 대상 제안. API 모델의
  파인튜닝 가능 여부는 확인 필요

## 다른 부서 적용 요청 3 (예진 제안, #소통 2026-11-27 15:46)

기획·개발 AI 두 세션 몫을 정리해 왔다 (각 담당 세션 초안). 준하 제안(총괄·아카이브·인사)과
마찬가지로 고객지원 표 형식 그대로 두고, 같은 모양의 별도 표를 각 AI마다 하나씩 쓴다.

### 기획 AI

**지금 쓰는 모델**
- 임베딩: `PLANNING_EMBED_MODEL` → 없으면 `EMBED_MODEL` → 없으면 글자 2-gram. 비슷한
  요청 후보 5개 찾기(`shared/workflow-core/ax_workflow/related.py`, 하한 embed
  0.6·2-gram 0.3). 서버 값은 확인 전
- LLM: `PLANNING_LLM_MODEL` → 없으면 `LLM_MODEL`. 쓰는 곳: 요청 관계 판단, 기획서
  초안, 자유 작성 → 필수 칸 채우기, 인사이트 → 제안 초안, 영상 제작안, 콘텐츠 포즈,
  대화방
- 비교 후보: 임베딩은 병환 님 표와 같은 4개, LLM은 `claude-haiku-5-5`(현재)·
  `claude-sonnet-5-5`

**항목별 기준과 우선순위**

| 순위 | 항목 | 평가 데이터 | 지표 | 현재 값 |
| --- | --- | --- | --- | --- |
| 1 | 관계 판단(same·conflict·update·related) | 의뢰문·근거 가린 사본으로 골든셋 | same 정밀도·재현율, 4분류 정확도 | 만들 것(평가 데이터 없음) |
| 2 | 영상 제작안 | 첫 답이 코드 검사를 통과한 비율(1판·2판) | 금칙어·지어낸 숫자·실명 0 | 만들 것(평가 데이터 없음) |
| 3 | 기획서 초안 | 요구사항이 개발 작업 하나로 바로 되는 비율 | 근거 관찰값·제약 문장·AXcellent 화면 요구사항 0 | 만들 것(평가 데이터 없음) |
| 4 | 제안 초안 | 형식 통과율 | 근거에 없는 숫자 0(검사는 코드) | 규칙 경로만 holdout 1400/1400, LLM 미측정 |
| 5 | 칸 채우기 | 칸 채움률·남은 질문 수 | 지어낸 값 0 | 만들 것(평가 데이터 없음) |
| 제외 | 콘텐츠 포즈·대화방 | — | — | 형식만 보면 됨 |
| 비고 | 비슷한 요청 찾기(임베딩) | 후보 5개 안 재현율·정밀도 | 같은 일 요청을 놓치지 않기 | 비고: 2-gram 대비 차이 |

**돌릴 때**
- 기존 holdout(`domains/planning/eval/holdout.py --n 200 --seed 20261110_02`)은 LLM을
  꺼서 규칙만 재요 → 모델 비교용 하네스는 새로 필요. 같은 입력에 `PLANNING_LLM_MODEL`·
  `PLANNING_EMBED_MODEL`만 바꾸고, 판정은 코드 검사 함수(`plan_checks`·
  `video_plan.reject_reason`·`spec_writer` 거르기)로
- 의뢰문·근거는 가린 사본으로 골든셋. 영상 제작안은 제작안에서 멈춤(크레딧 0)

### 개발 AI

**지금 쓰는 모델**
- 임베딩: 없음
- LLM: `DEV_LLM_*` → 없으면 `LLM_*`, 운영은 claude-haiku-5-5. 쓰는 곳: 코딩
  (`coder.py`: 후보 파일 고르기 → 확정 → 파일별 쓰기 → 완료 기준 리뷰 → 한 번 고침 →
  CI 실패 로그 보고 한 번 고침), 레포 고르기(`repo_routing.py`, 규칙 점수가 기준선),
  개발 대화. 후보 claude-haiku-5-5·claude-sonnet-5-5

**항목별 기준과 우선순위**

| 순위 | 항목 | 평가 데이터 | 지표 | 현재 값 |
| --- | --- | --- | --- | --- |
| 1 | 작업 성공률(코딩) | 완료 기준을 모두 채운 PR / 작업 수 | 정답 diff가 있는 작업만 | 비고: 실패 이유 분포. 평가 데이터 없음 → 만들 것(11/24~26 운영 시험 요청이 후보) |
| 2 | 범위 정확도(코딩) | 기대 파일·기대 줄만 바꾼 비율 | "다른 줄 바꾸지 마" 같은 기준 포함 | 미측정 |
| 3 | CI 자동 수정 성공률 | 실패 → 한 번 고친 뒤 통과 비율 | 일부러 깐 PR 케이스 | 평가 데이터 없음 → 만들 것 |
| 4 | 레포 고르기 정확도 | 정답 레포 비율 | 연결 2개 이상 + 정답 표시 | 평가 데이터 없음 → 만들 것 |
| 5 | 대화 답 정확도 | 바뀔 파일·완료 기준, CI 원인 파일·줄 맞힌 비율 | 같은 질문 묶음 | 미측정 |

순위: 작업 성공률 > 범위 정확도 > CI 자동 수정 > 레포 고르기 > 대화(같으면 토큰·시간
적은 쪽)

**돌릴 때**
- LLM 비교 하네스 없음 → 만들 것. `DEV_LLM_MODEL`만 바꿔 같은 케이스로
- 코딩 케이스는 실제 레포에 PR이 생기니 고객사 레포 말고 목 형상관리(MockVcsAdapter)에서.
  실행마다 토큰·호출 수도 기록. 정답 diff·정답 레포는 사람이 미리 적은 것만

**참고 기준선**: 규칙 skill holdout 95.0%(`domains/dev/eval/holdout.py`), 모델을
바꿔도 그대로여야 함
