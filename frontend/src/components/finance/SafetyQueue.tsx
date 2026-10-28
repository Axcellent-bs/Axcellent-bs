import { useState } from "react";
import { ago, shortDateTime, won } from "@/lib/format";
import { ACTION_KO, SAFETY_SERVER, finance, humanTokenSet, krw, type Review, type RuleHit } from "@/lib/finance";
import { Chip, TONE } from "./bits";

// 안전 장치 보류 큐: 사람만 정할 수 있는 일(C 등급). 정산 묶음 지급 승인도 여기로 온다.
// 대시보드에 따로 보류 큐 화면이 없어서 재무 화면에 둔다. 결정은 safety에 사람 토큰으로 보낸다.

const WHO_KO: Record<string, string> = {
  marketing: "마케팅 AI", support: "고객지원 AI", finance: "재무 AI", hr: "인사 AI", orchestrator: "총괄 AI",
  data: "데이터 AI", planning: "기획 AI", infra: "인프라 AI", memory: "기록 AI", safety: "안전 장치",
};
const WHY_HELD: Record<string, string> = {
  over_threshold: "금액 기준을 넘음", unregistered_action: "등록되지 않은 일", amount_mismatch: "적어 낸 금액과 계산이 다름",
  schema_invalid: "요청 내용이 잘못됨", review_timeout: "기한 안에 아무도 결정하지 않아 자동 취소",
};
const RULE_KO: Record<string, string> = {
  coupon_total: "쿠폰 한 번에 주는 총액", coupon_unit: "쿠폰 한 장 금액", coupon_daily: "하루 동안 준 쿠폰 합계",
  compensation_per_context: "장애 한 건에 준 보상 합계", compensation_per_customer: "한 고객에게 준 보상 합계",
  refund_single: "환불 한 건 금액", seller_risk: "판매자 위험 점수", base_grade: "항상 사람이 확인하는 일",
};
const MONEY_RULES = new Set(["coupon_total", "coupon_unit", "coupon_daily", "compensation_per_context", "compensation_per_customer", "refund_single"]);
const action = (a: string) => ACTION_KO[a] ?? "기타 작업";
const who = (w: string) => WHO_KO[w] ?? "다른 AI";
const num = (n: number) => n.toLocaleString("ko-KR");
const decider = (by: string | null) => (by === "finance-ai" ? "재무 AI" : by === "auto-rule" ? "자동 규칙" : by ? `${by}님` : "사람");

/** "3시간 20분", "45분", "30초" */
function duration(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h > 0) return m ? `${h}시간 ${m}분` : `${h}시간`;
  if (m > 0) return `${m}분`;
  return `${s}초`;
}

function ruleSentence(h: RuleHit): string {
  const name = RULE_KO[h.key] ?? "기타 기준";
  if (h.key === "base_grade") return "판매자 정산 지급처럼 항상 사람이 확인하는 일";
  if (h.outside) return `${name}가 아닌 곳을 바꿈: ${h.outside.join(", ")}`;
  if (h.invalid) return `${name} 정보가 없음`;
  if (h.observed != null && typeof h.value === "number") {
    const f = MONEY_RULES.has(h.key) ? won : num;
    return `${name}가 ${f(h.observed)} — 기준 ${f(h.value)}${h.op === ">" ? " 초과" : " 이상"}`;
  }
  return name;
}

function tipOf(r: Review): string {
  if (r.action === "confirm_settlement") return "위 ③ 판매자 정산 표의 판매자·기간·금액과 같으면 진행하세요.";
  switch (r.report.hold_reason) {
    case "amount_mismatch": return "요청한 AI가 금액을 잘못 적었다는 뜻입니다. 보통 멈추고 담당자에게 알리세요.";
    case "unregistered_action": return "처음 보는 일입니다. 멈추고 재무 담당자에게 문의하세요.";
    case "over_threshold":
      if (r.action === "refund_request") return "쇼핑몰 관리 화면에서 주문·결제 금액을 확인한 뒤 결정하세요.";
      if (r.action === "seller_review") return "판매자 서류와 정보를 본 뒤 결정하세요. 애매하면 멈추세요.";
      return "위 '장애 사건 재무'에서 그 사건의 피해 고객·피해 금액과 비교해 보상이 지나치지 않으면 진행하세요.";
    default: return "애매하면 멈추세요. 멈춘 일은 요청한 AI가 고쳐서 다시 요청할 수 있습니다.";
  }
}

export function SafetyQueue({ reviews, error, highlight, reload }: { reviews: Review[] | null; error: string | null; highlight: string | null; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const pending = (reviews ?? []).filter((r) => r.status === "pending").sort((a, b) => a.expires_at.localeCompare(b.expires_at));
  const recent = (reviews ?? []).filter((r) => r.status !== "pending").slice(-5).reverse();

  async function decide(id: string, decision: "approved" | "rejected") {
    setBusy(id + decision);
    setProblem(null);
    try { await finance.decide(id, decision); await reload(); }
    catch (e) { setProblem((e as Error).message); }
    finally { setBusy(null); }
  }

  return (
    <section id="safety-queue" aria-labelledby="safety-queue-title" className={`scroll-mt-4 rounded-3xl p-4 ring-1 sm:p-5 ${pending.length ? "bg-amber-50 ring-amber-100" : "bg-surface ring-ink-100"}`}>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-1">
        <h2 id="safety-queue-title" className="text-base font-semibold text-ink-900">안전 장치 보류 큐</h2>
        <p className={`text-sm font-semibold ${pending.length ? "text-amber-800" : "text-emerald-800"}`}>{pending.length ? `결정 기다림 ${pending.length}건` : "기다리는 일 없음"}</p>
      </div>
      <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-ink-500">
        판매자 정산 지급처럼 사람만 정할 수 있는 일과, 안전 장치가 C로 보류한 보상 쿠폰·환불(한도 초과·금액 불일치)이 여기 모입니다. 금액과 이유를 보고 진행 또는 멈춤을 누르세요. 진행은 되돌릴 수 없고, 애매하면 멈춤을 누르세요. 정한 시간 안에 아무도 누르지 않으면 자동으로 멈춥니다.
      </p>
      {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{SAFETY_SERVER}에서 보류 큐를 받지 못했습니다. {error}</p>}
      {!humanTokenSet && pending.length > 0 && <p className="mt-3 text-xs text-amber-800">결정 권한(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 버튼이 꺼져 있습니다.</p>}
      {problem && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{problem}</p>}

      {pending.length > 0 && (
        <ul className="mt-4 space-y-3">
          {pending.map((r) => {
            const left = (new Date(r.expires_at).getTime() - Date.now()) / 1000;
            const lines = (r.report.rules ?? []).map(ruleSentence);
            const mark = highlight === r.review_id;
            return (
              <li key={r.review_id} className={`grid gap-3 rounded-2xl bg-surface p-4 shadow-sm sm:grid-cols-[170px_1fr_auto] sm:items-center ${mark ? "ring-2 ring-brand-500" : ""}`}>
                <div>
                  {r.report.computed_amount != null
                    ? <p className="text-xl font-bold tabular-nums text-ink-900">{krw(r.report.computed_amount)}</p>
                    : <p className="text-sm font-semibold text-ink-500">금액으로 판단하지 않는 일</p>}
                  <p className="text-xs text-ink-500">{action(r.action)}</p>
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <b className="text-[15px]">{action(r.action)}</b>
                    <Chip label={r.action === "confirm_settlement" || r.report.rules?.some((h) => h.key === "base_grade") ? "항상 사람이 확인" : WHY_HELD[r.report.hold_reason ?? ""] ?? "확인 필요"} tone="bg-amber-100 text-amber-800" />
                    {mark && <Chip label={r.action === "confirm_settlement" ? "방금 요청한 정산 묶음" : "방금 고른 건"} tone={TONE.info} />}
                  </div>
                  {lines.length > 0 && <ul className="mt-1 space-y-0.5 text-sm text-ink-700">{lines.map((t) => <li key={t}>{t}</li>)}</ul>}
                  <p className="mt-1.5 rounded-lg bg-brand-50 px-2 py-1 text-xs text-brand-700">이럴 땐: {tipOf(r)}</p>
                  <p className="mt-1 break-all text-xs text-ink-500">
                    {who(r.requester)}가 {ago(r.created_at)} 요청 · 확인 번호 {r.review_id} · <span className={left < 3600 ? "font-semibold text-red-700" : ""}>{duration(left)} 안에 결정하지 않으면 자동 취소</span>
                  </p>
                </div>
                <div className="flex gap-2 sm:flex-col">
                  <button className="btn-primary" disabled={!humanTokenSet || busy !== null} onClick={() => decide(r.review_id, "approved")}>진행</button>
                  <button className="btn-ghost" disabled={!humanTokenSet || busy !== null} onClick={() => decide(r.review_id, "rejected")}>멈춤</button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {recent.length > 0 && (
        <div className="mt-4 text-xs">
          <p className="mb-1 font-medium text-ink-700">최근 결정</p>
          <ul className="space-y-1">
            {recent.map((r) => (
              <li key={r.review_id} className="flex flex-wrap items-center gap-2 text-ink-700">
                <Chip label={r.status === "approved" ? "진행" : r.status === "expired" ? "자동 취소" : "멈춤"} tone={r.status === "approved" ? TONE.ok : TONE.bad} />
                <span>{action(r.action)} · {who(r.requester)}</span>
                <span className="text-ink-500">{r.status === "expired" ? "기한이 지나 자동 취소" : `${decider(r.decided_by)} · ${shortDateTime(r.decided_at)}`}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
