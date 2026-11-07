"""SEO Audit Report - Apify Actor entry point.

Input:  { "urls": ["https://example.com", ...], "maxPages": 1, "sameDomainOnly": true }
Output: one dataset item per audited page (score, grade, checks, fixes) + HTML/MD reports in KV store.

maxPages > 1 turns on crawling: internal links of the start URL are followed
(same host by default) until maxPages pages have been audited.

Runs on Apify (apify SDK) and locally:
  python main.py --url https://example.com
  python main.py --url https://example.com --pages 10
  python main.py --input input.json
  echo '{"urls":["https://example.com"]}' | python main.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audit_core import audit, grade, render_md, render_html  # noqa: E402

try:
    from apify import Actor  # type: ignore
    HAVE_APIFY = True
except Exception:
    Actor = None  # type: ignore
    HAVE_APIFY = False

MAX_PAGES_LIMIT = 50


def normalize_urls(raw):
    out = []
    for u in raw or []:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u:
            continue
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        if u not in out:
            out.append(u)
    return out


def run_one(url: str):
    res = audit(url)
    item = {
        "url": res["url"],
        "domain": res["dom"],
        "status": res["status"],
        "responseMs": res["ms"],
        "htmlKb": res["kb"],
        "score": res["score"],
        "grade": grade(res["score"]),
        "weightedPassed": res["got_w"],
        "weightedTotal": res["total_w"],
        "checks": [
            {"id": cid, "name": name, "severity": sev, "effort": eff,
             "ok": ok, "detail": det}
            for (cid, name, sev, eff, ok, det) in res["results"]
        ],
        "fixes": res["fixes"],
        "markdown": render_md(res),
    }
    return item, render_html(res), res.get("links", [])


def crawl(start: str, max_pages: int, same_domain: bool = True, delay: float = 0.2):
    """BFS over internal links. -> list of (item, html_report) plus errors."""
    host = urlparse(start).netloc
    queue = [start]
    seen = set()
    pages = []
    errors = []
    while queue and len(pages) < max_pages:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            item, html_report, links = run_one(u)
        except Exception as e:  # keep the crawl alive on a single bad page
            errors.append({"url": u, "error": str(e)[:300]})
            continue
        pages.append((item, html_report))
        if len(pages) >= max_pages:
            break
        for link in links:
            if link in seen:
                continue
            if same_domain and urlparse(link).netloc != host:
                continue
            queue.append(link)
        if delay:
            time.sleep(delay)
    return pages, errors


def process_start(start, max_pages, same_domain=True):
    """Audit one start URL (single page) or crawl it (max_pages > 1)."""
    if max_pages > 1:
        return crawl(start, max_pages, same_domain)
    try:
        item, html_report, _ = run_one(start)
        return [(item, html_report)], []
    except Exception as e:
        return [], [{"url": start, "error": str(e)[:300]}]


def site_summary(start, pages, errors):
    if not pages:
        return {"startUrl": start, "pages": 0, "errors": errors}
    scores = [it["score"] for it, _ in pages]
    issues = {}
    for it, _ in pages:
        for fx in it["fixes"]:
            issues[fx["id"]] = issues.get(fx["id"], 0) + 1
    top = sorted(issues.items(), key=lambda kv: -kv[1])[:10]
    worst = min(pages, key=lambda p: p[0]["score"])[0]
    return {
        "startUrl": start,
        "pages": len(pages),
        "avgScore": round(sum(scores) / len(scores)),
        "minScore": min(scores),
        "maxScore": max(scores),
        "worstPage": {"url": worst["url"], "score": worst["score"]},
        "topIssues": [{"id": k, "pages": v} for k, v in top],
        "errors": errors,
    }


def local_main() -> int:
    urls = []
    max_pages = 1
    args = sys.argv[1:]
    if "--pages" in args:
        try:
            max_pages = int(args[args.index("--pages") + 1])
        except (IndexError, ValueError):
            max_pages = 1
    if "--url" in args:
        urls = [args[args.index("--url") + 1]]
    elif "--input" in args:
        data = json.loads(Path(args[args.index("--input") + 1]).read_text(encoding="utf-8"))
        urls = data.get("urls") or []
        max_pages = int(data.get("maxPages") or max_pages)
    elif not sys.stdin.isatty():
        data = json.loads(sys.stdin.read() or "{}")
        urls = data.get("urls") or []
        max_pages = int(data.get("maxPages") or max_pages)
    else:
        print("usage: main.py --url URL [--pages N] | --input file.json | stdin JSON", file=sys.stderr)
        return 2

    urls = normalize_urls(urls)
    if not urls:
        print("no urls", file=sys.stderr)
        return 2
    max_pages = max(1, min(max_pages, MAX_PAGES_LIMIT))

    out = []
    for u in urls:
        pages, errors = process_start(u, max_pages)
        for item, _ in pages:
            md = item.pop("markdown", "")
            out.append(item)
            print(json.dumps({"url": item["url"], "score": item["score"], "grade": item["grade"],
                              "fixes": len(item["fixes"]), "mdBytes": len(md)},
                             ensure_ascii=False))
        for e in errors:
            print(json.dumps(e, ensure_ascii=False))
        if max_pages > 1:
            print(json.dumps(site_summary(u, pages, errors), ensure_ascii=False))
    Path("last_run.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


async def apify_main() -> int:
    async with Actor:  # type: ignore
        inp = await Actor.get_input() or {}
        urls = normalize_urls(inp.get("urls"))
        if not urls:
            await Actor.fail("No URLs provided. Pass urls: ['https://example.com']")
            return 1
        try:
            max_pages = int(inp.get("maxPages") or 1)
        except (TypeError, ValueError):
            max_pages = 1
        max_pages = max(1, min(max_pages, MAX_PAGES_LIMIT))
        same_domain = bool(inp.get("sameDomainOnly", True))

        results = []
        for u in urls:
            pages, errors = process_start(u, max_pages, same_domain)
            for idx, (item, html_report) in enumerate(pages, 1):
                md = item.pop("markdown", "")
                await Actor.push_data(item)
                ts = int(time.time())
                key = (f"report-{item['domain']}-{ts}" if len(pages) == 1
                       else f"report-{item['domain']}-{ts}-p{idx}")
                await Actor.set_value(key, html_report, content_type="text/html")
                await Actor.set_value(f"{key}.md", md, content_type="text/markdown")
                results.append({"url": item["url"], "score": item["score"],
                                "grade": item["grade"], "htmlReportKey": key})
                try:
                    await Actor.charge("audit", count=1)
                except Exception:
                    pass
            for e in errors:
                results.append(e)
            if max_pages > 1:
                results.append({"siteSummary": site_summary(u, pages, errors)})
        await Actor.set_value("SUMMARY", json.dumps(results, indent=2),
                              content_type="application/json")
    return 0


if __name__ == "__main__":
    if HAVE_APIFY:
        raise SystemExit(asyncio.run(apify_main()))
    raise SystemExit(local_main())
