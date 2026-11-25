// 아주 작은 동작만 한다: 좁은 화면 메뉴 열기·닫기, 스크롤하면 나타나기. 모두 꺼도 페이지는 그대로 읽힌다.
(function () {
  var root = document.documentElement;
  var menu = document.getElementById("menu");
  var nav = document.getElementById("nav");
  if (menu && nav) {
    menu.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      menu.setAttribute("aria-expanded", open ? "true" : "false");
    });
    nav.addEventListener("click", function (e) {
      if (e.target && e.target.tagName === "A") {
        nav.classList.remove("open");
        menu.setAttribute("aria-expanded", "false");
      }
    });
    // Esc로 닫고 메뉴 버튼으로 돌아간다 (키보드로 열었으면 키보드로 닫을 수 있어야 한다)
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !nav.classList.contains("open")) return;
      nav.classList.remove("open");
      menu.setAttribute("aria-expanded", "false");
      menu.focus();
    });
  }
  // 콘텐츠 칸 인스타 게시물(3개)·홍보 영상(2개): 처음엔 앞의 몇 개만 보이고, 화살표 버튼으로 나머지를 펼치고 접는다
  document.querySelectorAll(".posts-wrap").forEach(function (wrap) {
    var list = wrap.querySelector(".posts, .vposts");
    var more = wrap.querySelector(".posts-more");
    if (!list || !more) return;
    var moreLabel = more.getAttribute("data-more") || "게시물 더 보기";
    var lessLabel = more.getAttribute("data-less") || "게시물 접기";
    list.classList.add("is-collapsed");
    more.hidden = false;
    more.addEventListener("click", function () {
      var collapsed = list.classList.toggle("is-collapsed");
      // 접을 때 재생 중이던 숨겨지는 영상은 멈춘다
      if (collapsed) list.querySelectorAll("video").forEach(function (v) { if (!v.offsetParent) v.pause(); });
      more.setAttribute("aria-expanded", collapsed ? "false" : "true");
      more.setAttribute("aria-label", collapsed ? moreLabel : lessLabel);
    });
  });
  // 마케팅 AI가 만든 영상 칸: 하나를 재생하면 칸 안의 다른 영상은 멈춘다
  document.querySelectorAll(".mkvideos").forEach(function (box) {
    box.addEventListener("play", function (e) {
      box.querySelectorAll("video").forEach(function (v) { if (v !== e.target) v.pause(); });
    }, true);
  });
  // 지금 보고 있는 구역을 메뉴에 표시한다
  if (nav && "IntersectionObserver" in window) {
    var links = {};
    nav.querySelectorAll("a[href^='#']").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    var current = null;
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var a = links[en.target.id];
        if (current) current.removeAttribute("aria-current");
        if (a) { a.setAttribute("aria-current", "true"); current = a; } else { current = null; }
      });
    }, { rootMargin: "-40% 0px -55% 0px", threshold: 0 });
    Object.keys(links).forEach(function (id) { var el = document.getElementById(id); if (el) spy.observe(el); });
  }
  // 첫 화면 밖 배경 사진은 가까이 왔을 때 불러온다(스크립트가 꺼져 있으면 noscript 규칙으로 바로 나온다)
  var bgs = document.querySelectorAll("[data-bg]");
  var showBg = function (el) { el.style.setProperty("--img", "url(" + el.getAttribute("data-bg") + ")"); };
  if ("IntersectionObserver" in window) {
    var bgio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { showBg(en.target); bgio.unobserve(en.target); } });
    }, { rootMargin: "600px 0px" });
    bgs.forEach(function (el) { bgio.observe(el); });
  } else {
    bgs.forEach(showBg);
  }
  var items = document.querySelectorAll(".reveal");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!("IntersectionObserver" in window) || reduce) {
    items.forEach(function (el) { el.classList.add("in"); });
    return;
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  items.forEach(function (el) { io.observe(el); });
  root.classList.remove("no-js");
})();
