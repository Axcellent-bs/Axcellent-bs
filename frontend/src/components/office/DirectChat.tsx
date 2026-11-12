import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { PreparingChip } from "@/components/Preparing";
import { HandoffLine, PlanCard, PROCEED_TEXT } from "@/components/office/DevCards";
import { DASHBOARD_API_URL, SAFETY_HUMAN_TOKEN } from "@/lib/env";
import { POLL_MS, classifyFailure, directChatApi, isAnswering } from "@/lib/directChat";
import { loadOwners, loadThreadId, saveOwner, saveThreadId, threadKey } from "@/lib/directThreads";
import { OFFICE_ROOM_MAX_HOURS, listOfficeRoom } from "@/lib/officeRoom";
import type { ChatMessage, ChatStatus, ChatThread as BaseThread } from "@/lib/planningData";
import { READER_AREAS } from "@/demo/members";
import { useSession, type Account } from "@/demo/session";
import { humanizePlanIds } from "@/lib/format"

// AI 대화방의 직접 대화. 사람이 부서 AI 하나를 골라 바로 말을 건다.
// 사람 말을 바로 받는 창구: 개발 AI(dashboard-api /api/dashboard/dev-chat), 인사 AI(/api/hr/chat), 기획 AI(dashboard-api /api/dashboard/planning-chat), 마케팅 AI(dashboard-api /api/dashboard/marketing-chat), 재무 AI(dashboard-api /api/dashboard/finance-chat), 총괄 AI(dashboard-api /api/dashboard/orchestrator-chat), 인프라 AI(dashboard-api /api/dashboard/infra-chat). 나머지 AI는 창구가 생길 때까지 "준비 중"으로 둔다.
// 창구는 모두 같은 대화 모양(id·title·status·messages[]·who·시각)을 돌려준다.
//
// 열람 권한
// - 본인: 자기 대화 원문 전부
// - 다른 사람 대화: 모두가 요약(제목·메시지 수·시각·결과)만 본다 (2026-11-09 팀 결정, 소규모 회사 기준)
// - 인사·재무 AI만 예외: 관리자와 회원·서버 관리에서 고른 열람 담당만 요약을 본다 (개인 사정·장부 숫자)
// - 관리자: 일이 생긴 대화(작업 시작)는 원문까지
// 데모 한계: 백엔드는 브라우저마다 같은 사람 토큰(공용)으로 대화를 받아 주인을 구분하지 못한다.
// 그래서 대화 주인은 이 브라우저가 기억한다(OWNER_KEY). 서버가 가린 대화(restricted, 인사 AI)는 서버가 준 요약만 보여 준다.

/** 대화 하나. 서버가 다른 사람 대화를 가리면 messages가 비고 restricted·summary가 온다 */
type ChatThread = BaseThread & { summary?: string; restricted?: boolean; messageCount?: number };
interface Talk {
  list: () => Promise<ChatThread[]>;
  get: (id: string) => Promise<ChatThread>;
  send: (x: { thread_id?: string; message: string }) => Promise<ChatThread>;
  /** 대화 끝내기. 이 창구가 지원할 때만 있다 (끝내면 서버가 한두 줄 요약을 남긴다) */
  close?: (id: string) => Promise<ChatThread>;
}

// 인사 AI 대화 창구(decisions 8114 `/api/hr/chat`). CORS가 없어 기본값은 Vite 개발 프록시(/hr-api → vite.config.ts)
// 인사 데이터를 다루는 대화라 읽기에도 사람 토큰을 붙인다
const HR_API_URL = (import.meta.env.VITE_HR_API_URL as string | undefined) ?? "/hr-api";
const toThread = (x: Record<string, unknown>): ChatThread => ({
  ...(x as unknown as ChatThread),
  taskId: (x.task_id as string | null | undefined) ?? null,
  createdAt: String(x.created_at ?? ""),
  updatedAt: String(x.updated_at ?? ""),
  messageCount: typeof x.message_count === "number" ? x.message_count : undefined,
});
async function hrCall<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${HR_API_URL}/api/hr${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(SAFETY_HUMAN_TOKEN ? { Authorization: `Bearer ${SAFETY_HUMAN_TOKEN}` } : {}) },
  });
  const body = (await res.json().catch(() => null)) as { data?: T; error?: { message?: string } } | null;
  if (!res.ok || !body || body.data === undefined) throw new Error(body?.error?.message ?? `인사 AI에 연결할 수 없어요 (${res.status})`);
  return body.data;
}
const hrTalk: Talk = {
  list: () => hrCall<Record<string, unknown>[]>("/chat").then((d) => (Array.isArray(d) ? d.map(toThread) : [])),
  get: (id) => hrCall<Record<string, unknown>>(`/chat/${encodeURIComponent(id)}`).then(toThread),
  send: (x) => hrCall<Record<string, unknown>>("/chat", { method: "POST", body: JSON.stringify(x) }).then(toThread),
};

export const DIRECT_PREFIX = "direct:";
export interface DirectAgent {
  key: string; name: string; note: string; live: boolean
  talk?: Talk
}
export const DIRECT_AGENTS: DirectAgent[] = [
  { key: "dev", name: "개발 AI", note: "고칠 것을 말하면 되묻고 계획을 세워요. '진행해'라고 하면 기획에 요청으로 넘기고, 기획에서 확정하면 개발이 시작돼요.", live: true, talk: directChatApi({ base: DASHBOARD_API_URL, path: "/api/dashboard/dev-chat" }, { closable: true }) },
  { key: "hr", name: "인사 AI", note: "팀 인원·교대·인수인계 같은 인사 기록을 읽고 설명해요. 대화로 바꾸는 일은 하지 않아요.", live: true, talk: hrTalk },
  { key: "planning", name: "기획 AI", note: "받은 요청·기획서·결정·제안 상태를 읽고 설명해요. 대화로 바꾸는 일은 하지 않아요.", live: true, talk: directChatApi({ base: DASHBOARD_API_URL, path: "/api/dashboard/planning-chat" }, { closable: true }) },
  { key: "orchestrator", name: "총괄 AI", note: "지금 장애가 어디까지 왔는지, 어느 부서에서 오래 걸렸는지, 승인 대기가 있는지를 총괄 기록으로 읽고 설명해요. 대화로 승인하거나 상태를 바꾸지 않아요. 답이 오기까지 몇 초 걸릴 수 있어요.", live: true, talk: directChatApi({ base: DASHBOARD_API_URL, path: "/api/dashboard/orchestrator-chat" }, { closable: true }) },
  { key: "support", name: "고객지원 AI", note: "문의·클레임·후기 현황을 숫자로 읽고 설명해요. 고객 이름과 원문은 보지 않고, 대화로 바꾸는 일은 하지 않아요. 답이 오기까지 몇 초 걸릴 수 있어요.", live: true, talk: directChatApi({ base: DASHBOARD_API_URL, path: "/api/dashboard/support-chat" }, { closable: true }) },
  { key: "finance", name: "재무 AI", note: "손익·결산·판매자 정산 결과(고객지원이 계산)·통장 대조·현금 흐름·캠페인 손익·장애 비용을 장부 숫자로 읽고 설명해요. 대화로 장부를 바꾸거나 비용을 승인하지 않아요. 답이 오기까지 몇 초 걸릴 수 있어요.", live: true, talk: directChatApi({ base: DASHBOARD_API_URL, path: "/api/dashboard/finance-chat" }, { closable: true }) },
  { key: "data", name: "데이터분석 AI", note: "트렌드·인사이트·영상 성과·캠페인 결과·수집 상태를 숫자로 읽고 설명해요. 대화로 바꾸는 일은 하지 않아요. 답이 오기까지 몇 초 걸릴 수 있어요. 개인정보·비밀은 적지 마세요.", live: true, talk: directChatApi({ base: DASHBOARD_API_URL, path: "/api/dashboard/data-chat" }, { closable: true }) },
  { key: "infra", name: "인프라 AI", note: "최근 배포, 결제 서버 상태, 장애 사건의 진단·롤백·복구 확인 결과를 읽고 설명해요. 대화로 롤백하거나 장애를 넣고 빼지 않아요. 답이 오기까지 몇 초 걸릴 수 있어요.", live: true, talk: directChatApi({ base: DASHBOARD_API_URL, path: "/api/dashboard/infra-chat" }, { closable: true }) },
  { key: "marketing", name: "마케팅 AI", note: "마케팅 일을 물어보면 마케팅 AI가 답해요. 답이 오기까지 몇 초 걸릴 수 있어요.", live: true, talk: directChatApi({ base: DASHBOARD_API_URL, path: "/api/dashboard/marketing-chat" }) },
];
export const directAgentOf = (room: string) =>
  room.startsWith(DIRECT_PREFIX) ? DIRECT_AGENTS.find((a) => DIRECT_PREFIX + a.key === room) : undefined;

/** 다른 사람 대화 요약을 보는가: 모두. 인사·재무 AI만 관리자와 열람 담당 (회원 부서는 표시용이라 권한에 쓰지 않는다) */
const canSeeOthers = (agentKey: string, account: Account) => {
  const area = READER_AREAS.find((r) => r.id === agentKey);
  return !area || !!account.admin || !!account.readers?.includes(area.id);
};

const hhmm = (iso: string) => (iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" }) : "");
const when = (iso: string) => (iso ? new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
const errText = (e: unknown, name: string) => (e instanceof Error ? e.message : `${name}에 연결할 수 없어요`);
const RESULT: Record<ChatStatus, string> = { talking: "대화만 함", handed: "기획에 넘김", started: "코딩 작업 시작", blocked: "막힘", waiting: "앞 요청 대기", answering: "답하는 중", closed: "끝난 대화" };
/** 일이 생긴 대화: 관리자가 원문까지 본다 */
const madeWork = (t: ChatThread) => t.status === "started" || !!t.taskId;

/** `narrow`: 좁은 칸(AI 오피스 1:1 탭)에서는 지난 대화 목록을 위에 짧게 두고 대화를 아래에 쌓는다 */
/**
 * `bare`: 겉 카드·제목 없이 대화만 (대화 기록 나눠 보기의 1:1 칸이 칸 머리를 따로 그린다).
 */
export function DirectChat({ agent, narrow = false, bare = false }: { agent: DirectAgent; narrow?: boolean; bare?: boolean }) {
  const { account } = useSession();
  const body = agent.talk && account ? <AgentTalk key={`${agent.key}:${account.id}`} agent={agent} talk={agent.talk} account={account} narrow={narrow} /> : (
    <div className="mx-auto max-w-md py-16 text-center text-sm text-ink-500">
      {agent.name}는 아직 사람 말을 바로 받는 창구가 없어요. 부서 AI에 대화 스킬이 생기면 여기서 바로 말을 걸 수 있어요.
    </div>
  );
  if (bare) return <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-surface">{body}</div>;
  return (
    <section aria-label={`${agent.name}와 직접 대화`} className="card flex min-h-[420px] min-w-0 flex-1 flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-2 border-b border-ink-100 px-4 py-3 lg:px-6">
        <h2 className="card-title">{agent.name}와 직접 대화</h2>
        {!agent.live && <PreparingChip />}
      </header>
      {body}
    </section>
  );
}

function AgentTalk({ agent, talk: api, account, narrow }: { agent: DirectAgent; talk: Talk; account: Account; narrow: boolean }) {
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [all, setAll] = useState<ChatThread[]>([]);
  const [closing, setClosing] = useState(false);
  const [owners, setOwners] = useState(loadOwners);
  const box = useRef<HTMLDivElement>(null);
  const seeOthers = canSeeOthers(agent.key, account);
  const tkey = threadKey(agent.key, account.id);

  // 백엔드가 아직 없거나 LLM 키가 없으면 "준비 중", 403이면 입력을 막고 이유를 보여 준다
  const [prep, setPrep] = useState(false);
  const [locked, setLocked] = useState<string | null>(null);
  const failed = (err: unknown) => {
    const k = classifyFailure(err);
    if (k === "preparing") { setPrep(true); return true; }
    if (k === "forbidden") { setLocked(errText(err, agent.name)); return true; }
    return false;
  };

  // 단톡방(전체 대화방·장애 방)에서 이 부서에 건 대화 번호. 1:1 "내 지난 대화"에서는 뺀다(방이 어디든).
  // 목록을 못 읽으면 지금처럼 다 보여 주고, 그 사실을 오류 줄로 알린다
  const [roomThreads, setRoomThreads] = useState<ReadonlySet<string>>(new Set());
  const [roomError, setRoomError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    listOfficeRoom({ hours: OFFICE_ROOM_MAX_HOURS }).then((rows) => {
      if (!live) return;
      setRoomThreads(new Set(rows.filter((r) => r.agent === agent.key).map((r) => r.threadId)));
      setRoomError(null);
    }).catch((err) => {
      if (live) setRoomError(`단톡방 목록을 읽지 못해 단톡방에서 건 대화가 내 지난 대화에 섞여 보일 수 있어요: ${errText(err, "단톡방 목록")}`);
    });
    return () => { live = false; };
  }, [agent.key, thread?.id]);

  // 서버가 남의 대화(restricted)로 알려 준 것은 원문 없이 요약만. 그 밖에는 이 브라우저가 기억한 주인으로 가른다
  const isMine = (t: ChatThread) => !t.restricted && owners[t.id] === account.id;
  const mine = all.filter((t) => isMine(t) && !roomThreads.has(t.id));
  const others = all.filter((t) => !isMine(t));
  // 지금 연 대화가 남의 대화면 보기 전용 (관리자가 일이 생긴 대화 원문을 연 경우)
  const readOnly = !!thread && !isMine(thread);

  useEffect(() => {
    const id = loadThreadId(tkey);
    if (!id || loadOwners()[id] !== account.id) return;
    api.get(id).then(setThread).catch((err) => { if (!failed(err)) saveThreadId(tkey, null); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, tkey, account.id]);
  useEffect(() => {
    api.list().then(setAll).catch((err) => { failed(err); setAll([]); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, thread?.id]);
  // 답을 기다리는 중(answering)이면 2초마다 상세를 다시 읽고, 답이 오면 멈춘다. 대화를 바꾸거나 화면을 떠나면 타이머를 지운다
  const answeringId = isAnswering(thread) ? thread?.id : undefined;
  useEffect(() => {
    if (!answeringId) return;
    let stop = false;
    const timer = window.setInterval(() => {
      api.get(answeringId).then((x) => { if (!stop) setThread((cur) => (cur?.id === x.id ? x : cur)); }).catch((err) => { if (!stop) { failed(err); setError(errText(err, agent.name)); window.clearInterval(timer); } });
    }, POLL_MS);
    return () => { stop = true; window.clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answeringId, api]);
  useLayoutEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread, sending]);

  const send = async (e?: FormEvent, said?: string) => {
    e?.preventDefault();
    const message = (said ?? text).trim();
    if (!message || sending || readOnly || prep || locked) return;
    setSending(message);
    if (said === undefined) setText("");
    setError(null);
    try {
      const next = await api.send({ ...(thread ? { thread_id: thread.id } : {}), message });
      if (!thread) { saveOwner(next.id, account.id); setOwners(loadOwners()); }
      setThread(next);
      saveThreadId(tkey, next.id);
    } catch (err) {
      if (!failed(err)) setError(errText(err, agent.name));
      if (said === undefined) setText(message);
    } finally {
      setSending(null);
    }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
  };
  const reset = () => { setThread(null); saveThreadId(tkey, null); setError(null); };
  const restart = () => {
    if (readOnly) return reset();
    if (!window.confirm("새 대화를 시작할까요? 지금 대화는 '내 지난 대화'에서 다시 열 수 있어요.")) return;
    reset();
  };
  const finish = async () => {
    if (!thread || !api.close || sending || closing) return;
    if (!window.confirm("이 대화를 끝낼까요? 끝내면 요약이 남고, 이어서 말할 수 없어요.")) return;
    setClosing(true);
    setError(null);
    try {
      setThread(await api.close(thread.id));
      api.list().then(setAll).catch(() => { /* 목록은 다음에 다시 읽는다 */ });
    } catch (err) {
      if (!failed(err)) setError(errText(err, agent.name));
    } finally {
      setClosing(false);
    }
  };
  const open = (t: ChatThread) => {
    setError(null);
    api.get(t.id).then((x) => { setThread(x); if (owners[x.id] === account.id) saveThreadId(tkey, x.id); }).catch((err) => { if (!failed(err)) setError(errText(err, agent.name)); });
  };
  const messages = thread?.messages ?? [];
  // "이대로 진행": 마지막 계획 메시지에만, 이야기 중(talking)인 내 대화일 때만 (개발 화면과 같은 규칙)
  const last = messages[messages.length - 1];
  const goId = !readOnly && thread?.status === "talking" && last?.role === "assistant" && last.kind === "plan" && last.plan ? last.id : null;
  const goBusy = !!sending || isAnswering(thread) || prep || !!locked;

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${narrow ? "" : "lg:flex-row"}`}>
      <aside aria-label="대화 목록" className={`scroll-thin shrink-0 space-y-4 overflow-y-auto border-b border-ink-100 px-4 py-4 text-sm ${narrow ? "max-h-40" : "lg:w-64 lg:border-b-0 lg:border-r"}`}>
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
          {!seeOthers ? (
            <p className="mt-2 text-xs text-ink-500">{agent.name} 대화는 관리자와 열람 담당만 요약을 볼 수 있어요. 열람 담당은 관리자가 회원·서버 관리에서 정해요.</p>
          ) : others.length === 0 ? <p className="mt-2 text-xs text-ink-500">아직 없어요.</p> : (
            <>
              <p className="mt-1 text-[11px] text-ink-500">다른 사람의 대화(요약만 보임).{account.admin ? " 관리자는 코딩 작업이 생긴 대화의 원문을 열 수 있어요." : ""}</p>
              <ul className="mt-2 space-y-1.5">
                {others.map((t) => (
                  <li key={t.id} className={`rounded-lg px-2 py-1.5 text-xs ${thread?.id === t.id ? "bg-brand-50" : "bg-ink-50"}`}>
                    <span className="block truncate font-medium">{t.title || "제목 없는 대화"}</span>
                    <span className="text-[11px] text-ink-500">{when(t.createdAt)} · 메시지 {t.messageCount ?? t.messages.length}개 · {RESULT[t.status]}</span>
                    {t.summary && <span className="mt-0.5 block text-[11px] text-ink-700">{humanizePlanIds(t.summary)}</span>}
                    {account.admin && madeWork(t) && !t.restricted && (
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
          {prep && (
            <p role="status" className="mb-4 flex flex-wrap items-center gap-2 rounded-xl bg-violet-50 px-3 py-2 text-xs text-violet-800">
              <PreparingChip /> {agent.name}는 아직 준비 중이에요. 서버가 연결되고 LLM 키가 들어가면 여기서 말을 걸 수 있어요.
            </p>
          )}
          {thread?.restricted && (
            <p className="mb-4 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">다른 사람의 대화(요약만 보임){thread.summary ? `: ${humanizePlanIds(thread.summary)}` : ""}</p>
          )}
          {readOnly
            ? <p className="mb-4 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">다른 사람의 대화예요. 코딩 작업이 생긴 대화라 관리자에게 원문을 보여 줘요. 이어서 말할 수는 없어요.</p>
            : <p className="mb-4 rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-500">{agent.note}</p>}
          {messages.length === 0 && !sending ? (
            <p className="py-12 text-center text-sm text-ink-500">{agent.name}에게 하고 싶은 말을 적어 보세요.</p>
          ) : (
            <ol role="log" aria-live="polite" aria-label={`${agent.name}와 나눈 대화`} className="flex flex-col gap-3">
              {messages.map((m) => <Bubble key={m.id} m={m} name={agent.name} me={!readOnly} requestId={thread?.requestId ?? null}
                onGo={m.id === goId ? () => void send(undefined, PROCEED_TEXT) : undefined} goBusy={goBusy} />)}
              {isAnswering(thread) && !sending && <li className="animate-pulse px-1 text-xs text-ink-500">{agent.name}가 답하는 중…</li>}
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
        {roomError && <p role="alert" className="mx-4 mb-2 rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-800 lg:mx-6">{roomError}</p>}
        {locked && <p role="alert" className="mx-4 mb-2 rounded-xl bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800 lg:mx-6">쓸 수 없어요: {locked}</p>}
        {readOnly ? (
          <div className="border-t border-ink-100 px-4 py-3 text-right lg:px-6">
            <button type="button" className="btn-ghost" onClick={reset}>내 대화로 돌아가기</button>
          </div>
        ) : (
          thread?.status === "closed" ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-ink-100 px-4 py-3 text-sm text-ink-500 lg:px-6">
              <span>끝난 대화예요. 이어서 말할 수 없어요.{thread.summary ? ` 요약: ${humanizePlanIds(thread.summary)}` : ""}</span>
              <button type="button" className="btn-ghost" onClick={reset}>새 대화</button>
            </div>
          ) : (
          <form onSubmit={send} className="flex items-end gap-2 border-t border-ink-100 px-4 py-3 lg:px-6">
            <textarea className="field min-h-[44px] flex-1 resize-none" rows={2} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
              placeholder={`${agent.name}에게 보낼 말 (Enter 보내기, Shift+Enter 줄바꿈)`} aria-label={`${agent.name}에게 보낼 말`} disabled={!!sending || prep || !!locked} />
            <button className="btn-primary" disabled={!text.trim() || !!sending || prep || !!locked || isAnswering(thread)}>보내기</button>
            {api.close && thread && (
              <button type="button" className="btn-ghost" onClick={finish} disabled={!!sending || closing || isAnswering(thread)}>{closing ? "끝내는 중…" : "대화 끝내기"}</button>
            )}
          </form>
          )
        )}
      </div>
    </div>
  );
}

function Bubble({ m, name, me, requestId = null, onGo, goBusy = false }: { m: ChatMessage; name: string; me: boolean; requestId?: string | null; onGo?: () => void; goBusy?: boolean }) {
  const mine = m.role === "user";
  return (
    <li className={`flex flex-col ${mine ? "items-end" : "items-start"}`}>
      <span className="mb-1 px-1 text-[11px] text-ink-500">{mine ? (me ? "나" : "말한 사람") : name}{m.at ? ` · ${hhmm(m.at)}` : ""}</span>
      <div className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${mine ? "bg-brand-600 text-white" : m.kind === "error" ? "bg-red-50 text-red-800" : "bg-ink-100 text-ink-900"}`}>
        {humanizePlanIds(m.text)}
        {m.plan && <PlanCard plan={m.plan} onGo={onGo} disabled={goBusy} />}
        {m.kind === "handoff" && <HandoffLine requestId={requestId} />}
        {m.kind === "started" && <p className="mt-1 text-xs opacity-80">코딩 작업을 시작했어요. 개발 AI 작업 현황에서 볼 수 있어요.</p>}
      </div>
    </li>
  );
}
