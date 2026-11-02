import { useState } from "react";
import { api } from "@/lib/api";
import { won } from "@/lib/format";
import { useOriginal } from "@/lib/useOriginal";
import { CLAIM_REASON_KO, CLAIM_TYPE_KO, OUTCOME_KO } from "@/lib/labels";
import type { ClaimIntake as Intake, ClaimSuggestion, Conversation } from "@/lib/types";

// 상담 콘솔의 클레임 접수. 상담원이 고객 메시지를 보고 환불·교환·취소를 접수한다.
// AI 제안은 접수하지 않고 읽기만 한다(고객 메시지에서 요청을 읽고, 접수했다면 규칙이 낼 판정을 미리 계산).
// 구매자·주문은 서버가 문의에서 읽고, 판정(환불액·승인 여부)은 접수할 때 규칙이 코드로 계산한다.

const TYPES: Intake["type"][] = ["cancel", "return", "exchange"];
const REASONS = Object.keys(CLAIM_REASON_KO) as Intake["reason"][];
const KIND_KO: Record<string, string> = {
  refund: "환불 요청", exchange: "교환 요청", cancel: "취소 요청", event_inquiry: "이벤트·쿠폰 문의", other: "클레임이 아닌 문의",
};

const ABUSE_LABEL: Record<string, string> = { profanity: "비속어 가림", insult: "모욕 표현 가림", threat: "위협 표현 있음" };

/** 접수 창에서 보는 고객 메시지: 욕설·모욕·위협은 가려서 보여 주고 원문은 눌러야 연다 */
function CustomerMessage({ conv }: { conv: Conversation }) {
  const last = [...conv.messages].reverse().find((m) => m.role === "customer");
  const orig = useOriginal(conv.id, last?.id ?? 0);
  if (!last) return null;
  const level = last.abuse_level ?? "none";
  const safe = last.softened_text ?? last.display_text ?? null;
  const masked = level !== "none" && safe != null;
  return (
    <div className="space-y-1 rounded-lg bg-surface-2 p-2" role="note" aria-label="고객 메시지">
      <div className="flex items-center gap-2">
        <b>고객 메시지</b>
        {masked && (
          <>
            <span className={`rounded-full px-2 py-0.5 font-medium ${level === "threat" ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-800"}`}>{ABUSE_LABEL[level]}</span>
            {last.softened_text != null && <span className="text-ink-500">AI가 순화함</span>}
            <button type="button" className="ml-auto text-ink-500 underline" disabled={orig.opening} onClick={orig.toggle}>{orig.show ? "가려서 보기" : orig.opening ? "여는 중…" : "원문 보기"}</button>
            {orig.failed && <span className="text-red-700">원문을 열지 못했어요</span>}
          </>
        )}
      </div>
      <p className="whitespace-pre-wrap text-ink-700">{masked ? (orig.show ? orig.text : safe) : last.text}</p>
    </div>
  );
}

export function ClaimIntake({ conv, onCreated }: { conv: Conversation; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState<Intake["type"]>("return");
  const [reason, setReason] = useState<Intake["reason"]>("defect");
  const [unsealed, setUnsealed] = useState(false);
  const [photos, setPhotos] = useState(0);
  const [memo, setMemo] = useState("");
  const [suggestion, setSuggestion] = useState<ClaimSuggestion | null>(null);
  const [busy, setBusy] = useState<"suggest" | "submit" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function suggest() {
    setBusy("suggest"); setError(null);
    try {
      const s = await api.admin.claimSuggestion(conv.id);
      setSuggestion(s);
      if (s.claim_type) setType(s.claim_type);
      if (s.reason) setReason(s.reason);
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }
  async function submit() {
    setBusy("submit"); setError(null);
    try {
      // AI 제안과 같은 유형일 때만 제안의 일부 품목을 쓴다. 다르면 주문 전체로 접수한다
      const items = suggestion && suggestion.claim_type === type ? suggestion.suggested_items ?? null : null;
      await api.admin.createClaim(conv.id, { type, reason, items, unsealed, evidence_count: photos, description: memo.trim() });
      setOpen(false); setSuggestion(null); setMemo(""); setPhotos(0); setUnsealed(false);
      onCreated();
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  if (!open) return <button type="button" className="btn-ghost w-full py-1.5 text-xs" onClick={() => setOpen(true)}>클레임 접수</button>;
  const p = suggestion?.preview;
  return (
    <div className="space-y-2 rounded-xl border border-brand-100 bg-surface p-3 text-xs" role="group" aria-label="클레임 접수">
      <div className="flex items-center gap-2">
        <b>클레임 접수</b><span className="text-ink-500">문의 #{conv.id} · 주문 {conv.order_no ?? conv.order_id}</span>
        <button type="button" className="btn-quiet ml-auto px-2 py-0.5" onClick={() => { setOpen(false); setError(null); }}>닫기</button>
      </div>
      <CustomerMessage conv={conv} />
      <button type="button" className="btn-ghost w-full py-1" disabled={busy !== null} onClick={suggest}>
        {busy === "suggest" ? "고객 메시지를 읽는 중…" : suggestion ? "AI 제안 다시 받기" : "AI 제안 받기 (접수하지 않고 읽기만)"}
      </button>
      {suggestion && (
        <div className="space-y-1 rounded-lg bg-surface-2 p-2" role="note">
          <p><b>{KIND_KO[suggestion.request_kind] ?? suggestion.request_kind}</b>
            {suggestion.items.length > 0 && <span className="text-ink-500"> · {suggestion.items.join(", ")}</span>}
            {suggestion.source === "rules" && <span className="text-amber-800"> · 단어 규칙으로 읽음(틀릴 수 있음)</span>}
          </p>
          {p ? (
            <>
              <p className="text-ink-700">접수하면 판정: {OUTCOME_KO[p.outcome] ?? p.outcome} · 환불 예정 {won(p.expected_refund)}{p.requires_return && " · 반품 입고 필요"}</p>
              {p.reasons.filter((r) => r.level === "block" || r.level === "review").slice(0, 2).map((r) => <p key={r.code} className="text-ink-700">· {r.message}</p>)}
            </>
          ) : <p className="text-ink-500">접수 입력을 만들지 못했습니다. 아래에서 직접 고르세요.</p>}
          {suggestion.unmatched_items.length > 0 && <p className="text-amber-800">주문에서 찾지 못한 품목: {suggestion.unmatched_items.join(", ")}</p>}
          {suggestion.notes.map((n) => <p key={n} className="text-ink-500">· {n}</p>)}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-0.5"><span className="text-ink-500">유형</span>
          <select className="field py-1 text-xs" value={type} onChange={(e) => setType(e.target.value as Intake["type"])}>
            {TYPES.map((t) => <option key={t} value={t}>{CLAIM_TYPE_KO[t]}</option>)}
          </select></label>
        <label className="space-y-0.5"><span className="text-ink-500">사유</span>
          <select className="field py-1 text-xs" value={reason} onChange={(e) => setReason(e.target.value as Intake["reason"])}>
            {REASONS.map((r) => <option key={r} value={r}>{CLAIM_REASON_KO[r]}</option>)}
          </select></label>
        <label className="space-y-0.5"><span className="text-ink-500">받은 사진(장)</span>
          <input className="field py-1 text-xs" type="number" min={0} max={20} value={photos} onChange={(e) => setPhotos(Math.max(0, Math.min(20, Number(e.target.value) || 0)))} /></label>
        <label className="flex items-end gap-1.5 pb-1.5"><input type="checkbox" checked={unsealed} onChange={(e) => setUnsealed(e.target.checked)} />개봉·사용함</label>
      </div>
      <textarea className="field h-14 resize-none text-xs" placeholder="접수 메모 (선택, 감사 기록에 남습니다)" maxLength={500} value={memo} onChange={(e) => setMemo(e.target.value)} aria-label="접수 메모" />
      {error && <p role="alert" className="rounded-lg bg-red-50 px-2 py-1 font-medium text-red-800">{error}</p>}
      <button type="button" className="btn-primary w-full py-1.5" disabled={busy !== null} onClick={submit}>{busy === "submit" ? "접수 중…" : "이 내용으로 접수"}</button>
      <p className="text-[11px] text-ink-500">환불액과 승인 여부는 접수할 때 규칙이 계산합니다. 접수한 결과는 아래 클레임과 AI 처리 내력에 남습니다.</p>
    </div>
  );
}
