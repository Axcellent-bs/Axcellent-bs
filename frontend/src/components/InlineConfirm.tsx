// 화면 안 확인 상자 (11/24). 브라우저 confirm() 대신 누른 버튼 바로 아래에 펼친다.
// 모양은 의뢰 삭제 확인(#1644 RequestActions)·1:1 [새 대화] 확인(#1670)과 같다:
// - danger: 빨간 상자 + 빨간 확인 버튼 (되돌릴 수 없는 동작. 문구에 "되돌릴 수 없어요"를 붙인다)
// - 아니면 회색 상자 + 기본 버튼
// 문구는 여러 줄이면 줄을 그대로 보인다. busy면 두 버튼을 끈다.

export interface InlineConfirmProps {
  message: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function InlineConfirm({ message, confirmLabel, danger = false, busy = false, onConfirm, onCancel }: InlineConfirmProps) {
  const box = danger ? "border-red-300 bg-red-50 text-red-800" : "border-ink-100 bg-ink-50 text-ink-700";
  const ok = danger ? "btn-primary bg-red-700 px-2.5 py-1 text-xs hover:bg-red-800" : "btn-primary px-2.5 py-1 text-xs";
  return (
    <div role="alertdialog" aria-label={message.split("\n")[0]} className="mt-1.5 w-full min-w-0 space-y-1.5 text-left">
      <p className={`w-full min-w-0 whitespace-pre-line break-keep rounded-xl border px-3 py-2 text-[12.5px] ${box}`}>{message}</p>
      <span className="flex w-full flex-wrap gap-1.5">
        <button type="button" className={ok} disabled={busy} onClick={onConfirm}>{confirmLabel}</button>
        <button type="button" className="btn-ghost px-2.5 py-1 text-xs" disabled={busy} onClick={onCancel}>취소</button>
      </span>
    </div>
  );
}

/** 한 줄 안내 (alert 대신 그 자리 아래). 성공은 회색, 실패는 빨강 */
export function InlineNote({ text, error = false }: { text: string; error?: boolean }) {
  return <p role={error ? "alert" : "status"} className={`mt-1 text-xs ${error ? "text-red-700" : "text-ink-500"}`}>{text}</p>;
}
