import type { CSSProperties } from "react";

const GLYPH: Record<string, string> = { book: "", foreign_book: "EN", ebook: "e-book", stationery: "文", goods: "GOODS" };

/** Generated cover: sample data has no images, so every product gets a title card from its hue. */
export function Cover({ title, hue, category, className = "", compact = false }: {
  title: string; hue: number; category: string; className?: string; compact?: boolean;
}) {
  const square = category === "stationery" || category === "goods";
  const style: CSSProperties = {
    background: `linear-gradient(155deg, hsl(${hue} 46% 34%), hsl(${(hue + 28) % 360} 52% 20%))`,
    aspectRatio: square ? "1 / 1" : "3 / 4",
  };
  const tag = GLYPH[category];
  return (
    <div className={`relative flex overflow-hidden rounded-md text-white shadow-[0_8px_18px_-10px_rgba(0,0,0,0.5)] ${className}`} style={style} role="img" aria-label={`${title} 표지`}>
      {!square && <span className="absolute inset-y-0 left-0 w-[7%] bg-black/25" />}
      <div className={`relative flex w-full flex-col justify-between ${compact ? "p-2" : "p-3.5"} ${square ? "" : "pl-[11%]"}`}>
        <span className={`self-start rounded-full bg-white/20 px-1.5 py-0.5 font-semibold tracking-wider ${compact ? "text-[8px]" : "text-[10px]"} ${tag ? "" : "invisible"}`}>{tag || "·"}</span>
        <p className={`line-clamp-4 break-keep font-bold leading-snug ${compact ? "text-[10px]" : "text-[15px]"}`}>{title}</p>
        <svg viewBox="0 0 24 16" className={`${compact ? "h-3" : "h-5"} self-end opacity-70`} aria-hidden="true">
          <path d="M3 15V3l5 4h8l5-4v12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
        </svg>
      </div>
    </div>
  );
}
