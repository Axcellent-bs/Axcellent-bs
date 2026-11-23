import { useCallback, useEffect, useState, type ReactNode } from "react";
import { DIRECT_AGENTS } from "@/components/office/DirectChat";
import { ConfirmRemove, REMOVE_LABEL } from "@/components/office/ConfirmRemove";
import type { MenuItem } from "@/components/office/RoomMenu";
import { removeFailText, type ToastApi } from "@/components/office/useToast";
import { loadOwners, saveThreadId, threadKey, loadThreadId } from "@/lib/directThreads";
import { OFFICE_ROOM_MAX_HOURS, ROOM_ALL, listOfficeRoom, removeOfficeRoom } from "@/lib/officeRoom";
import { useSession } from "@/demo/session";
import { hideConfirm, incidentHideItem } from "@/lib/roomCleanup";

// 방 목록 ⋯ 메뉴의 "지우기" 항목과 확인 창. 서버가 지운 결과만 믿는다(먼저 지운 척하지 않는다).
// 전체 대화방: 내가 만든 단톡방 대화만 / 1:1: 내 1:1 기록만 (단톡방에서 건 대화는 뺀다)
// 장애·이슈 방(11/23): "방 삭제 (모두에게 숨김)" — 해결된 사건만, 진행 중이면 흐리게 "해결된 뒤 삭제할 수 있어요".
//   기록은 서버에 남고 방 목록 "숨긴 방 보기"에서 되돌린다 (onHideIncident를 주는 화면만)

export type RowKind = "all" | "incident" | "direct";

interface Spec { title: string; name: string; lines: string[]; run: () => Promise<void>; note?: string | null }

/** 장애·이슈 방 줄의 사건 정보 (11/23 방 삭제: 해결된 사건만) */
export interface RoomInfo { id: string; status?: string }

export function useRoomRemoval({ toast, onAllRemoved, onDirectRemoved, onHideIncident }: {
  toast: ToastApi; onAllRemoved: () => void; onDirectRemoved: () => void;
  /** 11/23 장애·이슈 방 삭제(모두에게 숨김). 서버 문장으로 던진다. 없으면 항목을 두지 않는다 */
  onHideIncident?: (id: string) => Promise<void>;
}): { itemsFor: (kind: RowKind, title: string, agentKey?: string, room?: RoomInfo) => MenuItem[]; refreshMine: () => void; modal: ReactNode } {
  const { account } = useSession();
  const accountId = account?.id;
  const [spec, setSpec] = useState<Spec | null>(null);
  // 단톡방(전체)에서 내가 만든 대화 수. null = 아직 모름
  const [mine, setMine] = useState<{ n: number | null; err: boolean }>({ n: null, err: false });

  const refreshMine = useCallback(() => {
    if (!accountId) return;
    listOfficeRoom({ hours: OFFICE_ROOM_MAX_HOURS })
      .then((rows) => setMine({ n: rows.filter((e) => e.roomId === ROOM_ALL && e.fromId === accountId).length, err: false }))
      .catch(() => setMine({ n: null, err: true }));
  }, [accountId]);
  useEffect(() => { refreshMine(); }, [refreshMine]);

  const askAll = () => {
    if (!accountId || !mine.n) return;
    setSpec({
      title: `단톡방 대화 ${REMOVE_LABEL}`,
      name: "전체 대화방",
      lines: [
        `전체 대화방에서 내가 부서 AI에게 건 대화 ${mine.n}건을 모두의 목록에서 지웁니다. 각 부서 대화 창구에서도 안 보이게 됩니다.`,
        "기록은 서버에 보관됩니다. 다른 사람이 건 대화는 그대로 남습니다.",
      ],
      run: async () => {
        try {
          const r = await removeOfficeRoom(ROOM_ALL, accountId);
          const bad = r.departments.filter((d) => !d.ok);
          if (bad.length) toast.show(`삭제했지만 ${bad.length}개 부서에서 못 지웠어요: ${bad.map((d) => `${d.agent}(${d.status ?? "실패"})`).join(", ")}`, { ms: 8000 });
          else toast.show('"전체 대화방" 삭제됨');
          onAllRemoved();
        } catch (err) {
          toast.show(removeFailText(err), { ms: 6000 });
        } finally {
          refreshMine();
        }
      },
    });
  };

  const askDirect = async (agentKey: string) => {
    const agent = DIRECT_AGENTS.find((a) => a.key === agentKey);
    const talk = agent?.talk;
    if (!agent || !talk?.remove || !accountId) return;
    const remove = talk.remove;
    try {
      const [threads, rows] = await Promise.all([talk.list(), listOfficeRoom({ hours: OFFICE_ROOM_MAX_HOURS })]);
      const inRoom = new Set(rows.filter((r) => r.agent === agentKey).map((r) => r.threadId));
      const owners = loadOwners();
      const ids = threads.filter((t) => !t.restricted && owners[t.id] === accountId && !inRoom.has(t.id)).map((t) => t.id);
      if (!ids.length) { toast.show("지울 1:1 대화가 없어요"); return; }
      setSpec({
        title: `대화 기록 ${REMOVE_LABEL}`,
        name: `${agent.name} 1:1 대화`,
        lines: [
          `${agent.name}와 나눈 내 1:1 대화 ${ids.length}건을 목록에서 지웁니다. 기록은 서버에 보관됩니다.`,
          "단톡방에서 건 대화와 다른 사람의 대화는 그대로 남습니다.",
        ],
        run: async () => {
          const bad: string[] = [];
          for (const id of ids) {
            try { await remove(id); } catch (err) { bad.push(removeFailText(err).replace("삭제하지 못했어요 ", "")); }
          }
          const key = threadKey(agentKey, accountId);
          const saved = loadThreadId(key);
          if (saved && ids.includes(saved) && !bad.length) saveThreadId(key, null);
          if (bad.length) toast.show(`${ids.length - bad.length}건 지우고 ${bad.length}건은 못 지웠어요 ${[...new Set(bad)].join(" / ")}`, { ms: 8000 });
          else toast.show(`"${agent.name} 1:1 대화" 삭제됨 (${ids.length}건)`);
          onDirectRemoved();
        },
      });
    } catch (err) {
      toast.show(removeFailText(err), { ms: 6000 });
    }
  };

  // 11/23 장애·이슈 방 삭제 = 모두에게 숨김 (해결된 사건만, 확인은 화면 안 상자, 기록은 서버에 남고 되돌릴 수 있음)
  const askHide = (title: string, id: string) => {
    if (!onHideIncident) return;
    const text = hideConfirm(title);
    setSpec({
      title: text.head,
      name: "",
      lines: [text.body],
      note: null,
      run: async () => {
        try {
          await onHideIncident(id);
          toast.show(`"${title}" 방을 숨겼어요 · '숨긴 방 보기'에서 되돌릴 수 있어요`);
        } catch (err) {
          toast.show(err instanceof Error && err.message ? err.message : "방을 삭제하지 못했어요", { ms: 6000 });
        }
      },
    });
  };

  const itemsFor = (kind: RowKind, title: string, agentKey?: string, room?: RoomInfo): MenuItem[] => {
    if (kind === "incident") {
      if (!onHideIncident || !room) return [{ key: "rm", label: "삭제 없음", sub: "장애 기록은 감사용으로 남깁니다", disabled: true }];
      const item = incidentHideItem(room.status);
      return [{ key: "rm", ...item, onSelect: () => askHide(title, room.id) }];
    }
    if (kind === "all") {
      const sub = mine.n === null ? (mine.err ? "단톡방 목록을 읽지 못했어요" : "확인 중…") : mine.n === 0 ? "만든 사람만 삭제할 수 있어요" : `내가 건 대화 ${mine.n}건`;
      return [{ key: "rm", label: REMOVE_LABEL, sub, danger: true, disabled: !mine.n, onSelect: askAll }];
    }
    const agent = DIRECT_AGENTS.find((a) => a.key === agentKey);
    const can = !!agent?.live && !!agent.talk?.remove;
    return [{ key: "rm", label: REMOVE_LABEL, sub: can ? "내 1:1 기록만" : "이 부서는 아직 지울 수 없어요", danger: true, disabled: !can, onSelect: () => { if (agentKey) void askDirect(agentKey); } }];
  };

  const modal = spec ? <ConfirmRemove title={spec.title} name={spec.name} lines={spec.lines} onConfirm={spec.run} onClose={() => setSpec(null)} {...(spec.note !== undefined ? { note: spec.note } : {})} /> : null;
  return { itemsFor, refreshMine, modal };
}
