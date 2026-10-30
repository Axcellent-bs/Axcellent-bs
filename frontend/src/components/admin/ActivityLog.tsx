import { useState } from "react";
import { Link } from "@/lib/nav";
import { won, shortDateTime } from "@/lib/format";
import {
  CATEGORY_KO, CLAIM_REASON_KO, CLAIM_STATUS_KO, CLAIM_TYPE_KO, RECEIPT_VERDICT_KO, SENTIMENT_KO, STYLE_KO, URGENCY_KO,
} from "@/lib/labels";
import type { ActivityEntry } from "@/lib/types";

// AI 처리 내력: 이 문의에서 AI·상담원·시스템이 한 일을 시간순으로 보여 준다.
// 줄의 문장은 고객지원이 코드로 남긴 사실이고, 화면은 코드 값에 이름표만 붙인다(고객 원문·답변 본문은 담기지 않는다).

const ACTOR: Record<ActivityEntry["actor"], { label: string; tone: string }> = {
  ai: { label: "AI", tone: "bg-brand-50 text-brand-700" },
  agent: { label: "상담원", tone: "bg-violet-50 text-violet-700" },
  customer: { label: "고객", tone: "bg-amber-50 text-amber-800" },
  system: { label: "시스템", tone: "bg-ink-100 text-ink-700" },
};
const HOW_KO: Record<string, string> = { accepted: "AI 초안 그대로", edited: "AI 초안을 고쳐서", written: "직접 작성" };
// 클레임이 문의 서비스에서 올 때 쓰는 상태 이름 중 공용 이름표에 없는 것
const CLAIM_STATE_KO: Record<string, string> = { approved: "승인됨", ...CLAIM_STATUS_KO };
const STATUS_TO_KO: Record<string, string> = { open: "진행 중으로", resolved: "해결로", escalated: "이관으로" };
const FEEDBACK_STATUS_KO: Record<string, string> = { visible: "공개로", hidden: "숨김으로", flagged: "확인 대기로" };
const INSPECTION_RESULT_KO: Record<string, string> = { pass: "정상", fail: "불합격", partial: "일부만 정상" };

const str = (v: unknown) => (typeof v === "string" ? v : null);
const num = (v: unknown) => (typeof v === "number" ? v : null);
const parts = (...xs: (string | null | false | undefined)[]) => xs.filter(Boolean).join(" · ");

/** 줄 아래의 자세한 내용: 코드 값을 사람이 읽는 말로 바꾼다. 모르는 값은 지어내지 않고 비운다. */
function detailOf(e: ActivityEntry): string {
  const d = e.data;
  switch (e.kind) {
    case "inquiry_analyzed": {
      const drafts = num(d.drafts) ?? 0, passed = num(d.drafts_passed) ?? 0, sources = num(d.sources) ?? 0;
      return parts(
        str(d.category) && `분류 ${CATEGORY_KO[str(d.category)!] ?? str(d.category)}`,
        str(d.urgency) && `긴급도 ${URGENCY_KO[str(d.urgency)!] ?? str(d.urgency)}`,
        str(d.sentiment) && `감정 ${SENTIMENT_KO[str(d.sentiment)!] ?? str(d.sentiment)}`,
        `초안 ${drafts}개(검사 통과 ${passed}개)`,
        `근거 정책 ${sources}건`,
        d.fallback === true && "AI 연결이 불안정해 대체 경로로 만듦",
      );
    }
    case "reply_sent":
      return parts(HOW_KO[str(d.how) ?? ""], str(d.draft_style) && `${STYLE_KO[str(d.draft_style)!] ?? str(d.draft_style)} 초안`);
    case "draft_rejected":
      return STYLE_KO[str(d.draft_style) ?? ""] ? `${STYLE_KO[str(d.draft_style)!]} 초안` : "";
    case "reply_blocked": {
      const n = Array.isArray(d.findings) ? d.findings.length : 0;
      return n ? `답변 검사에서 ${n}건이 걸림` : "";
    }
    case "summary_made":
      return parts(d.resolved === true && "해결로 판단", d.escalate === true && "이관 필요로 판단", d.fallback === true && "대체 경로로 정리");
    case "status_changed":
      return STATUS_TO_KO[str(d.status) ?? ""] ?? "";
    case "receipt_checked": {
      const mismatches = Array.isArray(d.mismatches) ? d.mismatches.length : 0;
      return parts(RECEIPT_VERDICT_KO[str(d.verdict) ?? ""], mismatches > 0 && `다른 항목 ${mismatches}개`, num(d.images) ? `사진 ${num(d.images)}장` : null);
    }
    case "compensation":
      return parts(
        str(d.reason_code),
        num(d.count) != null && `쿠폰 ${num(d.count)}장`,
        num(d.total_value) != null && won(num(d.total_value)!),
        str(d.reason),
      );
    case "feedback_received":
      return parts(
        num(d.rating) != null && `별점 ${num(d.rating)}점`,
        d.resolved === true ? "해결됐다고 답함" : d.resolved === false ? "해결 안 됐다고 답함" : null,
        str(d.status) === "flagged" && "확인 대기",
        Array.isArray(d.flags) && d.flags.includes("CS_FOLLOW_UP") && "선제 연락 대상",
        d.source === "console" && "시험 평가",
      );
    case "feedback_status_changed":
      return FEEDBACK_STATUS_KO[str(d.status) ?? ""] ?? "";
    case "resolution_note_added":
      return "";
    case "claim_created":
    case "claim_decided":
    case "claim_return_received":
    case "claim_inspected":
    case "claim_paid":
      return parts(
        str(d.claim_type) && `${CLAIM_TYPE_KO[str(d.claim_type)!] ?? str(d.claim_type)}`,
        str(d.reason) && `${CLAIM_REASON_KO[str(d.reason)!] ?? str(d.reason)}`,
        str(d.inspection) && `검수 ${INSPECTION_RESULT_KO[str(d.inspection)!] ?? str(d.inspection)}`,
        str(d.status) && `지금 상태 ${CLAIM_STATE_KO[str(d.status)!] ?? str(d.status)}`,
        num(d.amount) != null && num(d.amount)! > 0 && `${e.kind === "claim_paid" ? "지급" : "환불 예정"} ${won(num(d.amount)!)}`,
      );
    default:
      return e.detail;
  }
}

/** 줄에서 이어 볼 곳: 클레임 줄은 클레임 화면, 총괄에 넘긴 줄은 총괄 화면 */
function linkOf(e: ActivityEntry): { href: string; label: string } | null {
  if (e.ref?.startsWith("claim:")) return { href: `/claims?id=${e.ref.slice(6)}`, label: "클레임 보기" };
  if (e.kind === "handed_to_orchestrator") return { href: "/orchestrator", label: "총괄에서 보기" };
  return null;
}

function Row({ e }: { e: ActivityEntry }) {
  const actor = ACTOR[e.actor] ?? ACTOR.system;
  const detail = detailOf(e);
  const link = linkOf(e);
  return (
    <li className="relative border-l border-ink-100 pb-3 pl-4 last:pb-0">
      <span className="absolute -left-[3px] top-1.5 h-1.5 w-1.5 rounded-full bg-brand-500" aria-hidden />
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-500">
        <span className={`chip ${actor.tone}`}>{actor.label}</span>
        <time dateTime={e.at}>{shortDateTime(e.at)}</time>
        {link && <Link href={link.href} className="ml-auto font-medium text-brand-700 underline">{link.label} →</Link>}
      </div>
      <p className="mt-0.5 text-xs font-medium text-ink-900" title={e.kind}>{e.title}</p>
      {detail && <p className="text-[11px] leading-relaxed text-ink-500">{detail}</p>}
    </li>
  );
}

export function ActivityLog({ items }: { items: ActivityEntry[] }) {
  const [all, setAll] = useState(false);
  const LIMIT = 8;
  const shown = all ? items : items.slice(-LIMIT);
  return (
    <section aria-label="AI 처리 내력">
      <h3 className="panel-title mb-2">AI 처리 내력 <span className="font-normal text-ink-500">{items.length}건</span></h3>
      {items.length === 0 ? (
        <p className="rounded-xl bg-surface px-3 py-2 text-xs text-ink-500">아직 기록이 없습니다. AI 분석·답변·클레임 처리가 여기에 시간순으로 쌓입니다.</p>
      ) : (
        <>
          {items.length > LIMIT && (
            <button type="button" className="btn-quiet mb-2 px-2 py-1 text-[11px]" onClick={() => setAll(!all)}>
              {all ? "최근 것만 보기" : `이전 ${items.length - LIMIT}건 더 보기`}
            </button>
          )}
          <ol className="ml-1">{shown.map((e, i) => <Row key={`${e.at}-${e.kind}-${i}`} e={e} />)}</ol>
        </>
      )}
    </section>
  );
}
