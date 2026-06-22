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
- Official API search providers:
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
