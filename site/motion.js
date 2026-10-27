/* SG Food Hunt revamp 2026-10-25: theme switch, counting numbers, "updated ago", "how is this number made",
   list entrances and filter transitions.
   Additive: app.js renders; this file only watches what it wrote (MutationObserver) and reads window.SGFH (the loaded data.json).
   Everything animated is transform/opacity and is skipped under prefers-reduced-motion. */
(() => {
  "use strict";
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const root = document.documentElement;
  const $ = (id) => document.getElementById(id);
  const KEY = "sgfh-theme";

  // ------------------------------------------------------------ 1. light / dark switch (the inline script in <head> already set data-theme before paint)
  const btn = $("theme");
  const apply = (t) => {
    root.dataset.theme = t;
    if (btn) { btn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme"); btn.setAttribute("aria-pressed", String(t === "dark")); }
  };
  apply(root.dataset.theme || "light");
  if (btn) {
    btn.onclick = () => {
      const t = root.dataset.theme === "dark" ? "light" : "dark";
      const go = () => { apply(t); try { localStorage.setItem(KEY, t); } catch (_) { /* private mode */ } };
      if (!still && document.startViewTransition) document.startViewTransition(go); else go();
    };
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    let saved = null; try { saved = localStorage.getItem(KEY); } catch (_) { /* private mode */ }
    if (saved !== "light" && saved !== "dark") apply(e.matches ? "dark" : "light");
  });

  // ------------------------------------------------------------ 2. numbers count up
  // Observers fire after the fact, so "was that write mine?" is answered by comparing text with the last value I wrote.
  const numNode = (el) => {
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let last = null; while (w.nextNode()) if (/\d/.test(w.currentNode.data)) last = w.currentNode;
    return last;
  };
  // animates node's first integer from -> to; `w` records the last text written so observers can tell my writes from the app's
  function countUp(node, from, to, w = {}) {
    const m = node.data.match(/\d+/);
    if (!m || still || from === to) return w;
    const head = node.data.slice(0, m.index), tail = node.data.slice(m.index + m[0].length), t0 = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - t0) / 900), v = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
      w.mine = head + v + tail; node.data = w.mine;
      if (k < 1) w.raf = requestAnimationFrame(step);
    };
    w.raf = requestAnimationFrame(step);
    return w;
  }
  // an element whose text the app rewrites (list count, worth chip): count from the last shown number to the new one
  function watchCount(el) {
    if (!el) return;
    const w = { mine: null, raf: 0 };
    let shown = 0;
    const run = () => {
      const node = numNode(el);
      if (!node || node.data === w.mine) return;
      cancelAnimationFrame(w.raf);
      const to = +node.data.match(/\d+/)[0], from = shown; shown = to;
      countUp(node, from, to, w);
    };
    new MutationObserver(run).observe(el, { childList: true, characterData: true, subtree: true });
    run();
  }
  watchCount($("list-count"));
  watchCount($("worth-chip"));

  // sidebar list counts: app.js rebuilds the buttons on every navigation, so remember what each list showed last time
  const seen = {};
  const groups = $("groups");
  if (groups) {
    const run = () => groups.querySelectorAll("button").forEach((b) => {
      const n = b.querySelector(".n");
      if (!n || n.dataset.c) return;
      n.dataset.c = "1";
      const to = +n.textContent, from = seen[b.dataset.g] ?? 0; seen[b.dataset.g] = to;
      if (isNaN(to) || still || from === to) return;
      const t0 = performance.now();
      const step = (now) => { const k = Math.min(1, (now - t0) / 800); n.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    });
    new MutationObserver(run).observe(groups, { childList: true });
    run();
  }

  // list title: slam in when the list changes
  const title = $("list-title");
  if (title) {
    let last = title.textContent;
    new MutationObserver(() => {
      if (title.textContent === last) return;
      last = title.textContent; title.classList.remove("swap"); void title.offsetWidth; title.classList.add("swap");
    }).observe(title, { childList: true, characterData: true, subtree: true });
  }

  // ------------------------------------------------------------ 3. totals, freshness and "how is this made" (needs the data)
  let D = null, chip = null, when = null;
  const HOW = () => {
    const lists = Object.values(D.categories).filter((c) => c.key !== "all" && !c.derived && c.ranked.length).length;
    return {
      places: [`Every place that holds a rank on at least one list in the latest run, merged across the blogs so a place counts once. ${lists} lists feed it; a place nobody ranks is left out.`, "sgfoodhunt/site.py: only venues with a rank in some list"],
      worth: ["A place is worth going unless it is closed, has under 4.0 stars from 20+ Google reviews, or has hygiene grade C or D. Then a Michelin star or Bib Gourmand is in; a falling rating is out; otherwise it is in with 4.2+ stars from 20+ reviews, a top 10 rank on a list, or 2+ independent sources.", "verdict() in sgfoodhunt/diff.py"],
      new: ["Places in a new-openings list, or first seen in the last 14 days. The very first run does not count, or everything would be new.", "NEW_DAYS in sgfoodhunt/site.py"],
      mich: ["Places holding a Bib Gourmand or a Michelin star. \"Selected\" and Green Star are not counted as awards.", "michelin_award() in sgfoodhunt/diff.py"],
    };
  };
  function boot() {
    if (!window.SGFH || D) return;
    D = window.SGFH;
    const V = Object.values(D.venues);
    const vals = { "s-places": V.length, "s-worth": V.filter((v) => v.worth).length, "s-new": V.filter((v) => v.new).length, "s-mich": V.filter((v) => v.michelin).length };
    $("stats").hidden = false;
    for (const [id, to] of Object.entries(vals)) { const el = $(id); el.textContent = String(to); if (!still) { el.firstChild.data = "0"; countUp(el.firstChild, 0, to); } }
    const how = $("how"), notes = HOW();
    document.querySelectorAll("[data-how]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.how, open = how.hidden || how.dataset.k !== k;
      document.querySelectorAll("[data-how]").forEach((x) => x.setAttribute("aria-expanded", String(open && x === b)));
      how.hidden = !open; how.dataset.k = k;
      if (open) {
        how.textContent = notes[k][0];
        const s = document.createElement("small"); s.textContent = "Defined in " + notes[k][1]; how.append(s);
        how.style.animation = "none"; void how.offsetWidth; how.style.animation = "";
      }
    }));
    // freshness
    chip = $("fresh"); when = new Date(D.run_finished_at || D.generated_at);
    if (!isNaN(when)) { chip.hidden = false; chip.title = "Last run finished " + when.toLocaleString("en-SG", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Singapore" }) + " (Singapore time). The scraper runs once a day."; tick(); setInterval(tick, 60000); }
  }
  function tick() {
    const m = Math.max(0, Math.round((Date.now() - when) / 60000)), h = m / 60;
    const ago = m < 2 ? "just now" : m < 90 ? `${m} min ago` : h < 48 ? `${Math.round(h)} h ago` : `${Math.round(h / 24)} days ago`;
    chip.lastChild.textContent = `Rankings updated ${ago}`;
    chip.classList.toggle("old", h > 36 && h <= 72); chip.classList.toggle("stale", h > 72);
  }
  document.addEventListener("sgfh:ready", boot);
  boot();

  // ------------------------------------------------------------ 4. venue cards: entrance as they scroll into view, glide when filters move them
  const rows = $("rows");
  if (rows) {
    const pos = new Map();    // venue id -> {x, y} inside #rows after the last render
    let queue = [], timer = 0;
    const flush = () => {
      timer = 0;
      queue.sort((a, b) => a.top - b.top || a.left - b.left);
      queue.forEach((q, i) => {
        q.li.style.transitionDelay = Math.min(i, 8) * 70 + "ms";
        q.li.classList.add("in");
        setTimeout(() => (q.li.style.transitionDelay = ""), 1200);
      });
      queue = [];
    };
    const io = new IntersectionObserver((es) => {
      for (const e of es) {
        if (!e.isIntersecting) continue;
        io.unobserve(e.target);
        queue.push({ li: e.target, top: Math.round(e.boundingClientRect.top / 40), left: e.boundingClientRect.left });
      }
      if (queue.length && !timer) timer = setTimeout(flush, 30);
    }, { rootMargin: "0px 0px -6% 0px" });

    const run = () => {
      const next = new Map();
      const lis = [...rows.children].filter((li) => li.tagName === "LI");
      lis.forEach((li) => {
        const id = li.firstElementChild?.dataset?.v;
        if (!id) { li.classList.add("in"); return; }
        const p = { x: li.offsetLeft, y: li.offsetTop };
        next.set(id, p);
        if (li.classList.contains("in") || li.dataset.seen) return;
        li.dataset.seen = "1";
        const old = pos.get(id);
        if (old && !still) {            // the card was already on screen: glide from where it was
          li.classList.add("in");
          const dx = old.x - p.x, dy = old.y - p.y;
          if (dx || dy) li.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 520, easing: "cubic-bezier(0.16, 1, 0.3, 1)" });
        } else if (still) li.classList.add("in");
        else io.observe(li);
      });
      pos.clear(); next.forEach((v, k) => pos.set(k, v));
    };
    new MutationObserver(run).observe(rows, { childList: true });
    run();
  }
})();
