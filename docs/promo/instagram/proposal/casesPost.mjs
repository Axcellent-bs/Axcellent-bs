// [시안] 사례 칸 게시물 카드. build.mjs의 casesPost를 이 함수로 바꿔 쓰는 제안이다(병환 님 파일은 그대로 두었다).
// 쓰는 법(build.mjs):
//   import { casesPost as casesPostProposal } from "./instagram/proposal/casesPost.mjs";
//   const casesPost = (p) => casesPostProposal(p, { esc, has, metricsHtml });
// 그리고 proposal/post-card.css의 내용을 style.css 끝에 붙인다.
//
// 바뀌는 점(기존 casesPost 대비)
// - 이미지를 누르면 원본 인스타(permalink)로 간다(글자 링크는 그대로 둔다)
// - 릴스(video)·여러 장(carousel)은 이미지 위에 "▶ 릴스"·"여러 장" 표시가 붙는다(type을 쓴다)
// - "AI가 만든 게시물" 표기를 이미지 아래쪽 표시로 옮겼다(문구는 그대로)
// - 캡션은 3줄까지만 보이고 나머지는 줄여서 카드 높이를 맞춘다. 전체 글은 인스타에서 본다
// - 한 줄에 2장(4개면 2×2)으로 깔아 마지막 카드가 혼자 아래로 내려가지 않는다(CSS)
const KIND = { video: "▶ 릴스", carousel: "여러 장" };

export function casesPost(p, { esc, has, metricsHtml }) {
  // 대표 그림만 두는 릴스(type "image")도 원본 주소가 /reel/이면 릴스 표시를 붙인다
  const label = KIND[p.type] || (String(p.permalink || "").includes("/reel/") ? KIND.video : "");
  const kind = label ? `<span class="post-kind">${label}</span>` : "";
  const ai = p.aiGenerated ? `<span class="post-ai-on">AI가 만든 게시물</span>` : "";
  const img = `<img src="${esc(p.img)}" alt="${esc(p.alt || "")}" loading="lazy">${kind}${ai}`;
  const thumb = has(p.permalink)
    ? `<a class="post-thumb" href="${esc(p.permalink)}" rel="noopener" target="_blank" aria-label="인스타에서 보기">${img}</a>`
    : `<div class="post-thumb">${img}</div>`;
  const link = has(p.permalink) ? `<a class="post-ig" href="${esc(p.permalink)}" rel="noopener" target="_blank">인스타에서 보기</a>` : "";
  return `<li class="post">${thumb}<div class="post-b">${has(p.date) ? `<span class="post-d">${esc(p.date)}</span>` : ""}${has(p.caption) ? `<p class="post-cap">${esc(p.caption)}</p>` : ""}${metricsHtml(p)}${link}</div></li>`;
}
