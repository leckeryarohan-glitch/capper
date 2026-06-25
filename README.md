# Capper

Capper is a small Python CLI for compliant B2B lead research. You give it a
category such as `hotel`, `restaurant`, `lager logistik`, or `elektronik`; it
uses an allowed search provider or a reviewed seed URL file, visits public
business websites, extracts public contact details, deduplicates the results,
and exports a CSV or JSON file for manual review.

It does **not** send bulk email and it does **not** bypass search-engine or
website restrictions. Use it only where you have a lawful basis for outreach and
where your message includes identification, a relevant business purpose, and an
easy opt-out.

## Features

- Category and optional location based search queries.
- No-key automated providers:
  - Combined multi-source via `--provider all` (default in the GUI)
  - OpenStreetMap/Overpass via `--provider osm`
  - DuckDuckGo web search via `--provider duckduckgo`
- Official API search providers:
  - Google Custom Search JSON API via `GOOGLE_SEARCH_API_KEY` and
    `GOOGLE_SEARCH_ENGINE_ID`
  - Brave Search API via `BRAVE_SEARCH_API_KEY`
  - Bing Web Search API via `BING_SEARCH_API_KEY`
  - SerpAPI via `SERPAPI_API_KEY`
- File-based seed provider for manually reviewed URLs.
- Website crawling with `robots.txt` checks enabled by default.
- Contact and imprint page discovery.
- Public email and phone extraction.
- Role-address preference (`info@`, `kontakt@`, `sales@`, etc.).
- Personal-looking emails are excluded by default and can only be exported with
  an explicit review flag.
- Suppression list support for opt-outs and blocked domains.
- Global email-based deduplication so the same address is never stored twice.
- Parallel website crawling (`--workers`) for high daily lead volume.
- Streaming CSV output and live statistics (websites, pages, domains, duplicates, leads/min).
- Batch mode for many category/location combinations with checkpoint/resume.
- Simple desktop GUI: enter a category and start the lead search.
- CSV and JSON export with source URLs and discovery timestamps.

## Quick start

```bash
python3 -m lead_research discover \
  --category "hotel" \
  --location "Berlin" \
  --provider brave \
  --limit 20 \
  --output leads.csv
```

For Brave, set an API key first:

```bash
export BRAVE_SEARCH_API_KEY="your-key"
```

You can also use a file with reviewed URLs:

```bash
python3 -m lead_research discover \
  --category "restaurant" \
  --provider file \
  --seed-file examples/seeds.txt \
  --output leads.csv
```

## Desktop GUI

Start the simple desktop app:

```bash
python3 -m lead_research gui
```

Then:

1. Enter a category such as `hotel`, `restaurant`, `lager logistik`, or
   `elektronik`.
2. Optionally enter a location such as `Berlin`.
3. Choose the CSV output file.
4. Click **Leads suchen**.

During the run, the GUI shows:

- a progress bar for crawled websites,
- the currently inspected website/page,
- the live number of found leads,
- a statistics line (websites, pages, unique domains, duplicates skipped, suppressed, leads/min),
- and a table with each lead as soon as it is discovered.

## Scaling to thousands of leads per day

`discover` crawls websites in parallel and writes results incrementally:

```bash
python3 -m lead_research discover \
  --category "hotel" \
  --provider all \
  --limit 1000 \
  --workers 24 \
  --max-leads 5000 \
  --output leads.csv
```

- `--provider all` combines OpenStreetMap, Nominatim, and DuckDuckGo (plus any
  configured API providers) for the widest coverage.
- `--limit` controls how many candidate websites are inspected (raise it for
  thousands of websites).
- `--workers` controls how many websites are crawled at the same time.
- `--max-leads` stops the run once enough unique leads are collected.
- `--dedupe email` (default) keeps only one lead per email address; use
  `--dedupe email_website` to keep one per email/website pair.
- Results are streamed to the CSV as they are found, so long runs persist
  progress even if interrupted.

The GUI is fully automated without API keys. It combines OpenStreetMap/Overpass,
Nominatim, and DuckDuckGo to find real businesses that match the category and
optional location, takes their public website URLs, then crawls those websites
in parallel for public B2B contact details. The GUI lets you set how many
websites to inspect (`Websites (max)`) and how many to crawl in parallel
(`Threads`). Website crawling still respects `robots.txt` and personal-looking
emails are excluded by default.

If no location is entered, Capper searches a default set of large German cities
in smaller Overpass requests instead of running one global query. Entering a
specific city, for example `Berlin`, usually produces faster and more targeted
results.

You can also run the no-key workflow from the CLI:

```bash
python3 -m lead_research discover \
  --category "hotel" \
  --location "Berlin" \
  --provider osm \
  --output leads.csv
```

Google-backed search remains available when you do have credentials:

```bash
export GOOGLE_SEARCH_API_KEY="your-google-api-key"
export GOOGLE_SEARCH_ENGINE_ID="your-search-engine-id"
python3 -m lead_research discover \
  --category "hotel" \
  --location "Berlin" \
  --provider google \
  --source-profile common \
  --output leads.csv
```

For CLI use you can also choose other official providers:

```bash
export BRAVE_SEARCH_API_KEY="your-key"
export BING_SEARCH_API_KEY="your-key"
# or
export SERPAPI_API_KEY="your-key"
```

For school demonstrations, the default GUI mode is fully automated with no keys
and no direct Google result-page scraping, CAPTCHA handling, or other anti-bot
bypasses.

## High-volume batches

For thousands of leads, use `batch` with category and location files. This
creates many normal provider queries, respects your configured delays, writes a
checkpoint after every query, and stops at `--max-leads`.

```bash
python3 -m lead_research batch \
  --categories-file examples/categories.txt \
  --locations-file examples/locations.txt \
  --provider brave \
  --limit-per-query 50 \
  --max-leads 5000 \
  --checkpoint capper-checkpoint.json \
  --suppression-file examples/suppression.txt \
  --output leads.csv
```

Resume an interrupted run:

```bash
python3 -m lead_research batch \
  --categories-file examples/categories.txt \
  --locations-file examples/locations.txt \
  --provider brave \
  --limit-per-query 50 \
  --max-leads 5000 \
  --checkpoint capper-checkpoint.json \
  --resume \
  --output leads.csv
```

Scaling guidance:

- Use official search APIs and stay within their quota and terms.
- Increase volume by adding relevant categories and locations, not by bypassing
  rate limits or website rules.
- Keep `robots.txt` checks enabled unless you have explicit permission.
- Use `--query-delay` and `--delay` to match provider and website limits.
- Keep `--suppression-file` current before every run.
- Export personal-looking emails only for manual review with
  `--include-personal-review`.

## Suppression / opt-out list

Create a text file with one email address or domain per line:

```text
unsubscribe@example.com
example.org
@blocked-domain.de
```

Then run:

```bash
python3 -m lead_research discover \
  --category "lager logistik" \
  --provider file \
  --seed-file examples/seeds.txt \
  --suppression-file examples/suppression.txt \
  --output leads.csv
```

## Output fields

The CSV export contains:

- `category`
- `company_name`
- `email`
- `phone`
- `website`
- `source_url`
- `page_title`
- `consent_status`
- `notes`
- `discovered_at`

`consent_status` is one of:

- `business_public`: a public role-based business contact.
- `personal_review_required`: a non-role email exported only when
  `--include-personal-review` is used.
- `suppressed`: excluded by the suppression list and not written to the export.

## Compliance checklist

Before sending any marketing email based on exported leads:

1. Confirm that the recipient is a business contact relevant to your offer.
2. Record the source URL and discovery date.
3. Keep and apply an opt-out/suppression list for every campaign.
4. Include your company identity, postal/contact details, and unsubscribe
   instructions in every email.
5. Avoid sensitive categories and personal/private email addresses.
6. Check local rules such as DSGVO/GDPR, UWG, ePrivacy, CAN-SPAM, and search API
   terms before use.

## Development

Run the test suite:

```bash
python3 -m unittest discover -s tests
```
