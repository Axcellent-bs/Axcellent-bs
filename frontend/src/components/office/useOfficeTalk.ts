import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DIRECT_AGENTS, type DirectAgent } from "@/components/office/DirectChat";
import { PROCEED_TEXT } from "@/components/office/DevCards";
import { ALL_ROOM, type ChatItem } from "@/lib/agentChat";
import { POLL_MS, classifyFailure, isAnswering } from "@/lib/directChat";
import { ROOM_POLL_MS, listOfficeRoom, touchOfficeRoom, type OfficeEntry } from "@/lib/officeRoom";
import { useSession, type Account } from "@/demo/session";

// 단톡방(AI 오피스 단톡방·AI 대화방 전체 대화방)에서 부서 AI에게 말 걸기. 단톡방 대화는 모두에게 보인다.
// - 누가 어느 부서에 건 대화인지는 dashboard-api 단톡방 목록(officeRoom.ts)에 올리고, 모든 화면이 그 목록을 읽는다.
//   원문은 각 부서 대화 창구에서 읽는다(목록에 새로 오르거나 마지막 시각이 바뀐 대화, 답을 기다리는 대화만).
// - 1:1 직접 대화(DirectChat)와는 대화를 나눈다: 단톡방 대화는 1:1 대화를 잇지 않고, 1:1 대화는 목록에 올리지 않는다(비공개).
// - 내 단톡방 대화는 목록의 보낸 사람(from_id)이 내 계정인 대화다. 끝난 대화(closed)만 새 대화로 시작하고,
//   기획에 넘긴 대화(handed)는 같은 대화에 잇는다 (직접 대화와 같은 규칙).

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
  /** 내가 그 부서에 보내는 중이거나 내 대화가 답을 기다리는 중 */
  busy: (key: string) => boolean;
  /** 누구의 대화든 답을 기다리는 부서 (자리 "답하는 중" 표시) */
  busyKeys: string[];
  /** 보내기. 성공하면 true (실패면 입력창에 글을 되돌린다) */
  send: (key: string, message: string) => Promise<boolean>;
  /** 섞을 사람 대화·시스템 줄 (시각순) */
  items: ChatItem[];
  /** 처음 불러온 지난 대화 id (말풍선 애니메이션 없이 보인다) */
  quiet: ReadonlySet<string>;
}

/** 읽어 온 단톡방 대화 하나 (목록 줄 + 원문) */
interface Loaded { entry: OfficeEntry; thread: Thread | null; seen: string }

const agentsOf = (): DirectAgent[] =>
  OFFICE_TALK_KEYS.map((k) => DIRECT_AGENTS.find((a) => a.key === k)).filter((a): a is DirectAgent => !!a && a.live && !!a.talk);
const errText = (e: unknown, name: string) => (e instanceof Error ? e.message : `${name}에 연결할 수 없어요`);
const byAt = (a: ChatItem, b: ChatItem) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0);
const keyOf = (agent: string, threadId: string) => `${agent}:${threadId}`;

export function useOfficeTalk(): OfficeTalk {
  const { account } = useSession();
  const agents = useMemo(agentsOf, []);
  const [loaded, setLoaded] = useState<Record<string, Loaded>>({});
  // 타이머 안에서 지금 읽어 둔 대화를 본다
  const loadedRef = useRef(loaded);
  loadedRef.current = loaded;
  // 보내는 중인 말 (서버 답이 오기 전에 내 말풍선을 바로 붙인다)
  const [sending, setSending] = useState<Record<string, { text: string; at: string } | null>>({});
  const [prep, setPrep] = useState<Record<string, boolean>>({});
  const [locked, setLocked] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<ChatItem[]>([]);
  const quiet = useRef(new Set<string>());
  // 원문을 읽는 중인 대화 (같은 대화를 겹쳐 읽지 않는다)
  const reading = useRef(new Set<string>());
  // 읽지 못한(지워졌거나 막힌) 대화의 마지막 시각. 시각이 바뀌기 전에는 다시 읽지 않는다
  const dead = useRef(new Map<string, string>());
  // 처음 받은 목록의 대화는 지난 대화라 애니메이션 없이 보인다
  const firstRound = useRef(true);
  const firstKeys = useRef(new Set<string>());

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

  // 대화 원문 읽기. 다 읽으면 그 대화의 목록 시각(seen)을 남긴다
  const read = useCallback((entry: OfficeEntry) => {
    const a = agents.find((x) => x.key === entry.agent);
    const k = keyOf(entry.agent, entry.threadId);
    if (!a?.talk || reading.current.has(k)) return;
    reading.current.add(k);
    a.talk.get(entry.threadId).then((t) => {
      if (firstKeys.current.has(k)) { for (const m of t.messages) quiet.current.add(`direct:${entry.agent}:${m.id}`); firstKeys.current.delete(k); }
      setLoaded((cur) => {
        // 그사이 내가 보낸 답(더 새 대화)이 들어왔으면 덮지 않는다
        const prev = cur[k];
        if (prev?.thread && prev.thread.updatedAt > t.updatedAt) return cur;
        return { ...cur, [k]: { entry, thread: t, seen: entry.updatedAt } };
      });
    }).catch((err) => {
      dead.current.set(k, entry.updatedAt);
      failed(entry.agent, a.name, err);
    }).finally(() => { reading.current.delete(k); });
  }, [agents, failed]);

  // 단톡방 목록을 읽고, 새로 오르거나 마지막 시각이 바뀐 대화만 원문을 다시 읽는다. 화면을 떠나면 멈춘다
  useEffect(() => {
    if (!account) return;
    let alive = true;
    const tick = () => {
      listOfficeRoom().then((entries) => {
        if (!alive) return;
        const known = new Set(agents.map((a) => a.key));
        const fresh = entries.filter((e) => known.has(e.agent));
        if (firstRound.current) { for (const e of fresh) firstKeys.current.add(keyOf(e.agent, e.threadId)); firstRound.current = false; }
        setLoaded((cur) => {
          let next = cur;
          for (const e of fresh) {
            const k = keyOf(e.agent, e.threadId);
            if (!cur[k]) next = { ...next, [k]: { entry: e, thread: null, seen: "" } };
          }
          return next;
        });
        for (const e of fresh) {
          const k = keyOf(e.agent, e.threadId);
          // 이미 그 시각까지 읽었거나, 그 시각에 읽지 못한 대화는 건너뛴다 (답을 기다리는 대화는 아래 2초 읽기가 맡는다)
          if (loadedRef.current[k]?.seen === e.updatedAt || dead.current.get(k) === e.updatedAt) continue;
          read(e);
        }
      }).catch(() => { /* 목록은 다음 차례에 다시 읽는다 */ });
    };
    tick();
    const timer = window.setInterval(tick, ROOM_POLL_MS);
    return () => { alive = false; window.clearInterval(timer); };
    // read는 바뀌지 않는다 (agents·failed가 고정)
  }, [account, agents, read]);

  // 답을 기다리는 대화(누구의 것이든)는 2초마다 다시 읽고, 답이 오면 멈춘다
  const waiting = Object.values(loaded).filter((l) => isAnswering(l.thread)).map((l) => keyOf(l.entry.agent, l.entry.threadId)).sort().join(",");
  useEffect(() => {
    if (!waiting || !account) return;
    const timer = window.setInterval(() => {
      for (const k of waiting.split(",")) {
        const l = loadedRef.current[k];
        // 읽지 못한 대화는 목록 시각이 바뀔 때까지 다시 읽지 않는다
        if (l && isAnswering(l.thread) && dead.current.get(k) !== l.entry.updatedAt) read(l.entry);
      }
    }, POLL_MS);
    return () => { window.clearInterval(timer); };
  }, [waiting, account, read]);

  // 내 단톡방 대화 중 그 부서의 가장 최근 대화
  const myThread = useCallback((key: string): Thread | null => {
    if (!account) return null;
    let best: Loaded | null = null;
    for (const l of Object.values(loaded)) {
      if (l.entry.agent !== key || l.entry.fromId !== account.id || !l.thread) continue;
      if (!best || l.entry.updatedAt > best.entry.updatedAt) best = l;
    }
    return best?.thread ?? null;
  }, [account, loaded]);

  const send = useCallback(async (key: string, message: string) => {
    const a = agents.find((x) => x.key === key);
    if (!account || !a?.talk || sending[key]) return false;
    const cur = myThread(key);
    const keep = cur && cur.status !== "closed";
    if (cur && !keep) note("지난 대화가 끝나서 새 대화로 시작했어요");
    setSending((s) => ({ ...s, [key]: { text: message, at: new Date().toISOString() } }));
    try {
      const next = await a.talk.send({ ...(keep ? { thread_id: cur.id } : {}), message });
      const k = keyOf(key, next.id);
      const fallback: OfficeEntry = { agent: key, threadId: next.id, fromId: account.id, fromName: account.name || "나", createdAt: next.createdAt, updatedAt: new Date().toISOString() };
      let entry = fallback;
      try {
        entry = (await touchOfficeRoom({ agent: key, threadId: next.id, fromId: account.id, fromName: account.name || "나" })) ?? fallback;
      } catch (err) {
        note(`${a.name}에 보냈지만 단톡방 목록에 올리지 못해 다른 사람에게는 안 보일 수 있어요: ${errText(err, "단톡방 목록")}`);
      }
      dead.current.delete(k);
      setLoaded((c) => ({ ...c, [k]: { entry, thread: next, seen: entry.updatedAt } }));
      return true;
    } catch (err) {
      failed(key, a.name, err);
      note(`${a.name}에 보내지 못했어요: ${errText(err, a.name)}`);
      return false;
    } finally {
      setSending((s) => ({ ...s, [key]: null }));
    }
  }, [account, agents, failed, myThread, note, sending]);

  const busy = useCallback((key: string) => !!sending[key] || isAnswering(myThread(key)), [sending, myThread]);
  const busyKeys = useMemo(
    () => agents.map((a) => a.key).filter((key) => !!sending[key] || Object.values(loaded).some((l) => l.entry.agent === key && isAnswering(l.thread))),
    [agents, sending, loaded],
  );

  const chips = useMemo<TalkChip[]>(() => OFFICE_TALK_KEYS.map((key) => {
    const a = agents.find((x) => x.key === key);
    const reason = !a ? "준비 중" : prep[key] ? "준비 중" : locked[key] ?? null;
    return { key, label: TALK_LABEL[key], disabled: !!reason, reason };
  }), [agents, prep, locked]);

  // 단톡방 대화 → 말풍선 (모두의 대화, 시각순). 내 말은 오른쪽, 다른 사람 말은 이름과 함께 왼쪽
  const items = useMemo(() => {
    if (!account) return [] as ChatItem[];
    const out: ChatItem[] = [...notes];
    for (const l of Object.values(loaded)) {
      const t = l.thread;
      const a = agents.find((x) => x.key === l.entry.agent);
      if (!t || !a) continue;
      const mine = l.entry.fromId === account.id;
      const who = mine ? account.name || "나" : l.entry.fromName || "다른 사람";
      if (t.restricted) {
        // 사람마다 다른 토큰을 쓰는 설정이면 서버가 남의 대화 원문을 가린다. 요약만 한 줄
        out.push({ kind: "system", id: `direct:${a.key}:${t.id}:restricted`, at: t.updatedAt || l.entry.updatedAt, room: ALL_ROOM, text: `${who}님이 ${a.name}와 나눈 대화${t.summary ? `: ${t.summary}` : " (원문은 본인만 봐요)"}` });
        continue;
      }
      // "이대로 진행"은 내 대화의 마지막 계획 메시지에만, 대화가 아직 이야기 중(talking)일 때만 (개발 화면과 같은 규칙)
      const last = t.messages[t.messages.length - 1];
      const goId = mine && t.status === "talking" && last?.role === "assistant" && last.kind === "plan" && last.plan ? last.id : null;
      for (const m of t.messages) {
        const at = m.at || t.updatedAt;
        const id = `direct:${a.key}:${m.id}`;
        if (m.role === "user") {
          out.push({ kind: "msg", id, at, room: ALL_ROOM, from: who, to: a.name, fromKey: "human", central: false, human: mine, text: m.text, tone: "request", facts: [] });
          continue;
        }
        out.push({
          kind: "msg", id, at, room: ALL_ROOM, from: a.name, to: who, fromKey: a.key, central: false, text: m.text, tone: m.kind === "error" ? "fail" : "request", facts: [],
          ...(m.plan ? { plan: { title: m.plan.title, done: m.plan.done } } : {}),
          ...(m.kind === "handoff" ? { handoff: { requestId: t.requestId ?? null } } : {}),
          ...(m.id === goId ? { goKey: a.key } : {}),
        });
      }
    }
    // 서버 답이 오기 전에도 내 말풍선을 바로 붙인다 (보내기가 끝나면 서버 대화의 같은 말로 바뀐다)
    for (const a of agents) {
      const pending = sending[a.key];
      if (pending) out.push({ kind: "msg", id: `direct:${a.key}:sending`, at: pending.at, room: ALL_ROOM, from: account.name || "나", to: a.name, fromKey: "human", central: false, human: true, text: pending.text, tone: "request", facts: [] });
    }
    return out.sort(byAt);
  }, [account, agents, loaded, notes, sending]);

  return { account: account ?? null, chips, busy, busyKeys, send, items, quiet: quiet.current };
}

/** 계획 카드의 "이대로 진행": 같은 대화에 "진행해"를 보낸다 */
export const proceed = (talk: OfficeTalk, key: string) => talk.send(key, PROCEED_TEXT);
