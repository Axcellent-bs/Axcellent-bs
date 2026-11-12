import { useState } from "react";
import type { Review } from "@/lib/finance";

// 보류 결정 화면은 모두가 같이 쓰는 공용 사람 토큰으로 보낸다. 토큰만으로는 누가 눌렀는지 알 수 없어
// 결정할 때 승인자 이름을 받아 safety에 `decided_as`로 같이 보낸다. 마지막 이름은 이 브라우저에 기억한다.

const KEY = "ax.safety.approver";
export const APPROVER_MAX = 40;

function load(): string {
  try { return localStorage.getItem(KEY) ?? ""; } catch { return ""; }
}

function save(name: string): void {
  try { localStorage.setItem(KEY, name); } catch { /* 저장이 막히면 다음에 다시 적는다 */ }
}

/** [입력값, 바꾸기, 보낼 이름(앞뒤 공백 뺀 값, 없으면 null)] */
export function useApproverName(): [string, (v: string) => void, string | null] {
  const [name, setName] = useState(load);
  const set = (v: string) => { const next = v.slice(0, APPROVER_MAX); setName(next); save(next.trim()); };
  const trimmed = name.trim();
  return [name, set, trimmed ? trimmed : null];
}

export function ApproverField({ value, onChange, id, small = false }: { value: string; onChange: (v: string) => void; id: string; small?: boolean }) {
  const missing = !value.trim();
  return (
    <div className={`flex flex-wrap items-center gap-2 ${small ? "text-xs" : "text-sm"}`}>
      <label htmlFor={id} className="font-medium text-ink-700">승인자 이름</label>
      <input
        id={id}
        className={`field w-40 ${small ? "py-1 text-xs" : "py-1.5"}`}
        value={value}
        maxLength={APPROVER_MAX}
        placeholder="예: 병환"
        autoComplete="name"
        onChange={(e) => onChange(e.target.value)}
      />
      <span className={missing ? "text-amber-800" : "text-ink-500"}>
        {missing ? "이름을 적어야 결정 버튼이 켜집니다." : "공용 토큰으로 보내므로 결정 기록에 이 이름을 함께 남깁니다."}
      </span>
    </div>
  );
}

/** 결정 기록의 "누가": 공용 토큰이면 적어 낸 이름을 보여 준다 */
export function deciderText(r: Pick<Review, "decided_by" | "decided_as">): string {
  if (r.decided_as) return `결정: ${r.decided_as} (공용 토큰)`;
  const by = r.decided_by;
  if (by === "finance-ai") return "재무 AI";
  if (by === "auto-rule") return "자동 규칙";
  return by ? `${by}님` : "사람";
}
