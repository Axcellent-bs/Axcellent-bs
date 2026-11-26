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
