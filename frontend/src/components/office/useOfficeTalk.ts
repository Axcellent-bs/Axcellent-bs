import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DIRECT_AGENTS, type DirectAgent } from "@/components/office/DirectChat";
import { PROCEED_TEXT } from "@/components/office/DevCards";
import { ALL_ROOM, type ChatItem } from "@/lib/agentChat";
import { POLL_MS, classifyFailure, isAnswering } from "@/lib/directChat";
import { loadOwners, loadThreadId, saveOwner, saveThreadId, threadKey } from "@/lib/directThreads";
import { useSession, type Account } from "@/demo/session";

// 부서 AI에게 직접 말 걸기. AI 오피스 단톡방과 AI 대화방 전체 대화방이 같이 쓴다.
// AI 대화방 직접 대화(DirectChat)와 같은 창구·같은 대화(이 탭의 sessionStorage 대화 번호, 주인 기억)를 잇는다.
// 열람 규칙도 같다: 서버가 가린 대화(restricted)나 이 브라우저가 기억한 주인이 내가 아닌 대화는 보여 주지 않는다.
// 이어 보내기도 직접 대화와 같다: 끝난 대화(closed)만 새 대화로 시작하고, 기획에 넘긴 대화(handed)는 같은 대화에 잇는다.

type Talk = NonNullable<DirectAgent["talk"]>;
type Thread = Awaited<ReturnType<Talk["get"]>>;

/** 받는 곳 칩 순서 (서버를 부르는 대상은 이 목록뿐) */
export const OFFICE_TALK_KEYS = ["dev", "data", "support", "finance", "planning", "marketing", "hr", "infra", "orchestrator"] as const;
export const TALK_KEYS = OFFICE_TALK_KEYS;
export type TalkKey = (typeof OFFICE_TALK_KEYS)[number];
/** 칩 이름 (사무실 자리 이름과 같다) */
export const TALK_LABEL: Record<string, string> = {
  dev: "개발", data: "데이터분석", support: "고객지원", finance: "재무", planning: "기획", marketing: "마케팅", hr: "인사", orchestrator: "총괄", infra: "인프라",
};

export interface TalkChip { key: string; label: string; disabled: boolean; reason: string | null }
export interface OfficeTalk {
  account: Account | null;
  chips: TalkChip[];
  /** 그 부서가 보내는 중이거나 답을 기다리는 중 */
  busy: (key: string) => boolean;
  busyKeys: string[];
  /** 보내기. 성공하면 true (실패면 입력창에 글을 되돌린다) */
  send: (key: string, message: string) => Promise<boolean>;
  /** 섞을 사람 대화·시스템 줄 (시각순) */
  items: ChatItem[];
  /** 처음 불러온 지난 대화 id (말풍선 애니메이션 없이 보인다) */
  quiet: ReadonlySet<string>;
}

const agentsOf = (): DirectAgent[] =>
  OFFICE_TALK_KEYS.map((k) => DIRECT_AGENTS.find((a) => a.key === k)).filter((a): a is DirectAgent => !!a && a.live && !!a.talk);
const errText = (e: unknown, name: string) => (e instanceof Error ? e.message : `${name}에 연결할 수 없어요`);
const byAt = (a: ChatItem, b: ChatItem) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0);

export function useOfficeTalk(): OfficeTalk {
  const { account } = useSession();
  const agents = useMemo(agentsOf, []);
  const [threads, setThreads] = useState<Record<string, Thread | null>>({});
  // 보내는 중인 말 (서버 답이 오기 전에 내 말풍선을 바로 붙인다)
  const [sending, setSending] = useState<Record<string, { text: string; at: string } | null>>({});
  const [prep, setPrep] = useState<Record<string, boolean>>({});
  const [locked, setLocked] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<ChatItem[]>([]);
  const quiet = useRef(new Set<string>());

  const note = useCallback((text: string) => {
    const at = new Date().toISOString();
    setNotes((n) => [...n, { kind: "system", id: `direct:note:${at}:${n.length}`, at, room: ALL_ROOM, text }]);
  }, []);
  // 실패를 칩 상태로 가른다 (대화방과 같은 규칙). other면 false
  const failed = useCallback((key: string, name: string, err: unknown) => {
    const k = classifyFailure(err);
    if (k === "preparing") { setPrep((p) => ({ ...p, [key]: true })); return true; }
    if (k === "forbidden") { setLocked((l) => ({ ...l, [key]: errText(err, name) })); return true; }
    return false;
  }, []);
  const mine = useCallback((t: Thread, acc: Account) => !t.restricted && loadOwners()[t.id] === acc.id, []);

  // 처음 열 때: 이 탭에서 이어 가던 내 대화만 읽는다. 기억한 대화가 없는 부서는 아무 호출도 하지 않는다
  // (미리 확인하는 목록 읽기도 공통 로그에 남아 모두의 단톡방에 줄이 쌓이므로 하지 않는다).
  // 칩은 처음에 모두 켜 두고, 실제로 실패했을 때만(이 읽기·보내기) "준비 중"·막힘으로 바꾼다
  useEffect(() => {
    if (!account) return;
    let alive = true;
    for (const a of agents) {
      const talk = a.talk as Talk;
      const tkey = threadKey(a.key, account.id);
      const id = loadThreadId(tkey);
      if (id && loadOwners()[id] === account.id) {
        talk.get(id).then((t) => {
          if (!alive || !mine(t, account)) return;
          for (const m of t.messages) quiet.current.add(`direct:${a.key}:${m.id}`);
          setThreads((cur) => ({ ...cur, [a.key]: t }));
        }).catch((err) => { if (alive && !failed(a.key, a.name, err)) saveThreadId(tkey, null); });
      }
    }
    return () => { alive = false; };
  }, [account, agents, failed, mine]);

  // 답을 기다리는 부서만 2초마다 다시 읽고, 답이 오면 멈춘다. 화면을 떠나면 타이머를 지운다
  const waiting = agents.filter((a) => isAnswering(threads[a.key])).map((a) => `${a.key}=${threads[a.key]?.id}`).join(",");
  useEffect(() => {
    if (!waiting || !account) return;
    let alive = true;
    const timer = window.setInterval(() => {
      for (const pair of waiting.split(",")) {
        const [key, id] = pair.split("=");
        const a = agents.find((x) => x.key === key);
        if (!a?.talk || !id) continue;
        a.talk.get(id).then((t) => {
          if (alive && mine(t, account)) setThreads((cur) => (cur[key]?.id === t.id ? { ...cur, [key]: t } : cur));
        }).catch((err) => {
          if (!alive) return;
          if (!failed(key, a.name, err)) note(`${a.name}에서 답을 읽지 못했어요: ${errText(err, a.name)}`);
          window.clearInterval(timer);
        });
      }
    }, POLL_MS);
    return () => { alive = false; window.clearInterval(timer); };
  }, [waiting, account, agents, failed, mine, note]);

  const send = useCallback(async (key: string, message: string) => {
    const a = agents.find((x) => x.key === key);
    if (!account || !a?.talk || sending[key]) return false;
    const cur = threads[key] ?? null;
    const keep = cur && cur.status !== "closed";
    if (cur && !keep) note("지난 대화가 끝나서 새 대화로 시작했어요");
    setSending((s) => ({ ...s, [key]: { text: message, at: new Date().toISOString() } }));
    try {
      const next = await a.talk.send({ ...(keep ? { thread_id: cur.id } : {}), message });
      // DirectChat.send와 같은 순서: 새 대화면 주인 기억 → 대화 → 이 탭의 대화 번호
      if (!keep) saveOwner(next.id, account.id);
      setThreads((t) => ({ ...t, [key]: next }));
      saveThreadId(threadKey(key, account.id), next.id);
      return true;
    } catch (err) {
      failed(key, a.name, err);
      note(`${a.name}에 보내지 못했어요: ${errText(err, a.name)}`);
      return false;
    } finally {
      setSending((s) => ({ ...s, [key]: null }));
    }
  }, [account, agents, failed, note, sending, threads]);

  const busy = useCallback((key: string) => !!sending[key] || isAnswering(threads[key]), [sending, threads]);
  const busyKeys = useMemo(() => agents.map((a) => a.key).filter(busy), [agents, busy]);

  const chips = useMemo<TalkChip[]>(() => OFFICE_TALK_KEYS.map((key) => {
    const a = agents.find((x) => x.key === key);
    const reason = !a ? "준비 중" : prep[key] ? "준비 중" : locked[key] ?? null;
    return { key, label: TALK_LABEL[key], disabled: !!reason, reason };
  }), [agents, prep, locked]);

  // 사람 대화 → 말풍선 (내 대화만, 시각순)
  const items = useMemo(() => {
    if (!account) return [] as ChatItem[];
    const me = account.name || "나";
    const out: ChatItem[] = [...notes];
    for (const a of agents) {
      const t = threads[a.key];
      const pending = sending[a.key];
      if (t && mine(t, account)) {
        // "이대로 진행"은 마지막 계획 메시지에만, 대화가 아직 이야기 중(talking)일 때만 (개발 화면과 같은 규칙)
        const last = t.messages[t.messages.length - 1];
        const goId = t.status === "talking" && last?.role === "assistant" && last.kind === "plan" && last.plan ? last.id : null;
        for (const m of t.messages) {
          const at = m.at || t.updatedAt;
          const id = `direct:${a.key}:${m.id}`;
          if (m.role === "user") {
            out.push({ kind: "msg", id, at, room: ALL_ROOM, from: me, to: a.name, fromKey: "human", central: false, human: true, text: m.text, tone: "request", facts: [] });
            continue;
          }
          out.push({
            kind: "msg", id, at, room: ALL_ROOM, from: a.name, to: me, fromKey: a.key, central: false, text: m.text, tone: m.kind === "error" ? "fail" : "request", facts: [],
            ...(m.plan ? { plan: { title: m.plan.title, done: m.plan.done } } : {}),
            ...(m.kind === "handoff" ? { handoff: { requestId: t.requestId ?? null } } : {}),
            ...(m.id === goId ? { goKey: a.key } : {}),
          });
        }
      }
      // 서버 답이 오기 전에도 내 말풍선을 바로 붙인다 (보내기가 끝나면 서버 대화의 같은 말로 바뀐다)
      if (pending) {
        out.push({ kind: "msg", id: `direct:${a.key}:sending`, at: pending.at, room: ALL_ROOM, from: me, to: a.name, fromKey: "human", central: false, human: true, text: pending.text, tone: "request", facts: [] });
      }
    }
    return out.sort(byAt);
  }, [account, agents, mine, notes, sending, threads]);

  return { account: account ?? null, chips, busy, busyKeys, send, items, quiet: quiet.current };
}

/** 계획 카드의 "이대로 진행": 같은 대화에 "진행해"를 보낸다 */
export const proceed = (talk: OfficeTalk, key: string) => talk.send(key, PROCEED_TEXT);
