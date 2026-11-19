import { useState, useSyncExternalStore } from "react";
import type { Review } from "@/lib/finance";
import { getSession, onSessionChange } from "@/lib/humanSession";

// 보류 결정의 "누가".
// - 서버 로그인(사람별 세션 토큰)이면 safety가 토큰에서 계정 이름을 읽어 기록한다. 이름을 따로 받지 않는다.
//   적어 낸 이름은 safety가 버리므로(공용 토큰일 때만 받음) 받을 이유가 없고, 받으면 아무 이름이나 쓸 수 있어 기록이 의미 없어진다.
// - 데모 로그인(공용 사람 토큰)이면 토큰만으로는 누가 눌렀는지 알 수 없어 승인자 이름을 받아 `decided_as`로 같이 보낸다.
//   마지막 이름은 이 브라우저에 기억한다.

const KEY = "ax.safety.approver";
export const APPROVER_MAX = 40;

function load(): string {
  try { return localStorage.getItem(KEY) ?? ""; } catch { return ""; }
}

function save(name: string): void {
  try { localStorage.setItem(KEY, name); } catch { /* 저장이 막히면 다음에 다시 적는다 */ }
}

/** 로그인한 계정 이름. 서버 로그인이 아니거나 세션이 만료됐으면 null */
function useLoginName(): string | null {
  const session = useSyncExternalStore(onSessionChange, getSession, () => null);
  return session?.account.name ?? null;
}

/**
 * [입력값, 바꾸기, 결정 버튼을 켤 이름, 같이 보낼 자기 신고 이름]
 * 서버 로그인이면 로그인 계정 이름으로 버튼을 켜고, 자기 신고 이름은 보내지 않는다(null).
 */
export function useApproverName(): [string, (v: string) => void, string | null, string | null] {
  const loginName = useLoginName();
  const [name, setName] = useState(load);
  const set = (v: string) => { const next = v.slice(0, APPROVER_MAX); setName(next); save(next.trim()); };
  if (loginName) return [loginName, () => {}, loginName, null];
  const trimmed = name.trim() || null;
  return [name, set, trimmed, trimmed];
}

export function ApproverField({ value, onChange, id, small = false }: { value: string; onChange: (v: string) => void; id: string; small?: boolean }) {
  const loginName = useLoginName();
  const size = small ? "text-xs" : "text-sm";
  if (loginName) {
    return (
      <div className={`flex flex-wrap items-center gap-2 ${size}`}>
        <span className="font-medium text-ink-700">승인자</span>
        <span className="chip bg-brand-50 text-brand-700">{loginName}</span>
        <span className="text-ink-500">로그인한 계정으로 결정합니다. 결정 기록에 이 계정 이름이 남습니다.</span>
      </div>
    );
  }
  const missing = !value.trim();
  return (
    <div className={`flex flex-wrap items-center gap-2 ${size}`}>
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
