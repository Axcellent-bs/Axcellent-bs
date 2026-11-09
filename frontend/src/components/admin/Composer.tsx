import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { STYLE_KO } from "@/lib/labels";
import type { Conversation, Draft, Validation } from "@/lib/types";
import { FindingList, ValidationBadge } from "../Findings";

export type ComposerLoad = { nonce: number; draft: Draft };

/** Reply box: every edit is re-checked by the same guard the server runs when sending. */
export function Composer({ conv, load, onSent }: { conv: Conversation; load: ComposerLoad | null; onSent: (c: Conversation) => void }) {
  const [text, setText] = useState("");
  const [validation, setValidation] = useState<Validation | null>(null);
  const [fromDraft, setFromDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const reset = () => { setText(""); setValidation(null); setFromDraft(null); setError(null); };
  useEffect(reset, [conv.id]);
  useEffect(() => {
    if (!load) return;
    setFromDraft(load.draft);
    setText(load.draft.reply);
    setValidation(load.draft.validation);
    setError(null);
  }, [load]);

  // Debounced live check while the agent edits.
  useEffect(() => {
    if (!text.trim()) { setValidation(null); return; }
    if (fromDraft && text === fromDraft.reply) { setValidation(fromDraft.validation); return; }
    const mine = ++seq.current;
    const t = setTimeout(() => api.admin.check(conv.id, text).then((v) => { if (mine === seq.current) setValidation(v); }).catch(() => undefined), 350);
    return () => clearTimeout(t);
  }, [text, conv.id, fromDraft]);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.admin.send(conv.id, { text, draft_style: fromDraft?.style ?? null, draft_text: fromDraft?.reply ?? null });
      reset();
      onSent(updated);
    } catch (e) {
      if (e instanceof ApiError && e.validation) setValidation(e.validation);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const blocked = !!validation && !validation.passed;
  return (
    <div className="shrink-0 border-t border-ink-100 bg-surface px-6 py-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="panel-title">고객에게 보낼 답변{fromDraft && <span className="ml-1 normal-case tracking-normal text-brand-700">· {STYLE_KO[fromDraft.style]} 초안 기반</span>}</span>
        {validation && <ValidationBadge v={validation} />}
      </div>
      <textarea className="field h-28 resize-none" placeholder="오른쪽 초안을 고르거나 직접 작성하세요. 판정과 모순되는 약속은 보낼 수 없습니다." value={text} onChange={(e) => setText(e.target.value)} aria-label="답변 입력" />
      <div className="mt-2 flex items-start gap-3">
        <div className="flex shrink-0 items-center gap-2">
          <button className="btn-primary whitespace-nowrap" disabled={!text.trim() || blocked || busy} onClick={send}>{busy ? "보내는 중…" : "답변 보내기"}</button>
          {text && <button className="btn-quiet whitespace-nowrap text-xs" onClick={reset}>지우기</button>}
        </div>
        <div className="min-w-0 flex-1 space-y-1" aria-live="polite">
          {error && <p className="rounded-md bg-red-50 px-2 py-1 text-xs font-medium text-red-800">{error}</p>}
          {validation && <FindingList v={validation} />}
          {!validation && <p className="pt-1.5 text-xs text-ink-500">약속·금액·개인정보·내부 정보 노출을 보내기 전에 검사합니다.</p>}
        </div>
      </div>
    </div>
  );
}
