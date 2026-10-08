"""Build the Parquet files from CFPB's own downloads (live CSV plus the FOIA narratives archive)."""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import duckdb

LIVE_URL = "https://files.consumerfinance.gov/ccdb/complaints.csv.zip"
ARCHIVE_PAGE = "https://www.consumerfinance.gov/foia-requests/foia-electronic-reading-room/cfpb-consumer-complaint-database-narratives-archive/"
UA = {"User-Agent": "cfpb-complaints-analysis (open source research project)"}
_ZIP = re.compile(r'https://files\.consumerfinance\.gov/f/documents/CCDB_Export_(\d+)_[^"\']*?\.zip')


def _get(url: str, dest: Path | None = None) -> bytes | None:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r:
        if dest is None:
            return r.read()
        with open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    return None


def archive_urls() -> list[tuple[int, str]]:
    """Scrape the Reading Room page; file names changed over time, so no URL pattern is assumed."""
    html = _get(ARCHIVE_PAGE).decode("utf-8", "replace")
    found = {int(m.group(1)): m.group(0) for m in _ZIP.finditer(html)}
    return sorted(found.items())


def _con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute(f"SET memory_limit='{os.environ.get('CFPB_BUILD_MEMORY', '900MB')}'")
    c.execute("SET threads=1")
    c.execute("SET preserve_insertion_order=false")
    return c


def build(data: Path, keep_raw: bool = False) -> None:
    raw, out = data / "raw", data / "parquet"
    raw.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    c = _con()
    z = raw / "complaints.csv.zip"
    print("downloading live database", file=sys.stderr)
    _get(LIVE_URL, z)
    subprocess.run(["unzip", "-q", "-o", str(z), "-d", str(raw)], check=True)
    csv = raw / "complaints.csv"
    c.execute(f"""COPY (SELECT cast("Date received" AS DATE) date_received, "Product" product, "Sub-product" sub_product,
        "Issue" issue, "Sub-issue" sub_issue, "Company public response" company_public_response, "Company" company,
        "State" state, "ZIP code" zip_code, "Tags" tags, "Submitted via" submitted_via,
        cast("Date sent to company" AS DATE) date_sent_to_company, "Company response to consumer" company_response,
        "Timely response?" timely_response, cast("Complaint ID" AS BIGINT) complaint_id
        FROM read_csv('{csv}', header=true, all_varchar=true, max_line_size=10000000)
        ORDER BY date_received, complaint_id) TO '{out}/complaints.parquet'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)""")
    csv.unlink()
    for n, url in archive_urls():
        print(f"narratives archive file {n}", file=sys.stderr)
        zp = raw / f"archive_{n:02d}.zip"
        _get(url, zp)
        subprocess.run(["unzip", "-q", "-o", str(zp), "-d", str(raw / f"a{n:02d}")], check=True)
        for f in glob.glob(str(raw / f"a{n:02d}" / "*.csv")):
            c.execute(f"""COPY (SELECT cast("Complaint ID" AS BIGINT) complaint_id, "Consumer complaint narrative" narrative
                FROM read_csv('{f}', header=true, all_varchar=true, max_line_size=10000000)
                WHERE trim(coalesce("Consumer complaint narrative", '')) <> '')
                TO '{out}/narratives_{n:02d}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 20000)""")
            os.remove(f)
        if not keep_raw:
            zp.unlink()
    print("done:", sorted(p.name for p in out.glob("*.parquet")), file=sys.stderr)
