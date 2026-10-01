#!/usr/bin/env python3
"""
Download Emma (e2ma) "webview" newsletters into the SIH newsletter archive.

Run from the root of your clone of Sydney-Informatics-Hub/newsletter:

    python archive_newsletters.py                      # download + sanitise only
    python archive_newsletters.py --resolve-links --localise-images   # recommended
    python archive_newsletters.py --only 2026-10       # one issue
    python archive_newsletters.py --from-dir DIR --series training ...   # training updates
    python archive_newsletters.py --update-indexes     # rebuild README list, index.html, nav.js, 404.html, search/
    python archive_newsletters.py --sanitise-existing --dry-run   # preview cleaning old pages
    python archive_newsletters.py --check-images       # are all images stored in the archive?

Standard library only (Python 3.8+).

What it does to each page, and why
----------------------------------
1. Removes the Emma open-tracking pixel (otherwise every archive visitor is
   counted as an "open" for the recipient whose webview link you copied).
2. Replaces the per-recipient "update preferences" and "unsubscribe" links,
   which embed a member id and signed token, with the generic subscribe link.
3. (--resolve-links) Replaces t.e2ma.net/click/... redirects with the real
   destination URLs, so links keep working if the Emma account lapses and
   clicks are not attributed to one recipient. NOTE: resolving requests each
   click URL once, which registers one click per link against that campaign.
4. (--localise-images) Downloads images into <series folder>/img/<YYYY-MM>/ so the
   archive does not depend on Emma's image host.
5. Saved copies from the Outlook web app ("Save as" of the reading pane) are
   ~7 MB because they contain Outlook's whole JavaScript app. For those the
   email body is extracted, Outlook artefacts (search highlights, internal ids,
   tooltips) are removed, and the result is wrapped in a minimal page.
   Mimecast / Safe Links wrappers around links are unwrapped (Safe Links
   offline; Mimecast needs --resolve-links because its URLs hide the target).
6. Leaves Emma's <meta name="robots" content="noindex, nofollow"> in place
   unless you pass --allow-index.
"""
import argparse
import collections
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

# --------------------------------------------------------------------------
# Add issues here as "YYYY-MM": "webview URL". Existing files are skipped.
# (No January issue appears to be sent, so none is listed.)
# --------------------------------------------------------------------------
ISSUES = {
    "2026-10": "https://t.e2ma.net/message/ig7cxs/2vg2xhp",
    "2026-09": "https://t.e2ma.net/message/i4tivs/2vg2xhp",
    "2026-08": "https://t.e2ma.net/message/utdkts/2vg2xhp",
    "2026-07": "https://t.e2ma.net/message/e9m5qs/2vg2xhp",
    "2026-06": "https://t.e2ma.net/message/yw6zos/2vg2xhp",
    "2026-05": "https://t.e2ma.net/message/2zz3ms/2vg2xhp",
    "2026-04": "https://t.e2ma.net/message/aiztjs/2vg2xhp",
    "2026-03": "https://t.e2ma.net/message/2v8nhs/2vg2xhp",
    "2026-02": "https://t.e2ma.net/message/mr62es/2vg2xhp",
    "2025-12": "https://t.e2ma.net/message/ai7fas/2vg2xhp",
    "2025-11": "https://t.e2ma.net/message/i4xp7r/2vg2xhp",
    "2025-10": "https://t.e2ma.net/message/elc54r/2vg2xhp",
    "2025-09": "https://t.e2ma.net/message/uxk01r/2vg2xhp",
    "2025-08": "https://t.e2ma.net/message/2ni6zr/2vg2xhp",
    "2025-07": "https://t.e2ma.net/message/e18sxr/2vg2xhp",
    "2025-06": "https://t.e2ma.net/message/2fgvvr/2vg2xhp",
    "2025-05": "https://t.e2ma.net/message/2rlvsr/2vg2xhp",
    "2025-04": "https://t.e2ma.net/message/ut1qqr/2vg2xhp",
    # ... add 2024-08 to 2025-03 here if you can recover them (see notes)
}

SUBSCRIBE_URL = "https://app.e2ma.net/app2/audience/signup/1945889/1928048.1127158640/"
SITE_BASE = "https://sydney-informatics-hub.github.io/newsletter/"
# Each series gets its own folder so same-month issues don't collide.
DOCS_DIR = Path("docs")   # published root: index.html, nav.js, 404.html and assets/ live here
SERIES = {
    "newsletter": {"dir": "docs/monthly", "title": "SIH Newsletter", "heading": "Monthly Newsletter",
                   "label": "Monthly newsletter",
                   "about": "News, scheme opportunities and training from the Sydney Informatics Hub.",
                   "subscribe": SUBSCRIBE_URL, "url_path": "monthly/"},
    # subscribe "#" = no link; set the real sign-up page with --subscribe-url
    "training": {"dir": "docs/training", "title": "SIH Training Update",
                 "heading": "Training Update", "label": "Training update",
                 "about": "Upcoming training and events from the Sydney Informatics Hub.",
                 "subscribe": "#", "url_path": "training/"},
}
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
DELAY = 0.5  # seconds between requests; be polite


# ------------------------------------------------------------------ network
def _request(url):
    return urllib.request.Request(url, headers={"User-Agent": UA})


def fetch_text(url):
    with urllib.request.urlopen(_request(url), timeout=30) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def fetch_bytes(url):
    with urllib.request.urlopen(_request(url), timeout=30) as r:
        return r.read(), r.headers.get_content_type()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def resolve_redirect(url):
    """One hop: the Location of a 3xx, or the target of a meta-refresh / JS
    redirect on a 200 interstitial page. Returns None if neither is found."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(_request(url), timeout=30) as r:
            body = r.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return e.headers.get("Location")
        return None
    except Exception:
        return None
    m = (re.search(r"http-equiv=[\"']refresh[\"'][^>]*content=[\"'][^\"']*url=([^\"']+)", body, re.I)
         or re.search(r"location(?:\.href)?\s*=\s*[\"'](https?://[^\"']+)[\"']", body))
    return html_unescape(m.group(1)) if m else None


# ----------------------------------------------------------------- cleaning
SAVED_FROM = re.compile(r"<!--\s*saved from url=\(\d+\)\S*\s*-->[ \t]*\r?\n?", re.I)
# Emma's web view adds <meta http-equiv="Content-Security-Policy" content="script-src 'self'">.
# It means nothing in the archive and would block any script from another host (GoatCounter).
CSP_META = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*[\"']?content-security-policy[\"']?[^>]*>[ \t]*\r?\n?", re.I)
TRACK_PIXEL = re.compile(r"<img\b[^>]*\bt\.e2ma\.net/track/[^>]*>", re.I)
OPTOUT = re.compile(r"https://t\.e2ma\.net/optout/[^\"'\s>]*", re.I)
# update-preferences link: signup/<audience>/<form>/<list>/<member>/?s=<token>
UPDATE = re.compile(
    r"https://app\.e2ma\.net/app2/audience/signup/\d+/\d+/\d+/\d+/[^\"'\s>]*", re.I)
ROBOTS = re.compile(r"<meta[^>]+name=[\"']robots[\"'][^>]*>", re.I)


def sanitise(html, allow_index=False, subscribe_url=None, nav_src=None):
    subscribe_url = subscribe_url or SUBSCRIBE_URL
    html = SAVED_FROM.sub("", html)
    html = CSP_META.sub("", html)
    html = TRACK_PIXEL.sub("", html)
    html = neutralise_footer_links(html, subscribe_url)
    html = unlink_webview(html)
    html = localise_toc_links(html)
    html = OPTOUT.sub(subscribe_url, html)
    html = UPDATE.sub(subscribe_url, html)
    if allow_index:
        html = ROBOTS.sub("", html)
    if not re.search(r"<meta[^>]+charset", html, re.I):
        html = re.sub(r"(<head[^>]*>)", r'\1<meta charset="utf-8">', html,
                      count=1, flags=re.I)
    if nav_src:
        html = add_nav_hook(html, nav_src)
    return html


# Hosts that only exist to wrap/redirect to the real destination.
WRAPPER_HOST = re.compile(
    r"(^|\.)(e2ma\.net|mimecastprotect\.com|mimecast\.com|"
    r"safelinks\.protection\.outlook\.com|myemma\.com)$", re.I)
WRAPPED_URL = re.compile(
    r"^https://(t\.e2ma\.net/click/|url\.[a-z0-9.]*mimecastprotect\.com/s/|"
    r"protect[a-z0-9-]*\.mimecast\.com/s/|"
    r"[a-z0-9.-]*safelinks\.protection\.outlook\.com/)", re.I)
ANCHOR = re.compile(r"<a\b([^>]*?)>(.*?)</a>", re.S | re.I)
HREF_ATTR = re.compile(r"(\bhref=)([\"'])([^\"']*)\2", re.I)
UNRESOLVED = []  # (slug, anchor text, url) reported at the end
# resolved targets that look recipient-specific are never published
SENSITIVE_DEST = re.compile(r"unsubscribe|opt-?out|[?&]email=|/audience/signup/\d+/\d+/\d+/\d+", re.I)
SUBSCRIBE_FALLBACK = [SUBSCRIBE_URL]


def _host(url):
    return urllib.parse.urlparse(url).hostname or ""


def unwrap_safelinks(url):
    """Safe Links stores the real target in the url= query parameter."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(html_unescape(url)).query)
    return q["url"][0] if q.get("url") else None


def html_unescape(s):
    import html
    return html.unescape(s)


def resolve_chain(url, max_hops=8):
    """Follow wrapper redirects until a non-wrapper host is reached."""
    cur = html_unescape(url)
    for _ in range(max_hops):
        if "safelinks.protection.outlook.com" in _host(cur):
            nxt = unwrap_safelinks(cur)
        else:
            nxt = resolve_redirect(cur)
            time.sleep(DELAY)
        if not nxt:
            return None
        cur = urllib.parse.urljoin(cur, nxt)
        if not WRAPPER_HOST.search(_host(cur)):
            return cur
    return None


def resolve_links(html, cache, cache_path, slug=""):
    """Replace click-tracking / Mimecast / Safe Links hrefs with real targets."""
    def swap(m):
        attrs, inner = m.group(1), m.group(2)
        h = HREF_ATTR.search(attrs)
        if not h or not WRAPPED_URL.match(html_unescape(h.group(3))):
            return m.group(0)
        wrapped = h.group(3)
        dest = cache.get(wrapped)
        if dest is None:
            dest = resolve_chain(wrapped)
            if dest:
                cache[wrapped] = dest
                cache_path.write_text(json.dumps(cache, indent=1))
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner)).strip()
        if dest and SENSITIVE_DEST.search(dest):
            UNRESOLVED.append((slug, f"[PERSONAL LINK REMOVED] {text[:45]}", wrapped))
            dest = SUBSCRIBE_FALLBACK[0]
        if dest is None and re.match(r"https?://", text):
            dest = text  # visible link text is itself the URL
        if dest is None:
            UNRESOLVED.append((slug, text[:60], wrapped))
            return m.group(0)
        attrs = HREF_ATTR.sub(lambda x: f"{x.group(1)}{x.group(2)}{dest}{x.group(2)}",
                              attrs, count=1)
        return f"<a{attrs}>{inner}</a>"

    return ANCHOR.sub(swap, html)


IMG_SRC = re.compile(r"(<img\b[^>]*?\bsrc=)([\"'])(https?://[^\"']+)\2", re.I)
BG_ATTR = re.compile(r"(\bbackground=)([\"'])((?:https?:)?//[^\"']+)\2", re.I)
CSS_URL = re.compile(r"url\(\s*([\"']?)((?:https?:)?//[^)\"']+)\1\s*\)", re.I)
SRCSET = re.compile(r"(\bsrcset=)([\"'])([^\"']*)\2", re.I)
EXT_BY_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
               "image/webp": ".webp", "image/svg+xml": ".svg", "image/x-icon": ".ico",
               "image/vnd.microsoft.icon": ".ico", "image/avif": ".avif",
               "font/woff2": ".woff2", "font/woff": ".woff", "font/ttf": ".ttf"}


def localise_images(html, slug, out_dir):
    """Download every remote image/asset the page embeds into <out_dir>/img/<slug>/
    and point the page at the local copy. Covers <img src>, srcset, the legacy
    background="..." attribute and CSS url(...). Links (<a href>) are left alone."""
    img_dir = out_dir / "img" / slug
    seen = {}

    def get(url):
        if url.startswith("//"):
            url = "https:" + url
        if url not in seen:
            try:
                data, ctype = fetch_bytes(url)
                ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
                if ext not in EXT_BY_TYPE.values():
                    ext = EXT_BY_TYPE.get(ctype, ".bin")
                name = hashlib.sha1(url.encode()).hexdigest()[:10] + ext
                img_dir.mkdir(parents=True, exist_ok=True)
                (img_dir / name).write_bytes(data)
                seen[url] = f"img/{slug}/{name}"
                time.sleep(DELAY / 2)
            except Exception as e:  # keep the remote URL if download fails
                print(f"    ! image failed {url}: {e}", file=sys.stderr)
                seen[url] = None
        return seen[url]

    def attr(m):                      # <img src> and background= (pre, quote, url)
        new = get(m.group(3))
        return m.group(0) if new is None else f"{m.group(1)}{m.group(2)}{new}{m.group(2)}"

    def css(m):
        new = get(m.group(2))
        return m.group(0) if new is None else f"url({m.group(1)}{new}{m.group(1)})"

    def srcset(m):
        out = []
        for cand in m.group(3).split(","):
            bits = cand.strip().split()
            if bits and re.match(r"(https?:)?//", bits[0]):
                bits[0] = get(bits[0]) or bits[0]
            out.append(" ".join(bits))
        return f"{m.group(1)}{m.group(2)}{', '.join(out)}{m.group(2)}"

    html = IMG_SRC.sub(attr, html)
    html = SRCSET.sub(srcset, html)
    html = BG_ATTR.sub(attr, html)
    return CSS_URL.sub(css, html)


# ----------------------------------------------------------- image auditing
def _is_hidden_pixel(tag):
    return bool(re.search(r"\bwidth=[\"']?1[\"']?[\s>/]", tag, re.I)
                and re.search(r"\bheight=[\"']?1[\"']?[\s>/]", tag, re.I)
                or re.search(r"display:\s*none", tag, re.I))


def image_refs(html):
    """Yield (kind, url, tag, position) for every place a page references an image/asset."""
    for m in re.finditer(r"<img\b[^>]*>", html, re.I | re.S):
        tag = m.group(0)
        for k, pat in (("<img src>", r"\bsrc\s*=\s*([\"'])(.*?)\1"),
                       ("srcset", r"\bsrcset\s*=\s*([\"'])(.*?)\1")):
            for mm in re.finditer(pat, tag, re.I | re.S):
                urls = ([c.split()[0] for c in mm.group(2).split(",") if c.split()]
                        if k == "srcset" else [mm.group(2)])
                for u in urls:
                    yield k, u.strip(), tag, m.start()
    for m in re.finditer(r"\bbackground\s*=\s*([\"'])(.*?)\1", html, re.I | re.S):
        yield "background=", m.group(2).strip(), m.group(0), m.start()
    for m in re.finditer(r"url\(\s*([\"']?)(.*?)\1\s*\)", html, re.I | re.S):
        yield "css url()", m.group(2).strip(), m.group(0), m.start()


def image_problems(html, page_dir):
    """Return a list of (category, kind, url) for images that are NOT stored in the
    archive. Categories: remote, unusable, missing file, plus harmless ones (hidden 1x1
    trackers and unloadable font references) that never affect how a page looks."""
    problems, seen = [], set()
    for kind, url, tag, pos in image_refs(html):
        if not url or url.startswith(("data:", "#", "about:")):
            continue
        if re.match(r"(https?:)?//", url):
            cat = "remote"
        elif re.match(r"[a-z][a-z0-9+.-]*:", url, re.I):      # content-blocker://, cid:, ...
            if url.lower().startswith("custom-font:"):
                cat = FONT_REF
            elif kind in ("<img src>", "srcset") and _is_hidden_pixel(tag):
                cat = HIDDEN_PIXEL
            else:
                cat = "unusable"
        else:
            path = Path(page_dir, urllib.parse.unquote(url.split("?")[0].split("#")[0]))
            if path.exists():
                continue
            cat = "missing file"
        if (pos, cat) not in seen:           # one finding per tag, not per attribute
            seen.add((pos, cat))
            problems.append((cat, kind, url))
    return problems


HIDDEN_PIXEL = "hidden tracker (harmless)"
FONT_REF = "font reference (harmless)"
HARMLESS = (HIDDEN_PIXEL, FONT_REF)


def check_images():
    """Audit every archived page. Returns the number of real problems found."""
    cats = ["remote", "unusable", "missing file", *HARMLESS]
    total = {c: 0 for c in cats}
    bad_pages = {}
    n_pages = 0
    targets = [f for key, cfg in SERIES.items()
               for f in sorted(Path(cfg["dir"]).glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].htm"))]
    index = DOCS_DIR / "index.html"
    if index.exists():
        targets.append(index)
    for f in targets:
        n_pages += 1
        html = f.read_bytes().decode("utf-8", "surrogateescape")
        counts = {}
        for cat, kind, url in image_problems(html, f.parent):
            counts[cat] = counts.get(cat, 0) + 1
            total[cat] += 1
        if any(c not in HARMLESS for c in counts):
            bad_pages[f] = {c: n for c, n in counts.items() if c not in HARMLESS}
    for f, counts in bad_pages.items():
        print(f"{f}: " + ", ".join(f"{n} {c}" for c, n in counts.items()))
    real = sum(v for c, v in total.items() if c not in HARMLESS)
    print(f"\n{n_pages} pages checked. Images not stored in the archive: {total['remote']} remote, "
          f"{total['unusable']} unusable (blocked when the email was saved), "
          f"{total['missing file']} missing file.")
    print(f"Ignored as harmless: {total[HIDDEN_PIXEL]} hidden 1x1 tracking pixels, "
          f"{total[FONT_REF]} font references the browser can't load.")
    if total["remote"]:
        print("Fix remote images:  python archive_newsletters.py --sanitise-existing "
              "--localise-images")
    if total["unusable"]:
        print("Unusable images were blocked when the email was saved, so their addresses are "
              "not in the file. Re-save that email with remote images loading and re-run it "
              "with --from-file ... --force.")
    if not real:
        print("OK: every image is stored in the archive.")
    return real


# ------------------------------------------------------- README + index page
REPO_URL = "https://github.com/Sydney-Informatics-Hub/newsletter"
LOGO_PATH = "assets/sih_logo.png"   # the logo file lives in docs/assets/

# Analytics: GoatCounter page-view counting. Set GOATCOUNTER = "" to switch all of it off.
GOATCOUNTER = "https://sih.goatcounter.com/count"
GOATCOUNTER_JS = "https://gc.zgo.at/count.js"
TRACK_SEARCH = True    # also send what people search for (and which issue they open); False = page views only


def goatcounter_html():
    """GoatCounter's standard snippet for static pages. No path settings: GoatCounter records the
    part after the domain (/newsletter/monthly/2026-08.htm) and builds the dashboard link from the
    domain set for the site, the same as the other SIH GitHub Pages sites."""
    if not GOATCOUNTER:
        return ""
    return f'<script data-goatcounter="{GOATCOUNTER}" async src="{GOATCOUNTER_JS}"></script>\n'
README_START = "<!-- ARCHIVE-LIST:START (generated by archive_newsletters.py; do not edit) -->"
README_END = "<!-- ARCHIVE-LIST:END -->"


def _slugs(folder):
    return sorted((p.stem for p in Path(folder).glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].htm")),
                  reverse=True)


def _present():
    found = [(k, v, _slugs(v["dir"])) for k, v in SERIES.items()]
    return [x for x in found if x[2]]


def readme_list():
    """Markdown tables of every archived issue: one row per year, months oldest first."""
    import calendar
    lines = []
    for key, cfg, slugs in _present():
        lines += [f"### {cfg['heading']}", "", "| Year | Issues |", "| --- | --- |"]
        by_year = {}
        for sl in sorted(slugs):
            by_year.setdefault(sl[:4], []).append(sl)
        for year in sorted(by_year, reverse=True):
            cells = " · ".join(
                f'[{calendar.month_abbr[int(sl[5:])]}]({SITE_BASE}{cfg["url_path"]}{sl}.htm '
                f'"{calendar.month_name[int(sl[5:])]} {year}")' for sl in by_year[year])
            lines.append(f"| {year} | {cells} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def update_readme(path=Path("README.md")):
    """Replace only the marked block of README.md, leaving the rest untouched."""
    block = f"{README_START}\n\n{readme_list()}\n{README_END}"
    text = path.read_text(encoding="utf-8") if path.exists() else "# SIH Newsletter Archive\n"
    if README_START.split(" (")[0] in text and README_END in text:
        text = re.sub(re.escape(README_START.split(" (")[0]) + r".*?" + re.escape(README_END),
                      lambda m: block, text, count=1, flags=re.S)
    else:
        text = text.rstrip() + f"\n\n## Archive\n\n{block}\n"
    path.write_text(text, encoding="utf-8")


INDEX_CSS = """
:root{--ochre:#E64626;--charcoal:#414141;--sandstone:#FCEDE2;--navy:#1A345E;--rule:#e2dad3;color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--charcoal);font:16px/1.5 Arial,sans-serif}
.wrap{max-width:60rem;margin:0 auto;padding:0 1.25rem}
header{background:var(--ochre);color:#000;padding:2rem 0 1.75rem}
.org{margin:0;font-size:1rem}
h1{margin:.15rem 0 .6rem;font-size:clamp(1.9rem,5.5vw,2.75rem);line-height:1.1}
.intro{margin:0 0 1.4rem;max-width:34rem;font:italic 1.25rem/1.4 "Times New Roman",Times,serif}
.head{display:grid;grid-template-columns:auto 1fr;gap:1.25rem 2.25rem;align-items:center}
.logo{display:block;width:auto;height:6.25rem}
.titles h1{margin-top:0}
.latest{display:flex;flex-wrap:wrap;gap:.6rem;margin:0;padding:0;list-style:none}
.latest a{display:block;padding:.55rem .85rem;background:#fff;color:#000;text-decoration:none;border-bottom:3px solid #000}
.latest a:hover,.latest a:focus-visible{background:var(--navy);color:#fff;border-bottom-color:var(--navy)}
header a:focus-visible{outline:3px solid #000;outline-offset:2px}
main a:focus-visible,footer a:focus-visible{outline:3px solid var(--navy);outline-offset:2px}
section{margin:2.75rem 0}
h2{margin:0;font-size:1.5rem;color:#000}
.meta{margin:.15rem 0 1rem}
.year{display:grid;grid-template-columns:4rem 1fr;gap:.5rem 1rem;align-items:center;padding:.55rem 0;border-top:1px solid var(--rule)}
.year h3{margin:0;font-size:1.1rem;color:#000}
.months{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:.35rem;margin:0;padding:0;list-style:none}
.months a,.months .gap{display:block;padding:.55rem 0;text-align:center;font-size:.9rem}
.months a{background:var(--sandstone);color:var(--charcoal);font-weight:bold;text-decoration:none;border-top:3px solid var(--ochre)}
.months a:hover,.months a:focus-visible{background:var(--navy);color:#fff;border-top-color:var(--navy)}
.months .gap{color:#767676;border:1px dashed #d3cbc4}
.months .pending{visibility:hidden}
[hidden]{display:none!important}
#search{margin-top:2rem}
#search label{display:block;margin:0 0 .35rem;font-weight:bold;color:#000}
#search .row{display:flex;flex-wrap:wrap;gap:.6rem}
#search input,#search select{font:inherit;color:var(--charcoal);background:#fff;border:2px solid var(--charcoal);border-radius:0;padding:.65rem .8rem}
#search input{flex:1 1 16rem;min-width:0}
#search input:focus-visible,#search select:focus-visible{outline:3px solid var(--navy);outline-offset:2px}
#search-status{margin:.75rem 0 0;min-height:1.5em}
#results h2{margin:1.5rem 0 .25rem;font-size:1.1rem}
.hits{margin:0;padding:0;list-style:none}
.hits li{padding:.9rem 0;border-top:1px solid var(--rule)}
.hits h3{margin:0;font-size:1.1rem}
.hits h3 a{color:var(--navy)}
.hits h3 a:focus-visible{outline:3px solid var(--navy);outline-offset:2px}
.hits .meta,#results .meta{margin:.1rem 0 .35rem;font-size:.9rem}
.hits .snip{margin:0;max-width:44rem}
.hits mark{background:var(--sandstone);color:#000;font-weight:bold;border-bottom:2px solid var(--ochre)}
footer{margin-top:3rem;padding:1.25rem 0 2.5rem;border-top:1px solid var(--rule);font-size:.9rem}
footer p{margin:.25rem 0}
footer a{color:var(--charcoal)}
@media (max-width:42rem){.year{grid-template-columns:1fr}.months{grid-template-columns:repeat(6,minmax(0,1fr))}
.head{grid-template-columns:1fr}.logo{height:4.5rem}}
"""


def index_html(allow_index=False):
    import calendar
    from html import escape
    present = _present()

    def month_name(sl):
        return f"{calendar.month_name[int(sl[5:])]} {sl[:4]}"

    latest = "".join(
        f'<li><a href="{cfg["url_path"]}{sl[0]}.htm">{escape(cfg["label"])}, {month_name(sl[0])}</a></li>'
        for _, cfg, sl in present)

    sections = []
    for key, cfg, slugs in present:
        have = set(slugs)
        years = range(int(slugs[0][:4]), int(slugs[-1][:4]) - 1, -1)
        rows = []
        for y in years:
            cells = []
            for m in range(1, 13):
                sl = f"{y}-{m:02d}"
                ab = calendar.month_abbr[m]
                if sl in have:
                    label = f'{cfg["label"]}, {month_name(sl)}'
                    cells.append(f'<li><a href="{cfg["url_path"]}{sl}.htm" '
                                 f'aria-label="{escape(label)}" title="{month_name(sl)}">{ab}</a></li>')
                elif sl > slugs[0]:   # later than the newest issue: not a gap, just not yet
                    cells.append(f'<li class="gap pending" aria-hidden="true">{ab}</li>')
                else:
                    cells.append(f'<li class="gap" aria-hidden="true">{ab}</li>')
            rows.append(f'<div class="year"><h3>{y}</h3><ul class="months">{"".join(cells)}</ul></div>')
        sections.append(
            f'<section aria-labelledby="s-{key}"><h2 id="s-{key}">{escape(cfg["label"])}</h2>'
            f'<p class="meta">{escape(cfg["about"])} {len(slugs)} issues, '
            f'{slugs[-1][:4]} to {slugs[0][:4]}.</p>{"".join(rows)}</section>')

    pub_options = "".join(f'<option value="{k}">{escape(v["label"])}</option>'
                          for k, _, _ in present for v in [SERIES[k]])
    robots = "" if allow_index else '<meta name="robots" content="noindex, nofollow">\n'
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{robots}<title>SIH newsletter archive</title>
<meta name="description" content="Archived copies of Sydney Informatics Hub newsletters and training updates.">
<style>{INDEX_CSS}</style>
</head>
<body>
<header><div class="wrap head">
<img class="logo" src="{LOGO_PATH}" width="431" height="219" alt="The University of Sydney, Sydney Informatics Hub">
<div class="titles">
<h1>Sydney Informatics Hub Newsletter archive</h1>
<ul class="latest" aria-label="Latest issues">{latest}</ul>
</div>
</div></header>
<div class="wrap" id="search" role="search" hidden>
<form id="search-form" action="#" autocomplete="off">
<label for="q">Search all issues</label>
<div class="row">
<input id="q" name="q" type="search" placeholder="For example: Nextflow, or March 2023" enterkeyhint="search" spellcheck="false">
<select id="pub" aria-label="Publication"><option value="all">All publications</option>{pub_options}</select>
</div>
</form>
<p id="search-status" role="status" aria-live="polite"></p>
<div id="results"></div>
</div>
<main class="wrap" id="browse">
{"".join(sections)}
</main>
<footer><div class="wrap">
<p>These are saved copies of emails sent to subscribers. Links in older issues may no longer work.</p>
<p>Gaps in the grid are months with no issue. <a href="{REPO_URL}">How to add issues (GitHub)</a></p>
</div></footer>
<script src="search/search.js" defer></script>
{goatcounter_html()}</body>
</html>
"""


def update_indexes(allow_index=False):
    update_readme()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(index_html(allow_index), encoding="utf-8")
    (DOCS_DIR / "nav.js").write_text(nav_js(), encoding="utf-8")
    (DOCS_DIR / "404.html").write_text(not_found_html(), encoding="utf-8")
    SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (SEARCH_DIR / "data.js").write_text(search_data_js(), encoding="utf-8")
    (SEARCH_DIR / "search.js").write_text(search_js(), encoding="utf-8")
    if not (SEARCH_DIR / "minisearch.min.js").exists():
        print("WARNING: docs/search/minisearch.min.js is missing, so search will not work. "
              "See the README section on search.", file=sys.stderr)
    print("README.md list, docs/index.html, nav.js, 404.html and docs/search/ rebuilt")


# ------------------------------------------------------ previous / next navigation
NAV_START = "<!-- sih-nav:start -->"
NAV_END = "<!-- sih-nav:end -->"

NAV_TEMPLATE = r'''/* Previous / next navigation for archived issues.
   Generated by archive_newsletters.py (rebuilt by --update-indexes). Do not edit by hand. */
(function () {
  "use strict";

  // Page-view counting (GoatCounter). Every issue page loads this file, so it is switched on
  // here instead of by editing each page.
  var GC = __GOATCOUNTER__;
  if (GC) {
    var gc = document.createElement("script");
    gc.async = true;
    gc.setAttribute("data-goatcounter", GC.endpoint);
    gc.src = GC.script;
    document.head.appendChild(gc);
  }

  var ISSUES = __ISSUES__;
  var LABELS = __LABELS__;
  var FOLDERS = __FOLDERS__;   // folder name -> series, e.g. {"monthly": "newsletter"}
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"];

  // .../monthly/2024-08.htm  or  .../training/2024-08.htm
  var m = location.pathname.match(/\/([^\/]+)\/(\d{4}-\d{2})(?:\.html?)?$/);
  if (!m || !document.body) return;
  var series = FOLDERS[m[1]];
  var list = series ? ISSUES[series] : null;
  var i = list ? list.indexOf(m[2]) : -1;
  if (i < 0) return;

  var up = "../";   // every series sits one folder below index.html
  function name(slug) { var p = slug.split("-"); return MONTHS[+p[1] - 1] + " " + p[0]; }

  var CSS = [
    ":host{all:initial;display:block}",
    "nav{box-sizing:border-box;display:flex;flex-wrap:wrap;align-items:center;gap:.5rem .75rem;" +
      "padding:.6rem max(1.25rem,calc((100% - 60rem)/2));background:#FCEDE2;color:#414141;" +
      "font:14px/1.4 Arial,sans-serif}",
    "nav.top{border-bottom:3px solid #E64626}",
    "nav.bottom{border-top:3px solid #E64626}",
    ".here{flex:1 1 14rem;text-align:center;font-weight:bold}",
    "a{color:#414141;background:#fff;font-weight:bold;text-decoration:none;padding:.4rem .7rem;" +
      "border-top:3px solid #E64626}",
    "a:hover,a:focus-visible{background:#1A345E;color:#fff;border-top-color:#1A345E}",
    "a:focus-visible{outline:3px solid #1A345E;outline-offset:2px}",
    "@media (max-width:40rem){.here{order:-1;flex-basis:100%;text-align:left}" +
      ".home,.prev,.next{flex:1 1 auto;text-align:center}.lbl{display:none}" +
      ".prev::before{content:'\\2039\\a0'}.next::after{content:'\\a0\\203a'}}",
    "@media print{:host{display:none}}"
  ].join("\n");

  function link(cls, text, href, rel) {
    var a = document.createElement("a");
    a.className = cls;
    a.textContent = text;
    a.href = href;
    if (rel) a.rel = rel;
    return a;
  }

  // "Previous: July 2024" on wide screens, "<chevron> July 2024" on narrow ones
  function step(cls, label, slug) {
    var a = link(cls, "", slug + ".htm", cls);
    var lbl = document.createElement("span");
    lbl.className = "lbl";
    lbl.textContent = label + ": ";
    a.appendChild(lbl);
    a.appendChild(document.createTextNode(name(slug)));
    a.setAttribute("aria-label", label + " issue: " + name(slug));
    return a;
  }

  function bar(position) {
    var host = document.createElement("div");
    host.setAttribute("data-sih-nav", position);
    var root = host.attachShadow({ mode: "open" });   // keeps the email's own CSS out
    var style = document.createElement("style");
    style.textContent = CSS;
    var nav = document.createElement("nav");
    nav.className = position;
    nav.setAttribute("aria-label",
      position === "top" ? "Issue navigation" : "Issue navigation (end of page)");
    nav.appendChild(link("home", "All issues", up + "index.html"));
    var here = document.createElement("span");
    here.className = "here";
    here.textContent = LABELS[series] + ", " + name(list[i]);
    nav.appendChild(here);
    if (i > 0) nav.appendChild(step("prev", "Previous", list[i - 1]));
    if (i < list.length - 1) nav.appendChild(step("next", "Next", list[i + 1]));
    root.appendChild(style);
    root.appendChild(nav);
    return host;
  }

  document.body.insertBefore(bar("top"), document.body.firstChild);
  document.body.appendChild(bar("bottom"));
})();
'''


def nav_js():
    """The shared script every issue page loads. It embeds the issue list, so only this
    one file changes when an issue is added."""
    data = {k: sorted(sl) for k, _, sl in _present()}
    labels = {k: v["label"] for k, v in SERIES.items()}
    folders = {v["url_path"].strip("/"): k for k, v in SERIES.items() if v["url_path"]}
    return (NAV_TEMPLATE.replace("__ISSUES__", json.dumps(data))
            .replace("__LABELS__", json.dumps(labels))
            .replace("__FOLDERS__", json.dumps(folders))
            .replace("__GOATCOUNTER__", json.dumps(
                {"endpoint": GOATCOUNTER, "script": GOATCOUNTER_JS} if GOATCOUNTER else None)))


def not_found_html():
    """GitHub Pages serves docs/404.html for any address that doesn't exist. Monthly issues used
    to sit next to index.html, so send old links to their new home instead of a dead end."""
    folders = "|".join(v["url_path"].strip("/") for v in SERIES.values() if v["url_path"])
    first = next(v["url_path"] for v in SERIES.values() if v["url_path"])
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Page not found - SIH newsletter archive</title>
<style>
body{{margin:0;color:#414141;font:16px/1.5 Arial,sans-serif;border-top:.6rem solid #E64626}}
main{{max-width:36rem;margin:3rem auto;padding:0 1.25rem}}
h1{{color:#000;font-size:1.75rem;margin:0 0 .75rem}}
a{{color:#1A345E}}
</style>
</head>
<body>
<main>
<h1>Page not found</h1>
<p>There is no page at this address. Issues of the monthly newsletter now live in the
<code>{first}</code> folder; if you followed an older link you will be taken there.</p>
<p><a href="{SITE_BASE}">Browse all issues</a></p>
</main>
<script>
(function () {{
  // an old address such as /2024-08.htm  ->  /{first}2024-08.htm
  var m = location.pathname.match(/^(.*\\/)(\\d{{4}}-\\d{{2}})(?:\\.html?)?$/);
  if (m && !/\\/({folders})\\/$/.test(m[1])) {{
    location.replace(m[1] + "{first}" + m[2] + ".htm" + location.search + location.hash);
  }}
}})();
</script>
</body>
</html>
"""


def nav_src_for(cfg):
    """Path from an issue page to nav.js (training pages sit one folder deeper)."""
    return ("../" if cfg["url_path"] else "") + "nav.js"


def add_nav_hook(html, src):
    """Insert (or refresh) the navigation hook just before </body>. Idempotent."""
    html = re.sub(re.escape(NAV_START) + r".*?" + re.escape(NAV_END) + r"\s*", "", html,
                  flags=re.S)
    home = ("../" if src.startswith("../") else "") + "index.html"
    hook = (f'{NAV_START}<noscript><p style="margin:1em;font:14px Arial,sans-serif">'
            f'<a href="{home}">All issues</a></p></noscript>'
            f'<script src="{src}" defer></script>{NAV_END}')
    i = html.lower().rfind("</body>")
    if i < 0:
        return html.rstrip() + "\n" + hook + "\n"
    return html[:i] + hook + "\n" + html[i:]


# ------------------------------------------------------------------- search
SEARCH_DIR = DOCS_DIR / "search"
BOILERPLATE_SHARE = 0.9   # a text block present in this share of a series' issues is boilerplate


class _TextBlocks(HTMLParser):
    """Collect the visible text of a page as a list of blocks (paragraphs, table cells...)."""
    SKIP = {"script", "style", "head", "noscript", "template", "svg", "title"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "tbody", "thead",
             "tfoot", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
             "footer", "blockquote", "hr", "center", "pre", "dd", "dt", "dl"}
    VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base", "col", "embed",
            "source", "track", "wbr", "param"}
    HIDDEN = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all", re.I)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.cur, self.stack, self.skip = [], [], [], 0

    def _flush(self):
        text = " ".join("".join(self.cur).split())
        if text:
            self.blocks.append(text)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hidden = (tag in self.SKIP or bool(self.HIDDEN.search(a.get("style") or ""))
                  or "hidden" in a)
        if tag in self.VOID:
            if tag in self.BLOCK:
                self._flush()
            return
        self.stack.append((tag, hidden))
        self.skip += hidden
        if tag in self.BLOCK:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):      # tolerate unclosed tags
            if self.stack[i][0] == tag:
                self.skip -= sum(1 for _, h in self.stack[i:] if h)
                del self.stack[i:]
                break
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.cur.append(data)

    def close(self):
        super().close()
        self._flush()


def page_blocks(html):
    parser = _TextBlocks()
    parser.feed(html)
    parser.close()
    return parser.blocks


def search_docs():
    """One searchable document per issue: its visible text minus boilerplate that appears
    in nearly every issue of its series (footers, 'Contact us', 'Keep in touch'...)."""
    import calendar
    docs = []
    for key, cfg, slugs in _present():
        pages = {sl: page_blocks(Path(cfg["dir"], f"{sl}.htm").read_bytes().decode("utf-8", "replace"))
                 for sl in slugs}
        n = len(pages)
        counts = collections.Counter(b for blocks in pages.values() for b in set(blocks))
        common = {b for b, c in counts.items() if n >= 8 and c / n >= BOILERPLATE_SHARE}
        for sl, blocks in pages.items():
            year, month = sl.split("-")
            docs.append({"id": f"{cfg['url_path']}{sl}", "series": key, "date": sl,
                         "title": f"{cfg['title']}, {calendar.month_name[int(month)]} {year}",
                         "url": f"{cfg['url_path']}{sl}.htm",
                         "text": " ".join(b for b in blocks if b not in common)})
    return docs


def search_data_js():
    """The data file, one issue per line so a new issue is a one-line change in git."""
    rows = ",\n".join(json.dumps(d, separators=(",", ":")) for d in search_docs())
    return ("/* Search data for the archive index. Generated by archive_newsletters.py "
            "(rebuilt by --update-indexes). Do not edit by hand. */\n"
            f"window.SIH_SEARCH = [\n{rows}\n];\n")


SEARCH_TEMPLATE = r"""/* Search for the archive index page. Generated by archive_newsletters.py
   (rebuilt by --update-indexes). Uses MiniSearch (MIT; see LICENSE-minisearch.txt).
   Do not edit by hand. */
(function () {
  "use strict";
  var LABELS = __LABELS__;
  var box = document.getElementById("search");
  var form = document.getElementById("search-form");
  if (!box || !form || !window.Promise) return;          // no search box, or a very old browser
  var input = document.getElementById("q");
  var pub = document.getElementById("pub");
  var status = document.getElementById("search-status");
  var out = document.getElementById("results");
  var browse = document.getElementById("browse");
  box.hidden = false;                                     // the box only appears when JS runs

  var STOP = new Set(["a", "an", "and", "at", "by", "for", "in", "is", "of", "on", "or", "the", "to", "with"]);
  function norm(t) {
    t = t.toLowerCase();
    if (t.normalize) t = t.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    return STOP.has(t) ? null : t;
  }
  // Two passes. Exact matches come first: every word must be present and the last word may be
  // unfinished (search as you type). "Similar matches" then follow, and are deliberately loose
  // so people reach the right issue after only a few keystrokes: partial words anywhere
  // ("tow hal" finds "town hall") and typos from three letters up (one slip, more for longer
  // words). The cost is some irrelevant results lower down, which is the intended trade.
  function lastTermPrefix(term, i, terms) { return i === terms.length - 1; }
  function anyTermPrefix(term) { return term.length >= 2; }
  function fuzz(term) { return term.length < 3 ? false : Math.min(3, Math.ceil(term.length * 0.25)); }

  var ms = null, byId = {}, ready = null, failed = false;
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = function () { reject(new Error("could not load " + src)); };
      document.head.appendChild(s);
    });
  }
  function load() {
    if (!ready) {
      ready = loadScript("search/minisearch.min.js").then(function () {
        return loadScript("search/data.js");
      }).then(function () {
        ms = new MiniSearch({
          fields: ["title", "text"], storeFields: ["title", "url", "series", "date"], idField: "id",
          processTerm: norm,
          searchOptions: { boost: { title: 3 }, combineWith: "AND", processTerm: norm }
        });
        window.SIH_SEARCH.forEach(function (d) { byId[d.id] = d; });
        ms.addAll(window.SIH_SEARCH);
      }).catch(function (e) { failed = true; throw e; });
    }
    return ready;
  }

  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  // A short passage around the first match, with the matched words wrapped in <mark>.
  function snippet(text, terms) {
    var re = terms.length ? new RegExp("(" + terms.map(esc).join("|") + ")", "gi") : null;
    var hit = re ? re.exec(text) : null;
    var start = hit ? Math.max(0, hit.index - 70) : 0;
    var end = Math.min(text.length, start + 230);
    if (start > 0) { var a = text.indexOf(" ", start); if (a > -1 && a < start + 25) start = a + 1; }
    if (end < text.length) { var b = text.lastIndexOf(" ", end); if (b > start + 100) end = b; }
    var piece = text.slice(start, end);
    var p = document.createElement("p");
    p.className = "snip";
    if (start > 0) p.appendChild(document.createTextNode("\u2026 "));
    if (re) {
      piece.split(new RegExp("(" + terms.map(esc).join("|") + ")", "gi")).forEach(function (part, i) {
        if (i % 2) { var m = document.createElement("mark"); m.textContent = part; p.appendChild(m); }
        else if (part) p.appendChild(document.createTextNode(part));
      });
    } else p.appendChild(document.createTextNode(piece));
    if (end < text.length) p.appendChild(document.createTextNode(" \u2026"));
    return { node: p, word: hit ? hit[0] : null };
  }

  function card(r) {
    var d = byId[r.id];
    var sn = snippet(d.text, r.terms || []);
    var li = document.createElement("li");
    var h = document.createElement("h3");
    var a = document.createElement("a");
    // "#:~:text=" makes supporting browsers scroll to and highlight the match in the issue
    a.href = d.url + (sn.word ? "#:~:text=" + encodeURIComponent(sn.word).replace(/-/g, "%2D") : "");
    a.textContent = d.title;
    h.appendChild(a);
    var meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = LABELS[d.series];
    li.appendChild(h); li.appendChild(meta); li.appendChild(sn.node);
    return li;
  }

  function sortHits(list) {
    return list.sort(function (x, y) { return y.score - x.score || (y.date < x.date ? -1 : 1); });
  }
  function section(title, list) {
    var wrap = document.createElement("div");
    if (title) { var h = document.createElement("h2"); h.textContent = title; wrap.appendChild(h); }
    var ul = document.createElement("ul");
    ul.className = "hits";
    list.slice(0, 60).forEach(function (r) { ul.appendChild(card(r)); });
    wrap.appendChild(ul);
    if (list.length > 60) {
      var more = document.createElement("p");
      more.className = "meta";
      more.textContent = "Showing the first 60 of " + list.length + ". Add a word to narrow it down.";
      wrap.appendChild(more);
    }
    return wrap;
  }

  function render(q) {
    var which = pub.value;
    var filter = function (r) { return which === "all" || r.series === which; };
    var exact = sortHits(ms.search(q, { prefix: lastTermPrefix, fuzzy: false, filter: filter }));
    var seen = {};
    exact.forEach(function (r) { seen[r.id] = true; });
    var similar = sortHits(ms.search(q, { prefix: anyTermPrefix, fuzzy: fuzz, filter: filter })
      .filter(function (r) { return !seen[r.id]; }));
    out.textContent = "";
    var word = function (n) { return n + (n === 1 ? " issue" : " issues"); };
    if (!exact.length && !similar.length) {
      settled(q, 0);
      status.textContent = "No issues match \u201c" + q + "\u201d. Try fewer or different words.";
      return;
    }
    settled(q, exact.length + similar.length);
    status.textContent = (exact.length ? word(exact.length) + " match \u201c" + q + "\u201d" : "No exact matches for \u201c" + q + "\u201d") +
      (similar.length ? (exact.length ? ", plus " : "; ") + similar.length + " similar" : "") + ".";
    if (exact.length) out.appendChild(section(null, exact));
    if (similar.length) out.appendChild(section("Similar matches", similar));
  }

  function setUrl(q) {
    try {
      var params = [];
      if (q) params.push("q=" + encodeURIComponent(q));
      if (q && pub.value !== "all") params.push("in=" + pub.value);
      history.replaceState(null, "", location.pathname + (params.length ? "?" + params.join("&") : ""));
    } catch (e) { /* file:// pages can't change their address; harmless */ }
  }

  // ---- what people look for, sent to GoatCounter as events (see README) ----------------------
  // Typing doesn't reload the page, so it would never be counted as a page view. An event is sent
  // once the person pauses, so "n", "ne", "nex" don't each count. The event name starts with this
  // site's folder (newsletter/search?q=...), so it can't be confused with another site's events.
  var TRACK = __TRACK__;
  var dir = location.pathname.replace(/[^\/]*$/, "");          // e.g. /newsletter/
  var sent = {}, trackTimer = null, fromLink = false;
  function track(name, q, title, extra) {
    var gc = window.goatcounter;
    if (!TRACK || !gc || typeof gc.count !== "function") return;   // blocked or not loaded yet
    q = q.slice(0, 200);
    var qs = "?q=" + encodeURIComponent(q) + (pub.value !== "all" ? "&in=" + pub.value : "") + (extra || "");
    gc.count({ event: true, path: dir + name + qs, title: title });
  }
  function settled(q, total) {
    clearTimeout(trackTimer);
    trackTimer = setTimeout(function () {
      var key = q + "|" + pub.value + "|" + (total ? "found" : "none");
      if (sent[key]) return;
      // a search that arrived as a ?q= link is already in the page-view path; don't count it twice,
      // but do record it if it found nothing
      if (total === 0) track("search-none", q, q + " (no results)");
      else if (!fromLink) track("search", q, q + " (" + total + (total === 1 ? " issue)" : " issues)"));
      sent[key] = true;
    }, 1500);
  }
  out.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a") : null;
    if (!a) return;
    track("search-open", input.value.trim(), "Opened " + a.textContent + " from search",
          "&to=" + encodeURIComponent(a.getAttribute("href").split("#")[0]));
  });

  var timer = null;
  function run() {
    var q = input.value.trim();
    clearTimeout(trackTimer);
    setUrl(q);
    if (!q) {
      out.textContent = ""; status.textContent = "";
      browse.hidden = false;
      return;
    }
    browse.hidden = true;
    if (!ms && !failed) status.textContent = "Loading search\u2026";
    load().then(function () {
      if (input.value.trim() === q) render(q);
    }, function () {
      browse.hidden = false;
      status.textContent = "Search could not be loaded. You can still browse the lists below.";
    });
  }

  input.addEventListener("focus", function () { load().catch(function () {}); }, { once: true });
  input.addEventListener("input", function () { fromLink = false; clearTimeout(timer); timer = setTimeout(run, 120); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && input.value) { input.value = ""; run(); }
  });
  pub.addEventListener("change", function () { fromLink = false; run(); });
  form.addEventListener("submit", function (e) { e.preventDefault(); clearTimeout(timer); run(); });

  var params = new URLSearchParams(location.search);
  if (params.get("in") && /^(newsletter|training)$/.test(params.get("in"))) pub.value = params.get("in");
  if (params.get("q")) { input.value = params.get("q"); fromLink = true; run(); }
})();
"""


def search_js():
    labels = {k: v["label"] for k, v in SERIES.items()}
    return (SEARCH_TEMPLATE.replace("__LABELS__", json.dumps(labels))
            .replace("__TRACK__", "true" if (GOATCOUNTER and TRACK_SEARCH) else "false"))


# --------------------------------------------------- Outlook web-app exports
SCRIPT_TAG = re.compile(r"<script\b.*?</script>", re.S | re.I)
MARKJS = re.compile(r"<span\b[^>]*\bdata-markjs=[^>]*>(.*?)</span>", re.S | re.I)
FOOTER_NAMES = ("subscribe", "manage_prefs", "unsubscribe")


def looks_like_outlook_export(html):
    return "olm-fragment-custom" in html or "ms-outlook-html-content-root" in html


def extract_outlook_body(html):
    """Return the <div class="olm-fragment-custom"> element (the email itself)."""
    h = SCRIPT_TAG.sub("", html)
    m0 = re.search(r"<div\b[^>]*\bclass=([\"'])[^\"']*\bolm-fragment-custom\b[^\"']*\1[^>]*>",
                   h, re.I)
    if not m0:
        raise ValueError("could not find the email body in this Outlook export")
    start = m0.start()
    depth = 0
    for m in re.finditer(r"<(/?)div\b[^>]*>", h[start:], re.I):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return h[start:start + m.end()]
    raise ValueError("unbalanced <div> tags in Outlook export")


def clean_outlook_artifacts(frag):
    prev = None
    while prev != frag:                                  # search highlights (can nest)
        prev, frag = frag, MARKJS.sub(r"\1", frag)
    frag = re.sub(r"\sdata-outlook-id=([\"'])[^\"']*\1", "", frag)
    frag = re.sub(r"\stabindex=([\"'])[^\"']*\1", "", frag)
    frag = re.sub(r"\sclass=([\"'])outlook-search-highlight\1", "", frag)
    return frag


def drop_tooltip_urls(html):
    """Remove title="https://..." tooltips on links (stale wrapper URLs)."""
    def fix(m):
        return re.sub(r"\stitle=([\"'])https?://[^\"']*\1", "", m.group(0))
    return re.sub(r"<a\b[^>]*>", fix, html, flags=re.I)


PERSONAL_TEXT = re.compile(
    r"^\s*(unsubscribe|opt[- ]?out|(update|manage|change|edit)\s+(your\s+)?"
    r"(email\s+)?(preferences|profile|details|subscription)s?)\W*$", re.I)


def neutralise_footer_links(html, subscribe_url=None):
    """Point subscribe / update-preferences / unsubscribe links at a generic URL.
    Matches Emma's data-name attribute or the visible link text, so it also
    works for emails built on other templates."""
    import html as _html
    subscribe_url = subscribe_url or SUBSCRIBE_URL

    def fix(m):
        attrs, inner = m.group(1), m.group(2)
        n = re.search(r"\bdata-name=([\"'])([^\"']*)\1", attrs)
        text = _html.unescape(re.sub(r"<[^>]+>", "", inner))
        if (n and n.group(2) in FOOTER_NAMES) or PERSONAL_TEXT.match(re.sub(r"\s+", " ", text)):
            attrs = HREF_ATTR.sub(
                lambda x: f"{x.group(1)}{x.group(2)}{subscribe_url}{x.group(2)}",
                attrs, count=1)
            return f"<a{attrs}>{inner}</a>"
        return m.group(0)

    return ANCHOR.sub(fix, html)


TOC_LINK = re.compile(r"""href=(["'])https://t\.e2ma\.net/message/\w+/\w+#([\w-]+)\1""", re.I)


def localise_toc_links(html):
    """Contents links point at '<web-view address>#section'. Make them in-page jumps
    (only when the page really has that section)."""
    def fix(m):
        frag = m.group(2)
        if re.search(r"""(?:id|name)=["']%s["']""" % re.escape(frag), html):
            return f"href={m.group(1)}#{frag}{m.group(1)}"
        return m.group(0)
    return TOC_LINK.sub(fix, html)


def unlink_webview(html):
    """'View the HTML version of this email' points at an expiring page that also
    carries the recipient's member hash. Keep the words, drop the link."""
    import html as _html

    def fix(m):
        text = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))).strip()
        named = re.search(r"\bdata-name=([\"'])HTML version of this email\1", m.group(1), re.I)
        if named or text.lower() == "html version of this email":
            return m.group(2)
        return m.group(0)

    return ANCHOR.sub(fix, html)


def wrap_page(fragment, slug, prefix=None):
    year, month = slug.split("-")
    import calendar
    title = f"{prefix or SERIES['newsletter']['title']}, {calendar.month_name[int(month)]} {year}"
    return ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<meta name=\"robots\" content=\"noindex, nofollow\">\n"
            f"<title>{title}</title>\n</head>\n<body style=\"margin:0\">\n"
            f"{fragment}\n</body>\n</html>\n")


# ------------------------------------------------------------ local files
def read_local(path):
    """Read a saved .html/.htm file, or the text/html part of an .eml file."""
    path = Path(path)
    if path.suffix.lower() == ".eml":
        import email
        from email import policy
        msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        part = msg.get_body(preferencelist=("html",))
        if part is None:
            raise ValueError(f"no text/html part in {path}")
        return part.get_content()
    return path.read_text(encoding="utf-8", errors="replace")


# ------------------------------------------------------ clean existing pages
CHANGE_RULES = [
    ("unsubscribe link", r"t\.e2ma\.net/optout/"),
    ("update-preferences link", r"audience/signup/\d+/\d+/\d+/\d+"),
    ("'saved from url' comment", r"<!--\s*saved from url="),
    ("web-view link", r"href=[\"']https://t\.e2ma\.net/message/"),
    ("Mimecast-wrapped link", r"mimecast(?:protect)?\.com/s/"),
    ("tracking pixel", r"t\.e2ma\.net/track/"),
    ("search highlight", r"data-markjs"),
    ("click redirect", r"t\.e2ma\.net/click/"),
    ("Content-Security-Policy tag", r"http-equiv\s*=\s*[\"']?content-security-policy"),
    ("navigation script", r"sih-nav:start"),
    ("remote image", r"""<img\b[^>]*?\bsrc=["']https?://"""),
]


def sanitise_existing(only=None, dry_run=False, resolve=False, cache=None, cache_path=None,
                      allow_index=False, localise=False):
    """Re-apply the cleaning rules to pages that are already in the archive.
    Files are rewritten byte-for-byte except for the cleaned parts."""
    cache = cache if cache is not None else {}
    changed = total = 0
    for key, cfg in SERIES.items():
        for f in sorted(Path(cfg["dir"]).glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].htm")):
            if only and f.stem != only:
                continue
            total += 1
            before = f.read_bytes().decode("utf-8", "surrogateescape")
            after = clean_outlook_artifacts(before)
            after = sanitise(after, False, cfg["subscribe"], nav_src_for(cfg))
            if resolve:
                SUBSCRIBE_FALLBACK[0] = cfg["subscribe"]
                after = resolve_links(after, cache, cache_path, f"{key}/{f.stem}")
                after = drop_tooltip_urls(after)
            if localise and not dry_run:
                after = localise_images(after, f.stem, f.parent)
            if after == before:
                continue
            changed += 1
            parts = []
            for label, pat in CHANGE_RULES:
                n0, n1 = len(re.findall(pat, before, re.I)), len(re.findall(pat, after, re.I))
                if n0 != n1:
                    parts.append(f"{label} {n0}->{n1}")
            print(f"{'would change' if dry_run else 'cleaned'} {f}: " + (", ".join(parts) or "minor"))
            if not dry_run:
                f.write_bytes(after.encode("utf-8", "surrogateescape"))
    print(f"{changed} of {total} page(s) {'would change' if dry_run else 'changed'}")


# --------------------------------------------------------------------- main
def main():
    global DELAY
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--series", choices=sorted(SERIES), default="newsletter",
                    help="which publication these issues belong to (own output folder "
                         "and page title); default: newsletter")
    ap.add_argument("--subscribe-url", help="URL that replaces personal unsubscribe/"
                    "preferences links (default depends on --series)")
    ap.add_argument("--out", help="output folder (overrides the series default)")
    ap.add_argument("--only", help="process a single YYYY-MM issue")
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument("--from-file", action="append", default=[], metavar="YYYY-MM=PATH",
                    help="use a saved .html or .eml file instead of downloading "
                         "(repeatable), e.g. --from-file 2025-03=saved/mar25.eml")
    ap.add_argument("--from-dir", metavar="DIR",
                    help="process every .html/.htm/.eml in DIR, inferring YYYY-MM from "
                         "the filename (e.g. SIH_Newsletter__August_2024.html). "
                         "Skips the built-in URL list.")
    ap.add_argument("--resolve-links", action="store_true")
    ap.add_argument("--localise-images", action="store_true")
    ap.add_argument("--allow-index", action="store_true",
                    help="remove noindex/nofollow so search engines may index pages")
    ap.add_argument("--update-indexes", "--readme-only", dest="update_indexes",
                    action="store_true",
                    help="only rebuild the README list, docs/index.html, nav.js, 404.html and search/ from the folders "
                         "on disk; no downloads (this also happens after every normal run)")
    ap.add_argument("--sanitise-existing", action="store_true",
                    help="re-apply the cleaning rules to pages already in the archive "
                         "(both publications), rewriting them in place; combine with "
                         "--resolve-links to also resolve their tracking links")
    ap.add_argument("--check-images", action="store_true",
                    help="audit every archived page for images that are not stored locally "
                         "(remote, blocked/unusable, missing); exits 1 if any are found")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --sanitise-existing: show what would change, write nothing")
    ap.add_argument("--delay", type=float, default=DELAY,
                    help="seconds between network requests (default %(default)s)")
    ap.add_argument("--write-readme", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    DELAY = args.delay
    if args.update_indexes:
        update_indexes(args.allow_index)
        return

    if args.check_images:
        sys.exit(1 if check_images() else 0)

    if args.sanitise_existing:
        cache_path = Path("link_cache.json")
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        sanitise_existing(args.only, args.dry_run, args.resolve_links, cache, cache_path,
                          localise=args.localise_images)
        if UNRESOLVED:
            Path("unresolved_links.tsv").write_text(
                "issue\tlink text\turl\n" + "\n".join("\t".join(r) for r in UNRESOLVED) + "\n")
            print(f"{len(UNRESOLVED)} link(s) need attention; see unresolved_links.tsv")
        return

    cfg = SERIES[args.series]
    out_dir = Path(args.out or cfg["dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    subscribe_url = args.subscribe_url or cfg["subscribe"]
    SUBSCRIBE_FALLBACK[0] = subscribe_url
    cache_path = Path("link_cache.json")
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    sources = {k: ("url", v) for k, v in ISSUES.items()} \
        if args.series == "newsletter" and not args.from_dir else {}
    if args.from_dir:
        import calendar
        months = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
        pat = re.compile(r"(" + "|".join(months) + r")\D*?(\d{4})", re.I)
        for f in sorted(Path(args.from_dir).iterdir()):
            if f.suffix.lower() not in (".html", ".htm", ".eml"):
                continue
            m = pat.search(f.stem)
            if not m:
                print(f"skipping {f.name}: no 'Month YYYY' in filename", file=sys.stderr)
                continue
            slug = f"{m.group(2)}-{months[m.group(1).lower()]:02d}"
            if slug in sources:
                sys.exit(f"two files map to {slug}: {sources[slug][1]} and {f}")
            sources[slug] = ("file", str(f))
    for item in args.from_file:
        slug, _, path = item.partition("=")
        if not re.fullmatch(r"\d{4}-\d{2}", slug) or not path:
            sys.exit(f"--from-file expects YYYY-MM=PATH, got: {item}")
        sources[slug] = ("file", path)

    written = 0
    for slug, (kind, src) in sorted(sources.items()):
        if args.only and slug != args.only:
            continue
        target = out_dir / f"{slug}.htm"
        if target.exists() and not args.force:
            print(f"{slug}: exists, skipping")
            continue
        print(f"{slug}: {'downloading' if kind == 'url' else 'reading'} {src}")
        try:
            html = fetch_text(src) if kind == "url" else read_local(src)
            if kind == "file" and looks_like_outlook_export(html):
                size = len(html) / 1e6
                html = wrap_page(extract_outlook_body(html), slug, cfg["title"])
                print(f"    Outlook export: extracted email body ({size:.1f} MB -> "
                      f"{len(html) / 1e3:.0f} KB)")
        except Exception as e:
            print(f"    ! failed: {e}", file=sys.stderr)
            continue
        if kind == "file":
            html = clean_outlook_artifacts(html)
        html = sanitise(html, args.allow_index, subscribe_url, nav_src_for(cfg))
        if args.resolve_links:
            html = resolve_links(html, cache, cache_path, slug)
        html = drop_tooltip_urls(html)
        if args.localise_images:
            html = localise_images(html, slug, out_dir)
        target.write_text(html, encoding="utf-8")
        written += 1
        left = [x for x in image_problems(html, out_dir) if x[0] not in HARMLESS]
        if left:
            cats = {}
            for c, _, _ in left:
                cats[c] = cats.get(c, 0) + 1
            print("    ! images not stored locally: " + ", ".join(f"{n} {c}" for c, n in cats.items()),
                  file=sys.stderr)
        if kind == "url":
            time.sleep(DELAY)

    if UNRESOLVED:
        print(f"\n{len(UNRESOLVED)} link(s) need attention: unresolved links still point "
              "at a wrapper URL; [PERSONAL LINK REMOVED] ones were replaced with the "
              "subscribe/neutral link. See unresolved_links.tsv", file=sys.stderr)
        Path("unresolved_links.tsv").write_text(
            "issue\tlink text\turl\n" + "\n".join("\t".join(r) for r in UNRESOLVED) + "\n")

    if written:
        update_indexes(args.allow_index)


if __name__ == "__main__":
    main()
