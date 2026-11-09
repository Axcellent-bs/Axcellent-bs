import { IconStar } from "./icons";

/** 별점 (후기 관리 화면) */
export function Stars({ value, size = 14 }: { value: number | null; size?: number }) {
  const v = value ?? 0;
  return (
    <span className="inline-flex items-center gap-0.5 text-amber-700" aria-label={value == null ? "평점 없음" : `평점 ${value}점`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={i <= Math.round(v) ? "" : "opacity-25"}><IconStar size={size} fill="currentColor" /></span>
      ))}
    </span>
  );
}
