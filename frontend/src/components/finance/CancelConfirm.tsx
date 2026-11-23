import { useState } from "react";
import { getSession } from "@/lib/humanSession";

// 확정 취소(결산·부가세 공용). 잘못한 확정을 되돌린다. 기록은 지우지 않고 누가·왜 취소했는지 남는다(서버).
// 이유는 꼭 적는다(300자 안). 확정·취소는 로그인한 계정으로만 된다(공용 토큰 불가, 11/23).

const MAX_REASON = 300;

export function CancelConfirm({ what, onCancel }: { what: string; onCancel: (reason: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const signedIn = getSession() !== null;

  async function submit() {
    setBusy(true);
    setProblem(null);
    try {
      await onCancel(reason.trim());
      setOpen(false);
      setReason("");
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost" disabled={!signedIn} onClick={() => setOpen(true)}>확정 취소</button>
        {!signedIn && <span className="text-sm text-ink-500">확정 취소는 로그인한 계정으로만 할 수 있어요.</span>}
      </span>
    );
  }
  return (
    <div className="w-full space-y-2 rounded-xl border border-ink-100 px-3 py-2">
      <p className="text-sm text-ink-700">{what} 확정을 취소합니다. 기록은 남고, 다시 확정할 수 있어요. 취소하는 이유를 적어 주세요.</p>
      <div className="flex flex-wrap items-center gap-2">
        <input className="field min-w-0 flex-1" aria-label="확정 취소 이유" placeholder="예: 판매 자료를 바꿔 새로 확정" maxLength={MAX_REASON}
          value={reason} onChange={(e) => setReason(e.target.value)} />
        <button type="button" className="btn-primary" disabled={busy || !reason.trim()} onClick={() => void submit()}>{busy ? "취소하는 중…" : "확정 취소하기"}</button>
        <button type="button" className="btn-quiet" disabled={busy} onClick={() => { setOpen(false); setProblem(null); }}>그만두기</button>
      </div>
      {problem && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{problem}</p>}
    </div>
  );
}
