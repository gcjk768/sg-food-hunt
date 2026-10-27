/* SG Food Hunt website. Reads live/data.json (written by `sgfh site` after every run) and renders
   the lists, the map and the venue drawer. No build step, no framework. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const DAY_LABEL = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
  const PAGE = 30;
  const DARK = matchMedia("(prefers-color-scheme: dark)");
  const ATTRIB = '<a href="https://www.onemap.gov.sg/" target="_blank" rel="noopener">OneMap</a> &copy; <a href="https://www.sla.gov.sg/" target="_blank" rel="noopener">Singapore Land Authority</a>';

  let D = null; // data.json
  let rankIndex = {}; // venue id -> [{rank, cat, label}]
  const state = { group: null, cat: null, by: "category", view: "list", shown: PAGE, q: "", near: null, picking: false, big: false };
  const NEAR_KEY = "sgfh-near";
  const km = (a, b, c, d) => {
    const r = Math.PI / 180;
    const x = Math.sin((c - a) * r / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin((d - b) * r / 2) ** 2;
    return 12742 * Math.asin(Math.sqrt(x));
  };
  const fmtKm = (d) => (d < 1 ? `${Math.max(50, Math.round(d * 20) * 50)} m` : `${d.toFixed(1)} km`);
  // Esri World Imagery: free with attribution. Labels come from Esri's reference layer on top.
  const SAT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
  const SAT_LABELS = "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";
  const SAT_ATTRIB = 'Imagery &copy; <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a>, Maxar, Earthstar Geographics';
  let map, markers = {}, layer, pins, markersKey = "";

  // ------------------------------------------------------------ boot
  fetch("live/data.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error("data.json " + r.status); return r.json(); })
    .then(setup)
    .catch((e) => {
      $("lede").textContent = "The data file is missing. Run `sgfh site` once and reload.";
      console.error(e);
    });

  function setup(data) {
    D = data;
    for (const [key, c] of Object.entries(D.categories)) {
      for (const r of c.ranked) (rankIndex[r.id] ||= []).push({ rank: r.rank, cat: key, label: c.label });
    }
    for (const list of Object.values(rankIndex)) list.sort((a, b) => a.rank - b.rank);
    // All: every place on the site in one list (built after rankIndex so it never shows as a rank chip).
    // Worth going first, then the best rank in any list, then the number of lists, then the name.
    const everyone = Object.values(D.venues).sort((a, b) =>
      (b.worth - a.worth) ||
      ((rankIndex[a.id]?.[0]?.rank ?? 999) - (rankIndex[b.id]?.[0]?.rank ?? 999)) ||
      ((rankIndex[b.id]?.length ?? 0) - (rankIndex[a.id]?.length ?? 0)) ||
      a.name.localeCompare(b.name));
    // website labels: zi char reads oddly in English, so it is shown in Chinese
    const LABELS = { zichar: "煮炒", zichar_family: "Best 煮炒 for a family meal", new_zichar: "Newly opened 煮炒", hawker_family: "Best hawker stalls" };
    for (const g of D.groups) if (LABELS[g.key]) g.label = LABELS[g.key];
    for (const [k, c] of Object.entries(D.categories)) if (LABELS[k]) c.label = LABELS[k];
    for (const list of Object.values(rankIndex)) for (const r of list) if (LABELS[r.cat]) r.label = LABELS[r.cat];
    D.categories.all = { key: "all", label: "All places", ranked: everyone.map((v, i) => ({ id: v.id, rank: i + 1, score: 0, components: {}, adjustments: {} })) };
    // The sidebar shows either the lists (by: "category") or one entry per cuisine (by: "cuisine"),
    // each in the All order. sgfoodhunt/site.py works out v.cuisines from each place's own words; the
    // cuisine lists are built here (derived) so they never show as a rank chip.
    for (const g of D.groups) g.by = "category";
    for (const c of D.cuisines || []) {
      const ranked = everyone.filter((v) => (v.cuisines || []).includes(c))
        .map((v, i) => ({ id: v.id, rank: i + 1, score: 0, components: {}, adjustments: {} }));
      if (!ranked.length) continue;
      const slug = c.toLowerCase().replace(/[^a-z]+/g, "_");
      D.categories["cuisine_" + slug] = { key: "cuisine_" + slug, label: c, derived: true, ranked };
      D.groups.push({ key: "cuisine-" + slug, label: c, icon: CUISINE_ICONS[c] || "fork-knife", categories: ["cuisine_" + slug], by: "cuisine" });
    }
    D.groups.unshift({ key: "all", label: "All", icon: "squares-four", categories: ["all"] });
    renderLede();
    renderGroups();
    renderChanges();
    renderPromosView();
    initMap();
    wireFilters();
    wireNear();
    addEventListener("hashchange", route);
    route();
    window.SGFH = D; // read by motion.js (totals, freshness)
    document.dispatchEvent(new Event("sgfh:ready"));
  }

  // ------------------------------------------------------------ routing
  function route() {
    const parts = location.hash.replace(/^#/, "").split("/").filter(Boolean);
    if (parts[0] === "promos") { state.view = "promos"; showView(); return; }
    state.view = "list";
    const groups = D.groups;
    const g = groups.find((x) => x.key === parts[0]) || groups[0];
    state.group = g.key;
    if (g.by) state.by = g.by; // a cuisine link opens the sidebar in cuisine mode
    state.cat = g.categories.includes(parts[1]) ? parts[1] : g.categories[0];
    state.shown = PAGE;
    showView();
    renderGroups();
    renderTabs();
    renderList();
    if (parts[2] && D.venues[parts[2]]) openVenue(parts[2], false);
    else closeDrawer(false);
  }
  const setHash = (h) => { if (location.hash !== "#" + h) location.hash = h; else route(); };
  const listHash = () => `${state.group}/${state.cat}`;

  function showView() {
    $("list-view").hidden = state.view !== "list";
    $("promos-view").hidden = state.view !== "promos";
    $("changes").hidden = state.view !== "list" || !($("changes-list").children.length);
  }

  // ------------------------------------------------------------ hero
  function renderLede() {
    const n = Object.keys(D.venues).length;
    const when = D.run_finished_at ? fmtDate(D.run_finished_at) : D.run_id;
    $("lede").textContent = `${n} places, re-ranked every morning. Last run ${when}.`;
    $("promos-link").dataset.count = String(D.promos.count || 0);
    $("foot").innerHTML =
      `<span>Basemap ${ATTRIB}, ${SAT_ATTRIB}</span>` +
      `<span>Blog and review data, not a booking. Check hours before you go.</span>` +
      `<span>Run <code>${esc(D.run_id)}</code></span>`;
  }

  const CUISINE_ICONS = {
    Japanese: "fish", Chinese: "bowl-steam", Korean: "fire", Thai: "pepper", Vietnamese: "bowl-food",
    Indian: "flame", "Malay & Indonesian": "tree-palm", Peranakan: "flower-lotus", "Local & hawker": "storefront",
    Italian: "pizza", French: "bread", Spanish: "sun", Mexican: "avocado",
    "Mediterranean & Middle Eastern": "globe-hemisphere-west", Western: "hamburger", Seafood: "shrimp", Desserts: "cake",
  };
  function renderGroups() {
    const modes = D.groups.some((g) => g.by === "cuisine")
      ? `<li class="by" role="group" aria-label="Browse by">` + [["category", "Category"], ["cuisine", "Cuisine"]].map(([m, label]) =>
        `<button type="button" data-by="${m}" aria-pressed="${state.by === m}">${label}</button>`).join("") + `</li>`
      : "";
    $("groups").innerHTML = modes + D.groups.filter((g) => !g.by || g.by === state.by).map((g) => {
      const n = new Set(g.categories.flatMap((c) => D.categories[c].ranked.map((r) => r.id))).size;
      return `<li><button type="button" data-g="${esc(g.key)}" aria-pressed="${g.key === state.group && state.view === "list"}">` +
        `<i class="ph ph-${esc(g.icon)}" aria-hidden="true"></i>${esc(g.label)} <span class="n">${n}</span></button></li>`;
    }).join("");
    $("groups").querySelectorAll("button[data-g]").forEach((b) => b.onclick = () => { $("q").value = ""; state.q = ""; setHash(b.dataset.g); });
    $("groups").querySelectorAll("button[data-by]").forEach((b) => b.onclick = () => { state.by = b.dataset.by; renderGroups(); });
  }

  function renderChanges() {
    const items = [];
    for (const e of D.changes.entered) {
      const v = D.venues[e.id];
      items.push({ id: e.id, name: v.name, sub: `#${e.rank} on ${D.categories[e.category]?.label || e.label}` });
    }
    for (const id of D.changes.new_venues) {
      if (items.some((i) => i.id === id)) continue;
      const v = D.venues[id];
      items.push({ id, name: v.name, sub: "New to the lists" });
    }
    $("changes-list").innerHTML = items.slice(0, 12).map((i) =>
      `<li><button type="button" data-v="${esc(i.id)}"><b>${esc(i.name)}</b><small>${esc(i.sub)}</small></button></li>`).join("");
    $("changes-list").querySelectorAll("button").forEach((b) => b.onclick = () => openVenue(b.dataset.v));
  }

  // ------------------------------------------------------------ list
  function renderTabs() {
    const g = D.groups.find((x) => x.key === state.group);
    $("list-title").textContent = state.q ? "Search results" : g.label;
    const tabs = g.categories.map((c) => {
      const cat = D.categories[c];
      return `<li role="presentation"><button type="button" role="tab" data-c="${esc(c)}" aria-selected="${c === state.cat}">${esc(cat.label)}</button></li>`;
    });
    $("tabs").innerHTML = g.categories.length > 1 ? tabs.join("") : "";
    $("tabs").querySelectorAll("button").forEach((b) => b.onclick = () => setHash(`${state.group}/${b.dataset.c}`));
  }

  function filters() {
    return {
      worth: $("f-worth").checked, region: $("f-region").value, price: $("f-price").value,
      halal: $("f-halal").checked, weekend: $("f-weekend").checked, michelin: $("f-michelin").checked, fresh: $("f-new").checked,
    };
  }
  function passes(v, f) {
    if (f.worth && !v.worth) return false;
    if (f.region && v.region !== f.region) return false;
    if (f.price && v.price_level && v.price_level > Number(f.price)) return false;
    if (f.halal && !v.halal) return false;
    if (f.weekend && v.open_weekends === false) return false;
    if (f.michelin && !v.michelin) return false;
    if (f.fresh && !v.new) return false;
    return true;
  }
  function matches(v, q) {
    const hay = [v.name, v.name_zh, ...(v.cuisine || []), ...(v.cuisines || []), v.mrt, v.district, v.region, v.address, ...(v.tags || [])].join(" ").toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  }

  function currentItems() {
    const f = filters();
    if (state.q) {
      const q = state.q.toLowerCase();
      return nearSort(Object.values(D.venues).filter((v) => matches(v, q) && passes(v, f))
        .map((v) => ({ v, rank: (rankIndex[v.id] || [])[0] }))
        .sort((a, b) => (a.rank?.rank ?? 999) - (b.rank?.rank ?? 999)));
    }
    return nearSort(D.categories[state.cat].ranked.map((r) => ({ v: D.venues[r.id], rank: { rank: r.rank, cat: state.cat }, scored: r }))
      .filter((x) => passes(x.v, f)));
  }
  // Near me: keep places with coordinates inside the radius, closest first
  function nearSort(items) {
    if (!state.near) return items;
    const rad = Number($("near-radius").value) || Infinity;
    return items.filter((x) => x.v.lat != null && x.v.lng != null)
      .map((x) => ({ ...x, dist: km(state.near.lat, state.near.lng, x.v.lat, x.v.lng) }))
      .filter((x) => x.dist <= rad)
      .sort((a, b) => a.dist - b.dist);
  }

  function renderList() {
    const items = currentItems();
    const total = state.q ? Object.keys(D.venues).length : D.categories[state.cat].ranked.length;
    $("list-count").textContent = items.length === total ? `${items.length} places` : `${items.length} of ${total} places`;
    const base = state.q ? [] : D.categories[state.cat].ranked.map((r) => D.venues[r.id]);
    const worthN = base.filter((v) => v.worth).length;
    $("worth-chip").hidden = !base.length;
    $("worth-chip").setAttribute("aria-pressed", String($("f-worth").checked));
    $("worth-chip").innerHTML = `<i class="ph ph-fill ph-check-circle"></i>${worthN} worth going`;
    const page = items.slice(0, state.shown);
    $("rows").innerHTML = page.length ? page.map((x, i) => rowHtml(x, i)).join("") :
      `<li class="empty"><b>Nothing matches</b>Loosen a filter or clear the search.</li>`;
    $("rows").querySelectorAll(".row").forEach((b) => {
      b.onclick = () => openVenue(b.dataset.v);
      b.onmouseenter = () => hot(b.dataset.v, true);
      b.onmouseleave = () => hot(b.dataset.v, false);
    });
    $("more").hidden = items.length <= state.shown;
    $("more").textContent = `Show ${Math.min(PAGE, items.length - state.shown)} more`;
    renderMarkers(items.map((x) => x.v));
  }

  function rowHtml({ v, rank, dist }, i) {
    const r = rank ? rank.rank : null;
    const best = (rankIndex[v.id] || [])[0];
    const rankLabel = rank && state.q ? `<small style="font:500 0.7rem var(--mono);display:block;color:var(--ink-3)">${esc(shortLabel(rank.cat))}</small>` : "";
    const bestLine = !state.q && state.cat === "all" && best ? `<span class="best"><i class="ph ph-trophy"></i>#${best.rank} ${esc(shortLabel(best.cat))}</span>` : "";
    const meta = [
      dist != null ? `<span class="dist"><i class="ph ph-navigation-arrow"></i>${fmtKm(dist)}</span>` : "",
      bestLine,
      cuisineOf(v).length ? `<span><i class="ph ph-fork-knife"></i>${esc(cuisineOf(v).slice(0, 3).join(", "))}</span>` : "",
      v.price_level ? `<span><i class="ph ph-coins"></i>${"$".repeat(v.price_level)}${v.per_pax ? ` about S$${Math.round(v.per_pax)} each` : ""}</span>` : (v.price_text ? `<span><i class="ph ph-coins"></i>${esc(v.price_text)}</span>` : ""),
      v.mrt ? `<span><i class="ph ph-train"></i>${esc(v.mrt)}</span>` : (v.region ? `<span><i class="ph ph-map-pin"></i>${esc(v.region)}</span>` : ""),
      v.rating ? `<span><i class="ph ph-star"></i>${v.rating} (${v.reviews ?? "?"})</span>` : "",
    ].filter(Boolean).join("");
    const badges = [
      v.new ? `<span class="badge new">New</span>` : "",
      v.michelin ? `<span class="badge mich"><i class="ph ph-fill ph-medal"></i>${esc(v.michelin)}</span>` : "",
      v.promo ? `<span class="badge promo"><i class="ph ph-gift"></i>Promo</span>` : "",
      v.buffet ? `<span class="badge">Buffet</span>` : "",
      v.halal ? `<span class="badge">Halal</span>` : "",
    ].filter(Boolean).join("");
    return `<li><button type="button" class="row" style="--i:${Math.min(i, 20)}" data-v="${esc(v.id)}" data-rank="${r ?? ""}">` +
      `<span class="rank">${r ?? "·"}${rankLabel}</span>` +
      `<span><h3 class="name">${esc(v.name)}${v.name_zh ? `<span class="zh">${esc(v.name_zh)}</span>` : ""}</h3>` +
      `<div class="meta">${meta}</div>` +
      (v.summary ? `<p class="summary">${esc(v.summary)}</p>` : "") +
      (v.dishes.length ? `<p class="dishes"><i class="ph ph-bowl-food"></i>${esc(v.dishes.slice(0, 5).join(" · "))}</p>` : "") +
      (badges ? `<div class="badges">${badges}</div>` : "") + `</span>` +
      `<span class="side">${verdictBadge(v)}</span></button></li>`;
  }
  const verdictBadge = (v) => (v.worth
    ? `<span class="badge worth"><i class="ph ph-fill ph-check-circle"></i>Worth going</span>`
    : `<span class="badge nope"><i class="ph ph-x-circle"></i>${v.why === "too little proof yet" ? "Not yet" : "Skip"}</span>`) +
    `<small class="why">${esc(v.why)}</small>`;
  const cuisineOf = (v) => (v.cuisine.length ? v.cuisine : v.cuisines || []);
  const shortLabel = (cat) => D.categories[cat]?.label.replace(/^Best /, "") || cat;

  function wireFilters() {
    $("filters").querySelectorAll("input,select").forEach((el) => el.onchange = () => { state.shown = PAGE; renderList(); });
    $("f-reset").onclick = () => { $("filters").querySelectorAll("input").forEach((i) => i.checked = false); $("filters").querySelectorAll("select").forEach((s) => s.value = ""); state.shown = PAGE; renderList(); };
    $("more").onclick = () => { state.shown += PAGE; renderList(); };
    $("worth-chip").onclick = () => { $("f-worth").checked = !$("f-worth").checked; state.shown = PAGE; renderList(); };
    let t;
    $("q").oninput = () => { clearTimeout(t); t = setTimeout(() => { state.q = $("q").value.trim(); state.shown = PAGE; if (state.view !== "list") setHash(listHash()); renderTabs(); renderList(); }, 120); };
    $("close").onclick = () => closeDrawer();
    $("scrim").onclick = () => closeDrawer();
    addEventListener("keydown", (e) => { if (e.key !== "Escape") return; if (state.big) expandMap(false); else closeDrawer(); });
  }

  // ------------------------------------------------------------ map
  function initMap() {
    map = L.map("map", { zoomControl: true, attributionControl: true, scrollWheelZoom: true, wheelPxPerZoomLevel: 90 }).setView([1.3521, 103.8198], 11);
    const satellite = L.layerGroup([
      L.tileLayer(SAT, { attribution: SAT_ATTRIB, maxZoom: 19, minZoom: 11 }),
      L.tileLayer(SAT_LABELS, { maxZoom: 19, minZoom: 11 }),
    ]).addTo(map);
    map.setMaxBounds([[1.144, 103.535], [1.494, 104.502]]);
    layer = L.layerGroup().addTo(map);
    // Pins are drawn on one canvas, not one DOM element per place: with 750+ places, DOM pins made every
    // list switch, filter and keystroke rebuild and re-composite hundreds of layers (the lag on phones).
    // tolerance widens the tap target. ponytail: no pop-in or ping animation on canvas pins.
    pins = L.canvas({ padding: 0.5, tolerance: 6 });
    new MutationObserver(() => { const c = pinColours(); for (const m of Object.values(markers)) m.setStyle({ fillColor: m.worth ? c.worth : c.dim }); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    map.on("click", (e) => {
      if (state.picking) return setNear(e.latlng.lat, e.latlng.lng, "the spot you tapped");
      if (!state.big) expandMap(true);
    });
    L.DomEvent.disableClickPropagation(document.querySelector(".map-tools"));
    $("expand-btn").onclick = () => expandMap(!state.big);
    $("near-btn").onclick = () => nearMe();
  }
  function expandMap(on) {
    state.big = on;
    document.querySelector(".map-wrap").classList.toggle("expanded", on);
    document.body.classList.toggle("map-open", on);
    $("expand-btn").setAttribute("aria-pressed", String(on));
    $("expand-btn").setAttribute("aria-label", on ? "Close full-screen map" : "Expand map");
    $("expand-btn").innerHTML = `<i class="ph ph-${on ? "x" : "arrows-out"}" aria-hidden="true"></i>`;
    setTimeout(() => map.invalidateSize(), 60);
  }
  function nearMe() {
    const ask = (why) => {
      state.picking = true;
      map.getContainer().classList.add("picking");
      if (state.view !== "list") setHash(listHash());
      $("near-form").hidden = false;
      $("near-hint").textContent = why;
      if (!state.big) $("near-q").focus();
    };
    if (!navigator.geolocation || !window.isSecureContext) {
      return ask("Browsers only share GPS with https sites, so type where you are or tap the map.");
    }
    $("near-btn").disabled = true;
    navigator.geolocation.getCurrentPosition(
      (p) => { $("near-btn").disabled = false; setNear(p.coords.latitude, p.coords.longitude, "your location"); },
      () => { $("near-btn").disabled = false; ask("Location is off. Type where you are or tap the map."); },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 });
  }
  function setNear(lat, lng, label, save = true) {
    state.near = { lat, lng, label };
    state.picking = false;
    state.shown = PAGE;
    map.getContainer().classList.remove("picking");
    $("near-form").hidden = true;
    $("near-results").innerHTML = "";
    $("near-bar").hidden = false;
    $("near-label").textContent = `Near ${label}`;
    if (save) { try { localStorage.setItem(NEAR_KEY, JSON.stringify(state.near)); } catch (_) { /* private mode */ } }
    renderList();
  }
  function clearNear() {
    state.near = null;
    state.picking = false;
    map.getContainer().classList.remove("picking");
    $("near-bar").hidden = true;
    $("near-form").hidden = true;
    try { localStorage.removeItem(NEAR_KEY); } catch (_) { /* private mode */ }
    renderList();
  }
  const title = (s) => s.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  function wireNear() {
    $("near-form").onsubmit = (e) => {
      e.preventDefault();
      const q = $("near-q").value.trim();
      if (!q) return;
      $("near-results").innerHTML = "<li><small>Searching OneMap...</small></li>";
      fetch(`https://www.onemap.gov.sg/api/common/elastic/search?searchVal=${encodeURIComponent(q)}&returnGeom=Y&getAddrDetails=N&pageNum=1`)
        .then((r) => r.json())
        .then((d) => {
          const res = (d.results || []).slice(0, 5);
          $("near-results").innerHTML = res.length
            ? res.map((r, i) => `<li><button type="button" data-i="${i}">${esc(title(r.SEARCHVAL))}</button></li>`).join("")
            : "<li><small>Nothing found. Try a postal code or an MRT name.</small></li>";
          $("near-results").querySelectorAll("button").forEach((b) => {
            b.onclick = () => { const r = res[b.dataset.i]; setNear(Number(r.LATITUDE), Number(r.LONGITUDE), title(r.SEARCHVAL)); };
          });
        })
        .catch(() => { $("near-results").innerHTML = "<li><small>OneMap search did not answer. Tap the map instead.</small></li>"; });
    };
    $("near-cancel").onclick = () => { state.picking = false; map.getContainer().classList.remove("picking"); $("near-form").hidden = true; };
    $("near-change").onclick = () => nearMe();
    $("near-clear").onclick = clearNear;
    $("near-radius").onchange = () => { state.shown = PAGE; renderList(); };
    try {
      const saved = JSON.parse(localStorage.getItem(NEAR_KEY) || "null");
      if (saved && typeof saved.lat === "number") setNear(saved.lat, saved.lng, saved.label, false);
    } catch (_) { /* bad saved value */ }
  }
  const PIN = { radius: 7, weight: 2.5, color: "#15123b", fillOpacity: 1, bubblingMouseEvents: false };
  const HOVER = matchMedia("(hover: hover)").matches;
  const pinColours = () => {
    const cs = getComputedStyle(document.documentElement);
    return { worth: cs.getPropertyValue("--accent").trim(), dim: "#9a95b8", hot: cs.getPropertyValue("--kaya").trim() };
  };
  function renderMarkers(venues) {
    // same places as last time (Show more, a re-render): keep the pins and the map where the user left it
    const key = venues.map((v) => v.id).join() + (state.near ? `|${state.near.lat},${state.near.lng},${$("near-radius").value}` : "");
    if (key === markersKey) return;
    markersKey = key;
    layer.clearLayers(); markers = {};
    const pts = [], c = pinColours();
    for (const v of venues) {
      if (v.lat == null || v.lng == null) continue;
      const m = L.circleMarker([v.lat, v.lng], { ...PIN, renderer: pins, fillColor: v.worth ? c.worth : c.dim }).addTo(layer);
      m.worth = v.worth;
      if (HOVER) m.bindTooltip(v.name, { direction: "top", offset: [0, -8] });
      m.bindPopup(`<b>${esc(v.name)}</b><br>${esc(v.mrt ? v.mrt + " MRT" : v.region || "")}<br><a href="#" data-open="${esc(v.id)}">Details</a>`);
      m.on("popupopen", (e) => { e.popup.getElement().querySelector("[data-open]").onclick = (ev) => { ev.preventDefault(); openVenue(v.id); }; });
      markers[v.id] = m;
      pts.push([v.lat, v.lng]);
    }
    $("map-note").textContent = pts.length ? `${pts.length} on the map` : (state.near ? "Nothing in range" : "No coordinates yet");
    if (state.near) {
      const me = [state.near.lat, state.near.lng];
      L.marker(me, { icon: L.divIcon({ className: "", html: `<div class="pin me"></div>`, iconSize: [18, 18], iconAnchor: [9, 9] }), keyboard: false, title: state.near.label, zIndexOffset: 1000 }).addTo(layer);
      const rad = Number($("near-radius").value);
      if (rad) L.circle(me, { radius: rad * 1000, color: "#2f7de1", weight: 1, fillOpacity: 0.06, interactive: false }).addTo(layer);
      pts.push(me);
    }
    if (pts.length > 1) map.fitBounds(pts, { padding: [24, 24], maxZoom: 15, animate: false });
    else if (pts.length) map.setView(pts[0], 15, { animate: false });
  }
  function hot(id, on) {
    const m = markers[id];
    if (!m) return;
    const c = pinColours();
    m.setStyle(on ? { radius: 11, fillColor: c.hot } : { radius: PIN.radius, fillColor: m.worth ? c.worth : c.dim });
    if (on) m.bringToFront();
  }

  // ------------------------------------------------------------ drawer
  function openVenue(id, push = true) {
    const v = D.venues[id];
    if (!v) return;
    if (state.big) expandMap(false);
    if (push) setHash(`${listHash()}/${id}`);
    $("drawer-body").innerHTML = drawerHtml(v);
    $("drawer").setAttribute("open", "");
    $("scrim").classList.add("on");
    $("drawer").scrollTop = 0;
    $("drawer-body").querySelectorAll("[data-copy]").forEach((b) => b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => { b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 1200); }));
    const m = markers[id];
    if (m) m.openPopup();
  }
  function closeDrawer(push = true) {
    if (!$("drawer").hasAttribute("open") && !push) return;
    $("drawer").removeAttribute("open");
    $("scrim").classList.remove("on");
    if (push && location.hash.split("/").length > 2) history.replaceState(null, "", "#" + listHash());
  }

  // A YouTube source the venue already has wins; otherwise a search for the place.
  // ponytail: search link, not an embedded video; embed needs a yt-dlp lookup per venue at site build.
  function ytUrl(v) {
    const src = v.sources.find((s) => /youtube\.com\/watch|youtu\.be\//.test(s.url));
    return src ? src.url : `https://www.youtube.com/results?search_query=${encodeURIComponent(v.name + " Singapore")}`;
  }

  function drawerHtml(v) {
    const ranks = rankIndex[v.id] || [];
    const scored = ranks.find((r) => r.cat === state.cat) || ranks[0];
    const entry = scored && D.categories[scored.cat].ranked.find((r) => r.id === v.id);
    const la = Number(v.lat).toFixed(6), lo = Number(v.lng).toFixed(6); // public OneMap static map, no token
    const img = v.lat != null ? `https://www.onemap.gov.sg/api/staticmap/getStaticImage?layerchosen=${document.documentElement.dataset.theme === "dark" ? "night" : "default"}&latitude=${la}&longitude=${lo}&zoom=16&height=320&width=512&points=[${la},${lo},%22196,58,38%22,%22%22]` : null;
    const gmaps = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([v.name, v.address || v.region || "Singapore"].join(" "))}`;
    const facts = [
      v.address ? `<li class="wide"><i class="ph ph-map-pin"></i><span>${esc(v.address)} <button type="button" class="badge" data-copy="${esc(v.address)}">Copy</button></span></li>` : "",
      v.mrt ? `<li><i class="ph ph-train"></i><span>${esc(v.mrt)} MRT${v.mrt_lines.length ? ` <small style="color:var(--ink-3)">${esc(v.mrt_lines.join(", "))}</small>` : ""}</span></li>` : "",
      v.district ? `<li><i class="ph ph-compass"></i><span>${esc(v.district)}</span></li>` : "",
      cuisineOf(v).length ? `<li><i class="ph ph-fork-knife"></i><span>${esc(cuisineOf(v).join(", "))}</span></li>` : "",
      v.price_level || v.price_text ? `<li><i class="ph ph-coins"></i><span>${v.price_level ? "$".repeat(v.price_level) : ""}${v.per_pax ? ` about S$${Math.round(v.per_pax)} a person` : ""}${v.price_text ? ` <small style="color:var(--ink-3)">${esc(v.price_text)}</small>` : ""}</span></li>` : "",
      v.rating ? `<li><i class="ph ph-star"></i><span>${v.rating} from ${v.reviews ?? "?"} Google reviews${v.trend ? `, ${esc(v.trend)}` : ""}</span></li>` : "",
      v.phone ? `<li><i class="ph ph-phone"></i><span><a href="tel:${esc(v.phone.replace(/\s/g, ""))}">${esc(v.phone)}</a></span></li>` : "",
      v.hygiene ? `<li><i class="ph ph-shield-check"></i><span>SFA hygiene grade ${esc(v.hygiene)}</span></li>` : "",
      v.best_for && v.best_for !== "not ranked yet" ? `<li class="wide"><i class="ph ph-heart"></i><span>Best for ${esc(v.best_for)}</span></li>` : "",
    ].filter(Boolean).join("");
    const chips = [v.new ? "New" : "", v.michelin, v.buffet ? "Buffet" : "", v.halal ? "Halal" : "", v.kids ? "Kid friendly" : "", v.private_room ? "Private room" : "", v.noise ? `${v.noise} noise` : "", ...(v.tags || [])].filter(Boolean);
    return (img ? `<div class="photo-wrap"><img class="photo" src="${img}" alt="Map around ${esc(v.name)}" loading="lazy"><span class="photo-note">OneMap</span></div>` : "") +
      `<div class="body">` +
      `<h2>${esc(v.name)}${v.name_zh ? `<span class="zh">${esc(v.name_zh)}</span>` : ""}</h2>` +
      (ranks.length ? `<div class="chips">${ranks.map((r) => `<span>#${r.rank} ${esc(shortLabel(r.cat))}</span>`).join("")}</div>` : "") +
      `<div class="verdict ${v.worth ? "good" : "bad"}"><i class="ph ph-fill ${v.worth ? "ph-check-circle" : "ph-x-circle"}"></i>${v.worth ? "Worth going" : "Not worth going yet"} <small>${esc(v.why)}</small></div>` +
      (v.promo ? `<div class="promo-box"><b><i class="ph ph-gift"></i> ${esc(v.promo.deal)}</b>${v.promo.ends ? ` <small>until ${esc(v.promo.ends)}</small>` : ""} <a href="${esc(v.promo.url)}" target="_blank" rel="noopener">source</a></div>` : "") +
      (v.summary ? `<p>${esc(v.summary)}</p>` : "") +
      `<ul class="facts">${facts}</ul>` +
      `<div class="actions">` +
      (v.booking_url ? `<a class="primary" href="${esc(v.booking_url)}" target="_blank" rel="noopener"><i class="ph ph-calendar-check"></i>Book a table</a>` : "") +
      `<a href="${gmaps}" target="_blank" rel="noopener"><i class="ph ph-navigation-arrow"></i>Directions</a>` +
      (v.website && v.website !== v.booking_url ? `<a href="${esc(v.website)}" target="_blank" rel="noopener"><i class="ph ph-globe"></i>Website</a>` : "") +
      `<a href="${esc(ytUrl(v))}" target="_blank" rel="noopener"><i class="ph ph-youtube-logo"></i>YouTube</a>` +
      `</div>` +
      (v.dishes.length ? `<h3>Popular dishes</h3><div class="chips">${v.dishes.map((d) => `<span>${esc(d)}</span>`).join("")}</div>` : "") +
      (chips.length ? `<h3>Good to know</h3><div class="chips">${chips.map((d) => `<span>${esc(d)}</span>`).join("")}</div>` : "") +
      `<h3>Opening hours</h3>${hoursHtml(v.hours)}` +
      (entry && Object.keys(entry.components).length ? `<h3>Why it ranks #${entry.rank} on ${esc(shortLabel(scored.cat))}</h3>${scoreHtml(entry)}` : "") +
      (v.sources.length ? `<h3>Where it was recommended</h3><ul class="sources">${v.sources.map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a><small>${esc(s.source)}${s.date ? ` ${s.date.slice(0, 7)}` : ""}</small></li>`).join("")}</ul>` : "") +
      `<p class="report"><small>Checked ${esc((D.generated_at || "").slice(0, 10))}. Closed, moved or wrong? <a href="${esc(reportUrl(v))}" target="_blank" rel="noopener">Report it</a></small></p>` +
      `</div>`;
  }

  /* A report is a prefilled GitHub issue on the app's repo: no server needed, and it reaches the owner. */
  function reportUrl(v) {
    const body = `Venue: ${v.name}\nId: ${v.id}\nAddress: ${v.address || ""}\n\nWhat is wrong (closed, moved, wrong dish or price)?\n`;
    return "https://github.com/gcjk768/sg-food-hunt/issues/new?labels=data&title=" + encodeURIComponent("Report: " + v.name) + "&body=" + encodeURIComponent(body);
  }

  function hoursHtml(h) {
    if (!h || !Object.keys(h).length) return `<p style="color:var(--ink-3)">Not known. Check before you go.</p>`;
    const today = DAYS[(new Date().getDay() + 6) % 7];
    return `<table class="hours">${DAYS.map((d) => {
      const slots = h[d];
      const txt = slots && slots.length ? slots.map((s) => `${s[0]} to ${s[1]}`).join(", ") : "Closed";
      return `<tr class="${d === today ? "today" : ""}"><td>${DAY_LABEL[d]}</td><td>${esc(txt)}</td></tr>`;
    }).join("")}</table>`;
  }
  function scoreHtml(entry) {
    const parts = Object.entries(entry.components).sort((a, b) => b[1] - a[1]);
    const max = parts[0][1] || 1;
    const adj = Object.entries(entry.adjustments || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v > 0 ? "+" : ""}${v}`);
    return `<div class="score">${parts.map(([k, v]) => `<div><span>${esc(k.replace(/_/g, " "))}</span><i style="transform:scaleX(${(v / max).toFixed(3)})"></i><span>${v.toFixed(2)}</span></div>`).join("")}</div>` +
      `<p style="color:var(--ink-3);font-size:0.85rem;margin-top:0.5rem">Score ${entry.score}${adj.length ? `, with ${esc(adj.join(", "))}` : ""}. Weights per list are in config/categories.yaml.</p>`;
  }

  // ------------------------------------------------------------ promos view
  function renderPromosView() {
    const deals = Object.values(D.venues).filter((v) => v.promo).sort((a, b) => a.name.localeCompare(b.name));
    $("promos-count").textContent = deals.length ? `${deals.length} deals${D.promos.found_at ? `, found ${fmtDate(D.promos.found_at)}` : ""}` : "";
    $("deals").innerHTML = deals.length ? deals.map((v) =>
      `<li><button type="button" class="deal" data-v="${esc(v.id)}"><b>${esc(v.name)}</b><span class="what"><i class="ph ph-${v.promo.festive ? "tree-evergreen" : "gift"}"></i> ${esc(v.promo.deal)}</span>` +
      `<small>${v.promo.ends ? `Until ${esc(v.promo.ends)}` : "End date not stated"}${v.mrt ? ` · ${esc(v.mrt)} MRT` : ""}</small></button></li>`).join("") :
      `<li class="empty"><b>No current deals</b>The weekly search found nothing for the places on these lists. It runs again after the next weekly search.</li>`;
    $("deals").querySelectorAll("button").forEach((b) => b.onclick = () => openVenue(b.dataset.v));
  }

  // ------------------------------------------------------------ helpers
  function fmtDate(iso) {
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString("en-SG", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Singapore" });
  }
})();
