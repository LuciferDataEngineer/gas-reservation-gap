"""Static briefing pages. Plain language only."""

from __future__ import annotations

from pathlib import Path

PAGES = (
    ("index.html", "The gap", "gap"),
    ("exports.html", "Exports", "exports"),
    ("storage.html", "Storage", "storage"),
    ("prices.html", "Prices", "prices"),
)

CSS = """
:root {
  color-scheme: dark;
  --bg: #0c121b;
  --bar: #101826;
  --card: #162033;
  --line: #2a3b55;
  --text: #eef3f8;
  --muted: #a9b7c9;
  --teal: #3ddec0;
  --amber: #ffc14d;
  --coral: #ff6b81;
  --blue: #7eb6ff;
  --violet: #c9b6ff;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text); }
body {
  font-family: "Segoe UI", system-ui, sans-serif;
  font-size: 16px;
  line-height: 1.45;
}
a { color: inherit; text-decoration: none; }
.bar {
  position: sticky; top: 0; z-index: 2;
  display: flex; align-items: center; gap: 18px;
  height: 60px; padding: 0 22px;
  background: var(--bar); border-bottom: 1px solid var(--line);
}
.brand { font-weight: 700; letter-spacing: 0.01em; white-space: nowrap; }
.tabs { display: flex; gap: 6px; overflow-x: auto; }
.tabs a {
  white-space: nowrap; padding: 8px 12px; border-radius: 999px; color: var(--muted);
}
.tabs a[aria-current="page"] { background: #1c3b46; color: var(--teal); }
.page { max-width: 1180px; margin: 0 auto; padding: 22px 28px 48px; }
.banner {
  background: linear-gradient(120deg, #17324a, #1b2a3d 55%, #1a2433);
  border: 1px solid var(--line); border-radius: 16px; padding: 18px 22px 16px;
  margin-bottom: 16px;
}
.kicker { margin: 0; color: var(--teal); font-size: 13px; letter-spacing: 0.04em; text-transform: uppercase; }
h1 { margin: 4px 0 6px; font-size: 28px; line-height: 1.2; font-weight: 700; }
.sub { margin: 0; color: var(--muted); max-width: 70ch; }
.kpi-row {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px; margin: 16px 0;
}
.kpi {
  background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 12px 14px 14px; min-height: 132px;
}
.kpi .k { margin: 0; color: var(--muted); font-size: 13px; }
.kpi .n { margin: 6px 0 4px; font-size: 32px; font-weight: 750; letter-spacing: -0.03em; line-height: 1; white-space: nowrap; }
.kpi .n span { font-size: 15px; font-weight: 600; margin-left: 4px; }
.kpi .take { margin: 0; font-size: 14px; line-height: 1.35; }
.tone-ok .n { color: var(--teal); }
.tone-watch .n { color: var(--amber); }
.tone-tight .n { color: var(--coral); }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 16px;
  padding: 14px 16px 16px; margin: 12px 0;
}
.card h2 { margin: 0 0 8px; font-size: 18px; line-height: 1.3; }
.pills { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.pill {
  border: 1px solid var(--line); background: transparent; color: var(--text);
  border-radius: 999px; padding: 7px 12px; font: inherit; cursor: pointer;
}
.pill.is-on { background: #1a4a44; border-color: var(--teal); color: var(--teal); }
.pane { display: none; }
.pane.is-shown { display: block; }
.chart { width: 100%; height: auto; display: block; }
.chart .grid { stroke: #24344a; stroke-width: 1; }
.chart .zero { stroke: #d5deea; stroke-width: 1.5; }
.chart .axis { fill: #c5d0de; font-size: 13px; }
.chart .bar-label { fill: #f4f7fb; font-size: 14px; font-weight: 650; }
.chart .gap { fill: none; stroke: var(--amber); stroke-width: 1.5; }
.chart .gap-label { fill: var(--amber); font-size: 13px; font-weight: 650; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 8px; color: var(--muted); font-size: 14px; }
.swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; }
.exact { color: var(--muted); font-size: 13px; margin: 8px 0 0; }
.means { border-left: 4px solid var(--teal); }
.means p { margin: 0; }
footer { color: var(--muted); font-size: 13px; margin-top: 18px; }
footer p { margin: 4px 0; }
@media (max-width: 720px) {
  .page { padding: 16px 16px 32px; }
  h1 { font-size: 24px; }
  .kpi-row { grid-template-columns: 1fr; }
  .kpi { min-height: 0; }
}
"""

JS = """
document.querySelectorAll("[data-switch]").forEach((group) => {
  group.querySelectorAll("[data-pill]").forEach((btn) => {
    btn.addEventListener("click", () => {
      group.querySelectorAll("[data-pill]").forEach((b) => {
        b.classList.toggle("is-on", b === btn);
      });
      group.querySelectorAll("[data-pane]").forEach((pane) => {
        pane.classList.toggle("is-shown", pane.dataset.pane === btn.dataset.pill);
      });
    });
  });
});
"""


def _kpi(card: dict) -> str:
    return (
        f'<article class="kpi tone-{card["tone"]}">'
        f'<p class="k">{card["kicker"]}</p>'
        f'<p class="n">{card["value"]}<span>{card["unit"]}</span></p>'
        f'<p class="take">{card["take"]}</p>'
        f"</article>"
    )


def _switch(hero: dict) -> str:
    pills = []
    panes = []
    for i, pane in enumerate(hero["panes"]):
        on = " is-on" if i == 0 else ""
        shown = " is-shown" if i == 0 else ""
        pills.append(
            f'<button type="button" class="pill{on}" data-pill="{pane["id"]}">{pane["pill"]}</button>'
        )
        panes.append(
            f'<div class="pane{shown}" data-pane="{pane["id"]}">{pane["svg"]}'
            f'<p class="exact">{pane["exact"]}</p></div>'
        )
    legend = "".join(
        f'<span><i class="swatch" style="background:{item["color"]}"></i>{item["name"]}</span>'
        for item in hero.get("legend", [])
    )
    return (
        f'<section class="card" data-switch>'
        f"<h2>{hero['title']}</h2>"
        f'<div class="pills">{"".join(pills)}</div>'
        f"{''.join(panes)}"
        f'<div class="legend">{legend}</div>'
        f"</section>"
    )


def _support(block: dict) -> str:
    legend = "".join(
        f'<span><i class="swatch" style="background:{item["color"]}"></i>{item["name"]}</span>'
        for item in block.get("legend", [])
    )
    return (
        f'<section class="card"><h2>{block["title"]}</h2>{block["svg"]}'
        f'<div class="legend">{legend}</div><p class="exact">{block["exact"]}</p></section>'
    )


def _page(slug: str, brief: dict) -> str:
    page = brief["pages"][slug]
    nav = []
    for href, label, key in PAGES:
        current = ' aria-current="page"' if key == slug else ""
        nav.append(f'<a href="{href}"{current}>{label}</a>')
    kpis = "".join(_kpi(card) for card in page["kpis"])
    support = "".join(_support(block) for block in page["support"])
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page["title"]} · East-coast gas</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="bar">
    <div class="brand">East-coast gas</div>
    <nav class="tabs">{"".join(nav)}</nav>
  </header>
  <main class="page">
    <section class="banner">
      <p class="kicker">{page["kicker"]}</p>
      <h1>{page["headline"]}</h1>
      <p class="sub">{page["sub"]}</p>
    </section>
    <section class="kpi-row">{kpis}</section>
    {_switch(page["hero"])}
    {support}
    <section class="card means"><h2>What this means</h2><p>{page["means"]}</p></section>
    <footer>
      <p>Times are Australian Eastern Daylight Time. Gas days are the dates AEMO publishes.</p>
      <p>1,000 terajoules = 1 petajoule. Snapshot {brief["as_of"]}. Gas flows to {brief["gas_through"]}. Prices to {brief["price_through"]}.</p>
      <p>Sources, all free to download: AEMO Gas Bulletin Board, AEMO 2026 gas outlook workbooks, Victorian wholesale prices, Sydney Adelaide and Brisbane daily market files, Open-Meteo temperatures.</p>
    </footer>
  </main>
  <script src="site.js"></script>
</body>
</html>
"""


def write_site(brief: dict, root: Path) -> None:
    site = root / "site"
    site.mkdir(parents=True, exist_ok=True)
    (site / "style.css").write_text(CSS)
    (site / "site.js").write_text(JS)
    for href, _label, key in PAGES:
        (site / href).write_text(_page(key, brief))
