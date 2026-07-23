"""
Render the ledger (data/ledger/index_level.csv) as a self-contained SVG line chart,
embedded by both READMEs.

Standard library only, and deterministic: the same input always produces byte-identical
output, so an unchanged ledger never creates a spurious data commit. Colors are mid-tone
so the chart stays legible on both GitHub themes.
"""
from __future__ import annotations
import csv, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "data/ledger/index_level.csv"
OUT = ROOT / "output/index_chart.svg"

W, H = 860, 360
ML, MR, MT, MB = 56, 26, 54, 34
INK, LINE = "#8b949e", "#a0392f"


def main():
    if not SRC.exists():
        print("Ledger not open yet — no chart."); return
    rows = list(csv.DictReader(open(SRC)))
    if len(rows) < 2:
        print("Fewer than two ledger rows — no chart yet."); return
    dates = [r["date"] for r in rows]
    levels = [float(r["level"]) for r in rows]

    lo, hi = min(levels + [100.0]), max(levels + [100.0])
    pad = max((hi - lo) * 0.18, 0.6)
    lo, hi = lo - pad, hi + pad

    def x(i): return ML + (W - ML - MR) * (i / (len(levels) - 1))
    def y(v): return MT + (H - MT - MB) * (1 - (v - lo) / (hi - lo))

    e = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'font-family="ui-sans-serif,system-ui,sans-serif">',
         f'<text x="{ML}" y="22" font-size="15" font-weight="600" fill="{INK}">'
         f'Book One — index level</text>',
         f'<text x="{ML}" y="40" font-size="12" fill="{INK}" opacity="0.8">'
         f'base 100 at inception {dates[0]} · score-weighted · never rebalanced after entry</text>']

    for k in range(5):
        v = lo + (hi - lo) * k / 4
        e.append(f'<line x1="{ML}" y1="{y(v):.1f}" x2="{W - MR}" y2="{y(v):.1f}" '
                 f'stroke="{INK}" stroke-opacity="0.18"/>')
        e.append(f'<text x="{ML - 8}" y="{y(v) + 4:.1f}" font-size="11" fill="{INK}" '
                 f'text-anchor="end">{v:.1f}</text>')

    e.append(f'<line x1="{ML}" y1="{y(100):.1f}" x2="{W - MR}" y2="{y(100):.1f}" '
             f'stroke="{INK}" stroke-opacity="0.5" stroke-dasharray="4 4"/>')
    e.append(f'<text x="{W - MR}" y="{y(100) - 6:.1f}" font-size="11" fill="{INK}" '
             f'text-anchor="end" opacity="0.8">base 100</text>')

    step = max(1, (len(dates) - 1) // 6)
    for i in sorted(set(range(0, len(dates), step)) | {len(dates) - 1}):
        e.append(f'<text x="{x(i):.1f}" y="{H - 10}" font-size="11" fill="{INK}" '
                 f'text-anchor="middle">{dates[i][5:]}</text>')

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(levels))
    e.append(f'<polyline points="{pts}" fill="none" stroke="{LINE}" stroke-width="2.5" '
             f'stroke-linejoin="round" stroke-linecap="round"/>')
    e.append(f'<circle cx="{x(len(levels) - 1):.1f}" cy="{y(levels[-1]):.1f}" r="3.5" fill="{LINE}"/>')
    e.append(f'<text x="{x(len(levels) - 1) - 8:.1f}" y="{y(levels[-1]) - 10:.1f}" font-size="12" '
             f'font-weight="600" fill="{LINE}" text-anchor="end">{levels[-1]:.2f}</text>')
    e.append('</svg>')

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(e) + "\n")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(levels)} points, last {levels[-1]:.4f})")


if __name__ == "__main__":
    main()
