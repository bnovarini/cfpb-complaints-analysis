"""MCP server over the CFPB Consumer Complaint Database.

Structured data: CFPB's public complaint file (2011 to present, refreshed by rebuilding).
Narratives: CFPB's FOIA Reading Room archive of narratives published through 2026-08-14.
CFPB stopped publishing narratives in September 2026, so the narrative layer is frozen at that date.
DuckDB queries the Parquet files directly.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Optional

import duckdb
from mcp.server.fastmcp import FastMCP

RELEASE = "2026-10-07"  # data snapshot; rebuild with `cfpb-complaints build` to refresh
BASE_URL = os.environ.get("CFPB_DATA_URL", "https://cfpb-complaints-analysis.fly.dev/data/")
FILES = ["complaints.parquet", *[f"narratives_{i:02d}.parquet" for i in range(1, 22)]]
MAX_ROWS = 100
QUERY_TIMEOUT_S = float(os.environ.get("CFPB_QUERY_TIMEOUT", "25"))
NARRATIVE_FREEZE = "2026-08-14"

NOTE = (
    "Data: CFPB Consumer Complaint Database (complaints received 2011-12-01 onward, structured fields as CFPB publishes them) "
    "plus consumer narratives from CFPB's FOIA Reading Room archive. CFPB stopped publishing narratives in September 2026: "
    "narratives exist only for complaints CFPB had published with a narrative through 2026-08-14 (3.85M of them, none before 2015), so a complaint with no "
    "narrative may be newer, may lack consumer consent, or may be pre-2015. Complaints are unverified consumer allegations, and a "
    "company's count is not adjusted for its size. Narratives have personal data masked as XXXX by CFPB."
)
mcp = FastMCP("cfpb-complaints-analysis", instructions=NOTE)

_con: Optional[duckdb.DuckDBPyConnection] = None


def data_dir() -> Path:
    env = os.environ.get("CFPB_DATA_DIR")
    d = Path(env) if env else Path.home() / ".cache" / "cfpb-complaints-analysis" / RELEASE
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_files() -> Path:
    d = data_dir()
    for f in FILES:
        p = d / f
        if not p.exists():
            tmp = p.with_suffix(".part")
            urllib.request.urlretrieve(BASE_URL + f, tmp)
            tmp.rename(p)
    return d


def con() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        d = ensure_files()
        c = duckdb.connect()
        c.execute(f"SET memory_limit='{os.environ.get('CFPB_MEMORY_LIMIT', '1GB')}'")
        c.execute(f"SET threads={int(os.environ.get('CFPB_THREADS', '2'))}")
        c.execute(f"CREATE VIEW c AS SELECT * FROM read_parquet('{d}/complaints.parquet')")
        c.execute(f"CREATE VIEW n AS SELECT * FROM read_parquet('{d}/narratives_*.parquet')")
        _con = c
    return _con


def run(sql: str, params: list[Any] | None = None) -> list[dict]:
    cur = con().cursor()
    timer = threading.Timer(QUERY_TIMEOUT_S, cur.interrupt)
    timer.start()
    try:
        cur.execute(sql, params or [])
        rows = cur.fetchall()
    except duckdb.InterruptException:
        raise RuntimeError(f"Query took longer than {QUERY_TIMEOUT_S:.0f}s and was stopped. Add a date range, company or product filter.")
    finally:
        timer.cancel()
    cols = [x[0] for x in cur.description]
    return [{k: (round(v, 4) if isinstance(v, float) else (v.isoformat() if isinstance(v, date) else v)) for k, v in zip(cols, r)} for r in rows]


# ---------------- names and filters ----------------
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI UM FM MH PW AA AE AP".split())
GROUPS = {
    "year": "year(c.date_received)", "quarter": "strftime(c.date_received, '%Y') || '-Q' || cast(quarter(c.date_received) AS VARCHAR)",
    "month": "strftime(c.date_received, '%Y-%m')", "product": "c.product", "sub_product": "c.sub_product", "issue": "c.issue",
    "sub_issue": "c.sub_issue", "company": "c.company", "state": "c.state", "company_response": "c.company_response",
    "company_public_response": "c.company_public_response", "submitted_via": "c.submitted_via", "timely_response": "c.timely_response",
}
STATUS_HINT = "In progress and Untimely response are CFPB response categories, not outcomes."


def _d(v: Optional[str], name: str, end: bool = False) -> Optional[str]:
    """YYYY-MM-DD passes through. YYYY-MM means the first day, or the last day when end=True (date_to)."""
    if v is None or v == "":
        return None
    if not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", v):
        raise ValueError(f"{name} must be YYYY-MM-DD or YYYY-MM, got {v!r}")
    try:
        if len(v) == 7:
            y, m = int(v[:4]), int(v[5:])
            if end:
                import calendar
                return f"{v}-{calendar.monthrange(y, m)[1]:02d}"
            date(y, m, 1)
            return v + "-01"
        date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{name} is not a real date: {v!r}")
    return v


def _filters(company=None, company_contains=None, product=None, sub_product=None, issue=None, state=None,
             date_from=None, date_to=None, company_response=None, timely=None, submitted_via=None, tag=None,
             has_narrative=None, prefix="c."):
    w: list[str] = []
    p: list[Any] = []
    if company:
        w.append(f"{prefix}company = ?"); p.append(company)
    if company_contains:
        w.append(f"{prefix}company ILIKE ? ESCAPE '\\'"); p.append(_like(company_contains))
    for col, v in (("product", product), ("sub_product", sub_product), ("issue", issue), ("company_response", company_response),
                   ("submitted_via", submitted_via)):
        if v:
            w.append(f"lower({prefix}{col}) = lower(?)"); p.append(v)
    if state:
        s = state.upper().strip()
        if s not in STATES:
            raise ValueError(f"state must be a two-letter code, got {state!r}")
        w.append(f"{prefix}state = ?"); p.append(s)
    f, t = _d(date_from, "date_from"), _d(date_to, "date_to", end=True)
    _check_range(f, t)
    if f:
        w.append(f"{prefix}date_received >= CAST(? AS DATE)"); p.append(f)
    if t:
        w.append(f"{prefix}date_received <= CAST(? AS DATE)"); p.append(t)
    if timely is not None:
        w.append(f"{prefix}timely_response = ?"); p.append("Yes" if timely else "No")
    if tag:
        w.append(f"{prefix}tags ILIKE ? ESCAPE '\\'"); p.append(_like(tag))
    if has_narrative is True:
        w.append(f"{prefix}complaint_id IN (SELECT complaint_id FROM n)")
    elif has_narrative is False:
        w.append(f"{prefix}complaint_id NOT IN (SELECT complaint_id FROM n)")
    return (" AND ".join(w) or "TRUE"), p


FILTER_DOC = (
    "Filters: company (exact name as in find_company), company_contains (substring), product, sub_product, issue, "
    "product: CFPB renamed credit reporting twice, so it appears under three product names (use list_values field=product), "
    "company_response (exact CFPB value, case-insensitive), submitted_via, state (2 letters), tag (e.g. 'Servicemember', 'Older American'), "
    "timely (true/false), date_from/date_to (YYYY-MM-DD or YYYY-MM, on date received)."
)

METRICS = (
    "count(*) AS complaints, "
    "round(avg(CASE WHEN c.timely_response='Yes' THEN 1.0 WHEN c.timely_response='No' THEN 0.0 END), 4) AS timely_rate, "
    "round(avg(CASE WHEN c.company_response='Closed with monetary relief' THEN 1.0 ELSE 0.0 END), 4) AS monetary_relief_rate, "
    "round(avg(CASE WHEN c.company_response='Closed with non-monetary relief' THEN 1.0 ELSE 0.0 END), 4) AS nonmonetary_relief_rate, "
    "round(avg(CASE WHEN c.company_response='In progress' THEN 1.0 ELSE 0.0 END), 4) AS in_progress_rate"
)


def _like(v: str) -> str:
    """Escape LIKE wildcards so user text is matched literally."""
    return "%" + v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _page(limit: int, offset: int) -> tuple[int, int]:
    if int(limit) < 1:
        raise ValueError(f"limit must be 1 or more, got {limit}")
    if int(offset) < 0:
        raise ValueError(f"offset must be 0 or more, got {offset}")
    return min(int(limit), MAX_ROWS), int(offset)


def _clamp_note(limit: int) -> list[dict]:
    return [{"limit_clamped": True, "message": f"limit {limit} is above the maximum of {MAX_ROWS}; returned at most {MAX_ROWS} rows. Use offset to page."}] if int(limit) > MAX_ROWS else []


def _check_range(f, t):
    if f and t and f > t:
        raise ValueError(f"date_from ({f}) is after date_to ({t})")


def _check_any_of(any_of):
    if any_of is not None and not [a for a in any_of if a and a.strip()]:
        raise ValueError("any_of is empty: pass at least one non-empty term, or leave it out")


_LAST: list = []


def _last_date():
    if not _LAST:
        _LAST.append(run("SELECT max(date_received) AS d FROM c")[0]["d"])
    v = _LAST[0]
    return date.fromisoformat(v) if isinstance(v, str) else v


def _company_hint(name: str) -> str:
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", name) if t]
    sug = []
    if toks:
        sug = [r["company"] for r in run("SELECT c.company, count(*) AS n FROM c WHERE " + " AND ".join("c.company ILIKE ?" for _ in toks)
                                         + " GROUP BY 1 ORDER BY 2 DESC LIMIT 5", [f"%{t}%" for t in toks])]
    if sug:
        return f"No company is named exactly {name!r}. Did you mean: {'; '.join(sug)}? (or use company_contains for all variants)"
    return f"No company is named exactly {name!r} and no similar name was found. Use find_company to search."


# ---------------- tools ----------------
@mcp.tool(description="What this dataset is: row counts, date ranges, narrative coverage, and the narrative freeze. Call first when unsure about coverage.")
def dataset_info() -> dict:
    r = run("SELECT count(*) AS complaints, min(date_received) AS first_received, max(date_received) AS last_received, "
            "count(DISTINCT company) AS companies, count(DISTINCT product) AS products FROM c")[0]
    nn = run("SELECT count(*) AS narratives, min(complaint_id) AS min_id FROM n")[0]
    r["narratives"] = nn["narratives"]
    r["narratives_linked_to_complaints"] = run("SELECT count(*) AS n FROM n WHERE complaint_id IN (SELECT complaint_id FROM c)")[0]["n"]
    r["narratives_note"] = (f"Narratives come from CFPB's FOIA Reading Room archive, frozen at {NARRATIVE_FREEZE}: CFPB stopped publishing "
                            "narratives in September 2026. Complaints published after that have none, and none exist before 2015. "
                            "The archive thins out near the end: about 17,000 narratives for each of May and June 2026, about 10,000 for July, and 1 for August 1-14, "
                            "so 2026 narrative counts are not comparable to earlier months. "
                            "A few narratives (see narratives minus narratives_linked_to_complaints) belong to complaints CFPB has since removed from its live file; "
                            "they have no structured fields, so they appear only in unfiltered narrative counts and get_complaint.")
    r["note"] = NOTE
    r["latest_complaint_year_partial"] = True
    return r


@mcp.tool(description="List the values of a field with complaint counts, to find exact product/issue/response names. "
                      "field is one of: product, sub_product, issue, sub_issue, company_response, company_public_response, submitted_via, state, timely_response. "
                      "Optional search narrows by substring; product narrows issues to that product. " + FILTER_DOC)
def list_values(field: str, search: Optional[str] = None, product: Optional[str] = None, limit: int = 50) -> list[dict]:
    allowed = ["product", "sub_product", "issue", "sub_issue", "company_response", "company_public_response", "submitted_via", "state", "timely_response"]
    if field not in allowed:
        raise ValueError(f"field must be one of {allowed}. For company names use find_company.")
    where, p = ["c." + field + " IS NOT NULL"], []
    if search:
        where.append(f"c.{field} ILIKE ? ESCAPE '\\'"); p.append(_like(search))
    if product:
        where.append("lower(c.product) = lower(?)"); p.append(product)
    limit, _ = _page(limit, 0)
    return run(f"SELECT c.{field} AS value, count(*) AS complaints FROM c WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY 2 DESC LIMIT {limit}", p)


@mcp.tool(description="Find companies by name. CFPB lists the same firm under variants and subsidiaries (e.g. 'EQUIFAX, INC.', 'Equifax Information Services LLC'), "
                      "so this returns every matching name with complaint counts and first/last dates; pass the exact names you want to other tools via company, "
                      "or use company_contains for all variants at once.")
def find_company(name: str, limit: int = 25) -> list[dict]:
    limit, _ = _page(limit, 0)
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", name) if t]
    if not toks:
        raise ValueError("name is empty")
    where = " AND ".join("c.company ILIKE ?" for _ in toks)
    rows = run(f"SELECT c.company, count(*) AS complaints, min(c.date_received) AS first_received, max(c.date_received) AS last_received "
               f"FROM c WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT {limit}", [f"%{t}%" for t in toks])
    if not rows:
        words = [t for t in toks if len(t) > 3] or toks
        alt = run("SELECT c.company, count(*) AS complaints FROM c WHERE " + " OR ".join("c.company ILIKE ?" for _ in words)
                  + " GROUP BY 1 ORDER BY 2 DESC LIMIT 5", [f"%{t}%" for t in words])
        return [{"message": f"No company name contains all of {toks}. " + ("Names matching any word: " + "; ".join(a["company"] for a in alt) if alt else "No similar names found."),
                 "complaints": 0}]
    return rows


@mcp.tool(description="Count complaints, optionally grouped. group_by is one or two of: " + ", ".join(GROUPS) + ". Returns complaints, timely_rate, "
                      "monetary_relief_rate, nonmonetary_relief_rate, in_progress_rate per group (rates are fractions of that group's complaints). "
                      "Counts are raw complaint counts, not adjusted for company size. " + STATUS_HINT + " " + FILTER_DOC)
def complaint_counts(group_by: Optional[list[str]] = None, company: Optional[str] = None, company_contains: Optional[str] = None,
                     product: Optional[str] = None, sub_product: Optional[str] = None, issue: Optional[str] = None, state: Optional[str] = None,
                     date_from: Optional[str] = None, date_to: Optional[str] = None, company_response: Optional[str] = None,
                     timely: Optional[bool] = None, submitted_via: Optional[str] = None, tag: Optional[str] = None,
                     has_narrative: Optional[bool] = None, order_by: str = "complaints", limit: int = 25, offset: int = 0) -> list[dict]:
    gb = group_by or []
    if len(gb) > 2:
        raise ValueError("group_by takes at most two fields")
    if len(set(gb)) != len(gb):
        raise ValueError(f"group_by has a repeated field: {gb}")
    if order_by not in ("complaints", "timely_rate", "monetary_relief_rate"):
        raise ValueError("order_by must be complaints, timely_rate or monetary_relief_rate")
    for g in gb:
        if g not in GROUPS:
            raise ValueError(f"unknown group_by {g!r}; choose from {list(GROUPS)}")
    requested = limit
    limit, offset = _page(limit, offset)
    where, p = _filters(company, company_contains, product, sub_product, issue, state, date_from, date_to, company_response, timely, submitted_via, tag, has_narrative)
    sel = ", ".join(f"{GROUPS[g]} AS {g}" for g in gb)
    grp = f" GROUP BY {', '.join(str(i + 1) for i in range(len(gb)))}" if gb else ""
    ob = order_by if order_by in ("complaints", "timely_rate", "monetary_relief_rate") else "complaints"
    chrono = gb and gb[0] in ("year", "quarter", "month")
    order = f" ORDER BY {'1 ASC' if chrono else ob + ' DESC'}" + (f", {ob} DESC" if chrono and len(gb) > 1 else "")
    rows = run(f"SELECT {sel + ', ' if sel else ''}{METRICS} FROM c WHERE {where}{grp}{order} LIMIT {limit + 1} OFFSET {offset}", p)
    if company and rows and not gb and not rows[0]["complaints"]:
        rows[0]["hint"] = _company_hint(company)
    elif tag and rows and not gb and not rows[0]["complaints"]:
        rows[0]["hint"] = "No complaints carry that tag with these filters. CFPB tags are 'Servicemember' and 'Older American' (a complaint can have both)."
    last = _last_date()
    for r in rows:
        for g, fmt in (("year", str(last.year)), ("month", last.strftime("%Y-%m")),
                       ("quarter", f"{last.year}-Q{(last.month - 1) // 3 + 1}")):
            if g in r and str(r[g]) == fmt:
                r["partial_period"] = f"{fmt} runs only through {last.isoformat()}; do not compare it to a full {g}."
    if len(rows) > limit:
        rows = rows[:limit]
        rows.append({"truncated": True, "next_offset": offset + limit, "message": "More groups exist; raise offset or narrow filters."})
    return rows + _clamp_note(requested)


@mcp.tool(description="Complaints over time (month, quarter or year), for one filter set. Set period to month, quarter or year. "
                      "The latest period is partial. " + STATUS_HINT + " " + FILTER_DOC)
def trend(period: str = "month", company: Optional[str] = None, company_contains: Optional[str] = None, product: Optional[str] = None,
          issue: Optional[str] = None, state: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
          company_response: Optional[str] = None, tag: Optional[str] = None, submitted_via: Optional[str] = None) -> list[dict]:
    if period not in ("month", "quarter", "year"):
        raise ValueError("period must be month, quarter or year")
    where, p = _filters(company, company_contains, product, None, issue, state, date_from, date_to, company_response, None, submitted_via, tag)
    rows = run(f"SELECT {GROUPS[period]} AS {period}, {METRICS} FROM c WHERE {where} GROUP BY 1 ORDER BY 1 LIMIT 1000", p)
    if not rows:
        msg = "No complaints match these filters."
        if company:
            msg += " " + _company_hint(company)
        elif company_contains:
            msg += " Use find_company to see which names exist."
        elif tag:
            msg += " CFPB tags are 'Servicemember' and 'Older American'."
        return [{"message": msg, "complaints": 0}]
    return rows


@mcp.tool(description="Profile of one company (or all name variants via company_contains): totals, first/last complaint, top products, issues, states, "
                      "response outcomes, yearly counts. Counts are not adjusted for company size. Optional date_from/date_to/product.")
def company_profile(company: Optional[str] = None, company_contains: Optional[str] = None, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, product: Optional[str] = None) -> dict:
    if not company and not company_contains:
        raise ValueError("pass company (exact) or company_contains")
    where, p = _filters(company, company_contains, product, None, None, None, date_from, date_to, None, None, None, None)
    tot = run(f"SELECT {METRICS}, min(c.date_received) AS first_received, max(c.date_received) AS last_received, count(DISTINCT c.company) AS name_variants FROM c WHERE {where}", p)[0]
    if not tot["complaints"]:
        return {"complaints": 0, "message": "No complaints match. Use find_company to see exact names."}
    def top(col, k=8):
        return run(f"SELECT c.{col} AS value, count(*) AS complaints FROM c WHERE {where} AND c.{col} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT {k}", p)
    return {**tot, "names_matched": run(f"SELECT c.company, count(*) AS complaints FROM c WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT 10", p),
            "top_products": top("product"), "top_issues": top("issue"), "top_states": top("state"), "responses": top("company_response", 10),
            "by_year": run(f"SELECT year(c.date_received) AS year, count(*) AS complaints FROM c WHERE {where} GROUP BY 1 ORDER BY 1", p),
            "narratives_available": run(f"SELECT count(*) AS n FROM c WHERE {where} AND c.complaint_id IN (SELECT complaint_id FROM n)", p)[0]["n"]}


@mcp.tool(description="Side-by-side comparison of up to 6 companies on the same filters: complaints, timely_rate, relief rates, top product and top issue each. "
                      "Each entry in companies is matched as a substring on the company name (all variants combined). Counts are not adjusted for company size.")
def compare_companies(companies: list[str], product: Optional[str] = None, issue: Optional[str] = None, state: Optional[str] = None,
                      date_from: Optional[str] = None, date_to: Optional[str] = None) -> list[dict]:
    if not companies or len(companies) > 6:
        raise ValueError("pass 1 to 6 company names")
    out = []
    for name in companies:
        where, p = _filters(None, name, product, None, issue, state, date_from, date_to, None, None, None, None)
        r = run(f"SELECT {METRICS}, count(DISTINCT c.company) AS name_variants FROM c WHERE {where}", p)[0]
        r = {"company_search": name, **r}
        if r["complaints"]:
            r["top_product"] = run(f"SELECT c.product AS v FROM c WHERE {where} GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", p)[0]["v"]
            r["top_issue"] = run(f"SELECT c.issue AS v FROM c WHERE {where} GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", p)[0]["v"]
        out.append(r)
    return out


def _snippet(text: str, terms: list[str], width: int = 450) -> str:
    low = text.lower()
    pos = min([i for i in (low.find(t.lower()) for t in terms) if i >= 0] or [0])
    s = max(0, pos - width // 3)
    seg = text[s:s + width].strip()
    return ("..." if s else "") + seg + ("..." if s + width < len(text) else "")


def _narr_where(words, phrase, any_of, company, company_contains, product, state, date_from, date_to):
    """Conditions on the narratives table itself. It carries date, company, product and state, sorted by date,
    so these filters skip most of the 3.9 GB of text before any keyword is matched."""
    w: list[str] = []
    p: list[Any] = []
    if company:
        w.append("n.company = ?"); p.append(company)
    if company_contains:
        w.append("n.company ILIKE ? ESCAPE '\\'"); p.append(_like(company_contains))
    if product:
        w.append("lower(n.product) = lower(?)"); p.append(product)
    if state:
        st = state.upper().strip()
        if st not in STATES:
            raise ValueError(f"state must be a two-letter code, got {state!r}")
        w.append("n.state = ?"); p.append(st)
    f, t = _d(date_from, "date_from"), _d(date_to, "date_to", end=True)
    _check_range(f, t)
    if f:
        w.append("n.date_received >= CAST(? AS DATE)"); p.append(f)
    if t:
        w.append("n.date_received <= CAST(? AS DATE)"); p.append(t)
    for x in words:
        w.append("contains(lower(n.narrative), ?)"); p.append(x.lower())
    if phrase:
        w.append("contains(lower(n.narrative), ?)"); p.append(phrase.lower())
    if any_of:
        w.append("(" + " OR ".join("contains(lower(n.narrative), ?)" for _ in any_of) + ")"); p += [a.lower() for a in any_of]
    return (" AND ".join(w) or "TRUE"), p


@mcp.tool(description="Keyword search over archived complaint narratives (frozen at " + NARRATIVE_FREEZE + "; CFPB stopped publishing them in September 2026). "
                      "query: words that must all appear (case-insensitive), or use phrase for an exact phrase, or any_of for alternatives. "
                      "Returns a snippet per complaint plus its structured fields, newest first (date order, not relevance-ranked: every result contains all your words); use get_complaint for the full text. "
                      "Searches scan text, so narrow with date_from/date_to, company/company_contains, product or state when you can: an unfiltered search over all years can time out. "
                      "Filters: company, company_contains, product, state (2 letters), date_from/date_to (YYYY-MM-DD or YYYY-MM), "
                      "plus issue, sub_product, company_response, timely, tag (these are slower).")
def search_narratives(query: Optional[str] = None, phrase: Optional[str] = None, any_of: Optional[list[str]] = None,
                      company: Optional[str] = None, company_contains: Optional[str] = None, product: Optional[str] = None,
                      sub_product: Optional[str] = None, issue: Optional[str] = None, state: Optional[str] = None,
                      date_from: Optional[str] = None, date_to: Optional[str] = None, company_response: Optional[str] = None,
                      timely: Optional[bool] = None, tag: Optional[str] = None, limit: int = 10, offset: int = 0) -> list[dict]:
    words = [w for w in re.split(r"\s+", (query or "").strip()) if w]
    _check_any_of(any_of)
    if not (words or phrase or any_of):
        raise ValueError("Add a keyword to search: query (all words must appear), phrase (exact text) or any_of (alternatives). "
                         "To count or filter complaints that have a narrative without a keyword, use complaint_counts with has_narrative=true.")
    limit, offset = _page(limit, offset)
    limit = min(limit, 25)
    nw, np_ = _narr_where(words, phrase, any_of, company, company_contains, product, state, date_from, date_to)
    cw, cp = _filters(None, None, None, sub_product, issue, None, None, None, company_response, timely, None, tag)
    if cw != "TRUE":
        sql = (f"SELECT n.complaint_id, n.date_received, n.company, n.product, c.issue, n.state, c.company_response, n.narrative "
               f"FROM n JOIN c USING (complaint_id) WHERE {nw} AND {cw} ORDER BY n.date_received DESC, n.complaint_id DESC LIMIT {limit + 1} OFFSET {offset}")
        rows = run(sql, np_ + cp)
    else:
        rows = run(f"SELECT n.complaint_id, n.date_received, n.company, n.product, n.state, n.narrative FROM n WHERE {nw} "
                   f"ORDER BY n.date_received DESC, n.complaint_id DESC LIMIT {limit + 1} OFFSET {offset}", np_)
        more_fields = {r["complaint_id"]: r for r in run(
            "SELECT complaint_id, issue, company_response FROM c WHERE complaint_id IN (" + ",".join(str(int(r["complaint_id"])) for r in rows[:limit]) + ")")} if rows else {}
        for r in rows:
            m = more_fields.get(r["complaint_id"], {})
            r["issue"], r["company_response"] = m.get("issue"), m.get("company_response")
    terms = words + ([phrase] if phrase else []) + (any_of or [])
    more = len(rows) > limit
    out = []
    for r in rows[:limit]:
        t = r.pop("narrative")
        r["snippet"] = _snippet(t, terms)
        out.append(r)
    if more:
        out.append({"truncated": True, "next_offset": offset + limit, "message": "More matches exist; raise offset, or narrow filters. Results are newest first."})
    if not out:
        out.append({"message": f"No narratives matched. Narratives exist only for complaints published by CFPB with a narrative through {NARRATIVE_FREEZE} (none for complaints received before 2015)."})
    return out


@mcp.tool(description="Count how many archived narratives match a keyword search, optionally grouped by year, month, product, company, issue or state. "
                      "Same search and filter arguments as search_narratives (without the slower issue/response filters). Use this for 'how many complaints mention X'. "
                      "Counts only complaints that have a narrative (through " + NARRATIVE_FREEZE + "), not all complaints. Narrow with date, company or product when you can.")
def count_narratives(query: Optional[str] = None, phrase: Optional[str] = None, any_of: Optional[list[str]] = None, group_by: Optional[str] = None,
                     company: Optional[str] = None, company_contains: Optional[str] = None, product: Optional[str] = None,
                     state: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 25) -> list[dict]:
    cols = {"year": "year(n.date_received)", "month": "strftime(n.date_received, '%Y-%m')", "product": "n.product", "company": "n.company", "state": "n.state"}
    if group_by and group_by not in cols:
        raise ValueError("group_by must be year, month, product, company or state")
    words = [w for w in re.split(r"\s+", (query or "").strip()) if w]
    _check_any_of(any_of)
    nw, np_ = _narr_where(words, phrase, any_of, company, company_contains, product, state, date_from, date_to)
    limit, _ = _page(limit, 0)
    sel = f"{cols[group_by]} AS {group_by}, " if group_by else ""
    grp = " GROUP BY 1 ORDER BY " + ("1" if group_by in ("year", "month") else "2 DESC") if group_by else ""
    return run(f"SELECT {sel}count(*) AS narratives FROM n WHERE {nw}{grp} LIMIT {limit}", np_)


@mcp.tool(description="One complaint by its CFPB Complaint ID: all structured fields and, when archived, the full narrative.")
def get_complaint(complaint_id: int) -> dict:
    rows = run("SELECT * FROM c WHERE complaint_id = ?", [int(complaint_id)])
    nr = run("SELECT narrative FROM n WHERE complaint_id = ? LIMIT 1", [int(complaint_id)])
    if not rows and not nr:
        return {"error": f"Complaint {complaint_id} is not in the dataset. It may be newer than the data, or CFPB may have removed it."}
    r = rows[0] if rows else {"complaint_id": int(complaint_id), "note": "Narrative is archived but the complaint is no longer in the live CFPB file."}
    r["narrative"] = nr[0]["narrative"] if nr else None
    if not nr:
        r["narrative_note"] = (f"No archived narrative. Narratives exist only for complaints published by CFPB with a narrative through {NARRATIVE_FREEZE} (none for complaints received before 2015) where the consumer consented to publish.")
    return r


# ---------------- hosting ----------------
class RateLimit:
    def __init__(self, app, per_minute: int):
        self.app, self.per_minute, self.hits = app, per_minute, {}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/healthz":
            h = dict(scope["headers"])
            ip = (h.get(b"fly-client-ip") or h.get(b"x-forwarded-for", b"").split(b",")[0] or b"?").decode().strip()
            now = time.time()
            q_ = [t for t in self.hits.get(ip, []) if now - t < 60]
            if len(q_) >= self.per_minute:
                await send({"type": "http.response.start", "status": 429, "headers": [(b"content-type", b"application/json"), (b"retry-after", b"60")]})
                await send({"type": "http.response.body", "body": b'{"error":"rate limit exceeded, try again in a minute"}'})
                return
            q_.append(now)
            self.hits[ip] = q_
            if len(self.hits) > 5000:
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < 60}
        await self.app(scope, receive, send)


def _forbid_extra_arguments() -> None:
    for t in mcp._tool_manager.list_tools():
        model = t.fn_metadata.arg_model
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)


_forbid_extra_arguments()


def http_app():
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import JSONResponse
    hosts = [h for h in os.environ.get("CFPB_ALLOWED_HOSTS", "").split(",") if h]
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(hosts), allowed_hosts=hosts, allowed_origins=["*"] if hosts else [])

    @mcp.custom_route("/data/{name}", methods=["GET"])
    async def data_file(request):
        from starlette.responses import FileResponse
        name = request.path_params["name"]
        p = data_dir() / name
        if name not in FILES or not p.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type="application/octet-stream", filename=name)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        return JSONResponse({"ok": True})

    return RateLimit(mcp.streamable_http_app(), int(os.environ.get("CFPB_RATE_PER_MIN", "60")))


def main() -> None:
    argv = sys.argv[1:]
    if "--check" in argv:
        ensure_files()
        print(dataset_info())
        return
    if "--http" in argv:
        import uvicorn
        uvicorn.run(http_app(), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")), log_level="warning", timeout_keep_alive=5)
        return
    mcp.run()


if __name__ == "__main__":
    main()
