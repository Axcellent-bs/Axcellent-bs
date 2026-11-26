"""EmbeddingGemma 2(Gemma 4 기반, `google/embeddinggemma-2`)를 로컬에서 띄우는
아주 얇은 OpenAI 호환 `/embeddings` 서버.

왜 필요한가:
- `collect_variant.py`가 쓰는 `LLMClient.embed()`는 `POST <base_url>/embeddings`에
  `{"model": ..., "input": [...]}`을 보내고 `{"data": [{"embedding": [...], "index": N}],
  "usage": {...}}` 모양을 기대한다(`shared/agent-template/src/ax_agent_template/llm.py`).
  EmbeddingGemma 2는 API가 아니라 로컬 가중치라서, 이 서버가 그 계약만 흉내 낸다.
- Apache-2.0, 게이팅 없음(요청·승인 불필요) → 다운로드·추론 둘 다 비용이 안 든다.
  OpenAI·Upstage처럼 충전이 필요 없다.

준비 (최초 1회):
    pip install sentence-transformers

실행:
    python docs/notes/support/embed-compare/local_embeddinggemma_server.py
    (첫 실행은 모델 다운로드 때문에 오래 걸릴 수 있다. 740M, CPU에서도 돈다 —
    24GB GPU가 꼭 필요하진 않다. float16은 쓰지 말 것: bfloat16·float32만 권장)

다른 터미널에서 (backend/ 기준, 서버가 떠 있는 동안):
    EMBED_MODEL=embeddinggemma-2 EMBED_BASE_URL=http://127.0.0.1:8500 \
      EMBED_API_KEY=local-dummy \
      uv run python docs/notes/support/embed-compare/collect_variant.py embeddinggemma-2

`EMBED_API_KEY`는 실제로 검사하지 않지만, `LLMClient.embed()`가 "임베딩 주소가 LLM
주소와 다른데 키가 없으면 멈춘다" 규칙이 있어서 빈 값이 아니기만 하면 된다.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8500
MODEL_ID = "google/embeddinggemma-2"


def _load_model():
    from sentence_transformers import SentenceTransformer

    print(f"{MODEL_ID} 불러오는 중 (최초 실행은 다운로드 때문에 오래 걸릴 수 있다)...")
    model = SentenceTransformer(MODEL_ID)
    print("준비 완료.")
    return model


class Handler(BaseHTTPRequestHandler):
    model = None  # 클래스 속성: 서버 시작할 때 한 번만 불러온다

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/embeddings":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        texts = body.get("input") or []
        if isinstance(texts, str):
            texts = [texts]
        vectors = self.model.encode(texts, convert_to_numpy=True).tolist()
        data = [{"embedding": vec, "index": i} for i, vec in enumerate(vectors)]
        payload = json.dumps({"data": data, "usage": {}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_str: str, *args: object) -> None:
        pass  # 요청마다 로그를 찍지 않는다 (콘솔을 조용하게)


def main() -> None:
    Handler.model = _load_model()
    print(f"포트 {PORT}에서 대기 중 (Ctrl+C로 종료)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
