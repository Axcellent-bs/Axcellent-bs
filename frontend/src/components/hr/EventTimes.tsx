import type { OrchIncident } from "@/lib/agentChat";
import { durationKo, secondsBetween, segmentsOf, type EventTimes as Times } from "@/lib/duration";

// 이벤트 하나가 어떻게 흘렀는지. 시각은 모두 시스템이 잰 값이다(단계 완료, 사람 할 일함, 쇼핑몰의 정상 복귀 신호).
const clock = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit", second: "2-digit" }) : null;

export default function EventTimes({ inc }: { inc: OrchIncident & { timeline?: Times | null } }) {
  const t = inc.timeline ?? null;
  if (!t) return null;
  const seg = segmentsOf(t);
  const rows: { label: string; at?: string | null; span: number | null; note?: string }[] = [
    { label: "감지", at: t.detected_at, span: null },
    { label: "원인 후보 도착(AI)", at: t.cause_candidate_at, span: seg.evidence, note: t.cause_candidate_at ? undefined : "AI가 원인 후보를 내놓지 못함" },
    { label: "사람에게 넘김", at: t.human_work_started_at, span: secondsBetween(t.detected_at, t.human_work_started_at), note: t.human_work_started_at ? undefined : "넘기지 않고 AI가 처리함" },
    { label: "정상 복귀", at: t.recovered_at, span: seg.total, note: t.recovered_at ? undefined : "쇼핑몰이 정상 복귀를 알리면 채워집니다" },
  ];
  return (
    <section className="card space-y-3 p-6">
      <div>
        <h2 className="text-lg font-semibold">이벤트 타임라인</h2>
        <p className="mt-1 text-xs text-ink-500">시각은 모두 시스템이 잰 값입니다. 사람이 적는 값은 없습니다. 괄호는 감지부터 걸린 시간입니다.</p>
      </div>
      <ol className="space-y-1.5 text-sm">
        {rows.map((r) => (
          <li key={r.label} className="grid grid-cols-[10rem_7rem_1fr] items-baseline gap-2">
            <span className="font-medium">{r.label}</span>
            <span className="tabular-nums text-ink-700">{clock(r.at) ?? "–"}</span>
            <span className="text-xs text-ink-500 tabular-nums">{r.at ? (r.span != null ? `(+${durationKo(r.span)})` : "") : r.note}</span>
          </li>
        ))}
      </ol>
      {seg.total != null && (
        <p className="text-sm">
          감지부터 정상 복귀까지 <b className="tabular-nums">{durationKo(seg.total)}</b>
          {seg.ai != null && seg.human != null && (
            <span className="text-ink-500"> · AI 처리 {durationKo(seg.ai)} + 사람에게 넘긴 뒤 복귀까지 {durationKo(seg.human)}</span>
          )}
        </p>
      )}
      {t.prior_case_id && <p className="text-xs text-ink-500">시작할 때 같은 유형의 과거 이벤트를 근거로 봤어요(반복 장애).</p>}
    </section>
  );
}
