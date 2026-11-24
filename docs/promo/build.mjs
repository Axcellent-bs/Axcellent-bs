// content.mjs를 읽어 index.html을 만든다. 사용: `node build.mjs` (이 폴더에서). 외부 패키지는 쓰지 않는다.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import C from "./content.mjs";
import { casesPost as casesPostProposal } from "./instagram/proposal/casesPost.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const lines = (arr, sep = "<br>") => arr.map(esc).join(sep);
const has = (v) => typeof v === "string" && v.trim() !== "";

// ----- 링크 버튼 (비어 있으면 안 나온다)
const brochureBtn = (cls = "btn btn-gold") => (has(C.links.brochureUrl) ? `<a class="${cls}" href="${esc(C.links.brochureUrl)}" download>${esc(C.links.brochureLabel)}</a>` : "");
const contactBtn = (cls = "btn btn-gold") => (has(C.links.contactUrl) ? `<a class="${cls}" href="${esc(C.links.contactUrl)}" rel="noopener">${esc(C.links.contactLabel)}</a>` : "");
const contactNote = () => (has(C.links.contactNote) ? `<p class="cta-note">${esc(C.links.contactNote)}</p>` : "");

// ----- 히어로 허브 그림 (총괄 AI 가운데, 9명이 둘레에)
function hubSvg() {
  const m = C.team.members;
  const cx = 270, cy = 270, ring = 205, r = 40;
  const pts = m.map((_, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / m.length;
    return [cx + ring * Math.cos(a), cy + ring * Math.sin(a)];
  });
  const spokes = pts.map(([x, y]) => `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="spoke"/>`).join("");
  const rim = pts.map(([x, y], i) => { const [x2, y2] = pts[(i + 1) % pts.length]; return `<line x1="${x.toFixed(1)}" y1="${y.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" class="rim"/>`; }).join("");
  const nodes = pts.map(([x, y], i) => `<g class="node n${i % 5}"><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r}"/><text x="${x.toFixed(1)}" y="${(y + 5).toFixed(1)}" text-anchor="middle">${esc(m[i].name)}</text></g>`).join("");
  return `<svg class="hub" viewBox="0 0 540 540" role="img" aria-label="${esc(C.team.hub)}가 AI 직원 ${m.length}명을 잇는 그림">${rim}${spokes}${nodes}<g class="hubnode"><circle cx="${cx}" cy="${cy}" r="58"/><text x="${cx}" y="${cy + 6}" text-anchor="middle">${esc(C.team.hub)}</text></g></svg>`;
}

// ----- 섹션들
const heroTitle = C.hero.titleLines.map((line) => line.map((p) => (p.em ? `<span class="em">${esc(p.text)}</span>` : esc(p.text))).join("")).join("<br>");
const heroLead = C.hero.lead.map((l) => esc(l).replace(esc(C.hero.leadEm), `<strong>${esc(C.hero.leadEm)}</strong>`)).join("<br>");

const hero = `
<section class="hero" id="top">
  <div class="hero-in">
    <div class="hero-copy">
      <p class="pill pill-gold">${esc(C.hero.badge)}</p>
      <h1 class="hero-title">${heroTitle}</h1>
      <p class="hero-lead">${heroLead}</p>
      <div class="hero-cta">${brochureBtn()}${contactBtn()}<a class="btn btn-ghost" href="#story">시연 장면 보기</a></div>
    </div>
    <div class="hero-art">${hubSvg()}</div>
  </div>
</section>`;

const vision = `
<section class="sec sec-light" id="vision">
  <div class="wrap vision">
    <div class="vision-head reveal">
      <p class="kicker">${esc(C.vision.kicker)}</p>
      <h2 class="h2">${lines(C.vision.title)}</h2>
      <p class="tag">Agent to Agent Automation</p>
    </div>
    <ol class="points">${C.vision.points.map((p) => `<li class="point reveal"><span class="pn">${esc(p.n)}</span><div><h3>${esc(p.title)}</h3><p>${esc(p.body)}</p></div></li>`).join("")}</ol>
  </div>
  <p class="wrap note">${esc(C.vision.note)}</p>
</section>`;

const team = `
<section class="sec sec-paper" id="team">
  <div class="wrap split">
    <div class="photo-card reveal" data-bg="assets/chief.webp">
      <p class="pill pill-gold">총괄 AI</p>
      <h2 class="h2 on-photo">${esc(C.team.title[0])}<br>${esc(C.team.title[1])}<br><span class="em">${esc(C.team.title[2])}</span></h2>
      <p class="on-photo-lead">${esc(C.team.lead)}</p>
    </div>
    <div class="team-body reveal">
      <p class="pill">${esc(C.team.kicker)}</p>
      <h2 class="h2 ink">${esc(C.team.diagramTitle[0])}<br><span class="brand">${esc(C.team.diagramTitle[1])}</span></h2>
      <div class="hubchip">${esc(C.team.hub)}</div>
      <ul class="members">${C.team.members.map((m, i) => `<li class="member m${i % 5}"><strong>${esc(m.name)}</strong><span>${esc(m.role)}</span></li>`).join("")}</ul>
      <ul class="facts">${C.team.facts.map(([k, v]) => `<li><b>${esc(k)}</b><span>${esc(v)}</span></li>`).join("")}</ul>
    </div>
  </div>
</section>`;

const media = C.story.media
  ? C.story.media.type === "video"
    ? `<figure class="demo reveal"><video controls preload="metadata" playsinline ${has(C.story.media.poster) ? `poster="${esc(C.story.media.poster)}"` : ""}><source src="${esc(C.story.media.src)}"></video>${has(C.story.media.caption) ? `<figcaption>${esc(C.story.media.caption)}</figcaption>` : ""}</figure>`
    : `<figure class="demo reveal"><img src="${esc(C.story.media.src)}" alt="${esc(C.story.media.alt || "")}" loading="lazy">${has(C.story.media.caption) ? `<figcaption>${esc(C.story.media.caption)}</figcaption>` : ""}</figure>`
  : "";

const story = `
<section class="sec sec-night" id="story" data-bg="assets/night.webp">
  <div class="wrap">
    <div class="story-head reveal">
      <p class="pill pill-gold">${esc(C.story.kicker)}</p>
      <h2 class="h2 huge">${lines(C.story.title)}</h2>
      <p class="story-lead">${esc(C.story.lead)}</p>
    </div>
    <div class="turn reveal">
      <p class="turn-line">${esc(C.story.turn[0])}<br><span class="em">${esc(C.story.turn[1])}</span></p>
      <div class="stats">${C.story.stats.map((s) => `<div class="stat"><div class="num">${esc(s.value)}<small>${esc(s.unit)}</small></div><div class="lab">${esc(s.label)}</div></div>`).join("")}</div>
      <p class="fine">${esc(C.story.statsNote)}<br>${esc(C.story.statsSource)}</p>
    </div>
    ${media}
    <div class="steps-card reveal">
      <p class="pill">${esc(C.story.stepsKicker)}</p>
      <h2 class="h2 ink">${esc(C.story.stepsTitle[0])}<br><span class="brand">${esc(C.story.stepsTitle[1])}</span></h2>
      <ol class="steps">${C.story.steps.map((s) => `<li class="step"><span class="sn">${esc(s.n)}</span><h3>${esc(s.title)}</h3><ul>${s.items.map(([who, what]) => `<li><span class="chip">${esc(who)}</span>${esc(what)}</li>`).join("")}</ul></li>`).join("")}</ol>
      <p class="fine dark">${esc(C.story.stepsNote)}</p>
    </div>
  </div>
</section>`;

const a2a = `
<section class="sec sec-paper" id="a2a">
  <div class="wrap">
    <div class="a2a-head reveal">
      <p class="pill">${esc(C.a2a.kicker)}</p>
      <h2 class="h2 ink">${C.a2a.title.map(esc).join("<br>")}</h2>
      <p class="a2a-lead">${esc(C.a2a.lead)}</p>
    </div>
    <ul class="flows flows-wide">${C.a2a.items.map((it) => `<li class="flow reveal"><div class="who"><span class="chip chip-soft">${esc(it.from)}</span><span class="arrow" aria-label="에서">→</span><span class="chip chip-solid">${esc(it.to)}</span></div><p>${esc(it.body)}</p></li>`).join("")}</ul>
    <p class="callout reveal">${esc(C.a2a.closing)}</p>
  </div>
</section>`;

const ap = C.approval;
const approval = `
<section class="sec sec-paper" id="approval">
  <div class="wrap">
  <div class="split flip">
    <div class="photo-card reveal" data-bg="assets/sign.webp">
      <p class="pill pill-gold">${esc(ap.kicker)}</p>
      <h2 class="h2 on-photo huge">${esc(ap.title[0])}<br><span class="em">${esc(ap.title[1])}</span></h2>
      <p class="on-photo-lead">${ap.lead.map((l) => esc(l).replace(esc(ap.leadEm), `<strong>${esc(ap.leadEm)}</strong>`)).join(" ")}</p>
    </div>
    <div class="approval-body reveal">
      <p class="pill">${esc(ap.listKicker)}</p>
      <h2 class="h2 ink">${esc(ap.listTitle[0])}<br><span class="brand">${esc(ap.listTitle[1])}</span></h2>
      <ul class="rules">${ap.items.map((it) => `<li><span class="ico" aria-hidden="true">${esc(it.icon)}</span><div><h3>${esc(it.title)}</h3><p>${esc(it.body)}</p></div></li>`).join("")}</ul>
    </div>
  </div>
  <figure class="shot shot-wide reveal" tabindex="0" aria-label="판매자 정산 지급 승인 화면 (좁은 화면에서는 옆으로 밀어 볼 수 있음)"><img src="${esc(ap.shot.src)}"${ap.shot.w ? ` width="${ap.shot.w}" height="${ap.shot.h}"` : ""} alt="${esc(ap.shot.alt)}" loading="lazy"><figcaption><b>${esc(ap.shot.title)}</b><span>${esc(ap.shot.body)}</span><em>${esc(ap.shot.note)}</em></figcaption></figure>
  </div>
</section>`;


// 반응 숫자는 "언제 어디서 잰 값인지"(measuredAt, source)가 함께 있을 때만 보인다. 없으면 숫자를 숨기고 알린다(지어낸 숫자가 나가지 않게)
function metricsHtml(p) {
  const list = p.metrics || [];
  if (!list.length) return "";
  if (!has(p.measuredAt) || !has(p.source)) {
    console.warn("[경고] 반응 숫자가 있는데 measuredAt·source가 없어 숨겼다: " + (p.alt || p.img));
    return "";
  }
  return `<dl class="metrics">${list.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl><p class="measured">잰 날짜 ${esc(p.measuredAt)} · ${esc(p.source)}</p>`;
}
const casesPost = (p) => casesPostProposal(p, { esc, has, metricsHtml });
// 사례 칸 규칙: 채워진 사례가 하나라도 있으면 모든 고객사 카드를 보이고(빈 쪽은 "사례 준비 중"), 하나도 없으면
// showEmpty가 true일 때만 빈 카드를 보인다. 카드가 없어도 순환 그림은 남는다(구역이 하는 말이라서)
const casesItems = C.cases.items;
const anyCase = casesItems.some((it) => it.posts && it.posts.length);
const showCaseCards = anyCase || C.cases.showEmpty === true;
const cases = `
<section class="sec sec-light" id="cases">
  <div class="wrap">
    <p class="pill reveal">${esc(C.cases.kicker)}</p>
    <h2 class="h2 ink reveal">${esc(C.cases.title[0])}<br><span class="brand">${esc(C.cases.title[1])}</span></h2>
    <p class="cases-lead reveal">${esc(C.cases.lead)}</p>
    <ol class="loop reveal">${C.cases.loop.map((s, i) => `<li class="loop-step"><span class="ln">${i + 1}</span><b>${esc(s.label)}</b><span>${esc(s.body)}</span>${has(s.note) ? `<em>${esc(s.note)}</em>` : ""}</li>`).join("")}</ol>
    ${showCaseCards ? `<div class="case-cards">${casesItems.map((it) => `<article class="case reveal"><header><h3>${esc(it.name)}</h3><p class="badges">${it.badges.map((b) => `<span class="badge">${esc(b)}</span>`).join("")}</p></header>${has(it.summary) ? `<p class="case-sum">${esc(it.summary)}</p>` : ""}${it.posts && it.posts.length ? `<ul class="posts">${it.posts.map(casesPost).join("")}</ul>` : `<div class="case-empty"><span>${esc(C.cases.emptyText)}</span></div>`}</article>`).join("")}</div>` : ""}
    <p class="fine dark reveal">${esc(C.cases.note)}</p>
  </div>
</section>`;

const benefits = `
<section class="sec sec-dark" id="benefits">
  <div class="wrap">
    <p class="pill pill-line reveal">${esc(C.benefits.kicker)}</p>
    <h2 class="h2 huge reveal">${esc(C.benefits.title[0])}<span class="em">${esc(C.benefits.title[1])}</span>${esc(C.benefits.title[2])}</h2>
    <div class="cols">${C.benefits.items.map((b) => `<article class="col reveal" data-bg="${esc(b.img)}" aria-label="${esc(b.alt)}"><p class="pill pill-gold">${esc(b.tag)}</p><h3>${b.title.map(esc).join("<br>")}</h3><p>${esc(b.body)}</p></article>`).join("")}</div>
    <p class="fine">${esc(C.benefits.note)}</p>
  </div>
</section>`;

const customers = `
<section class="sec sec-light" id="customers">
  <div class="wrap">
    <p class="pill reveal">${esc(C.customers.kicker)}</p>
    <h2 class="h2 ink reveal">${esc(C.customers.title[0])}<br><span class="brand">${esc(C.customers.title[1])}</span></h2>
    <div class="cards">${C.customers.items.map((c) => `<article class="cust reveal"><img src="${esc(c.img)}"${c.w ? ` width="${c.w}" height="${c.h}"` : ""} alt="${esc(c.alt)}" loading="lazy"><h3>${esc(c.name)}</h3><p class="badges">${c.badges.map((b, i) => `<span class="badge ${b.includes("준비") ? "badge-warn" : ""}">${esc(b)}</span>`).join("")}</p><p>${esc(c.body)}</p></article>`).join("")}</div>
    <p class="callout reveal">${esc(C.customers.note)}</p>
  </div>
</section>`;

const PS = C.posts;
const postCard = (it) => `<article class="vpost${it.wide ? " vpost-wide" : ""} reveal"><div class="vpost-media"${has(it.ratio) ? ` style="aspect-ratio:${esc(it.ratio)}"` : ""}><video controls preload="metadata" playsinline ${has(it.poster) ? `poster="${esc(it.poster)}"` : ""}><source src="${esc(it.src)}${has(it.poster) ? "" : "#t=0.1"}"></video>${it.aiMade ? '<span class="ai-mark">AI가 만든 영상</span>' : ""}</div><h3>${esc(it.title)}</h3>${has(it.customer) ? `<p class="badges"><span class="badge">${esc(it.customer)}</span></p>` : ""}${has(it.caption) ? `<p>${esc(it.caption)}</p>` : ""}${has(it.postUrl) ? `<a class="vpost-link" href="${esc(it.postUrl)}" rel="noopener" target="_blank">원 게시물 보기</a>` : ""}</article>`;
const brochureCard = has(C.links.brochureUrl) && PS.brochure ? `<div class="dl-card reveal"><div class="dl-body"><h3>${esc(PS.brochure.title)}</h3><p>${esc(PS.brochure.body)}</p><span class="dl-size">${esc(PS.brochure.size)}</span></div>${brochureBtn()}</div>` : "";
const postEmpty = () => `<article class="vpost vpost-empty reveal"><div class="vpost-media"><span>${esc(PS.emptyTitle)}</span></div><h3>홍보 영상 칸</h3><p>영상이 올라오면 이 자리에 나옵니다.</p></article>`;
const postList = PS.items.length ? PS.items.map(postCard).join("") : Array.from({ length: PS.emptySlots }, postEmpty).join("");
// 홍보 영상이 itemsFirst개를 넘으면 처음에는 앞의 itemsFirst개만 보이고, 오른쪽 화살표 버튼(app.js가 켠다)을 눌러야 나머지가 보인다.
// 스크립트가 꺼져 있으면 버튼은 숨겨진 채로 전부 보인다.
const itemsFirst = PS.itemsFirst > 0 ? PS.itemsFirst : 0;
const itemsMore = itemsFirst && PS.items.length > itemsFirst ? `<button type="button" class="posts-more" hidden aria-expanded="false" aria-label="영상 더 보기" data-more="영상 더 보기" data-less="영상 접기" data-first="${itemsFirst}"><span aria-hidden="true">›</span></button>` : "";
const itemsBlock = `<div class="posts-wrap vposts-wrap"><div class="vposts">${postList}</div>${itemsMore}</div>`;

// 고객사별 인스타 게시물. 게시물이 있는 고객사만 보인다
const igGroups = (PS.instagram || []).filter((it) => it.posts && it.posts.length);
// 게시물이 3개를 넘으면 처음에는 3개만 보이고, 세 번째 오른쪽의 화살표 버튼(app.js가 켠다)을 눌러야 나머지가 보인다.
// 스크립트가 꺼져 있으면 버튼은 숨겨진 채로 전부 보인다.
const IG_FIRST = 3;
const igMore = (it) => it.posts.length > IG_FIRST ? `<button type="button" class="posts-more" hidden aria-expanded="false" aria-label="게시물 더 보기"><span aria-hidden="true">›</span></button>` : "";
const igHtml = igGroups.map((it) => `<div class="ig-group reveal"><header><h3>${esc(it.name)} 인스타그램</h3><p class="badges">${it.badges.map((b) => `<span class="badge">${esc(b)}</span>`).join("")}</p></header><div class="posts-wrap"><ul class="posts">${it.posts.map(casesPost).join("")}</ul>${igMore(it)}</div></div>`).join("");

// 고객사별 영상 묶음. 영상이 아직 없으면 "영상 준비 중" 칸 하나를 보인다(영상을 넣으면 그 칸이 영상으로 바뀐다)
const groupEmpty = (g) => `<article class="vpost vpost-empty reveal"><div class="vpost-media"><span>영상 준비 중</span></div><h3>${esc(g.title)} 칸</h3><p>영상이 올라오면 이 자리에 나옵니다.</p></article>`;
const vgroupHtml = (PS.videoGroups || []).map((g) => `<div class="vgroup reveal"><header class="vgroup-h"><h3>${esc(g.title)}</h3>${(g.badges || []).length ? `<p class="badges">${g.badges.map((b) => `<span class="badge">${esc(b)}</span>`).join("")}</p>` : ""}</header><div class="vposts">${g.items && g.items.length ? g.items.map(postCard).join("") : groupEmpty(g)}</div></div>`).join("");

const showPosts = PS.items.length > 0 || igGroups.length > 0 || PS.showEmpty === true;
const posts = !showPosts ? "" : `
<section class="sec sec-light" id="posts">
  <div class="wrap">
    <p class="pill reveal">${esc(PS.kicker)}</p>
    <h2 class="h2 ink reveal">${esc(PS.title[0])}<br><span class="brand">${esc(PS.title[1])}</span></h2>
    <p class="posts-lead reveal">${esc(PS.lead)}</p>
    ${brochureCard}
    ${PS.items.length || !igGroups.length ? `${has(PS.itemsTitle) && PS.items.length ? `<header class="vgroup-h reveal"><h3>${esc(PS.itemsTitle)}</h3></header>` : ""}${itemsBlock}` : ""}
    ${igHtml}
    ${vgroupHtml}
    <p class="callout reveal">${esc(PS.note)}</p>
  </div>
</section>`;

const cta = `
<section class="sec sec-cta" id="contact" data-bg="assets/bookstore.webp">
  <div class="wrap">
    <h2 class="h2 huge reveal">${C.cta.title.map((t, i) => (i === C.cta.emIndex ? `<span class="em">${esc(t)}</span>` : esc(t))).join("<br>")}</h2>
    <p class="cta-lead reveal">${esc(C.cta.body)}</p>
    <div class="hero-cta reveal">${brochureBtn()}${contactBtn()}</div>
    ${contactNote()}
  </div>
</section>`;

const header = `
<header class="top" id="header">
  <div class="top-in">
    <a class="brand-logo" href="#top" aria-label="AXcellent 처음으로"><img src="assets/logo-white.webp" alt="AXcellent" width="156" height="30"></a>
    <button class="menu" id="menu" aria-expanded="false" aria-controls="nav">메뉴</button>
    <nav class="nav" id="nav" aria-label="구역 이동">${C.nav.filter(([id]) => id !== "posts" || showPosts).map(([id, label]) => `<a href="#${id}">${esc(label)}</a>`).join("")}</nav>
    <div class="top-cta">${brochureBtn("btn btn-gold btn-sm top-pdf")}${contactBtn("btn btn-gold btn-sm")}</div>
  </div>
</header>`;

const footer = `
<footer class="foot">
  <div class="wrap foot-in">
    <img src="assets/logo-white.webp" alt="AXcellent" width="130" height="25" loading="lazy">
    <p>${esc(C.site.disclaimer)} · ${esc(C.site.photoCredit)}</p>
  </div>
</footer>`;

const mainHtml = `<main>
${hero}${vision}${team}${story}${a2a}${cases}${approval}${benefits}${customers}${posts}${cta}
</main>`;
const bgFiles = [...new Set([...mainHtml.matchAll(/data-bg="([^"]+)"/g)].map((m) => m[1]))];
const bgNoscript = bgFiles.length ? `<noscript><style>${bgFiles.map((f) => `[data-bg="${f}"]{--img:url(${f})}`).join("")}</style></noscript>` : "";

const html = `<!doctype html>
<html lang="${esc(C.site.lang)}" class="no-js">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${esc(C.site.title)}</title>
  <meta name="description" content="${esc(C.site.description)}">
  <meta property="og:title" content="${esc(C.site.title)}">
  <meta property="og:description" content="${esc(C.site.description)}">
  <meta property="og:type" content="website">
  ${C.site.noindex === false ? "" : '<meta name="robots" content="noindex">'}
  <link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css" media="print" onload="this.media='all'">
  <noscript><link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"></noscript>
  <link rel="icon" href="favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="style.css">
  ${bgNoscript}
</head>
<body>
<a class="skip" href="#vision">본문으로 건너뛰기</a>
${header}
${mainHtml}
${footer}
<script src="app.js" defer></script>
</body>
</html>
`;

writeFileSync(join(HERE, "index.html"), html);
console.log("index.html 만듦 (" + Math.round(html.length / 1024) + " KB)");
