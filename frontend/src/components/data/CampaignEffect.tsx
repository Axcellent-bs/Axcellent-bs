import { useEffect, useState, type FormEvent } from "react";
import { Chip, Empty, Panel, TONE, Waiting } from "@/components/finance/bits";
import type { LoopProbe } from "@/lib/contentLoop";
import {
  CAMPAIGN_DIRECTION_KO, DATA_SERVER, UNKNOWN, dataAi, kpiLive, percent,
  type CampaignChange, type CampaignCoupons, type CampaignEffectOutput,
} from "@/lib/data";

// 캠페인 효과 (트렌드 탭 아래쪽): 데이터 AI(8106) campaign_effect 출력을 그대로 보여 준다.
// - 주문 방향·증분·기준선, 쿠폰 누적 사용률·설명 문장, "확인할 수 없음" 이유는 모두 서버 값이다. 화면은 자릿수만 정한다.
// - 방향이 "같음"인데 증분이 0이 아니면 "같음" 대신 "증가(감소) 추정 ±N건, 아직 통계적으로 확실하지 않음"으로 쓴다.
// - 서버가 설명 문장을 LLM으로 쓰므로 30초 새로고침을 따르지 않는다. 처음 한 번 + "측정"을 누를 때만 부른다.
// - 응답 결과(정상·실패)는 콘텐츠 루프 4단계 칩으로 올린다(onProbe). 칩을 위해 따로 부르지 않는다.

/** Date → datetime-local 입력 값 (이 컴퓨터 시간) */
function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 기본 입력: 지금 시(정각)까지 24시간 */
function defaultWindow(): { from: string; to: string } {
  const end = new Date();
  end.setMinutes(0, 0, 0);
  return { from: toLocalInput(new Date(end.getTime() - 86_400_000)), to: toLocalInput(end) };
}

const fixed = (v: number | null | undefined, digits = 2) =>
  typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : null;
const signed = (v: number) => `${v > 0 ? "+" : ""}${fixed(v)}`;
const when = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? iso : new Date(t).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
};

type Query = { from: string; to: string; campaignId: string | null };
type State =
  | { kind: "loading" }
  | { kind: "ok"; out: CampaignEffectOutput }
  | { kind: "failed"; reason: string };

// ------------------------------------------------------------------- 항목 카드

function Unknown({ reason }: { reason: string | null | undefined }) {
  return (
    <div className="space-y-1">
      <p className="text-2xl font-bold text-ink-500">{UNKNOWN}</p>
      <p className="text-sm text-amber-700">{reason ?? "서버가 이유를 주지 않았어요"}</p>
    </div>
  );
}

/** 방향 줄. 주문은 "같음"이어도 증분이 0이 아니면 추정 방향과 증분을 보여 준다 */
function directionLine(c: CampaignChange, unit: string, isOrders: boolean): { text: string; tone: string } {
  const dir = c.direction ?? "none";
  const excess = typeof c.excess === "number" && Number.isFinite(c.excess) ? c.excess : null;
  if (isOrders && dir === "none" && excess != null && excess !== 0) {
    return { text: `${excess > 0 ? "증가" : "감소"} 추정 ${signed(excess)}${unit}, 아직 통계적으로 확실하지 않음`, tone: TONE.muted };
  }
  const label = CAMPAIGN_DIRECTION_KO[dir] ?? dir;
  const tone = dir === "up" ? TONE.ok : dir === "down" ? TONE.bad : TONE.muted;
  return { text: dir !== "none" && excess != null ? `${label} ${signed(excess)}${unit}` : label, tone };
}

function ChangeCard({ title, c, unit, isOrders }: { title: string; c: CampaignChange; unit: string; isOrders: boolean }) {
  return (
    <article className="card flex flex-col gap-3 p-4" aria-label={title}>
      <p className="panel-title">{title}</p>
      {c.status !== "known" ? <Unknown reason={c.unknown_reason} /> : (() => {
        const line = directionLine(c, unit, isOrders);
        return (
          <>
            <div>
              <p className="text-3xl font-bold leading-none text-ink-900">
                {c.excess != null ? `${signed(c.excess)}${unit}` : UNKNOWN}
              </p>
              <p className="mt-1 text-[11px] text-ink-500">증분 = 캠페인 기간 − 기준선</p>
            </div>
            <div><Chip label={line.text} tone={line.tone} /></div>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1 bg-ink-50 px-3 py-2 text-xs text-ink-700">
              <dt className="text-ink-500">캠페인 기간</dt><dd>{fixed(c.actual) ?? UNKNOWN}{c.actual != null ? unit : ""}</dd>
              <dt className="text-ink-500">기준선</dt><dd>{fixed(c.expected) ?? UNKNOWN}{c.expected != null ? unit : ""}</dd>
              <dt className="text-ink-500">기준선에 쓴 날</dt><dd>지난 {c.baseline_days}일 같은 시간대 평균</dd>
              {c.ratio != null && <><dt className="text-ink-500">기준선 대비</dt><dd>{fixed(c.ratio)}배</dd></>}
              {c.p_value != null && <><dt className="text-ink-500">꼬리확률(p)</dt><dd>{fixed(c.p_value, 4)}</dd></>}
            </dl>
          </>
        );
      })()}
    </article>
  );
}

function CouponCard({ c }: { c: CampaignCoupons }) {
  return (
    <article className="card flex flex-col gap-3 p-4" aria-label="쿠폰">
      <p className="panel-title">쿠폰 누적 사용률</p>
      {c.status !== "known" ? <Unknown reason={c.unknown_reason} /> : (
        <>
          <p className="text-3xl font-bold leading-none text-ink-900">{percent(c.use_rate)}</p>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 bg-ink-50 px-3 py-2 text-xs text-ink-700">
            <dt className="text-ink-500">사용 / 발급</dt><dd>{fixed(c.used_units) ?? UNKNOWN} / {fixed(c.issued_units) ?? UNKNOWN}장</dd>
            {c.coupon_count != null && <><dt className="text-ink-500">쿠폰 수</dt><dd>{fixed(c.coupon_count)}개</dd></>}
          </dl>
          {c.note && <p className="text-xs leading-relaxed text-ink-500">{c.note}</p>}
        </>
      )}
    </article>
  );
}

function Result({ out }: { out: CampaignEffectOutput }) {
  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-500">
        {when(out.window_from)} ~ {when(out.window_to)}{out.campaign_id ? ` · 캠페인 ${out.campaign_id}` : " · campaign_id 없음"}
      </p>
      {out.summary && <p className="border-l-2 border-brand-500 bg-ink-50 px-3 py-2 text-sm leading-relaxed text-ink-700">{out.summary}</p>}
      <div className="grid gap-4 md:grid-cols-3">
        <ChangeCard title="주문" c={out.orders} unit="건" isOrders />
        <CouponCard c={out.coupons} />
        <ChangeCard title="매출" c={out.revenue} unit="원" isOrders={false} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- 본문

export function CampaignEffect({ onProbe }: { onProbe: (p: LoopProbe) => void }) {
  const [form, setForm] = useState(() => ({ ...defaultWindow(), campaignId: "" }));
  const [query, setQuery] = useState<Query>(() => {
    const w = defaultWindow();
    return { from: new Date(w.from).toISOString(), to: new Date(w.to).toISOString(), campaignId: null };
  });
  const [run, setRun] = useState(0);
  const [inputError, setInputError] = useState<string | null>(null);
  const [state, setState] = useState<State>(() =>
    kpiLive ? { kind: "loading" } : { kind: "failed", reason: "데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 캠페인 효과를 받지 않아요. backend/의 scripts/run_web.sh로 화면을 켜면 토큰이 들어갑니다." },
  );

  useEffect(() => {
    if (!kpiLive) return;
    let live = true;
    setState({ kind: "loading" });
    dataAi.campaignEffect(query.from, query.to, query.campaignId)
      .then((r) => live && setState({ kind: "ok", out: r.output }))
      .catch((e: Error) => live && setState({ kind: "failed", reason: e.message }));
    return () => { live = false; };
  }, [query, run]);

  useEffect(() => {
    onProbe(state.kind === "ok" ? "ok" : state.kind === "failed" ? "failed" : "checking");
  }, [state.kind, onProbe]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const from = Date.parse(form.from);
    const to = Date.parse(form.to);
    if (Number.isNaN(from) || Number.isNaN(to)) return setInputError("시작·끝 시각을 모두 넣어 주세요.");
    if (to <= from) return setInputError("끝 시각이 시작 시각보다 뒤여야 해요.");
    setInputError(null);
    setQuery({ from: new Date(from).toISOString(), to: new Date(to).toISOString(), campaignId: form.campaignId.trim() || null });
    setRun((n) => n + 1);
  };

  return (
    <Panel id="data-campaign" title="캠페인 효과"
      hint="캠페인 기간 주문을 지난 7일 같은 시간대 평균(기준선)과 비교하고, 캠페인 쿠폰 누적 사용률을 봅니다. 숫자·이유·문장은 모두 데이터 AI 서버 값입니다.">
      <form onSubmit={submit} className="mb-4 flex flex-wrap items-end gap-2" aria-label="캠페인 효과 측정">
        <label className="min-w-0 flex-1 text-xs text-ink-500 sm:max-w-48">
          campaign_id (선택)
          <input className="field mt-1" value={form.campaignId} onChange={(e) => setForm((f) => ({ ...f, campaignId: e.target.value }))} placeholder="비우면 쿠폰은 보지 않음" />
        </label>
        <label className="min-w-0 text-xs text-ink-500">
          시작
          <input type="datetime-local" className="field mt-1" value={form.from} onChange={(e) => setForm((f) => ({ ...f, from: e.target.value }))} required />
        </label>
        <label className="min-w-0 text-xs text-ink-500">
          끝
          <input type="datetime-local" className="field mt-1" value={form.to} onChange={(e) => setForm((f) => ({ ...f, to: e.target.value }))} required />
        </label>
        <button type="submit" className="btn-primary" disabled={!kpiLive || state.kind === "loading"}>측정</button>
        <p className={`w-full text-[11px] ${inputError ? "text-red-700" : "text-ink-500"}`}>
          {inputError ?? "시각은 이 컴퓨터 시간 기준입니다. 처음에는 지금 정각까지 24시간을 봅니다."}
        </p>
      </form>

      {state.kind === "loading" && <Waiting what="측정하는 중… (지난 7일 같은 시간대 주문을 읽고 설명을 씁니다)" />}
      {state.kind === "failed" && (
        <Empty>
          캠페인 효과를 받지 못했어요. {state.reason}
          {kpiLive ? ` ${DATA_SERVER}가 켜져 있는지 확인해 주세요.` : ""}
        </Empty>
      )}
      {state.kind === "ok" && <Result out={state.out} />}
    </Panel>
  );
}
