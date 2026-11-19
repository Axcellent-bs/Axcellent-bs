import { useCallback, useState } from "react";
import { Link } from "react-router";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { shortDateTime } from "@/lib/format";
import { getPublishControl, setPublishControl } from "@/lib/briefs";
import { approvalLink } from "@/lib/briefMedia";
import { useVisibleInterval } from "@/lib/visibleInterval";
import { publishControlHttpErrorKo, publishControlLine, publishControlResultKo, type PublishControlResult, type PublishControlState } from "@/lib/publishControl";

// Instagram 자동 게시 스위치. 계정이 정지돼도 초안·미디어·자동 넘김은 그대로 돌고 자동 게시만 끈다. 꺼진 동안은 승인된 건을 사람이 직접 올리고 링크를 기록한다(수동 게시).
// - 현재 상태·사유·바꾼 사람·시각은 서버가 준 값만 보여 준다(못 받으면 게시 가능이라고 짐작하지 않는다).
// - 끄기는 누르면 바로 적용된다(A 등급). 켜기는 사람 승인(C 등급)이 한 번 들어가므로 보류로 올 수 있고,
//   승인 화면에서 승인한 뒤 같은 버튼을 다시 누르면 적용된다. 이 화면이 대신 승인하지 않는다.
// - 바꾸는 사람은 사람 토큰으로만 한다. 이름은 서버가 토큰에서 채운다.

const REFRESH_MS = 15_000;
const TONE: Record<string, string> = { ok: "bg-brand-50 text-brand-700", warn: "bg-amber-50 text-amber-800", bad: "bg-red-50 text-red-800" };

export function PublishControlCard() {
  const [state, setState] = useState<PublishControlState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PublishControlResult | null>(null);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const answer = await getPublishControl();
      const out = answer.output;
      setState(answer.outcome === "state" && out && typeof out.paused === "boolean" ? ({ history: [], ...out } as PublishControlState) : null);
      setError(null);
    } catch (e) {
      setState(null);
      setError(publishControlHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code));
    } finally {
      setLoaded(true);
    }
  }, []);
  useVisibleInterval(load, REFRESH_MS);

  async function toggle() {
    if (!state || busy) return; // 클릭 잠금
    setBusy(true);
    setResult(null);
    setReviewId(null);
    try {
      const answer = await setPublishControl(!state.paused, state.paused ? "" : reason.trim());
      const shown = publishControlResultKo(answer);
      setResult(shown);
      if (shown.kind === "held") setReviewId(answer.output?.review_id ?? null);
      if (shown.kind === "updated") setReason("");
    } catch (e) {
      setResult({ kind: "failed", text: publishControlHttpErrorKo((e as { status?: number }).status, (e as { code?: string | null }).code) });
    } finally {
      setBusy(false);
      void load();
    }
  }

  const line = loaded ? publishControlLine(state, shortDateTime) : null;
  return (
    <section className="card space-y-3 p-6" aria-label="Instagram 자동 게시 스위치">
      <div>
        <h2 className="text-lg font-semibold">Instagram 자동 게시</h2>
        <p className="mt-1 text-xs text-ink-500">
          계정 문제가 있을 때 자동 게시만 끌 수 있습니다. 꺼 두면 승인된 건은 사람이 Instagram에 직접 올리고 게시물 링크를 기록하는 수동 게시로 이어집니다. 초안·미디어 요청·미디어 생성·자동 넘김은 멈추지 않습니다. 끄기는 바로 적용되고, 다시 켜기는 사람 승인이 한 번 필요합니다.
        </p>
      </div>
      {!loaded && <p className="text-sm text-ink-500">불러오는 중</p>}
      {line && (
        <p className={`rounded-xl px-3 py-2 text-sm ${TONE[line.tone]}`} role="status">
          <span className="font-semibold">{line.title}</span> · {line.detail}
        </p>
      )}
      {error && <p className="text-xs text-red-700" role="alert">{error}</p>}
      {state && (
        <div className="flex flex-wrap items-end gap-3">
          {!state.paused && (
            <label className="flex-1 text-xs text-ink-700">
              끄는 사유(선택, 200자까지, 비밀값은 적지 않습니다)
              <input className="field mt-1 w-full" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="예: 계정 정지 재고 대기" />
            </label>
          )}
          <button className={state.paused ? "btn-primary text-xs" : "btn-ghost text-xs"} disabled={!HUMAN_TOKEN_SET || busy} onClick={() => void toggle()}>
            {busy ? "처리 중" : state.paused ? "자동 게시 켜기" : "자동 게시 끄기"}
          </button>
          {!HUMAN_TOKEN_SET && <p className="w-full text-xs text-ink-500">사람 토큰이 설정되지 않아 바꿀 수 없습니다.</p>}
        </div>
      )}
      {result && (
        <p className={`rounded-xl px-3 py-2 text-sm ${result.kind === "failed" ? TONE.bad : result.kind === "held" ? TONE.warn : TONE.ok}`} role="status">
          {result.text}
          {reviewId && <> <Link className="font-medium underline" to={approvalLink(reviewId)}>사람 승인 화면 열기</Link></>}
        </p>
      )}
      {state && state.history.length > 0 && (
        <details className="text-xs text-ink-500">
          <summary className="cursor-pointer">최근 변경 내력 {state.history.length}건</summary>
          <ul className="mt-2 space-y-1">
            {state.history.map((h, i) => (
              <li key={`${h.updated_at}-${i}`}>{shortDateTime(h.updated_at)} · {h.updated_by} · {h.paused ? "끔" : "켬"}{h.reason ? ` · ${h.reason}` : ""}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
