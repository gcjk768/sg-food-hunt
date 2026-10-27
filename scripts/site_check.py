"""Browser regression check for the SG Food Hunt website (site/ + data/site/data.json).

    python scripts/site_check.py [URL] [--local DIR] [--perf]

URL defaults to $SGFH_SITE_URL, else http://localhost:8783 (the food-site container on the NAS); the public
address is https://foodhunt.<tailnet>.ts.net.

--local DIR serves index.html, app.js, motion.js and styles.css from DIR instead of the server, so a
page change can be checked against the live data before it is deployed. --perf adds timings at 4x
CPU throttle (phone-like). Exit code 1 when any check fails. Needs `pip install playwright` and
`playwright install chromium`.
"""

from __future__ import annotations

import os
import statistics
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

args = [a for a in sys.argv[1:] if not a.startswith("--")]
URL = (args[0] if args else os.environ.get("SGFH_SITE_URL", "http://localhost:8783")).rstrip("/")
LOCAL = Path(sys.argv[sys.argv.index("--local") + 1]) if "--local" in sys.argv else None
if LOCAL:
    args = [a for a in args if Path(a) != LOCAL]
    URL = (args[0] if args else URL).rstrip("/")
PERF = "--perf" in sys.argv
PAGE = 30
failures: list[str] = []
errors: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail != "" else ""))
    if not ok:
        failures.append(name)


def frame(pg: Page) -> None:  # wait until the change is painted
    pg.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")


def rows(pg: Page) -> int:
    return pg.locator("#rows .row").count()


def settled(pg: Page, sel: str) -> str:
    """Text of sel once it stops changing (the page counts numbers up)."""
    # the count-up runs 900 ms (motion.js), so the text must hold for a full second
    return pg.evaluate(
        """async (sel) => { let last = null, since = performance.now();
      for (let i = 0; i < 60; i++) { const t = document.querySelector(sel).innerText.trim();
        if (t !== last) { last = t; since = performance.now(); } else if (performance.now() - since >= 1000) return t;
        await new Promise((r) => setTimeout(r, 100)); } return last; }""",
        sel,
    )


def count_text(pg: Page) -> str:
    return settled(pg, "#list-count")


def map_note(pg: Page) -> str:
    return pg.locator("#map-note").inner_text().strip()


def go(pg: Page, hash_: str) -> None:
    pg.evaluate(f"location.hash = {hash_!r}")
    pg.wait_for_timeout(150)
    frame(pg)


# expected numbers, worked out in the page from data.json (window.SGFH) the same way the page does
EXPECT = """(cat) => { const D = window.SGFH, c = D.categories[cat];
  const vs = c.ranked.map((r) => D.venues[r.id]);
  return { n: vs.length, coords: vs.filter((v) => v.lat != null && v.lng != null).length, worth: vs.filter((v) => v.worth).length, label: c.label }; }"""


def setup(browser, phone: bool = False, near: dict | None = None):
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844} if phone else {"width": 1440, "height": 900},
        is_mobile=phone,
        has_touch=phone,
    )
    if near:
        ctx.add_init_script(
            f"localStorage.setItem('sgfh-near', JSON.stringify({near!r}))".replace("'", '"', 0)
        )
    if LOCAL:
        for name in ("index.html", "app.js", "motion.js", "styles.css"):
            body = (LOCAL / name).read_bytes()
            ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css"}[
                name.rsplit(".", 1)[1]
            ]
            # Playwright passes (route, request) to a 2+ argument handler, so the handler takes route only
            serve = (lambda b, t: lambda route: route.fulfill(body=b, content_type=t))(body, ctype)
            for path in ["/", "/index.html"] if name == "index.html" else ["/" + name]:
                ctx.route(URL + path, serve)
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on(
        "console",
        lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None,
    )
    return ctx, pg


def load(pg: Page, hash_: str = "#all") -> None:
    pg.goto(URL + "/" + hash_, wait_until="load")
    pg.wait_for_function(
        "window.SGFH && document.querySelectorAll('#rows .row').length > 0", timeout=60000
    )
    frame(pg)


def desktop(browser) -> None:
    ctx, pg = setup(browser)
    load(pg)
    e = pg.evaluate(EXPECT, "all")
    check("all: first page of cards", rows(pg) == min(PAGE, e["n"]), rows(pg))
    check("all: list count", count_text(pg) == f"{e['n']} places", count_text(pg))
    check("all: map count", map_note(pg) == f"{e['coords']} on the map", map_note(pg))
    check(
        "all: totals tile",
        settled(pg, "#s-places") == str(len(pg.evaluate("Object.keys(window.SGFH.venues)"))),
    )

    # every category group, and every tab inside it
    groups = pg.evaluate(
        "[...document.querySelectorAll('#groups button[data-g]')].map((b) => b.dataset.g)"
    )
    check(
        "sidebar: category mode lists",
        {"all", "restaurants", "michelin", "fine", "cafes", "hawker"} <= set(groups),
        groups,
    )
    bad = []
    for g in groups:
        pg.click(f'#groups button[data-g="{g}"]')
        frame(pg)
        cats = pg.evaluate("(g) => window.SGFH.groups.find((x) => x.key === g).categories", g)
        for i, cat in enumerate(cats):
            if i:
                pg.click(f'#tabs button[data-c="{cat}"]')
                frame(pg)
            x = pg.evaluate(EXPECT, cat)
            got = (
                rows(pg),
                count_text(pg),
                map_note(pg),
                pg.locator(f'#groups button[data-g="{g}"]').get_attribute("aria-pressed"),
            )
            want = (
                min(PAGE, x["n"]),
                f"{x['n']} places",
                f"{x['coords']} on the map" if x["coords"] else got[2],
                "true",
            )
            h = pg.evaluate("location.hash")  # a list's first tab may use the short #group form
            if got != want or not (h.startswith(f"#{g}/{cat}") or (i == 0 and h == f"#{g}")):
                bad.append((g, cat, got, want))
    check("sidebar: every list and tab renders its own places", not bad, bad[:3])

    # cuisine mode
    pg.click('#groups button[data-by="cuisine"]')
    frame(pg)
    cg = pg.evaluate(
        "[...document.querySelectorAll('#groups button[data-g]')].map((b) => b.dataset.g)"
    )
    want_c = pg.evaluate(
        "window.SGFH.cuisines.filter((c) => Object.values(window.SGFH.venues).some((v) => (v.cuisines || []).includes(c))).length"
    )
    check(
        "cuisine mode: one entry per cuisine (+ All)",
        len([g for g in cg if g.startswith("cuisine-")]) == want_c and "all" in cg,
        len(cg),
    )
    check("cuisine mode: category lists hidden", "restaurants" not in cg)
    pg.click('#groups button[data-g="cuisine-japanese"]')
    frame(pg)
    jp = pg.evaluate(
        "Object.values(window.SGFH.venues).filter((v) => (v.cuisines || []).includes('Japanese')).length"
    )
    check("cuisine: Japanese list count", count_text(pg) == f"{jp} places", count_text(pg))
    check(
        "cuisine: list title",
        pg.locator("#list-title").inner_text().strip().lower() == "japanese",
        pg.locator("#list-title").inner_text(),
    )
    check(
        "cuisine: no rank chips leak into All",
        pg.evaluate(
            "!Object.values(window.SGFH.categories).some((c) => c.derived && c.ranked.some((r) => r.score))"
        ),
    )
    pg.click('#groups button[data-by="category"]')
    frame(pg)
    check("category mode restored", pg.locator('#groups button[data-g="restaurants"]').count() == 1)

    # search
    go(pg, "#all")
    pg.fill("#q", "chicken rice")
    pg.wait_for_timeout(400)
    frame(pg)
    n_q = pg.evaluate("""() => { const D = window.SGFH; return Object.values(D.venues).filter((v) => { const hay = [v.name, v.name_zh, ...(v.cuisine || []), ...(v.cuisines || []), v.mrt, v.district, v.region, v.address, ...(v.tags || [])].join(' ').toLowerCase();
      return ['chicken', 'rice'].every((w) => hay.includes(w)); }).length; }""")
    check(
        "search: results match",
        count_text(pg) == f"{n_q} of {len(pg.evaluate('Object.keys(window.SGFH.venues)'))} places"
        and rows(pg) == min(PAGE, n_q),
        (count_text(pg), n_q),
    )
    pg.fill("#q", "")
    pg.wait_for_timeout(400)
    frame(pg)
    check("search: cleared", count_text(pg) == f"{e['n']} places", count_text(pg))

    # filters, worth chip, show more
    pg.click("#worth-chip")
    frame(pg)
    check(
        "worth chip filters", count_text(pg) == f"{e['worth']} of {e['n']} places", count_text(pg)
    )
    pg.click("#f-reset")
    frame(pg)
    check(
        "filters reset",
        count_text(pg) == f"{e['n']} places" and not pg.locator("#f-worth").is_checked(),
    )
    region = pg.evaluate(
        "[...document.querySelectorAll('#f-region option')].map((o) => o.value).filter(Boolean)[0]"
    )
    pg.select_option("#f-region", region)
    frame(pg)
    n_r = pg.evaluate(
        "(r) => window.SGFH.categories.all.ranked.filter((x) => window.SGFH.venues[x.id].region === r).length",
        region,
    )
    check("region filter", count_text(pg) == f"{n_r} of {e['n']} places", (region, count_text(pg)))
    pg.click("#f-reset")
    frame(pg)
    pg.click("#more")
    frame(pg)
    check("show more adds a page", rows(pg) == min(2 * PAGE, e["n"]), rows(pg))
    check(
        "show more keeps the map count", map_note(pg) == f"{e['coords']} on the map", map_note(pg)
    )
    go(pg, "#all")

    # drawer + map popup from a card
    # the most isolated pin among the first page of cards, so a tap on it cannot land on a neighbour
    vid = pg.evaluate("""() => { const D = window.SGFH, all = Object.values(D.venues).filter((v) => v.lat != null);
      const page = [...document.querySelectorAll('#rows .row')].map((b) => D.venues[b.dataset.v]).filter((v) => v.lat != null);
      const gap = (v) => Math.min(...all.filter((w) => w !== v).map((w) => (w.lat - v.lat) ** 2 + (w.lng - v.lng) ** 2));
      return page.map((v) => [gap(v), v.id]).sort((a, b) => b[0] - a[0])[0][1]; }""")
    name = pg.evaluate("(id) => window.SGFH.venues[id].name", vid)
    pg.locator(f'#rows .row[data-v="{vid}"]').scroll_into_view_if_needed()
    pg.locator(f'#rows .row[data-v="{vid}"]').hover()
    frame(pg)
    pg.click(f'#rows .row[data-v="{vid}"]')
    pg.wait_for_timeout(400)
    check(
        "card opens the drawer",
        pg.locator("#drawer[open]").count() == 1
        and name in pg.locator("#drawer-body").inner_text(),
    )
    check("drawer link in the address bar", pg.evaluate("location.hash").endswith("/" + vid))
    check(
        "card opens its map popup",
        pg.locator(".leaflet-popup").count() == 1
        and name in pg.locator(".leaflet-popup").inner_text(),
    )
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    check(
        "Escape closes the drawer",
        pg.locator("#drawer[open]").count() == 0 and pg.evaluate("location.hash").count("/") == 1,
    )

    # tap the pin itself: the popup tip points at it
    tip = pg.locator(".leaflet-popup-tip").bounding_box()
    pg.click(".leaflet-popup-close-button")
    pg.wait_for_timeout(300)
    check("popup closes", pg.locator(".leaflet-popup").count() == 0)
    if tip:
        pg.mouse.click(tip["x"] + tip["width"] / 2, tip["y"] + tip["height"] + 9)
        pg.wait_for_timeout(400)
        check(
            "tapping a pin opens its popup",
            pg.locator(".leaflet-popup").count() == 1
            and name in pg.locator(".leaflet-popup").inner_text(),
        )
        check(
            "tapping a pin does not expand the map", pg.locator(".map-wrap.expanded").count() == 0
        )
        pg.click(".leaflet-popup [data-open]")
        pg.wait_for_timeout(400)
        check(
            "popup Details opens the drawer",
            pg.locator("#drawer[open]").count() == 1
            and name in pg.locator("#drawer-body").inner_text(),
        )
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
    else:
        check("popup tip found", False)

    # deep links, theme, promos
    load(pg, f"#all/all/{vid}")
    check(
        "deep link opens the drawer",
        pg.locator("#drawer[open]").count() == 1
        and name in pg.locator("#drawer-body").inner_text(),
    )
    load(pg, "#cuisine-chinese")
    check(
        "cuisine deep link opens cuisine mode",
        pg.locator('#groups button[data-by="cuisine"][aria-pressed="true"]').count() == 1
        and pg.locator('#groups button[data-g="cuisine-chinese"][aria-pressed="true"]').count()
        == 1,
    )
    t0 = pg.evaluate("document.documentElement.dataset.theme")
    pg.click("#theme")
    pg.wait_for_timeout(600)
    t1 = pg.evaluate("document.documentElement.dataset.theme")
    check("theme toggle", t0 != t1 and t1 in ("light", "dark"), (t0, t1))
    pg.click("#theme")
    pg.wait_for_timeout(600)
    go(pg, "#promos")
    check(
        "promos view",
        pg.locator("#promos-view").is_visible() and not pg.locator("#list-view").is_visible(),
    )
    go(pg, "#all")
    check("back to the list", pg.locator("#list-view").is_visible() and rows(pg) > 0)
    ctx.close()


def near_me(browser) -> None:
    # a saved place (Near me) sorts by distance and draws the "you are here" pin + radius
    ctx, pg = setup(browser, near={"lat": 1.3006, "lng": 103.8559, "label": "Bugis"})
    load(pg)
    d = pg.locator("#rows .row .dist").all_inner_texts()
    check("near me: cards show distance", len(d) == rows(pg) and rows(pg) > 0, d[:3])
    km = [float(x.split()[0]) / (1000 if x.endswith(" m") else 1) for x in d]
    check("near me: closest first", km == sorted(km), km[:5])
    check("near me: label shown", "Bugis" in pg.locator("#near-label").inner_text())
    ctx.close()


def phone(browser) -> None:
    ctx, pg = setup(browser, phone=True)
    load(pg)
    pg.wait_for_timeout(1500)  # the bar slides up on load
    box = pg.locator("#groups").bounding_box()
    check(
        "phone: list bar pinned to the bottom", box and abs(box["y"] + box["height"] - 844) < 2, box
    )
    pg.locator('#groups button[data-by="cuisine"]').tap()
    frame(pg)
    check(
        "phone: cuisine switch",
        pg.locator('#groups button[data-g="cuisine-japanese"]').count() == 1,
    )
    pg.locator('#groups button[data-g="cuisine-japanese"]').tap()
    frame(pg)
    check(
        "phone: cuisine list opens",
        pg.locator("#list-title").inner_text().strip().lower() == "japanese",
    )
    pg.locator("#rows .row").first.tap()
    pg.wait_for_timeout(400)
    check("phone: card opens the drawer", pg.locator("#drawer[open]").count() == 1)
    ctx.close()


def perf(browser) -> None:
    ctx, pg = setup(browser)
    cdp = ctx.new_cdp_session(pg)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
    t = pg.evaluate("performance.now()")
    load(pg)
    ready = pg.evaluate("performance.now()") - t
    clicks = []
    for g in ["restaurants", "all", "cafes", "all", "michelin", "hawker", "all"]:
        clicks.append(
            pg.evaluate(
                """async (g) => { const t0 = performance.now(); document.querySelector(`#groups button[data-g="${g}"]`).click();
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); return performance.now() - t0; }""",
                g,
            )
        )
        pg.wait_for_timeout(300)
    keys = []
    for word in ["chicken", ""]:
        for i in range(len(word) or 1):
            v = word[: i + 1]
            keys.append(
                pg.evaluate(
                    """async (v) => { const q = document.getElementById('q'); const t0 = performance.now(); q.value = v;
              q.dispatchEvent(new Event('input', { bubbles: true })); await new Promise((r) => setTimeout(r, 130));
              await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); return performance.now() - t0 - 130; }""",
                    v,
                )
            )
    print(
        f"PERF (4x CPU) ready {ready:.0f} ms | list switch median {statistics.median(clicks):.0f} max {max(clicks):.0f} ms"
        f" | search render median {statistics.median(keys):.0f} max {max(keys):.0f} ms"
    )
    ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch()
    print(f"site: {URL}" + (f"  (page files from {LOCAL})" if LOCAL else ""))
    desktop(b)
    near_me(b)
    phone(b)
    check("no console errors", not errors, errors[:5])
    if PERF:
        perf(b)
    b.close()
print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
