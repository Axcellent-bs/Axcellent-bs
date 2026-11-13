import { useEffect, useState } from "react";
import { actorText } from "@/lib/actor";
import { PLANNING_ASK_EVENT, finance, humanTokenSet, type PlanningAsk, type PlanningQuestion, type PlanningRequest } from "@/lib/finance";
import { shortDateTime } from "@/lib/format";
import { Empty, Panel, ResourceBody, useResource } from "./bits";

// 재무 → 기획 개선 요청: 사람이 정한 요청만 보낸다. 기획이 근거를 되물으면 재무 AI가 보낸 근거로 답하고,
// 기록으로 답하지 못한 질문은 사람이 여기서 답한다. 재무는 요청만 보내고 기획서·우선순위는 기획이 정한다.
// 고칠 점은 어느 탭에서든 생기므로 탭 밖에 늘 펼쳐 둔다. 화면 위 버튼·"이걸로 개선 요청"은 이 섹션으로 내려간다.

/** 진행 중(보낸 요청) 수와 사람이 답할 기획 질문 수. 화면 위 버튼 배지에 쓴다 */
export function usePlanningCounts(tick: number) {
  const requests = useResource("planning-requests", () => finance.planningRequests(), tick);
  const questions = useResource("planning-questions", () => finance.planningQuestions(), tick);
  return {
    open: requests.env?.data.requests.length ?? null,
    waiting: questions.env?.data.waiting ?? null,
  };
}

/** 화면 위에 늘 보이는 "기획에 개선 요청" 버튼. 답할 질문이 있으면 눈에 띄게 한다 */
export function PlanningButton({ tick, onOpen }: { tick: number; onOpen: () => void }) {
  const { open, waiting } = usePlanningCounts(tick);
  const label = [open ? `진행 중 ${open}` : null, waiting ? `답할 질문 ${waiting}` : null].filter(Boolean).join(" · ");
  return (
    <button type="button" onClick={onOpen} className={waiting ? "btn-primary" : "btn-ghost"}>
      기획에 개선 요청{label && <span className="ml-1 tabular-nums">({label})</span>}
    </button>
  );
}

/** 표 옆 "이걸로 개선 요청": 누르면 기획 요청 섹션으로 내려가 제목·근거를 미리 채운다 */
export function PlanningAskLink({ title, evidence, className = "" }: PlanningAsk & { className?: string }) {
  return (
    <button type="button" className={`text-sm font-medium text-brand-700 hover:underline ${className}`}
      onClick={() => window.dispatchEvent(new CustomEvent<PlanningAsk>(PLANNING_ASK_EVENT, { detail: { title, evidence } }))}>
      이걸로 개선 요청
    </button>
  );
}

/** 기획이 되물었는데 재무 AI가 기록으로 답하지 못한 질문. 사람이 답하면 기획에 보낸다 */
export function PlanningQuestionsSection({ tick }: { tick: number }) {
  const res = useResource("planning-questions", () => finance.planningQuestions(), tick);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function answer(q: PlanningQuestion) {
    const text = (answers[q.question_id] ?? "").trim();
    if (!text) return;
    setBusy(q.question_id);
    setMessage(null);
    try {
      await finance.answerPlanningQuestion(q.question_id, text);
      setMessage(`${q.request_id}의 질문에 답을 보냈습니다.`);
      setAnswers((a) => ({ ...a, [q.question_id]: "" }));
      await res.reload();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <ResourceBody res={res}>
      {(d) => {
        const waiting = d.questions.filter((q) => q.status === "waiting");
        const answered = d.questions.filter((q) => q.status !== "waiting");
        if (!waiting.length && !answered.length) return null;
        return (
          <Panel id="planning-questions" title="기획이 되물은 질문"
            hint="기획이 기획서를 쓰다 근거를 되물었는데, 재무 AI가 보낸 기록으로는 답하지 못한 질문입니다. 답하면 기획서 근거로 남습니다.">
            {waiting.length === 0 ? <Empty>답할 질문이 없습니다.</Empty> : (
              <ul className="space-y-3">
                {waiting.map((q) => (
                  <li key={q.question_id} className="space-y-2 rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <code className="font-mono text-ink-500">{q.request_id}</code>
                      <span className="text-ink-500">{shortDateTime(q.asked_at)}</span>
                    </div>
                    <p className="font-medium text-ink-900">{q.question}</p>
                    {q.options.length > 0 && <p className="text-ink-500">고를 답: {q.options.join(" · ")}</p>}
                    <textarea className="field w-full" rows={2} maxLength={1000} placeholder="답 (기획서 근거로 남습니다)"
                      aria-label={`${q.request_id} 질문의 답`}
                      value={answers[q.question_id] ?? ""} disabled={!humanTokenSet || busy !== null}
                      onChange={(e) => setAnswers((a) => ({ ...a, [q.question_id]: e.target.value }))} />
                    <button type="button" className="btn-primary" disabled={!humanTokenSet || busy !== null || !(answers[q.question_id] ?? "").trim()}
                      onClick={() => void answer(q)}>
                      {busy === q.question_id ? "보내는 중…" : "기획에 답 보내기"}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {message && <p className="mt-2 text-sm text-ink-600">{message}</p>}
            {answered.length > 0 && (
              <details className="mt-3 text-sm">
                <summary className="cursor-pointer text-ink-500">답한 질문 {answered.length}건</summary>
                <ul className="mt-2 divide-y divide-ink-100">
                  {answered.map((q) => (
                    <li key={q.question_id} className="py-2">
                      <p className="text-ink-900"><code className="font-mono text-ink-500">{q.request_id}</code> {q.question}</p>
                      <p className="text-ink-500">답: {q.answer} · {actorText(q.answered_by_label, q.answered_by ?? "")}{q.answered_at ? ` · ${shortDateTime(q.answered_at)}` : ""}</p>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Panel>
        );
      }}
    </ResourceBody>
  );
}

export function PlanningRequestsSection({ tick, prefill = null }: { tick: number; prefill?: (PlanningAsk & { n: number }) | null }) {
  const res = useResource("planning-requests", () => finance.planningRequests(), tick);
  const [title, setTitle] = useState("");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // 요청 빼기: 한 번 누르면 확인을 묻고, 한 번 더 누르면 기획에서 지운다 (브라우저 확인 창을 쓰지 않는다)
  const [confirming, setConfirming] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const ready = humanTokenSet && !busy && title.trim().length > 0 && evidence.trim().length > 0;

  // 다른 탭의 "이걸로 개선 요청"에서 열면 제목·근거를 미리 채운다 (n이 바뀌면 같은 내용이어도 다시 채운다)
  useEffect(() => {
    if (!prefill) return;
    if (prefill.title) setTitle(prefill.title.slice(0, 120));
    if (prefill.evidence) setEvidence(prefill.evidence.slice(0, 2000));
    setMessage(null);
  }, [prefill]);

  async function send() {
    setBusy(true);
    setMessage(null);
    try {
      const got = (await finance.sendPlanningRequest(title.trim(), evidence.trim())).data;
      setMessage(got.outcome === "linked_duplicate"
        ? `기획에 같은 요청(${got.duplicate_of})이 있어 그 요청에 이었습니다.`
        : `기획 요청 ${got.request_id}로 보냈습니다.`);
      setTitle("");
      setEvidence("");
      await res.reload();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function withdraw(r: PlanningRequest) {
    setRemoving(r.source_ref);
    setMessage(null);
    try {
      await finance.withdrawPlanningRequest(r.source_ref);
      setMessage(`${r.request_id} 요청을 뺐습니다. 기획 목록에서도 지웠습니다.`);
      setConfirming(null);
      await res.reload();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setRemoving(null);
    }
  }

  return (
    <Panel id="planning-requests" title="기획에 개선 요청"
      hint="재무 업무에서 고칠 점을 기획에 보냅니다. 근거는 한 줄에 하나씩 적어 주세요. 기획이 근거를 되물으면 재무 AI가 이 근거로 답합니다. 잘못 보낸 요청은 '요청 빼기'로 기획에서도 지웁니다(이미 개발로 넘어간 요청은 기획 화면에서 중단해야 해요).">
      <div className="space-y-2">
        <input className="field w-full" placeholder="바꿀 내용 한 줄 (예: PG 정산 파일을 매일 자동으로 받기)" maxLength={120}
          aria-label="바꿀 내용" value={title} onChange={(e) => setTitle(e.target.value)} disabled={!humanTokenSet || busy} />
        <textarea className="field w-full" rows={3} maxLength={2000} placeholder="근거 (예: 지난주 대사 예외 4건이 하루 늦게 잡힘)"
          aria-label="근거" value={evidence} onChange={(e) => setEvidence(e.target.value)} disabled={!humanTokenSet || busy} />
        <div className="flex items-center gap-3">
          <button type="button" className="btn-primary" disabled={!ready} onClick={() => void send()}>
            {busy ? "보내는 중…" : "기획에 보내기"}
          </button>
          {message && <span className="text-sm text-ink-600">{message}</span>}
        </div>
      </div>
      <div className="mt-4">
        <ResourceBody res={res}>
          {(d) => d.requests.length === 0
            ? <Empty>아직 보낸 요청이 없습니다.</Empty>
            : (
              <ul className="divide-y divide-ink-100 text-sm">
                {d.requests.map((r) => (
                  <li key={r.source_ref} className="py-2">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <b className="text-ink-900">{r.title}</b>
                      <div className="flex items-center gap-2">
                        <code className="font-mono text-ink-500">{r.request_id}</code>
                        {confirming === r.source_ref ? (
                          <>
                            <button type="button" className="btn-primary px-2 py-0.5 text-xs" disabled={removing !== null}
                              onClick={() => void withdraw(r)}>
                              {removing === r.source_ref ? "빼는 중…" : "기획에서도 지우기"}
                            </button>
                            <button type="button" className="btn-ghost px-2 py-0.5 text-xs" disabled={removing !== null}
                              onClick={() => setConfirming(null)}>
                              취소
                            </button>
                          </>
                        ) : (
                          <button type="button" className="btn-ghost px-2 py-0.5 text-xs" disabled={!humanTokenSet || removing !== null}
                            onClick={() => setConfirming(r.source_ref)}>
                            요청 빼기
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="text-ink-500">{actorText(r.by_label, r.by)} · {shortDateTime(r.at)} · 근거 {r.facts.length}줄</div>
                  </li>
                ))}
              </ul>
            )}
        </ResourceBody>
      </div>
    </Panel>
  );
}
