# SIH Newsletter Archive

Static copies of the Sydney Informatics Hub newsletters, published with GitHub Pages from the `docs/` folder.

**Browse the archive: <https://sydney-informatics-hub.github.io/newsletter/>**

There are two publications, each with its own folder so same-month issues don't collide:

| Publication | Folder | Page address |
| --- | --- | --- |
| Monthly newsletter | `docs/monthly/` | `.../newsletter/monthly/2026-10.htm` |
| Training update | `docs/training/` | `.../newsletter/training/2026-09.htm` |

Issues are named `YYYY-MM.htm`. Images are stored next to them in `img/`. `docs/index.html` (the front page), `docs/nav.js` (the previous/next bar on every issue) and `docs/404.html` (see below) are generated, so don't edit them by hand. `docs/assets/` holds the logo.

Monthly issues used to sit next to `index.html` (for example `.../newsletter/2024-08.htm`). `docs/404.html` sends any such old address to `.../newsletter/monthly/2024-08.htm`, so existing links keep working.

## Adding a new issue

Everything is done by one script, `archive_newsletters.py`. It needs Python 3.8 or newer and no extra packages. Run it from the root of this repository.

Issues come from one of two sources.

### From an Emma "view in browser" link (monthly newsletter)

1. Open `archive_newsletters.py` and add a line to the `ISSUES` list near the top:

   ```python
   "2026-11": "https://t.e2ma.net/message/xxxxxx/yyyyyyy",
   ```

2. Run:

   ```bash
   python archive_newsletters.py --resolve-links --localise-images
   ```

Issues already in `docs/monthly/` are skipped. Do this soon after each send: Emma's links stop working after a while (the March 2025 link had already expired when we went looking), and the script can't recover an issue once its link is dead.

### From a saved email (either publication)

1. Save the email as an `.html` (or `.eml`) file, the same way as the existing exports. Keep these files **outside** this repository, because an Outlook export is about 7 MB.
2. Make sure the file name contains the month and year, for example `SIH Newsletter, November 2026.html` or `SIH November 2026 Training Update.html`. The script reads the month and year from the name and skips files without them.
3. Run the command for the right publication:

   ```bash
   # monthly newsletter
   python archive_newsletters.py --from-dir ~/Desktop/exports --resolve-links --localise-images

   # training update
   python archive_newsletters.py --series training --from-dir ~/Desktop/training \
       --resolve-links --localise-images
   ```

   For a single file, use `--from-file 2026-11=path/to/file.html` instead of `--from-dir`.

Outlook-on-the-web exports include Outlook's entire application, so the script keeps only the email itself and discards the rest.

### Check before you commit

The repository is public, so look at the result first.

1. Try one issue before a big batch (for example `--only 2026-11`, or a folder holding a single file) and open the page in a browser.
2. Read `unresolved_links.tsv`, which the script writes in the repo root. It lists links that could not be resolved and still point at a tracking address, and any link that was replaced because it looked personal (marked `[PERSONAL LINK REMOVED]`).
3. Run `python archive_newsletters.py --check-images`. It should finish with `OK: every image is stored in the archive.` If it lists pages, see "Making sure every image is stored" below.
4. Run `git status` and `git diff --stat`. You should see new `docs/monthly/YYYY-MM.htm` (or `docs/training/YYYY-MM.htm`) files, their `img/` folders, `docs/index.html`, `docs/nav.js` and this README. You should **not** see the raw email exports.
5. Commit and push. GitHub Pages republishes within a few minutes.

`link_cache.json` and `unresolved_links.tsv` are local working files and are listed in `.gitignore`.

## Navigation between issues

Every issue page shows a bar at the top and bottom with **All issues** (back to the front page), the issue name, and links to the **previous** and **next** issue of the same publication. Months with no issue are skipped, and the first and last issues simply have no link on that side.

It works through one shared file, `docs/nav.js`, which contains the list of issues. Each issue page only carries a small hook, marked by `<!-- sih-nav:start -->` and `<!-- sih-nav:end -->`, that loads it. That means:

- Adding an issue changes `nav.js` and the new page only; older pages are not touched.
- `nav.js` is rebuilt automatically after any run that adds an issue, or on demand with `python archive_newsletters.py --update-indexes`.
- New issues get the hook automatically. Pages already in the archive get it from `--sanitise-existing`, which is safe to repeat.

The bar is drawn in an isolated container so an email's own styles can't distort it, and it is hidden when printing. It needs JavaScript; without it, only an "All issues" link appears at the bottom of the page.

## Script options

| Option | What it does |
| --- | --- |
| `--from-dir DIR` | Process every `.html`, `.htm` and `.eml` file in `DIR`, taking the month from the file name. Skips the built-in `ISSUES` list. |
| `--from-file YYYY-MM=PATH` | Process one saved email. Can be repeated. |
| `--series {newsletter,training}` | Which publication the issues belong to. Sets the output folder and page title. Default: `newsletter`. |
| `--resolve-links` | Replace Emma, Mimecast and Safe Links tracking addresses with the real destination. This requests each link once, so each link registers one click in Emma. |
| `--localise-images` | Download images into `img/` so the archive doesn't depend on Emma's image host. Covers `<img>` tags, `srcset`, `background=` attributes and CSS `url(...)`. Combine with `--sanitise-existing` to fix pages already in the archive. |
| `--check-images` | Audit every page and report images that are not stored in the archive (remote, blocked when saved, or missing). Exits with an error if any are found. Needs no network. |
| `--subscribe-url URL` | Address that replaces personal unsubscribe and update-preferences links. Default: the newsletter sign-up form, or `#` (no link) for the training update. |
| `--only YYYY-MM` | Process a single issue. |
| `--force` | Overwrite issues that already exist. |
| `--allow-index` | Let search engines index the pages. By default pages carry `noindex, nofollow`. |
| `--delay SECONDS` | Pause between network requests (default 0.5). |
| `--out DIR` | Write to a different folder instead of the series default. |
| `--sanitise-existing` | Re-apply the cleaning rules to every page already in the archive (both publications), rewriting them in place. Add `--dry-run` first to see what would change without writing anything. Add `--resolve-links` to also resolve their tracking links. It also adds the navigation hook to any page missing it. |
| `--dry-run` | With `--sanitise-existing`: list what would change and write nothing. |
| `--update-indexes` | Only rebuild the list below, `docs/index.html`, `docs/nav.js` and `docs/404.html`; no downloads. This also happens automatically after any run that adds an issue. |

## What the script changes in each issue

Saved newsletters contain things that should not be published, so each page is cleaned:

- The Emma open-tracking pixel is removed.
- Personal unsubscribe, update-preferences and subscribe links (which carry a recipient's member ID and token) are replaced with a generic address. They are matched by Emma's markup and by the link text.
- With `--resolve-links`, tracking redirects are replaced with the real destination. If a resolved address looks recipient-specific (an unsubscribe or opt-out address, or an `email=` parameter) it is replaced too. This is a safety net, not a guarantee, so skim a couple of pages.
- Contents links ("News and Announcements", "Training and Events") that pointed at the expiring web-view page become plain jumps within the page.
- The browser's "saved from url" comment, which reveals the web-view address, is removed.
- A two-line navigation hook is added just before `</body>` (see "Navigation between issues").
- Outlook leftovers are removed: the application shell, yellow search highlights, internal ids and link tooltips.
- With `--localise-images`, images are saved locally.
- The "HTML version of this email" link is removed, because it points at a page that expires.

Images in `<img>` tags, `srcset`, `background=` attributes and CSS `url(...)` are downloaded. A link that merely points at an image (`<a href>`) is left as a link.

## Making sure every image is stored

An archive page should never fetch anything from another website, because those images disappear when the sending service or its image host does. Check at any time with:

```bash
python archive_newsletters.py --check-images
```

It reads every page (no network needed) and reports three kinds of problem:

- **remote**: the page still loads an image from another site (for example `cloudfront.net` or `images.e2ma.net`). Fix it with `python archive_newsletters.py --sanitise-existing --localise-images`. The script downloads each image into `img/YYYY-MM/` and rewrites the page, so run it while the originals still load.
- **unusable**: the image address is something a browser can't load, such as `content-blocker://`, which an email app writes when it saves a message with remote content switched off. The real address is not in the file, so the image cannot be recovered from it. Re-save the email with remote images loading and re-run that issue with `--from-file YYYY-MM=path --force`.
- **missing file**: the page points at a local file that isn't in the repository.

Two things are ignored as harmless: hidden 1x1 tracking pixels and `custom-font://` font references, neither of which affects how a page looks.

## Cleaning pages that are already in the archive

If the cleaning rules improve, or an old page turns out to contain something it shouldn't, re-apply them to everything in `docs/`:

```bash
python archive_newsletters.py --sanitise-existing --dry-run   # preview, writes nothing
python archive_newsletters.py --sanitise-existing             # apply
git diff --stat
```

It is safe to run repeatedly: a second run changes nothing. Visible text is never altered, only links, comments and Outlook leftovers. Many older pages still contain Emma and Mimecast tracking links; adding `--resolve-links` replaces them with the real destinations, but it requests each link once and can take an hour or more for the whole archive.

Cleaning a page does not remove earlier versions from git history.

## Troubleshooting

- **`HTTP Error 403` or "download failed" for an Emma link.** The link has probably expired. Open it in a browser to check. If the page is gone, use a saved copy of the email instead.
- **Links still start with `url.au.m.mimecastprotect.com`.** Resolving them failed, usually because the destination was an expired Emma tracking link. They are listed in `unresolved_links.tsv` so you can fix them by hand. If the visible link text is itself a web address, the script uses that.
- **A file was skipped with "no 'Month YYYY' in filename".** Rename it to include the month name and year.
- **Some images are still remote.** A download failed (a warning is printed) or the page predates `--localise-images`. Run `python archive_newsletters.py --check-images`, then `python archive_newsletters.py --sanitise-existing --localise-images` once the image host responds.
- **Images are blank and `--check-images` says "unusable".** The email was saved with remote content blocked. See "Making sure every image is stored".
- **"could not find the email body in this Outlook export".** The file looks like an Outlook export but has a different layout from the ones we've seen, so `extract_outlook_body` in the script needs adjusting.

## Archive

<!-- ARCHIVE-LIST:START (generated by archive_newsletters.py; do not edit) -->

### Monthly Newsletter

| Year | Issues |
| --- | --- |
| 2026 | [Feb](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-02.htm "February 2026") · [Mar](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-03.htm "March 2026") · [Apr](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-04.htm "April 2026") · [May](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-05.htm "May 2026") · [Jun](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-06.htm "June 2026") · [Jul](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-07.htm "July 2026") · [Aug](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-08.htm "August 2026") · [Sep](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-09.htm "September 2026") · [Oct](https://sydney-informatics-hub.github.io/newsletter/monthly/2026-10.htm "October 2026") |
| 2025 | [Feb](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-02.htm "February 2025") · [Mar](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-03.htm "March 2025") · [Apr](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-04.htm "April 2025") · [May](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-05.htm "May 2025") · [Jun](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-06.htm "June 2025") · [Jul](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-07.htm "July 2025") · [Aug](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-08.htm "August 2025") · [Sep](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-09.htm "September 2025") · [Oct](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-10.htm "October 2025") · [Nov](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-11.htm "November 2025") · [Dec](https://sydney-informatics-hub.github.io/newsletter/monthly/2025-12.htm "December 2025") |
| 2024 | [Feb](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-02.htm "February 2024") · [Mar](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-03.htm "March 2024") · [Apr](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-04.htm "April 2024") · [May](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-05.htm "May 2024") · [Jun](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-06.htm "June 2024") · [Jul](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-07.htm "July 2024") · [Aug](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-08.htm "August 2024") · [Sep](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-09.htm "September 2024") · [Oct](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-10.htm "October 2024") · [Nov](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-11.htm "November 2024") · [Dec](https://sydney-informatics-hub.github.io/newsletter/monthly/2024-12.htm "December 2024") |
| 2023 | [Feb](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-02.htm "February 2023") · [Mar](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-03.htm "March 2023") · [Apr](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-04.htm "April 2023") · [May](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-05.htm "May 2023") · [Jun](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-06.htm "June 2023") · [Jul](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-07.htm "July 2023") · [Aug](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-08.htm "August 2023") · [Sep](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-09.htm "September 2023") · [Oct](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-10.htm "October 2023") · [Nov](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-11.htm "November 2023") · [Dec](https://sydney-informatics-hub.github.io/newsletter/monthly/2023-12.htm "December 2023") |
| 2022 | [Feb](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-02.htm "February 2022") · [Mar](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-03.htm "March 2022") · [Apr](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-04.htm "April 2022") · [May](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-05.htm "May 2022") · [Jun](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-06.htm "June 2022") · [Jul](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-07.htm "July 2022") · [Aug](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-08.htm "August 2022") · [Sep](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-09.htm "September 2022") · [Oct](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-10.htm "October 2022") · [Nov](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-11.htm "November 2022") · [Dec](https://sydney-informatics-hub.github.io/newsletter/monthly/2022-12.htm "December 2022") |
| 2021 | [Jun](https://sydney-informatics-hub.github.io/newsletter/monthly/2021-06.htm "June 2021") · [Jul](https://sydney-informatics-hub.github.io/newsletter/monthly/2021-07.htm "July 2021") · [Aug](https://sydney-informatics-hub.github.io/newsletter/monthly/2021-08.htm "August 2021") · [Nov](https://sydney-informatics-hub.github.io/newsletter/monthly/2021-11.htm "November 2021") · [Dec](https://sydney-informatics-hub.github.io/newsletter/monthly/2021-12.htm "December 2021") |

### Training Update

| Year | Issues |
| --- | --- |
| 2026 | [Jan](https://sydney-informatics-hub.github.io/newsletter/training/2026-01.htm "January 2026") · [Feb](https://sydney-informatics-hub.github.io/newsletter/training/2026-02.htm "February 2026") · [Mar](https://sydney-informatics-hub.github.io/newsletter/training/2026-03.htm "March 2026") · [Apr](https://sydney-informatics-hub.github.io/newsletter/training/2026-04.htm "April 2026") · [May](https://sydney-informatics-hub.github.io/newsletter/training/2026-05.htm "May 2026") · [Jun](https://sydney-informatics-hub.github.io/newsletter/training/2026-06.htm "June 2026") · [Jul](https://sydney-informatics-hub.github.io/newsletter/training/2026-07.htm "July 2026") · [Aug](https://sydney-informatics-hub.github.io/newsletter/training/2026-08.htm "August 2026") · [Sep](https://sydney-informatics-hub.github.io/newsletter/training/2026-09.htm "September 2026") |
| 2025 | [Jan](https://sydney-informatics-hub.github.io/newsletter/training/2025-01.htm "January 2025") · [Feb](https://sydney-informatics-hub.github.io/newsletter/training/2025-02.htm "February 2025") · [Mar](https://sydney-informatics-hub.github.io/newsletter/training/2025-03.htm "March 2025") · [Apr](https://sydney-informatics-hub.github.io/newsletter/training/2025-04.htm "April 2025") · [May](https://sydney-informatics-hub.github.io/newsletter/training/2025-05.htm "May 2025") · [Jun](https://sydney-informatics-hub.github.io/newsletter/training/2025-06.htm "June 2025") · [Jul](https://sydney-informatics-hub.github.io/newsletter/training/2025-07.htm "July 2025") · [Aug](https://sydney-informatics-hub.github.io/newsletter/training/2025-08.htm "August 2025") · [Sep](https://sydney-informatics-hub.github.io/newsletter/training/2025-09.htm "September 2025") · [Oct](https://sydney-informatics-hub.github.io/newsletter/training/2025-10.htm "October 2025") · [Nov](https://sydney-informatics-hub.github.io/newsletter/training/2025-11.htm "November 2025") |
| 2024 | [Feb](https://sydney-informatics-hub.github.io/newsletter/training/2024-02.htm "February 2024") · [Apr](https://sydney-informatics-hub.github.io/newsletter/training/2024-04.htm "April 2024") · [May](https://sydney-informatics-hub.github.io/newsletter/training/2024-05.htm "May 2024") · [Jun](https://sydney-informatics-hub.github.io/newsletter/training/2024-06.htm "June 2024") · [Aug](https://sydney-informatics-hub.github.io/newsletter/training/2024-08.htm "August 2024") · [Sep](https://sydney-informatics-hub.github.io/newsletter/training/2024-09.htm "September 2024") · [Oct](https://sydney-informatics-hub.github.io/newsletter/training/2024-10.htm "October 2024") · [Nov](https://sydney-informatics-hub.github.io/newsletter/training/2024-11.htm "November 2024") |
| 2023 | [Jan](https://sydney-informatics-hub.github.io/newsletter/training/2023-01.htm "January 2023") · [Mar](https://sydney-informatics-hub.github.io/newsletter/training/2023-03.htm "March 2023") · [Apr](https://sydney-informatics-hub.github.io/newsletter/training/2023-04.htm "April 2023") · [May](https://sydney-informatics-hub.github.io/newsletter/training/2023-05.htm "May 2023") · [Jun](https://sydney-informatics-hub.github.io/newsletter/training/2023-06.htm "June 2023") · [Jul](https://sydney-informatics-hub.github.io/newsletter/training/2023-07.htm "July 2023") · [Aug](https://sydney-informatics-hub.github.io/newsletter/training/2023-08.htm "August 2023") · [Sep](https://sydney-informatics-hub.github.io/newsletter/training/2023-09.htm "September 2023") · [Oct](https://sydney-informatics-hub.github.io/newsletter/training/2023-10.htm "October 2023") · [Nov](https://sydney-informatics-hub.github.io/newsletter/training/2023-11.htm "November 2023") |
| 2022 | [Jan](https://sydney-informatics-hub.github.io/newsletter/training/2022-01.htm "January 2022") · [Feb](https://sydney-informatics-hub.github.io/newsletter/training/2022-02.htm "February 2022") · [May](https://sydney-informatics-hub.github.io/newsletter/training/2022-05.htm "May 2022") · [Jun](https://sydney-informatics-hub.github.io/newsletter/training/2022-06.htm "June 2022") · [Jul](https://sydney-informatics-hub.github.io/newsletter/training/2022-07.htm "July 2022") · [Aug](https://sydney-informatics-hub.github.io/newsletter/training/2022-08.htm "August 2022") · [Sep](https://sydney-informatics-hub.github.io/newsletter/training/2022-09.htm "September 2022") · [Oct](https://sydney-informatics-hub.github.io/newsletter/training/2022-10.htm "October 2022") · [Nov](https://sydney-informatics-hub.github.io/newsletter/training/2022-11.htm "November 2022") |
| 2021 | [Jul](https://sydney-informatics-hub.github.io/newsletter/training/2021-07.htm "July 2021") · [Sep](https://sydney-informatics-hub.github.io/newsletter/training/2021-09.htm "September 2021") · [Oct](https://sydney-informatics-hub.github.io/newsletter/training/2021-10.htm "October 2021") · [Nov](https://sydney-informatics-hub.github.io/newsletter/training/2021-11.htm "November 2021") |

<!-- ARCHIVE-LIST:END -->
