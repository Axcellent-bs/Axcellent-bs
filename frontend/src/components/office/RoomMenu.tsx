import { useEffect, useRef, useState } from "react";

// 방 목록 줄의 ⋯ 메뉴. 줄에 마우스를 올리거나 키보드로 오면 보인다. Esc·바깥 클릭으로 닫는다.

export interface MenuItem {
  key: string;
  label: string;
  sub?: string;
  disabled?: boolean;
  danger?: boolean;
  onSelect?: () => void;
}

export function RoomMenu({ title, items, onOpen, className = "right-2" }: {
  title: string; items: MenuItem[]; onOpen?: () => void; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btn.current?.focus(); } };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div ref={wrap} className={`absolute top-1/2 -translate-y-1/2 ${className}`}>
      <button ref={btn} type="button" aria-label={`${title} 메뉴`} aria-haspopup="menu" aria-expanded={open}
        onClick={() => { if (!open) onOpen?.(); setOpen((v) => !v); }}
        className={`border border-ink-100 bg-surface px-1.5 text-[13px] font-bold leading-5 text-ink-700 transition hover:bg-ink-50 focus:opacity-100 group-hover:opacity-100 ${open ? "opacity-100" : "opacity-0"}`}>
        ⋯
      </button>
      {open && (
        <div role="menu" aria-label={`${title} 메뉴`} className="absolute right-0 top-full z-30 mt-1 w-56 border border-ink-100 bg-surface py-1 shadow-lg">
          {items.map((it) => (
            <button key={it.key} type="button" role="menuitem" disabled={it.disabled}
              onClick={() => { setOpen(false); it.onSelect?.(); }}
              className="block w-full px-3 py-1.5 text-left hover:bg-ink-50 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent">
              <span className={`block text-xs font-medium ${it.danger ? "text-red-700" : "text-ink-900"}`}>{it.label}</span>
              {it.sub && <span className="block text-[11px] text-ink-500">{it.sub}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
