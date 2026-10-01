#!/usr/bin/env python3
"""
Download Emma (e2ma) "webview" newsletters into the SIH newsletter archive.

Run from the root of your clone of Sydney-Informatics-Hub/newsletter:

    python archive_newsletters.py                      # download + sanitise only
    python archive_newsletters.py --resolve-links --localise-images   # recommended
    python archive_newsletters.py --only 2026-10       # one issue
    python archive_newsletters.py --from-dir DIR --series training ...   # training updates
    python archive_newsletters.py --update-indexes     # rebuild README list + docs/index.html
    python archive_newsletters.py --sanitise-existing --dry-run   # preview cleaning old pages

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
4. (--localise-images) Downloads images into docs/img/<YYYY-MM>/ so the
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
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
SERIES = {
    "newsletter": {"dir": "docs", "title": "SIH Newsletter", "heading": "Monthly Newsletter",
                   "label": "Monthly newsletter",
                   "about": "News, scheme opportunities and training from the Sydney Informatics Hub.",
                   "subscribe": SUBSCRIBE_URL, "url_path": ""},
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
TRACK_PIXEL = re.compile(r"<img\b[^>]*\bt\.e2ma\.net/track/[^>]*>", re.I)
OPTOUT = re.compile(r"https://t\.e2ma\.net/optout/[^\"'\s>]*", re.I)
# update-preferences link: signup/<audience>/<form>/<list>/<member>/?s=<token>
UPDATE = re.compile(
    r"https://app\.e2ma\.net/app2/audience/signup/\d+/\d+/\d+/\d+/[^\"'\s>]*", re.I)
ROBOTS = re.compile(r"<meta[^>]+name=[\"']robots[\"'][^>]*>", re.I)


def sanitise(html, allow_index=False, subscribe_url=None):
    subscribe_url = subscribe_url or SUBSCRIBE_URL
    html = SAVED_FROM.sub("", html)
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
EXT_BY_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
               "image/webp": ".webp", "image/svg+xml": ".svg"}


def localise_images(html, slug, out_dir):
    img_dir = out_dir / "img" / slug
    seen = {}

    def swap(m):
        url = m.group(3)
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
                seen[url] = url
        return f"{m.group(1)}{m.group(2)}{seen[url]}{m.group(2)}"

    return IMG_SRC.sub(swap, html)


# ------------------------------------------------------- README + index page
REPO_URL = "https://github.com/Sydney-Informatics-Hub/newsletter"
README_START = "<!-- ARCHIVE-LIST:START (generated by archive_newsletters.py; do not edit) -->"
README_END = "<!-- ARCHIVE-LIST:END -->"


def _slugs(folder):
    return sorted((p.stem for p in Path(folder).glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].htm")),
                  reverse=True)


def _present():
    found = [(k, v, _slugs(v["dir"])) for k, v in SERIES.items()]
    return [x for x in found if x[2]]


def readme_list():
    """Markdown list of every archived issue (newest first)."""
    lines = []
    for key, cfg, slugs in _present():
        lines += [f"### {cfg['heading']}", ""]
        year = None
        for sl in slugs:
            if sl[:4] != year:
                year = sl[:4]
                lines += [f"#### {year}", ""]
            lines += [f"[{sl}]({SITE_BASE}{cfg['url_path']}{sl}.htm)", ""]
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
footer{margin-top:3rem;padding:1.25rem 0 2.5rem;border-top:1px solid var(--rule);font-size:.9rem}
footer p{margin:.25rem 0}
footer a{color:var(--charcoal)}
@media (max-width:42rem){.year{grid-template-columns:1fr}.months{grid-template-columns:repeat(6,minmax(0,1fr))}}
"""


def index_html(allow_index=False):
    import calendar
    from html import escape
    present = _present()
    total = sum(len(sl) for _, _, sl in present)
    all_slugs = sorted(x for _, _, sl in present for x in sl)
    span = f"{all_slugs[0][:4]} to {all_slugs[-1][:4]}" if all_slugs else ""

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

    robots = "" if allow_index else '<meta name="robots" content="noindex, nofollow">\n'
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{robots}<title>SIH newsletter archive</title>
<meta name="description" content="Saved copies of Sydney Informatics Hub newsletters and training updates.">
<style>{INDEX_CSS}</style>
</head>
<body>
<header><div class="wrap">
<p class="org">Sydney Informatics Hub</p>
<h1>Newsletter archive</h1>
<p class="intro">{total} past issues, {span}, saved as web pages.</p>
<ul class="latest" aria-label="Latest issues">{latest}</ul>
</div></header>
<main class="wrap">
{"".join(sections)}
</main>
<footer><div class="wrap">
<p>These are saved copies of emails sent to subscribers. Links in older issues may no longer work.</p>
<p>Gaps in the grid are months with no issue. <a href="{REPO_URL}">How to add issues (GitHub)</a></p>
</div></footer>
</body>
</html>
"""


def update_indexes(allow_index=False):
    update_readme()
    Path(SERIES["newsletter"]["dir"], "index.html").write_text(index_html(allow_index),
                                                               encoding="utf-8")
    print("README.md list and docs/index.html rebuilt")


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
]


def sanitise_existing(only=None, dry_run=False, resolve=False, cache=None, cache_path=None,
                      allow_index=False):
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
            after = sanitise(after, False, cfg["subscribe"])
            if resolve:
                SUBSCRIBE_FALLBACK[0] = cfg["subscribe"]
                after = resolve_links(after, cache, cache_path, f"{key}/{f.stem}")
                after = drop_tooltip_urls(after)
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
                    help="only rebuild the README list and docs/index.html from the folders "
                         "on disk; no downloads (this also happens after every normal run)")
    ap.add_argument("--sanitise-existing", action="store_true",
                    help="re-apply the cleaning rules to pages already in the archive "
                         "(both publications), rewriting them in place; combine with "
                         "--resolve-links to also resolve their tracking links")
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

    if args.sanitise_existing:
        cache_path = Path("link_cache.json")
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        sanitise_existing(args.only, args.dry_run, args.resolve_links, cache, cache_path)
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
        html = sanitise(html, args.allow_index, subscribe_url)
        if args.resolve_links:
            html = resolve_links(html, cache, cache_path, slug)
        html = drop_tooltip_urls(html)
        if args.localise_images:
            html = localise_images(html, slug, out_dir)
        target.write_text(html, encoding="utf-8")
        written += 1
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
