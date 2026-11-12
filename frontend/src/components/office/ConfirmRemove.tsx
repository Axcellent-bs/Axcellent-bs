import { useEffect, useRef, useState, type KeyboardEvent } from "react";

// 지우기 확인 창 (화면 안 모달). 되돌릴 수 없는 일이라 숨기기를 먼저 권한다.
// onConfirm은 실패해도 던지지 않는 함수여야 한다(실패 안내는 부르는 쪽이 알림으로 한다). 끝나면 창이 닫힌다.

export function ConfirmRemove({ title, name, lines, onConfirm, onClose }: {
  title: string; name: string; lines: string[]; onConfirm: () => Promise<void>; onClose: () => void;
}) {
  const [running, setRunning] = useState(false);
  const cancel = useRef<HTMLButtonElement>(null);
  const ok = useRef<HTMLButtonElement>(null);
  useEffect(() => { cancel.current?.focus(); }, []);

  const run = async () => {
    if (running) return;
    setRunning(true);
    try { await onConfirm(); } finally { setRunning(false); onClose(); }
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") { e.stopPropagation(); if (!running) onClose(); return; }
    if (e.key === "Tab") { // 창 밖으로 포커스가 나가지 않게 두 버튼 사이에서만 돈다
      const a = document.activeElement;
      if (e.shiftKey && a === cancel.current) { e.preventDefault(); ok.current?.focus(); }
      else if (!e.shiftKey && a === ok.current) { e.preventDefault(); cancel.current?.focus(); }
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget && !running) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="confirm-remove-title" onKeyDown={onKey} className="card w-full max-w-md p-5">
        <h2 id="confirm-remove-title" className="text-base font-semibold">{title}</h2>
        <p className="mt-1 truncate text-sm font-medium text-ink-900">{name}</p>
        <ul className="mt-3 space-y-1 text-xs leading-relaxed text-ink-700">
          {lines.map((l) => <li key={l}>{l}</li>)}
        </ul>
        <p className="mt-3 text-xs font-semibold text-red-700">되돌릴 수 없습니다. 잠깐 안 보이게만 하려면 숨기기를 쓰세요</p>
        <div className="mt-4 flex justify-end gap-2">
          <button ref={cancel} type="button" className="btn-ghost" onClick={onClose} disabled={running}>취소</button>
          <button ref={ok} type="button" onClick={() => void run()} disabled={running}
            className="bg-red-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700">
            {running ? "삭제 중…" : "삭제"}
          </button>
        </div>
      </div>
    </div>
  );
}
