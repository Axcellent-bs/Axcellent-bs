import { useCallback, useEffect, useState, type ReactNode } from "react";
import { shortDateTime, won } from "@/lib/format";
import { finance, type Review } from "@/lib/finance";
import { holdKo } from "@/lib/marketingLive";
import { BriefMediaMaker } from "./BriefMediaMaker";
import { ApiError } from "@/lib/apiError";
import { mergeHttpErrorKo, mergeSummaryKo, previewDraftIds, skipReasonKo, type MergeAnswer } from "@/lib/outageMerge";
import { BRIEFS_LIST_MAX, KIND_SOURCE_KO, briefs, briefsEnabled, briefsError, draftStateKo, handoffReasonKo, mediaFieldKo, mediaKindKo, outageTriggerKo, type OutageSnsDraft, type StoredBrief } from "@/lib/briefs";

// SNS·미디어 탭. 이미지·영상 생성과 SNS 게시 흐름을 안내하고,
// 넘어온 기획안은 저장된 미디어 요청으로 미디어 초안을 만들 수 있다(BriefMediaMaker: 비용 확인 → 생성 → 초안·검사). 게시는 하지 않는다.
// 안전 장치 보류 큐(finance.reviews)에서 읽을 수 있는 마케팅 항목(publish_sns, generate_media)만 읽기 전용으로 보여 준다.
// 보류 결정은 장애·공지·캠페인 탭의 보류 큐에서 한다. 이 탭은 결정하지 않는다.
// 비용 장부 숫자(오늘·이달 합계, 건당 상한)를 읽는 API는 화면에 연결돼 있지 않아 지어내지 않고 빈 상태로 둔다.

const REFRESH_MS = 15_000;
const SNS_ACTIONS = ["publish_sns", "generate_media"] as const;
const ACTION_NAME: Record<string, string> = { publish_sns: "SNS 게시", generate_media: "이미지·영상 만들기" };
// 캐러셀 게시 요청의 요약("SNS 캐러셀 게시 요청(N장): ...")에서 장수만 읽는다. 다른 요청이면 null이다.
const carouselCount = (summary?: string): number | null => {
  const m = /^SNS 캐러셀 게시 요청\((\d+)장\)/.exec(summary ?? "");
  return m ? Number(m[1]) : null;
};
const fromMarketing = (requester: string) => requester === "coupon" || requester === "marketing";

function Section({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {desc && <p className="mt-1 text-xs text-ink-500">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

const Empty = ({ children }: { children: ReactNode }) => <p className="py-6 text-center text-sm text-ink-500">{children}</p>;
const NOT_WIRED = "아직 화면에 연결되지 않았습니다.";

const PIPELINE: { step: string; who: string; note: string }[] = [
  { step: "초안", who: "마케팅 AI", note: "게시된 공지, 공개 쿠폰 사실, 받은 기획안으로 캡션 초안을 만듭니다(draft_sns_post). AI 생성 표시 문구가 들어갑니다." },
  { step: "검사", who: "마케팅 AI", note: "AI 표시 누락·승인한 문구와의 차이·쿠폰 필수 안내 누락을 코드 규칙으로 점검합니다(check_sns_post)." },
  { step: "사람 승인", who: "사람", note: "SNS 게시(publish_sns)는 C 등급이라 항상 사람이 승인해야 합니다. 기획안 카드의 승인 패널이나 승인 화면에서 승인합니다." },
  { step: "게시", who: "마케팅 AI", note: "승인을 받은 뒤에만 게시합니다. 마케팅 AI가 스스로 승인하는 길은 없습니다." },
];

export function SnsMedia() {
  const [holds, setHolds] = useState<Review[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [handed, setHanded] = useState<StoredBrief[] | null>(null);
  const [handedError, setHandedError] = useState<string | null>(null);
  const [outage, setOutage] = useState<OutageSnsDraft[] | null>(null);
  const [outageError, setOutageError] = useState<string | null>(null);
  const [mergedTotal, setMergedTotal] = useState(0);
  const [showMerged, setShowMerged] = useState(false);
  // 유사 초안 병합: 미리보기(저장 없음) → 사람이 확인 → 저장. busy는 중복 클릭 잠금
  const [mergePreview, setMergePreview] = useState<MergeAnswer | null>(null);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState<string | null>(null);
  const [mergeDone, setMergeDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    if (briefsEnabled) {
      try {
        const list = await briefs.list(BRIEFS_LIST_MAX);
        setHanded(list.items.filter((i) => i.sns_handoff && i.sns_handoff.status !== "not_handed"));
        setHandedError(null);
      } catch (e) {
        const err = e as { status?: number; message?: string };
        setHandedError(briefsError({ status: err.status, message: err.message ?? "알 수 없는 오류" }));
      }
    }
    if (briefsEnabled) {
      try {
        const listed = await briefs.outageSnsDrafts(20, showMerged);
        setOutage(listed.items);
        setMergedTotal(listed.merged_total ?? 0);
        setOutageError(null);
      } catch (e) {
        const err = e as { status?: number; message?: string };
        setOutageError(briefsError({ status: err.status, message: err.message ?? "알 수 없는 오류" }));
      }
    }
    try {
      const all = await finance.reviews(true);
      setHolds(all.filter((r) => r.status === "pending" && fromMarketing(r.requester) && (SNS_ACTIONS as readonly string[]).includes(r.action)));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [showMerged]);

  const runMerge = async (confirm: boolean) => {
    if (mergeBusy) return;
    setMergeBusy(true);
    setMergeError(null);
    try {
      if (!confirm) {
        setMergeDone(null);
        setMergePreview(await briefs.mergeOutageSnsDrafts(true));
      } else if (mergePreview) {
        const done = await briefs.mergeOutageSnsDrafts(false, previewDraftIds(mergePreview));
        setMergePreview(null);
        setMergeDone(mergeSummaryKo(done));
        await load();
      }
    } catch (e) {
      const err = e as ApiError;
      setMergeError(mergeHttpErrorKo(err.status, err.code));
    } finally {
      setMergeBusy(false);
    }
  };

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">SNS·미디어</h1>
          <p className="mt-1 text-sm text-ink-500">마케팅 AI가 만드는 이미지·영상과 SNS 게시 흐름입니다. 이 탭에서는 넘어온 기획안의 미디어 초안을 만들 수 있고, 게시와 보류 결정은 하지 않습니다. 보류 결정은 장애·공지 또는 캠페인·쿠폰 탭의 보류 큐에서 합니다.</p>
        </div>
        <button className="btn-ghost ml-auto text-xs" onClick={() => void load()} disabled={loading}>{loading ? "불러오는 중" : "새로고침"}</button>
      </header>

      <Section title="이미지·영상 만들기" desc="B 등급 일입니다. 안전 장치가 비용(cost_krw)을 코드 규칙으로 검사하고, 적어 낸 비용과 계산이 다르거나 기준을 넘으면 사람 확인으로 보류합니다.">
        <ul className="space-y-1 text-sm text-ink-700">
          <li>생성한 이미지·영상에는 AI 생성 표시를 넣습니다.</li>
          <li>비용은 건당 상한과 하루 합계 기준으로 막습니다. 기준을 넘은 건은 아래 보류 목록에 나옵니다.</li>
        </ul>
        <div className="rounded-2xl border border-ink-100 p-4">
          <p className="text-xs font-medium text-ink-700">비용 장부 (오늘·이달 합계, 건당 상한)</p>
          <Empty>{NOT_WIRED}</Empty>
        </div>
      </Section>

      <Section title="장애 사과·보상 글 초안" desc="장애 때 사과 공지가 나가거나 보상 쿠폰을 드리면 마케팅 AI가 SNS 글 초안을 바로 만들어 둡니다. 초안일 뿐 게시된 글이 아닙니다. 게시는 사람이 승인해야 합니다(C 등급).">
        <ul className="list-disc space-y-1 pl-5 text-xs text-ink-700">
          <li>원인은 확인된 경우에만 쉬운 말로 적고, 확인되지 않았으면 "원인 확인 중"이라고 적습니다.</li>
          <li>내부 용어·숫자·담당자 이름은 넣지 않고, AI 생성 표시와 시연용 가상 서비스 안내가 들어갑니다.</li>
        </ul>
        {briefsEnabled && (
          <div className="space-y-3 rounded-2xl border border-ink-100 p-4" aria-label="유사 초안 병합">
            <div className="flex flex-wrap items-center gap-3">
              <button className="btn-ghost text-xs" onClick={() => void runMerge(false)} disabled={mergeBusy || mergePreview !== null}>{mergeBusy && !mergePreview ? "확인하는 중" : "유사 초안 병합"}</button>
              <p className="text-xs text-ink-500">거의 같은 초안을 대표 1건만 남기고 접습니다. 새 글을 만들지 않고, 접힌 초안은 지우지 않으며, 게시와 비용은 없습니다.</p>
              {(mergedTotal > 0 || showMerged) && (
                <label className="ml-auto flex items-center gap-2 text-xs text-ink-700">
                  <input type="checkbox" checked={showMerged} onChange={(e) => setShowMerged(e.target.checked)} />
                  병합됨 {mergedTotal}건 보기
                </label>
              )}
            </div>
            {mergeError && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{mergeError}</p>}
            {mergeDone && <p role="status" className="rounded-xl bg-brand-50 px-4 py-2 text-sm text-brand-700">{mergeDone}</p>}
            {mergePreview && (
              <div className="space-y-3">
                <p className="text-sm font-medium text-ink-700">미리보기: {mergeSummaryKo(mergePreview)} 아직 저장하지 않았습니다.</p>
                {mergePreview.groups.map((g) => (
                  <div key={g.representative} className="space-y-1 rounded-xl bg-ink-50 p-3 text-xs">
                    <p><span className="chip bg-brand-50 text-brand-700">남는 대표</span> <span className="tabular-nums text-ink-500">{g.representative}</span> · 접히는 초안 {g.merged_from.length}건 <span className="tabular-nums text-ink-500">({g.merged_from.join(", ")})</span></p>
                    <p className="line-clamp-3 whitespace-pre-wrap text-ink-700">{g.representative_caption}</p>
                  </div>
                ))}
                {mergePreview.skipped.length > 0 && (
                  <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-500" aria-label="병합하지 않은 초안">
                    {mergePreview.skipped.map((k) => <li key={k.id}><span className="tabular-nums">{k.id}</span>: {skipReasonKo(k.reason)}</li>)}
                  </ul>
                )}
                <div className="flex gap-2">
                  {mergePreview.groups.length > 0 && <button className="btn-primary text-xs" onClick={() => void runMerge(true)} disabled={mergeBusy}>{mergeBusy ? "병합하는 중" : `${mergePreview.merged_count}건 병합하기`}</button>}
                  <button className="btn-ghost text-xs" onClick={() => setMergePreview(null)} disabled={mergeBusy}>{mergePreview.groups.length > 0 ? "취소" : "닫기"}</button>
                </div>
              </div>
            )}
          </div>
        )}
        {!briefsEnabled ? <Empty>마케팅 읽기 연결이 꺼져 있어 초안 목록을 읽을 수 없습니다.</Empty>
          : outageError ? <p className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{outageError}</p>
            : outage === null ? <Empty>불러오는 중입니다.</Empty>
              : outage.length === 0 ? <Empty>만들어진 초안이 없습니다.</Empty> : (
                <ul className="space-y-3" aria-label="장애 사과·보상 글 초안 목록">
                  {outage.map((d) => (
                    <li key={d.post_draft_id} className="space-y-2 rounded-2xl border border-ink-100 p-4">
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                        <span className="chip bg-brand-50 text-brand-700">{outageTriggerKo(d.trigger)}</span>
                        <span className="chip bg-amber-50 text-amber-800">초안 · 게시 안 됨</span>
                        {d.status === "merged" && <span className="chip bg-ink-50 text-ink-700" title={d.merged_into ?? undefined}>병합됨 · 대표 초안 아래로 접힘</span>}
                        {d.merged_from && d.merged_from.length > 0 && <span className="chip bg-ink-50 text-ink-700">비슷한 초안 {d.merged_from.length}건 병합</span>}
                        <span className="text-ink-500">{d.media_attached ? "미디어가 붙은 초안" : "캡션 초안만 있음 (미디어 없음)"}</span>
                        <span className="ml-auto tabular-nums text-ink-500" title={`초안 ${d.post_draft_id} · 이벤트 ${d.context_id}`}>{shortDateTime(d.created_at)} 만듦</span>
                      </div>
                      <p className="whitespace-pre-wrap text-sm text-ink-700">{d.caption}</p>
                    </li>
                  ))}
                </ul>
              )}
      </Section>

      <Section title="받은 기획안에서 넘어온 작업" desc="받은 기획안 탭에서 SNS·미디어로 넘긴 기획안입니다. 넘기면 글 초안과 미디어 요청 초안까지만 만들고, 아래 다음 단계는 사람이 이어서 합니다. '미디어 만들기'를 누르면 예상 비용을 확인한 뒤 저장된 종류·필드로 미디어를 만들고 초안 검사까지 합니다. 게시는 하지 않으며 사람 승인(C 등급)이 따로 필요합니다. 종류와 필드는 받은 기획안 탭에서 바꿀 수 있습니다.">
        <ol className="flex flex-wrap items-center gap-1 text-xs text-ink-700" aria-label="다음 단계">
          {["미디어 생성 (B 등급, 비용 상한 검사)", "초안 검사 (AI 표시·문구·숫자)", "사람 승인 게시 (C 등급)"].map((t, i) => (
            <li key={t} className="flex items-center gap-1">{i > 0 && <span aria-hidden="true">›</span>}<span className="rounded-lg bg-ink-50 px-2 py-1">{i + 1}. {t}</span></li>
          ))}
        </ol>
        {!briefsEnabled ? <Empty>받은 기획안 읽기 연결이 꺼져 있어 넘겨진 목록을 읽을 수 없습니다.</Empty>
          : handedError ? <p className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{handedError}</p>
            : handed === null ? <Empty>불러오는 중입니다.</Empty>
              : handed.length === 0 ? <Empty>넘겨진 기획안이 없습니다.</Empty> : (
                <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100" aria-label="넘겨진 기획안 목록">
                  {handed.map((i) => {
                    const h = i.sns_handoff!;
                    const m = h.status === "handed" ? h.media_request : null;
                    return (
                      <li key={i.proposal_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-xs">
                        <span className="min-w-0 flex-1 truncate text-sm font-medium">{i.brief.title}</span>
                        <span className="tabular-nums text-ink-500">{i.proposal_id}</span>
                        {h.status === "handed" ? (
                          <>
                            <span className="text-ink-500">{shortDateTime(h.handed_at)} 넘김</span>
                            {h.prepared?.stage === "draft_ready"
                              ? <span className="chip bg-brand-50 text-brand-700" title={h.prepared.post_draft_id ?? undefined}>초안 준비됨 · 사람 승인 대기</span>
                              : <span className="chip bg-brand-50 text-brand-700" title={h.draft_id ?? undefined}>{draftStateKo(h.draft_state)}</span>}
                            {m && <span className="chip bg-ink-50 text-ink-700">{mediaKindKo(m.media_kind)}{m.kind_source ? ` · ${KIND_SOURCE_KO[m.kind_source] ?? m.kind_source}` : ""}</span>}
                            {m && m.missing_fields.length > 0 && <span className="text-ink-500">미디어 요청 필드 미정 {m.missing_fields.length}개</span>}
                          </>
                        ) : <span className="chip bg-amber-50 text-amber-800">{handoffReasonKo(h.reason_code)}</span>}
                        {m && (m.recommendation_reason || Object.keys(m.fields).length > 0) && (
                          <p className="w-full text-ink-500">
                            {m.recommendation_reason && <>AI 추천 {mediaKindKo(m.recommended_media_kind)} · 근거: {m.recommendation_reason}</>}
                            {m.recommendation_reason && Object.keys(m.fields).length > 0 && " · "}
                            {Object.entries(m.fields).map(([name, value]) => `${mediaFieldKo(name)} ${value}${m.field_sources?.[name] === "admin" ? "(관리자)" : ""}`).join(", ")}
                            {m.missing_fields.length > 0 && ` · 미정: ${m.missing_fields.map(mediaFieldKo).join(", ")}`}
                          </p>
                        )}
                        {m && <BriefMediaMaker item={i} onDone={() => void load()} />}
                      </li>
                    );
                  })}
                </ul>
              )}
      </Section>

      <Section title="SNS 게시 흐름" desc="초안에서 게시까지 네 단계입니다. 사람 승인 없이는 게시되지 않습니다.">
        <ol className="grid gap-3 sm:grid-cols-4">
          {PIPELINE.map((p, i) => (
            <li key={p.step} className="space-y-1 rounded-2xl border border-ink-100 p-4">
              <p className="text-xs text-ink-500">{i + 1}단계 · {p.who}</p>
              <p className="text-sm font-semibold">{p.step}</p>
              <p className="text-xs text-ink-700">{p.note}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section title="여러 장 올리기 (캐러셀)" desc="Instagram 한 게시물에 이미지 2장에서 10장까지 묶어 올립니다. 영상을 섞는 것은 아직 지원하지 않습니다.">
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-700">
          <li>장마다 AI 표시 라벨(오른쪽 하단)을 입히고 규격을 검사합니다. 한 장이라도 빠지면 게시 대상이 되지 않습니다.</li>
          <li>생성 비용은 장수만큼 더해 건당·월 상한 안에서만 만듭니다. 상한을 넘으면 한 장도 만들지 않습니다.</li>
          <li>게시(publish_sns)는 여전히 사람 승인입니다. 아래 대기 목록에 장수와 각 이미지 참조가 함께 나옵니다.</li>
        </ul>
      </Section>

      <Section title="AI 표시 라벨 안내" desc="AI 기본법을 적용 대상으로 보고, AI가 만든 이미지·영상·캡션에는 항상 표시합니다.">
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-700">
          <li>이미지·영상 위(오른쪽 하단)에 사람이 읽는 표시 문구를 넣고, 파일에도 기계가 읽는 표시를 함께 남깁니다.</li>
          <li>캡션에도 AI 생성 안내 문구가 들어갑니다. 표시가 빠지면 검사 단계에서 막힙니다.</li>
          <li>계정 프로필 쪽 라벨은 사람이 따로 확인합니다.</li>
        </ul>
      </Section>

      <Section title="사람 결정 대기 (SNS·미디어)" desc="안전 장치 보류 큐에서 마케팅 AI가 요청한 SNS 게시·이미지·영상 만들기를 읽기만 하는 목록입니다. 게시 승인·거절은 위 기획안 카드의 승인 패널에서 하고, 이 목록에서는 누를 수 없습니다.">
        {error ? <p className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">보류 건을 읽지 못했습니다. {error}</p>
          : holds === null ? <Empty>불러오는 중입니다.</Empty>
            : holds.length === 0 ? <Empty>기다리는 건이 없습니다.</Empty> : (
              <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
                {holds.map((r) => (
                  <li key={r.review_id} className="space-y-1 px-4 py-2 text-xs" title={`보류 건 ${r.review_id}`}>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium">{ACTION_NAME[r.action] ?? "기타 요청"}</span>
                      {r.action === "publish_sns" && carouselCount(r.report.summary) != null && <span className="chip">캐러셀 {carouselCount(r.report.summary)}장</span>}
                      {r.report.computed_amount != null && <span className="tabular-nums">{won(r.report.computed_amount)}</span>}
                      {r.report.hold_reason && <span className="chip bg-amber-50 text-amber-800" title={r.report.hold_reason}>{holdKo(r.report.hold_reason)}</span>}
                      <span className="ml-auto text-ink-500">{shortDateTime(r.expires_at)}까지 결정</span>
                    </div>
                    {r.report.summary && <p className="text-ink-700">{r.report.summary}</p>}
                  </li>
                ))}
              </ul>
            )}
      </Section>
    </div>
  );
}
