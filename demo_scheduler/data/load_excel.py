"""Load and shape the master order table from Packaging Ampoules.xlsx."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

XL_PATH_DEFAULT = Path(__file__).resolve().parents[2] / "Packaging Ampoules.xlsx"

COUNTRY_COL = "Country\nΧώρα έγκρισης"
TOTAL_POINTS_COL = "Total points "   # trailing space in the Excel header

QUARTER_VOL_COLS = {
    "Q1": "Q1 2025",
    "Q2": "Q2 2025",
    "Q3": "Q3 2025",
    "Q4": "Q4 2025",
}
BO_COL = "BO 2024"

QUARTER_TO_MONTHS = {
    "Q1": (1, 2, 3),
    "Q2": (4, 5, 6),
    "Q3": (7, 8, 9),
    "Q4": (10, 11, 12),
}


@dataclass
class RawData:
    orders: pd.DataFrame      # the demand table (filtered to chosen horizon)
    ranking: pd.DataFrame     # customer master from `Ranking` sheet
    full_demand: pd.DataFrame # full unfiltered demand (for downstream analysis)


def load_orders(
    quarter: str = "Q1",
    include_backorder: bool = True,
    xl_path: Path | None = None,
) -> RawData:
    """Read the master demand sheet and the customer ranking, filter to horizon.

    The filter keeps any row that has demand in the chosen quarter,
    plus (optionally) back-order rows that must clear in that quarter.
    """
    path = Path(xl_path) if xl_path else XL_PATH_DEFAULT
    if quarter not in QUARTER_VOL_COLS:
        raise ValueError(f"quarter must be one of Q1..Q4, got {quarter!r}")

    full = pd.read_excel(path, sheet_name="BO & FC 2025", header=0)
    full = full.rename(columns={COUNTRY_COL: "Country"})

    vol_col = QUARTER_VOL_COLS[quarter]
    mask = full[vol_col].fillna(0) > 0
    if include_backorder and quarter == "Q1":
        mask = mask | (full[BO_COL].fillna(0) > 0)

    orders = full.loc[mask].copy()
    orders = orders.reset_index(drop=True)
    orders.index.name = "order_id"

    ranking = pd.read_excel(path, sheet_name="Ranking", header=0)

    return RawData(orders=orders, ranking=ranking, full_demand=full)
