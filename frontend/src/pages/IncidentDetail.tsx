import { useEffect, useState, type ReactNode } from "react";
import { Navigate } from "react-router";
import { Link, useParams } from "@/lib/nav";
import { ago, shortDateTime } from "@/lib/format";
import {
  ANALYSIS, CONFIDENCE, SEVERITY, TIER, UNKNOWN_REASON, incidentDetail, readableTitle,
  type Evidence, type IncidentDetail as Detail, type PlanStep,
} from "@/lib/dejafix";

// 장애 상세 = DejaFix API #11(GET /api/incidents/{id}) 한 번으로 그린다. DejaFix 화면을 띄우지 않는다.
// DejaFix가 저장한 리포트·원인·티어를 옮겨 보여 주기만 한다. 실행 버튼은 두지 않는다(DejaFix도 미리보기만).

function Chip({ map, k }: { map: Record<string | number, { label: string; tone: string }>; k: string | number }) {
  const v = map[k] ?? { label: String(k), tone: "bg-ink-100 text-ink-700" };
  return <span className={`chip whitespace-nowrap ${v.tone}`}>{v.label}</span>;
}

function Section({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <section className="card px-6 py-5">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
        <h2 className="font-semibold">{title}</h2>
        {sub && <span className="text-xs text-ink-500">{sub}</span>}
      </div>
      {children}
    </section>
  );
}

/** 근거 ID 칩. 누르면 아래 근거 목록의 그 항목을 연다 */
function EvidenceIds({ ids, known, onPick }: { ids: string[]; known: Set<string>; onPick: (id: string) => void }) {
  if (ids.length === 0) return null;
  return (
    <p className="mt-2 flex flex-wrap items-center gap-1 text-xs text-ink-500">
      근거
      {ids.map((id) => known.has(id)
        ? <button key={id} type="button" className="chip bg-ink-100 font-mono text-ink-700 hover:bg-brand-50" onClick={() => onPick(id)}>{id}</button>
        : <span key={id} className="chip bg-ink-100 font-mono text-ink-500" title="근거 기록 없음">{id}</span>)}
    </p>
  );
}

function EvidenceList({ evidence, openId, onToggle }: { evidence: Evidence[]; openId: string | null; onToggle: (id: string) => void }) {
  if (evidence.length === 0) return <p className="text-sm text-ink-500">기록된 근거가 없습니다.</p>;
  return (
    <ul className="divide-y divide-ink-100 text-sm">
      {evidence.map((e) => {
        const open = openId === e.id;
        return (
          <li key={e.id} id={`ev-${e.id}`} className="py-2">
            <button type="button" aria-expanded={open} onClick={() => onToggle(e.id)} className="flex w-full items-center gap-2 text-left">
              <span className="font-semibold">{e.tool}</span>
              <span className="font-mono text-xs text-ink-500">{e.id}</span>
              {!e.ok && <span className="chip bg-red-50 text-red-700">실패</span>}
              {e.duration_ms !== null && <span className="ml-auto text-xs text-ink-500">{e.duration_ms}ms</span>}
            </button>
            {open && (
              <div className="mt-2 space-y-1 pl-2">
                <code className="block overflow-x-auto bg-app px-3 py-2 text-xs">{JSON.stringify(e.args)}</code>
                <p>{e.result_summary ?? <span className="text-ink-500">확인할 수 없음 · 데이터 없음</span>}</p>
                <p className="text-xs text-ink-500">호출 {shortDateTime(e.called_at)}{e.data_clock && ` · 데이터 시각 ${shortDateTime(e.data_clock)}`}</p>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function StepCard({ step, known, onPick }: { step: PlanStep; known: Set<string>; onPick: (id: string) => void }) {
  const rows: [string, string | null][] = [
    ["대상", step.target],
    ["사전 확인", step.precheck],
    ["예상 효과", step.expected_effect],
    ["위험", step.risk],
    ["되돌리기", step.rollback],
    ["판정 근거", step.rationale],
  ];
  return (
    <li className="border border-ink-100 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-500">{step.seq}단계</span>
        <span className="font-semibold">{step.title}</span>
        <span className="ml-auto"><Chip map={TIER} k={step.tier} /></span>
      </div>
      {step.command_preview && <code className="mt-2 block overflow-x-auto bg-app px-3 py-2 text-xs">{step.command_preview}</code>}
      <dl className="mt-2 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[80px_1fr]">
        {rows.filter(([, v]) => v).map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-ink-500">{k}</dt><dd className="text-ink-700">{v}</dd></div>
        ))}
      </dl>
      <EvidenceIds ids={step.evidence_ids} known={known} onPick={onPick} />
    </li>
  );
}

export default function IncidentDetail() {
  const { id } = useParams<{ id: string }>();
  const num = Number(id);
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openEv, setOpenEv] = useState<string | null>(null);

  useEffect(() => {
    if (!Number.isInteger(num)) return;
    let alive = true;
    setData(null);
    setError(null);
    incidentDetail(num)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "장애 정보를 불러오지 못했습니다."));
    return () => { alive = false; };
  }, [num]);

  // 예전 DejaFix 화면 주소(/infra/reports 등)는 숫자가 아니라 현재 장애로 보낸다
  if (!Number.isInteger(num)) return <Navigate to="/infra" replace />;

  const known = new Set((data?.evidence ?? []).map((e) => e.id));
  const pick = (ev: string) => {
    setOpenEv(ev);
    requestAnimationFrame(() => { document.getElementById(`ev-${ev}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); });
  };
  const i = data?.incident;
  const r = data?.report;

  return (
    <div className="space-y-6 px-8 py-8">
      <Link href="/infra" className="text-sm text-ink-500 hover:text-ink-900">← 현재 장애</Link>

      {error && <p role="alert" className="card border-l-4 border-l-red-500 px-5 py-3 text-sm text-red-700">{error}</p>}
      {!data && !error && <p className="card px-6 py-10 text-center text-sm text-ink-500">장애 정보를 불러오는 중…</p>}

      {i && (
        <header className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Chip map={SEVERITY} k={i.severity.level} />
            <span className={`chip ${i.status === "problem" ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700"}`}>{i.status === "problem" ? "진행 중" : "해결됨"}</span>
            <Chip map={ANALYSIS} k={i.analysis.status} />
            <span className="text-xs text-ink-500">#{i.id}</span>
          </div>
          <h1 className="text-2xl font-semibold" title={i.title}>{readableTitle(i.title)}</h1>
          <p className="flex flex-wrap gap-x-4 text-sm text-ink-500">
            <span>호스트 <b className="font-mono text-ink-900">{i.host.name ?? "확인 중"}</b></span>
            <span>시작 <b className="text-ink-900">{shortDateTime(i.started_at)}</b> ({ago(i.started_at)})</span>
            {i.resolved_at && <span>해결 <b className="text-ink-900">{shortDateTime(i.resolved_at)}</b></span>}
          </p>
          {i.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">{i.tags.map((t) => <span key={`${t.tag}:${t.value}`} className="chip bg-ink-100 text-ink-700">{t.tag}: {t.value}</span>)}</div>
          )}
        </header>
      )}

      {data && !r && (
        <Section title="AI 초동 분석">
          <p className="text-sm text-ink-500">아직 리포트가 없습니다. 분석 상태: {ANALYSIS[i!.analysis.status]?.label ?? i!.analysis.status}</p>
        </Section>
      )}

      {r && (
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-6">
            <Section title="AI 초동 분석" sub={`리포트 v${r.version} · ${shortDateTime(r.created_at)}`}>
              {r.kind === "fallback" && <p className="mb-2"><span className="chip bg-amber-50 text-amber-700">LLM 미연결 — 규칙 기반 최소 리포트</span></p>}
              <p className="whitespace-pre-line text-sm leading-relaxed text-ink-900">{r.summary}</p>
            </Section>

            <Section title="원인 후보" sub="DejaFix AI가 근거와 함께 남긴 순서">
              {r.causes.length === 0 ? <p className="text-sm text-ink-500">원인 후보가 없습니다.</p> : (
                <ol className="space-y-3">
                  {r.causes.map((c) => (
                    <li key={c.rank} className="border-l-4 border-l-brand-500 pl-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs text-ink-500">{c.rank}순위</span>
                        <span className="font-semibold">{c.title}</span>
                        <Chip map={CONFIDENCE} k={c.confidence} />
                      </div>
                      <p className="mt-1 text-sm text-ink-700">{c.reasoning}</p>
                      {c.verify && <p className="mt-1 text-xs text-ink-500">확인 방법: {c.verify}</p>}
                      <EvidenceIds ids={c.evidence_ids} known={known} onPick={pick} />
                    </li>
                  ))}
                </ol>
              )}
            </Section>

            {data.plan && (
              <Section title="변경 계획 미리보기" sub="보여 주기만 합니다 · 실행은 사람이 판단">
                <p className="text-sm font-medium">{data.plan.title}</p>
                {data.plan.goal && <p className="mt-1 text-sm text-ink-700">목표: {data.plan.goal}</p>}
                <p className="mt-1 flex items-center gap-2 text-xs text-ink-500">가장 높은 등급 <Chip map={TIER} k={data.plan.max_tier} /></p>
                {data.plan.assumptions.length > 0 && (
                  <ul className="mt-2 list-disc pl-5 text-xs text-ink-500">{data.plan.assumptions.map((a) => <li key={a}>가정: {a}</li>)}</ul>
                )}
                <ol className="mt-3 space-y-2">{data.steps.map((s) => <StepCard key={s.seq} step={s} known={known} onPick={pick} />)}</ol>
              </Section>
            )}

            <Section title="근거" sub={`DejaFix AI가 부른 도구 ${data.evidence.length}회 · 눌러서 펼치기`}>
              <EvidenceList evidence={data.evidence} openId={openEv} onToggle={(ev) => setOpenEv((cur) => (cur === ev ? null : ev))} />
            </Section>
          </div>

          <aside className="space-y-6">
            {r.facts.length > 0 && (
              <Section title="수치">
                <dl className="space-y-2 text-sm">
                  {r.facts.map((f) => (
                    <div key={f.label} className="flex items-baseline justify-between gap-3">
                      <dt className="text-ink-500">{f.label}</dt>
                      <dd className="text-right font-semibold">
                        {f.value === null
                          ? <span className="text-xs font-normal text-ink-500">확인할 수 없음 · {UNKNOWN_REASON[f.unknown_reason ?? ""] ?? f.unknown_reason ?? "데이터 없음"}</span>
                          : <>{typeof f.value === "number" ? f.value.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) : f.value}{f.unit}</>}
                      </dd>
                    </div>
                  ))}
                </dl>
              </Section>
            )}

            <Section title="영향">
              <dl className="space-y-1 text-sm">
                <div><dt className="inline text-ink-500">호스트 </dt><dd className="inline font-mono">{r.impact.hosts.join(", ") || "—"}</dd></div>
                {r.impact.services.length > 0 && <div><dt className="inline text-ink-500">서비스 </dt><dd className="inline">{r.impact.services.join(", ")}</dd></div>}
                {r.impact.user_impact && <p className="pt-1 text-ink-700">{r.impact.user_impact}</p>}
              </dl>
            </Section>

            {r.unknowns.length > 0 && (
              <Section title="확인할 수 없음">
                <ul className="space-y-1 text-sm">{r.unknowns.map((u) => <li key={u.label}>{u.label} <span className="text-xs text-ink-500">· {UNKNOWN_REASON[u.reason] ?? u.reason}</span></li>)}</ul>
              </Section>
            )}

            {r.next_checks.length > 0 && (
              <Section title="사람이 확인할 것">
                <ul className="list-disc space-y-1 pl-5 text-sm">{r.next_checks.map((c) => <li key={c}>{c}</li>)}</ul>
              </Section>
            )}

            <Section title="메일 발송 기록">
              {data.mail_logs.length === 0 ? <p className="text-sm text-ink-500">보낸 메일이 없습니다.</p> : (
                <ul className="space-y-1 text-xs">
                  {data.mail_logs.map((m) => (
                    <li key={m.id} className="flex flex-wrap gap-x-2">
                      <span className={m.status === "sent" ? "text-emerald-700" : "text-red-700"}>{m.status === "sent" ? "보냄" : m.status}</span>
                      <span>{m.recipient}</span>
                      <span className="text-ink-500">{shortDateTime(m.sent_at ?? m.created_at)}</span>
                      {m.error && <span className="text-red-700">{m.error}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </aside>
        </div>
      )}
    </div>
  );
}
