import { useCallback, useEffect, useState } from "react";
import { ago, shortDateTime } from "@/lib/format";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { FIELD_SOURCE_KO, STATUS_STEPS, briefStatusKo, briefs, briefsError, briefsEnabled, draftStateKo, handoffReasonKo, mediaFieldKo, reviseProblemKo, safeLink, type BriefList, type MediaKindChoice, type StoredBrief } from "@/lib/briefs";
import { KindSelect, MediaRequestEditor, RecommendationLine } from "./MediaRequestEditor";

// 받은 기획안. 기획이 사람 확정을 거쳐 넘긴 기획안을 마케팅이 받은 그대로 보여 준다(읽기만).
// 근거 수치는 데이터분석이 낸 값 그대로 인용하고 이 화면에서 다시 계산하지 않는다.
// 캠페인 시작·거절 버튼은 다음 단계에서 붙인다(예산 입력과 쓰기 경로가 필요하다).
// "SNS·미디어로 넘기기"만 쓰기다: 초안을 만들 뿐 미디어 생성·게시는 하지 않는다.
// 미디어 종류·필드는 마케팅 AI가 기획안에서 추천하고(서버 계산), 관리자가 고르면 그 값이 우선한다.

const REFRESH_MS = 30_000;

function StatusSteps({ current }: { current: string }) {
  return (
    <ol className="flex flex-wrap items-center gap-1 text-xs text-ink-500" aria-label="진행 상태">
      {STATUS_STEPS.map((step, i) => (
        <li key={step} className="flex items-center gap-1">
          {i > 0 && <span aria-hidden="true">›</span>}
          <span className={step === current ? "font-semibold text-brand-700" : ""}>{step}</span>
        </li>
      ))}
    </ol>
  );
}

function HandoffBox({ item, onDone }: { item: StoredBrief; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<MediaKindChoice>("auto");
  const h = item.sns_handoff;
  const handed = h?.status === "handed";
  const rec = item.media_recommendation;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const answer = await briefs.handoff(item.proposal_id, { mediaKind: kind });
      if (answer.output.field) setError(reviseProblemKo(answer.output));
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-ink-100 px-3 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-medium text-ink-700">SNS·미디어로 넘기기</p>
        {h?.status === "handed" && <span className="chip bg-brand-50 text-brand-700">넘김</span>}
        {h?.status === "failed" && <span className="chip bg-amber-50 text-amber-800">넘기지 못함</span>}
        {(!h || h.status === "not_handed") && <span className="chip bg-ink-50 text-ink-700">아직 안 넘김</span>}
        {!handed && (
          <button className="btn-primary ml-auto text-xs" disabled={busy || !HUMAN_TOKEN_SET} onClick={() => void run()} title={HUMAN_TOKEN_SET ? undefined : "사람 토큰이 없어 쓸 수 없습니다"}>
            {busy ? "넘기는 중" : h?.status === "failed" ? "다시 넘기기" : "SNS·미디어로 넘기기"}
          </button>
        )}
      </div>
      <p className="text-xs text-ink-500">넘기면 SNS 글 초안과 미디어 요청 초안만 만듭니다. 이미지·영상 생성(비용)과 SNS 게시(사람 승인)는 자동으로 하지 않습니다. 초안과 게시물에는 AI 생성 표시가 들어갑니다.</p>
      {!handed && (
        <div className="space-y-2 rounded-lg bg-ink-50 px-3 py-2">
          <RecommendationLine rec={rec} />
          <label className="flex flex-wrap items-center gap-2 text-xs" htmlFor={`handoff-kind-${item.proposal_id}`}>
            <span className="text-ink-500">미디어 종류</span>
            <KindSelect id={`handoff-kind-${item.proposal_id}`} value={kind} onChange={setKind} rec={rec} disabled={busy} />
            <span className="text-ink-500">고르지 않으면 AI 추천대로 넘깁니다. 넘긴 뒤에도 종류·필드를 바꿀 수 있습니다.</span>
          </label>
          {rec && rec.field_notes.length > 0 && (
            <ul className="space-y-0.5 text-xs text-ink-700" aria-label="AI가 채울 필드">
              {rec.field_notes.map((n) => (
                <li key={n.field}>
                  <span className="text-ink-500">{mediaFieldKo(n.field)}</span> <b>{n.value ?? "미정"}</b> <span className="text-ink-500">· {FIELD_SOURCE_KO[n.source] ?? n.source} · {n.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!HUMAN_TOKEN_SET && <p className="text-xs text-ink-500">사람 토큰이 설정돼 있지 않아 넘길 수 없습니다.</p>}
      {h?.status === "failed" && <p className="text-xs text-amber-800">{handoffReasonKo(h.reason_code)}</p>}
      {handed && h && (
        <p className="text-xs text-ink-700">
          {shortDateTime(h.handed_at)} 넘김 · 초안 <span className="tabular-nums" title="SNS 게시 초안 번호">{h.draft_id}</span> · {draftStateKo(h.draft_state)} · 다음은 SNS·미디어 탭에서 이어갑니다.
        </p>
      )}
      {handed && h?.media_request && (
        <MediaRequestEditor
          key={`${item.proposal_id}:${h.media_request.media_kind}:${h.media_request.kind_source ?? ""}:${JSON.stringify(h.media_request.fields)}`}
          item={item}
          request={h.media_request}
          onDone={onDone}
        />
      )}
      {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-xs text-red-800">{error}</p>}
    </div>
  );
}

function Detail({ item, onDone }: { item: StoredBrief; onDone: () => void }) {
  const b = item.brief;
  return (
    <article className="space-y-4 rounded-2xl border border-ink-100 p-5">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
        <div>
          <h3 className="text-base font-semibold">{b.title}</h3>
          <p className="mt-1 text-xs text-ink-500">
            <span title="기획안 번호">{item.proposal_id}</span> · <span title="마케팅이 붙인 캠페인 번호">{item.campaign_id}</span> · {shortDateTime(item.received_at)} 받음 · 확정 {b.approved_by}
          </p>
        </div>
        <a className="ml-auto text-xs text-brand-700 underline" href={`/planning/proposals/${encodeURIComponent(item.proposal_id)}`}>기획 화면에서 보기</a>
      </div>
      <StatusSteps current={briefStatusKo(item.status)} />

      <dl className="space-y-3 text-sm">
        <div><dt className="text-xs text-ink-500">무엇을 하나</dt><dd className="mt-0.5 whitespace-pre-wrap">{b.idea}</dd></div>
        <div><dt className="text-xs text-ink-500">왜 하나</dt><dd className="mt-0.5 whitespace-pre-wrap">{b.why}</dd></div>
        <div>
          <dt className="text-xs text-ink-500">목표 (기획이 정함)</dt>
          <dd className="mt-0.5">
            {b.target ? <><b>{b.target.label} {b.target.value}</b>{b.target.measure && <span className="text-ink-500"> · 잴 때 쓰는 기능 {b.target.measure}</span>}</> : "확인할 수 없음"}
          </dd>
        </div>
      </dl>

      <div>
        <p className="text-xs text-ink-500">근거 (데이터분석이 낸 값 그대로)</p>
        {b.facts.length === 0 ? <p className="mt-1 text-sm text-ink-500">넘어온 근거가 없습니다.</p> : (
          <table className="mt-1 w-full text-left text-xs">
            <thead className="text-ink-500"><tr>{["항목", "값", "출처"].map((h) => <th key={h} className="px-2 py-1 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-ink-100">
              {b.facts.map((f, i) => {
                const href = safeLink(f.link);
                return (
                  <tr key={`${f.label}-${i}`}>
                    <td className="px-2 py-1">{f.label}</td>
                    <td className="px-2 py-1 font-medium tabular-nums">{f.value}</td>
                    <td className="px-2 py-1">{href ? <a className="text-brand-700 underline" href={href} target="_blank" rel="noreferrer noopener" title={f.source}>출처 보기</a> : <span title={f.source}>{f.source}</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <div className="flex gap-2"><dt className="text-xs text-ink-500">형식</dt><dd>{b.format ?? "정하지 않음"}</dd></div>
        <div className="flex gap-2"><dt className="text-xs text-ink-500">상품</dt><dd>{b.products.length ? b.products.join(", ") : "정하지 않음"}</dd></div>
      </dl>
      {b.note && <p className="rounded-lg bg-ink-50 px-3 py-2 text-xs"><span className="text-ink-500">메모 </span>{b.note}</p>}

      <HandoffBox key={item.proposal_id} item={item} onDone={onDone} />

      {b.body_md && (
        <details className="rounded-lg border border-ink-100 px-3 py-2 text-xs">
          <summary className="cursor-pointer font-medium text-ink-700">기획안 본문 보기</summary>
          <pre className="mt-2 whitespace-pre-wrap font-sans text-ink-700">{b.body_md}</pre>
          <p className="mt-2 text-ink-500">사람이 고친 기획안 전체입니다. 설명용이고 판단은 위 근거와 목표만 씁니다.</p>
        </details>
      )}
    </article>
  );
}

export function ReceivedBriefs() {
  const [list, setList] = useState<BriefList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setList(await briefs.list());
      setError(null);
    } catch (e) {
      const err = e as { status?: number; message?: string };
      setError(briefsError({ status: err.status, message: err.message ?? "알 수 없는 오류" }));
    }
  }, []);

  useEffect(() => {
    if (!briefsEnabled) return;
    void load();
    const timer = window.setInterval(() => { void load(); }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const items = list?.items ?? [];
  const current = items.find((i) => i.proposal_id === selected) ?? items[0] ?? null;

  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">받은 기획안 {list && <span className="text-sm font-normal text-ink-500">{list.total}건</span>}</h2>
        <p className="mt-1 text-xs text-ink-500">기획이 사람 확정을 거쳐 넘긴 기획안입니다. 읽기만 하며, 캠페인 시작은 이 화면의 캠페인 칸에서 합니다. 서버를 다시 배포하면 목록이 비어 기획이 다시 보내야 할 수 있습니다.</p>
      </div>

      {!briefsEnabled && <p className="py-4 text-center text-sm text-ink-500">받은 기획안 읽기 연결이 꺼져 있습니다. 열람 토큰을 설정해 주세요.</p>}
      {error && <p className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{error}</p>}
      {briefsEnabled && !list && !error && <p className="text-sm text-ink-500">불러오는 중입니다.</p>}
      {list && items.length === 0 && <p className="py-6 text-center text-sm text-ink-500">{list.unknown_reason ?? "받은 기획안이 없습니다."}</p>}

      {items.length > 0 && (
        <>
          <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100" aria-label="받은 기획안 목록">
            {items.map((i) => {
              const on = current?.proposal_id === i.proposal_id;
              return (
                <li key={i.proposal_id}>
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => setSelected(i.proposal_id)}
                    className={`flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-left text-xs hover:bg-ink-50 ${on ? "bg-brand-50" : ""}`}
                  >
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{i.brief.title}</span>
                    <span className="tabular-nums text-ink-500">{i.proposal_id}</span>
                    <span>{i.brief.target ? `${i.brief.target.label} ${i.brief.target.value}` : "목표 없음"}</span>
                    <span className="chip bg-ink-50 text-ink-700">{briefStatusKo(i.status)}</span>
                    <span className="text-ink-500" title={shortDateTime(i.received_at)}>{ago(i.received_at)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {current && <Detail item={current} onDone={() => void load()} />}
        </>
      )}
    </section>
  );
}
