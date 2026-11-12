import { Fragment, type ReactNode } from "react";
import { HandoffLine, PlanCard } from "@/components/office/DevCards";
import { TALK_LABEL, proceed, type OfficeTalk } from "@/components/office/useOfficeTalk";
import { INCIDENT_STATUS_KO, type ChatItem, type OrchIncident, type Tone } from "@/lib/agentChat";
import { clock, detectorName, impactOf, num, rootCauseOf } from "@/lib/data";
import { humanizePlanIds } from "@/lib/format";
import { Link } from "@/lib/nav";

// 메신저형 대화 그리기. 대화 기록(/agents/chat)과 AI 오피스 단톡방이 같이 쓴다.
// - 남: 왼쪽 흰 말풍선 + 아바타 + 이름. 같은 쪽이 3분 안에 이어 말하면 아바타·이름을 생략한다
// - 나(내가 단톡방에서 건 말): 오른쪽 보라 말풍선, 시각은 말풍선 옆 아래
// - 날짜가 바뀌는 곳에 날짜 줄, 시스템 알림(사건 열림·종료 등)은 가운데 회색 알약
// - 숫자 묶음(facts)은 카드 말풍선, 사람 승인 대기는 카드 말풍선(결정 경로는 예전 HoldBanner 그대로)
// 문장은 lib/agentChat.ts가 기록된 값으로만 만든다. 이 파일은 모양만 맡는다.

const GROUP_MS = 3 * 60_000;

// 부서별 아바타 색 (디자인 토큰만 사용)
const AVATAR: Record<string, string> = {
  infra: "bg-ink-700 text-app", data: "bg-violet-700 text-white", memory: "bg-violet-700 text-white",
  planning: "bg-brand-500 text-white", marketing: "bg-accent text-white", support: "bg-emerald-700 text-white",
  finance: "bg-amber-700 text-white", hr: "bg-red-700 text-white", dev: "bg-ink-900 text-app",
  human: "bg-ink-500 text-white", safety: "bg-brand-strong text-white", orchestrator: "bg-brand-strong text-white",
};
export const avatarLook = (key: string) => AVATAR[key] ?? "bg-ink-500 text-white";

// 성공·실패는 말풍선 앞 표시로만 (왼쪽 색 줄은 쓰지 않는다)
const TONE_ICON: Record<Tone, string> = { request: "", ok: "✓ ", warn: "⏸ ", fail: "✕ ", muted: "" };

const hhmm = (iso: string) => (iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" }) : "");
const dayKey = (iso: string) => (iso ? new Date(iso).toLocaleDateString("sv-SE") : "");
const dayText = (iso: string) => new Date(iso).toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long" });
/** 받는 쪽 표시: "개발 AI" → "@개발" */
const atName = (to: string) => `@${TALK_LABEL[to] ?? to.replace(/\s*AI$/, "")}`;

export function Avatar({ name, k, size = "md" }: { name: string; k: string; size?: "sm" | "md" }) {
  return (
    <span aria-hidden="true" className={`chat-avatar flex shrink-0 items-center justify-center font-semibold ${size === "sm" ? "h-6 w-6 text-[10px]" : "h-9 w-9 text-xs"} ${avatarLook(k)}`}>
      {name.slice(0, 1)}
    </span>
  );
}

// 사건 상태 칩 (진행 중 빨강 · 해결됨 초록 · 사람 확인 필요 주황). 문구는 INCIDENT_STATUS_KO
const STATUS_TONE: Record<string, string> = {
  open: "bg-red-50 text-red-700", resolved: "bg-emerald-50 text-emerald-700", degraded: "bg-amber-50 text-amber-700",
};
export function StatusChip({ status }: { status?: string }) {
  if (!status) return null;
  return <span className={`chip shrink-0 whitespace-nowrap ${STATUS_TONE[status] ?? "bg-ink-100 text-ink-500"}`}>{INCIDENT_STATUS_KO[status] ?? status}</span>;
}

/** 시스템 알림·날짜 줄: 가운데 회색 알약 */
function Pill({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <li className="flex justify-center">
      <p title={title} className="chat-pill max-w-[85%] bg-ink-900/[.07] px-3 py-1 text-center text-[11px] text-ink-700">{children}</p>
    </li>
  );
}

export interface HoldProps {
  /** 결정 중인 보류 id */
  busyHold?: string | null;
  /** 총괄이 켜져 있어 결정을 보낼 수 있다 */
  canDecide?: boolean;
  /** 총괄 목 모드: 총괄 보류에 승인·거절 버튼 (실제 모드는 보류 큐 링크) */
  orchMock?: boolean;
  onDecide?: (h: Extract<ChatItem, { kind: "hold" }>, approved: boolean) => void;
  /** 결정 버튼을 두지 않는 화면(AI 오피스)이 보낼 곳 */
  decideHref?: string;
}

/** 사람 승인 대기 카드. 결정 경로는 하나: 실제 C 보류는 담당 보류 큐에서만. 총괄 보류 버튼은 목 모드 데모일 때만 */
function HoldCard({ h, hold }: { h: Extract<ChatItem, { kind: "hold" }>; hold: HoldProps }) {
  const orchHold = !h.link;
  const replay = h.contextId?.startsWith("replay-");
  const showButtons = !!hold.onDecide && orchHold && !!hold.orchMock;
  const link = hold.decideHref ?? h.link ?? (hold.orchMock ? undefined : h.decideAt ?? "/finance");
  const busy = hold.busyHold === h.id;
  const outcome = { approved: "사람이 승인 → 다시 진행", rejected: "사람이 거절 → 진행하지 않음", timeout: "기한 안에 결정 없음 → 자동 거절", pending: "" }[h.state];
  return (
    <div className="min-w-[240px] max-w-full overflow-hidden bg-surface text-ink-900 shadow-sm">
      <p className="bg-amber-50 px-3 py-2 text-[13px] font-semibold text-amber-800">
        <span aria-hidden="true">⏸ </span>{h.state === "pending" ? "사람 승인이 필요해요" : "사람이 판단했어요"} · {h.title}
      </p>
      <p className="px-3 py-2 text-xs leading-relaxed text-ink-700">{humanizePlanIds(h.detail)}</p>
      {h.state !== "pending" ? (
        <p className="border-t border-ink-100 px-3 py-2 text-xs font-medium text-ink-700">{outcome}</p>
      ) : replay ? (
        <p className="border-t border-ink-100 px-3 py-2 text-xs text-ink-500">리플레이에서는 잠시 뒤 자동으로 승인됩니다.</p>
      ) : showButtons ? (
        <>
          <div className="flex border-t border-ink-100">
            <button type="button" className="flex-1 py-2 text-xs font-semibold text-ink-700 hover:bg-ink-50 disabled:opacity-40"
              disabled={busy || !hold.canDecide} onClick={() => hold.onDecide?.(h, false)}>거절</button>
            <button type="button" className="flex-1 border-l border-ink-100 py-2 text-xs font-semibold text-brand-700 hover:bg-ink-50 disabled:opacity-40"
              disabled={busy || !hold.canDecide} onClick={() => hold.onDecide?.(h, true)}>승인</button>
          </div>
          <p className="px-3 pb-2 text-[11px] text-ink-500">목 모드 데모 전용 버튼입니다. 총괄 AI는 스스로 승인하지 않고, 여기서 누른 결정을 그대로 전달합니다.</p>
        </>
      ) : link ? (
        <>
          <Link href={link} className="block border-t border-ink-100 py-2 text-center text-xs font-semibold text-brand-700 hover:bg-ink-50">
            {hold.decideHref ? "대화 기록에서 결정 →" : orchHold ? `안전 관문 보류 큐에서 결정 (${link}) →` : "담당 화면에서 결정 →"}
          </Link>
          {orchHold && !hold.orchMock && !hold.decideHref && (
            <p className="px-3 pb-2 text-[11px] text-ink-500">실제 모드에서는 C 등급 보류를 안전 관문 보류 큐 한 곳에서만 결정합니다.</p>
          )}
        </>
      ) : null}
    </div>
  );
}

/** 말풍선 하나 (남·나) */
function Bubble({ m, talk }: { m: Extract<ChatItem, { kind: "msg" }>; talk?: OfficeTalk }) {
  const mine = !!m.human;
  const direct = m.id.startsWith("direct:");
  const showTo = m.to && m.to !== "받는 쪽 미기록" && m.to !== "담당 미정";
  // 숫자 묶음이 붙은 카드 말풍선은 네모, 글만 있는 말풍선은 둥글게(styles.css .chat-round .chat-bubble)
  const cardLike = m.facts.length > 0;
  const shape = cardLike ? "min-w-[220px]" : `chat-bubble ${mine ? "chat-mine" : ""}`;
  const look = mine ? "bg-brand-600 text-white" : "bg-surface text-ink-900 shadow-sm";
  // 출처(공통 로그·안전 관문 기록 등)와 코드 정보는 툴팁으로만
  const tip = [m.tag, m.raw].filter(Boolean).join(" · ") || undefined;
  return (
    <div title={tip} className={`min-w-0 px-3 py-2 text-[13px] leading-relaxed ${look} ${shape}`}>
      <p className={direct ? "whitespace-pre-wrap break-words" : "break-words"}>
        {showTo && <span className={`mr-1 text-[11px] font-bold ${mine ? "text-white/80" : "text-brand-700"}`}>{atName(m.to)}</span>}
        {mine ? "" : TONE_ICON[m.tone]}{humanizePlanIds(m.text)}
      </p>
      {cardLike && (
        <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 border-t border-ink-100 pt-1.5 text-xs">
          {m.facts.map((f) => (
            <Fragment key={f.label}>
              <dt className="text-ink-500">{f.label}</dt>
              <dd className="font-semibold tabular-nums">{f.value}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      {m.plan && (
        <PlanCard plan={m.plan} disabled={!talk || !m.goKey || talk.busy(m.goKey, m.room)}
          onGo={talk && m.goKey ? () => { void proceed(talk, m.goKey as string, m.room); } : undefined} />
      )}
      {m.handoff && <HandoffLine requestId={m.handoff.requestId} />}
    </div>
  );
}

/** 대화 목록. 날짜 줄은 여기서 다시 만든다(기록의 날짜 구분선은 쓰지 않는다) */
export function MessengerLog({ items, talk, hold = {}, fresh, label }: {
  items: ChatItem[]; talk?: OfficeTalk; hold?: HoldProps; fresh?: ReadonlySet<string>; label: string;
}) {
  const rows: ReactNode[] = [];
  let lastDay = "";
  let prev: { who: string; at: number } | null = null;
  for (const it of items) {
    if (it.kind === "separator" && it.variant === "day") continue;
    const day = dayKey(it.at);
    if (day && day !== lastDay) {
      lastDay = day;
      prev = null;
      rows.push(<Pill key={`day:${day}:${it.id}`}>{dayText(it.at)}</Pill>);
    }
    const pop = fresh?.has(it.id) ? "animate-[officePop_.28s_ease-out]" : "";
    if (it.kind === "system" || it.kind === "separator") {
      prev = null;
      rows.push(
        <Pill key={it.id} title={it.kind === "separator" ? it.raw : undefined}>
          {humanizePlanIds(it.text)}{it.at && <time dateTime={it.at} className="ml-1.5 tabular-nums opacity-70">{hhmm(it.at)}</time>}
        </Pill>,
      );
      continue;
    }
    if (it.kind === "hold") {
      prev = null;
      rows.push(
        <li key={it.id} className={`flex max-w-[85%] items-start gap-2 ${pop}`}>
          <Avatar name="안전 관문" k="safety" />
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="text-xs text-ink-700">안전 관문</span>
            <div className="flex items-end gap-1.5">
              <HoldCard h={it} hold={hold} />
              <time dateTime={it.at} className="shrink-0 pb-0.5 text-[10px] tabular-nums text-ink-500">{hhmm(it.at)}</time>
            </div>
          </div>
        </li>,
      );
      continue;
    }
    const mine = !!it.human;
    const at = it.at ? Date.parse(it.at) : 0;
    const who = `${it.fromKey}|${it.from}`;
    const cont = !!prev && prev.who === who && at - prev.at <= GROUP_MS;
    prev = { who, at };
    const time = <time dateTime={it.at} className="shrink-0 pb-0.5 text-[10px] tabular-nums text-ink-500">{hhmm(it.at)}{it.repeat && it.lastAt ? ` ~ ${hhmm(it.lastAt)}` : ""}</time>;
    const repeat = it.repeat ? <span className="shrink-0 pb-0.5 text-[10px] font-semibold text-brand-700" title="같은 내용을 여러 번 주고받아 하나로 묶었습니다">×{it.repeat}</span> : null;
    if (mine) {
      rows.push(
        <li key={it.id} className={`flex max-w-[85%] flex-row-reverse items-end gap-1.5 self-end ${cont ? "-mt-1.5" : ""} ${pop}`}>
          <Bubble m={it} talk={talk} />
          {repeat}{time}
        </li>,
      );
      continue;
    }
    rows.push(
      <li key={it.id} className={`flex max-w-[85%] items-start gap-2 ${cont ? "-mt-1.5" : ""} ${pop}`}>
        {cont ? <span className="w-9 shrink-0" aria-hidden="true" /> : <Avatar name={it.from} k={it.fromKey} />}
        <div className="flex min-w-0 flex-col gap-0.5">
          {!cont && <span className="text-xs text-ink-700">{it.from}</span>}
          <div className="flex items-end gap-1.5">
            <Bubble m={it} talk={talk} />
            {repeat}{time}
          </div>
        </div>
      </li>,
    );
  }
  return <ol role="log" aria-live="polite" aria-relevant="additions" aria-label={label} className="flex flex-col gap-2.5">{rows}</ol>;
}

/** 답을 쓰는 중인 부서: "개발 AI가 답을 쓰는 중…" */
export function TypingLines({ names }: { names: { key: string; name: string; what?: string }[] }) {
  if (!names.length) return null;
  return (
    <ul aria-label="답을 쓰는 중인 부서" className="flex flex-col gap-1 px-4 pb-2">
      {names.map((a) => (
        <li key={a.key} className="flex items-center gap-2 text-xs text-ink-700">
          <span className="flex gap-0.5" aria-hidden="true">
            {[0, 1, 2].map((i) => <span key={i} className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-500" style={{ animationDelay: `${i * 120}ms` }} />)}
          </span>
          <span className="truncate">{a.what ? <><b className="font-semibold">{a.name}</b> {humanizePlanIds(a.what)} 중…</> : <>{a.name}가 답을 쓰는 중…</>}</span>
        </li>
      ))}
    </ul>
  );
}

/** 장애 방 맨 위 고정 공지: 감지 출처·실패 주문·영향 고객·원인 후보 중 기록에 있는 값만. 하나도 없으면 그리지 않는다 */
export function PinNotice({ inc }: { inc: OrchIncident }) {
  const parts: string[] = [];
  const by = inc.detected_by;
  if (by?.source) parts.push(`감지 ${detectorName(by.source)}${clock(by.at) ? ` ${clock(by.at)}` : ""}`);
  const impact = impactOf(inc);
  const failed = num(impact?.failed_order_count);
  const customers = num(impact?.affected_customer_count);
  if (failed != null) parts.push(`실패 주문 ${failed.toLocaleString("ko-KR")}건`);
  if (customers != null) parts.push(`영향 고객 ${customers.toLocaleString("ko-KR")}명`);
  const cause = (rootCauseOf(inc)?.hypotheses ?? []).find((h) => h && typeof h.explanation === "string" && h.explanation);
  if (cause) parts.push(`원인 후보: ${cause.explanation}`);
  if (!parts.length) return null;
  return (
    <p className="mx-3 mt-3 flex items-start gap-2 rounded-xl bg-surface px-3 py-2 text-xs text-ink-700 shadow-sm">
      <span aria-hidden="true">📌</span>
      <span><b className="font-semibold text-ink-900">사건 요약</b> {parts.join(" · ")}</span>
    </p>
  );
}
