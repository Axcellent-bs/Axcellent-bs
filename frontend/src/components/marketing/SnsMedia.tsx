import { useCallback, useState, type ReactNode } from "react";
import { Navigate, useSearchParams } from "react-router";
import { Tabs } from "@/components/connect/parts";
import { Skeleton } from "@/components/finance/bits";
import { useVisibleInterval } from "@/lib/visibleInterval";
import { shortDateTime } from "@/lib/format";
import { SNS_HOLD_ACTIONS, finance, type Review } from "@/lib/finance";
import { ActionQueue } from "@/components/finance/SafetyQueue";
import { REQUEST_VIDEOS_PATH } from "@/lib/requestVideoView";
import { BriefMediaMaker } from "./BriefMediaMaker";
import { SnsAccountsCard } from "./SnsAccountsCard";
import { CharacterSheetMaker } from "./CharacterSheetMaker";
import { BRIEFS_LIST_MAX, KIND_SOURCE_KO, briefs, briefsEnabled, briefsError, draftStateKo, handoffReasonKo, mediaFieldKo, mediaKindKo, type StoredBrief } from "@/lib/briefs";

// SNS·미디어 탭. 이미지·영상 생성을 안내하고(SNS 게시 흐름·캐러셀·AI 표시 안내는 Instagram 자동 게시 카드의 물음표),
// 넘어온 기획안은 저장된 미디어 요청으로 미디어 초안을 만들 수 있다(BriefMediaMaker: 사용량 확인 → 생성 → 초안·검사). 게시는 하지 않는다.
// 안전 장치 보류 큐(finance.reviews)에서 읽을 수 있는 마케팅 항목(publish_sns, generate_media)만 읽기 전용으로 보여 준다.
// 보류 결정은 장애·공지·캠페인 탭의 보류 큐에서 한다. 이 탭은 결정하지 않는다.
// 이미지 시트 · 스토리보드 카드(Higgsfield 결과는 카드 안 RenderPanel이 보여 준다).
// 11/23 화면 정리: 카드를 한 줄로 늘어놓지 않고 탭(만들기·넘어온 기획안·승인 대기·계정·연결)으로 나눈다.
// 의뢰 영상은 제작 기록(/marketing/records)에서 본다. 옛 주소(?view=videos·?request=)는 그리로 보낸다.
// 고른 탭은 주소 ?view=에 남는다. 유튜브 연결에서 돌아오면(?youtube=) 계정·연결 탭을 연다.

const REFRESH_MS = 15_000;
const SNS_ACTIONS = SNS_HOLD_ACTIONS;
const fromMarketing = (requester: string) => requester === "coupon" || requester === "marketing";

/** 계정 연결에서 돌아올 때 붙는 표시(유튜브 ?youtube=, Higgsfield ?higgsfield=finish, 인스타 ?instagram=) */
export const OAUTH_RETURN_PARAMS = ["youtube", "higgsfield", "instagram"] as const;

type View = "make" | "briefs" | "holds" | "accounts";
const VIEWS: readonly View[] = ["make", "briefs", "holds", "accounts"];
const isView = (v: string | null): v is View => v !== null && (VIEWS as readonly string[]).includes(v);

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
  const [loading, setLoading] = useState(false);
  const [handed, setHanded] = useState<StoredBrief[] | null>(null);
  const [handedError, setHandedError] = useState<string | null>(null);

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
    } catch {
      setHolds(null); // 탭의 건수만 비운다. 결정 대기 목록은 ActionQueue가 따로 읽고 오류를 보여 준다
    } finally {
      setLoading(false);
    }
  }, []);

  useVisibleInterval(load, REFRESH_MS);

  const [params, setParams] = useSearchParams();
  const asked = params.get("view");
  // 계정 연결(유튜브·Higgsfield·인스타)에서 돌아오면 계정·연결 탭을 연다. 연결 마무리(complete)는 그 탭의
  // SNS 계정 연결 카드가 한다(만들기 탭의 한 줄 요약은 하지 않는다). 11/23 Higgsfield가 만들기 탭으로 돌아와 연결이 안 되던 문제
  const returning = OAUTH_RETURN_PARAMS.some((k) => params.get(k));
  const view: View = isView(asked) ? asked : returning ? "accounts" : "make";
  const pick = (next: View) => setParams((cur) => {
    const out = new URLSearchParams(cur);
    out.set("view", next);
    for (const k of OAUTH_RETURN_PARAMS) out.delete(k);
    return out;
  }, { replace: true });
  const tabs: { id: View; label: string; count?: number }[] = [
    { id: "make", label: "이미지 시트 · 스토리보드" },
    { id: "briefs", label: "넘어온 기획안", count: handed?.length },
    { id: "holds", label: "사람 결정 대기", count: holds?.length },
    { id: "accounts", label: "계정·연결" },
  ];

  const movedRequest = asked === "videos" || params.get("request") !== null;
  if (movedRequest) {
    const request = params.get("request");
    return <Navigate replace to={`${REQUEST_VIDEOS_PATH}${request ? `&request=${encodeURIComponent(request)}` : ""}`} />;
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">SNS·미디어</h1>
          <p className="mt-1 text-sm text-ink-500">이미지 시트·스토리보드로 영상을 만들고 회사 SNS에 올립니다. 게시는 늘 사람 승인을 거칩니다.</p>
        </div>
        <button className="btn-ghost ml-auto text-xs" onClick={() => void load()} disabled={loading}>{loading ? "불러오는 중" : "새로고침"}</button>
      </header>

      <Tabs label="SNS·미디어 보기" items={tabs} value={view} onPick={pick} />

      {view === "make" && (
        <div className="space-y-6" role="tabpanel" aria-label="이미지 시트 · 스토리보드">
          {/* 로그인한 회사의 연결 상태(Higgsfield·유튜브·인스타)를 한 줄로. 바꾸기는 계정·연결 탭 */}
          <SnsAccountsCard compact onManage={() => pick("accounts")} />
          <CharacterSheetMaker />
        </div>
      )}

      {view === "accounts" && (
        <div className="space-y-6" role="tabpanel" aria-label="계정·연결">
          <SnsAccountsCard />
        </div>
      )}

      {view === "briefs" && (
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
      )}

      {view === "holds" && (
      <Section title="사람 결정 대기 (SNS·미디어)" desc="마케팅 AI가 요청한 SNS 게시·이미지·영상 만들기·유튜브 올리기 가운데 사람이 정해야 하는 일입니다. 내용을 보고 진행 또는 멈춤을 누르세요. 요청한 계정과 다른 계정으로만 진행할 수 있어요.">
        <ActionQueue action={SNS_HOLD_ACTIONS} intro="안전 장치가 사람 확인으로 보류한 SNS 게시·이미지·영상 만들기·유튜브 올리기가 여기 모입니다. " />
      </Section>
      )}
    </div>
  );
}
