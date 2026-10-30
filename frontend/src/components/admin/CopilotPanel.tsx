import { Link } from "@/lib/nav";
import { useState } from "react";
import { CATEGORY_KO, CLAIM_REASON_KO, CLAIM_TYPE_KO, OUTCOME_KO, SENTIMENT_KO, STYLE_KO, URGENCY_KO } from "@/lib/labels";
import { won } from "@/lib/format";
import type { Conversation, Draft, Message, Source } from "@/lib/types";
import { FindingList, ValidationBadge } from "../Findings";
import { StatusChip } from "./bits";
import { ActivityLog } from "./ActivityLog";
import { ClaimIntake } from "./ClaimIntake";
import { FeedbackCard } from "./FeedbackCard";
import { ResolutionNotesCard } from "./ResolutionNotesCard";
import { ReceiptAnalysisCard } from "./ReceiptAnalysisCard";

/** 가장 가까운 근거 정책의 유사도가 이 값 미만이면 "근거 정책 부족"으로 알린다.
 *  기준은 시험에서 본 값(맞는 정책 0.6~0.7, 맞는 정책이 없는 문의 0.1~0.2)에서 정한 임시 값이다. */
const POLICY_LOW_SCORE = 0.3;

/** 이 문의에 맞는 정책이 있는지. 점수는 백엔드가 준 값을 그대로 쓰고 화면이 새로 만들지 않는다. */
function policyGap(sources: Source[] | undefined): { kind: "none" | "weak"; top: number } | null {
  if (!sources || sources.length === 0) return { kind: "none", top: 0 };
  const top = Math.max(...sources.filter((s) => !s.from_decision).map((s) => s.score), 0);
  return top < POLICY_LOW_SCORE && !sources.some((s) => s.from_decision) ? { kind: "weak", top } : null;
}

function PolicyGapNotice({ gap }: { gap: { kind: "none" | "weak"; top: number } }) {
  return (
    <section role="note" className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-900">
      <p className="font-semibold">{gap.kind === "none" ? "근거 정책 없음" : `근거 정책 부족 · 가장 가까운 정책 유사도 ${gap.top.toFixed(1)}`}</p>
      <p className="mt-1">
        이 문의에 맞는 정책을 찾지 못했습니다. 아래 초안은 일정·금액을 단정하지 못하고 일반적인 안내에 그칩니다.
        보내기 전에 정책을 직접 확인하고, 자주 들어오는 문의라면 정책 문서 추가를 요청하세요.
      </p>
    </section>
  );
}

function DraftCard({ draft, onUse, onReject }: { draft: Draft; onUse: () => void; onReject: () => void }) {
  return (
    <div className="rounded-2xl border border-ink-100 bg-surface p-3 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-xs font-semibold">{STYLE_KO[draft.style]}</span>
        <ValidationBadge v={draft.validation} />
        <div className="ml-auto flex flex-wrap justify-end gap-1">{draft.source_ids.map((id) => <span key={id} className="chip bg-ink-100 font-mono text-[10px] text-ink-700">{id}</span>)}</div>
      </div>
      <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-900">{draft.reply}</p>
      <div className="mt-2"><FindingList v={draft.validation} /></div>
      <div className="mt-2 flex gap-1.5">
        <button className="btn-primary px-2.5 py-1 text-xs" disabled={!draft.validation.passed} onClick={onUse}>이 초안 사용</button>
        <button className="btn-quiet ml-auto px-2 py-1 text-xs" onClick={onReject}>제외</button>
      </div>
    </div>
  );
}

function SourceItem({ s }: { s: Source }) {
  return (
    <li className="rounded-lg border border-ink-100 bg-surface p-2.5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-ink-500">{s.id}</span>
        <span className="truncate text-xs font-medium">{s.section}</span>
        {s.from_decision ? <span className="chip ml-auto bg-brand-50 text-brand-700">판정 인용</span> : <span className="ml-auto text-[10px] tabular-nums text-ink-500">{s.score.toFixed(1)}</span>}
      </div>
      <p className="mt-1 text-xs leading-relaxed text-ink-700">{s.text}</p>
    </li>
  );
}

export function CopilotPanel({ conv, onUseDraft, onRejectDraft, onClaimCreated, onFeedback }: { conv: Conversation; onUseDraft: (d: Draft) => void; onRejectDraft: (d: Draft) => void; onClaimCreated: () => void; onFeedback: (c: Conversation) => void }) {
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const last: Message | undefined = [...conv.messages].reverse().find((m) => m.role === "customer" && m.analysis);
  const a = last?.analysis;
  const cls = a?.classification;
  const awaiting = conv.messages.at(-1)?.role === "customer";
  const drafts = (a?.drafts ?? []).filter((d) => !hidden[`${last?.id}-${d.style}`]);
  const gap = a ? policyGap(a.sources) : null;

  return (
    <aside className="scroll-thin flex w-[380px] shrink-0 flex-col gap-4 overflow-y-auto rounded-3xl bg-surface-2 p-4" aria-label="상담 코파일럿">
      {cls && (
        <section>
          <h3 className="panel-title mb-2">문의 분석</h3>
          <div className="rounded-xl border border-ink-100 bg-surface p-3">
            <p className="text-sm font-medium leading-snug">{cls.intent_ko}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              <span className="chip bg-brand-50 text-brand-700">{CATEGORY_KO[cls.category]}</span>
              <span className={`chip ${cls.sentiment === "angry" ? "bg-red-50 text-red-700" : cls.sentiment === "frustrated" ? "bg-amber-50 text-amber-700" : "bg-ink-100 text-ink-700"}`}>감정 {SENTIMENT_KO[cls.sentiment]}</span>
              <span className={`chip ${cls.urgency === "high" ? "bg-red-50 text-red-700" : "bg-ink-100 text-ink-700"}`}>긴급도 {URGENCY_KO[cls.urgency]}</span>
              {cls.pii_masked.length > 0 && <span className="chip bg-violet-50 text-violet-700">개인정보 마스킹: {cls.pii_masked.join(", ")}</span>}
            </div>
            {a?.timings_ms && <p className="mt-2 text-[10px] tabular-nums text-ink-500">분석 {a.timings_ms.analyze}ms · 검색 {a.timings_ms.retrieve}ms · 초안 {a.timings_ms.drafts}ms</p>}
          </div>
        </section>
      )}

      {gap && <PolicyGapNotice gap={gap} />}

      {conv.order_id && (
        <section>
          <h3 className="panel-title mb-2">연결된 주문 · 클레임</h3>
          <div className="space-y-2">
            <p className="rounded-xl border border-ink-100 bg-surface px-3 py-2 text-xs">주문 <b>{conv.order_no ?? conv.order_id}</b> — 답변 초안은 이 영수증과 아래 판정을 근거로 만들어집니다.</p>
            <ReceiptAnalysisCard key={conv.id} conversationId={conv.id} />
            {conv.claims.map((c) => (
              <Link key={c.id} href={`/claims?id=${c.id}`} className="block rounded-xl border border-ink-100 bg-surface p-3 text-xs hover:border-brand-500">
                <div className="flex items-center gap-2"><StatusChip status={c.status} /><b>{CLAIM_TYPE_KO[c.type]} · {CLAIM_REASON_KO[c.reason]}</b><span className="ml-auto text-ink-500">#{c.id}</span></div>
                <p className="mt-1 text-ink-500">판정 {OUTCOME_KO[c.outcome ?? ""]} · 환불 예정 {won(c.expected_refund)}{c.compensation > 0 && ` · 보상 ${won(c.compensation)}`}</p>
                {c.reasons.filter((r) => r.level === "block" || r.level === "review").slice(0, 2).map((r) => <p key={r.code} className="mt-0.5 text-ink-700">· {r.message}</p>)}
              </Link>
            ))}
            {!conv.claims.length && <p className="text-xs text-ink-500">이 주문에 접수된 클레임이 없습니다.</p>}
            <ClaimIntake key={conv.id} conv={conv} onCreated={onClaimCreated} />
          </div>
        </section>
      )}

      <section>
        <h3 className="panel-title mb-2">답변 초안 {awaiting ? "" : "· 답변 완료"}</h3>
        {awaiting ? (
          <div className="space-y-2">
            {drafts.map((d) => <DraftCard key={d.style} draft={d} onUse={() => onUseDraft(d)} onReject={() => { setHidden((h) => ({ ...h, [`${last?.id}-${d.style}`]: true })); onRejectDraft(d); }} />)}
            {!drafts.length && <p className="text-xs text-ink-500">표시할 초안이 없습니다. 직접 작성하세요.</p>}
          </div>
        ) : <p className="rounded-lg bg-surface p-3 text-xs text-ink-500">고객의 새 메시지가 오면 초안이 생성됩니다.</p>}
      </section>

      {a?.sources && a.sources.length > 0 && (
        <section>
          <h3 className="panel-title mb-2">근거 정책 (RAG)</h3>
          <ul className="space-y-2">{a.sources.map((s) => <SourceItem key={s.id} s={s} />)}</ul>
        </section>
      )}

      <FeedbackCard conv={conv} onSaved={onFeedback} />

      <ResolutionNotesCard conv={conv} onSaved={onFeedback} />

      <ActivityLog items={conv.activity ?? []} />
    </aside>
  );
}
