"""Baseline analytical schema and deterministic normalisation (no I/O).

Defines the three analytical tables, how each workbook column maps to them, and
the row-level conversion and parsing rules. Rules follow
docs/metadata/data_dictionary_DRAFT.md and docs/architecture/baseline_contract.md
(section 3). Raw values are always preserved; derived numeric values are added
next to them and never replace them.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence

SHEET_INVESTMENTS = "Investments_data_50k"
SHEET_MEETINGS = "meeting_notes_20k"
SHEET_PERFORMANCE = "performance_data"

PARSE_OK, PARSE_NULL, PARSE_INVALID = "ok", "null", "invalid"
PARSE_STATUSES = (PARSE_OK, PARSE_NULL, PARSE_INVALID)

# Exact MOIC form: digits, optional decimals, trailing lowercase x (dictionary section 6).
_MOIC_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?x")
# Exact deal-size form: <number><k|M|B> USD, case-sensitive, single space (dictionary section 6).
_DEAL_SIZE_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)([kMB]) USD")
_UNIT_MULTIPLIER = {"k": Decimal(1_000), "M": Decimal(1_000_000), "B": Decimal(1_000_000_000)}


class IngestError(Exception):
    """A structural or data problem that must stop the build."""


# ---------------------------------------------------------------- parsers
def parse_moic(raw: Any) -> tuple[Optional[float], str]:
    """Numeric value of an exact `<number>x` string; NULL plus a status otherwise."""
    if raw is None:
        return None, PARSE_NULL
    if isinstance(raw, str) and _MOIC_RE.fullmatch(raw):
        return float(Decimal(raw[:-1])), PARSE_OK
    return None, PARSE_INVALID


def parse_deal_size(raw: Any) -> tuple[Optional[float], str]:
    """USD value of an exact `<number><k|M|B> USD` string; NULL plus a status otherwise."""
    if raw is None:
        return None, PARSE_NULL
    if isinstance(raw, str):
        m = _DEAL_SIZE_RE.fullmatch(raw)
        if m:
            return float(Decimal(m.group(1)) * _UNIT_MULTIPLIER[m.group(2)]), PARSE_OK
    return None, PARSE_INVALID


# ---------------------------------------------------------------- column specs
@dataclass(frozen=True)
class Col:
    source: str                  # workbook column name
    name: Optional[str]          # analytical column; None = checked but not stored
    sqltype: str                 # TEXT, INTEGER or REAL
    kind: str                    # text, int, real, bool, date, moic, dealsize, dup_client_id
    note: str = ""


@dataclass(frozen=True)
class TableSpec:
    table: str
    sheet: str
    cols: tuple[Col, ...]

    @property
    def source_columns(self) -> list[str]:
        return [c.source for c in self.cols]

    def stored(self) -> list[Col]:
        return [c for c in self.cols if c.name is not None]

    def derived(self) -> list[tuple[str, str, str, str]]:
        """(name, sqltype, source column, description) for every derived column, in order."""
        out = []
        for c in self.stored():
            if c.kind == "moic":
                out.append((f"{c.name}_num", "REAL", c.source, "numeric MOIC (text before x)"))
                out.append((f"{c.name}_parse_status", "TEXT", c.source, "ok / null / invalid"))
            elif c.kind == "dealsize":
                out.append((f"{c.name}_usd", "REAL", c.source, "USD value of the k/M/B estimate"))
                out.append((f"{c.name}_parse_status", "TEXT", c.source, "ok / null / invalid"))
        return out

    def column_names(self) -> list[str]:
        """Physical column order: source_row, stored source columns, derived columns."""
        return ["source_row"] + [c.name for c in self.stored()] + [d[0] for d in self.derived()]

    def date_columns(self) -> list[str]:
        return [c.name for c in self.stored() if c.kind == "date"]

    def ddl(self) -> str:
        parts = ["source_row INTEGER PRIMARY KEY"]
        parts += [f"{c.name} {c.sqltype}" for c in self.stored()]
        for name, sqltype, _src, _desc in self.derived():
            check = f" CHECK ({name} IN ('ok','null','invalid'))" if name.endswith("_parse_status") else ""
            parts.append(f"{name} {sqltype}{check}")
        return f"CREATE TABLE {self.table} (\n  " + ",\n  ".join(parts) + "\n)"


def _c(source, name, sqltype, kind, note=""):
    return Col(source, name, sqltype, kind, note)


INVESTMENTS = TableSpec("investments", SHEET_INVESTMENTS, (
    _c("Client_Id__c", None, "TEXT", "dup_client_id", "duplicate of client_id; verified identical, not stored"),
    _c("Client Name", "client_name", "TEXT", "text"),
    _c("Client_Group_Id_c", "group_id", "INTEGER", "int"),
    _c("deal_id", "deal_id", "TEXT", "text", "independent of deal_name (A5)"),
    _c("id_CapitalCall", "id_capital_call", "INTEGER", "int", "dictionary: a capital-call identifier within a deal; observed values are only 0/1 (a flag, not an identifier)"),
    _c("Investment_Amount_USD_for_agg", "investment_amount_usd_for_agg", "REAL", "real"),
    _c("Investment_Amount_Natural_Currency", "investment_amount_natural_currency", "REAL", "real"),
    _c("Natural_Currency_Code", "natural_currency_code", "TEXT", "text"),
    _c("Investment_Exchange_Rate", "investment_exchange_rate", "REAL", "real"),
    _c("dat_MinInvested", "dat_min_invested", "TEXT", "date", "meaning of 'Min' undocumented"),
    _c("deal_name", "deal_name", "TEXT", "text", "independent of deal_id (A5)"),
    _c("cod_lob", "cod_lob", "TEXT", "text"),
    _c("nam_lob", "nam_lob", "TEXT", "text"),
    _c("client_id", "client_id", "TEXT", "text", "canonical client identifier"),
    _c("flg_Realised", "flg_realised", "INTEGER", "bool", "meaning undocumented"),
    _c("ClientStatus", "client_status", "TEXT", "text", "relationship/record-level (A4); no client-wide status"),
    _c("AccountName", "account_name", "TEXT", "text"),
    _c("AccountName_org", "account_name_org", "TEXT", "text", "meaning undocumented"),
    _c("AccountRM", "account_rm", "TEXT", "text", "authoritative RM field (A1); record-level (A2)"),
    _c("AccountRM_org", "nonauth_account_rm_org", "TEXT", "text", "non-authoritative (A3); stored raw, unused"),
    _c("AccountOwnerId", "nonauth_account_owner_id", "TEXT", "text", "non-authoritative (A3); stored raw, unused"),
    _c("AccountRMEmail", "nonauth_account_rm_email", "TEXT", "text", "non-authoritative (A3); stored raw, unused"),
    _c("AccountRMEmail_org", "nonauth_account_rm_email_org", "TEXT", "text", "non-authoritative (A3); stored raw, unused"),
    _c("RM_Alias", "nonauth_rm_alias", "TEXT", "text", "non-authoritative (A3); stored raw, unused"),
))

MEETINGS = TableSpec("meetings", SHEET_MEETINGS, (
    _c("meeting_id", "meeting_id", "INTEGER", "int", "unique in this data; no documented key"),
    _c("date", "meeting_date", "TEXT", "date"),
    _c("attendees", "attendees", "TEXT", "text", "raw text as in the workbook (no encoding repair, A11)"),
    _c("company", "company", "TEXT", "text"),
    _c("sector", "sector", "TEXT", "text"),
    _c("region", "region", "TEXT", "text"),
    _c("investment_stage", "investment_stage", "TEXT", "text"),
    _c("deal_size_estimate", "deal_size_estimate", "TEXT", "dealsize", "raw text kept; numeric value derived"),
    _c("summary", "summary", "TEXT", "text", "raw text as in the workbook (no encoding repair, A11)"),
    _c("action_items", "action_items", "TEXT", "text", "NULL = not recorded (A8)"),
    _c("summary_char_length", "summary_char_length", "INTEGER", "int"),
    _c("client_id", "client_id", "TEXT", "text", "canonical client identifier"),
    _c("group_id", "group_id", "INTEGER", "int"),
))

_PERFORMANCE_SOURCE = [
    "Client_Group_Id", "Client_Id", "Client_Name", "IsGroup_Flag", "CI_CY_FR_Amount", "CI_Current_IRR", "CI_FR_SI_Amount",
    "CI_Current_MOIC", "CI_L3Y_DIS_Amount", "CI_Total_IRR", "CI_L3Y_FR_Amount", "CI_Total_MOIC", "HF_AUM_Amount",
    "CI_Realised_IRR", "Mena_AUM_Amount", "CI_Realised_MOIC", "Pref_Shares_AUM_Amount", "CI_Since_2001_IRR",
    "RE_CY_FR_Amount", "HF_Total_MOIC", "RE_FR_SI_Amount", "HF_Total_IRR", "RE_L3Y_DIS_Amount", "RE_Core_IRR",
    "RE_L3Y_FR_Amount", "RE_Core_MOIC", "RE_AUM_Amount", "RE_Current_MOIC", "Receivables_Amount", "RE_Current_IRR",
    "Tech_AUM_Amount", "RE_Total_IRR", "As_Of_Date", "RE_Total_MOIC", "Client_Last_Met_Date", "RE_Realised_IRR",
    "Last_HF_Investment_Date", "RE_Realised_MOIC", "Last_CI_Investment_Date", "Last_CI_Investment_Name",
    "Last_CI_Investment_Amount", "Last_RE_Investment_Name", "Last_RE_Investment_Date", "First_CI_Investment_Name",
    "Last_RE_Investment_Amount", "First_RE_Investment_Name", "First_HF_Investment_Date", "Investment_Status_Name",
    "First_CI_Investment_Date", "CI_Status_Name", "First_RE_Investment_Date", "RE_Status_Name",
    "Call_Account_Balance_Amount", "INF_Status_Name", "Product_Count_Number", "ICM_Status_Name", "Total_AUM_Amount",
    "Future_Distribution_Amount", "CI_AUM_Amount", "COP_Total_IRR", "Last_COP_Investment_Name", "COP_Realised_IRR",
    "COP_Current_IRR", "COP_FR_SI_Amount", "Last_COP_Investment_Amount",
]
_PERFORMANCE_SPECIAL = {
    "Client_Group_Id": ("group_id", "INTEGER", "int", ""),
    "Client_Id": ("client_id", "TEXT", "text", "canonical client identifier; NULL on group rows"),
    "Client_Name": ("client_name", "TEXT", "text", "NULL on group rows"),
    "IsGroup_Flag": ("is_group_flag", "TEXT", "text", "Y = group row (kept), N = client snapshot"),
    "Product_Count_Number": ("product_count_number", "INTEGER", "int", ""),
    "As_Of_Date": ("as_of_date", "TEXT", "date", "latest is per client (A6), a query rule"),
    "Client_Last_Met_Date": ("client_last_met_date", "TEXT", "date", "not the last-met source (A13)"),
}


def _performance_col(source: str) -> Col:
    if source in _PERFORMANCE_SPECIAL:
        name, sqltype, kind, note = _PERFORMANCE_SPECIAL[source]
        return Col(source, name, sqltype, kind, note)
    name = source.lower()
    if source.endswith("_Amount") or source.endswith("_IRR"):
        return Col(source, name, "REAL", "real")
    if source.endswith("MOIC"):
        return Col(source, name, "TEXT", "moic", "raw text kept; numeric value derived")
    if source.endswith("_Date"):
        return Col(source, name, "TEXT", "date")
    if source.endswith("_Name"):
        return Col(source, name, "TEXT", "text")
    raise IngestError(f"performance column {source!r} has no mapping rule")


PERFORMANCE = TableSpec("performance", SHEET_PERFORMANCE, tuple(_performance_col(s) for s in _PERFORMANCE_SOURCE))

TABLE_SPECS = (INVESTMENTS, MEETINGS, PERFORMANCE)

INDEXES = {
    "investments": [("client_id",), ("group_id",), ("deal_id",), ("deal_name",), ("account_rm",)],
    "meetings": [("meeting_id",), ("client_id",), ("group_id",), ("meeting_date",)],
    "performance": [("client_id",), ("group_id",), ("as_of_date",), ("client_id", "as_of_date")],
}


def index_name(table: str, cols: Sequence[str]) -> str:
    return f"idx_{table}_{'_'.join(cols)}"


# ---------------------------------------------------------------- value conversion
def _fail(where: str, expected: str, value: Any) -> IngestError:
    return IngestError(f"{where}: expected {expected}, got {type(value).__name__} {str(value)[:40]!r}")


def convert_value(kind: str, value: Any, where: str) -> Any:
    """Convert one workbook cell to its stored form. Strict: unexpected types stop the build."""
    if value is None:
        return None
    if kind in ("text", "moic", "dealsize", "dup_client_id"):
        if not isinstance(value, str):
            raise _fail(where, "text", value)
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _fail(where, "integer", value)
        return value
    if kind == "real":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            raise _fail(where, "finite number", value)
        return float(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise _fail(where, "boolean", value)
        return int(value)
    if kind == "date":
        if isinstance(value, dt.datetime):
            return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat(sep=" ")
        if isinstance(value, dt.date):
            return value.isoformat()
        raise _fail(where, "date", value)
    raise IngestError(f"{where}: unknown column kind {kind!r}")


def check_header(spec: TableSpec, header: Sequence[Any]) -> None:
    names = list(header)
    if len(set(names)) != len(names):
        raise IngestError(f"sheet {spec.sheet!r}: duplicate column names in header")
    expected, actual = set(spec.source_columns), set(names)
    if expected != actual:
        raise IngestError(f"sheet {spec.sheet!r}: header mismatch; missing {sorted(expected - actual)}, unexpected {sorted(actual - expected, key=str)}")


def transform_rows(spec: TableSpec, header: Sequence[str], raw_rows: Sequence[Sequence[Any]], first_source_row: int = 2) -> list[tuple]:
    """Workbook rows -> analytical rows in `spec.column_names()` order. Nothing is dropped or repaired."""
    check_header(spec, header)
    index = {name: i for i, name in enumerate(header)}
    out = []
    for n, raw in enumerate(raw_rows, start=first_source_row):
        values: list[Any] = [n]
        derived: list[Any] = []
        for col in spec.cols:
            cell = raw[index[col.source]]
            where = f"{spec.sheet} row {n} column {col.source!r}"
            if col.kind == "dup_client_id":
                convert_value("text", cell, where)
                if cell != raw[index["client_id"]]:
                    raise IngestError(f"{where}: Client_Id__c differs from client_id (canonical identifier would be ambiguous)")
                continue
            stored = convert_value(col.kind, cell, where)
            values.append(stored)
            if col.kind == "moic":
                num, status = parse_moic(stored)
                derived += [num, status]
            elif col.kind == "dealsize":
                num, status = parse_deal_size(stored)
                derived += [num, status]
        out.append(tuple(values + derived))
    return out


def lineage_rows(spec: TableSpec) -> list[tuple[str, Optional[str], str, str, str, str]]:
    """(table, column, source sheet, source column, kind, note) for the lineage table."""
    rows = [(spec.table, "source_row", spec.sheet, "(Excel row number)", "row reference", "row number in the sheet; not a business key")]
    for c in spec.cols:
        rows.append((spec.table, c.name, spec.sheet, c.source, c.kind if c.name else "not stored", c.note))
    for name, _t, src, desc in spec.derived():
        rows.append((spec.table, name, spec.sheet, src, "derived", desc))
    return rows
