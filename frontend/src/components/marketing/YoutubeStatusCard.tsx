import { useCallback, useState } from "react";
import { useVisibleInterval } from "@/lib/visibleInterval";
import { shortDateTime } from "@/lib/format";
import { fetchYoutubeStatus, type YoutubeStatus } from "@/lib/youtube";

// 마케팅 › SNS·미디어 › 이미지 시트 위. AXcellent 유튜브 채널이 연결돼 있는지 화면이 보일 때 60초마다 다시 본다.
// 서버가 토큰을 새로 받아 채널 이름을 읽어 본다(영상은 올리지 않음). 키 값은 오지 않는다.

const REFRESH_MS = 60_000;
const LOOK: Record<string, { dot: string; label: string; note: string }> = {
  ok: { dot: "bg-emerald-500", label: "연결됨", note: "승인된 영상은 이 채널에 올라가요" },
  missing_keys: { dot: "bg-ink-300", label: "연결 전", note: "서버에 유튜브 연결 값(MARKETING_YOUTUBE_*)이 아직 없어요" },
  invalid_credentials: { dot: "bg-red-500", label: "연결 거절", note: "연결 값이 만료됐거나 틀렸어요. 다시 연결해 주세요" },
  error: { dot: "bg-amber-500", label: "닿지 않음", note: "유튜브에 닿지 않았어요. 잠시 뒤 다시 확인해요" },
};

export function YoutubeStatusCard() {
  const [status, setStatus] = useState<YoutubeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchYoutubeStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "유튜브 연결 상태를 읽지 못했어요");
    } finally {
      setLoading(false);
    }
  }, []);

  useVisibleInterval(load, REFRESH_MS);

  const look = status ? LOOK[status.connection] ?? LOOK.error : null;
  return (
    <section className="card flex flex-wrap items-center gap-x-4 gap-y-2 px-6 py-4" aria-labelledby="yt-status-title" aria-live="polite">
      <h2 id="yt-status-title" className="text-sm font-semibold">유튜브 연결</h2>
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
          {status.channel && <span className="chip bg-ink-50 text-ink-700">채널 {status.channel}</span>}
          <span className="text-sm text-ink-700">{look.note}</span>
          <span className="text-xs tabular-nums text-ink-500">{shortDateTime(status.checked_at)} 확인 · 1분마다</span>
        </>
      )}
      <button type="button" className="btn-ghost ml-auto text-xs" onClick={() => void load()} disabled={loading}>{loading ? "확인 중" : "지금 확인"}</button>
    </section>
  );
}
