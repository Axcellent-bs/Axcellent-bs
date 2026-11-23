import { useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import { DIRECT_AGENTS } from "@/components/office/DirectChat";
import { roomPeople } from "@/lib/chatRooms";
import { ClearDivider, MessengerLog, PinNotice, StatusChip, TypingLines, WorkLogBar } from "@/components/office/Messenger";
import { useQuietLog } from "@/components/office/useQuietLog";
import { TalkInput } from "@/components/office/TalkInput";
import type { OfficeTalk } from "@/components/office/useOfficeTalk";
import { ALL_ROOM, timelineLanes, type ChatItem, type OrchIncident } from "@/lib/agentChat";
import { Link } from "@/lib/nav";
import { useScrollAnchor } from "@/components/office/useScrollAnchor";

// AI 오피스 옆 단톡방. 대화 기록(/agents/chat)과 같은 방·같은 대화를 메신저형으로 좁게 보여 준다.
// 승인 결정 버튼은 두지 않고 대화 기록의 같은 방으로 보낸다.
// 머리 줄 한 줄: 방 고르기(드롭다운, 안 읽은 수 배지) · 사람 수 · "⤢ 넓게"(580px ↔ 화면 절반) · "크게 보기". 받는 AI는 입력칸 왼쪽 드롭다운.
// 스크롤: 맨 아래에 붙어 있으면 따라가고, 위로 올려 보던 중이면 새 줄이 위에 끼어도 보던 줄이 그대로다(useScrollAnchor).

export interface Typing { key: string; name: string; what: string }

/** 대화 칸에 보이는 줄 수 (작업 기록을 거른 뒤 마지막 N줄) */
const SHOWN = 80;

export function OfficeChat({ items: all, typing, freshIds, title, longTitle, status, inc, room = ALL_ROOM, talk, picker, wide, onWide, big = true, clearedAt, onClear, onUndoClear }: {
  /** 11/23 /clear: 내가 지운 시각(있으면 구분선)·지우기·되돌리기. items는 지운 시각 뒤만 넘긴다 */
  clearedAt?: string;
  onClear?: () => Promise<void>;
  onUndoClear?: () => Promise<void>;
  items: ChatItem[]; typing: Typing[]; freshIds: ReadonlySet<string>; title: string; status?: string;
  /** 장애 방의 긴 문장 이름 (방 머리 아래 보조 줄) */
  longTitle?: string;
  /** 장애 방이면 그 사건 (맨 위 고정 공지) */
  inc?: OrchIncident;
  /** 말을 거는 방 */
  room?: string;
  talk?: OfficeTalk;
  /** 방 고르기 (머리 줄 왼쪽, 방 이름 자리) */
  picker?: ReactNode;
  /** 넓게 보기 (오른쪽 칸 화면 절반). onWide가 없으면 버튼 없음 */
  wide?: boolean;
  onWide?: () => void;
  /** "대화 기록에서 크게 보기" 링크 */
  big?: boolean;
}) {
  // 작업 기록 줄(AI끼리 주고받은 호출 기록)은 기본으로 접는다. 토글은 방과 상관없이 하나 (AI 대화방과 같은 값, useQuietLog)
  const [showLog, setShowLog] = useQuietLog();
  const lanes = useMemo(() => timelineLanes(all), [all]);
  const items = useMemo(() => (showLog ? all : lanes.chat).slice(-SHOWN), [showLog, all, lanes]);
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
  const remember = useScrollAnchor(box, pinned, [items, typing]);
  const onScroll = () => {
    const el = box.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    remember();
  };
  const chatHref = `/agents/chat?room=${encodeURIComponent(room)}`;
  const people = useMemo(() => roomPeople(items, DIRECT_AGENTS.map((a) => a.key)), [items]);

  return (
    // chat-round: 이 안의 말풍선·알약·아바타·배지만 둥글게 (styles.css)
    <section aria-label="부서 단톡방" className={`chat-round card flex min-h-[460px] min-w-0 xl:min-h-0 flex-1 flex-col overflow-hidden bg-chat xl:flex-none ${officeChatWidth(!!wide)}`}>
      <header className="flex items-center gap-2 border-b border-ink-900/[.07] px-3 py-2">
        <div className="flex min-w-0 flex-1 items-center gap-x-2">
          {picker ?? <h2 className="min-w-0 truncate text-sm font-semibold">{title}</h2>}
          {people && <span className="hidden shrink-0 text-xs text-ink-700 sm:inline">{people}</span>}
          <StatusChip status={status} />
          {longTitle && <span className="min-w-0 truncate text-xs text-ink-500" title={longTitle}>{longTitle}</span>}
        </div>
        {onWide && (
          <button type="button" onClick={onWide} aria-pressed={!!wide} title={wide ? "단톡방을 580px로" : "단톡방을 화면 절반으로"}
            className="btn-ghost hidden shrink-0 px-2 py-1 text-xs xl:inline-flex">⤢ {wide ? "좁게" : "넓게"}</button>
        )}
        {big && <Link href={chatHref} className="btn-ghost shrink-0 px-2 py-1 text-xs" title="대화 기록에서 크게 보기">크게 보기</Link>}
      </header>
      {inc && <PinNotice inc={inc} />}
      <div ref={box} onScroll={onScroll} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-4">
        {clearedAt && onUndoClear && <ClearDivider clearedAt={clearedAt} onUndo={onUndoClear} />}
        {items.length === 0 ? (
          clearedAt ? null : <p className="py-16 text-center text-sm text-ink-500">아직 이 방에서 주고받은 말이 없어요.</p>
        ) : (
          <MessengerLog items={items} talk={talk} fresh={freshIds} label={`${title} 대화`} hold={{ decideHref: big ? chatHref : undefined }} />
        )}
      </div>
      <WorkLogBar logCount={lanes.logCount} failCount={lanes.failCount} show={showLog} onToggle={() => setShowLog(!showLog)} />
      <TypingLines names={typing} />
      {talk && <TalkInput talk={talk} onSent={toBottom} where="office" room={room} roomName={inc ? title : undefined} onClear={onClear} />}
    </section>
  );
}

/** 오른쪽 칸 폭 (xl 이상): 기본 580px, 넓게면 화면 절반 */
export const officeChatWidth = (wide: boolean) => (wide ? "xl:w-1/2" : "xl:w-[580px]");
