import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { PreparingChip } from "@/components/Preparing";
import { DEV_API, chatApi } from "@/lib/planning";
import type { ChatMessage, ChatStatus, ChatThread } from "@/lib/planningData";
import { useSession, type Account } from "@/demo/session";

// AI 대화방의 직접 대화. 사람이 부서 AI 하나를 골라 바로 말을 건다.
// 지금 사람 말을 바로 받는 창구는 개발 AI(dev 서비스 /dev-chat)뿐이다. 나머지 AI는 대화 스킬이 생길 때까지 "준비 중"으로 둔다.
//
// 열람 권한
// - 본인: 자기 대화 원문 전부
// - 그 AI 담당(담당 부서 회원·관리자): 다른 사람 대화는 요약(제목·메시지 수·시각·결과)만. 일이 생긴 대화(작업 시작)는 원문까지
// - 그 밖의 사람: 다른 사람 대화를 볼 수 없다
// 데모 한계: 백엔드는 브라우저마다 같은 사람 토큰(공용)으로 대화를 받아 주인을 구분하지 못한다.
// 그래서 대화 주인은 이 브라우저가 기억한다(OWNER_KEY). 실제 열람 제한은 백엔드가 대화마다 주인을 남긴 뒤에 서버에서 건다.

export const DIRECT_PREFIX = "direct:";
export interface DirectAgent { key: string; name: string; note: string; live: boolean; /** 이 AI를 맡은 부서. 그 부서 회원이 다른 사람 대화 요약을 본다 */ dept?: string }
export const DIRECT_AGENTS: DirectAgent[] = [
  { key: "dev", name: "개발 AI", note: "고칠 것을 말하면 되묻고 계획을 세워요. 계획이 서면 코딩 작업(PR)을 시작해요.", live: true, dept: "개발팀" },
  { key: "orchestrator", name: "총괄 AI", note: "", live: false },
  { key: "planning", name: "기획 AI", note: "", live: false },
  { key: "support", name: "고객지원 AI", note: "", live: false },
  { key: "finance", name: "재무 AI", note: "", live: false },
  { key: "data", name: "데이터분석 AI", note: "", live: false },
  { key: "hr", name: "인사 AI", note: "", live: false },
  { key: "marketing", name: "마케팅 AI", note: "", live: false },
];
export const directAgentOf = (room: string) =>
  room.startsWith(DIRECT_PREFIX) ? DIRECT_AGENTS.find((a) => DIRECT_PREFIX + a.key === room) : undefined;

/** 이 계정이 그 AI 담당인가: 관리자이거나 그 AI를 맡은 부서 회원 */
const isKeeper = (account: Account, agent: DirectAgent) => !!account.admin || (!!agent.dept && account.dept === agent.dept);

const api = chatApi(DEV_API);

// 대화 주인 (대화 id → 계정 id). 데모라 이 브라우저에만 저장한다
const OWNER_KEY = "axcellent-direct-chat-owner";
const loadOwners = (): Record<string, string> => {
  try {
    const v = JSON.parse(localStorage.getItem(OWNER_KEY) ?? "{}");
    return v && typeof v === "object" ? (v as Record<string, string>) : {};
  } catch { return {}; }
};
const saveOwner = (threadId: string, accountId: string) => {
  try { localStorage.setItem(OWNER_KEY, JSON.stringify({ ...loadOwners(), [threadId]: accountId })); } catch { /* 저장이 막히면 새로고침 때 내 대화 목록에서 빠진다 */ }
};
// 이 계정이 이 탭에서 이어 가던 대화
const threadKey = (accountId: string) => `axcellent-direct-chat-dev:${accountId}`;
const loadThreadId = (accountId: string) => { try { return sessionStorage.getItem(threadKey(accountId)); } catch { return null; } };
const saveThreadId = (accountId: string, id: string | null) => {
  try { if (id) sessionStorage.setItem(threadKey(accountId), id); else sessionStorage.removeItem(threadKey(accountId)); } catch { /* 저장이 막히면 새로고침 때 새 대화 */ }
};

const hhmm = (iso: string) => (iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" }) : "");
const when = (iso: string) => (iso ? new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
const errText = (e: unknown) => (e instanceof Error ? e.message : "개발 AI에 연결할 수 없어요");
const RESULT: Record<ChatStatus, string> = { talking: "대화만 함", started: "코딩 작업 시작", blocked: "막힘", waiting: "앞 요청 대기" };
/** 일이 생긴 대화: 담당자가 원문까지 본다 */
const madeWork = (t: ChatThread) => t.status === "started" || !!t.taskId;

export function DirectChat({ agent }: { agent: DirectAgent }) {
  const { account } = useSession();
  return (
    <section aria-label={`${agent.name}와 직접 대화`} className="card flex min-h-[420px] min-w-0 flex-1 flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-2 border-b border-ink-100 px-4 py-3 lg:px-6">
        <h2 className="card-title">{agent.name}와 직접 대화</h2>
        {!agent.live && <PreparingChip />}
      </header>
      {agent.live && account ? <DevTalk key={account.id} agent={agent} account={account} /> : (
        <div className="mx-auto max-w-md py-16 text-center text-sm text-ink-500">
          {agent.name}는 아직 사람 말을 바로 받는 창구가 없어요. 부서 AI에 대화 스킬이 생기면 여기서 바로 말을 걸 수 있어요.
        </div>
      )}
    </section>
  );
}

function DevTalk({ agent, account }: { agent: DirectAgent; account: Account }) {
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [all, setAll] = useState<ChatThread[]>([]);
  const [owners, setOwners] = useState(loadOwners);
  const box = useRef<HTMLDivElement>(null);
  const keeper = isKeeper(account, agent);

  const mine = all.filter((t) => owners[t.id] === account.id);
  const others = all.filter((t) => owners[t.id] !== account.id);
  // 지금 연 대화가 남의 대화면 보기 전용 (담당자가 일이 생긴 대화 원문을 연 경우)
  const readOnly = !!thread && owners[thread.id] !== account.id;

  useEffect(() => {
    const id = loadThreadId(account.id);
    if (!id || loadOwners()[id] !== account.id) return;
    api.get(id).then(setThread).catch(() => saveThreadId(account.id, null));
  }, [account.id]);
  useEffect(() => {
    api.list().then(setAll).catch(() => setAll([]));
  }, [thread?.id]);
  useLayoutEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread, sending]);

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const message = text.trim();
    if (!message || sending || readOnly) return;
    setSending(message);
    setText("");
    setError(null);
    try {
      const next = await api.send({ ...(thread ? { thread_id: thread.id } : {}), message });
      if (!thread) { saveOwner(next.id, account.id); setOwners(loadOwners()); }
      setThread(next);
      saveThreadId(account.id, next.id);
    } catch (err) {
      setError(errText(err));
      setText(message);
    } finally {
      setSending(null);
    }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
  };
  const reset = () => { setThread(null); saveThreadId(account.id, null); setError(null); };
  const restart = () => {
    if (readOnly) return reset();
    if (!window.confirm("새 대화를 시작할까요? 지금 대화는 '내 지난 대화'에서 다시 열 수 있어요.")) return;
    reset();
  };
  const open = (t: ChatThread) => {
    setError(null);
    api.get(t.id).then((x) => { setThread(x); if (owners[x.id] === account.id) saveThreadId(account.id, x.id); }).catch((err) => setError(errText(err)));
  };
  const messages = thread?.messages ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <aside aria-label="대화 목록" className="scroll-thin shrink-0 space-y-4 overflow-y-auto border-b border-ink-100 px-4 py-4 text-sm lg:w-64 lg:border-b-0 lg:border-r">
        <div>
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-ink-700">내 지난 대화</h3>
            <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={restart} disabled={!!sending || !thread}>새 대화</button>
          </div>
          {mine.length === 0 ? <p className="mt-2 text-xs text-ink-500">아직 없어요.</p> : (
            <ul className="mt-2 space-y-1">
              {mine.map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => open(t)} disabled={!!sending}
                    className={`w-full rounded-lg px-2 py-1.5 text-left text-xs ${thread?.id === t.id ? "bg-brand-50 font-semibold text-brand-700" : "hover:bg-ink-50"}`}>
                    <span className="block truncate">{t.title || "제목 없는 대화"}</span>
                    <span className="text-[11px] text-ink-500">{when(t.updatedAt)} · {RESULT[t.status]}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3 className="text-xs font-semibold text-ink-700">다른 사람 대화</h3>
          {!keeper ? (
            <p className="mt-2 text-xs text-ink-500">{agent.name} 담당({agent.dept ?? "담당 부서"}·관리자)만 요약을 볼 수 있어요.</p>
          ) : others.length === 0 ? <p className="mt-2 text-xs text-ink-500">아직 없어요.</p> : (
            <>
              <p className="mt-1 text-[11px] text-ink-500">담당이라 요약만 보여요. 코딩 작업이 생긴 대화는 원문을 열 수 있어요.</p>
              <ul className="mt-2 space-y-1.5">
                {others.map((t) => (
                  <li key={t.id} className={`rounded-lg px-2 py-1.5 text-xs ${thread?.id === t.id ? "bg-brand-50" : "bg-ink-50"}`}>
                    <span className="block truncate font-medium">{t.title || "제목 없는 대화"}</span>
                    <span className="text-[11px] text-ink-500">{when(t.createdAt)} · 메시지 {t.messages.length}개 · {RESULT[t.status]}</span>
                    {madeWork(t) && (
                      <button type="button" className="mt-1 block text-[11px] font-medium text-brand-700 hover:underline" onClick={() => open(t)} disabled={!!sending}>원문 보기</button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div ref={box} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6">
          {readOnly
            ? <p className="mb-4 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">다른 사람의 대화예요. 코딩 작업이 생긴 대화라 담당자에게 원문을 보여 줘요. 이어서 말할 수는 없어요.</p>
            : <p className="mb-4 rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-500">{agent.note}</p>}
          {messages.length === 0 && !sending ? (
            <p className="py-12 text-center text-sm text-ink-500">{agent.name}에게 하고 싶은 말을 적어 보세요.</p>
          ) : (
            <ol role="log" aria-live="polite" aria-label={`${agent.name}와 나눈 대화`} className="flex flex-col gap-3">
              {messages.map((m) => <Bubble key={m.id} m={m} name={agent.name} me={!readOnly} />)}
              {sending && (
                <>
                  <Bubble m={{ id: "sending", role: "user", text: sending, at: "", kind: "text" }} name={agent.name} me />
                  <li className="animate-pulse px-1 text-xs text-ink-500">{agent.name}가 답하는 중… (5~30초)</li>
                </>
              )}
            </ol>
          )}
        </div>
        {error && <p role="alert" className="mx-4 mb-2 rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-800 lg:mx-6">{error}</p>}
        {readOnly ? (
          <div className="border-t border-ink-100 px-4 py-3 text-right lg:px-6">
            <button type="button" className="btn-ghost" onClick={reset}>내 대화로 돌아가기</button>
          </div>
        ) : (
          <form onSubmit={send} className="flex items-end gap-2 border-t border-ink-100 px-4 py-3 lg:px-6">
            <textarea className="field min-h-[44px] flex-1 resize-none" rows={2} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
              placeholder={`${agent.name}에게 보낼 말 (Enter 보내기, Shift+Enter 줄바꿈)`} aria-label={`${agent.name}에게 보낼 말`} disabled={!!sending} />
            <button className="btn-primary" disabled={!text.trim() || !!sending}>보내기</button>
          </form>
        )}
      </div>
    </div>
  );
}

function Bubble({ m, name, me }: { m: ChatMessage; name: string; me: boolean }) {
  const mine = m.role === "user";
  return (
    <li className={`flex flex-col ${mine ? "items-end" : "items-start"}`}>
      <span className="mb-1 px-1 text-[11px] text-ink-500">{mine ? (me ? "나" : "말한 사람") : name}{m.at ? ` · ${hhmm(m.at)}` : ""}</span>
      <div className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${mine ? "bg-brand-600 text-white" : m.kind === "error" ? "bg-red-50 text-red-800" : "bg-ink-100 text-ink-900"}`}>
        {m.text}
        {m.plan && (
          <div className="mt-2 rounded-xl bg-surface-2 p-3 text-ink-900">
            <p className="font-semibold">계획: {m.plan.title}</p>
            {m.plan.done.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-xs text-ink-700">{m.plan.done.map((d) => <li key={d}>{d}</li>)}</ul>
            )}
          </div>
        )}
        {m.kind === "started" && <p className="mt-1 text-xs opacity-80">코딩 작업을 시작했어요. 개발 AI 작업 현황에서 볼 수 있어요.</p>}
      </div>
    </li>
  );
}
