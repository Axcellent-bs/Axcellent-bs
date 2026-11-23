import { useCallback, useEffect, useState } from "react";
import { KEY_LABEL, keyHintKo, keyResultKo, type CompanyAiAdmin, type KeyName } from "@/lib/companyAi";
import { deleteCompanyAiKey, errText, getCompanyAi, putCompanyAiKey, youtubeConnectUrl, youtubeDisconnect } from "@/lib/companyAiApi";
import { fetchHiggsfieldStatus, type HiggsfieldStatus } from "@/lib/higgsfield";
import { fetchYoutubeStatus, type YoutubeStatus } from "@/lib/youtube";
import { useVisibleInterval } from "@/lib/visibleInterval";

// 마케팅 › SNS·미디어의 "SNS 계정 연결". 회사마다 자기 Higgsfield·유튜브·인스타 계정을 쓴다(로그인한 회사 기준, 11/23).
// - 연결 상태는 서버가 실제로 확인한 값(Higgsfield: 키를 한 번 보여 봄, 유튜브: 채널 이름을 읽어 봄). 1분마다 다시 본다
// - 값은 그 회사의 키 저장소에만 들어간다(tenant=회사 ID). 화면에는 끝 4자리만 보인다
// - Higgsfield: 회사 키(`<ID>:<SECRET>`)를 넣는다. 없으면 설치 기본 키를 쓴다
// - 유튜브: [유튜브 채널 연결] → 구글 동의 화면(채널 주인 계정) → 돌아오면 ?youtube=connected
// - 인스타: Meta에서 받은 토큰·계정 ID. 게시는 늘 사람 승인을 거친다(자동 게시 없음)
// - 바꾸기는 고객사 관리자만. compact면 상태 한 줄 + [계정·연결에서 바꾸기]만 보인다

const IG_TOKEN: KeyName = "MARKETING_IG_ACCESS_TOKEN";
const IG_USER: KeyName = "MARKETING_IG_USER_ID";
const YT: KeyName = "MARKETING_YOUTUBE_REFRESH_TOKEN";
const HF: KeyName = "MARKETING_HIGGSFIELD_API_KEY";
const REFRESH_MS = 60_000;
const BACK_KO: Record<string, string> = {
  connected: "유튜브 채널을 연결했어요",
  denied: "구글 동의를 취소해서 연결하지 않았어요",
  bad_state: "연결 시간이 지났거나 주소가 맞지 않아요. 다시 눌러 주세요",
  app_missing: "서버에 AXcellent 유튜브 앱 설정이 없어요",
  google_failed: "구글에 닿지 않았어요. 잠시 뒤 다시 해 주세요",
  no_refresh_token: "구글이 연결 값을 주지 않았어요. 구글 계정의 앱 권한에서 AXcellent를 지우고 다시 연결해 주세요",
  store_failed: "키 저장소에 넣지 못했어요(키 저장소가 꺼져 있을 수 있어요)",
};
type Tone = "ok" | "bad" | "wait" | "off";
const DOT: Record<Tone, string> = { ok: "bg-emerald-500", bad: "bg-red-500", wait: "bg-amber-500", off: "bg-ink-300" };

function hfView(s: HiggsfieldStatus | null): { tone: Tone; label: string } {
  if (!s) return { tone: "off", label: "확인 중" };
  if (s.connection === "ok") return { tone: "ok", label: "연결됨" };
  if (s.connection === "invalid_key") return { tone: "bad", label: "키 거절" };
  if (s.connection === "unreachable") return { tone: "wait", label: "닿지 않음" };
  return { tone: "off", label: "연결 전" };
}
function ytView(s: YoutubeStatus | null): { tone: Tone; label: string } {
  if (!s) return { tone: "off", label: "확인 중" };
  if (s.connection === "ok") return { tone: "ok", label: s.channel ? `연결됨 · ${s.channel}` : "연결됨" };
  if (s.connection === "invalid_credentials") return { tone: "bad", label: "다시 연결 필요" };
  if (s.connection === "error") return { tone: "wait", label: "닿지 않음" };
  return { tone: "off", label: "연결 전" };
}

function Status({ name, tone, label }: { name: string; tone: Tone; label: string }) {
  return (
    <span className="flex items-center gap-2 text-sm text-ink-900">
      <span className={`inline-block h-2.5 w-2.5 rounded-full ${DOT[tone]}`} aria-hidden="true" />
      <span className="font-medium">{name}</span>
      <span className="text-ink-700">{label}</span>
    </span>
  );
}

export function SnsAccountsCard({ compact = false, onManage }: { compact?: boolean; onManage?: () => void }) {
  const [admin, setAdmin] = useState<CompanyAiAdmin | null>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [hf, setHf] = useState<HiggsfieldStatus | null>(null);
  const [yt, setYt] = useState<YoutubeStatus | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hfKey, setHfKey] = useState("");
  const [token, setToken] = useState("");
  const [userId, setUserId] = useState("");

  const loadStatus = useCallback(async () => {
    const [h, y] = await Promise.allSettled([fetchHiggsfieldStatus(), fetchYoutubeStatus()]);
    if (h.status === "fulfilled") setHf(h.value);
    if (y.status === "fulfilled") setYt(y.value);
  }, []);
  const loadAdmin = useCallback(async () => {
    try {
      setAdmin(await getCompanyAi());
      setDenied(null);
    } catch (e) {
      setDenied(errText(e));
    }
  }, []);

  useVisibleInterval(loadStatus, REFRESH_MS);
  useEffect(() => {
    if (compact) return;
    void loadAdmin();
    const back = new URLSearchParams(window.location.search).get("youtube");
    if (back) setMsg(BACK_KO[back] ?? "유튜브 연결을 마치지 못했어요");
  }, [compact, loadAdmin]);

  const row = (name: KeyName) => admin?.keys[name];
  const igOn = !!row(IG_TOKEN)?.has_key && !!row(IG_USER)?.has_key;
  const admins = admin?.approval.admins ?? 1;
  const blocked = !admin?.secret_store.enabled;
  const hfLook = hfView(hf);
  const ytLook = ytView(yt);

  if (compact) {
    return (
      <section className="card flex flex-wrap items-center gap-x-6 gap-y-2 px-6 py-4" aria-label="SNS 계정 연결 상태" aria-live="polite">
        <h2 className="text-sm font-semibold">SNS 계정 연결</h2>
        <Status name="Higgsfield" {...hfLook} />
        <Status name="유튜브" {...ytLook} />
        {onManage && <button type="button" className="btn-ghost ml-auto text-xs" onClick={onManage}>계정·연결에서 바꾸기</button>}
      </section>
    );
  }

  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true);
    setMsg(null);
    try {
      const out = await fn();
      if (out) setMsg(out);
      await Promise.all([loadAdmin(), loadStatus()]);
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  };
  const saveHf = async () => {
    const out = keyResultKo(await putCompanyAiKey(HF, hfKey.trim()), admins);
    setHfKey("");
    return `${KEY_LABEL[HF]}: ${out}`;
  };
  const saveIg = async () => {
    const parts: string[] = [];
    if (token.trim()) parts.push(`${KEY_LABEL[IG_TOKEN]}: ${keyResultKo(await putCompanyAiKey(IG_TOKEN, token.trim()), admins)}`);
    if (userId.trim()) parts.push(`${KEY_LABEL[IG_USER]}: ${keyResultKo(await putCompanyAiKey(IG_USER, userId.trim()), admins)}`);
    setToken("");
    setUserId("");
    return parts.join(" ");
  };
  const dropIg = async () => {
    await deleteCompanyAiKey(IG_TOKEN, "인스타 연결 끊기");
    await deleteCompanyAiKey(IG_USER, "인스타 연결 끊기");
    return "인스타 연결 끊기를 요청했어요";
  };
  const canEdit = !denied && !!admin;

  return (
    <section className="card space-y-4 px-6 py-5" aria-labelledby="sns-accounts-title">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="sns-accounts-title" className="text-sm font-semibold">SNS 계정 연결</h2>
        {admin?.company && <span className="chip bg-ink-50 text-ink-700">{admin.company.name}</span>}
        <span className="text-xs text-ink-500">회사마다 자기 계정을 써요. 게시·업로드는 늘 사람 승인을 거쳐요</span>
      </div>
      {denied && <p className="text-xs text-ink-700">연결을 바꾸는 건 고객사 관리자만 할 수 있어요. ({denied})</p>}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-2 rounded-xl border border-ink-100 p-4">
          <Status name="Higgsfield" {...hfLook} />
          <p className="text-xs text-ink-500">스토리보드로 영상을 만들 때 써요. {hf?.message_ko}{hf?.image_model ? ` · 이미지 모델 ${hf.image_model}` : ""}</p>
          {canEdit && (
            <>
              <p className="text-xs text-ink-500">이 회사 키 {keyHintKo(row(HF)?.hint ?? "")}{row(HF)?.has_key ? "" : " (없으면 설치 기본 키를 써요)"}</p>
              <input className="field text-sm" type="password" autoComplete="off" aria-label="Higgsfield 키" placeholder="Higgsfield 키 (ID:SECRET)" value={hfKey} onChange={(e) => setHfKey(e.target.value)} />
              <button type="button" className="btn-primary text-xs" disabled={busy || blocked || !hfKey.trim()} onClick={() => void run(saveHf)}>저장</button>
            </>
          )}
        </div>
        <div className="space-y-2 rounded-xl border border-ink-100 p-4">
          <Status name="유튜브" {...ytLook} />
          <p className="text-xs text-ink-500">채널 주인 구글 계정으로 동의하면 이 회사 채널에 영상을 올릴 수 있어요</p>
          {canEdit && (
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary text-xs" disabled={busy || blocked}
                onClick={() => void run(async () => { window.location.assign(await youtubeConnectUrl()); })}>
                {row(YT)?.has_key ? "다른 채널로 다시 연결" : "유튜브 채널 연결"}
              </button>
              {row(YT)?.has_key && (
                <button type="button" className="btn-ghost text-xs" disabled={busy}
                  onClick={() => void run(async () => { await youtubeDisconnect(); return "유튜브 연결을 끊었어요"; })}>연결 끊기</button>
              )}
            </div>
          )}
        </div>
        <div className="space-y-2 rounded-xl border border-ink-100 p-4">
          <Status name="인스타그램" tone={igOn ? "ok" : "off"} label={admin ? (igOn ? "연결됨" : "연결 전") : "—"} />
          <p className="text-xs text-ink-500">Meta에서 받은 Instagram 토큰과 계정 ID를 넣어요</p>
          {canEdit && (
            <>
              <p className="text-xs text-ink-500">{KEY_LABEL[IG_TOKEN]} {keyHintKo(row(IG_TOKEN)?.hint ?? "")} · {KEY_LABEL[IG_USER]} {keyHintKo(row(IG_USER)?.hint ?? "")}</p>
              <input className="field text-sm" type="password" autoComplete="off" aria-label="인스타 토큰" placeholder="인스타 토큰" value={token} onChange={(e) => setToken(e.target.value)} />
              <input className="field text-sm" autoComplete="off" aria-label="인스타 계정 ID" placeholder="인스타 계정 ID(숫자)" value={userId} onChange={(e) => setUserId(e.target.value)} />
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-primary text-xs" disabled={busy || blocked || (!token.trim() && !userId.trim())} onClick={() => void run(saveIg)}>저장</button>
                {igOn && <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => void run(dropIg)}>연결 끊기</button>}
              </div>
            </>
          )}
        </div>
      </div>
      {blocked && admin && <p className="text-xs text-accent">{admin.secret_store.reason}</p>}
      {msg && <p className="text-xs text-ink-700" role="status">{msg}</p>}
    </section>
  );
}
