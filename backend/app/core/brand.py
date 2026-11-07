"""The authentic Mintree logo for printable documents. The vector paths come from the supplied brand asset (app/assets/brand-logo.svg, a copy of
frontend/public/brand-logo.svg); nothing is redrawn or approximated. `logo_drawing(height_mm)` returns a reportlab Drawing with the full wordmark, `mark=True`
crops to the leading "M" for tight spaces. Proportions are preserved; the colour is the brand teal on a transparent background."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ASSET = Path(__file__).resolve().parents[1] / "assets" / "brand-logo.svg"
VIEW_W, VIEW_H, MARK_W = 172.0, 32.0, 54.0        # same crop the web Logo component uses for the compact mark
TEAL = "#01877A"


@lru_cache(maxsize=1)
def _paths() -> list[list[tuple[str, list[float]]]]:
    """Each path as a list of (command, numbers); the asset uses only absolute M L C H V Z."""
    out = []
    for d in re.findall(r' d="([^"]+)"', ASSET.read_text()):
        cmds = []
        for cmd, nums in re.findall(r"([MLCHVZ])([^MLCHVZ]*)", d):
            cmds.append((cmd, [float(x) for x in re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", nums)]))
        out.append(cmds)
    return out


def logo_drawing(height_mm: float = 9.0, mark: bool = False):
    from reportlab.graphics.shapes import Drawing, Path
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    h = height_mm * mm
    scale = h / VIEW_H
    w = (MARK_W if mark else VIEW_W) * scale
    d = Drawing(w, h)
    for cmds in _paths():
        p = Path(fillColor=colors.HexColor(TEAL), strokeColor=None, strokeWidth=0)
        x = y = 0.0
        for cmd, n in cmds:
            if cmd == "M":
                x, y = n[0], n[1]
                p.moveTo(x * scale, h - y * scale)
            elif cmd == "L":
                x, y = n[0], n[1]
                p.lineTo(x * scale, h - y * scale)
            elif cmd == "H":
                x = n[0]
                p.lineTo(x * scale, h - y * scale)
            elif cmd == "V":
                y = n[0]
                p.lineTo(x * scale, h - y * scale)
            elif cmd == "C":
                for i in range(0, len(n), 6):
                    p.curveTo(n[i] * scale, h - n[i + 1] * scale, n[i + 2] * scale, h - n[i + 3] * scale, n[i + 4] * scale, h - n[i + 5] * scale)
                    x, y = n[i + 4], n[i + 5]
            elif cmd == "Z":
                p.closePath()
        d.add(p)
    return d


def header_flowable(company: str, title: str, subtitle: str = "", width_mm: float = 182.0):
    """Document header: the logo (left, 9 mm high, clear space around it) and the document title block (right). Used once per document, never per row."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle
    right = ParagraphStyle("brand-right", fontName="Helvetica-Bold", fontSize=13, leading=16, alignment=2, textColor=colors.HexColor("#171717"))
    sub = ParagraphStyle("brand-sub", fontName="Helvetica", fontSize=8, leading=10, alignment=2, textColor=colors.HexColor("#666666"))
    cell = [Paragraph(title, right)] + ([Paragraph(subtitle, sub)] if subtitle else [])
    t = Table([[logo_drawing(9.0), cell]], colWidths=[62 * mm, (width_mm - 62) * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#01877A")),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (0, 0), 0)]))
    return t
