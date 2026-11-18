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
