#!/usr/bin/env python3
"""Reproduce the desk-brief setup table (2026-08-17) as an Excel workbook.

Run: python build_setups_xlsx.py [out.xlsx]   (default: setups_2026-08-17.xlsx)
Regenerates the workbook with the current static/live levels.
"""

import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

OUT = sys.argv[1] if len(sys.argv) > 1 else "setups_2026-08-17.xlsx"

DATE = "2026-08-17"
LIVE = "live 06:07 UTC (kucoin)"

HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(bold=True, color="FFFFFF", size=11)
SUB_FILL = PatternFill("solid", fgColor="D9E2F3")
SUB_FONT = Font(bold=True, size=11)
THIN = Side(style="thin", color="B0B0B0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="top")


def style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = BORDER


def style_body(ws, first, last, ncols):
    for r in range(first, last + 1):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = BORDER
            cell.alignment = WRAP
            if isinstance(cell.value, (int, float)):
                cell.number_format = "#,##0"


def widths(ws, w):
    for i, x in enumerate(w, start=1):
        ws.column_dimensions[get_column_letter(i)].width = x


wb = Workbook()

# ---------------- Sheet 1: Setups ----------------
ws = wb.active
ws.title = "Setups"
ws.append(["ID", "Side", "Setup", "Trigger", "Entry", "Stop", "T1", "T2", "Volume condition", "Status"])
style_header(ws, 1, 10)

rows = [
    [
        "L1",
        "Long",
        "VWAP/R1 pullback",
        "Price enters VWAP..R1 zone (63,288-63,308, VWAP rotates) and holds >= 30 min",
        "63,288-63,308",
        "63,002 (Pivot)",
        "63,676 (R2)",
        "64,986 (78.6% fib)",
        "-",
        "Waiting - price 63,564 below zone (LIVE)",
    ],
    [
        "L2",
        "Long",
        "4h-close breakout",
        "4h candle closes above prior session high 63,390",
        "~63,429 (breakout close)",
        "63,288 (R1)",
        "64,986 (78.6% fib)",
        "65,391 (shelf)",
        "> 3,000 BTC (confirm)",
        "TRIGGERED 06:00 UTC (close 63,429 > 63,390) - vol UNCONFIRMED (2,170 < 3,000). Watching stop/T1/T2",
    ],
    [
        "S1",
        "Short",
        "R2 rejection",
        "Price at 63,676 + rejection: bearish 4h close or upper wick > 40% of range",
        "~63,676 (R2)",
        "64,060",
        "63,002 (Pivot)",
        "62,614 (S1)",
        "< 1,000 BTC (cap)",
        "Waiting - no rejection yet (LIVE)",
    ],
    [
        "S2",
        "Short",
        "4h-close breakdown",
        "4h candle closes below Pivot 63,002",
        "~63,002 (below Pivot)",
        "63,288 (R1)",
        "62,614 (S1)",
        "62,352 (50% fib)",
        "> 2,000 BTC (confirm)",
        "Waiting - price 63,564 above Pivot (LIVE)",
    ],
]
for r in rows:
    ws.append(r)
style_body(ws, 2, 5, 10)
widths(ws, [6, 9, 22, 46, 20, 16, 14, 14, 20, 50])
ws.freeze_panes = "A2"
ws.auto_filter.ref = f"A1:J{len(rows) + 1}"

note = (
    f"Setups from the desk brief dated {DATE}. Pivots/fibs/shelf are static; VWAP, session H/L "
    "and prior-day VWAP rotate intraday (entry references resolve against live levels). "
    "All stop/T1/T2 references are level names - see Levels sheet."
)
ws.cell(row=7, column=1, value=note).font = Font(italic=True, size=9, color="555555")
ws.merge_cells(start_row=7, start_column=1, end_row=7, end_column=10)

# ---------------- Sheet 2: Levels ----------------
ws2 = wb.create_sheet("Levels")
ws2.append(["Reference", "Value", "Type", "Notes"])
style_header(ws2, 1, 4)

levels = [
    ["Prior day high", "63,390", "static (8/16 close)", "Pivot input - also L2 breakout reference"],
    ["Prior day low", "62,716", "static (8/16 close)", "Pivot input"],
    ["Prior day close", "62,900", "static (8/16 close)", "Pivot input"],
    ["PIVOT", "63,002", "static", "Floor pivot (classic formula)"],
    ["R1", "63,288", "static", "L1 entry max, L2 stop, S2 stop"],
    ["R2", "63,676", "static", "L1 T1, S1 short zone"],
    ["R3", "63,962", "static", ""],
    ["S1", "62,614", "static", "S1 T2, S2 T1"],
    ["S2", "62,328", "static", ""],
    ["S3", "61,940", "static", ""],
    ["Session VWAP", "63,366", f"{LIVE} - rotates", "L1 entry min; anchored 00:00 UTC"],
    ["Prior-day VWAP", "63,057", f"{LIVE} - rotates", "Previous full day"],
    ["Session high", "63,588", f"{LIVE} - rotates", ""],
    ["Session low", "62,753", f"{LIVE} - rotates", ""],
    ["61.8% fib", "63,439", "static (desk brief)", ""],
    ["78.6% fib", "64,986", "static (desk brief)", "L1/L2 T2"],
    ["50% fib", "62,352", "static (desk brief)", "S2 T2"],
    ["Shelf", "65,391", "static (desk brief)", "L2 T2"],
    ["ATR (4h)", "220", f"{LIVE}", "Average true range, 14 closed candles"],
    ["SMA9 / SMA20 / SMA50", "63,715 / 63,846 / 63,623", f"{LIVE}", "Closed daily closes - price below all three"],
]
for r in levels:
    ws2.append(r)
style_body(ws2, 2, len(levels) + 1, 4)
widths(ws2, [26, 24, 30, 60])
ws2.freeze_panes = "A2"

n2 = (
    f"Static values are from the desk brief ({DATE}); live values are the monitor's "
    "latest run (kucoin 06:07 UTC) and rotate - pivot R1-R3/S1-S3 skew by < $100 vs "
    "the brief due to exchange data. See README.md for how each level is computed."
)
ws2.cell(row=len(levels) + 3, column=1, value=n2).font = Font(italic=True, size=9, color="555555")
ws2.merge_cells(start_row=len(levels) + 3, start_column=1, end_row=len(levels) + 3, end_column=4)

# ---------------- Sheet 3: Legend ----------------
ws3 = wb.create_sheet("Legend")
ws3.append(["Reference", "Meaning"])
style_header(ws3, 1, 2)
legend = [
    ["PIVOT, R1-R3, S1-S3", "Floor pivots of the last closed daily candle"],
    ["VWAP", "Session VWAP anchored at 00:00 UTC (includes in-progress 4h candle)"],
    ["PRIOR_VWAP", "Previous full day's VWAP (daily volume-weighted)"],
    ["SESSION_HIGH / SESSION_LOW", "Current session high / low"],
    ["SESSION_HIGH_PRIOR", "Session high before the last closed 4h candle (breakout reference)"],
    ["FIB618 / FIB786 / FIB50", "Fibonacci levels from the desk brief"],
    ["SHELF65K", "Static 65,391 shelf from the desk brief"],
    ["Volume CONFIRMED", "4h candle volume >= threshold (L2 3,000 BTC, S2 2,000 BTC)"],
    ["Volume cap (S1)", "Entry only while 4h volume < 1,000 BTC"],
    ["L1 hold rule", "Entry requires price to hold in zone >= 30 min"],
    ["S1 rejection", "Bearish 4h close or upper wick > 40% of the candle's range at R2"],
]
for r in legend:
    ws3.append(r)
style_body(ws3, 2, len(legend) + 1, 2)
widths(ws3, [34, 90])
ws3.freeze_panes = "A2"

wb.save(OUT)
print(f"wrote {OUT}")