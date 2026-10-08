# cfpb-complaints-analysis

The CFPB Consumer Complaint Database as clean Parquet, plus an MCP server so an AI assistant can query it: 18.2 million complaints (2011 to today) and 3.85 million consumer narratives.

## What is in it

| Layer | Source | Coverage |
|---|---|---|
| Structured complaints | CFPB's public complaint file, `files.consumerfinance.gov/ccdb/complaints.csv.zip` | 18,239,728 complaints, received 2011-12-01 to 2026-10-07 (rebuild to refresh) |
| Narratives | CFPB FOIA Reading Room, "CFPB Consumer Complaint Database Narratives Archive" | 3,851,415 narratives, published through 2026-08-14, none before 2015 |

Both are CFPB's own files. Nothing comes from a third party. CFPB describes the narratives as public domain for FOIA purposes, and says on its data page that complaint data is "freely available for anyone to use, analyze, and build on."

## The narrative freeze

CFPB stopped publishing complaint narratives in September 2026 (database release 24, and the Aug 14, 2026 announcement that it would cease discretionary publication of narratives and visualizations). It moved the previously published narratives to its FOIA Reading Room. The live complaint file has no narrative column any more.

This project joins the archived narratives back to the live complaints by Complaint ID. So:

- Narratives exist only for complaints CFPB had published with one through 2026-08-14. Complaints from after that have none, and it will stay that way.
- Fewer than half of complaints ever had a narrative: the consumer had to consent, and none were published before 2015.
- Narratives are scrubbed by CFPB (personal data shows as XXXX).
- Structured fields keep updating when you rebuild from CFPB's live file.

## Use it

```
pip install cfpb-complaints-analysis
cfpb-complaints-mcp
```

The first run downloads the Parquet files (about 700 MB) from the hosted service to `~/.cache/cfpb-complaints-analysis`. Set `CFPB_DATA_URL` to fetch them from somewhere else, or `CFPB_DATA_DIR` to use a folder you built yourself. Add the server to your MCP client as a stdio command, or use the hosted endpoint listed in the MCP registry as `io.github.bnovarini/cfpb-complaints-analysis`.

Tools: `dataset_info`, `list_values`, `find_company`, `complaint_counts`, `trend`, `company_profile`, `compare_companies`, `search_narratives`, `count_narratives`, `get_complaint`.

Example questions: What do people complain about at Navy Federal versus PenFed? How did mortgage complaints about Rocket change by year? Find 2024 narratives that mention "overdraft fee" at a credit union.

Build it yourself from CFPB's sources: `cfpb-complaints --data data build`.

## Read this before quoting numbers

- Complaints are unverified consumer allegations. CFPB says so itself.
- Counts are raw. A large company will have more complaints than a small one; nothing is scaled by customers or accounts.
- CFPB lists some firms under several names. Use `find_company` and `company_contains` to include all variants.
- The newest months are partial: complaints are still being sent to companies, and "In progress" is a status, not an outcome.
- Keyword narrative search scans text. Unfiltered searches take about ten seconds. A date, company or product filter makes them much faster.

## Checks

Every count is reconciled to CFPB's own published numbers. See [docs/AUDIT.md](docs/AUDIT.md), including the figures that do not match and why.

## License

MIT for the code. The data is CFPB's.
