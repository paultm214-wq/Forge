Change: glassinv

<!--
Drop in ~/forge/inbox/ once glassinv has a committed run, or:
    forge change glassinv wendy-theme.md

BEFORE YOU RUN IT - fill in every [[FILL IN: ...]] below:
  1. Site title and the line under it (Decisions).
  2. A short description of Wendy and of Rue (Assets prompts) - the photos
     do the likeness, the words fix what the photos cannot show.
  3. Put the reference photos in ~/forge/refs/wendy/ on Gemini, named as in
     the Assets block (or change the names there):
       wendy-1.jpg, wendy-2.jpg, wendy-3.jpg   - face clearly visible, good
                                                 light, different angles
       rue-1.jpg, rue-2.jpg                    - Rue's face and coat pattern
     About 1-2 megapixels each (phone photos scaled to ~1600 px are ideal).
     They are copied into forge's run and sent only to Vulcan; nothing leaves
     the house. Do ask Wendy before using her photos.

The images render on Vulcan at full steps (the likeness matters), except
the background tile, which is abstract and renders on Mercury with
Lightning. If a likeness misses, forge's review can reroll it with a new
seed; you can also reroll by hand with a smaller change request.
-->

# Wendy's theme

## What changes

The app becomes Wendy's own: her name in the title, a banner of a cartoon
Wendy and her cat Rue at the glass workbench, a stained-glass background
behind the pages, and Rue as the site icon and on empty pages. Nothing about
the inventory, the API or the existing views changes.

## Decisions

Site identity
- `make_app` gains two keyword parameters, both optional:
  `assets_dir=None` and `title="Glass inventory"`.
- `config_from_env` adds:
  - `assets_dir`: `GLASSINV_ASSETS`, default `/app/assets`;
  - `title`: `GLASSINV_TITLE`, default `Glass inventory`.
- `serve()` passes both to `make_app`.
- The title appears, HTML-escaped, in every page's `<title>` (as
  `<page> - <title>`, or just `<title>` on the dashboard) and in the header's
  `<h1>`.
- Wendy's values (set in compose.yaml by build_outputs):
  - `GLASSINV_TITLE`: [[FILL IN: e.g. "Wendy's Glass Studio"]]
  - a subtitle under the header title, shown on the dashboard only:
    [[FILL IN: e.g. "Glass, patterns and projects - with Rue's supervision"]].
    It comes from `GLASSINV_SUBTITLE` (config key `subtitle`, default empty,
    `make_app(..., subtitle="")`); empty means no subtitle element.

Serving the images
- New route `GET /static/img/<name>`:
  - `<name>` must match `^[a-z0-9][a-z0-9_-]*\.(png|jpg|jpeg|webp|svg)$`;
    anything else is 404.
  - The file is `<assets_dir>/<name>`. 404 if `assets_dir` is None or the
    file does not exist. Resolve the path and refuse (404) anything that is
    not directly inside `assets_dir`.
  - Content-Type by extension (`image/png`, `image/jpeg`, `image/webp`,
    `image/svg+xml`) and `Cache-Control: max-age=86400`.
  - Like `/static/app.css`, it needs the password when one is set.
- The app checks once, when `make_app` runs, which of the three theme files
  exist in `assets_dir`. A missing file is simply left out of the pages - no
  broken image icons. So the app still works with no images at all.

The three images (rendered by forge - see Assets)
- `wendy-banner.png` (1536 x 512): in the shared layout's header, full width
  of the content column, `alt` text [[FILL IN: e.g. "Wendy and Rue at the
  glass workbench"]] from `GLASSINV_BANNER_ALT` (config key `banner_alt`,
  `make_app(..., banner_alt="")`; empty falls back to the title). On the
  dashboard it is shown at full height; on every other page the header uses
  a compact strip (the same image, `object-fit: cover`, 96 px tall) so the
  inventory stays above the fold on a phone. The `<h1>` title sits on the
  banner in a lead-coloured band at its bottom edge.
- `glass-tile.png` (768 x 768, seamless): the `body` background, repeating.
  Content sits on a paper-coloured column (`--paper`, max-width 1200 px,
  the existing palette) with 24 px of the tile visible on each side at
  desktop width and none at phone width (under 600 px the tile shows only
  in the header). In dark mode the tile is dimmed with a
  `linear-gradient(rgba(0,0,0,.55), rgba(0,0,0,.55))` layered over it.
- `rue-mark.png` (512 x 512): Rue in a round leaded-glass medallion.
  - the favicon: `<link rel="icon" href="/static/img/rue-mark.png">` in every
    page's head;
  - on any list or browser page with no results, a "nothing here yet"
    message with the mark at 96 px and alt text "Rue";
  - in the footer at 24 px, next to the title.

Look and feel
- The "leaded light" palette, type and grid stay exactly as they are; the
  images join that style rather than replacing it.
- Everything still works at 375 px wide.

Deployment
- `build_outputs` additionally creates the directory `<out_dir>/assets/`
  (forge copies the rendered images into it), so its returned list of four
  files does not change.
- `Dockerfile`: `COPY assets/ /app/assets/` before `USER`, and
  `ENV GLASSINV_ASSETS=/app/assets`.
- `compose.yaml` sets `GLASSINV_TITLE`, `GLASSINV_SUBTITLE` and
  `GLASSINV_BANNER_ALT` to Wendy's values above (each quoted).
- `README.md` gains a "Theme" section: the three files in `assets/`, that
  replacing one and rebuilding the image changes the look, and the three
  environment variables.

Must keep working unchanged: every acceptance item of the original spec.

## Acceptance

T-tests use `A = tempfile.mkdtemp()` holding three small files:
`wendy-banner.png` and `rue-mark.png` (`b"\x89PNG\r\n\x1a\n" + b"0" * 100`)
and `glass-tile.png` (the same bytes), and
`app = make_app(tempfile.mkdtemp(), assets_dir=A, title="Wendy's <Glass>", subtitle="Rue approves", banner_alt="Wendy and Rue")`.

T1. `GET /static/img/wendy-banner.png` returns 200, `image/png`, the exact
    bytes and a `Cache-Control` header containing `max-age=86400`.
T2. Each of these returns 404: `/static/img/nope.png`,
    `/static/img/..%2Fglassinv.py`, `/static/img/Wendy.PNG`,
    `/static/img/.hidden.png`, `/static/img/banner.gif`. With
    `make_app(dir)` (no assets_dir), `/static/img/wendy-banner.png` is 404.
T3. `GET /` contains:
    - `<title>Wendy&#x27;s &lt;Glass&gt;</title>` (escaped; the raw
      `Wendy's <Glass>` appears nowhere);
    - `src="/static/img/wendy-banner.png"` with `alt="Wendy and Rue"`;
    - the text `Rue approves`;
    - `<link rel="icon" href="/static/img/rue-mark.png">`.
T4. `GET /glass` (on that empty app) contains `/static/img/rue-mark.png`
    with `alt="Rue"` and does not contain `Rue approves`. Its `<title>`
    ends with `Wendy&#x27;s &lt;Glass&gt;</title>`.
T5. `GET /static/app.css` contains `/static/img/glass-tile.png` and
    `prefers-color-scheme`.
T6. With an `assets_dir` holding only `glass-tile.png`, `GET /` contains no
    `wendy-banner.png` and no `rue-mark.png`, and still returns 200.
T7. With a password set, `/static/img/wendy-banner.png` returns 401 without
    credentials and 200 with them.
T8. `config_from_env({})` adds `assets_dir` "/app/assets", `title`
    "Glass inventory", `subtitle` "" and `banner_alt` "" to the original
    values. `GLASSINV_ASSETS=/x`, `GLASSINV_TITLE=W` and
    `GLASSINV_SUBTITLE=S` come through as given.
T9. `build_outputs(d)` still returns four paths; `<d>/assets` is a
    directory; the `Dockerfile` contains `COPY assets/ /app/assets/` and
    `GLASSINV_ASSETS=/app/assets`; `compose.yaml` contains
    `GLASSINV_TITLE`; `README.md` contains `GLASSINV_TITLE`.
T10. All of the original acceptance items 1-36 still pass.

## Assets

```json
[
  {
    "id": "theme-banner",
    "title": "Wendy and Rue at the workbench",
    "filename": "wendy-banner.png",
    "server": "vulcan",
    "width": 1536, "height": 512,
    "steps": 30, "cfg_scale": 2.5, "seed": 2611,
    "reference_images": ["refs/wendy/wendy-1.jpg", "refs/wendy/wendy-2.jpg", "refs/wendy/wendy-3.jpg",
                         "refs/wendy/rue-1.jpg", "refs/wendy/rue-2.jpg"],
    "prompt": "Warm cartoon illustration, wide banner. The woman from the reference photos, drawn as a friendly cartoon that keeps her likeness - [[FILL IN: hair, glasses, anything that should carry over, e.g. 'shoulder-length silver hair, round glasses, a denim apron']] - sits at a stained-glass workbench, smiling, holding a sheet of cobalt-blue cathedral glass up to the light. Beside her on the bench sits her cat Rue from the reference photos - [[FILL IN: e.g. 'a grey tabby with white paws and green eyes']] - watching a copper foil roll with great interest. Behind them a tall leaded-glass window glows with deep blue, amber, teal and ruby panes; glass sheets lean in a rack, a soldering iron rests on its stand, scraps of coloured glass catch the light on the bench. Rich jewel tones, soft outlines, cosy and charming, picture-book style. The left third of the image is calmer (the window) so a title can sit over it.",
    "negative_prompt": "text, letters, words, watermark, signature, logo, photorealistic, extra fingers, deformed hands, two cats, extra people, blurry face"
  },
  {
    "id": "theme-mark",
    "title": "Rue medallion",
    "filename": "rue-mark.png",
    "server": "vulcan",
    "width": 512, "height": 512,
    "steps": 30, "cfg_scale": 2.5, "seed": 2612,
    "reference_images": ["refs/wendy/rue-1.jpg", "refs/wendy/rue-2.jpg"],
    "prompt": "Round stained-glass medallion, a simple leaded-glass roundel: the cat Rue from the reference photos - [[FILL IN: same description as in the banner]] - sitting upright, face forward, drawn in bold flat glass pieces with dark lead lines, on a background of deep blue and amber glass. Clear silhouette that still reads at 24 pixels, centred, the circle filling the frame, plain dark background outside the circle.",
    "negative_prompt": "text, letters, watermark, photorealistic, extra cats, busy background, cropped circle"
  },
  {
    "id": "theme-tile",
    "title": "Stained-glass background tile",
    "filename": "glass-tile.png",
    "server": "mercury",
    "tileable": true,
    "width": 768, "height": 768,
    "steps": 30, "cfg_scale": 2.5, "seed": 2613,
    "prompt": "Seamless stained-glass pattern seen flat, filling the whole frame: irregular cathedral glass pieces in muted cobalt, teal, amber and soft ruby separated by thin dark grey lead lines, a few clear textured pieces, gentle light coming through, low contrast and calm so it works as a website background behind a paper-coloured page. Even density everywhere, no focal point, no border.",
    "negative_prompt": "text, letters, watermark, border, frame, vignette, bright highlights, high contrast, figures, faces, central motif"
  }
]
```
