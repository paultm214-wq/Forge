Change: ~/forge/runs/glassinv-20261020-185618

<!--
Fixes the six issues Claude's review left blocking on run
glassinv-20261020-185618 (that run is not committed, so this change starts
from its run directory rather than from the project name):
    forge change ~/forge/runs/glassinv-20261020-185618 glassinv-review-fixes.md

Each issue was checked against the run's assembled module.py: all six are
real. Four of them are the same kind of bug: the HTML, the JavaScript and
the router were written by different chunks that never agreed on element
ids, data attributes, URLs or how forms are sent. So this change states
those agreements exactly ("Shared hooks"), and its acceptance tests check
both sides of each one, plus every link on every page.

Nothing here needs filling in.
-->

# Review fixes: threads, 404s, URLs, forms and the browser hooks

## What changes

The app works in the container (not only in the in-process tests), every
link on every page leads somewhere, patterns, projects and supplies get new
and edit forms, and photo-first intake, the lot buttons and the detail-page
toggles actually work in the browser. Nothing in the JSON API or the data
changes except that deleting something that does not exist now returns 404.

## Decisions

### 1. Store is safe across server threads

- `Store.__init__` opens its connection with
  `sqlite3.connect(path, check_same_thread=False)` and creates
  `self._lock = threading.RLock()`.
- Every public Store method (create, get, update, delete, query, the lot,
  link, photo, export and usage methods) runs its whole body under
  `with self._lock:`. An RLock, because these methods call one another.
- `serve()` is unchanged (it already uses a threading server with
  `make_server`).

### 2. Deleting what does not exist is 404

- `Store.delete(kind, id)` raises `KeyError` if the record does not exist,
  before touching photos or files.
- `Store.delete_lot(lot_id)`, `Store.delete_photo(photo_id)` and
  `Store.unlink_glass(project_id, glass_id)` raise `KeyError` when the lot,
  photo or link does not exist.
- The router already maps `KeyError` to 404; check that every DELETE route
  goes through that mapping.

### 3. One URL scheme for every page

Page URLs, all GET, all `text/html; charset=utf-8`. `<kind>` in a supplies
URL is the SCHEMAS kind: `foil`, `solder`, `came` or `supply`.

| Page | URL |
|---|---|
| glass browser, detail, intake, compare | `/glass`, `/glass/<id>`, `/glass/new`, `/glass/compare?ids=...` (unchanged) |
| glass edit | `/glass/<id>/edit` |
| patterns, projects: list, new, detail, edit | `/patterns`, `/patterns/new`, `/patterns/<id>`, `/patterns/<id>/edit` (same for `/projects`) |
| supplies list | `/supplies?tab=foil\|solder\|came\|other` (`other` lists the `supply` kind; default tab `foil`) |
| supply new, detail, edit | `/supplies/<kind>/new`, `/supplies/<kind>/<id>`, `/supplies/<kind>/<id>/edit` |

- An unknown id, an unknown `<kind>` or any other path is 404.
- The old two-segment supply routes (`/foil/<id>`, `/supplies/<id>`, ...)
  are removed. Nothing links to them.
- Every list page has a "New ..." link to its `new` URL; every detail page
  has an "Edit" link to its `edit` URL. Supply list items link to
  `/supplies/<kind>/<id>`.

### 4. Forms are sent by JavaScript as JSON

The API takes JSON only, so a plain HTML form post can never work.
- `_new_form(kind, record=None) -> str` builds the form for any kind from
  SCHEMAS (new when `record` is None, edit otherwise, pre-filled from the
  record):
  `<form class="record-form" data-kind="<kind>" data-plural="<plural>" data-method="POST|PUT" data-id="<id or empty>">`
  - one control per schema field, each with `name="<field>"` and
    `data-type="<the SCHEMAS type, e.g. enum:texture>"`: text and inches as
    text inputs, longtext as `<textarea>`, enum as `<select>` (with an empty
    first option), multi as `<select multiple>`, date as
    `<input type="date">`, number as `<input type="number" step="any">`,
    bool as a checkbox, tags as a comma-separated text input, ref as a
    `<select>` of that kind's records (id and name), hex as
    `<input type="color">`;
  - under each control, `<span class="field-error" data-error-for="<field>"></span>`;
  - a submit button, and `<noscript>Saving needs JavaScript.</noscript>`.
  - No `action` or `method` attribute: nothing is ever posted as a form.
- The new and edit pages for every kind (glass edit included) are the
  layout around `_new_form`.
- `app.js` defines `formToJson(form)` and `submitRecordForm(form)`, and
  binds every `form.record-form`'s submit event on `DOMContentLoaded`:
  - `formToJson`: by `data-type` - empty -> null; number -> Number;
    bool -> true/false; multi -> the array of selected values; tags -> the
    text as given (the server splits it); ref -> Number or null; everything
    else the string.
  - `submitRecordForm`: `preventDefault`, then `fetch("/api/<plural>" or "/api/<plural>/<id>", {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(formToJson(form))})`.
    On 200/201 it goes to the record's detail page (the URL table above);
    on 400 it writes the error into `[data-error-for="<field>"]` (or above
    the form when `field` is null).

### 5. Shared hooks (the HTML renders them, app.js uses exactly these)

| Where | HTML (rendered by the page) | app.js |
|---|---|---|
| `/glass/new` | `<form id="intake-form" class="record-form" data-kind="glass" data-plural="glass" data-method="PUT" data-id="">`, `<input type="file" id="photo-input" accept="image/*" capture="environment">`, `<img id="intake-preview" alt="">`, `<p id="intake-status"></p>`, `<button type="button" id="intake-cancel">` | `initIntake()` |
| suggested fields | `<span class="suggested" data-suggested-for="<field>" hidden>suggested</span>` next to each vision-fillable control | shown when a suggestion is applied, hidden on that control's change |
| `/glass/<id>` | wrapper `<div class="glass-detail" data-glass="<id>" data-backdrop="light">`; `<button type="button" data-backdrop-toggle>` | toggles the wrapper's `data-backdrop` between `light` and `dark` |
| lots table | `<button type="button" class="lot-btn" data-lot="<lot_id>" data-delta="-1">` and `data-delta="1"`; `<span data-lot-count="<lot_id>">n</span>`; `<span data-stock-summary>...</span>` | `PUT /api/lots/<lot_id>` `{"count": n+delta}` (never below 0), then sets the count and, from `GET /api/glass/<id>`, the stock summary |
| "Projects used in" | `<section class="projects-used" data-mode="thumbs">` with `<button type="button" data-mode-toggle>` | toggles `data-mode` between `thumbs` and `list` (CSS shows one layout per mode) |

Intake flow (`initIntake`, when `#intake-form` exists):
1. When a file is chosen: show it in `#intake-preview`; make the thumbnail
   (`makeThumbnail`) and colour (`dominantColor`).
2. Create a draft: `POST /api/glass` `{"name": "New glass", "color_hex": <colour>}`
   -> id; set the form's `data-id`. Upload the original file raw to
   `POST /api/glass/<id>/photos` (Content-Type = the file's type) -> photo
   id, then the thumbnail to `POST /api/photos/<photo_id>/thumb`
   (`image/jpeg`). Put the colour in the `color_hex` field.
3. If `GET /api/meta` says `vision` is true: `POST /api/intake/describe`
   `{"photo_id": <photo_id>}` and apply `response.suggestions` to the
   matching fields, showing their "suggested" markers. `#intake-status`
   says what is happening ("Uploading...", "Asking for suggestions...",
   errors).
4. Save is the normal `submitRecordForm` (PUT to the draft) -> `/glass/<id>`.
5. `#intake-cancel` deletes the draft (`DELETE /api/glass/<id>`) if one was
   made, then goes to `/glass`.

All binding happens in one `DOMContentLoaded` handler that calls
`initIntake()`, binds `.record-form`, `.lot-btn`, `[data-backdrop-toggle]`
and `[data-mode-toggle]`, each only when present. The CSS styles
`[data-backdrop="dark"]` (dark ground) and `.projects-used[data-mode=...]`.

## Acceptance

"Fixture app" is as in the original spec; "full fixture" is the fixture app
plus a pattern `{"name": "Iris", "technique": "copper_foil"}`, a project
`{"name": "Window", "pattern_id": <iris>}` linked to glass 4, and one record
of each supply kind (the four from acceptance item 12), plus one JPEG on
glass 1. `JS = APP_JS`.

R1. Threads: on the fixture app, 8 `threading.Thread`s each make 10 calls
    (`GET /api/glass`, `GET /api/glass/<1..9>`, `POST /api/patterns`
    `{"name": "t"}`) through `call`. No call returns 500, and afterwards
    `GET /api/patterns` has `total` 80. `Store(d).conn` was opened with
    `check_same_thread=False` (a `get` from a new thread works).
R2. 404s: `DELETE /api/glass/99`, `DELETE /api/patterns/99`,
    `DELETE /api/lots/999`, `DELETE /api/photos/999` and
    `DELETE /api/projects/<window>/glass/9` (not linked) each return 404;
    `store.delete("glass", 99)` raises `KeyError`. Deleting an existing
    record is still 200.
R3. Every link works: starting from `/`, `/glass`, `/patterns`,
    `/projects` and `/supplies?tab=<each tab>` on the full fixture, collect
    every `href="/..."` in every page reached (following links to depth 3,
    each URL once, ignoring `/media/` and `/static/`), and `GET` each one:
    every response is 200. At least one `/supplies/foil/<id>`,
    `/supplies/supply/<id>`, `/patterns/<id>/edit` and `/patterns/new` URL
    is among them.
R4. Supply pages: `/supplies/foil/<id>` shows "Venture 7/32 black" and
    `7/32"`; `/supplies/supply/<id>` shows "Black patina" and "Patina";
    `/supplies/glue/1`, `/supplies/foil/999`, `/foil/1` and `/supplies/1`
    are 404. `/supplies?tab=other` lists "Black patina".
R5. Forms: `GET /patterns/new` contains `class="record-form"`,
    `data-kind="pattern"`, `data-method="POST"`, `name="technique"`,
    `data-type="enum:technique"`, `data-error-for="name"`, and no
    `method="post"` (any case) and no `action=`. `GET /patterns/<iris>/edit`
    has `data-method="PUT"`, `data-id="<iris>"` and `value="Iris"`.
    `GET /glass/1/edit` has `data-kind="glass"` and `<select multiple`.
    `/projects/new`, `/supplies/came/new` and `/supplies/came/<id>/edit`
    are 200 and contain `record-form`.
R6. Hooks, both sides: on the full fixture, `/glass/new` contains
    `id="intake-form"`, `id="photo-input"`, `capture="environment"`,
    `id="intake-preview"`, `id="intake-cancel"` and `data-suggested-for=`;
    `/glass/1` contains `data-backdrop="light"`, `data-backdrop-toggle`,
    `class="lot-btn"`, `data-delta="-1"`, `data-lot-count=`,
    `data-stock-summary`, `data-mode="thumbs"` and `data-mode-toggle`.
    JS contains each of `function formToJson`, `function submitRecordForm`,
    `function initIntake`, `DOMContentLoaded`, `'#intake-form'` or
    `"intake-form"`, `photo-input`, `.lot-btn`, `data-backdrop-toggle`,
    `data-mode-toggle`, `data-lot-count`, `data-error-for`,
    `/api/intake/describe`, `.suggestions`, `/thumb` and
    `application/json`.
R7. No stale hooks: every string passed to `getElementById(...)`,
    `querySelector(...)` or `querySelectorAll(...)` in JS (a string literal)
    names an id, class or attribute that appears in at least one page of
    R3's crawl or in `/glass/new`. JS does not contain `/api/glass/photos`
    or `.lot-minus`.
R8. The intake page's form has no `action=` and the JS never posts form
    data urlencoded: JS does not contain `application/x-www-form-urlencoded`
    or `new FormData`.
R9. Every earlier acceptance item (1-36) still passes, with item 23's and
    item 24's DELETE behaviour as in R2.

## Chunks (optional)

Expected to touch: the Store chunks (R1, R2), the HTML chunks for glass
detail/intake and for patterns/projects/supplies (R3-R6), the static JS/CSS
chunk (R6-R8) and the router (the URL table, R2-R5). Put the URL table, the
shared-hooks table and the JSON bodies into the interface contract (its
routes, dom_hooks and payloads lists), so every chunk sees them and forge's
wiring check tests them.
