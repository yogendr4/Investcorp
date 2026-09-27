"""Approved schema for the Baseline structured-query engine (no I/O).

The LLM may only query the objects defined here:
  investments          (approved columns only; RM/status per record)
  performance_client   client snapshots (group rows excluded)
  performance_latest   one row per client: the client's own latest as_of_date
  performance_group    group rows only
Performance MOIC columns are exposed as NUMERIC values under their natural names; the raw text
MOIC columns are not exposed, so text comparison is impossible. Column lists come from data_schema.
Meetings are not part of this slice. Descriptions follow docs/metadata/data_dictionary_DRAFT.md.
"""
from __future__ import annotations

import re
from typing import Optional

from .data_schema import INVESTMENTS, PERFORMANCE

INVESTMENT_ROUTE, PERFORMANCE_ROUTE = "investment", "performance"
INVESTMENTS_TABLE = "investments"
PERF_VIEWS = ("performance_client", "performance_latest", "performance_group")
VIEW_BASE_TABLE = {v: "performance" for v in PERF_VIEWS}

_INVESTMENT_HIDDEN = {c.name for c in INVESTMENTS.stored() if c.name.startswith("nonauth_")} | {"account_name_org"}
_PERF_HIDDEN = {
    "client_last_met_date", "is_group_flag",      # last-met comes from meetings (A13); the flag is applied by the views
    # Abhishek's clarification: performance First_*/Last_*_Investment_Date are not usable application facts.
    # First/last investment facts, if asked, come from investments.dat_min_invested instead (decision 1); no SQL
    # route derives that aggregate today, so such questions remain unsupported rather than answered from these columns.
    "first_ci_investment_date", "last_ci_investment_date",
    "first_re_investment_date", "last_re_investment_date",
    "first_hf_investment_date", "last_hf_investment_date",
}
_PERF_GROUP_HIDDEN = {"client_id", "client_name"}

INVESTMENT_DESCRIPTIONS = {
    "client_id": "client identifier such as A12345",
    "client_name": "client name form Client_A12345",
    "group_id": "group identifier",
    "deal_id": "deal identifier (8 values); independent of deal_name; one deal_id can carry several deal_name values",
    "id_capital_call": "0/1 flag; the official dictionary describes it as a capital-call identifier within a deal, but observed values are only 0 or 1, so it cannot identify a specific capital call",
    "investment_amount_usd_for_agg": "amount in USD intended for aggregation; whether committed, called or invested is undocumented",
    "investment_amount_natural_currency": "amount in the record's own currency; add up only within one natural_currency_code",
    "natural_currency_code": "AED, EUR, GBP, INR, SGD or USD",
    "investment_exchange_rate": "fixed rate per currency to USD",
    "dat_min_invested": "ISO date text; the meaning of 'Min' is undocumented",
    "deal_name": "deal name (9 values); independent of deal_id",
    "cod_lob": "line-of-business code: PE=Private Equity, HF=Hedge Fund, RE=Real Estate, COP=Credit Opportunity, INF=Infrastructure",
    "nam_lob": "line-of-business name as stored in the source; its raw text does not match cod_lob's business meaning above, use cod_lob for LOB meaning",
    "flg_realised": "0/1 flag, meaning undocumented",
    "client_status": "Active, Dormant, Prospect or Closed; a property of the RECORD (a client has records with every status), never of the client",
    "account_name": "account name form 'Client A12345 Holdings'",
    "account_rm": "the authoritative relationship-manager field; a property of the RECORD (a client has records under several RMs)",
}


def investment_columns() -> dict[str, str]:
    """name -> SQL type for the approved investments columns."""
    return {c.name: c.sqltype for c in INVESTMENTS.stored() if c.name not in _INVESTMENT_HIDDEN}


def _perf_cols(group: bool) -> list[tuple[str, str, str, str]]:
    """(name, sqltype, kind, select expression) for a performance view."""
    out = []
    for c in PERFORMANCE.stored():
        if c.name in _PERF_HIDDEN or (group and c.name in _PERF_GROUP_HIDDEN):
            continue
        if c.kind == "moic":
            out.append((c.name, "REAL", "moic", f"{c.name}_num AS {c.name}"))
        else:
            out.append((c.name, c.sqltype, c.kind, c.name))
    return out


def performance_columns(group: bool = False) -> dict[str, str]:
    return {name: t for name, t, _k, _e in _perf_cols(group)}


def approved_columns() -> dict[str, set[str]]:
    return {INVESTMENTS_TABLE: set(investment_columns()),
            "performance_client": set(performance_columns()), "performance_latest": set(performance_columns()),
            "performance_group": set(performance_columns(group=True))}


def view_ddl() -> list[str]:
    """TEMP views that apply the deterministic performance rules (group-row exclusion, latest per client, numeric MOIC)."""
    cols = _perf_cols(False)
    sel = ", ".join(e for *_x, e in cols)
    sel_p = ", ".join(f"p.{e}" if " AS " not in e else f"p.{e}" for *_x, e in cols)
    gsel = ", ".join(e for *_x, e in _perf_cols(True))
    return [
        f"CREATE TEMP VIEW performance_client AS SELECT {sel} FROM main.performance WHERE is_group_flag = 'N'",
        f"CREATE TEMP VIEW performance_latest AS SELECT {sel_p} FROM main.performance p JOIN "
        f"(SELECT client_id AS cid, MAX(as_of_date) AS md FROM main.performance WHERE is_group_flag = 'N' GROUP BY client_id) m "
        f"ON p.client_id = m.cid AND p.as_of_date = m.md WHERE p.is_group_flag = 'N'",
        f"CREATE TEMP VIEW performance_group AS SELECT {gsel} FROM main.performance WHERE is_group_flag = 'Y'",
    ]


# ---------------------------------------------------------------- metric availability (dictionary section 7)
_METRIC_COL = re.compile(r"^(ci|hf|re|cop)_(current|total|realised|core|since_2001)_(moic|irr)$")
_AUM_COL = re.compile(r"^(ci|hf|re|mena|tech|pref_shares)_aum_amount$")
FAMILIES = ("ci", "hf", "re", "cop", "inf", "icm", "pe", "mena", "tech", "pref_shares")


def available_metrics() -> set[tuple[str, Optional[str], str]]:
    """(family, variant or None, metric) for every metric column that exists, derived from the performance columns."""
    out = set()
    for name in performance_columns():
        m = _METRIC_COL.match(name)
        if m:
            out.add((m.group(1), m.group(2), m.group(3)))
            out.add((m.group(1), None, m.group(3)))
        a = _AUM_COL.match(name)
        if a:
            out.add((a.group(1), None, "aum"))
    return out


_REQUEST = re.compile(r"\b(ci|hf|re|cop|inf|icm|pe|mena|tech|pref(?:erence)?[\s_]+shares?)\b[\s_]+(?:(current|total|realised|realized|core|since[\s_]+2001)[\s_]+)?(moic|irr|aum)\b", re.I)


def check_metric_availability(question: str) -> Optional[dict]:
    """None if every family+metric the question names exists; otherwise a description of the first unavailable one."""
    have = available_metrics()
    for m in _REQUEST.finditer(question):
        family = re.sub(r"[\s_]+", "_", m.group(1).lower()).replace("preference", "pref")
        variant = re.sub(r"[\s_]+", "_", (m.group(2) or "").lower()).replace("realized", "realised") or None
        metric = m.group(3).lower()
        key_variant = None if metric == "aum" else variant
        if (family, key_variant, metric) not in have:
            exist = sorted({f"{f} {v or ''} {mt}".replace("  ", " ").strip() for f, v, mt in have if f == family and mt == metric and v})
            return {"requested": m.group(0), "family": family, "variant": variant, "metric": metric,
                    "message": f"'{m.group(0)}' does not exist in the performance data; no other metric is substituted",
                    "existing_for_family_and_metric": exist}
    return None


# ---------------------------------------------------------------- prompt context
def _availability_text() -> str:
    have = available_metrics()
    parts = []
    for metric in ("moic", "irr", "aum"):
        fams = []
        for f in FAMILIES:
            variants = sorted(v for ff, v, mt in have if ff == f and mt == metric and v)
            if variants:
                fams.append(f"{f.upper()}({'/'.join(v.replace('_', ' ') for v in variants)})")
            elif (f, None, metric) in have:
                fams.append(f.upper())
        parts.append(f"{metric.upper()}: {', '.join(fams)}")
    return "Metrics that exist: " + "; ".join(parts) + ". Any other family/metric combination (for example COP MOIC) does not exist: do not substitute, answer unsupported."


def build_schema_context(route: str) -> str:
    if route == INVESTMENT_ROUTE:
        lines = ["TABLE investments  (one row per investment record; a row is NOT a deal and NOT a client; 50,000 rows, 192 clients)"]
        for name, t in investment_columns().items():
            lines.append(f"  {name} {t} - {INVESTMENT_DESCRIPTIONS[name]}")
        return "\n".join(lines)
    if route == PERFORMANCE_ROUTE:
        cols = _perf_cols(False)
        def group(kind, pred=None):
            return ", ".join(n for n, _t, k, _e in cols if k == kind and (pred is None or pred(n)))
        lines = [
            "VIEW performance_latest  (ONE row per client: that client's own latest as_of_date; use for 'latest' and 'current')",
            "VIEW performance_client  (all snapshots of clients, 8 per client; group rows excluded; use for history)",
            "VIEW performance_group   (group rows only: one row per group_id, no client_id; use only for group questions)",
            "All three views have the same columns (performance_group has no client_id or client_name):",
            "  client_id TEXT, client_name TEXT, group_id INTEGER, as_of_date TEXT (ISO date of the snapshot), product_count_number INTEGER",
            f"  MOIC, REAL, already NUMERIC (2.26 means 2.26x): {group('moic')}",
            f"  IRR, REAL, a fraction (0.0684, unit undocumented): {group('real', lambda n: n.endswith('_irr'))}",
            f"  Amounts, REAL, currency not stated: {group('real', lambda n: n.endswith('_amount'))}",
            "  total_aum_amount is NOT the sum of the LOB *_aum_amount fields in this data (non-additive; never derive one from the other, use the field the question names)",
            f"  Status text: {group('text', lambda n: n.endswith('_status_name'))}",
            f"  Fund names text: {group('text', lambda n: n.endswith('_investment_name'))}  (no reliable key to relate these names to another sheet's deal names; never join across sources)",
            "  First_*/Last_*_Investment_Date are not exposed here: they are not usable application facts (Abhishek's clarification); "
            "first/last investment date questions are unsupported in this data model, not derived from these columns",
            _availability_text(),
        ]
        return "\n".join(lines)
    raise ValueError(f"no schema context for route {route!r}")
