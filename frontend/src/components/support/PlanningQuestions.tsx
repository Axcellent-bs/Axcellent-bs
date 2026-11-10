import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { shortDateTime } from "@/lib/format";
import { humanTokenSet } from "@/lib/finance";
import { support, type PlanningQuestion } from "@/lib/support";

// 기획이 되물은 질문 중 고객지원이 기록으로 답하지 못한 것(사람이 답해야 하는 질문)을 모아 보여 주고,
// 상담원이 답하면 고객지원이 안전 관문을 거쳐 기획으로 보낸다. 기획은 답을 기다리느라 멈추지 않고
// 기획서에 "질문 대기"로 표시만 하므로 천천히 답해도 된다. 답에 개인정보를 쓰면 보내지 않는다.

const ABOUT_KO: Record<string, string> = { evidence: "근거", done: "완료 기준" };
const OUTCOME_KO: Record<string, string> = {
  applied: "기획이 답을 받아 기획서에 반영했습니다.",
  duplicate: "기획이 이미 받은 답이라 그대로 닫았습니다.",
  unknown_question: "기획이 이 질문을 찾지 못했습니다. 질문은 열려 있어 다시 보낼 수 있습니다.",
};
const aboutOf = (q: PlanningQuestion) => (q.about ? ABOUT_KO[q.about] ?? q.about : "질문");

export function usePlanningQuestionCount(): number | null {
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    support.planningQuestions().then((env) => { if (alive) setCount(env.data.open_count); }).catch(() => { if (alive) setCount(null); });
    return () => { alive = false; };
  }, []);
  return count;
}

export default function PlanningQuestions({ onCount }: { onCount?: (n: number) => void }) {
  const [items, setItems] = useState<PlanningQuestion[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const env = await support.planningQuestions();
      setItems(env.data.items);
      onCount?.(env.data.open_count);
      setError(null);
    } catch (e) {
      setError(`기획 질문을 받지 못했습니다. ${(e as Error).message}`);
    }
  }, [onCount]);
  useEffect(() => { load(); }, [load]);

  async function send(q: PlanningQuestion) {
    const answer = (drafts[q.question_id] ?? "").trim();
    if (!answer) return;
    setBusy(q.question_id);
    setNotice(null);
    try {
      const done = await api.admin.answerPlanningQuestion({ question_id: q.question_id, answer });
      setNotice(`${q.request_id} ${q.question_id}: ${OUTCOME_KO[done.planning_outcome] ?? done.planning_outcome}`);
      setDrafts((d) => { const next = { ...d }; delete next[q.question_id]; return next; });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const open = items ?? [];
  return (
    <section aria-labelledby="planning-questions-title" className={`rounded-2xl p-4 ring-1 sm:p-5 ${open.length ? "bg-amber-50 ring-amber-100" : "bg-surface ring-ink-100"}`}>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-1">
        <h2 id="planning-questions-title" className="text-base font-semibold text-ink-900">기획이 되물은 질문</h2>
        <p className={`text-sm font-semibold ${open.length ? "text-amber-800" : "text-emerald-800"}`}>
          {items === null ? "불러오는 중" : open.length ? `답을 기다리는 질문 ${open.length}건` : "기다리는 질문 없음"}
        </p>
      </div>
      <p className="mt-0.5 text-xs leading-relaxed text-ink-500">
        기획이 변경 요청의 근거를 되물었는데 고객지원이 보낸 기록으로는 답할 수 없는 질문입니다. 기획은 기다리지 않고 기획서를 이어 쓰며 "질문 대기"로만 표시하니 천천히 답해도 됩니다.
        답에는 전화번호·이메일 같은 개인정보를 쓰지 마세요(쓰면 보내지 않습니다).
      </p>
      {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-3 rounded-xl bg-emerald-50 px-4 py-2 text-xs font-medium text-emerald-800">{notice}</p>}
      {!humanTokenSet && open.length > 0 && <p className="mt-3 text-xs text-amber-800">이 계정에는 결정 권한이 없어 답 보내기 버튼이 꺼져 있습니다. 결정 권한이 있는 담당자 계정으로 들어오면 켜집니다.</p>}

      {open.length > 0 && (
        <ul className="mt-4 space-y-3">
          {open.map((q) => {
            const text = drafts[q.question_id] ?? "";
            return (
              <li key={q.question_id} className="rounded-xl bg-surface p-4 ring-1 ring-ink-100">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <b className="font-mono">{q.request_id}</b>
                  <span className="chip bg-brand-50 text-brand-700">{aboutOf(q)}</span>
                  {q.spec_id && <span className="chip bg-ink-100 text-ink-700">기획서 {q.spec_id}</span>}
                  <span className="ml-auto text-ink-500">받은 때 {shortDateTime(q.received_at)}</span>
                </div>
                <p className="mt-2 text-sm font-medium">{q.question}</p>
                {q.options.length > 0 && (
                  <p className="mt-1 text-xs text-ink-500">기획이 제시한 선택지: {q.options.join(" · ")}</p>
                )}
                <label className="mt-3 block text-xs font-medium text-ink-700" htmlFor={`answer-${q.question_id}`}>답</label>
                <textarea id={`answer-${q.question_id}`} className="field mt-1 w-full text-sm" rows={3} maxLength={1000}
                  value={text} disabled={!humanTokenSet || busy === q.question_id}
                  placeholder="기획이 기획서에 쓸 근거나 결정을 사실 위주로 적어 주세요"
                  onChange={(e) => setDrafts((d) => ({ ...d, [q.question_id]: e.target.value }))} />
                <div className="mt-2 flex items-center gap-3">
                  <button className="btn-primary text-xs" disabled={!humanTokenSet || !text.trim() || busy !== null} onClick={() => send(q)}>
                    {busy === q.question_id ? "보내는 중…" : "기획에 답 보내기"}
                  </button>
                  <span className="text-[11px] text-ink-500">{text.length}/1000자 · 보내면 되돌릴 수 없습니다</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
