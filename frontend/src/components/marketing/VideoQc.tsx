import { TONE } from "@/components/finance/bits";
import { QC_NOTE, QC_NO_AUTO, canRegenerate, qcView, regenerateLabel, regenerateNote, type RegenerateResult, type VideoQcState } from "@/lib/videoQcView";

const TONES = { ok: TONE.ok, bad: TONE.bad, muted: TONE.muted } as const;

/** 영상 기술 검사 (11/26): 칩(모두 통과 / N개 확인 필요) + 표(검사·결과·내용) + 확인 필요면 [영상 다시 만들기 · 사람 결정 · 약 N 크레딧].
 *  버튼은 지금 있는 영상 다시 요청 길(사람 승인·영상 예산 그대로)만 부른다. 자동으로 다시 만들지 않는다 */
export function VideoQc({ qc, videoStatus, credits, busy, error, regenerate, onRegenerate }: {
  qc: VideoQcState | null | undefined; videoStatus: string | null | undefined; credits: number | null | undefined;
  busy: boolean; error?: string | null; regenerate?: RegenerateResult | null; onRegenerate: () => void;
}) {
  const v = qcView(qc);
  if (!v) return null;
  const note = regenerateNote(regenerate);
  return (
    <section className="space-y-2" aria-label="영상 기술 검사">
      <p className="flex flex-wrap items-center gap-1.5 text-xs font-semibold text-ink-900">
        기술 검사 <span className={`chip text-[11px] ${TONES[v.chip.tone]}`}>{v.chip.label}</span>
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[18rem] text-left text-xs">
          <thead><tr className="border-b border-ink-100 text-[11px] text-ink-500"><th className="px-1.5 py-1 font-medium">검사</th><th className="px-1.5 py-1 font-medium">결과</th><th className="px-1.5 py-1 font-medium">내용</th></tr></thead>
          <tbody>
            {v.rows.map((r) => (
              <tr key={r.id} className="border-b border-ink-100 align-top">
                <td className="px-1.5 py-1 text-ink-900">{r.label}</td>
                <td className="px-1.5 py-1"><span className={`chip text-[11px] ${TONES[r.tone]}`}>{r.result}</span></td>
                <td className="px-1.5 py-1 tabular-nums text-ink-700">{r.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-ink-500">{QC_NOTE}</p>
      {note && <p className="text-[11px] text-ink-700" role="status">{note}</p>}
      {canRegenerate(videoStatus, qc) && (
        <p className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={onRegenerate}>{busy ? "요청 중…" : regenerateLabel(credits)}</button>
          <span className="text-[11px] text-ink-500">{QC_NO_AUTO}</span>
        </p>
      )}
      {error && <p className="text-[11px] text-red-700" role="alert">{error}</p>}
    </section>
  );
}
