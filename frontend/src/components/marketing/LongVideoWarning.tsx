import { useEffect, useRef, type KeyboardEvent } from "react";
import type { LongWarning } from "@/lib/briefMedia";

// 긴 영상 경고창(화면 안 모달). 화면 안내일 뿐 안전 관문이 아니다: 확인해도 서버가 상한·비용을 따로 막는다.
// 키보드: 처음 포커스는 '길이 줄이기'(안전한 쪽), Tab은 두 버튼 사이에서만 돌고, Esc는 취소다.
// 닫히면 포커스는 창을 연 버튼으로 돌아간다.
export function LongVideoWarning({ warning, onConfirm, onCancel }: { warning: LongWarning; onConfirm: () => void; onCancel: () => void }) {
  const cancel = useRef<HTMLButtonElement>(null);
  const ok = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    cancel.current?.focus();
    return () => { opener?.focus?.(); };
  }, []);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") { e.stopPropagation(); onCancel(); return; }
    if (e.key === "Tab") {
      const a = document.activeElement;
      if (e.shiftKey && a === cancel.current) { e.preventDefault(); ok.current?.focus(); }
      else if (!e.shiftKey && a === ok.current) { e.preventDefault(); cancel.current?.focus(); }
      else if (a !== cancel.current && a !== ok.current) { e.preventDefault(); cancel.current?.focus(); }
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="long-video-title" aria-describedby="long-video-desc" onKeyDown={onKey} className="card w-full max-w-md p-5">
        <h2 id="long-video-title" className="text-base font-semibold">{warning.title}</h2>
        <div id="long-video-desc">
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs leading-relaxed text-ink-900">
            {warning.lines.map((l) => <li key={l}>{l}</li>)}
          </ul>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs leading-relaxed text-amber-800">
            {warning.notes.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button ref={cancel} type="button" className="btn-ghost" onClick={onCancel}>{warning.cancelLabel}</button>
          <button ref={ok} type="button" className="btn-primary" onClick={onConfirm}>{warning.confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
