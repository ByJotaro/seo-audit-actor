"""SEO-аудит конвейер: URL -> Markdown + HTML отчёты. Полный автомат, юзер только вставляет URL.
Venv: tools/venv (requests + beautifulsoup4 уже там; без них — stdlib-фолбэк urllib+regex).
Usage:
  venv-python audit.py https://example.com          # один URL -> out/<domain>.md + .html
  venv-python audit.py --batch urls.txt              # файл со списком URL (по одному на строку, # = коммент)
Out: pipelines/seo-audit/out/<domain>.md + .html, плюс index.md/.html (портфолио-реестр).
Граница карты: карта не нужна вообще. Продажа: Fiverr/Upwork/Kwork гиг (GIG_FINAL.md).
25 проверок: meta/og/twitter/canonical/h1-h3/img-alt/robots/sitemap/https/viewport/lang/
размер/TTFB/security-заголовки/mixed-content/ссылки/charset/JSON-LD. Взвешенный скор 0-100.
"""
import sys
import time
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from html import escape as _esc

OUT = Path(__file__).parent / "out"

try:
    import requests as _rq  # type: ignore
    HAVE_RQ = True
except Exception:
    HAVE_RQ = False

try:
    from bs4 import BeautifulSoup as _BS  # type: ignore
    HAVE_BS = True
except Exception:
    HAVE_BS = False

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) auto-income-audit/2.0"}

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# id, human name, severity, effort, weight, recommendation text
CHECKS_META = [
    ("http200", "HTTP 200 (page loads)", "critical", "S", 5,
     "Fix URL availability: remove redirect chains/404/5xx, check DNS and hosting."),
    ("https", "HTTPS (encryption)", "critical", "S", 5,
     "Move the site to HTTPS, issue a certificate (Let's Encrypt is free), set up 301 from HTTP."),
    ("noindex", "No accidental noindex (page is indexable)", "critical", "S", 5,
     "Remove meta robots noindex / X-Robots-Tag noindex if the page should rank."),
    ("title", "Title 30-60 characters", "high", "S", 4,
     "Rewrite the title: main keyword first, 30-60 characters, unique per page."),
    ("meta-desc", "Meta description 70-160 characters", "high", "S", 4,
     "Write a 70-160 character description with the keyword and a CTA (improves SERP CTR)."),
    ("viewport", "Viewport (mobile adaptation)", "high", "S", 4,
     "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."),
    ("h1-one", "Exactly one H1", "high", "S", 4,
     "Keep one H1 with the main keyword; demote the rest to H2/H3."),
    ("canonical", "Canonical tag present", "high", "S", 3,
     "Add <link rel=\"canonical\" href=\"...\"> with the page canonical URL."),
    ("mixed", "No mixed content (http assets on https)", "high", "M", 3,
     "Move all src/href to https or relative URLs; browsers block http assets."),
    ("img-alt", "IMG alt on all images", "medium", "S", 3,
     "Add meaningful alt text to every <img>; decorative images get empty alt=\"\"."),
    ("size", "HTML weight < 300 KB", "medium", "M", 3,
     "Compress HTML/images (WebP/AVIF), enable gzip/brotli, remove markup bloat."),
    ("ttfb", "TTFB < 800 ms (response speed)", "medium", "M", 3,
     "Speed up the server: cache, CDN, DB/PHP tuning; target TTFB < 800 ms, LCP < 2.5 s."),
    ("robots-meta", "robots.txt available", "medium", "S", 2,
     "Create /robots.txt: allow what matters, disallow admin/search/junk, point to the sitemap."),
    ("sitemap", "sitemap.xml available", "medium", "S", 2,
     "Generate /sitemap.xml with all indexable URLs and keep it updated automatically."),
    ("lang", "lang attribute on <html>", "medium", "S", 2,
     "Add lang (e.g. <html lang=\"en\">) - helps search engines and screen readers."),
    ("charset", "Charset UTF-8 declared", "medium", "S", 2,
     "Declare <meta charset=\"utf-8\"> first in <head> (prevents mojibake and indexing issues)."),
    ("og", "Open Graph (og:title/description/image >=2)", "medium", "S", 2,
     "Add og:title/og:description/og:image - rich previews in social apps and messengers."),
    ("sec-headers", "Security headers (>=2 of 5)", "medium", "M", 2,
     "Enable HSTS, CSP, X-Content-Type-Options, X-Frame-Options/frame-ancestors, Referrer-Policy."),
    ("h2", "H2 subheadings present", "medium", "S", 2,
     "Break the text into H2 subheadings with secondary keywords (structure + retention)."),
    ("assets", "Moderate external assets (<=25 scripts+styles)", "low", "M", 1,
     "Reduce/merge external CSS/JS, defer non-critical ones (defer/async), drop duplicates."),
    ("anchors", "Links with non-empty anchors", "low", "S", 1,
     "Give every <a> meaningful text/aria-label; rewrite empty links and 'click here'."),
    ("h-order", "Heading order without gaps (H1->H2->H3)", "low", "S", 1,
     "Do not skip levels (after H1 comes H2, not H3); fix the heading hierarchy."),
    ("twitter", "Twitter Card markup", "low", "S", 1,
     "Add <meta name=\"twitter:card\" content=\"summary_large_image\"> (+title/description)."),
    ("favicon", "Favicon declared", "low", "S", 1,
     "Add <link rel=\"icon\"> + apple-touch-icon; missing favicon means extra 404 traffic."),
    ("jsonld", "Structured data (JSON-LD)", "low", "M", 1,
     "Add schema.org JSON-LD (Organization/Article/Product/FAQ) for rich snippets."),
]

SEC_HEADERS = ["strict-transport-security", "content-security-policy",
               "x-content-type-options", "x-frame-options", "referrer-policy"]


def fetch(url):
    """-> (html, status, ms, headers_dict_lowercased). requests primary, urllib fallback."""
    t0 = time.time()
    if HAVE_RQ:
        try:
            r = _rq.get(url, headers=UA, timeout=20, allow_redirects=True)
            ms = round((time.time() - t0) * 1000)
            enc = r.encoding or "utf-8"
            try:
                html = r.content.decode(enc, errors="ignore")
            except Exception:
                html = r.text
            hdr = {k.lower(): v for k, v in r.headers.items()}
            return html, r.status_code, ms, hdr, (r.url or url)
        except Exception as e:
            return "", 0, 0, {"error": str(e)}, url
    import urllib.request
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "ignore")
            return html, r.status, round((time.time() - t0) * 1000), dict(r.headers), (r.geturl() or url)
    except Exception as e:
        return "", 0, 0, {"error": str(e)}, url


def tiny_get(url, limit=6000):
    """Лёгкая проверка существования robots.txt/sitemap.xml. -> (ok, detail)."""
    if HAVE_RQ:
        try:
            r = _rq.get(url, headers=UA, timeout=10)
            body = r.content[:limit]
            ok = r.status_code == 200 and len(body.strip()) > 0
            return ok, f"status={r.status_code}" if ok else f"HTTP {r.status_code}"
        except Exception:
            return False, "none"
    import urllib.request
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read(limit)
            return r.status == 200 and len(body.strip()) > 0, ""
    except Exception:
        return False, "none"


def _soup(html):
    if HAVE_BS:
        return _BS(html, "html.parser")
    return None


def audit(url):
    html, status, ms, hdr, final_url = fetch(url)
    dom = urlparse(final_url).netloc or urlparse("https://" + url).netloc
    low = html.lower()
    soup = _soup(html)
    R = {}  # id -> (ok, detail)

    def rx(pat, flags=re.S | re.I):
        return re.search(pat, html, flags)

    R["http200"] = (status == 200, f"status={status} time={ms}ms")
    R["https"] = (final_url.startswith("https"), "")

    # --- noindex ---
    noindex = False
    if soup:
        m = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
        if m and m.get("content") and "noindex" in m["content"].lower():
            noindex = True
    elif re.search(r'<meta[^>]+name=["\']robots["\'][^>]*noindex', html, re.I):
        noindex = True
    if "noindex" in hdr.get("x-robots-tag", "").lower():
        noindex = True
    R["noindex"] = (not noindex and status == 200, "noindex found!" if noindex else "")

    # --- title / description ---
    if soup and soup.title and soup.title.string:
        title = soup.title.string.strip()
    else:
        m = rx(r"<title>(.*?)</title>")
        title = (m.group(1).strip() if m else "")
    title = re.sub(r"\s+", " ", title)
    R["title"] = (30 <= len(title) <= 60, f"{len(title)} chars: {title[:80]}")

    if soup:
        dm = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        desc = (dm.get("content", "") if dm else "").strip()
    else:
        d = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I)
        if not d:
            d = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', html, re.I)
        desc = (d.group(1) if d else "")
    R["meta-desc"] = (70 <= len(desc) <= 160, f"{len(desc)} chars")

    R["viewport"] = ("viewport" in low, "")

    # --- lang ---
    if soup and soup.find("html") and soup.find("html").get("lang"):
        R["lang"] = (True, soup.find("html")["lang"])
    else:
        m = re.search(r'<html[^>]+lang=["\']([^"\']+)["\']', html, re.I)
        R["lang"] = (bool(m), m.group(1) if m else "none")

    # --- canonical ---
    if soup:
        can = soup.find("link", attrs={"rel": re.compile(r"canonical", re.I)})
        R["canonical"] = (bool(can and can.get("href")), (can.get("href", "")[:80] if can and can.get("href") else "none"))
    else:
        R["canonical"] = (bool(re.search(r'rel=["\']canonical["\']', html, re.I)), "")

    # --- headings ---
    if soup:
        h1n = len(soup.find_all("h1"))
        h2n = len(soup.find_all("h2"))
        levels = [int(h.name[1]) for h in soup.find_all(re.compile(r"^h[1-6]$"))]
    else:
        h1n = len(re.findall(r"<h1[^>]*>", html, re.I))
        h2n = len(re.findall(r"<h2[^>]*>", html, re.I))
        levels = [int(x) for x in re.findall(r"<(h[1-6])[^>]*>", html, re.I) for x in [x[1]]]
    R["h1-one"] = (h1n == 1, f"h1={h1n}")
    R["h2"] = (h2n >= 1, f"h2={h2n}")
    skip = any(b - a > 1 for a, b in zip(levels, levels[1:])) if levels else False
    R["h-order"] = (not skip and bool(levels), f"levels={len(levels)}" if levels else "no headings")

    # --- og / twitter / favicon / json-ld ---
    og_tags = ["og:title", "og:description", "og:image"]
    if soup:
        og_hit = sum(1 for p in og_tags if soup.find("meta", attrs={"property": p}))
        tw = bool(soup.find("meta", attrs={"name": re.compile(r"^twitter:card$", re.I)}))
        fav = bool(soup.find("link", attrs={"rel": re.compile(r"icon", re.I)}))
        has_ld = bool(soup.find("script", attrs={"type": re.compile(r"ld\+json", re.I)}))
    else:
        og_hit = sum(1 for p in og_tags if p in low)
        tw = "twitter:card" in low
        fav = bool(re.search(r'rel=["\'][^"\']*icon', html, re.I))
        has_ld = "ld+json" in low
    R["og"] = (og_hit >= 2, f"{og_hit}/3")
    R["twitter"] = (tw, "" if tw else "none")
    R["favicon"] = (fav, "" if fav else "none")
    R["jsonld"] = (has_ld, "" if has_ld else "none")

    # --- charset ---
    ctype = hdr.get("content-type", "")
    R["charset"] = (("utf-8" in low[:4000] and "charset" in low[:4000]) or "utf-8" in ctype.lower(),
                    "" if True else "")

    # --- images ---
    if soup:
        imgs = soup.find_all("img")
        noalt = [i for i in imgs if not i.get("alt")]
        R["img-alt"] = (len(noalt) == 0, f"img={len(imgs)} without alt={len(noalt)}")
    else:
        imgs = re.findall(r"<img[^>]*>", html, re.I)
        noalt = [i for i in imgs if "alt" not in i.lower()]
        R["img-alt"] = (len(noalt) == 0, f"img={len(imgs)} without alt={len(noalt)}")

    # --- robots / sitemap ---
    _u = urlparse(url)
    base = f"{_u.scheme}://{_u.netloc}"
    ok_r, det_r = tiny_get(base + "/robots.txt")
    ok_s, det_s = tiny_get(base + "/sitemap.xml")
    R["robots-meta"] = (ok_r, det_r)
    R["sitemap"] = (ok_s, det_s)

    # --- size / speed ---
    kb = round(len(html.encode("utf-8", "ignore")) / 1024, 1)
    R["size"] = (len(html) < 300_000, f"{kb}KB")
    R["ttfb"] = (0 < ms < 800, f"{ms}ms")

    # --- security headers ---
    sec_hit = [h for h in SEC_HEADERS if h in hdr]
    R["sec-headers"] = (len(sec_hit) >= 2, f"{len(sec_hit)}/5: {','.join(s.split('-')[0] for s in sec_hit) or 'none'}")

    # --- mixed content ---
    if url.startswith("https"):
        mixed = re.findall(r'''(?:src|href)\s*=\s*["']http://[^"']+''', html, re.I)
        R["mixed"] = (len(mixed) == 0, f"http-assets={len(mixed)}")
    else:
        R["mixed"] = (True, "n/a (page is not https)")

    # --- external assets count ---
    if soup:
        n_js = len([s for s in soup.find_all("script", src=True)])
        n_css = len([l for l in soup.find_all("link", rel=re.compile(r"stylesheet", re.I))])
    else:
        n_js = len(re.findall(r"<script[^>]+src=", html, re.I))
        n_css = len(re.findall(r'rel=["\']stylesheet["\']', html, re.I))
    R["assets"] = (n_js + n_css <= 25, f"js={n_js} css={n_css}")

    # --- anchors ---
    internal_links = []
    if soup:
        links = soup.find_all("a")
        bad = 0
        for a in links:
            txt = (a.get_text() or "").strip()
            if not txt and not a.get("aria-label") and not a.get("title") and not a.find("img"):
                bad += 1
        R["anchors"] = (bad == 0 or not links, f"links={len(links)} empty={bad}")
        seen_links = set()
        for a in links:
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absu = urljoin(final_url, href).split("#")[0]
            p = urlparse(absu)
            if p.scheme not in ("http", "https") or p.netloc != dom:
                continue
            if absu in seen_links:
                continue
            seen_links.add(absu)
            internal_links.append(absu)
            if len(internal_links) >= 200:
                break
    else:
        R["anchors"] = (True, "n/a (regex mode)")

    # --- score ---
    total_w = sum(w for _, _, _, _, w, _ in CHECKS_META)
    got_w = sum(w for (cid, _, _, _, w, _) in CHECKS_META if R.get(cid, (False, ""))[0])
    score = round(got_w / total_w * 100) if total_w else 0

    # --- fixes: failed, sorted by severity then weight ---
    meta_by_id = {c[0]: c for c in CHECKS_META}
    failed = [cid for cid in meta_by_id if not R.get(cid, (False, ""))[0]]
    failed.sort(key=lambda c: (SEV_RANK[meta_by_id[c][2]], -meta_by_id[c][4]))
    fixes = []
    for cid in failed:
        _, name, sev, eff, w, rec = meta_by_id[cid]
        fixes.append({"id": cid, "name": name, "sev": sev, "effort": eff,
                      "detail": R[cid][1], "rec": rec})

    return {"url": url, "final_url": final_url, "dom": dom, "status": status, "ms": ms, "kb": kb,
            "score": score, "got_w": got_w, "total_w": total_w, "links": internal_links,
            "results": [(c[0], c[1], c[2], c[3], R[c[0]][0], R[c[0]][1]) for c in CHECKS_META],
            "fixes": fixes}


def grade(score):
    if score >= 90:
        return "A - excellent"
    if score >= 75:
        return "B - good"
    if score >= 55:
        return "C - needs work"
    if score >= 35:
        return "D - weak"
    return "F - poor"


SEV_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}


def render_md(a):
    L = [f"# SEO audit {a['dom']}",
         f"URL: {a['url']}  |  date: {time.strftime('%Y-%m-%d')}  |  engine: seo-audit v2 (25 checks)",
         "",
         f"## Score: {a['score']}/100 ({grade(a['score'])})",
         f"Weighted: {a['got_w']}/{a['total_w']} · HTTP {a['status']} · {a['ms']}ms · {a['kb']}KB",
         "",
         "## Checks",
         "| # | Check | Status | Details | Severity | Effort |",
         "|---|----------|--------|--------|----------|--------------|"]
    for i, (cid, name, sev, eff, w, _) in enumerate(
            [(c[0], c[1], c[2], c[3], c[4], c[5]) for c in a["results"]], 1):
        ok = a["results"][i - 1][4]
        det = a["results"][i - 1][5]
        L.append(f"| {i} | {name} | {'✅' if ok else '❌'} | {det or '—'} | {sev} | {eff} |")
    L += ["", "## Priority fixes"]
    if not a["fixes"]:
        L.append("All 25 checks passed. Great job! 🎉")
    else:
        for i, f in enumerate(a["fixes"], 1):
            L.append(f"{i}. {SEV_EMOJI[f['sev']]} **[{f['sev']}/{f['effort']}]** {f['name']}"
                     + (f" — {f['detail']}" if f["detail"] else "")
                     + f" → {f['rec']}")
    L += ["",
          "## What to do next",
          "1. Title/description/H1 aligned with your keyword set (keywords + CTR).",
          "2. Technical fix pack: canonical/robots/sitemap/security headers.",
          "3. Speed: compression, cache, CDN, Core Web Vitals.",
          "4. Content plan: H2 structure, alt text, internal links.",
          "",
          "_Generated by seo-audit v2 · 25 checks · stdlib-first_"]
    return "\n".join(L) + "\n"


def render_html(a):
    rows = []
    for i, (cid, name, sev, eff, w, _) in enumerate(
            [(c[0], c[1], c[2], c[3], c[4], c[5]) for c in a["results"]], 1):
        ok = a["results"][i - 1][4]
        det = a["results"][i - 1][5]
        cls = "ok" if ok else "bad"
        rows.append(f"<tr class='{cls}'><td>{i}</td><td>{_esc(name)}</td>"
                    f"<td>{'✅' if ok else '❌'}</td><td>{_esc(det or '—')}</td>"
                    f"<td>{sev}</td><td>{eff}</td></tr>")
    fixes = ("<p>All 25 checks passed. 🎉</p>" if not a["fixes"]
             else "<ol>" + "".join(
                 f"<li><b>[{f['sev']}/{f['effort']}]</b> {_esc(f['name'])}"
                 + (f" — <i>{_esc(f['detail'])}</i>" if f["detail"] else "")
                 + f"<br>{_esc(f['rec'])}</li>" for f in a["fixes"]) + "</ol>")
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEO audit {_esc(a['dom'])} - {a['score']}/100</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}}
.score{{font-size:28px;background:#f4f6fb;border:1px solid #dde;padding:12px 18px;border-radius:10px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:6px 8px;font-size:14px}}
tr.ok td{{background:#f2fbf2}}tr.bad td{{background:#fff5f3}}th{{background:#f0f0f0}}</style>
</head><body>
<h1>SEO audit {_esc(a['dom'])}</h1>
<p>URL: {_esc(a['url'])} | date: {time.strftime('%Y-%m-%d')} | seo-audit v2 (25 checks)</p>
<div class="score">Score: {a['score']}/100 ({_esc(grade(a['score']))})</div>
<p>Weighted: {a['got_w']}/{a['total_w']} · HTTP {a['status']} · {a['ms']}ms · {a['kb']}KB</p>
<h2>Checks</h2>
<table><tr><th>#</th><th>Check</th><th>Status</th><th>Details</th><th>Severity</th><th>Effort</th></tr>
{''.join(rows)}</table>
<h2>Priority fixes</h2>
{fixes}
<hr><p><i>Generated by seo-audit v2 · 25 checks · stdlib-first</i></p>
</body></html>
"""


def save(a):
    OUT.mkdir(parents=True, exist_ok=True)
    md = OUT / f"{a['dom']}.md"
    ht = OUT / f"{a['dom']}.html"
    md.write_text(render_md(a), encoding="utf-8")
    ht.write_text(render_html(a), encoding="utf-8")
    print(f"OK {md.name} + {ht.name} score={a['score']}/100")
    return a


def write_index(results):
    OUT.mkdir(parents=True, exist_ok=True)
    rows = sorted(results, key=lambda a: -a["score"])
    L = ["# SEO-аудит — портфолио демо", "",
         f"Сгенерировано: {time.strftime('%Y-%m-%d')} · audit.py v2 · 25 проверок · скор 0–100",
         "",
         "| Сайт | Скор | Оценка | Детали |",
         "|------|------|--------|--------|"]
    for a in rows:
        L.append(f"| {a['dom']} | **{a['score']}/100** | {grade(a['score'])} "
                 f"| [{a['dom']}.md]({a['dom']}.md) · [{a['dom']}.html]({a['dom']}.html) |")
    L += ["", "_Демо-отчёты для гига (Fiverr/Upwork/Kwork). Полные версии — в GIG_FINAL.md._", ""]
    (OUT / "index.md").write_text("\n".join(L), encoding="utf-8")
    items = "".join(
        f"<li><b>{_esc(a['dom'])}</b> — {a['score']}/100 ({_esc(grade(a['score']))}) "
        f"<a href='{a['dom']}.html'>{a['dom']}.html</a> · <a href='{a['dom']}.md'>md</a></li>"
        for a in rows)
    (OUT / "index.html").write_text(
        f"<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        f"<title>SEO-аудит — портфолио</title></head><body>"
        f"<h1>SEO-аудит — портфолио демо</h1><ol>{items}</ol></body></html>",
        encoding="utf-8")
    print(f"OK index.md/index.html ({len(rows)} отчётов)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: audit.py URL | audit.py --batch urls.txt")
        sys.exit(1)
    if sys.argv[1] == "--batch" and len(sys.argv) > 2:
        urls = [l.strip() for l in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")]
        res = [save(audit(u)) for u in urls]
        write_index(res)
    else:
        save(audit(sys.argv[1]))
