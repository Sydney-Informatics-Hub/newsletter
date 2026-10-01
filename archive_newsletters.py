#!/usr/bin/env python3
"""
Download Emma (e2ma) "webview" newsletters into the SIH newsletter archive.

Run from the root of your clone of Sydney-Informatics-Hub/newsletter:

    python archive_newsletters.py                      # download + sanitise only
    python archive_newsletters.py --resolve-links --localise-images   # recommended
    python archive_newsletters.py --only 2026-10       # one issue
    python archive_newsletters.py --from-dir DIR --series training ...   # training updates
    python archive_newsletters.py --readme-only        # regenerate README list

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
                   "subscribe": SUBSCRIBE_URL, "url_path": ""},
    # subscribe "#" = no link; set the real sign-up page with --subscribe-url
    "training": {"dir": "docs/training", "title": "SIH Training Update",
                 "heading": "Training Update", "subscribe": "#", "url_path": "training/"},
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
TRACK_PIXEL = re.compile(r"<img\b[^>]*\bt\.e2ma\.net/track/[^>]*>", re.I)
OPTOUT = re.compile(r"https://t\.e2ma\.net/optout/[^\"'\s>]*", re.I)
# update-preferences link: signup/<audience>/<form>/<list>/<member>/?s=<token>
UPDATE = re.compile(
    r"https://app\.e2ma\.net/app2/audience/signup/\d+/\d+/\d+/\d+/[^\"'\s>]*", re.I)
ROBOTS = re.compile(r"<meta[^>]+name=[\"']robots[\"'][^>]*>", re.I)


def sanitise(html, allow_index=False, subscribe_url=None):
    subscribe_url = subscribe_url or SUBSCRIBE_URL
    html = TRACK_PIXEL.sub("", html)
    html = neutralise_footer_links(html, subscribe_url)
    html = unlink_webview(html)
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
    r"(^|\.)(e2ma\.net|mimecastprotect\.com|safelinks\.protection\.outlook\.com|"
    r"myemma\.com)$", re.I)
WRAPPED_URL = re.compile(
    r"^https://(t\.e2ma\.net/click/|url\.[a-z0-9.]*mimecastprotect\.com/s/|"
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


# ------------------------------------------------------------------- README
def _slugs(folder):
    return sorted((p.stem for p in Path(folder).glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].htm")),
                  reverse=True)


def readme_text():
    present = [(k, v, _slugs(v["dir"])) for k, v in SERIES.items()]
    present = [x for x in present if x[2]]
    lines = ["# SIH Newsletter Archive", "", "Archive of SIH Newsletters", ""]
    for key, cfg, slugs in present:
        level = "##"
        if len(present) > 1:
            lines += [f"## {cfg['heading']}", ""]
            level = "###"
        year = None
        for sl in slugs:
            if sl[:4] != year:
                year = sl[:4]
                lines += [f"{level} {year}", ""]
            lines += [f"[{sl}]({SITE_BASE}{cfg['url_path']}{sl}.htm)", ""]
    return "\n".join(lines)


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
    frag = MARKJS.sub(r"\1", frag)                       # search highlights
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


def unlink_webview(html):
    """'View the HTML version of this email' points at an expiring page."""
    return re.sub(r"<a\b[^>]*>(\s*HTML version of this email\s*)</a>", r"\1", html,
                  flags=re.I)


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
    ap.add_argument("--readme-only", action="store_true",
                    help="just rebuild README.md from the folders on disk; no downloads")
    ap.add_argument("--delay", type=float, default=DELAY,
                    help="seconds between network requests (default %(default)s)")
    ap.add_argument("--write-readme", action="store_true",
                    help="overwrite README.md with a list built from the docs folder")
    args = ap.parse_args()

    DELAY = args.delay
    if args.readme_only:
        Path("README.md").write_text(readme_text(), encoding="utf-8")
        print("README.md rewritten")
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
                html = wrap_page(clean_outlook_artifacts(extract_outlook_body(html)), slug,
                                 cfg["title"])
                print(f"    Outlook export: extracted email body ({size:.1f} MB -> "
                      f"{len(html) / 1e3:.0f} KB)")
        except Exception as e:
            print(f"    ! failed: {e}", file=sys.stderr)
            continue
        html = sanitise(html, args.allow_index, subscribe_url)
        if args.resolve_links:
            html = resolve_links(html, cache, cache_path, slug)
        html = drop_tooltip_urls(html)
        if args.localise_images:
            html = localise_images(html, slug, out_dir)
        target.write_text(html, encoding="utf-8")
        if kind == "url":
            time.sleep(DELAY)

    if UNRESOLVED:
        print(f"\n{len(UNRESOLVED)} link(s) need attention: unresolved links still point "
              "at a wrapper URL; [PERSONAL LINK REMOVED] ones were replaced with the "
              "subscribe/neutral link. See unresolved_links.tsv", file=sys.stderr)
        Path("unresolved_links.tsv").write_text(
            "issue\tlink text\turl\n" + "\n".join("\t".join(r) for r in UNRESOLVED) + "\n")

    if args.write_readme:
        Path("README.md").write_text(readme_text(), encoding="utf-8")
        print("README.md rewritten")


if __name__ == "__main__":
    main()
