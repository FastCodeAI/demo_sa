"""Canonicalise the noisy Excel inputs into consistent codes."""
from __future__ import annotations

import pandas as pd

MACHINE_CANONICAL = {
    "Marchesini GL": "Marchesini_GL",
    "Marchesini_GL": "Marchesini_GL",
    "Farcon": "Farcon",
    "Dividella": "Dividella",
    "Partena": "Partena",
}

FORMAT_CANONICAL = {
    "0002ml": "2ml",
    "0003ml": "3ml",
    "0005ml": "5ml",
    "0010ml": "10ml",
    "0020ml": "20ml",
    "2ml": "2ml",
    "3ml": "3ml",
    "5ml": "5ml",
    "10ml": "10ml",
    "20ml": "20ml",
}

FORMAT_VOLUME_ML = {
    "2ml": 2.0,
    "3ml": 3.0,
    "5ml": 5.0,
    "10ml": 10.0,
    "20ml": 20.0,
}

UK_ALIASES = {
    "UNITED KINGDOM - TROTWOOD": "United Kingdom",
}

ORG_ALIASES = {
    "IDA FOUNDATION": "IDA",
}


def canonicalise_machine(m: object) -> str | None:
    if pd.isna(m):
        return None
    s = str(m).strip()
    return MACHINE_CANONICAL.get(s, s)


def canonicalise_format(f: object) -> str | None:
    if pd.isna(f):
        return None
    s = str(f).strip()
    return FORMAT_CANONICAL.get(s, s)


def canonicalise_customer(c: object) -> str:
    if pd.isna(c):
        return "Unknown"
    s = str(c).strip()
    s = UK_ALIASES.get(s, s)
    s = ORG_ALIASES.get(s, s)
    return s


def add_canonical_columns(orders: pd.DataFrame) -> pd.DataFrame:
    out = orders.copy()
    out["machine_pref"] = out["packaging line"].map(canonicalise_machine)
    out["format"] = out["Mould / Volume"].map(canonicalise_format)
    out["customer"] = out["Country"].map(canonicalise_customer)
    return out


def is_org_no_split(customer: str, orgs: list[str]) -> bool:
    cu = customer.casefold()
    return any(o.casefold() in cu for o in orgs)


def is_piramal(customer: str) -> bool:
    return "piramal" in str(customer).casefold()
