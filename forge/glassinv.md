# glassinv

<!--
A self-hosted web app for a stained-glass hobbyist's inventory: sheet glass
(browsed by photo), patterns, projects, foil, solder, came and other supplies.
Runs as one Docker container on the LAN.

Every computed value in Acceptance (colour ordering, inch fractions, stock
summaries, vision-suggestion cleanup, and every query result over the
fixture) was checked against a reference implementation before this spec was
written. The HTTP and HTML checks are contracts, not computed values.
-->

## Goal

A stained-glass hobbyist keeps a few hundred sheets and scraps of glass plus
patterns and supplies, and needs to find the right piece for a project by
looking at it. The app is photo-first: every glass item is browsed as
thumbnails, with several ways to view and filter the same inventory. Adding a
new sheet starts with a photo taken on a phone. If a vision model is
configured, the app suggests the colour, type and texture from the photo for
the user to confirm.

The deliverable is the `glassinv` module plus the files to run it in Docker,
written by `build_outputs`.

## Constraints

- Language / runtime: Python 3.12, standard library only. Storage is
  `sqlite3`; the server is `wsgiref` with a threading mix-in. Never import
  `cgi` (removed in Python 3.13): uploads are raw request bodies, not
  multipart forms.
- Module name: `glassinv`.
- No network at test time. The only outbound call the app ever makes is to
  the optional vision model, through an injectable `post` function.
- No `if __name__ == "__main__"` block. The container starts the server with
  `python -c "import glassinv; glassinv.serve()"`.
- The server never decodes images. The browser makes the thumbnail (canvas,
  JPEG) and picks the dominant colour before upload; the server checks file
  signatures and stores bytes.
- No external fonts, scripts or CDNs: the app must work on a LAN with no
  internet. CSS and JS are served from strings inside the module.
- Escape every user-supplied string in HTML (`html.escape`).
- Copy the vocabulary/schema block and the test fixture below verbatim.

## Vocabulary and schemas (copy verbatim)

The terms follow trade usage:
- **Base type and colour mix are separate fields.** Cathedral glass is
  transparent and opalescent is opaque; streaky, wispy, mottle, ring mottle
  and fracture-and-streamer describe how the colours are mixed.
- **Texture is the surface, and effects are separate.** Hammered, granite,
  glue chip, waterglass, reeded and similar are textures. Iridized,
  dichroic, seedy and reamy are effects.
- **COE only matters for fusing.** Most stained-glass sheet is never tested
  for fusing compatibility, so `not_fusible` is a normal value.
- **Supplies:**
  - Foil is sold by width (7/32" is the common one), backing colour and
    thickness in mil.
  - Solder is sold by alloy (60/40, 63/37, 50/50, lead-free), usually as
    1/8" solid wire on 1/2 lb and 1 lb spools.
  - Came is sized by its face (leaf) width, comes in H and U profiles, and
    is sold in strips of about 6 ft.
- **Glass quantity is held as lots**, each a count of one size class (full
  sheet, half sheet, quarter sheet, scrap, other). Sheet sizes differ by
  manufacturer, so a lot may also record its actual dimensions.

```python
# Controlled vocabularies: slug -> label. Order is display order.
VOCAB = {
    "color_family": {
        "red": "Red", "orange": "Orange", "amber": "Amber / Yellow", "green": "Green",
        "teal": "Teal", "blue": "Blue", "purple": "Purple", "pink": "Pink",
        "brown": "Brown", "black": "Black", "white": "White", "clear": "Clear",
        "grey": "Grey", "multi": "Multicolor",
    },
    "base_type": {
        "cathedral": "Cathedral (transparent)", "opalescent": "Opalescent (opaque)",
        "semi_opaque": "Semi-opaque", "antique": "Antique (mouth-blown)",
        "new_antique": "New antique (drawn)", "other": "Other",
    },
    "color_mix": {
        "solid": "Solid", "streaky": "Streaky", "wispy": "Wispy", "mottle": "Mottle",
        "ring_mottle": "Ring mottle", "baroque": "Baroque / swirl", "stipple": "Stipple",
        "fracture_streamer": "Fracture & streamer", "flashed": "Flashed", "other": "Other",
    },
    "texture": {
        "smooth": "Smooth", "hammered": "Hammered", "ripple": "Ripple", "granite": "Granite",
        "waterglass": "Waterglass", "glue_chip": "Glue chip", "reeded": "Reeded",
        "artique": "Artique", "drapery": "Drapery", "crackle": "Crackle",
        "rough_rolled": "Rough rolled", "other": "Other",
    },
    "effect": {
        "iridized": "Iridized", "dichroic": "Dichroic", "seedy": "Seedy (bubbles)",
        "reamy": "Reamy", "crinkle": "Crinkle", "sparkle": "Sparkle / metallic",
    },
    "coe": {"90": "COE 90", "96": "COE 96", "104": "COE 104", "not_fusible": "Not tested for fusing", "unknown": "Unknown"},
    "size_class": {
        "full_sheet": "Full sheet", "half_sheet": "Half sheet", "quarter_sheet": "Quarter sheet",
        "scrap": "Scrap", "other": "Other",
    },
    "pattern_source": {"book": "Book", "online": "Online", "original": "Original design", "class": "Class / kit", "other": "Other"},
    "technique": {"copper_foil": "Copper foil", "lead_came": "Lead came", "mixed": "Foil + came", "fused": "Fused", "mosaic": "Mosaic", "other": "Other"},
    "project_status": {"idea": "Idea", "planned": "Planned", "in_progress": "In progress", "finished": "Finished", "gifted": "Gifted / sold"},
    "foil_backing": {"copper": "Copper", "black": "Black", "silver": "Silver"},
    "foil_edge": {"straight": "Straight", "scalloped": "Scalloped / decorative"},
    "solder_alloy": {"60_40": "60/40 tin/lead", "63_37": "63/37 tin/lead", "50_50": "50/50 tin/lead", "lead_free": "Lead-free", "other": "Other"},
    "solder_core": {"solid": "Solid", "flux_core": "Flux core"},
    "came_material": {"lead": "Lead", "zinc": "Zinc", "brass": "Brass", "copper": "Copper", "brass_capped_lead": "Brass-capped lead"},
    "came_profile": {"h": "H (two channels)", "u": "U (one channel, borders)"},
    "came_face": {"flat": "Flat", "round": "Round", "colonial": "Colonial"},
    "supply_category": {
        "flux": "Flux", "patina": "Patina", "finishing": "Finishing / polish / wax",
        "reinforcement": "Reinforcement (restrip, rebar)", "hanging": "Hooks, rings & chain",
        "ornament": "Bevels, jewels & nuggets", "frame": "Frames", "tool": "Tools & grinder bits",
        "safety": "Safety", "other": "Other",
    },
}
# Words a vision model may use, mapped to slugs. Lower-case keys.
SYNONYMS = {
    "base_type": {"opal": "opalescent", "transparent": "cathedral", "translucent": "semi_opaque", "mouth blown": "antique", "mouth-blown": "antique"},
    "color_mix": {"streak": "streaky", "streaked": "streaky", "mottled": "mottle", "swirl": "baroque", "swirled": "baroque", "fracture": "fracture_streamer", "streamer": "fracture_streamer"},
    "texture": {"glue-chip": "glue_chip", "gluechip": "glue_chip", "water glass": "waterglass", "hammer": "hammered", "reed": "reeded", "rippled": "ripple"},
    "effect": {"iridescent": "iridized", "irid": "iridized", "seeds": "seedy", "bubbles": "seedy", "dichro": "dichroic"},
    "color_family": {"yellow": "amber", "gray": "grey", "violet": "purple", "aqua": "teal", "turquoise": "teal", "multicolor": "multi", "multicolored": "multi"},
}

# Field types: "text" (<=200 chars), "longtext" (<=10000), "enum:<vocab>",
# "multi:<vocab>" (list of slugs), "date" (YYYY-MM-DD), "number" (finite, >= 0),
# "inches" (decimal inches > 0), "hex" (#rrggbb), "tags" (list of words),
# "bool", "ref:<kind>" (id of an existing record of that kind).
# "name" is the only required field of every kind.
SCHEMAS = {
    "glass": [
        ("name", "text"), ("brand", "text"), ("product_code", "text"), ("vendor", "text"),
        ("purchase_date", "date"), ("color_family", "enum:color_family"), ("color_hex", "hex"),
        ("accent_families", "multi:color_family"), ("base_type", "enum:base_type"),
        ("color_mix", "enum:color_mix"), ("texture", "enum:texture"), ("effects", "multi:effect"),
        ("coe", "enum:coe"), ("thickness_mm", "number"), ("location", "text"),
        ("description", "longtext"), ("tags", "tags"), ("reorder", "bool"),
    ],
    "pattern": [
        ("name", "text"), ("designer", "text"), ("source", "enum:pattern_source"),
        ("book_title", "text"), ("book_page", "text"), ("url", "text"),
        ("width_in", "inches"), ("height_in", "inches"), ("piece_count", "number"),
        ("difficulty", "number"), ("technique", "enum:technique"),
        ("description", "longtext"), ("tags", "tags"),
    ],
    "project": [
        ("name", "text"), ("status", "enum:project_status"), ("technique", "enum:technique"),
        ("pattern_id", "ref:pattern"), ("started", "date"), ("finished", "date"),
        ("width_in", "inches"), ("height_in", "inches"), ("description", "longtext"),
        ("tags", "tags"),
    ],
    "foil": [
        ("name", "text"), ("brand", "text"), ("width_in", "inches"),
        ("backing", "enum:foil_backing"), ("thickness_mil", "number"),
        ("edge", "enum:foil_edge"), ("roll_length_ft", "number"), ("rolls", "number"),
        ("vendor", "text"), ("purchase_date", "date"), ("location", "text"),
        ("reorder", "bool"), ("notes", "longtext"),
    ],
    "solder": [
        ("name", "text"), ("brand", "text"), ("alloy", "enum:solder_alloy"),
        ("alloy_detail", "text"), ("core", "enum:solder_core"), ("diameter_in", "inches"),
        ("spool_lb", "number"), ("spools", "number"), ("vendor", "text"),
        ("purchase_date", "date"), ("location", "text"), ("reorder", "bool"),
        ("notes", "longtext"),
    ],
    "came": [
        ("name", "text"), ("brand", "text"), ("material", "enum:came_material"),
        ("profile", "enum:came_profile"), ("face", "enum:came_face"),
        ("face_width_in", "inches"), ("heart_in", "inches"), ("length_ft", "number"),
        ("strips", "number"), ("vendor", "text"), ("purchase_date", "date"),
        ("location", "text"), ("reorder", "bool"), ("notes", "longtext"),
    ],
    "supply": [
        ("name", "text"), ("category", "enum:supply_category"), ("brand", "text"),
        ("quantity", "number"), ("unit", "text"), ("vendor", "text"),
        ("purchase_date", "date"), ("location", "text"), ("reorder", "bool"),
        ("notes", "longtext"),
    ],
}
# URL segment for each kind: /api/<plural>, /<plural> pages.
PLURAL = {"glass": "glass", "pattern": "patterns", "project": "projects", "foil": "foil",
          "solder": "solder", "came": "came", "supply": "supplies"}
```

## Test fixture and helpers (copy verbatim)

Every acceptance test that needs data uses this fixture, loaded into a fresh
`make_app(<new temp dir>)` with `load_fixture`, which gives ids 1 to 9 in
order. `call` drives the app in-process; tests never open a socket.

```python
L = lambda *p: [{"size_class": s, "count": c} for s, c in p]
GLASS = [
 dict(name="Cobalt Blue Cathedral", brand="Oceanside", product_code="FX-101", vendor="Delphi", color_family="blue", color_hex="#1f3fbf", base_type="cathedral", color_mix="solid", texture="smooth", effects=[], accent_families=[], coe="not_fusible", purchase_date="2026-03-14", tags=["sky"], lots=L(("full_sheet", 1), ("scrap", 2))),
 dict(name="Wispy White Opal", brand="Wissmach", product_code="FX-102", vendor="Local shop", color_family="white", color_hex="#f2f0ea", base_type="opalescent", color_mix="wispy", texture="smooth", effects=[], accent_families=["clear"], coe="not_fusible", purchase_date="2025-11-02", tags=["clouds"], lots=L(("half_sheet", 1))),
 dict(name="Amber Granite", brand="Kokomo", product_code="FX-103", vendor="Delphi", color_family="amber", color_hex="#c8811e", base_type="cathedral", color_mix="solid", texture="granite", effects=[], accent_families=[], coe="not_fusible", purchase_date=None, tags=[], lots=[]),
 dict(name="Teal Ring Mottle", brand="Youghiogheny", product_code="FX-104", vendor="Delphi", color_family="teal", color_hex="#1e8a8a", base_type="opalescent", color_mix="ring_mottle", texture="smooth", effects=[], accent_families=["blue", "green"], coe="not_fusible", purchase_date="2026-06-30", tags=["water"], lots=L(("half_sheet", 1), ("quarter_sheet", 1))),
 dict(name="Red Streaky Iridized", brand="Youghiogheny", product_code="FX-105", vendor="Online", color_family="red", color_hex="#b3122e", base_type="cathedral", color_mix="streaky", texture="smooth", effects=["iridized"], accent_families=["orange"], coe="96", purchase_date="2026-06-30", tags=[], lots=L(("scrap", 3))),
 dict(name="Clear Seedy", brand="Kokomo", product_code="FX-106", vendor="Local shop", color_family="clear", color_hex="#e8eef0", base_type="cathedral", color_mix="solid", texture="smooth", effects=["seedy"], accent_families=[], coe="not_fusible", purchase_date="2024-08-09", tags=["background"], lots=L(("full_sheet", 1))),
 dict(name="Plum Fracture-Streamer", brand="Bullseye", product_code="FX-107", vendor="Online", color_family="purple", color_hex="#5b2a6e", base_type="cathedral", color_mix="fracture_streamer", texture="smooth", effects=[], accent_families=["black"], coe="90", purchase_date="2026-01-20", tags=[], lots=L(("half_sheet", 1))),
 dict(name="Green Glue Chip", brand="Kokomo", product_code="FX-108", vendor="Delphi", color_family="green", color_hex="#3f8f3a", base_type="cathedral", color_mix="solid", texture="glue_chip", effects=[], accent_families=[], coe="not_fusible", purchase_date=None, tags=["leaves"], lots=L(("scrap", 2))),
 dict(name="Black Opal", brand="Wissmach", product_code="FX-109", vendor="Local shop", color_family="black", color_hex="#151515", base_type="opalescent", color_mix="solid", texture="smooth", effects=[], accent_families=[], coe="not_fusible", purchase_date="2025-05-05", tags=["outline"], lots=L(("full_sheet", 1))),
]


def call(app, method, path, body=None, headers=None):
    """Drive the WSGI app in-process. body: dict -> JSON, bytes -> raw.
    Returns (status_int, headers_dict_lowercased, body_bytes)."""
    import io, json
    from wsgiref.util import setup_testing_defaults
    path, _, qs = path.partition("?")
    data = b""
    env = {}
    setup_testing_defaults(env)
    env.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING=qs)
    if isinstance(body, dict):
        data = json.dumps(body).encode()
        env["CONTENT_TYPE"] = "application/json"
    elif isinstance(body, bytes):
        data = body
    for k, v in (headers or {}).items():
        if k.lower() == "content-type":
            env["CONTENT_TYPE"] = v
        else:
            env["HTTP_" + k.upper().replace("-", "_")] = v
    env["CONTENT_LENGTH"] = str(len(data))
    env["wsgi.input"] = io.BytesIO(data)
    out = {}
    def start_response(status, hdrs, exc_info=None):
        out["status"] = int(status.split()[0])
        out["headers"] = {k.lower(): v for k, v in hdrs}
    chunks = app(env, start_response)
    payload = b"".join(chunks)
    if hasattr(chunks, "close"):
        chunks.close()
    return out["status"], out["headers"], payload


def load_fixture(app):
    """Create the nine glass items in order (ids 1..9) and their lots."""
    for g in GLASS:
        fields = {k: v for k, v in g.items() if k != "lots"}
        st, _, b = call(app, "POST", "/api/glass", fields)
        assert st == 201, (st, b)
        gid = __import__("json").loads(b)["id"]
        for lot in g["lots"]:
            st, _, b = call(app, "POST", f"/api/glass/{gid}/lots", lot)
            assert st == 201, (st, b)
```

## Interfaces

### Pure helpers

- `hex_to_hsl(h: str) -> tuple[float, float, float]` - hue in degrees
  [0, 360), saturation and lightness in [0, 1], via `colorsys.rgb_to_hls`.
  `h` must match `#rrggbb` (either case), else `ValueError`.
- `hue_key(h: str) -> tuple` - the sort key for browsing by colour. With
  `hue, s, l = hex_to_hsl(h)`:
  - a neutral (s < 0.15, or l < 0.10, or l > 0.92) gives
    `(1, round(l, 6), 0.0)`;
  - otherwise it gives `(0, round((hue + 15) % 360, 6), round(l, 6))`.
  The +15 keeps deep reds (hue 345-360) with the other reds. Neutrals sort
  after all colours, dark to light.
- `parse_inches(s) -> float` - accepts `"7/32"`, `"1/4\""`, `"0.25"`,
  `"1 1/2"`, `"3/8 in"`, `"2"` and numbers. Strip `"`, `in`, `inch` and
  `inches`. The value must be > 0, else `ValueError`, as are a zero
  denominator and anything else.
- `format_inches(x: float) -> str` - round to the nearest 1/64 and reduce:
  `0.21875 -> '7/32"'`, `1.5 -> '1 1/2"'`, `2.0 -> '2"'`. `ValueError` if
  the rounded value is <= 0.
- `stock_summary(lots: list[dict]) -> str` - totals `count` per
  `size_class` in VOCAB order, skips zeros, and joins with `" · "`, using
  singular or plural nouns: `"1 full sheet · 2 scraps"`. The nouns are full
  sheet(s), half sheet(s), quarter sheet(s), scrap(s) and other piece(s). No
  stock gives `"out of stock"`. An unknown size_class or a negative count
  raises `ValueError`.
- `normalize_suggestions(raw) -> dict` - cleans a vision model's reply. A
  non-dict gives `{}`.
  - Single fields `color_family`, `base_type`, `color_mix`, `texture` and
    `coe` are kept only if the value maps to a slug. A value maps if,
    lower-cased with spaces and hyphens turned into `_`, it is a slug, or
    if its lower-cased form is a SYNONYMS key for that vocabulary.
  - List fields `effects` (vocabulary `effect`) and `accent_families`
    (vocabulary `color_family`) keep the mapped values in order, without
    duplicates. Non-strings are dropped, and an empty result drops the key.
  - `color_hex` is kept if it matches `#?[0-9a-fA-F]{6}` once stripped,
    normalised to lower case with a `#`.
  - `name` (at most 200 characters) and `description` (at most 2000) are
    kept if non-blank, with runs of whitespace collapsed to one space.
  - Every other key is dropped.

### Storage - `Store(data_dir: str)`

SQLite at `<data_dir>/inventory.db`, media under `<data_dir>/media/`, both
created if missing. Opening an existing store keeps its data. Each kind in
SCHEMAS gets its own table. Glass also has lots; projects link to glass
(project_glass: project_id, glass_id, note) and to a pattern via
`pattern_id`; photos belong to one record of any kind.

`ValidationError(field: str, message: str)` subclasses `ValueError`; every
validation failure raises it.

Validation, applied on create and update:
- an unknown field;
- a missing or blank `name` on create;
- text over its length limit;
- an enum value that is not a slug, or a multi value that is not a list of
  slugs (duplicates are dropped, order kept);
- a date that is not a real `YYYY-MM-DD`;
- a number that is not finite or is below 0, and `difficulty` outside 1-5;
- inches that `parse_inches` rejects (a number > 0 is also accepted; stored
  as a float);
- a hex that is not `#?rrggbb` (stored lower case with `#`);
- tags that are not a list of strings or a comma-separated string (stored
  trimmed, lower case, without duplicates, as a list);
- a bool that is not true/false;
- a ref to an id that does not exist.

Records are dicts with `id` plus every schema field. Unset fields are
`None`, or `[]` for multi and tags fields.

- `create(kind, data) -> int`
- `get(kind, id) -> dict | None`. It also includes:
  - `photos`: a list of `{"id", "caption", "primary", "is_document", "full_url", "thumb_url"}`,
    primary first, then in upload order;
  - `thumb_url`: the primary photo's thumb_url, or None.

  Glass also gets `lots` (each `{"id", "size_class", "count", "width_in", "height_in", "location", "price_paid", "note"}`),
  `stock_summary` and `projects` (`{"id", "name", "status", "thumb_url"}`).
  A pattern also gets `projects`; a project also gets `glass`
  (`{"id", "name", "thumb_url", "note"}`).
- `update(kind, id, data) -> None`: a partial update. Only the fields given
  change; `None` clears a field except `name`. An unknown id raises
  `KeyError`.
- `delete(kind, id) -> None`: removes the record, its photos (and their
  files), its lots and its project links. Deleting a pattern sets
  `pattern_id` to None on projects that used it.
- `query(kind, params: dict[str, list[str]]) -> list[dict]`: summaries
  (`id`, `name`, `thumb_url`; glass also `color_hex`, `color_family` and
  `stock_summary`) in result order:
  - `q`: every whitespace-separated word must appear, case-insensitive, in
    the record's text, longtext and tags fields combined.
  - any enum field name: OR within the field (`color_family=red&color_family=purple`);
    AND across fields;
  - any multi field name: the record matches if it has ANY of the given
    values;
  - `reorder=1`: only records with reorder true;
  - glass only: `in_stock=1` (total lot count > 0) or `in_stock=0` (none);
  - `sort`: `added` (default, newest id first), `name`, `brand` (then name),
    `purchased` (purchase_date newest first, missing dates last); glass also
    has `hue` (by `hue_key` of color_hex, missing colours last) and
    `quantity` (total lot count, highest first). Ties go to the lower id. An
    unknown sort raises ValidationError.

  Unknown parameter names are ignored.
- `add_lot(glass_id, data) -> int`, `update_lot(lot_id, data)`,
  `delete_lot(lot_id)`. Lot fields are `size_class` (required),
  `count` (integer >= 0, required), `width_in`, `height_in` (inches),
  `location`, `price_paid` (number) and `note`.
- `link_glass(project_id, glass_id, note=None)` (re-linking updates the
  note) and `unlink_glass(project_id, glass_id)`.
- Photos:
  - `add_photo(kind, id, data: bytes, content_type: str, caption=None) -> int`
    accepts JPEG (`FF D8 FF`), PNG (`89 50 4E 47 0D 0A 1A 0A`) and WebP
    (`RIFF....WEBP`) for every kind, and PDF (`%PDF-`) for patterns only,
    with `is_document` true. The bytes decide, not the header; anything
    else raises `ValidationError("photo", ...)`. A record's first photo
    becomes primary.
  - `set_thumb(photo_id, data, content_type)`: JPEG, PNG or WebP only.
  - `set_primary(photo_id)`.
  - `delete_photo(photo_id)`: deletes the files. If it was primary, the next
    photo by upload order becomes primary.
  - `media_path(photo_id, variant) -> (path, content_type)`, where variant
    is "full" or "thumb". A photo with no thumb serves its full file for
    "thumb".
- `export_zip() -> bytes`: a zip holding `inventory.json` (every kind's
  records with their lots, links and photo metadata, under
  `{"version": 1, "records": {<kind>: [...]}}`) and every media file
  under `media/`.

### HTTP - `make_app(data_dir, describer=None, password=None, max_upload=25*1024*1024)`

Returns a WSGI callable over a `Store(data_dir)`.

JSON API. Request bodies are JSON unless noted; errors are
`{"error": message, "field": name-or-null}`:
- `GET /api/meta` -> `{"vocab": VOCAB, "schemas": SCHEMAS, "plural": PLURAL, "vision": bool}`.
- `GET /api/<plural>?<params>` -> `{"items": [...], "total": n}` (Store.query).
- `POST /api/<plural>` -> 201 `{"id"}`. `GET` / `PUT` / `DELETE`
  `/api/<plural>/<id>` -> 200 (the record, the updated record, or `{"ok": true}`).
- `POST /api/glass/<id>/lots` -> 201 `{"id"}`; `PUT` / `DELETE /api/lots/<lot_id>`.
- `POST /api/projects/<id>/glass` with `{"glass_id", "note"?}` -> 201;
  `DELETE /api/projects/<id>/glass/<glass_id>`.
- `POST /api/<plural>/<id>/photos[?caption=...]` with the raw file as the
  body -> 201 `{"id", "full_url", "thumb_url"}`.
  `POST /api/photos/<pid>/thumb` (raw body);
  `PUT /api/photos/<pid>` with `{"primary": true, "caption"?}`;
  `DELETE /api/photos/<pid>`.
- `GET /media/<pid>/full`, `GET /media/<pid>/thumb` -> the bytes, with the
  stored Content-Type and `Cache-Control: max-age=86400`.
- `POST /api/intake/describe` with `{"photo_id"}`:
  - 200 `{"suggestions": normalize_suggestions(describer(bytes, content_type))}`;
  - 503 if `describer` is None;
  - 502 with the error text if the describer raises.
- `GET /api/export.zip` -> `application/zip`.
- `GET /healthz` -> 200 `ok` (never needs a password).

Status codes:
- 400: ValidationError, or JSON that will not parse.
- 404: an unknown route or id.
- 405: a known route with the wrong method.
- 413: a body over `max_upload`. Check it before reading the body.
- 415: a file the store rejects.

If `password` is set, every route except `/healthz` needs HTTP Basic auth
with that password (any user name), compared with `hmac.compare_digest`.
Otherwise the response is 401 with `WWW-Authenticate: Basic realm="glassinv"`.

HTML pages. All are server-rendered, `text/html; charset=utf-8`, and share
one layout:
- `/`: a dashboard with
  - counts per kind and the number of glass items in stock;
  - a palette band of every in-stock glass colour in `hue` order (one
    swatch per item);
  - the 8 most recently added glass thumbnails;
  - every record of any kind flagged `reorder`.
- `/glass`: the browser. It takes the same parameters as the API plus
  `view` = `grid` (default) | `cards` | `list` | `swatch` and
  `size` = `s` | `m` (default) | `l` for grid tiles. The results sit in one
  element with `data-view="<view>"`, and each item is an element with
  `data-id="<id>"` in result order.
  - `swatch` always sorts by hue, ignoring `sort`.
  - `list` renders a `<table>` whose column headers are sort links.
  - A filter panel lists each enum and multi field's values with the count
    of matching items in the current results. Every control is a plain link
    or GET form, so any view is a bookmarkable URL.
- `/glass/<id>`: a detail page with:
  - the primary photo large and the other photos as thumbnails;
  - a light/dark backdrop toggle, because glass looks different lit from
    behind than on a dark ground;
  - every field, using VOCAB labels and `format_inches`;
  - the lots table with + and - count buttons;
  - "Projects used in", switchable between thumbnails and a list.
- `/glass/new`: photo-first intake. Choose or capture a photo
  (`<input type="file" accept="image/*" capture="environment">`); the
  browser makes the thumbnail and dominant colour and uploads both. If
  `/api/meta` says vision is on, it fetches suggestions and pre-fills the
  form, marking each pre-filled field "suggested" until the user changes
  or confirms it. Then save.
- `/glass/compare?ids=1,4,7`: the chosen pieces' large photos side by side
  on a light-table backdrop, for planning a project's palette.
- `/patterns`, `/projects`, `/supplies` (with tabs for foil, solder, came
  and other supplies), each with a detail page and new/edit forms built from
  SCHEMAS. Patterns and projects support the grid, cards and list views.
  Pattern PDFs show as a document icon that opens the file.
- `GET /static/app.css` (`text/css`) and `/static/app.js`
  (`text/javascript`). The JS defines:
  - `makeThumbnail(file)`: canvas, longest edge 480 px, JPEG quality 0.8;
  - `dominantColor(file)`: the average of a 64x64 downsample, skipping
    near-white and near-black pixels, returned as `#rrggbb`;
  - the upload, intake and view-toggle code.

### Look and feel

The theme is "leaded light": the app should feel like a well-made panel.
- **Grid view:** tiles sit edge to edge, separated by 6 px gutters in a
  near-black "lead" colour (`--lead: #1d1f22`), so a full grid reads as a
  leaded window. Each tile has a faint inner highlight, as if lit from
  behind.
- **Palette:**
  - warm paper background `--paper: #f4f1ea` and ink text `--ink: #22252a`;
  - pewter `--solder: #8a8f96` for rules and borders;
  - copper `--copper: #b06a3b` for accents and focus, plus patina
    `--patina: #4f6b5a`;
  - a dark mode via `prefers-color-scheme` that inverts paper and lead.
- **Type:** headings use the serif stack
  `"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif`,
  and the interface uses `system-ui, sans-serif`. Field labels are small
  letter-spaced capitals.
- **Swatch view** groups chips by colour family, each chip showing the
  item's color_hex with the photo on hover.
- **Mobile:** the layout works at phone width, because intake happens at
  the glass rack with a phone.

### Vision - `vision_describer(url, model, api_key=None, extra=None, post=None) -> callable`

Returns `describe(data: bytes, content_type: str) -> dict`. It sends
`POST <url>/chat/completions` (`url` ends at `/v1`) with JSON:
- `model`;
- `messages`: a system message listing the JSON keys and every allowed
  value from VOCAB, then a user message whose `content` is a text part and
  an `image_url` part with `url` = `data:<content_type>;base64,<data>`;
- `temperature` 0.2 and `max_tokens` 600;
- every key of `extra` merged in at the top level.

The request carries `Authorization: Bearer <api_key>` when a key is given.
`post(url, body: dict, headers: dict) -> dict` defaults to a urllib JSON
POST with a 120 s timeout. The reply's
`choices[0].message.content` is parsed by taking the text from the first
`{` to the last `}` as JSON, so fences and prose around it are fine. A
reply that does not parse gives `{}`.

### Running - `config_from_env(env: dict) -> dict` and `serve()`

`config_from_env` returns:
- `data_dir`: `GLASSINV_DATA`, default `/data`;
- `port`: `GLASSINV_PORT`, int, default 8090;
- `password`: `GLASSINV_PASSWORD`, None if unset or empty;
- `max_upload`: `GLASSINV_MAX_UPLOAD_MB` x 1048576, default 25 MB;
- `vision`: None unless `GLASSINV_VISION_URL` is set. Then it is
  `{"url", "model": GLASSINV_VISION_MODEL or "local", "api_key", "extra"}`,
  where the api_key is read and stripped from the file named by
  `GLASSINV_VISION_KEY_FILE` (None if unset), and `extra` is
  `json.loads(GLASSINV_VISION_EXTRA)` (None if unset).

`serve()` builds the config from `os.environ`, makes the app and runs a
threading `wsgiref` server on `0.0.0.0:<port>` forever.

### Deployment - `build_outputs(out_dir: str) -> list[str]`

Writes four files into `out_dir` and returns their paths sorted:
- `glassinv.py`: this module's exact source (read `__file__`).
- `Dockerfile`:
  - `FROM python:3.12-slim`;
  - a non-root user with uid 1000, `WORKDIR /app`, then copy `glassinv.py`;
  - `ENV GLASSINV_DATA=/data` and `EXPOSE 8090`;
  - a `HEALTHCHECK` that runs python urllib against
    `http://127.0.0.1:8090/healthz`;
  - `USER` set to that user;
  - `CMD ["python", "-c", "import glassinv; glassinv.serve()"]`.
- `compose.yaml`: one service, `glassinv`, with:
  - `build: .`, ports `"8090:8090"`, the volume `./data:/data` and
    `restart: unless-stopped`;
  - commented-out vision settings for Gemini: `GLASSINV_VISION_URL` set to
    `http://192.168.50.96:8080/v1`, `GLASSINV_VISION_EXTRA` set to
    `{"chat_template_kwargs": {"reasoning_effort": "low"}}`, and the key file
    mounted read-only from `~/.llama-api-key`.
- `README.md`: build and run commands; where the data lives and that it is
  what to back up; how to turn vision on; that the app has no login unless
  `GLASSINV_PASSWORD` is set, so keep it on the LAN.

## Acceptance

Floats compare within 1e-6. "Fixture app" means
`app = make_app(tempfile.mkdtemp()); load_fixture(app)`.

Helpers
1. `hex_to_hsl("#38bdf8")` is (198.4375, 0.932039, 0.596078) within 1e-6
   on each part. `hex_to_hsl("38bdf8")` and `hex_to_hsl("#38bdf")` raise
   `ValueError`.
2. `hue_key("#b3122e") == (0, 4.565217, 0.386275)`,
   `hue_key("#f2f0ea") == (1, 0.933333, 0.0)` and
   `hue_key("#151515") == (1, 0.082353, 0.0)`.
3. `parse_inches` gives 0.21875 for `"7/32"`, 0.25 for `'1/4"'` and for
   `"0.25"`, 1.5 for `"1 1/2"`, 0.375 for `"3/8 in"` and 2.0 for `"2"`. It
   raises `ValueError` for `"0"`, `"1/0"`, `"abc"`, `""` and `"-1/4"`.
4. `format_inches` gives `'7/32"'` for 0.21875, `'1 1/2"'` for 1.5, `'2"'`
   for 2.0 and `'1/8"'` for 0.126. It raises `ValueError` for 0.001.
5. `stock_summary`:
   - `[{"size_class":"full_sheet","count":1}, {"size_class":"scrap","count":2}, {"size_class":"full_sheet","count":1}]`
     gives `"2 full sheets · 2 scraps"`;
   - `[]` and `[{"size_class":"half_sheet","count":0}]` give
     `"out of stock"`;
   - `[{"size_class":"slab","count":1}]` raises `ValueError`.
6. `normalize_suggestions({"color_family":"Blue","base_type":"Opal","color_mix":"Mottled","texture":"Glue-Chip","coe":"96","effects":["Iridescent","sparkle","plaid",3],"accent_families":["gray","Teal","gray"],"color_hex":"1E8A8A","name":"  Teal   ring mottle ","description":"x","price":5})`
   is exactly
   `{"color_family":"blue","base_type":"opalescent","color_mix":"mottle","texture":"glue_chip","coe":"96","effects":["iridized","sparkle"],"accent_families":["grey","teal"],"color_hex":"#1e8a8a","name":"Teal ring mottle","description":"x"}`.
   Both `normalize_suggestions({"color_family":"chartreuse","texture":None,"color_hex":"#12345"})`
   and `normalize_suggestions("nope")` give `{}`.

Records
7. On the fixture app, `GET /api/glass/1` returns name "Cobalt Blue
   Cathedral", tags `["sky"]`, 2 lots, `stock_summary` "1 full sheet ·
   2 scraps", `photos` `[]`, `thumb_url` None and `projects` `[]`.
8. The nine records' stock_summary values, ids 1 to 9 in order, are:
   "1 full sheet · 2 scraps", "1 half sheet", "out of stock",
   "1 half sheet · 1 quarter sheet", "3 scraps", "1 full sheet",
   "1 half sheet", "2 scraps", "1 full sheet".
9. `POST /api/glass` returns 400, with `field` naming the bad field, for:
   - `{"name":""}`;
   - `{"name":"x","color_family":"chartreuse"}`;
   - `{"name":"x","purchase_date":"2026-02-30"}`;
   - `{"name":"x","color_hex":"blue"}`;
   - `{"name":"x","shape":"round"}`;
   - `{"name":"x","effects":"iridized"}`;
   - `{"name":"x","thickness_mm":-1}`.

   A body that is not JSON returns 400 with field null.
10. `PUT /api/glass/2` with `{"vendor": null, "tags": "Clouds, Sky ,clouds"}`
    returns vendor None and tags `["clouds","sky"]`; the name is unchanged.
    `PUT /api/glass/99` returns 404.
11. A pattern created with `{"name":"Tulip","width_in":"10 1/2","difficulty":3}`
    reads back width_in 10.5. `{"name":"x","difficulty":6}` returns 400.
    A project with `{"name":"P","pattern_id":99}` returns 400.
12. Foil `{"name":"Venture 7/32 black","width_in":"7/32","backing":"black","thickness_mil":1.25,"rolls":2}`,
    solder `{"name":"60/40 1lb","alloy":"60_40","core":"solid","diameter_in":"1/8","spool_lb":1,"spools":3}`,
    came `{"name":"1/4 H lead","material":"lead","profile":"h","face":"round","face_width_in":"1/4","length_ft":6,"strips":4}`
    and supply `{"name":"Black patina","category":"patina","quantity":8,"unit":"oz"}`
    all return 201 and read back with width_in 0.21875, diameter_in 0.125
    and face_width_in 0.25.

Queries (fixture app, `GET /api/glass?...`, comparing the `id`s in order)
13. `color_family=blue` -> [1]. `color_family=red&color_family=purple`
    -> [7, 5]. `coe=96` -> [5]. `texture=glue_chip&texture=granite`
    -> [8, 3].
14. `base_type=opalescent&in_stock=1` -> [9, 4, 2]. `in_stock=0` -> [3].
    `effects=iridized` -> [5]. `accent_families=blue` -> [4].
15. `q=kokomo` -> [8, 6, 3]. `q=mottle` -> [4]. `q=leaves` -> [8].
    `q=delphi%20sky` -> [1]. `q=Youghiogheny&sort=name` -> [5, 4].
16. Sorts over everything:
    - `sort=added` (and no sort) -> [9, 8, 7, 6, 5, 4, 3, 2, 1]
    - `sort=name` -> [3, 9, 6, 1, 8, 7, 5, 4, 2]
    - `sort=brand` -> [7, 3, 6, 8, 1, 9, 2, 5, 4]
    - `sort=hue` -> [5, 3, 8, 4, 1, 7, 9, 6, 2]
    - `sort=quantity` -> [1, 5, 4, 8, 2, 6, 7, 9, 3]
    - `sort=purchased` -> [4, 5, 1, 7, 2, 9, 6, 3, 8]
17. `sort=sideways` returns 400. `nonsense=1` is ignored (all nine,
    newest first). Every result's `total` equals the length of its `items`.
18. After `PUT /api/glass/3` with `{"reorder": true}`, `reorder=1` gives [3].

Lots, projects, photos
19. `POST /api/glass/3/lots` with `{"size_class":"scrap","count":4}` makes
    glass 3's stock_summary "4 scraps", and `in_stock=0` then gives [].
    `PUT /api/lots/<that id>` with `{"count":0}` makes it "out of stock"
    again. A lot with size_class "slab", or with count -1, returns 400.
20. On the fixture app, create a pattern "Iris", then a project
    `{"name":"Window","pattern_id":<iris id>,"status":"in_progress"}`.
    Link glass 4 (note "petals") and glass 1.
    - `GET /api/glass/4` lists that project in `projects`.
    - The project's `glass` holds ids {4, 1}, and glass 4's note is
      "petals".
    - The pattern's `projects` holds the project.
    - Deleting the pattern leaves the project with pattern_id None.
    - Deleting the project empties glass 4's `projects`.
21. Photos (with JPEG = `b"\xff\xd8\xff\xe0" + b"0" * 100`, PNG =
    `b"\x89PNG\r\n\x1a\n" + b"0" * 100`):
    - uploading JPEG then PNG to glass 1 returns 201 twice;
    - the record's `photos` has 2 entries, the first (the JPEG) primary,
      and `thumb_url` equals the first photo's thumb_url;
    - `GET` of that thumb_url returns the JPEG bytes (no thumb uploaded
      yet), with Content-Type `image/jpeg` and a Cache-Control header.
22. Then:
    - `POST /api/photos/<jpeg id>/thumb` with PNG bytes and
      `content-type: image/png` makes `/media/<jpeg id>/thumb` return the
      PNG bytes;
    - `PUT /api/photos/<png id>` with `{"primary": true}` makes it
      primary;
    - deleting it makes the JPEG primary again, and its files are gone
      from `<data_dir>/media`.
23. Uploads that fail:
    - plain text to glass 1 -> 415;
    - a PDF (`b"%PDF-1.7 ..."`) to glass 1 -> 415;
    - the same PDF to a pattern -> 201 with `is_document` true;
    - upload to `/api/glass/99/photos` -> 404;
    - with `make_app(dir, max_upload=1000)`, a 2000-byte JPEG -> 413.
24. `DELETE /api/glass/1` (with its photos) returns 200; `GET /api/glass/1`
    then returns 404, and no files for its photos remain.

HTML and static
25. On the fixture app:
    - `GET /glass` returns 200, text/html, an element with
      `data-view="grid"`, and `data-id` values in the order
      [9, 8, 7, 6, 5, 4, 3, 2, 1];
    - `GET /glass?view=swatch&sort=name` lists `data-id` in hue order
      [5, 3, 8, 4, 1, 7, 9, 6, 2];
    - `GET /glass?view=list&sort=name` contains a `<table>` and lists
      [3, 9, 6, 1, 8, 7, 5, 4, 2].
26. `GET /glass?color_family=blue&view=cards` lists only `data-id="1"`.
    `GET /glass/4` contains "Teal Ring Mottle", "Ring mottle" (the VOCAB
    label) and "1 half sheet · 1 quarter sheet". `GET /glass/99` returns
    404.
27. A glass named `<script>alert(1)</script>` appears on `/glass` as
    `&lt;script&gt;alert(1)&lt;/script&gt;`, and the raw
    `<script>alert(1)` appears nowhere in the page.
28. Every one of these returns 200 HTML: `/`, `/glass/new`,
    `/glass/compare?ids=1,4,7`, `/patterns`, `/projects`, `/supplies`.
    `GET /` contains `data-id` for glass 9, the newest.
29. `GET /static/app.css` is `text/css`. `GET /static/app.js` is
    `text/javascript` and contains `function makeThumbnail` and
    `function dominantColor`. `GET /api/meta` returns `vocab` equal to
    VOCAB, `schemas` covering all seven kinds, and `"vision": false`.

Vision
30. With `make_app(dir, describer=lambda b, ct: {"color_family":"Teal","texture":"rippled","junk":1})`,
    upload a JPEG to a new glass. `POST /api/intake/describe` with that
    photo id returns 200
    `{"suggestions": {"color_family":"teal","texture":"ripple"}}`, and
    `/api/meta` says `"vision": true`.
    - Without a describer the same call returns 503.
    - A describer that raises `RuntimeError("down")` gives 502.
    - An unknown photo id gives 404.
31. `vision_describer("http://v/v1", "m", api_key="k", extra={"chat_template_kwargs":{"reasoning_effort":"low"}}, post=fake)`
    called on `(JPEG, "image/jpeg")`:
    - `fake` gets the url `http://v/v1/chat/completions`;
    - the headers include `Authorization: Bearer k`;
    - the body has model "m", temperature 0.2, max_tokens 600 and
      `chat_template_kwargs` at the top level;
    - the last message has an `image_url` part whose url starts
      `data:image/jpeg;base64,/9j/`.

    When `fake` returns content `"Sure!\n```json\n{\"color_family\": \"blue\"}\n```"`
    the result is `{"color_family": "blue"}`. Content `"no idea"` gives `{}`.

Auth, export, config, deployment
32. With `make_app(dir, password="pw")`:
    - `GET /glass` returns 401 with `WWW-Authenticate` containing `Basic`;
    - with `authorization: Basic dXNlcjpwdw==` (user:pw) it returns 200;
    - a wrong password returns 401;
    - `/healthz` returns 200 without credentials.
33. On the fixture app with one JPEG uploaded to glass 2,
    `GET /api/export.zip` returns `application/zip`. Its `inventory.json`
    has `version` 1, nine glass records (record 2 with its lot and one
    photo entry), and exactly one file under `media/`.
34. `config_from_env({})` gives data_dir "/data", port 8090, password None,
    max_upload 26214400 and vision None. With
    `GLASSINV_PORT=9000`, `GLASSINV_VISION_URL=http://h/v1`,
    `GLASSINV_VISION_EXTRA='{"a":1}'` and
    `GLASSINV_VISION_KEY_FILE` pointing to a /tmp file containing
    `"secret\n"`, the config has port 9000 and vision
    `{"url":"http://h/v1","model":"local","api_key":"secret","extra":{"a":1}}`.
35. `build_outputs("/tmp/out_glass")` returns the four sorted paths.
    - `glassinv.py` equals the module's own source.
    - `Dockerfile` contains `FROM python:3.12-slim`, `EXPOSE 8090`,
      `HEALTHCHECK`, `USER` and
      `CMD ["python", "-c", "import glassinv; glassinv.serve()"]`.
    - `compose.yaml` contains `8090:8090`, `./data:/data` and
      `unless-stopped`.
    - `README.md` mentions `GLASSINV_PASSWORD`.
36. Data survives a restart: create a glass, then build a second
    `make_app` on the same directory, and `GET` returns the same record.

## Chunks (optional)

This spec is larger than forge's earlier ones. Suggested split:
- helpers and vocabulary;
- schema validation;
- Store CRUD, lots and links;
- Store query;
- photos and export;
- the WSGI router and API;
- HTML pages (layout, glass browser and detail pages);
- HTML for the other kinds plus the intake page;
- static CSS and JS;
- vision, config and serve;
- build_outputs.

Plan the query engine, validation, the router (status codes, auth, the
upload size check) and the HTML browser with thinking.

## Visual review

Claude's review sees code only; nothing here renders the HTML. Look for:
- every user string escaped;
- every view reachable as a plain link;
- the grid reading as a leaded panel (lead-coloured gutters, tiles edge to
  edge);
- no external URLs in the CSS or JS;
- a layout that holds at 375 px wide.
