import { useCallback, useEffect, useState } from "react";
import { Loading, OFFLINE } from "@/components/admin/bits";
import { Stars } from "@/components/Stars";
import { api } from "@/lib/api";
import { dateStr } from "@/lib/format";
import type { AdminReview } from "@/lib/types";

const FLAG_KO: Record<string, string> = {
  PERSONAL_INFO: "개인정보", LINK_OR_CONTACT: "링크·연락 유도", PROFANITY: "비속어", PROMOTION: "광고성", REPEATED_CHARS: "반복 문자", CS_FOLLOW_UP: "선제 연락 대상",
};
const STATUS: Record<string, { label: string; tone: string }> = {
  flagged: { label: "보류 — 확인 필요", tone: "bg-amber-50 text-amber-700" }, visible: { label: "공개", tone: "bg-emerald-50 text-emerald-700" }, hidden: { label: "숨김", tone: "bg-ink-100 text-ink-500" },
};

export default function ReviewsPage() {
  const [rows, setRows] = useState<AdminReview[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api.admin.reviews().then((r) => { setRows(r); setError(null); }).catch(() => setError(OFFLINE)), []);
  useEffect(() => { load(); }, [load]);
  if (!rows) return <Loading error={error} />;
  const pending = rows.filter((r) => r.status === "flagged").length;
  const outreach = rows.filter((r) => r.flags.some((f) => f.code === "CS_FOLLOW_UP"));

  return (
    <div className="space-y-5 px-8 pb-10 pt-2">
      <div>
        <h1 className="text-xl font-semibold">후기 관리</h1>
        <p className="mt-1 text-sm text-ink-500">개인정보·외부 링크·비속어·광고성 문구가 있는 후기는 자동으로 보류됩니다. 낮은 별점과 함께 불량·배송 문제를 말한 후기는 공개한 채 선제 연락 대상으로 표시합니다.</p>
      </div>
      <div className="flex gap-3 text-sm"><span className="chip bg-amber-50 py-1 text-amber-700">확인 대기 {pending}</span><span className="chip bg-red-50 py-1 text-red-700">선제 연락 대상 {outreach.length}</span></div>
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      <ul className="space-y-3">
        {rows.map((r) => (
          <li key={r.id} className="card p-4">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {(() => { const st = STATUS[r.status] ?? { label: r.status, tone: "bg-ink-100 text-ink-500" }; return <span className={`chip ${st.tone}`}>{st.label}</span>; })()}
              <Stars value={r.rating} size={13} /><b>{r.user}</b><span className="text-ink-500">· {r.product} · {dateStr(r.created_at)}</span>
              {r.verified && <span className="chip bg-brand-50 text-brand-700">구매 인증</span>}
              <div className="ml-auto flex gap-1.5">
                {r.status !== "visible" && <button className="btn-primary py-1 text-xs" onClick={() => api.admin.setReview(r.id, "visible").then(load)}>공개</button>}
                {r.status !== "hidden" && <button className="btn-ghost py-1 text-xs" onClick={() => api.admin.setReview(r.id, "hidden").then(load)}>숨김</button>}
              </div>
            </div>
            {r.title && <p className="mt-2 text-sm font-semibold">{r.title}</p>}
            <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink-700">{r.body}</p>
            {r.flags.length > 0 && (
              <ul className="mt-2 space-y-1">{r.flags.map((f, i) => <li key={i} className={`rounded-md px-2 py-1 text-xs ${f.info ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-800"}`}><b className="mr-1">{FLAG_KO[f.code] ?? f.code}</b>{f.message}</li>)}</ul>
            )}
          </li>
        ))}
        {!rows.length && <li className="py-10 text-center text-sm text-ink-500">후기가 없습니다.</li>}
      </ul>
      <p className="text-[11px] text-ink-500">후기 삭제를 조건으로 환불이나 보상을 제안하지 않습니다. 답변 초안 가드가 이런 문장을 차단합니다.</p>
    </div>
  );
}
