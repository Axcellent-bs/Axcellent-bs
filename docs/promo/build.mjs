// content.mjs를 읽어 index.html을 만든다. 사용: `node build.mjs` (이 폴더에서). 외부 패키지는 쓰지 않는다.
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import C from "./content.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const lines = (arr, sep = "<br>") => arr.map(esc).join(sep);
const has = (v) => typeof v === "string" && v.trim() !== "";

// ----- 링크 버튼 (비어 있으면 안 나온다)
const brochureBtn = (cls = "btn btn-gold") => (has(C.links.brochureUrl) ? `<a class="${cls}" href="${esc(C.links.brochureUrl)}" download>${esc(C.links.brochureLabel)}</a>` : "");
const contactBtn = (cls = "btn btn-ghost") => (has(C.links.contactUrl) ? `<a class="${cls}" href="${esc(C.links.contactUrl)}" rel="noopener">${esc(C.links.contactLabel)}</a>` : "");
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
    <div class="photo-card reveal" style="--img:url(assets/chief.jpg)">
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
<section class="sec sec-night" id="story" style="--img:url(assets/night.jpg)">
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
  <div class="wrap split">
    <div class="photo-card tall reveal" style="--img:url(assets/desk.jpg)">
      <p class="pill pill-gold">${esc(C.a2a.kicker)}</p>
      <h2 class="h2 on-photo">${C.a2a.title.map(esc).join("<br>")}</h2>
      <p class="on-photo-lead">${esc(C.a2a.lead)}</p>
    </div>
    <div class="a2a-body reveal">
      <p class="pill">${esc(C.a2a.listKicker)}</p>
      <h2 class="h2 ink">${esc(C.a2a.listTitle[0])}<br><span class="brand">${esc(C.a2a.listTitle[1])}</span></h2>
      <ul class="flows">${C.a2a.items.map((it) => `<li class="flow"><div class="who"><span class="chip chip-soft">${esc(it.from)}</span><span class="arrow" aria-label="에서">→</span><span class="chip chip-solid">${esc(it.to)}</span></div><p>${esc(it.body)}</p></li>`).join("")}</ul>
      <p class="callout">${esc(C.a2a.closing)}</p>
    </div>
  </div>
</section>`;

const ap = C.approval;
const approval = `
<section class="sec sec-paper" id="approval">
  <div class="wrap split flip">
    <div class="photo-card reveal" style="--img:url(assets/sign.jpg)">
      <p class="pill pill-gold">${esc(ap.kicker)}</p>
      <h2 class="h2 on-photo huge">${esc(ap.title[0])}<br><span class="em">${esc(ap.title[1])}</span></h2>
      <p class="on-photo-lead">${ap.lead.map((l) => esc(l).replace(esc(ap.leadEm), `<strong>${esc(ap.leadEm)}</strong>`)).join(" ")}</p>
    </div>
    <div class="approval-body reveal">
      <p class="pill">${esc(ap.listKicker)}</p>
      <h2 class="h2 ink">${esc(ap.listTitle[0])}<br><span class="brand">${esc(ap.listTitle[1])}</span></h2>
      <ul class="rules">${ap.items.map((it) => `<li><span class="ico" aria-hidden="true">${esc(it.icon)}</span><div><h3>${esc(it.title)}</h3><p>${esc(it.body)}</p></div></li>`).join("")}</ul>
      <figure class="shot"><img src="${esc(ap.shot.src)}" alt="${esc(ap.shot.alt)}" loading="lazy"><figcaption><b>${esc(ap.shot.title)}</b><span>${esc(ap.shot.body)}</span><em>${esc(ap.shot.note)}</em></figcaption></figure>
    </div>
  </div>
</section>`;

const benefits = `
<section class="sec sec-dark" id="benefits">
  <div class="wrap">
    <p class="pill pill-line reveal">${esc(C.benefits.kicker)}</p>
    <h2 class="h2 huge reveal">${esc(C.benefits.title[0])}<span class="em">${esc(C.benefits.title[1])}</span>${esc(C.benefits.title[2])}</h2>
    <div class="cols">${C.benefits.items.map((b) => `<article class="col reveal" style="--img:url(${esc(b.img)})" aria-label="${esc(b.alt)}"><p class="pill pill-gold">${esc(b.tag)}</p><h3>${b.title.map(esc).join("<br>")}</h3><p>${esc(b.body)}</p></article>`).join("")}</div>
    <p class="fine">${esc(C.benefits.note)}</p>
  </div>
</section>`;

const customers = `
<section class="sec sec-light" id="customers">
  <div class="wrap">
    <p class="pill reveal">${esc(C.customers.kicker)}</p>
    <h2 class="h2 ink reveal">${esc(C.customers.title[0])}<br><span class="brand">${esc(C.customers.title[1])}</span></h2>
    <div class="cards">${C.customers.items.map((c) => `<article class="cust reveal"><img src="${esc(c.img)}" alt="${esc(c.alt)}" loading="lazy"><h3>${esc(c.name)}</h3><p class="badges">${c.badges.map((b, i) => `<span class="badge ${b.includes("준비") ? "badge-warn" : ""}">${esc(b)}</span>`).join("")}</p><p>${esc(c.body)}</p></article>`).join("")}</div>
    <p class="callout reveal">${esc(C.customers.note)}</p>
  </div>
</section>`;

const cta = `
<section class="sec sec-cta" id="contact" style="--img:url(assets/bookstore.jpg)">
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
    <a class="brand-logo" href="#top" aria-label="AXcellent 처음으로"><img src="assets/logo-white.png" alt="AXcellent" width="156" height="30"></a>
    <button class="menu" id="menu" aria-expanded="false" aria-controls="nav">메뉴</button>
    <nav class="nav" id="nav" aria-label="구역 이동">${C.nav.map(([id, label]) => `<a href="#${id}">${esc(label)}</a>`).join("")}</nav>
    <div class="top-cta">${brochureBtn("btn btn-gold btn-sm")}${contactBtn("btn btn-ghost btn-sm")}</div>
  </div>
</header>`;

const footer = `
<footer class="foot">
  <div class="wrap foot-in">
    <img src="assets/logo-white.png" alt="AXcellent" width="130" height="25">
    <p>${esc(C.site.disclaimer)} · ${esc(C.site.photoCredit)}</p>
  </div>
</footer>`;

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
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
  <link rel="stylesheet" href="style.css">
</head>
<body>
<a class="skip" href="#vision">본문으로 건너뛰기</a>
${header}
<main>
${hero}${vision}${team}${story}${a2a}${approval}${benefits}${customers}${cta}
</main>
${footer}
<script src="app.js" defer></script>
</body>
</html>
`;

writeFileSync(join(HERE, "index.html"), html);
console.log("index.html 만듦 (" + Math.round(html.length / 1024) + " KB)");
