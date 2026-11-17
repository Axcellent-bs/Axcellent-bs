// 도구 연결 › 형상관리 › 레포 연결 폼의 순수 함수: 입력 확인과 저장 본문 (node --test로 시험, repoForm.test.ts).
// GitHub App 인증은 공용 보관함의 GitHub 앱(github_app)을 고른다(`app_ref`). 앱 ID·설치 ID·개인 키는 레포마다 넣지 않는다.
import type { VcsAuth, VcsConnection, VcsConnectionInput } from "./planning";

export type RepoForm = {
  id?: string;
  addr: string;
  kind: string;
  base_url: string;
  repo: string;
  base_branch: string;
  token: string;
  description: string;
  paths: string;
  mount: string;
  auth: VcsAuth;
  /** 보관함 github_app 연결 id */
  app_ref: string;
};

export const EMPTY_REPO_FORM: RepoForm = {
  addr: "", kind: "github", base_url: "", repo: "", base_branch: "", token: "", description: "", paths: "", mount: "", auth: "token", app_ref: "",
};

export const repoFormOf = (c: VcsConnection): RepoForm => ({
  id: c.id, addr: c.repo, kind: c.kind, base_url: c.base_url ?? "", repo: c.repo, base_branch: c.base_branch ?? "", token: "",
  description: c.description ?? "", paths: c.paths.join(", "), mount: c.mount ?? "", auth: c.auth ?? "token", app_ref: c.app_ref ?? "",
});

/** 담당 경로: 쉼표·줄바꿈으로 나눈다 */
export const splitPaths = (x: string) => x.split(/[,\n]/).map((p) => p.trim()).filter(Boolean);

/** 저장 전에 막을 까닭. 없으면 null. `apps`는 고를 수 있는 GitHub 앱 id */
export function repoFormError(form: RepoForm, editing: Pick<VcsConnection, "token_set"> | undefined, apps: readonly string[]): string | null {
  if (!/^[\w.-]+\/[\w.-]+$/.test(form.repo.trim())) return "레포 주소를 넣어 주세요. 예: https://github.com/owner/name";
  if (form.auth === "app") {
    if (!apps.length) return "먼저 위의 GitHub 앱 칸에서 GitHub 앱을 연결해 주세요.";
    if (!form.app_ref || !apps.includes(form.app_ref)) return "이 레포에 쓸 GitHub 앱을 골라 주세요.";
    return null;
  }
  return !form.token && !editing?.token_set ? "접근 토큰을 넣어 주세요." : null;
}

/** 저장 본문. App이면 `app_ref`만, 토큰이면 넣은 토큰만 보낸다 (비우면 서버가 이전 값을 둔다) */
export function repoBody(form: RepoForm): VcsConnectionInput {
  const { paths, token, app_ref, addr: _addr, ...rest } = form;
  const branch = form.base_branch.trim();
  return {
    ...rest,
    repo: form.repo.trim(),
    base_url: form.base_url.trim(),
    base_branch: branch,
    detect_branch: !branch && form.kind === "github",
    description: form.description.trim(),
    paths: splitPaths(paths),
    mount: form.mount.trim(),
    ...(form.auth === "app" ? { app_ref } : token ? { token } : {}),
  };
}

/** 목록에 보일 GitHub App 칸 값 */
export function repoAppLabel(c: Pick<VcsConnection, "app_ref" | "app_name" | "app_id" | "installation_id" | "key_set">): string {
  if (c.app_ref) return c.app_name || "GitHub 앱을 찾을 수 없음 · 다시 골라 주세요";
  return `앱 ${c.app_id ?? ""} · 설치 ${c.installation_id ?? ""}${c.key_set ? "" : " · 키 없음"}`;
}
