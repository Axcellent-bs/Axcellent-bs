import { useCallback, useState, type ReactNode } from "react";
import { Skeleton } from "@/components/finance/bits";
import { useVisibleInterval } from "@/lib/visibleInterval";
import { shortDateTime, won } from "@/lib/format";
import { finance, type Review } from "@/lib/finance";
import { holdKo } from "@/lib/marketingLive";
import { BriefMediaMaker } from "./BriefMediaMaker";
import { PublishControlCard } from "./PublishControlCard";
import { VideoProviderCard } from "./VideoProviderCard";
import { CostLimitCard } from "./CostLimitCard";
import { CharacterSheetMaker } from "./CharacterSheetMaker";
import { StoryboardMaker } from "./StoryboardMaker";
import type { CharacterSheetResult } from "@/lib/characterSheet";
import { BRIEFS_LIST_MAX, KIND_SOURCE_KO, briefs, briefsEnabled, briefsError, draftStateKo, handoffReasonKo, mediaFieldKo, mediaKindKo, type StoredBrief } from "@/lib/briefs";

// SNS·미디어 탭. 이미지·영상 생성을 안내하고(SNS 게시 흐름·캐러셀·AI 표시 안내는 Instagram 자동 게시 카드의 물음표),
// 넘어온 기획안은 저장된 미디어 요청으로 미디어 초안을 만들 수 있다(BriefMediaMaker: 사용량 확인 → 생성 → 초안·검사). 게시는 하지 않는다.
// 안전 장치 보류 큐(finance.reviews)에서 읽을 수 있는 마케팅 항목(publish_sns, generate_media)만 읽기 전용으로 보여 준다.
// 보류 결정은 장애·공지·캠페인 탭의 보류 큐에서 한다. 이 탭은 결정하지 않는다.
// 이미지 시트(캐릭터 시트 프롬프트)를 만들면 그 캐릭터 문장을 스토리보드 만들기로 넘겨 쓴다.

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

export function SnsMedia() {
  const [holds, setHolds] = useState<Review[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [handed, setHanded] = useState<StoredBrief[] | null>(null);
  const [handedError, setHandedError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<CharacterSheetResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    if (briefsEnabled) {
      try {
        const list = await briefs.list(BRIEFS_LIST_MAX);
        setHanded(list.items.filter((i) => i.sns_handoff && i.sns_handoff.status !== "not_handed"));
        setHandedError(null);
      } catch (e) {
        const err = e as { status?: number; message?: string; code?: string | null };
        setHandedError(briefsError({ status: err.status, message: err.message ?? "알 수 없는 오류", code: err.code }));
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
  }, []);

  useVisibleInterval(load, REFRESH_MS);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">SNS·미디어</h1>
          <p className="mt-1 text-sm text-ink-500">마케팅 AI가 만드는 이미지·영상과 SNS 게시 흐름입니다. 이 탭에서는 넘어온 기획안의 미디어 초안을 만들 수 있고, 게시와 보류 결정은 하지 않습니다. 보류 결정은 장애·공지 또는 캠페인·쿠폰 탭의 보류 큐에서 합니다.</p>
        </div>
        <button className="btn-ghost ml-auto text-xs" onClick={() => void load()} disabled={loading}>{loading ? "불러오는 중" : "새로고침"}</button>
      </header>

      <PublishControlCard />
      <VideoProviderCard />
      <CostLimitCard />

      <CharacterSheetMaker onResult={setSheet} />
      <StoryboardMaker sheet={sheet} />

      <Section title="받은 기획안에서 넘어온 작업" desc="받은 기획안 탭에서 SNS·미디어로 넘긴 기획안입니다. 넘기면 글 초안과 미디어 요청 초안까지만 만들고, 아래 다음 단계는 사람이 이어서 합니다. '미디어 만들기'를 누르면 예상 비용을 확인한 뒤 저장된 종류·필드로 미디어를 만들고 초안 검사까지 합니다. 게시는 하지 않으며 사람 승인(C 등급)이 따로 필요합니다. 종류와 필드는 받은 기획안 탭에서 바꿀 수 있습니다.">
        <ol className="flex flex-wrap items-center gap-1 text-xs text-ink-700" aria-label="다음 단계">
          {["미디어 생성 (B 등급, 월 사용량 예산 검사)", "초안 검사 (AI 표시·문구·숫자)", "사람 승인 게시 (C 등급)"].map((t, i) => (
            <li key={t} className="flex items-center gap-1">{i > 0 && <span aria-hidden="true">›</span>}<span className="rounded-lg bg-ink-50 px-2 py-1">{i + 1}. {t}</span></li>
          ))}
        </ol>
        {!briefsEnabled ? <Empty>받은 기획안 읽기 연결이 꺼져 있어 넘겨진 목록을 읽을 수 없습니다.</Empty>
          : handedError ? <p className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{handedError}</p>
            : handed === null ? <Skeleton stats={0} lines={2} />
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

      <Section title="사람 결정 대기 (SNS·미디어)" desc="안전 장치 보류 큐에서 마케팅 AI가 요청한 SNS 게시·이미지·영상 만들기를 읽기만 하는 목록입니다. 게시 승인·거절은 위 기획안 카드의 승인 패널에서 하고, 이 목록에서는 누를 수 없습니다.">
        {error ? <p className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">보류 건을 읽지 못했습니다. {error}</p>
          : holds === null ? <Skeleton stats={0} lines={2} />
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
