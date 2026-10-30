import { useState, type ReactNode } from "react";
import { batchOf, finance, humanTokenSet, krw, plainText, signedKrw, type FinanceTodo, type TodoItem } from "@/lib/finance";
import { Chip, TONE } from "./bits";

// "지금 사람이 할 일" 한 줄. 보기만 하지 않고 그 자리에서 사람이 결정한다.
// - 결산 확정: 숫자를 보고 확정 (돈은 움직이지 않음)
// - 판매자 정산: 지급 승인 요청 → 안전 장치 보류 큐에서 승인 (요청과 승인을 한 사람이 바로 끝내지 않는다)
// - 대사 불일치·감사 의심·한도 경보: 건마다 확인 결과(메모)를 남기면 할 일에서 빠진다
// 결정 기록은 재무 AI 서버에 남는다(누가·언제·메모). 예시 데이터 모드에서는 화면 안에서만 바뀐다.

const KIND: Record<FinanceTodo["kind"], { label: string; tone: string }> = {
  settlement_batch: { label: "판매자 정산", tone: TONE.info },
  settlement_deadline: { label: "정산 기한", tone: TONE.bad },
  closing_confirm: { label: "결산", tone: TONE.info },
  recon_exception: { label: "대사", tone: TONE.bad },
  audit_finding: { label: "감사", tone: TONE.warn },
  policy_alert: { label: "한도", tone: TONE.warn },
};

/** 건마다 메모를 남기는 할 일의 버튼 이름·안내 */
const ITEM_ACTION = {
  recon_note: {
    button: "확인 결과 저장",
    placeholder: "예: PG사에 문의함, 다음 정산일에 들어옴",
    hint: "PG사·카드사에서 확인한 내용을 적으면 이 건은 할 일에서 빠집니다. 돈을 옮기지는 않습니다.",
    send: (ref: string, note: string) => finance.reconNote(ref, note),
  },
  anomaly_resolve: {
    button: "처리 완료",
    placeholder: "예: 고객에게 확인, 정상 환불",
    hint: "조사한 결론을 적고 처리 완료를 누르면 목록에서 빠집니다. 돈을 돌려받거나 고객을 막지는 않습니다.",
    send: (ref: string, note: string) => finance.resolveAnomaly(ref, note),
  },
  policy_ack: {
    button: "확인함",
    placeholder: "예: 이번 달 캠페인 때문, 다음 달 예산 늘림 요청",
    hint: "확인한 내용을 적으면 이번 달에는 다시 묻지 않습니다(경보가 더 세지면 다시 보입니다). 한도를 바꾸려면 기준표 변경을 요청하세요.",
    send: (ref: string, note: string) => finance.ackPolicy(ref, note),
  },
} as const;
type ItemActionType = keyof typeof ITEM_ACTION;

const NO_TOKEN = "사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 없어 결정 버튼이 꺼져 있습니다. bash scripts/run_web.sh로 화면을 띄우면 켜집니다.";

export function TodoCard({ todo, onDone, onOpen, onGoToQueue }: {
  todo: FinanceTodo;
  onDone: () => void;
  onOpen: () => void;
  onGoToQueue: (reviewId: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const kind = KIND[todo.kind] ?? { label: "할 일", tone: TONE.muted };
  const action = todo.action;

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setProblem(null);
    try { await fn(); onDone(); } catch (e) { setProblem((e as Error).message); } finally { setBusy(false); }
  }

  let decision: ReactNode = null;
  if (action?.type === "closing_confirm" && action.date) {
    const date = action.date;
    decision = (
      <Decision hint="숫자가 맞으면 확정하세요. 이 날 숫자를 보고 기준으로 고정할 뿐, 돈은 움직이지 않습니다.">
        <button type="button" className="btn-ghost" onClick={onOpen}>숫자 보기</button>
        <button type="button" className="btn-primary" disabled={!humanTokenSet || busy}
          onClick={() => run(() => finance.confirmClosing("day", date))}>{busy ? "확정하는 중…" : `${date} 결산 확정`}</button>
      </Decision>
    );
  } else if (action?.type === "settlement_request" && action.period) {
    const period = action.period;
    decision = (
      <Decision hint="보류 사유가 없는 판매자만 묶어 지급 승인을 요청합니다. 요청은 안전 장치 보류 큐로 가고, 거기서 사람이 한 번 더 승인해야 지급이 기록됩니다.">
        <button type="button" className="btn-ghost" onClick={onOpen}>판매자별 내역 보기</button>
        <button type="button" className="btn-primary" disabled={!humanTokenSet || busy}
          onClick={() => run(async () => { const res = await finance.requestSettlementBatch(period); onGoToQueue(batchOf(res.data).review_id); })}>
          {busy ? "요청하는 중…" : "지급 승인 요청"}
        </button>
      </Decision>
    );
  } else if (action?.type === "review_decision") {
    const reviewId = action.review_id ?? null;
    decision = (
      <Decision hint="지급 승인 요청이 안전 장치 보류 큐에서 기다립니다. 판매자·기간·금액이 정산 표와 같으면 진행을 누르세요.">
        <button type="button" className="btn-primary" onClick={() => onGoToQueue(reviewId)}>보류 큐에서 승인하기</button>
      </Decision>
    );
  } else if (action?.type === "open_tab") {
    decision = (
      <div className="mt-3 space-y-2">
        <p className="text-xs text-ink-500">판매 대금 지급 기한이 가까웠거나 지났습니다. 판매자 정산 탭에서 보류 사유를 풀거나 이번 지급 묶음에 넣으세요. 지급은 지금처럼 보류 큐 승인으로만 합니다.</p>
        <ul className="space-y-1 text-sm">
          {(todo.items ?? []).map((item) => (
            <li key={item.ref} className="flex flex-wrap items-baseline gap-x-2 rounded-xl border border-ink-100 px-3 py-2">
              <b className="text-ink-900">{plainText(item.title)}</b>
              <span className="text-xs text-ink-500">{plainText(item.detail)}</span>
              {item.amount != null && <span className="ml-auto text-xs tabular-nums text-ink-700">{krw(item.amount)}</span>}
            </li>
          ))}
        </ul>
        <button type="button" className="btn-primary" onClick={onOpen}>판매자 정산에서 처리하기</button>
      </div>
    );
  } else if (action && action.type in ITEM_ACTION) {
    const spec = ITEM_ACTION[action.type as ItemActionType];
    decision = (
      <div className="mt-3 space-y-2">
        <p className="text-xs text-ink-500">{spec.hint}</p>
        <ul className="space-y-2">
          {(todo.items ?? []).map((item) => (
            <ItemRow key={item.ref} item={item} button={spec.button} placeholder={spec.placeholder}
              onSend={(note) => run(() => spec.send(item.ref, note))} busy={busy} />
          ))}
        </ul>
        {(todo.count ?? 0) > (todo.items?.length ?? 0) && (
          <button type="button" className="btn-quiet text-xs" onClick={onOpen}>나머지 {(todo.count ?? 0) - (todo.items?.length ?? 0)}건은 탭에서 보기 →</button>
        )}
      </div>
    );
  }

  return (
    <li className="rounded-2xl bg-surface px-4 py-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Chip label={kind.label} tone={kind.tone} />
        <span className="min-w-0 flex-1 text-sm font-medium text-ink-900">{plainText(todo.title)}</span>
        {todo.amount != null && <span className="text-sm tabular-nums text-ink-700">{todo.kind === "closing_confirm" ? `영업 결과 ${signedKrw(todo.amount)}` : krw(todo.amount)}</span>}
        <button type="button" className="text-xs font-medium text-brand-700 hover:underline" onClick={onOpen}>탭에서 보기 →</button>
      </div>
      {decision}
      {decision && action?.type !== "open_tab" && !humanTokenSet && <p className="mt-2 text-xs text-amber-800">{NO_TOKEN}</p>}
      {problem && <p role="alert" className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{problem}</p>}
    </li>
  );
}

function Decision({ hint, children }: { hint: string; children: ReactNode }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <p className="w-full text-xs text-ink-500">{hint}</p>
      {children}
    </div>
  );
}

function ItemRow({ item, button, placeholder, onSend, busy }: {
  item: TodoItem; button: string; placeholder: string; onSend: (note: string) => void; busy: boolean;
}) {
  const [note, setNote] = useState("");
  return (
    <li className="rounded-xl border border-ink-100 px-3 py-2 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <b className="text-ink-900">{plainText(item.title)}</b>
        <span className="text-xs text-ink-500">{plainText(item.detail)}</span>
        {item.amount != null && <span className="ml-auto text-xs tabular-nums text-ink-700">{krw(item.amount)}</span>}
      </div>
      <form className="mt-2 flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (note.trim()) onSend(note.trim()); }}>
        <label className="min-w-0 flex-1">
          <span className="sr-only">{button} 메모</span>
          <input className="field py-1" maxLength={500} placeholder={placeholder} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <button type="submit" className="btn-primary" disabled={!humanTokenSet || busy || !note.trim()}>{button}</button>
      </form>
    </li>
  );
}
