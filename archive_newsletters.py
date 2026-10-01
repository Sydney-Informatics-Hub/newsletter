#!/usr/bin/env python3
"""
Download Emma (e2ma) "webview" newsletters into the SIH newsletter archive.

Run from the root of your clone of Sydney-Informatics-Hub/newsletter:

    python archive_newsletters.py                      # download + sanitise only
    python archive_newsletters.py --resolve-links --localise-images   # recommended
    python archive_newsletters.py --only 2026-10       # one issue
    python archive_newsletters.py --write-readme       # regenerate README list

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
5. Leaves Emma's <meta name="robots" content="noindex, nofollow"> in place
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
    """Return the Location a click-tracking URL redirects to (or None)."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(_request(url), timeout=30)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return e.headers.get("Location")
    return None


# ----------------------------------------------------------------- cleaning
TRACK_PIXEL = re.compile(r"<img\b[^>]*\bt\.e2ma\.net/track/[^>]*>", re.I)
OPTOUT = re.compile(r"https://t\.e2ma\.net/optout/[^\"'\s>]*", re.I)
# update-preferences link: signup/<audience>/<form>/<list>/<member>/?s=<token>
UPDATE = re.compile(
    r"https://app\.e2ma\.net/app2/audience/signup/\d+/\d+/\d+/\d+/[^\"'\s>]*", re.I)
ROBOTS = re.compile(r"<meta[^>]+name=[\"']robots[\"'][^>]*>", re.I)


def sanitise(html, allow_index=False):
    html = TRACK_PIXEL.sub("", html)
    html = OPTOUT.sub(SUBSCRIBE_URL, html)
    html = UPDATE.sub(SUBSCRIBE_URL, html)
    if allow_index:
        html = ROBOTS.sub("", html)
    if not re.search(r"<meta[^>]+charset", html, re.I):
        html = re.sub(r"(<head[^>]*>)", r'\1<meta charset="utf-8">', html,
                      count=1, flags=re.I)
    return html


CLICK_HREF = re.compile(r"href=([\"'])(https://t\.e2ma\.net/click/[^\"']+)\1", re.I)


def resolve_links(html, cache, cache_path):
    urls = sorted({m.group(2) for m in CLICK_HREF.finditer(html)})
    for u in urls:
        if u not in cache:
            dest = resolve_redirect(u)
            if dest:
                cache[u] = dest
                cache_path.write_text(json.dumps(cache, indent=1))
            else:
                print(f"    ! could not resolve {u}", file=sys.stderr)
            time.sleep(DELAY)

    def swap(m):
        return f"href={m.group(1)}{cache.get(m.group(2), m.group(2))}{m.group(1)}"

    return CLICK_HREF.sub(swap, html)


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
def readme_text(out_dir):
    slugs = sorted((p.stem for p in out_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].htm")),
                   reverse=True)
    lines = ["# SIH Newsletter Archive", "", "Archive of SIH Newsletters", ""]
    year = None
    for s in slugs:
        if s[:4] != year:
            year = s[:4]
            lines += [f"## {year}", ""]
        lines += [f"[{s}]({SITE_BASE}{s}.htm)", ""]
    return "\n".join(lines)


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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="docs", help="output folder (default: docs)")
    ap.add_argument("--only", help="process a single YYYY-MM issue")
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument("--from-file", action="append", default=[], metavar="YYYY-MM=PATH",
                    help="use a saved .html or .eml file instead of downloading "
                         "(repeatable), e.g. --from-file 2025-03=saved/mar25.eml")
    ap.add_argument("--resolve-links", action="store_true")
    ap.add_argument("--localise-images", action="store_true")
    ap.add_argument("--allow-index", action="store_true",
                    help="remove noindex/nofollow so search engines may index pages")
    ap.add_argument("--write-readme", action="store_true",
                    help="overwrite README.md with a list built from the docs folder")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    cache_path = Path("link_cache.json")
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    sources = {k: ("url", v) for k, v in ISSUES.items()}
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
        except Exception as e:
            print(f"    ! failed: {e}", file=sys.stderr)
            continue
        html = sanitise(html, args.allow_index)
        if args.resolve_links:
            html = resolve_links(html, cache, cache_path)
        if args.localise_images:
            html = localise_images(html, slug, out_dir)
        target.write_text(html, encoding="utf-8")
        if kind == "url":
            time.sleep(DELAY)

    if args.write_readme:
        Path("README.md").write_text(readme_text(out_dir), encoding="utf-8")
        print("README.md rewritten")


if __name__ == "__main__":
    main()
