import { CATEGORY_KO } from "@/lib/labels";
import type { Summary } from "@/lib/types";

export function SummaryCard({ s, onClose }: { s: Summary; onClose: () => void }) {
  return (
    <div className="mx-6 mt-4 rounded-xl border border-brand-100 bg-brand-50 p-4 text-sm">
      <div className="mb-2 flex items-center gap-2">
        <span className="panel-title text-brand-700">상담 기록</span>
        <span className="chip bg-surface text-brand-700">{CATEGORY_KO[s.category] ?? s.category}</span>
        <span className={`chip ${s.resolved ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>{s.resolved ? "해결" : "미해결"}</span>
        {s.escalate && <span className="chip bg-red-100 text-red-800">이관 필요</span>}
        <button className="btn-quiet ml-auto px-2 py-0.5 text-xs" onClick={onClose}>접기</button>
      </div>
      <p className="leading-relaxed">{s.summary_ko}</p>
      <dl className="mt-3 grid grid-cols-[88px_1fr] gap-x-3 gap-y-1.5 text-xs">
        <dt className="text-ink-500">문의 내용</dt><dd>{s.issue_ko}</dd>
        <dt className="text-ink-500">조치</dt><dd><ul className="list-disc pl-4">{s.actions_ko.map((a, i) => <li key={i}>{a}</li>)}</ul></dd>
        <dt className="text-ink-500">후속 조치</dt><dd>{s.next_action_ko}</dd>
        {s.escalate && <><dt className="text-ink-500">이관 사유</dt><dd>{s.escalate_reason_ko}</dd></>}
        <dt className="text-ink-500">태그</dt><dd className="flex flex-wrap gap-1">{s.tags.map((t) => <span key={t} className="chip bg-surface text-ink-700">#{t}</span>)}</dd>
      </dl>
    </div>
  );
}
