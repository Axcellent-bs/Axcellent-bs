import { useState } from "react";
import { api } from "@/lib/api";
import { dateStr } from "@/lib/format";
import type { Conversation } from "@/lib/types";

// 상담 콘솔의 해결 메모: 해결로 닫은 문의에 상담원이 원인·조치·결과를 남긴다. 최신 순으로 쌓인다.

const FIELDS = [
  { key: "root_cause", label: "실제 원인" },
  { key: "action_taken", label: "한 조치" },
  { key: "result", label: "결과" },
] as const;

type Key = (typeof FIELDS)[number]["key"];
const EMPTY: Record<Key, string> = { root_cause: "", action_taken: "", result: "" };

export function ResolutionNotesCard({ conv, onSaved }: { conv: Conversation; onSaved: (c: Conversation) => void }) {
  const [form, setForm] = useState<Record<Key, string>>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notes = conv.resolution_notes ?? [];
  const empty = FIELDS.every((f) => !form[f.key].trim());

  async function save() {
    if (empty) return;
    setBusy(true); setError(null);
    try {
      onSaved(await api.admin.addResolutionNote(conv.id, { root_cause: form.root_cause.trim(), action_taken: form.action_taken.trim(), result: form.result.trim() }));
      setForm(EMPTY);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <section aria-label="해결 메모">
      <h3 className="panel-title mb-2">해결 메모</h3>
      {conv.status !== "resolved" ? (
        <p className="rounded-xl bg-surface px-3 py-2 text-xs text-ink-500">문의를 해결로 닫으면 원인·조치·결과를 남길 수 있습니다.</p>
      ) : (
        <div className="space-y-2 rounded-xl border border-dashed border-ink-100 bg-surface p-3 text-xs">
          {FIELDS.map((f) => (
            <label key={f.key} className="block space-y-1">
              <span className="text-ink-500">{f.label}</span>
              <textarea className="field py-1 text-xs" rows={2} maxLength={1000} value={form[f.key]} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
            </label>
          ))}
          {error && <p role="alert" className="rounded-lg bg-red-50 px-2 py-1 font-medium text-red-800">{error}</p>}
          <button type="button" className="btn-ghost w-full py-1.5" disabled={busy || empty} onClick={save}>{busy ? "저장하는 중…" : "해결 메모 저장"}</button>
        </div>
      )}
      {notes.length ? (
        <ul className="mt-2 space-y-2">
          {notes.map((n) => (
            <li key={n.id} className="space-y-1.5 rounded-xl border border-ink-100 bg-surface p-3 text-xs">
              <div className="flex items-center gap-2 text-ink-500"><span className="chip bg-ink-100 text-ink-700">{n.author}</span><span className="ml-auto">{dateStr(n.created_at)}</span></div>
              {FIELDS.map((f) => n[f.key] ? (
                <p key={f.key} className="whitespace-pre-wrap leading-relaxed text-ink-700"><b>{f.label}</b> {n[f.key]}</p>
              ) : null)}
            </li>
          ))}
        </ul>
      ) : <p className="mt-2 text-xs text-ink-500">아직 남긴 해결 메모가 없습니다.</p>}
    </section>
  );
}
