// 인스타 자료(instagram.json)를 읽어 홍보 페이지의 content.mjs가 바로 쓸 수 있는 모양으로 바꿔 준다.
// 쓰는 법(content.mjs):
//   import instagram from "./instagram/load.mjs";
//   ... cases: { ..., items: instagram.casesItems, ... }
// - 자료에 오류가 있으면 만드는 단계에서 멈춘다(잘못된 숫자·표기가 화면에 나가지 않게). 경고는 알려 주기만 한다.
// - 사진·영상 경로는 index.html 기준(instagram/assets/…)으로 바꿔 준다.
// - 반응 숫자의 출처 글에는 metricsKind에 따라 "팀 내 시연 반응 · " 표시가 자동으로 붙는다.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { validate } from "./check.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const data = JSON.parse(readFileSync(path.join(HERE, "instagram.json"), "utf8"));

const { errors, warnings } = validate(data, { baseDir: HERE, checkFiles: true });
warnings.forEach((w) => console.warn(`[인스타 자료 경고] ${w}`));
if (errors.length) throw new Error(`인스타 자료 오류 ${errors.length}건:\n` + errors.map((e) => "  - " + e).join("\n") + "\n(node docs/promo/instagram/check.mjs 로 다시 확인한다)");

// instagram/ 아래 파일이면 instagram/…, 아니면 홍보 사이트 공용 폴더(assets/instagram/…) 그대로
const url = (p) => (existsSync(path.join(HERE, p)) ? `instagram/${p}` : p);
const ko = (iso) => `${Number(iso.slice(5, 7))}월 ${Number(iso.slice(8, 10))}일`;

function post(p) {
  const out = {
    id: p.id,
    type: p.type,
    img: url(p.img),
    media: p.media.map(url),
    alt: p.alt,
    caption: p.caption,
    date: ko(p.date), // content.mjs의 casesPost가 그대로 보여 주는 글자("11월 13일")
    dateIso: p.date, // 정렬·비교용
    permalink: p.permalink,
    aiGenerated: p.aiGenerated,
    axcellent: p.axcellent,
  };
  if (Array.isArray(p.metrics) && p.metrics.length) {
    out.metrics = p.metrics;
    out.metricsKind = p.metricsKind;
    out.measuredAt = ko(p.measuredAt);
    out.source = p.metricsKind === "team" ? `팀 내 시연 반응 · ${p.source}` : p.source;
  }
  return out;
}

const customers = data.customers.map((c) => ({
  id: c.id,
  name: c.name,
  badges: c.badges,
  summary: c.summary,
  account: c.account,
  profileUrl: c.profileUrl,
  // 최신 게시물이 앞에 오게 정렬한다
  posts: c.posts.map(post).sort((a, b) => b.dateIso.localeCompare(a.dateIso)),
  highlights: c.highlights.map((h) => ({
    id: h.id,
    title: h.title,
    cover: url(h.cover),
    slides: h.slides.map((s) => ({ type: s.type, src: url(s.src), ...(s.date ? { date: ko(s.date) } : {}) })),
  })),
}));

// content.mjs의 cases.items와 같은 모양(name, badges, summary, posts)
const casesItems = customers.map(({ name, badges, summary, posts }) => ({ name, badges, summary, posts }));

export default { customers, casesItems };
