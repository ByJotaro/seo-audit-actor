"""SEO Audit Report - Apify Actor entry point.

Input:  { "urls": ["https://example.com", ...] }
Output: one dataset item per URL (score, grade, checks, fixes) + HTML report in KV store.

Runs on Apify (apify SDK) and locally:
  python main.py --url https://example.com
  python main.py --input input.json
  echo '{"urls":["https://example.com"]}' | python main.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audit_core import audit, grade, render_md, render_html  # noqa: E402

try:
    from apify import Actor  # type: ignore
    HAVE_APIFY = True
except Exception:
    Actor = None  # type: ignore
    HAVE_APIFY = False


def normalize_urls(raw):
    out = []
    for u in raw or []:
        u = (u or "").strip()
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
    return item, render_html(res)


def local_main() -> int:
    urls = []
    args = sys.argv[1:]
    if "--url" in args:
        urls = [args[args.index("--url") + 1]]
    elif "--input" in args:
        data = json.loads(Path(args[args.index("--input") + 1]).read_text(encoding="utf-8"))
        urls = data.get("urls") or []
    elif not sys.stdin.isatty():
        data = json.loads(sys.stdin.read() or "{}")
        urls = data.get("urls") or []
    else:
        print("usage: main.py --url URL | --input file.json | stdin JSON", file=sys.stderr)
        return 2

    urls = normalize_urls(urls)
    if not urls:
        print("no urls", file=sys.stderr)
        return 2

    out = []
    for u in urls:
        try:
            item, _ = run_one(u)
            md = item.pop("markdown", "")
            out.append(item)
            print(json.dumps({"url": u, "score": item["score"], "grade": item["grade"],
                              "fixes": len(item["fixes"]), "mdBytes": len(md)},
                             ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"url": u, "error": str(e)[:300]}, ensure_ascii=False))
    Path("last_run.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


async def apify_main() -> int:
    async with Actor:  # type: ignore
        inp = await Actor.get_input() or {}
        urls = normalize_urls(inp.get("urls"))
        if not urls:
            await Actor.fail("No URLs provided. Pass urls: ['https://example.com']")
            return 1

        results = []
        for u in urls:
            try:
                item, html_report = run_one(u)
                md = item.pop("markdown", "")
                await Actor.push_data(item)
                key = f"report-{item['domain']}-{int(time.time())}"
                await Actor.set_value(key, html_report, content_type="text/html")
                await Actor.set_value(f"{key}.md", md, content_type="text/markdown")
                results.append({"url": u, "score": item["score"], "grade": item["grade"],
                                "htmlReportKey": key})
                try:
                    await Actor.charge("audit", count=1)
                except Exception:
                    pass
            except Exception as e:
                results.append({"url": u, "error": str(e)[:300]})
        await Actor.set_value("SUMMARY", json.dumps(results, indent=2),
                              content_type="application/json")
    return 0


if __name__ == "__main__":
    if HAVE_APIFY:
        raise SystemExit(asyncio.run(apify_main()))
    raise SystemExit(local_main())
