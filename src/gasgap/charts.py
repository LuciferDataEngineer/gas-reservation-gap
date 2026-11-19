"""Small SVG charts. Labels sit in reserved bands so they do not collide."""

from __future__ import annotations


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def bars(
    rows: list[dict],
    *,
    title: str,
    y_label: str,
    width: int = 1000,
    height: int = 360,
) -> str:
    """One bar per row. ``value`` may be negative. ``color`` is a fill."""
    left, right, top, bottom = 64, 20, 36, 52
    plot_w = width - left - right
    plot_h = height - top - bottom
    values = [float(row["value"]) for row in rows] or [0.0]
    lo = min(0.0, min(values))
    hi = max(0.0, max(values))
    if hi == lo:
        hi = lo + 1
    span = hi - lo

    def y(value: float) -> float:
        return top + (hi - value) / span * plot_h

    zero = y(0.0)
    slot = plot_w / max(len(rows), 1)
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(title)}">',
    ]
    grid = 4
    for step in range(grid + 1):
        value = lo + span * step / grid
        yy = y(value)
        parts.append(
            f'<line x1="{left}" y1="{yy:.1f}" x2="{width - right}" y2="{yy:.1f}" class="grid"/>'
        )
        parts.append(
            f'<text class="axis" x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end">{value:,.0f}</text>'
        )
    parts.append(
        f'<line x1="{left}" y1="{zero:.1f}" x2="{width - right}" y2="{zero:.1f}" class="zero"/>'
    )
    for i, row in enumerate(rows):
        value = float(row["value"])
        cx = left + slot * i + slot / 2
        bw = min(48, slot * 0.55)
        y0, y1 = sorted((zero, y(value)))
        parts.append(
            f'<rect x="{cx - bw / 2:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1 - y0, 1):.1f}" '
            f'fill="{row["color"]}" rx="3"/>'
        )
        # Negative labels sit just above the zero line so they do not hit the year.
        label_y = y(value) - 8 if value >= 0 else zero - 10
        parts.append(
            f'<text class="bar-label" x="{cx:.1f}" y="{label_y:.1f}" text-anchor="middle">'
            f"{_esc(row['label'])}</text>"
        )
        parts.append(
            f'<text class="axis" x="{cx:.1f}" y="{height - 18}" text-anchor="middle">{_esc(row["name"])}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def lines(
    series: list[dict],
    *,
    title: str,
    y_label: str,
    width: int = 1000,
    height: int = 340,
) -> str:
    """``series`` items have name, color, and points [(x_label, value), ...]. Shared x order."""
    left, right, top, bottom = 64, 24, 36, 48
    plot_w = width - left - right
    plot_h = height - top - bottom
    labels = [p[0] for p in series[0]["points"]]
    values = [float(p[1]) for s in series for p in s["points"]]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.12 or 1
    lo -= pad
    hi += pad
    span = hi - lo
    n = max(len(labels) - 1, 1)

    def x(i: int) -> float:
        return left + plot_w * i / n

    def y(value: float) -> float:
        return top + (hi - value) / span * plot_h

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(title)}">',
    ]
    for step in range(5):
        value = lo + span * step / 4
        yy = y(value)
        parts.append(
            f'<line x1="{left}" y1="{yy:.1f}" x2="{width - right}" y2="{yy:.1f}" class="grid"/>'
        )
        parts.append(
            f'<text class="axis" x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end">{value:,.0f}</text>'
        )
    many = len(labels) > 16
    for s_i, s in enumerate(series):
        coords = " ".join(f"{x(i):.1f},{y(float(v)):.1f}" for i, (_, v) in enumerate(s["points"]))
        parts.append(
            f'<polyline fill="none" stroke="{s["color"]}" stroke-width="3" points="{coords}"/>'
        )
        if not many:
            for i, (_, v) in enumerate(s["points"]):
                parts.append(
                    f'<circle cx="{x(i):.1f}" cy="{y(float(v)):.1f}" r="4" fill="{s["color"]}"/>'
                )
        last_v = float(s["points"][-1][1])
        # Stagger end labels so two series do not share a line.
        lift = 10 + s_i * 16
        parts.append(
            f'<text class="bar-label" x="{x(len(labels) - 1) - 8:.1f}" y="{y(last_v) - lift:.1f}" '
            f'text-anchor="end" fill="{s["color"]}">{_esc(s["name"])} {last_v:,.0f}</text>'
        )
    tick_at = set(range(0, len(labels), max(len(labels) // 6, 1)))
    tick_at.add(len(labels) - 1)
    for i, label in enumerate(labels):
        if i in tick_at:
            parts.append(
                f'<text class="axis" x="{x(i):.1f}" y="{height - 16}" text-anchor="middle">{_esc(label)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)
