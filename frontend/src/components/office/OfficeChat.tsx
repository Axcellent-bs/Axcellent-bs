import { useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import { DIRECT_AGENTS } from "@/components/office/DirectChat";
import { roomPeople } from "@/lib/chatRooms";
import { MessengerLog, PinNotice, StatusChip, TypingLines } from "@/components/office/Messenger";
import { TalkInput } from "@/components/office/TalkInput";
import type { OfficeTalk } from "@/components/office/useOfficeTalk";
import { ALL_ROOM, type ChatItem, type OrchIncident } from "@/lib/agentChat";
import { Link } from "@/lib/nav";

// AI 오피스 옆 단톡방. 대화 기록(/agents/chat)과 같은 방·같은 대화를 메신저형으로 좁게 보여 준다.
// 승인 결정 버튼은 두지 않고 대화 기록의 같은 방으로 보낸다.

export interface Typing { key: string; name: string; what: string }

export function OfficeChat({ items, typing, freshIds, title, sub, longTitle, status, inc, room = ALL_ROOM, talk, tabs, big = true }: {
  items: ChatItem[]; typing: Typing[]; freshIds: ReadonlySet<string>; title: string; sub?: string; status?: string;
  /** 장애 방의 긴 문장 이름 (방 머리 아래 보조 줄) */
  longTitle?: string;
  /** 장애 방이면 그 사건 (맨 위 고정 공지) */
  inc?: OrchIncident;
  /** 말을 거는 방 */
  room?: string;
  talk?: OfficeTalk;
  /** 방 탭 (위에 붙인다) */
  tabs?: ReactNode;
  /** "대화 기록에서 크게 보기" 링크 (리플레이에는 두지 않는다) */
  big?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  // 내가 보내면 맨 아래로 (붙어 있던 위치와 상관없이)
  const toBottom = () => {
    pinned.current = true;
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  };
  useLayoutEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [items, typing]);
  useLayoutEffect(() => { pinned.current = true; }, [room]);
  const onScroll = () => {
    const el = box.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  };
  const chatHref = `/agents/chat?room=${encodeURIComponent(room)}`;
  const people = useMemo(() => roomPeople(items, DIRECT_AGENTS.map((a) => a.key)), [items]);

  return (
    // chat-round: 이 안의 말풍선·알약·아바타·배지만 둥글게 (styles.css)
    <section aria-label="부서 단톡방" className="chat-round card flex min-h-[460px] min-w-0 flex-1 flex-col overflow-hidden bg-chat xl:w-[580px] xl:flex-none">
      {tabs}
      <header className="flex flex-wrap items-center gap-2 border-b border-ink-900/[.07] px-4 py-2.5">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h2 className="min-w-0 truncate text-sm font-semibold">{title}</h2>
            {people && <span className="text-xs text-ink-700">{people}</span>}
            <StatusChip status={status} />
            {sub && <span className="truncate text-xs text-ink-500">{sub}</span>}
          </div>
          {longTitle && <p className="mt-0.5 truncate text-xs text-ink-500">{longTitle}</p>}
        </div>
        {big && <Link href={chatHref} className="btn-ghost ml-auto px-2.5 py-1 text-xs">대화 기록에서 크게 보기</Link>}
      </header>
      {inc && <PinNotice inc={inc} />}
      <div ref={box} onScroll={onScroll} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-4">
        {items.length === 0 ? (
          <p className="py-16 text-center text-sm text-ink-500">아직 이 방에서 주고받은 말이 없어요.</p>
        ) : (
          <MessengerLog items={items} talk={talk} fresh={freshIds} label={`${title} 대화`} hold={{ decideHref: big ? chatHref : undefined }} />
        )}
      </div>
      <TypingLines names={typing} />
      {talk && <TalkInput talk={talk} onSent={toBottom} where="office" room={room} roomName={inc ? title : undefined} />}
    </section>
  );
}
