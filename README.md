# SEO Audit Report

Technical SEO audit for any URL: a weighted **0-100 score**, **25 checks**, and a **fix list** with severity (`critical` / `high` / `medium` / `low`) and effort labels (`S` / `M`).

No API keys, no headless browser, no proxies. One URL in — a structured audit out.

**Live on the Apify Store:** https://apify.com/by_jotaro/seo-audit-report

```bash
curl -X POST "https://api.apify.com/v2/acts/by_jotaro~seo-audit-report/run-sync-get-dataset-items?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"urls":["https://example.com"]}'
```

## What you get

- **Dataset item per URL**: `score`, `grade`, `status`, `responseMs`, `htmlKb`, `checks[]`, `fixes[]`
- **Key-value store**: `report-<domain>-<ts>` (HTML report) and `report-<domain>-<ts>.md` (Markdown)
- **SUMMARY** key with all URLs and scores

## Checks (25)

- **Availability and indexing**: HTTP status, HTTPS, accidental `noindex`, `robots.txt`, `sitemap.xml`, charset, `lang`
- **Meta and content**: title length, meta description, viewport, single H1, H2s, heading order, image `alt`, anchors
- **Social and rich results**: Open Graph, Twitter Card, favicon, JSON-LD structured data
- **Performance and security**: HTML weight, TTFB, external asset count, mixed content, security headers
- **Canonicalization**: canonical tag

## Input

```json
{
  "urls": ["https://example.com", "https://example.com/pricing"]
}
```

## Output example

```json
{
  "url": "https://example.com",
  "domain": "example.com",
  "status": 200,
  "responseMs": 532,
  "htmlKb": 0.6,
  "score": 58,
  "grade": "C - needs work",
  "weightedPassed": 38,
  "weightedTotal": 66,
  "checks": [{"id": "title", "name": "Title 30-60 characters", "severity": "high", "effort": "S", "ok": false, "detail": "87 chars: ..."}],
  "fixes": [{"id": "title", "name": "Title 30-60 characters", "sev": "high", "effort": "S", "detail": "...", "rec": "Rewrite the title: ..."}]
}
```

## Run locally

```bash
pip install -r requirements.txt
python main.py --url https://example.com          # writes out/<domain>.md + .html
python main.py --batch urls.txt                   # one URL per line, # = comment
```

On the Apify platform the same `main.py` runs in actor mode: reads the input from
`Actor.get_input()`, pushes one dataset item per URL and stores the reports in the
key-value store.

## Deploy to Apify

```bash
npm i -g apify-cli
apify login --token <YOUR_TOKEN>
apify push
```

## Limits

- Audits the server-rendered HTML (SPAs are analyzed as delivered, without JS execution).
- One HTTP request per URL (redirects followed); no crawling of internal links.
- Respects the target site's robots and rate limits — use it on sites you own or may audit.

## License

MIT
