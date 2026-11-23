import { useCallback, useState } from "react";
import { useVisibleInterval } from "@/lib/visibleInterval";
import { shortDateTime } from "@/lib/format";
import { fetchHiggsfieldStatus, type HiggsfieldConnection, type HiggsfieldStatus } from "@/lib/higgsfield";

// 마케팅 › SNS·미디어 › 이미지 시트 위. Higgsfield에 실제로 연결돼 있는지 화면이 보일 때 30초마다 다시 본다.
// 서버가 Higgsfield에 키를 한 번 보여 주고(이미지를 만들지 않음) 받아들여지는지 알려 준다. 키 값은 오지 않는다.

const REFRESH_MS = 30_000;
const LOOK: Record<HiggsfieldConnection, { dot: string; label: string }> = {
  ok: { dot: "bg-emerald-500", label: "연결됨" },
  invalid_key: { dot: "bg-red-500", label: "키 거절" },
  unreachable: { dot: "bg-amber-500", label: "닿지 않음" },
  not_configured: { dot: "bg-ink-300", label: "키 없음" },
};

export function HiggsfieldStatusCard() {
  const [status, setStatus] = useState<HiggsfieldStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchHiggsfieldStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Higgsfield 연결 상태를 읽지 못했어요");
    } finally {
      setLoading(false);
    }
  }, []);

  useVisibleInterval(load, REFRESH_MS);

  const look = status ? LOOK[status.connection] : null;
  return (
    <section className="card flex flex-wrap items-center gap-x-4 gap-y-2 px-6 py-4" aria-labelledby="hf-status-title" aria-live="polite">
      <h2 id="hf-status-title" className="text-sm font-semibold">Higgsfield 연결</h2>
      {error ? (
        <span className="text-sm text-accent" role="alert">{error}</span>
      ) : !status || !look ? (
        <span className="text-sm text-ink-500">확인하는 중…</span>
      ) : (
        <>
          <span className="flex items-center gap-2 text-sm font-medium text-ink-900">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${look.dot}`} aria-hidden="true" />
            {look.label}
          </span>
          <span className="text-sm text-ink-700">{status.message_ko}</span>
          <span className="chip bg-ink-50 text-ink-700" title="MARKETING_HIGGSFIELD_IMAGE_MODEL">
            이미지 모델 {status.image_model ?? "미설정"}
          </span>
          <span className="text-xs tabular-nums text-ink-500">{shortDateTime(status.checked_at)} 확인 · 30초마다</span>
        </>
      )}
      <button type="button" className="btn-ghost ml-auto text-xs" onClick={() => void load()} disabled={loading}>{loading ? "확인 중" : "지금 확인"}</button>
    </section>
  );
}
