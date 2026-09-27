"""Small inline-SVG chart builders.

Charts are drawn as plain SVG so the report is one self-contained HTML
file: no JavaScript, no internet needed at the control centre, and the PDF
prints exactly what the screen shows. Hovering any bar or dot shows its
value (SVG <title>).
"""
from __future__ import annotations

import math
from html import escape

W = 720
FONT = "font-family:var(--font-body);"
INK2 = "var(--ink-2)"
GRID = "var(--grid)"


def _step(v: float, n: int = 5) -> float:
    raw = max(v, 1e-9) / n
    exp = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * exp:
            return m * exp
    return 10 * exp


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    s = _step(v)
    return math.ceil(v / s - 1e-9) * s


def _ticks(vmax: float, n: int = 5) -> list[float]:
    s = _step(vmax, n)
    k = int(round(vmax / s))
    return [round(i * s, 6) for i in range(k + 1)]


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 10 or v == int(v) else f"{v:,.1f}"


def _svg(h: int, body: str, label: str) -> str:
    return (f'<svg class="chart" viewBox="0 0 {W} {h}" role="img" aria-label="{escape(label)}" '
            f'preserveAspectRatio="xMinYMin meet">{body}</svg>')


def legend(items: list[tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:{c}"></i>{escape(n)}</span>' for n, c in items) + "</div>"


def stacked_hbar(cats: list[str], series: list[str], colors: dict, data: dict, label: str,
                 unit: str = "", left: int = 110) -> str:
    row_h, gap, top = 22, 8, 8
    totals = [sum(data[c].get(s, 0) for s in series) for c in cats]
    vmax = _nice_max(max(totals + [1]))
    plot_w = W - left - 20
    h = top + len(cats) * (row_h + gap) + 28
    out = []
    for t in _ticks(vmax):
        x = left + plot_w * t / vmax
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{h - 26}" stroke="{GRID}"/>'
                   f'<text x="{x:.1f}" y="{h - 10}" text-anchor="middle" class="tick">{_fmt(t)}</text>')
    for i, c in enumerate(cats):
        y = top + i * (row_h + gap)
        out.append(f'<text x="{left - 8}" y="{y + row_h / 2 + 4}" text-anchor="end" class="cat">{escape(c)}</text>')
        x = left
        for s in series:
            v = data[c].get(s, 0)
            if not v:
                continue
            w = plot_w * v / vmax
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{max(w - 1.5, 0.5):.1f}" height="{row_h}" rx="2" fill="{colors[s]}">'
                       f'<title>{escape(c)} · {escape(s)}: {_fmt(v)}{unit}</title></rect>')
            x += w
    return _svg(h, "".join(out), label)


def hbar(cats: list[str], values: list[float], color: str | list[str], label: str, unit: str = "",
         left: int = 110, axis_label: str = "", show_values: bool = False, vmax: float | None = None,
         notes: list[str] | None = None) -> str:
    row_h, gap, top = 20, 7, 8
    vmax = vmax or _nice_max(max(values + [1]))
    plot_w = W - left - (60 if show_values else 20)
    h = top + len(cats) * (row_h + gap) + (44 if axis_label else 28)
    base = h - (44 if axis_label else 28)
    out = []
    for t in _ticks(vmax):
        x = left + plot_w * t / vmax
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{base + 2}" stroke="{GRID}"/>'
                   f'<text x="{x:.1f}" y="{base + 18}" text-anchor="middle" class="tick">{_fmt(t)}</text>')
    if axis_label:
        out.append(f'<text x="{left + plot_w / 2}" y="{h - 4}" text-anchor="middle" class="axis">{escape(axis_label)}</text>')
    for i, (c, v) in enumerate(zip(cats, values)):
        y = top + i * (row_h + gap)
        col = color[i] if isinstance(color, list) else color
        note = f" {notes[i]}" if notes and notes[i] else ""
        out.append(f'<text x="{left - 8}" y="{y + row_h / 2 + 4}" text-anchor="end" class="cat">{escape(c)}{escape(note)}</text>')
        w = plot_w * min(v, vmax) / vmax
        out.append(f'<rect x="{left}" y="{y}" width="{max(w, 0.5):.1f}" height="{row_h}" rx="2" fill="{col}">'
                   f'<title>{escape(c)}: {_fmt(v)}{unit}</title></rect>')
        if show_values:
            out.append(f'<text x="{left + w + 6:.1f}" y="{y + row_h / 2 + 4}" class="val">{_fmt(v)}{unit}</text>')
    return _svg(h, "".join(out), label)


def stacked_vbar(cats: list[str], series: list[str], colors: dict, data: dict, label: str,
                 y_label: str = "", rotate: bool = True, x_label: str = "", unit: str = "") -> str:
    left, top, bottom = 48, 10, (58 if rotate else 34) + (16 if x_label else 0)
    plot_w, plot_h = W - left - 12, 230
    h = top + plot_h + bottom
    totals = [sum(data[c].get(s, 0) for s in series) for c in cats]
    vmax = _nice_max(max(totals + [1]))
    slot = plot_w / max(len(cats), 1)
    bw = min(slot * 0.62, 56)
    out = []
    for t in _ticks(vmax):
        y = top + plot_h - plot_h * t / vmax
        out.append(f'<line x1="{left}" x2="{W - 12}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/>'
                   f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="tick">{_fmt(t)}</text>')
    if y_label:
        out.append(f'<text transform="translate(12 {top + plot_h / 2}) rotate(-90)" text-anchor="middle" class="axis">{escape(y_label)}</text>')
    for i, c in enumerate(cats):
        x = left + i * slot + (slot - bw) / 2
        y = top + plot_h
        for s in series:
            v = data[c].get(s, 0)
            if not v:
                continue
            hh = plot_h * v / vmax
            y -= hh
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(hh - 1.5, 0.5):.1f}" rx="2" fill="{colors[s]}">'
                       f'<title>{escape(str(c))} · {escape(s)}: {_fmt(v)}{unit}</title></rect>')
        cx = x + bw / 2
        ty = top + plot_h + 16
        if rotate:
            out.append(f'<text transform="translate({cx:.1f} {ty}) rotate(-30)" text-anchor="end" class="cat">{escape(str(c))}</text>')
        else:
            out.append(f'<text x="{cx:.1f}" y="{ty}" text-anchor="middle" class="tick">{escape(str(c))}</text>')
    if x_label:
        out.append(f'<text x="{left + plot_w / 2}" y="{h - 4}" text-anchor="middle" class="axis">{escape(x_label)}</text>')
    return _svg(h, "".join(out), label)


def scatter(points: list[dict], label: str, x_label: str, y_label: str, x_range: tuple, y_range: tuple,
            band: tuple | None = None) -> str:
    """points: {x, y, color, title}. band: (x0, x1, y0, y1) shaded normal region."""
    left, top, bottom = 52, 10, 44
    plot_w, plot_h = W - left - 16, 320
    h = top + plot_h + bottom
    (x0, x1), (y0, y1) = x_range, y_range
    sx = lambda v: left + plot_w * (v - x0) / (x1 - x0)
    sy = lambda v: top + plot_h - plot_h * (v - y0) / (y1 - y0)
    out = []
    for i in range(6):
        xv = x0 + (x1 - x0) * i / 5
        yv = y0 + (y1 - y0) * i / 5
        out.append(f'<line x1="{sx(xv):.1f}" x2="{sx(xv):.1f}" y1="{top}" y2="{top + plot_h}" stroke="{GRID}"/>'
                   f'<text x="{sx(xv):.1f}" y="{top + plot_h + 16}" text-anchor="middle" class="tick">{_fmt(xv)}</text>'
                   f'<line x1="{left}" x2="{left + plot_w}" y1="{sy(yv):.1f}" y2="{sy(yv):.1f}" stroke="{GRID}"/>'
                   f'<text x="{left - 6}" y="{sy(yv) + 4:.1f}" text-anchor="end" class="tick">{_fmt(yv)}</text>')
    if band:
        bx0, bx1, by0, by1 = band
        out.append(f'<rect x="{sx(bx0):.1f}" y="{sy(by1):.1f}" width="{sx(bx1) - sx(bx0):.1f}" height="{sy(by0) - sy(by1):.1f}" '
                   f'fill="var(--ok-band)" stroke="var(--ok-line)"/>')
    for p in points:
        if not (x0 <= p["x"] <= x1 and y0 <= p["y"] <= y1):
            continue
        out.append(f'<circle cx="{sx(p["x"]):.1f}" cy="{sy(p["y"]):.1f}" r="4.5" fill="{p["color"]}" stroke="var(--surface)" stroke-width="1.5">'
                   f'<title>{escape(p["title"])}</title></circle>')
    out.append(f'<text x="{left + plot_w / 2}" y="{h - 6}" text-anchor="middle" class="axis">{escape(x_label)}</text>'
               f'<text transform="translate(12 {top + plot_h / 2}) rotate(-90)" text-anchor="middle" class="axis">{escape(y_label)}</text>')
    return _svg(h, "".join(out), label)


def paired_hbar(cats: list[str], a: list[float], b: list[float], a_color: str, b_colors: list[str],
                label: str, a_name: str, b_name: str, left: int = 230) -> str:
    pair_h, gap, top = 26, 8, 6
    vmax = _nice_max(max(a + b + [1]))
    plot_w = W - left - 20
    h = top + len(cats) * (pair_h + gap) + 40
    base = h - 40
    out = []
    for t in _ticks(vmax, 6):
        x = left + plot_w * t / vmax
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{base}" stroke="{GRID}"/>'
                   f'<text x="{x:.1f}" y="{base + 16}" text-anchor="middle" class="tick">{_fmt(t)}</text>')
    out.append(f'<text x="{left + plot_w / 2}" y="{h - 4}" text-anchor="middle" class="axis">MW</text>')
    for i, c in enumerate(cats):
        y = top + i * (pair_h + gap)
        out.append(f'<text x="{left - 8}" y="{y + pair_h / 2 + 4}" text-anchor="end" class="cat small">{escape(c)}</text>')
        wa, wb = plot_w * a[i] / vmax, plot_w * b[i] / vmax
        out.append(f'<rect x="{left}" y="{y}" width="{wa:.1f}" height="11" rx="2" fill="{a_color}"><title>{escape(c)} · {a_name}: {_fmt(a[i])} MW</title></rect>'
                   f'<rect x="{left}" y="{y + 13}" width="{wb:.1f}" height="11" rx="2" fill="{b_colors[i]}"><title>{escape(c)} · {b_name}: {_fmt(b[i])} MW</title></rect>')
    return _svg(h, "".join(out), label)
