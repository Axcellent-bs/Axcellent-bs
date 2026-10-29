import { useCallback, useEffect, useState } from "react";
import { Loading } from "@/components/admin/bits";
import { SupportOverviewCard } from "@/components/admin/SupportOverviewCard";
import {
  GRADE, INCIDENT_STATUS, STEP_STATUS, adminRelay, clock, deptKo, relayEnabled, relayError,
  type Incident,
} from "@/lib/adminRelay";
import { incidentTitle, stepLabel } from "@/lib/agentChat";
import { ago } from "@/lib/format";
import { recordState } from "@/lib/incidentRecord";
import { Link, useSearchParams } from "@/lib/nav";
import { CompanyName } from "@/demo/CompanyName";

// 총괄 화면 (재무 AI 화면과 같은 짜임): 머리말 → 요약 카드 → 사건 목록과 상세.
// 고객사에서 문제가 터지면 총괄이 사건 하나로 묶고 부서에 나눠 맡긴다. 이 화면은 읽기 전용이다.
const POLL_MS = 10_000;

// ---------------------------------------------------------------- 사람이 읽는 말
// 총괄은 단계 이름·메모·진행 기록을 코드 섞인 문장으로 준다("원인 진단 (infra): infra에 요청"). 화면에서는 사람 말로 바꾸고
// 코드는 마우스를 올리면(title) 보이게 둔다.
const DEPT_SAY: Record<string, string> = {
  infra: "장애 대응 AI", data: "데이터분석 AI", planning: "기획개발 AI", marketing: "마케팅 AI",
  support: "고객지원 AI", finance: "재무 AI", hr: "인사 AI", orchestrator: "총괄 AI",
};
const STEP_KO: Record<string, string> = {
  rollback: "배포 되돌리기", recovery: "복구 확인", recovered_draft: "정상화 공지 초안", diagnose: "원인 진단",
  impact: "영향 분석", notice: "점검 공지", compensation: "피해 고객 보상",
  similar_cases: "비슷한 과거 사건 찾기", resolution: "해결 내용 기록",
  cost: "배송비·비용 기록 확인", record: "처리 결과 기록", verify: "인사 기록 읽어 확인",
};
const NOTE_KO: Record<string, string> = {
  undetermined: "원인을 특정하지 못함", requested: "보상을 요청함", card_created: "기록을 남김",
  listed: "인사 기록에서 확인함", partial: "일부만 확인함", candidate: "후보 기록으로 저장함 (사람 확인 대기)",
  found: "비슷한 과거 사건을 찾음", none: "비슷한 과거 사건 없음", recorded: "해결 내용을 남김", duplicate: "이미 남긴 해결 내용",
  no_card: "해결 내용을 남길 기록이 없음",
  "failed: gate_rejected": "안전 관문이 막음", recovered: "복구됨", not_recovered: "아직 복구 안 됨",
};
const STATUS_SAY: Record<string, string> = { degraded: "사람 확인 필요", resolved: "해결됨", open: "AI가 처리 중" };

/** 단계 메모: 코드면 이름표로, "선행 단계 미완료(rollback)"의 괄호 코드도 바꾼다 */
function noteKo(note: string): string {
  if (!note) return "–";
  if (NOTE_KO[note]) return NOTE_KO[note];
  return note.replace(/\(([a-z_]+)\)/g, (_, k: string) => `(${STEP_KO[k] ?? k})`);
}

/** 진행 기록 한 줄 */
function eventKo(message: string, inc: Incident): string {
  if (message.startsWith("이벤트 수신: shop.degraded")) return "캣북스 쇼핑몰에서 장애 신호를 받음";
  if (message.startsWith("이벤트 수신: shop.recovered")) return "캣북스 쇼핑몰에서 복구 신호를 받음";
  if (message.startsWith("이벤트 수신: finance.anomaly_detected")) return "재무 AI에서 사후 감사 이상 신호를 받음";
  if (message.startsWith("이벤트 수신: support.inquiry_resolved")) return "고객지원 AI에서 문의 해결 신호를 받음";
  if (message.startsWith("사건 시작:")) return `사건 시작: ${incidentTitle(inc)}`;
  const end = /^사건 종료: (\w+)/.exec(message);
  if (end) return `사건 종료: ${STATUS_SAY[end[1]] ?? end[1]}`;
  // 괄호 속 단계 코드는 먼저 이름으로 바꾼다 (stepLabel이 영어 꼬리표로 보고 지우지 않게)
  const named = message
    .replace(/notice_id 보관 \([^)]*\)/, "게시한 공지 번호를 기록함")
    .replace(/미완료\(([a-z_]+)\)/g, (_, k: string) => `미완료: ${STEP_KO[k] ?? k}`);
  return stepLabel(named)
    .replace(/\s+:/g, ":")
    .replace(/(\w+)에 요청/, (_, d: string) => `${DEPT_SAY[d] ?? d}에게 맡김`);
}

export default function OrchestratorPage() {
  const ctxParam = useSearchParams().get("ctx"); // 결정 기록 화면에서 "총괄 사건에서 보기"로 들어올 때
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [selected, setSelected] = useState<string | null>(ctxParam);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    if (!relayEnabled) return;
    try {
      const { data } = await adminRelay.incidents();
      setIncidents(data); setError(null); setUpdatedAt(new Date());
      setSelected((cur) => (cur && data.some((i) => i.context_id === cur) ? cur : data.at(-1)?.context_id ?? null));
    } catch (e) { setError(relayError(e as { status?: number; message: string })); }
  }, []);
  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  if (!relayEnabled) return <Loading error="총괄 읽기 연결이 꺼져 있습니다. 관리자 토큰(VITE_SHOP_ADMIN_TOKEN)이 없습니다. 화면을 bash backend/scripts/run_web.sh 로 다시 켜 주세요(run_all.sh가 만든 backend/.data/web.env 값을 넘깁니다)." />;
  if (!incidents) return <Loading error={error} what="총괄 사건을 불러오는 중…" />;

  const list = [...incidents].reverse();
  const current = list.find((i) => i.context_id === selected) ?? null;
  const open = incidents.filter((i) => i.status === "open").length;
  const holds = incidents.reduce((n, i) => n + i.pending_holds.length, 0);
  const tone = holds ? "text-amber-800" : "text-emerald-800";

  return (
    <div className="space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">총괄·사건</h1>
          <p className="mt-1 text-sm text-ink-500"><CompanyName />에서 문제가 터지면 총괄 에이전트가 사건 하나로 묶고, 7개 부서에 일을 나눠 맡깁니다. 사람 승인이 필요한 일만 사람에게 올립니다. 이 화면은 읽기 전용입니다.</p>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <button className="btn-ghost" onClick={load}>새로고침{updatedAt ? ` · ${clock(updatedAt.toISOString())}` : ""}</button>
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</p>}

      {/* 요약: 화면에서 가장 중요한 곳 */}
      <section aria-labelledby="orch-summary" className={`rounded-[28px] p-6 ring-1 ${holds ? "bg-amber-50 ring-amber-100" : "bg-emerald-50 ring-emerald-100"}`}>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
          <div>
            <h2 id="orch-summary" className={`text-sm font-semibold ${tone}`}>사람 승인을 기다리는 일</h2>
            <p className={`mt-1 text-4xl font-bold tabular-nums ${tone}`}>{holds ? `${holds}건` : "없음"}</p>
          </div>
          <dl className={`flex gap-8 pb-1 text-sm ${tone}`}>
            <div><dt className="text-xs opacity-80">전체 사건</dt><dd className="text-lg font-semibold tabular-nums">{incidents.length}건</dd></div>
            <div><dt className="text-xs opacity-80">진행 중</dt><dd className="text-lg font-semibold tabular-nums">{open}건</dd></div>
          </dl>
          <p className={`ml-auto max-w-sm pb-1 text-xs ${tone}`}>승인·거절은 총괄이 하지 않고 사람이 안전 관문 보류 큐에서 합니다. 대부분의 단계는 총괄이 규칙대로 부서에 맡겨 끝냅니다.</p>
        </div>
      </section>

      <SupportOverviewCard />

      {list.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-300 px-5 py-8 text-center text-sm text-ink-500">
          아직 사건이 없습니다. <CompanyName />에 장애가 생기면 총괄 AI가 사건을 열고 여기에 나타납니다.
        </p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
          <ul className="space-y-2" aria-label="사건 목록">
            {list.map((i) => (
              <li key={i.context_id}>
                <button onClick={() => setSelected(i.context_id)}
                  className={`w-full rounded-2xl border p-3 text-left text-sm transition ${i.context_id === selected ? "border-brand-500 bg-brand-50" : "border-ink-100 bg-surface hover:border-brand-500"}`}>
                  <span className="flex items-center justify-between gap-2">
                    <span className={`chip ${INCIDENT_STATUS[i.status].tone}`}>{INCIDENT_STATUS[i.status].label}</span>
                    <span className="text-xs text-ink-500">{ago(i.opened_at)}</span>
                  </span>
                  <span className="mt-1.5 block truncate font-semibold" title={i.title}>{incidentTitle(i)}</span>
                  <span className="block truncate text-xs text-ink-500" title={`사건 번호 ${i.context_id}`}>{clock(i.opened_at)} 시작</span>
                </button>
              </li>
            ))}
          </ul>
          {current && <IncidentDetail incident={current} />}
        </div>
      )}
    </div>
  );
}

interface PastCase {
  card_id: string; title: string; summary: string; similarity: number; resolved: boolean;
  resolution?: { outcome: string; root_cause: string | null; action: string | null; minutes_to_resolve: number | null } | null;
}
const OUTCOME_KO: Record<string, string> = { resolved: "해결됨", workaround: "임시 조치", unresolved: "미해결" };

/** 사건이 열릴 때 인사 AI가 찾은 비슷한 과거 사건. 저장된 해결 내용만 보여 주고 추천은 하지 않는다 */
function SimilarCases({ incident }: { incident: Incident }) {
  const step = incident.steps.find((s) => s.skill === "find_similar_cases");
  if (!step || step.status === "pending" || step.status === "running") return null;
  const cases = (step.output?.cases as PastCase[] | undefined) ?? [];
  return (
    <section className="card space-y-2 p-5">
      <h3 className="text-[15px] font-semibold">비슷한 과거 사건</h3>
      {step.status !== "completed" ? (
        <p className="text-sm text-ink-500">인사 AI에서 과거 사건을 찾지 못했습니다. 사건 처리에는 영향이 없습니다.</p>
      ) : !cases.length ? (
        <p className="text-sm text-ink-500">비슷한 과거 사건이 없습니다.</p>
      ) : (
        <ul className="space-y-3">
          {cases.map((c) => (
            <li key={c.card_id} className="rounded-xl border border-ink-100 p-3 text-sm">
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{c.title}</span>
                <span className="chip bg-ink-100 text-ink-700 tabular-nums">유사도 {Math.round(c.similarity * 100)}%</span>
                <span className={`chip ${c.resolution?.outcome === "resolved" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>{c.resolution ? OUTCOME_KO[c.resolution.outcome] ?? c.resolution.outcome : "해결 기록 없음"}</span>
              </p>
              {c.resolution && (
                <dl className="mt-2 grid grid-cols-[4rem_1fr] gap-x-3 gap-y-1 text-xs text-ink-700">
                  <dt className="text-ink-500">결과</dt><dd>{OUTCOME_KO[c.resolution.outcome] ?? c.resolution.outcome}</dd>
                  <dt className="text-ink-500">원인</dt><dd>{c.resolution.root_cause ?? "확인할 수 없음"}</dd>
                  <dt className="text-ink-500">조치</dt><dd>{c.resolution.action ?? "확인할 수 없음"}</dd>
                  <dt className="text-ink-500">소요 시간</dt><dd>{c.resolution.minutes_to_resolve != null ? `${c.resolution.minutes_to_resolve}분` : "확인할 수 없음"}</dd>
                </dl>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-ink-500">참고용입니다. 이 결과로 자동 조치하지 않습니다.</p>
    </section>
  );
}

/** 과거 해결 기록에서 가져온 원인 후보와 조치. 사람이 확인할 참고이고 자동으로 실행하지 않는다 */
function PastRecommendation({ incident }: { incident: Incident }) {
  const r = incident.recommendation;
  if (!r) return null;
  return (
    <section className="card space-y-2 p-5">
      <h3 className="text-[15px] font-semibold">과거 근거 기반 추천</h3>
      {r.status !== "found" ? (
        <p className="text-sm text-ink-500">추천할 과거 근거가 없습니다. {r.reason}.</p>
      ) : (
        <>
          <p className="text-sm">
            <span className="font-medium">{r.title}</span>
            <span className="chip ml-2 bg-ink-100 text-ink-700 tabular-nums">유사도 {Math.round((r.similarity ?? 0) * 100)}%</span>
          </p>
          <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="text-ink-500">원인 후보</dt><dd>{r.root_cause ?? "확인할 수 없음"}</dd>
            <dt className="text-ink-500">그때 한 조치</dt><dd>{r.action ?? "확인할 수 없음"}</dd>
            <dt className="text-ink-500">그때 소요 시간</dt><dd>{r.minutes_to_resolve != null ? `${r.minutes_to_resolve}분` : "확인할 수 없음"}</dd>
          </dl>
          <p className="text-xs text-ink-500">{r.caveat}</p>
        </>
      )}
      <p className="text-xs text-ink-500">참고용입니다. 이 추천으로 자동 조치하지 않고, 조치는 기존 안전 관문과 사람 승인을 따릅니다.</p>
    </section>
  );
}

function IncidentDetail({ incident }: { incident: Incident }) {
  const shared = Object.entries(incident.shared ?? {});
  const done = incident.steps.filter((s) => s.status === "completed").length;
  const record = recordState(incident);
  return (
    <div className="min-w-0 space-y-5">
      <section className="card space-y-3 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold" title={incident.title}>{incidentTitle(incident)}</h2>
          <span className={`chip ${INCIDENT_STATUS[incident.status].tone}`}>{INCIDENT_STATUS[incident.status].label}</span>
          <span className="ml-auto text-xs tabular-nums text-ink-500">단계 {done}/{incident.steps.length} 완료</span>
        </div>
        <p className="text-xs text-ink-500" title={`사건 번호 ${incident.context_id}`}>시작 {clock(incident.opened_at)} · 종료 {clock(incident.closed_at)}</p>
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className={`chip ${record.tone}`}>{record.label}</span>
          {record.step && <Link href={`/ops?ctx=${encodeURIComponent(incident.context_id)}`} className="text-xs font-semibold text-brand-600 underline underline-offset-2">결정 기록에서 보기 →</Link>}
        </p>
        {!!shared.length && (
          <ul className="flex flex-wrap gap-2 text-sm">
            {shared.map(([key, value]) => (
              <li key={key} className="chip bg-emerald-50 text-emerald-700" title={`${key} = ${String(value)}`}>
                {key === "notice_id" ? "점검 공지를 쇼핑몰에 게시함" : key === "recovered_notice_id" ? "정상화 공지를 쇼핑몰에 게시함" : key}
              </li>
            ))}
          </ul>
        )}
        {!!incident.pending_holds.length && (
          <div className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">
            <p className="font-semibold">사람 승인 대기 {incident.pending_holds.length}건</p>
            <ul className="mt-1 list-inside list-disc">{incident.pending_holds.map((h) => <li key={h.step_id}>{stepLabel(h.label)}</li>)}</ul>
          </div>
        )}
      </section>

      <SimilarCases incident={incident} />
      <PastRecommendation incident={incident} />

      <section className="card overflow-x-auto p-5">
        <h3 className="mb-3 text-[15px] font-semibold">단계</h3>
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-ink-500">
            <tr><th className="py-1 pr-3 font-medium">단계</th><th className="pr-3 font-medium">맡은 AI</th><th className="pr-3 font-medium">상태</th><th className="pr-3 font-medium">시도</th><th className="font-medium">메모</th></tr>
          </thead>
          <tbody>
            {incident.steps.map((s) => (
              <tr key={s.step_id} className="border-t border-ink-100 align-top">
                <td className="py-2 pr-3"><span className="font-medium" title={s.skill}>{stepLabel(s.label)}</span></td>
                <td className="whitespace-nowrap py-2 pr-3">
                  {s.dept ? DEPT_SAY[s.dept] ?? deptKo(s.dept) : "–"}
                  {s.transferred_to && <span className="block text-xs text-ink-500">→ {DEPT_SAY[s.transferred_to] ?? deptKo(s.transferred_to)}에게 넘김</span>}
                </td>
                <td className="whitespace-nowrap py-2 pr-3">
                  <span className={`chip ${(STEP_STATUS[s.status] ?? STEP_STATUS.pending).tone}`}>{(STEP_STATUS[s.status] ?? { label: s.status }).label}</span>
                  {s.grade && <span className="ml-1 text-[11px] text-ink-500">{GRADE[s.grade]?.label ?? s.grade}</span>}
                </td>
                <td className="py-2 pr-3 tabular-nums">{s.attempts || "–"}</td>
                <td className="py-2 text-xs text-ink-500" title={s.note || undefined}>{noteKo(s.note)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {!!incident.summary.length && (
        <section className="card p-5">
          <h3 className="mb-2 text-[15px] font-semibold">요약</h3>
          <ul className="list-inside list-disc space-y-0.5 text-sm">{incident.summary.map((line) => <li key={line} title={line}>{eventKo(line.replace(/\s*\[[^\]]*\]/g, ""), incident)}</li>)}</ul>
        </section>
      )}

      <section className="card p-5">
        <h3 className="mb-2 text-[15px] font-semibold">진행 기록</h3>
        <ol className="space-y-1 text-sm">
          {incident.events.map((e, idx) => (
            <li key={`${e.at}-${idx}`} className="flex gap-3"><span className="w-16 shrink-0 text-xs tabular-nums text-ink-500">{clock(e.at)}</span><span title={e.message}>{eventKo(e.message, incident)}</span></li>
          ))}
        </ol>
      </section>
    </div>
  );
}
