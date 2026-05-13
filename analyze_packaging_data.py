"""
Exploratory analyzer for the DEMO Pharma 2025 packaging dataset.

Reads `Packaging Ampoules.xlsx` from the script directory, profiles every
sheet, computes demand breakdowns and the Piramal monthly-band check, and
writes CSVs / PNGs to `outputs/`. This is a baseline before the MILP/CP
optimizer is built — see README.md for the modeling plan.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
XL_PATH = ROOT / "Packaging Ampoules.xlsx"
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

COUNTRY_COL = "Country\nΧώρα έγκρισης"
TOTAL_COL = "Total Quantity for packaging 2025"
QUARTER_COLS = ["BO 2024", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]


def profile_all_sheets() -> None:
    xl = pd.ExcelFile(XL_PATH)
    lines: list[str] = []
    for s in xl.sheet_names:
        raw = pd.read_excel(xl, sheet_name=s, header=None)
        lines.append(f"=== {s} === shape={raw.shape}")
        lines.append(raw.head(5).to_string(max_cols=12, max_colwidth=30))
        lines.append("")
    (OUT / "sheet_profiles.txt").write_text("\n".join(lines))


def master_demand_analysis() -> dict:
    df = pd.read_excel(XL_PATH, sheet_name="BO & FC 2025", header=0)
    df = df.rename(columns={COUNTRY_COL: "Country"})

    by_period = pd.Series({c: df[c].sum() for c in QUARTER_COLS}, name="volume")
    by_period.to_frame().to_csv(OUT / "demand_by_quarter.csv")

    by_country = (
        df.groupby("Country", dropna=False)[TOTAL_COL]
        .sum()
        .sort_values(ascending=False)
    )
    by_country.head(30).to_frame("volume").to_csv(OUT / "demand_by_country.csv")

    by_sku = (
        df.groupby(["Material Number", "SAP Material Description"], dropna=False)[TOTAL_COL]
        .sum()
        .sort_values(ascending=False)
    )
    by_sku.head(40).to_frame("volume").to_csv(OUT / "demand_by_sku.csv")

    by_line = (
        df.groupby("packaging line", dropna=False)[TOTAL_COL]
        .sum()
        .sort_values(ascending=False)
    )
    by_line.to_frame("volume").to_csv(OUT / "demand_by_packaging_line.csv")

    by_format = (
        df.groupby("Mould / Volume", dropna=False)[TOTAL_COL]
        .sum()
        .sort_values(ascending=False)
    )
    by_format.to_frame("volume").to_csv(OUT / "demand_by_format.csv")

    df["Final Factor"].value_counts(dropna=False).to_frame("count").to_csv(
        OUT / "rating_final_factor.csv"
    )
    df["VIP"].value_counts(dropna=False).to_frame("count").to_csv(
        OUT / "rating_vip.csv"
    )

    plt.figure(figsize=(8, 5))
    by_period.plot(kind="bar", color="steelblue")
    plt.title("2025 Demand by Period (units)")
    plt.ylabel("Units")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(OUT / "demand_by_quarter.png", dpi=120)
    plt.close()

    plt.figure(figsize=(10, 8))
    by_country.head(20).iloc[::-1].plot(kind="barh", color="steelblue")
    plt.title("Top 20 Countries / Customers — 2025 Total Packaging Volume")
    plt.xlabel("Units")
    plt.tight_layout()
    plt.savefig(OUT / "top20_countries.png", dpi=120)
    plt.close()

    sku_top = by_sku.head(20)
    plt.figure(figsize=(11, 8))
    labels = [f"{int(m) if pd.notna(m) else '?'} – {(d or '')[:35]}" for (m, d) in sku_top.index]
    plt.barh(range(len(sku_top))[::-1], sku_top.values, color="steelblue")
    plt.yticks(range(len(sku_top))[::-1], labels)
    plt.title("Top 20 SKUs — 2025 Total Packaging Volume")
    plt.xlabel("Units")
    plt.tight_layout()
    plt.savefig(OUT / "top20_skus.png", dpi=120)
    plt.close()

    plt.figure(figsize=(8, 5))
    by_line.dropna().plot(kind="bar", color="steelblue")
    plt.title("2025 Demand by Packaging Line")
    plt.ylabel("Units")
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(OUT / "demand_by_packaging_line.png", dpi=120)
    plt.close()

    return {
        "df": df,
        "by_period": by_period,
        "by_country": by_country,
        "by_sku": by_sku,
        "by_line": by_line,
        "by_format": by_format,
    }


def piramal_monthly_check(df: pd.DataFrame) -> pd.DataFrame:
    pir = df[df["Country"].astype(str).str.contains("Piramal", case=False, na=False)]
    rows = []
    quarter_to_months = {"Q1 2025": (1, 2, 3), "Q2 2025": (4, 5, 6), "Q3 2025": (7, 8, 9), "Q4 2025": (10, 11, 12)}
    for qcol, months in quarter_to_months.items():
        per_month = pir[qcol].sum() / 3.0
        for m in months:
            if per_month < 1_800_000:
                verdict = "under-band"
            elif per_month > 2_000_000:
                verdict = "over-band"
            else:
                verdict = "OK"
            rows.append({"month": f"M{m:02d}", "volume": round(per_month), "verdict": verdict})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "piramal_monthly_check.csv", index=False)
    return out


def machine_pivots() -> dict:
    sheets = [("Marchesini", 2), ("FARCON-DIVIDELLA", 3), ("Partena", 3)]
    summary: dict[str, pd.Series] = {}
    for name, header_row in sheets:
        df = pd.read_excel(XL_PATH, sheet_name=name, header=header_row)
        df = df.dropna(how="all").reset_index(drop=True)
        df.to_csv(OUT / f"machine_pivot_{name}.csv", index=False)
        slot_cols = [c for c in df.columns if isinstance(c, str) and c.startswith("Sum of Pack slot")]
        if slot_cols:
            numeric = df[slot_cols].apply(pd.to_numeric, errors="coerce")
            summary[name] = numeric.sum()
        else:
            summary[name] = pd.Series(dtype=float)
    pd.DataFrame(summary).fillna(0).to_csv(OUT / "machine_pivot_totals.csv")
    return summary


def isd_forecast() -> pd.DataFrame:
    df = pd.read_excel(XL_PATH, sheet_name="ISD Forecast Totals 2025", header=0)
    df.columns = ["mode_line", "volume_2025"]
    df = df.dropna(subset=["mode_line"])
    df = df[df["mode_line"].astype(str).str.lower() != "grand total"]
    df.to_csv(OUT / "isd_forecast_totals.csv", index=False)

    plt.figure(figsize=(8, 5))
    plt.bar(df["mode_line"].astype(str), df["volume_2025"], color="steelblue")
    plt.xticks(rotation=25, ha="right")
    plt.title("ISD 2025 Forecast — Volume by Mode Line")
    plt.ylabel("Units")
    plt.tight_layout()
    plt.savefig(OUT / "isd_forecast.png", dpi=120)
    plt.close()
    return df


def fmt(v) -> str:
    return f"{v:,.0f}" if isinstance(v, (int, float, np.integer, np.floating)) and pd.notna(v) else str(v)


def main() -> None:
    print("Profiling sheets...")
    profile_all_sheets()

    print("Master demand analysis...")
    res = master_demand_analysis()
    df = res["df"]

    total = df[TOTAL_COL].sum()
    print(f"\nTotal 2025 packaging volume: {fmt(total)}")
    print("\nDemand by period (units):")
    print(res["by_period"].apply(fmt).to_string())
    print("\nTop 5 countries / customers:")
    print(res["by_country"].head(5).apply(fmt).to_string())
    print("\nDemand by packaging line:")
    print(res["by_line"].apply(fmt).to_string())
    print("\nDemand by format (top 8):")
    print(res["by_format"].head(8).apply(fmt).to_string())

    print("\nMachine-pivot per-quarter pack-slot sums:")
    summary = machine_pivots()
    for name, s in summary.items():
        if s.empty:
            print(f"  {name}: (no Sum-of-Pack-slot columns)")
        else:
            print(f"  {name}: " + ", ".join(f"{k}={int(v)}" for k, v in s.items()))

    isd = isd_forecast()
    print("\nISD forecast totals 2025:")
    print(isd.assign(volume_2025=isd["volume_2025"].apply(fmt)).to_string(index=False))

    print("\nPiramal monthly band check (1.8M–2.0M amp/month, req #37):")
    pir = piramal_monthly_check(df)
    pir_disp = pir.assign(volume=pir["volume"].apply(fmt))
    print(pir_disp.to_string(index=False))

    print("\nNote: capacity (machine-hours) check requires units/hour rates")
    print("from ProdAction (req #6) — not present in this Excel.")
    print(f"\nDone. Outputs written to {OUT}")


if __name__ == "__main__":
    main()
