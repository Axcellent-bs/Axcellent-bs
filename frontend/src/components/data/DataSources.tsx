import type { ReactNode } from "react";
import { Chip, Empty, TONE, Waiting } from "@/components/finance/bits";
import { okEvidence, useSkill, type SkillState } from "@/components/data/useSkill";
import { Sources, when } from "@/components/data/Sources";
import {
  DATA_SERVER, dataAi, kpiLive,
  type CollectorStatus, type DataSource, type DataSourcesOutput, type DomainProfileOutput, type LlmDailyUsage, type ProfileEvidence, type SourceDataKind, type SourcePlanItem, type SourcePlanOutput,
} from "@/lib/data";

// 트렌드&콘텐츠 "데이터 출처" 하위 탭 (루프 4단계와 별개, 탭 맨 끝). 서버 응답 그대로 보여 준다.
// - 위: 회사 이해(domain_profile, 계약 DomainProfileOutput). 업종·상품군·고객 축·분석 질문·씨앗 키워드.
//   씨앗 키워드는 키워드마다 근거 문장, 업종·상품군·고객 축·분석 질문은 칸마다 접힘 "근거 N개"(서버 evidence, 원문 대조를 통과한 것만).
//   사람이 덮어쓴 칸(overridden)은 "사람이 덮어씀" 칩
// - 가운데: 수집 계획(source_plan, 계약 SourcePlanOutput). 출처별 고른 이유·카테고리·키워드·연령 여부·호출 수,
//   출처별 할당량(quota: 쓸 양 / 상한). 뺀 값(카테고리 id·키워드)은 접힘. [계획 승인]은 꺼 둠, 승인 상태는 서버 값
// - 아래: 출처 현황 표(data_sources, 계약 DataSourcesOutput). 키는 이름(key_envs)만 있고 값은 응답에 없다.
//   표 위에 스냅샷 수집기 상태(collectors: 수집 중·수집 멈춤, 마지막 수집 시각, 서버 경고 문장). 예전 응답엔 없다
//   "등록하면 얻는 근거"는 수집 계획의 그 출처 unlocks (계획에 없는 출처는 칸이 없다)
// - 세 응답은 이 탭을 처음 열 때 받는다

const NO_REASON = "서버가 이유를 주지 않았어요";
const APPROVE_BLOCKED = "승인 기능은 승인 절차 등록 후 열려요";
const KIND: Record<SourceDataKind, string> = { search_interest: "검색 관심", consumption: "실제 소비", viewing: "시청", survey: "조사" };
const CHECK: Record<SourcePlanItem["category_check"], string> = {
  verified: "카테고리 확인됨",
  after_key: "키 등록 후 확인",
  not_applicable: "",
  unknown: "카테고리 확인 못 함",
};
const n = (v: number) => v.toLocaleString("ko-KR");

function State<T>({ s, what, children }: { s: SkillState<T>; what: string; children: (out: T) => ReactNode }) {
  if (s.kind === "loading") return <Waiting what={`${what} 받는 중…`} />;
  if (s.kind === "failed") return <Empty>{what} 응답을 받지 못했어요. {s.reason} {DATA_SERVER}가 켜져 있는지 확인해 주세요.</Empty>;
  return <>{children(s.out)}</>;
}

function Section({ id, title, meta, tools, children }: { id: string; title: string; meta?: string | null; tools?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="card space-y-3 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 id={id} className="text-lg font-bold text-ink-900">{title}</h2>
          {meta && <p className="text-xs text-ink-500">{meta}</p>}
        </div>
        {tools}
      </div>
      {children}
    </section>
  );
}

// ------------------------------------------------------------------- 회사 이해

const OVERRIDDEN = <Chip label="사람이 덮어씀" tone={TONE.warn} />;

const EVIDENCE_FROM: Record<ProfileEvidence["evidence_from"], string> = {
  about: "회사 소개", product: "상품 이름", category: "카테고리 이름", description: "상품 설명",
};

/** 칸의 근거 문장 접힘 (서버 evidence 중 그 칸 것만). 없으면 그리지 않는다 */
function EvidenceFold({ list }: { list: ProfileEvidence[] }) {
  if (list.length === 0) return null;
  return (
    <details className="mt-1.5 text-xs">
      <summary className="cursor-pointer font-semibold text-ink-500">근거 {list.length}개</summary>
      <ul className="mt-1 space-y-1">
        {list.map((e, k) => (
          <li key={`${e.value}-${k}`} className="text-ink-700">
            <span className="font-semibold text-ink-900">{e.value}</span> · {e.evidence}
            <span className="text-ink-500"> ({EVIDENCE_FROM[e.evidence_from]})</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function Field({ label, name, overridden, evidence, children }: {
  label: string; name: string; overridden: string[]; evidence?: ProfileEvidence[]; children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <p className="flex flex-wrap items-center gap-1.5 text-xs font-semibold text-ink-500">{label}{overridden.includes(name) && OVERRIDDEN}</p>
      <div className="text-sm text-ink-700">{children}</div>
      {evidence && <EvidenceFold list={evidence} />}
    </div>
  );
}

function Profile({ p }: { p: DomainProfileOutput }) {
  if (p.status === "unknown") return <Empty>{p.reason ?? NO_REASON}</Empty>;
  const o = p.overridden;
  const ev = (field: ProfileEvidence["field"]) => (p.evidence ?? []).filter((e) => e.field === field);
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        {p.industry && <Field label="업종" name="industry" overridden={o} evidence={ev("industry")}><span className="font-semibold text-ink-900">{p.industry}</span></Field>}
        {p.customer_axes.length > 0 && (
          <Field label="고객 축" name="customer_axes" overridden={o} evidence={ev("customer_axis")}>
            <ul className="flex flex-wrap gap-1.5">{p.customer_axes.map((a) => <li key={a} className="chip bg-ink-100 text-ink-700">{a}</li>)}</ul>
          </Field>
        )}
      </div>
      {p.product_groups.length > 0 && (
        <Field label="상품군" name="product_groups" overridden={o} evidence={ev("product_group")}>
          <ul className="flex flex-wrap gap-1.5">
            {p.product_groups.map((g) => (
              <li key={g.name} className="chip bg-surface text-ink-700 ring-1 ring-ink-100" title={g.category_codes.length ? `카테고리 ${g.category_codes.join(", ")}` : undefined}>
                <span className="font-semibold">{g.name}</span><span className="ml-1 text-xs text-ink-500">상품 {n(g.product_count)}개</span>
              </li>
            ))}
          </ul>
        </Field>
      )}
      {p.questions.length > 0 && (
        <Field label="분석 질문" name="questions" overridden={o} evidence={ev("question")}>
          <ul className="list-disc space-y-0.5 pl-5">{p.questions.map((q) => <li key={q}>{q}</li>)}</ul>
        </Field>
      )}
      {p.seed_keywords.length > 0 && (
        <Field label={`씨앗 키워드 ${p.seed_keywords.length}개`} name="seed_keywords" overridden={o}>
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {p.seed_keywords.map((k) => (
              <li key={k.keyword}>
                <details className="rounded-xl px-3 py-1.5 ring-1 ring-ink-100">
                  <summary className="cursor-pointer text-sm font-semibold text-ink-900">
                    {k.keyword}{k.evidence_from === "override" && <span className="ml-1.5 align-middle">{OVERRIDDEN}</span>}
                  </summary>
                  <p className="mt-1 text-xs text-ink-700">근거 · {k.evidence}</p>
                </details>
              </li>
            ))}
          </ul>
        </Field>
      )}
      {p.dropped_keywords.length > 0 && (
        <details className="text-xs text-ink-500">
          <summary className="cursor-pointer font-semibold text-ink-700">근거가 없어 뺀 키워드 {p.dropped_keywords.length}개</summary>
          <ul className="mt-1 space-y-0.5 pl-4">{p.dropped_keywords.map((d) => <li key={d.keyword}>{d.keyword} · {d.reason}</li>)}</ul>
        </details>
      )}
      {p.limits.length > 0 && <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-500">{p.limits.map((l) => <li key={l}>{l}</li>)}</ul>}
    </div>
  );
}

// ------------------------------------------------------------------- 수집 계획

function ApprovalChip({ approved }: { approved: boolean }) {
  return <Chip label={approved ? "수집 승인됨" : "승인 전"} tone={approved ? TONE.ok : TONE.muted} />;
}

function PlanItem({ i, name }: { i: SourcePlanItem; name: string }) {
  const check = CHECK[i.category_check];
  return (
    <li className="space-y-2 rounded-xl p-3 ring-1 ring-ink-100">
      <div className="flex flex-wrap items-center gap-1.5">
        <h3 className="mr-auto text-base font-semibold text-ink-900">{name}</h3>
        <Chip label={i.status === "available" ? "사용 가능" : "키 필요"} tone={i.status === "available" ? TONE.ok : TONE.muted} />
        <ApprovalChip approved={i.approved} />
      </div>
      <p className="text-sm text-ink-700">{i.reason}</p>
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[6rem_minmax(0,1fr)]">
        {i.categories.length > 0 && (<>
          <dt className="text-xs font-semibold text-ink-500">카테고리</dt>
          <dd className="text-ink-700">{i.categories.map((c) => c.name ? `${c.name} (${c.category_id})` : c.category_id).join(", ")}
            {check && <span className="block text-xs text-ink-500">{check}{i.category_check_reason ? ` · ${i.category_check_reason}` : ""}</span>}
          </dd>
        </>)}
        {i.keywords.length > 0 && (<>
          <dt className="text-xs font-semibold text-ink-500">키워드</dt>
          <dd className="text-ink-700">{i.keywords.join(", ")}</dd>
        </>)}
        <dt className="text-xs font-semibold text-ink-500">연령대</dt>
        <dd className="text-ink-700">{i.use_age ? "연령대별로 받음" : "연령대 안 씀"}</dd>
        <dt className="text-xs font-semibold text-ink-500">예상 호출</dt>
        <dd className="tabular-nums text-ink-700">{n(i.calls)}번 · 할당량 {n(i.quota_units)}</dd>
      </dl>
      {i.dropped.length > 0 && (
        <details className="text-xs text-ink-500">
          <summary className="cursor-pointer font-semibold text-ink-700">확인 안 된 ID·뺀 값 {i.dropped.length}개</summary>
          <ul className="mt-1 space-y-0.5 pl-4">{i.dropped.map((d, k) => <li key={`${d.value}-${k}`}><code className="font-mono">{d.value}</code> · {d.reason}</li>)}</ul>
        </details>
      )}
    </li>
  );
}

function Plan({ p, names }: { p: SourcePlanOutput; names: Map<string, string> }) {
  if (p.status === "unknown") return <Empty>{p.reason ?? NO_REASON}</Empty>;
  const nameOf = (id: string) => names.get(id) ?? id;
  return (
    <div className="space-y-3">
      {p.items.length === 0 ? <Empty>{p.summary || "고른 출처가 없어요."}</Empty>
        : <ul className="space-y-3">{p.items.map((i) => <PlanItem key={i.source_id} i={i} name={nameOf(i.source_id)} />)}</ul>}
      {p.quota.length > 0 && (
        <div className="rounded-xl bg-ink-50 px-3 py-2">
          <p className="text-xs font-semibold text-ink-500">출처별 할당량 (계획이 쓸 양 / 상한)</p>
          <ul className="mt-1 space-y-0.5 text-sm text-ink-700">
            {p.quota.map((q) => <li key={q.source_id} className="tabular-nums">{nameOf(q.source_id)} · {n(q.units)} / {n(q.cap)} {q.unit}</li>)}
          </ul>
        </div>
      )}
      {p.trimmed.length > 0 && <p className="text-xs text-amber-700">할당량 상한으로 자름 · {p.trimmed.join(" · ")}</p>}
      {p.dropped_sources.length > 0 && (
        <details className="text-xs text-ink-500">
          <summary className="cursor-pointer font-semibold text-ink-700">뺀 출처 {p.dropped_sources.length}개</summary>
          <ul className="mt-1 space-y-0.5 pl-4">{p.dropped_sources.map((d, k) => <li key={`${d.value}-${k}`}><code className="font-mono">{d.value}</code> · {d.reason}</li>)}</ul>
        </details>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-ink-100 pt-3">
        <button type="button" className="btn-primary" disabled aria-describedby="plan-approve-note">계획 승인</button>
        <p id="plan-approve-note" className="text-xs text-ink-500">{APPROVE_BLOCKED}</p>
      </div>
      {p.approval_note && <p className="text-xs text-ink-500">{p.approval_note}</p>}
    </div>
  );
}

// ------------------------------------------------------------------- 출처 현황 표

function SourceRow({ s, unlocks }: { s: DataSource; unlocks: string | null }) {
  const ok = s.status === "available";
  return (
    <tr className="align-top">
      <td className="py-2 pr-3">
        <p className="font-semibold text-ink-900">{s.docs_url ? <a href={s.docs_url} target="_blank" rel="noreferrer noopener" className="hover:underline">{s.name}</a> : s.name}</p>
        {s.period && <p className="text-xs text-ink-500">{s.period}</p>}
      </td>
      <td className="py-2 pr-3 text-ink-700">{s.kinds.map((k) => KIND[k]).join(", ")}</td>
      <td className="py-2 pr-3 text-ink-700">{s.industries.map((x) => (x === "all" ? "전체" : x)).join(", ")}</td>
      <td className="py-2 pr-3 text-ink-700">{[s.age ? "연령" : null, s.gender ? "성별" : null].filter(Boolean).join("·") || "없음"}</td>
      <td className="py-2">
        <div className="flex flex-wrap gap-1">
          <Chip label={ok ? "사용 가능" : "키 필요"} tone={ok ? TONE.ok : TONE.muted} />
          <ApprovalChip approved={s.approved} />
        </div>
        {s.status_reason && <p className="mt-1 text-xs text-ink-500">{s.status_reason}</p>}
        {s.key_envs.length > 0 && <p className="mt-1 text-xs text-ink-500">키 이름 · {s.key_envs.map((k) => <code key={k} className="mr-1 font-mono">{k}</code>)}</p>}
        {!ok && unlocks && <p className="mt-1 text-xs text-ink-700">등록하면 얻는 근거 · {unlocks}</p>}
      </td>
    </tr>
  );
}

/** 스냅샷 수집기 (서버 값 그대로). 멈췄으면 경고 문장을 눈에 띄게 */
function Collectors({ list }: { list: CollectorStatus[] | undefined }) {
  if (!list || list.length === 0) return null;
  return (
    <ul className="space-y-2" aria-label="스냅샷 수집기">
      {list.map((c) => (
        <li key={c.collector_id} className={`rounded-xl px-3 py-2 text-sm ${c.stalled ? "bg-amber-50 text-amber-900" : "bg-ink-50 text-ink-700"}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{c.name}</span>
            <Chip label={c.stalled ? "수집 멈춤" : "수집 중"} tone={c.stalled ? TONE.warn : TONE.ok} />
            <span className="text-xs">
              마지막 수집 {c.last_collected_at ? when(c.last_collected_at) : "없음"} · 기준 {c.max_age_hours}시간
            </span>
          </div>
          {c.warning && <p className="mt-1 text-xs font-semibold">{c.warning}</p>}
          <p className="mt-0.5 text-xs opacity-80">{c.reason}</p>
        </li>
      ))}
    </ul>
  );
}

/** data 부서 LLM 오늘 사용량/상한 (서버 값 그대로). 닿았으면 서버 문장을 눈에 띄게 */
function LlmUsage({ usage }: { usage: LlmDailyUsage | null }) {
  if (!usage) return null;
  return (
    <div className={`rounded-xl px-3 py-2 text-sm ${usage.reached ? "bg-amber-50 text-amber-900" : "bg-ink-50 text-ink-700"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">데이터 AI LLM 오늘 사용량</span>
        <Chip label={usage.reached ? "하루 상한 도달" : "상한 안"} tone={usage.reached ? TONE.warn : TONE.ok} />
        <span className="text-xs">호출 {n(usage.calls)}/{n(usage.call_cap)}회 · 토큰 {n(usage.tokens)}/{n(usage.token_cap)}</span>
      </div>
      {usage.note && <p className="mt-1 text-xs font-semibold">{usage.note}</p>}
    </div>
  );
}

function SourceTable({ out, plan }: { out: DataSourcesOutput; plan: SourcePlanOutput | null }) {
  if (out.sources.length === 0) return <Empty>{out.summary || "출처가 없어요."}</Empty>;
  const unlocks = new Map((plan?.items ?? []).map((i) => [i.source_id, i.unlocks]));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] text-left text-sm">
        <thead className="text-xs text-ink-500">
          <tr className="border-b border-ink-100">
            <th scope="col" className="py-2 pr-3 font-semibold">출처</th>
            <th scope="col" className="py-2 pr-3 font-semibold">주는 데이터</th>
            <th scope="col" className="py-2 pr-3 font-semibold">맞는 업종</th>
            <th scope="col" className="py-2 pr-3 font-semibold">연령·성별</th>
            <th scope="col" className="py-2 font-semibold">상태</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {out.sources.map((s) => <SourceRow key={s.source_id} s={s} unlocks={unlocks.get(s.source_id) ?? null} />)}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------- 탭

export function DataSources() {
  const profile = useSkill(() => dataAi.domainProfile());
  const plan = useSkill(() => dataAi.sourcePlan());
  const sources = useSkill(() => dataAi.dataSources());
  if (!kpiLive) return <Empty>데이터 AI 토큰(VITE_DATA_A2A_TOKEN)이 없어 데이터 출처를 받지 않아요.</Empty>;
  const names = new Map((sources.state.kind === "ok" ? sources.state.out.sources : []).map((s) => [s.source_id, s.name]));
  const planOut = plan.state.kind === "ok" ? plan.state.out : null;
  const p = profile.state.kind === "ok" ? profile.state.out : null;
  return (
    <div className="space-y-5">
      <Section id="data-sources-profile" title="회사 이해"
        meta={p ? [p.tenant, p.products_seen ? `상품 ${n(p.products_seen)}개를 봄` : null, p.generated_at ? `만든 시각 ${when(p.generated_at)}` : null].filter(Boolean).join(" · ") : null}>
        <State s={profile.state} what="회사 이해">{(out) => <Profile p={out} />}</State>
        <Sources evidence={okEvidence(profile.state)} label="근거 · 회사 이해가 불러온 곳" />
      </Section>
      <Section id="data-sources-plan" title="수집 계획"
        meta={planOut ? [planOut.generated_at ? `만든 시각 ${when(planOut.generated_at)}` : null, planOut.cached ? "저장된 계획" : null].filter(Boolean).join(" · ") : null}>
        <State s={plan.state} what="수집 계획">{(out) => <Plan p={out} names={names} />}</State>
        <Sources evidence={okEvidence(plan.state)} label="근거 · 수집 계획이 불러온 곳" />
      </Section>
      <Section id="data-sources-list" title="출처 현황"
        meta={sources.state.kind === "ok" && sources.state.out.checked_at ? `확인 시각 ${when(sources.state.out.checked_at)}` : null}>
        <State s={sources.state} what="출처 목록">{(out) => (
          <div className="space-y-3">
            <Collectors list={out.collectors} />
            <LlmUsage usage={out.llm_usage ?? null} />
            <SourceTable out={out} plan={planOut} />
          </div>
        )}</State>
      </Section>
    </div>
  );
}
