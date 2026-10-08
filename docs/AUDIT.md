# Audit: do the numbers match CFPB's own?

Run on 2026-10-07 (evening, US Eastern) against the live complaint file CFPB last modified at 2026-10-07 09:16 GMT. Every public number was compared to something CFPB publishes. Anything that does not reconcile is listed under "Does not reconcile".

## 1. Pipeline vs CFPB's search API (matches)

CFPB's complaint search API (`consumerfinance.gov/data-research/consumer-complaints/search/api/v1/`) returns totals and breakdowns for any date range.

- **Per year, 2011 to 2026:** for each year, the total and every bucket of product, issue, company, state, company response, public response, timely response and submission channel was compared. 46,991 bucket comparisons, 0 differences. Totals: 2011 2,536; 2012 72,368; 2013 108,214; 2014 152,908; 2015 168,272; 2016 191,292; 2017 242,745; 2018 257,130; 2019 277,239; 2020 444,213; 2021 495,940; 2022 800,245; 2023 1,292,049; 2024 2,734,268; 2025 5,442,953; 2026 (to Oct 7) 5,557,356.
- **Per month, 2011-12 to 2026-10:** 179 monthly totals, 0 differences.
- **Through the MCP tools:** 75 filtered queries (company and year; product, state and 2025; issue and company response) on randomly sampled values, run through `complaint_counts` and compared with the API's count. 0 differences.

Limit of this check: the API and the downloadable file come from the same CFPB system, so this proves the pipeline loses and alters nothing. It does not prove CFPB's data is right.

## 2. Vs CFPB's 2024 Consumer Response Annual Report (independent publication)

Source: https://files.consumerfinance.gov/f/documents/cfpb_cr-annual-report_2025-05.pdf

| Report says (2024) | This dataset (2024) | Result |
|---|---|---|
| Companies timely on 99.7% of complaints | 99.72% | matches |
| Closed with monetary relief 0.8% | 0.9% | matches within rounding of a different base |
| More than 3,600 companies | 3,588 distinct company names | close, see below |
| Footnote 28 links to a timely=No query with size=5,528 (accessed 2025-03-03) | timely=No for 2024 is 7,736, and the live API returns 7,736 too | differs by date: more complaints lapsed past the response deadline since March 2025 |

## 3. Does not reconcile

These do not match. They are explained where we can, not hidden.

1. **Complaint volume.** The report says CFPB "sent more than 2.8 million complaints" to companies in 2024 (about 2,829,400). The public database has 2,734,268 for 2024. The database is what CFPB publishes, and it does not contain every complaint sent (for example, ones not published). We cannot see the difference, so use the database figure for anything built on this data.
2. **Credit reporting.** The report says "more than 2.7 million" credit or consumer reporting complaints in 2024, 85% of all complaints. In the database, the product "Credit reporting or other personal consumer reports" has 2,365,586 complaints in 2024, 86.5% of the 2,734,268. The share is close; the count is not. We could not find which complaints the report counts that the database does not.
3. **Response mix.** The report shows 48% non-monetary relief, 46% explanation, 3% administrative response, 2% company reviewing. The database shows 50.1% non-monetary relief, 49.0% explanation, 0.9% monetary, 0.1% untimely. The database has no "administrative response" category, and in-progress complaints appear only as "In progress", so the bases differ.
4. **Company count.** More than 3,600 in the report versus 3,588 names. The report may count at a different level (parent company); the database lists names as filed.

## 4. Narrative archive (CFPB FOIA Reading Room) vs the live file

Archive: https://www.consumerfinance.gov/foia-requests/foia-electronic-reading-room/cfpb-consumer-complaint-database-narratives-archive/ (21 zip files).

- 17,533,918 complaint rows, all unique IDs, received 2011-12-01 to 2026-08-31. 3,851,415 have a narrative. None has one before 2015. None received after 2026-08-14 has one: 361,832 archive rows are dated after Aug 14, and 0 have a narrative.
- 31,840 archive complaint IDs are not in the live file (31,824 are from 2026). 1,841 live complaints received through Aug 14 are not in the archive, and 3,853 received through Aug 31. We do not know why. These are probably complaints CFPB removed, reclassified or added after the archive was cut.
- 15 archived narratives belong to IDs no longer in the live file. They are kept and returned by `get_complaint` with a note.
- For the 17.5M IDs present in both, structured fields in the archive were not compared field by field beyond the join; the server serves structured fields only from the live file.

Narrative counts have no CFPB-published aggregate to reconcile with (CFPB removed the narrative filters and counts from its site). The count of 3,851,415 is our own count of the archive.

## 5. Known data quality limits

- The same company appears under several names. The tools return all variants and let you filter by substring.
- ZIP codes are partly masked by CFPB (for example 391XX).
- Complaints are unverified allegations. Counts are not scaled by company size.
