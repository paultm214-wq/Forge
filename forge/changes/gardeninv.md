Derive: glassinv
New-project: gardeninv

<!--
A NEW project, built from glassinv's finished code. Run it AFTER
wendy-theme.md and claude-integration.md are both accepted, so the theme and
the Claude client carry over:
    forge change glassinv gardeninv.md --new-project gardeninv
(or drop this file in ~/forge/inbox/ - the two header lines above do the same).
forge maps each new chunk onto the glassinv chunk it most resembles and
seeds it with that code, so the store, router, photo handling, auth, theme
and Claude client are adapted rather than written again.

BEFORE YOU RUN IT - fill in every [[FILL IN: ...]]:
  1. Site title, subtitle and banner alt text (Theme).
  2. Wendy's and Rue's descriptions in the two Assets prompts (copy them from
     wendy-theme.md). The same photos in ~/forge/refs/wendy/ are reused.
  3. Garden facts (Garden settings): hardiness zone and average frost dates.
  4. City-level location and the monthly token budget (Ask Claude). As in
     glassinv: city, region, country, time zone only - never a street
     address.

Every computed value in Acceptance (colour order, month labels, seasons,
suggestion cleanup, harvest totals, lineage, dashboard lists, the Claude
prompt lines and every query over the fixture) was checked against a
reference implementation before this spec was written.

The weather module is a later change to this project; the dashboard's
"Cover before a freeze" list and the frost-tender fields are its hooks.
-->

# gardeninv

## Goal

Wendy is a master gardener in Central Texas with Texas native beds, a
vegetable garden and many houseplants. gardeninv is her garden notebook: every
plant with its photos through the seasons, where it grows, when it was
planted, what it was propagated from (seed, cutting, division, a nursery or a
friend), when it blooms or crops, what wildlife it feeds, and a journal of
plantings, harvests, pests and freeze damage. It also keeps her seed box and
garden supplies.

It is derived from glassinv and keeps that app's shape: one Python module,
SQLite, photo-first browsing with several views, phone-first intake with
optional vision suggestions, the Wendy-and-Rue theme, and optional Ask
Claude. What changes is the domain: glass, patterns, projects, foil, solder,
came and lots go away; plants, seeds, areas, journal entries and garden
supplies replace them.

The deliverable is the `gardeninv` module plus the files to run it in
Docker, written by `build_outputs`.

## Constraints

- Language / runtime: Python 3.12, standard library only. Storage is
  `sqlite3`; the server is `wsgiref` with a threading mix-in. Never import
  `cgi`: uploads are raw request bodies, not multipart forms.
- Module name: `gardeninv`. Port 8091 (glassinv keeps 8090, so both can run
  on one host). Every environment variable starts `GARDENINV_`.
- No network at test time. The only outbound calls are to the optional
  vision model and the optional Claude API, each through an injectable
  `post` function.
- No `if __name__ == "__main__"` block. The container starts the server with
  `python -c "import gardeninv; gardeninv.serve()"`.
- The server never decodes images. The browser makes the thumbnail and picks
  the dominant colour before upload; the server checks file signatures and
  stores bytes.
- No external fonts, scripts or CDNs. CSS and JS are served from strings
  inside the module.
- Escape every user-supplied string in HTML (`html.escape`).
- Copy the vocabulary/schema block and the test fixture below verbatim.
- Nothing from glassinv's glass domain survives: no `lots`, `stock_summary`,
  `project_glass`, glass routes or glass vocabularies.

## Vocabulary and schemas (copy verbatim)

The terms follow Texas extension usage:
- **Origin is separate from category.** "Texas native" and "adapted"
  (non-native but suited to the region without coddling) are the terms
  Texas native-plant programmes use.
- **Sun is hours of direct sun outdoors**; the two indoor values describe
  houseplant light.
- **Propagation says where the plant came from.** A plant grown from
  Wendy's own seed links to the seed record; a cutting or division links to
  its parent plant, so a plant's lineage can be followed back.
- **Months are the months it blooms or crops in this garden**, not a
  catalogue's range.
- **A journal entry** is one dated event, optionally about one plant;
  harvests carry a quantity and unit.

```python
# Controlled vocabularies: slug -> label. Order is display order.
VOCAB = {
    "category": {
        "perennial": "Flowering perennial", "wildflower": "Wildflower (annual / biennial)",
        "shrub": "Shrub", "tree": "Tree", "grass": "Grass", "vine": "Vine",
        "succulent": "Succulent / cactus", "groundcover": "Groundcover", "bulb": "Bulb",
        "vegetable": "Vegetable", "herb": "Herb", "fruit": "Fruit", "houseplant": "Houseplant",
        "other": "Other",
    },
    "origin": {
        "texas_native": "Texas native", "us_native": "Native to the US",
        "adapted": "Adapted (non-native, well suited)", "non_native": "Non-native",
        "unknown": "Unknown",
    },
    "lifecycle": {"annual": "Annual", "biennial": "Biennial", "perennial": "Perennial"},
    "sun": {
        "full_sun": "Full sun (6+ h)", "part_sun": "Part sun (4-6 h)",
        "part_shade": "Part shade (2-4 h)", "full_shade": "Shade",
        "bright_indirect": "Bright indirect (indoors)", "low_light": "Low light (indoors)",
    },
    "water": {"very_low": "Very low (rain only once established)", "low": "Low",
              "medium": "Medium", "high": "High"},
    "plant_status": {
        "planned": "Planned", "seed_started": "Seed started", "growing": "Growing",
        "dormant": "Dormant", "harvested": "Harvested / done for the season",
        "died": "Died", "removed": "Removed", "given_away": "Given away",
    },
    "propagation": {
        "seed": "Grown from seed", "cutting": "Cutting", "division": "Division",
        "layering": "Layering", "offset": "Offset / pup", "graft": "Graft",
        "nursery": "Bought (nursery)", "gift": "Gift / swap", "volunteer": "Volunteer / self-sown",
    },
    "wildlife": {
        "bees": "Bees", "butterflies": "Butterflies", "hummingbirds": "Hummingbirds",
        "moths": "Moths", "birds": "Birds (seed / berries)", "caterpillars": "Host plant (caterpillars)",
    },
    "month": {
        "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "may": "May", "jun": "Jun",
        "jul": "Jul", "aug": "Aug", "sep": "Sep", "oct": "Oct", "nov": "Nov", "dec": "Dec",
    },
    "color_family": {
        "red": "Red", "orange": "Orange", "yellow": "Yellow / gold", "green": "Green",
        "blue": "Blue", "purple": "Purple", "pink": "Pink", "white": "White",
        "brown": "Brown / bronze", "black": "Black / near-black", "multi": "Multicolor",
    },
    "seed_unit": {"packet": "Packets", "seeds": "Seeds", "grams": "Grams", "ounces": "Ounces"},
    "area_kind": {
        "bed": "Bed", "raised_bed": "Raised bed", "container": "Container", "meadow": "Meadow",
        "indoor": "Indoors", "greenhouse": "Greenhouse", "other": "Other",
    },
    "irrigation": {"rain_only": "Rain only", "hand": "Hand watering", "drip": "Drip", "sprinkler": "Sprinkler"},
    "entry_type": {
        "observation": "Observation", "planted": "Planted", "transplanted": "Transplanted",
        "bloom": "First bloom", "harvest": "Harvest", "pruned": "Pruned",
        "fertilized": "Fertilized", "pest": "Pest", "disease": "Disease",
        "frost_damage": "Frost / freeze damage", "heat_damage": "Heat / drought damage",
        "divided": "Divided", "propagated": "Propagated", "other": "Other",
    },
    "supply_category": {
        "soil": "Soil & amendments", "fertilizer": "Fertilizer", "mulch": "Mulch",
        "pots": "Pots & containers", "seed_starting": "Seed starting", "tools": "Tools",
        "irrigation": "Irrigation parts", "pest_control": "Pest control",
        "supports": "Stakes & supports", "other": "Other",
    },
}
# Words a vision model may use, mapped to slugs. Lower-case keys.
SYNONYMS = {
    "category": {"annual": "wildflower", "wild flower": "wildflower", "bush": "shrub",
                 "ornamental grass": "grass", "cactus": "succulent", "climber": "vine",
                 "veggie": "vegetable", "indoor plant": "houseplant", "house plant": "houseplant"},
    "lifecycle": {"perennials": "perennial", "annuals": "annual"},
    "sun": {"full sun": "full_sun", "sun": "full_sun", "partial sun": "part_sun",
            "part sun": "part_sun", "partial shade": "part_shade", "part shade": "part_shade",
            "shade": "full_shade", "full shade": "full_shade",
            "bright indirect light": "bright_indirect", "indirect light": "bright_indirect",
            "low light": "low_light"},
    "water": {"drought tolerant": "very_low", "dry": "low", "moderate": "medium",
              "average": "medium", "moist": "high", "wet": "high"},
    "color_family": {"gold": "yellow", "violet": "purple", "lavender": "purple",
                     "magenta": "pink", "gray": "white", "grey": "white", "maroon": "red",
                     "multicolor": "multi", "multicolored": "multi"},
}

# Field types: "text" (<=200 chars), "longtext" (<=10000), "enum:<vocab>",
# "multi:<vocab>" (list of slugs), "date" (YYYY-MM-DD), "number" (finite, >= 0),
# "degrees" (finite, -60 to 130, Fahrenheit), "inches" (decimal inches > 0),
# "hex" (#rrggbb), "tags" (list of words), "bool", "ref:<kind>" (id of an
# existing record of that kind).
# "name" is the only required field of every kind.
SCHEMAS = {
    "plant": [
        ("name", "text"), ("botanical_name", "text"), ("variety", "text"),
        ("category", "enum:category"), ("origin", "enum:origin"),
        ("lifecycle", "enum:lifecycle"), ("sun", "enum:sun"), ("water", "enum:water"),
        ("area_id", "ref:area"), ("spot", "text"), ("status", "enum:plant_status"),
        ("planted", "date"), ("propagation", "enum:propagation"),
        ("parent_id", "ref:plant"), ("seed_id", "ref:seed"), ("acquired_from", "text"),
        ("color_hex", "hex"), ("color_family", "enum:color_family"),
        ("bloom_months", "multi:month"), ("harvest_months", "multi:month"),
        ("wildlife", "multi:wildlife"), ("deer_resistant", "bool"),
        ("frost_tender", "bool"), ("min_temp_f", "degrees"), ("height_in", "inches"),
        ("spread_in", "inches"), ("favorite", "bool"), ("description", "longtext"),
        ("tags", "tags"),
    ],
    "seed": [
        ("name", "text"), ("botanical_name", "text"), ("variety", "text"),
        ("category", "enum:category"), ("source", "text"), ("packed_year", "number"),
        ("quantity", "number"), ("unit", "enum:seed_unit"), ("sow_months", "multi:month"),
        ("days_to_maturity", "number"), ("germination_days", "number"),
        ("saved", "bool"), ("location", "text"), ("reorder", "bool"),
        ("notes", "longtext"), ("tags", "tags"),
    ],
    "area": [
        ("name", "text"), ("kind", "enum:area_kind"), ("sun", "enum:sun"),
        ("size", "text"), ("soil", "text"), ("irrigation", "enum:irrigation"),
        ("notes", "longtext"),
    ],
    "journal": [
        ("name", "text"), ("date", "date"), ("plant_id", "ref:plant"),
        ("entry_type", "enum:entry_type"), ("quantity", "number"), ("unit", "text"),
        ("notes", "longtext"),
    ],
    "supply": [
        ("name", "text"), ("category", "enum:supply_category"), ("brand", "text"),
        ("quantity", "number"), ("unit", "text"), ("vendor", "text"),
        ("purchase_date", "date"), ("location", "text"), ("reorder", "bool"),
        ("notes", "longtext"),
    ],
}
# URL segment for each kind: /api/<plural>, /<plural> pages.
PLURAL = {"plant": "plants", "seed": "seeds", "area": "areas", "journal": "journal",
          "supply": "supplies"}
```

## Test fixture and helpers (copy verbatim)

Every acceptance test that needs data uses this fixture, loaded into a fresh
`make_app(<new temp dir>)` with `load_fixture`: areas get ids 1-4, seeds 1-3,
plants 1-10 and journal entries 1-5, each in list order. Areas and seeds load
first because plants refer to them; plant 3 refers to plant 2 and plant 9 to
plant 8, which already exist when they load.

```python
M = lambda *m: list(m)
AREAS = [
 dict(name="Front bed", kind="bed", sun="full_sun", size="40 x 6 ft", soil="Amended clay over limestone", irrigation="drip"),
 dict(name="Shade bed", kind="bed", sun="part_shade", size="20 x 5 ft", soil="Leaf mould", irrigation="hand"),
 dict(name="Vegetable beds", kind="raised_bed", sun="full_sun", size="three 4 x 8 ft", soil="Raised-bed mix", irrigation="drip"),
 dict(name="Living room", kind="indoor", sun="bright_indirect"),
]
SEEDS = [
 dict(name="Bluebonnet seed (saved)", botanical_name="Lupinus texensis", category="wildflower", source="Saved from the front bed", packed_year=2025, quantity=1, unit="ounces", sow_months=M("sep", "oct", "nov"), germination_days=14, saved=True, tags=["native"]),
 dict(name="Cherokee Purple tomato seed", botanical_name="Solanum lycopersicum", variety="Cherokee Purple", category="vegetable", source="Seed swap", packed_year=2025, quantity=20, unit="seeds", sow_months=M("jan", "feb"), days_to_maturity=80, germination_days=7, tags=["heirloom"]),
 dict(name="Jalapeño Early seed", botanical_name="Capsicum annuum", variety="Early", category="vegetable", source="Garden centre", packed_year=2026, quantity=1, unit="packet", sow_months=M("jan", "feb"), days_to_maturity=65, germination_days=10, reorder=True),
]
PLANTS = [
 dict(name="Texas Bluebonnet", botanical_name="Lupinus texensis", category="wildflower", origin="texas_native", lifecycle="annual", sun="full_sun", water="low", area_id=1, status="growing", planted="2025-10-15", propagation="seed", seed_id=1, color_hex="#2f5fd0", color_family="blue", bloom_months=M("mar", "apr"), wildlife=["bees"], deer_resistant=True, frost_tender=False, min_temp_f=0, tags=["spring", "front yard"]),
 dict(name="Turk's Cap", botanical_name="Malvaviscus arboreus var. drummondii", category="perennial", origin="texas_native", lifecycle="perennial", sun="part_shade", water="low", area_id=2, status="growing", planted="2024-04-20", propagation="nursery", acquired_from="Native plant sale", color_hex="#c8102e", color_family="red", bloom_months=M("may", "jun", "jul", "aug", "sep", "oct"), wildlife=["hummingbirds", "butterflies"], deer_resistant=True, frost_tender=False, min_temp_f=15),
 dict(name="Turk's Cap (division)", botanical_name="Malvaviscus arboreus var. drummondii", category="perennial", origin="texas_native", lifecycle="perennial", sun="part_sun", water="low", area_id=1, status="growing", planted="2026-03-10", propagation="division", parent_id=2, color_hex="#c8102e", color_family="red", bloom_months=M("jun", "jul", "aug", "sep", "oct"), wildlife=["hummingbirds"], deer_resistant=True),
 dict(name="Gulf Muhly", botanical_name="Muhlenbergia capillaris", category="grass", origin="texas_native", lifecycle="perennial", sun="full_sun", water="low", area_id=1, status="growing", planted="2023-11-02", propagation="nursery", color_hex="#d78fbf", color_family="pink", bloom_months=M("sep", "oct", "nov"), deer_resistant=True, min_temp_f=5, tags=["fall"]),
 dict(name="Cherokee Purple Tomato", botanical_name="Solanum lycopersicum", variety="Cherokee Purple", category="vegetable", origin="non_native", lifecycle="annual", sun="full_sun", water="high", area_id=3, status="harvested", planted="2026-03-20", propagation="seed", seed_id=2, color_hex="#6b2737", color_family="purple", harvest_months=M("jun", "jul"), frost_tender=True, min_temp_f=35, tags=["heirloom"]),
 dict(name="Jalapeño Early", botanical_name="Capsicum annuum", variety="Early", category="vegetable", origin="non_native", lifecycle="annual", sun="full_sun", water="medium", area_id=3, status="growing", planted="2026-04-05", propagation="seed", seed_id=3, color_hex="#2e7d32", color_family="green", harvest_months=M("jun", "jul", "aug", "sep", "oct"), frost_tender=True, min_temp_f=35),
 dict(name="Rosemary Arp", botanical_name="Salvia rosmarinus", variety="Arp", category="herb", origin="adapted", lifecycle="perennial", sun="full_sun", water="very_low", area_id=1, status="growing", planted="2022-09-10", propagation="gift", acquired_from="Neighbour's cutting", color_hex="#7b8fd6", color_family="blue", bloom_months=M("dec", "jan", "feb", "mar"), wildlife=["bees"], deer_resistant=True, min_temp_f=10),
 dict(name="Marble Queen Pothos", botanical_name="Epipremnum aureum", variety="Marble Queen", category="houseplant", origin="non_native", lifecycle="perennial", sun="bright_indirect", water="medium", area_id=4, status="growing", planted="2021-06-01", propagation="gift", color_hex="#8db37a", color_family="green", frost_tender=True, min_temp_f=55, tags=["indoor"]),
 dict(name="Marble Queen Pothos (cutting)", botanical_name="Epipremnum aureum", variety="Marble Queen", category="houseplant", origin="non_native", lifecycle="perennial", sun="low_light", water="medium", area_id=4, status="growing", planted="2026-02-14", propagation="cutting", parent_id=8, color_hex="#8db37a", color_family="green", frost_tender=True, min_temp_f=55, tags=["indoor", "gift for mom"]),
 dict(name="Esperanza", botanical_name="Tecoma stans", category="shrub", origin="texas_native", lifecycle="perennial", sun="full_sun", water="low", area_id=1, status="growing", planted="2024-05-01", propagation="nursery", color_hex="#f2c12e", color_family="yellow", bloom_months=M("may", "jun", "jul", "aug", "sep", "oct", "nov"), wildlife=["hummingbirds", "bees", "butterflies"], deer_resistant=True, frost_tender=True, min_temp_f=25),
]
JOURNAL = [
 dict(name="Big harvest", date="2026-07-02", plant_id=5, entry_type="harvest", quantity=4.5, unit="lb"),
 dict(name="Last tomatoes", date="2026-07-20", plant_id=5, entry_type="harvest", quantity=2, unit="lb"),
 dict(name="Peppers", date="2026-08-15", plant_id=6, entry_type="harvest", quantity=30, unit="peppers"),
 dict(name="First bloom", date="2026-03-02", plant_id=1, entry_type="bloom"),
 dict(name="Hard freeze", date="2026-01-21", plant_id=10, entry_type="frost_damage", notes="Died back to the ground; resprouted in April."),
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
    """Areas 1-4, seeds 1-3, plants 1-10 and journal entries 1-5, in order."""
    for plural, rows in (("areas", AREAS), ("seeds", SEEDS), ("plants", PLANTS), ("journal", JOURNAL)):
        for row in rows:
            st, _, b = call(app, "POST", f"/api/{plural}", row)
            assert st == 201, (plural, row.get("name"), st, b)
```

## Interfaces

### Pure helpers

Kept from glassinv unchanged: `hex_to_hsl`, `hue_key`, `parse_inches` and
`format_inches` (same rules, same errors). New:

- `months_label(months: list[str]) -> str` - a short label for a list of
  month slugs. Duplicates are ignored and order does not matter.
  - Consecutive months (in calendar order) join into runs written
    `<first>–<last>` with an en dash (U+2013) and the VOCAB labels; a run of
    one month is just its label. Runs are joined with `", "`.
  - A run ending in December and a run starting in January are one run that
    wraps the year (`["dec","jan","feb","mar"]` -> `"Dec–Mar"`), listed
    first.
  - All twelve months give `"All year"`; an empty list gives `""`.
  - An unknown slug raises `ValueError`.
- `season_of(date: str) -> tuple[int, str]` - the garden season of a
  `YYYY-MM-DD` date: Mar-May `"spring"`, Jun-Aug `"summer"`, Sep-Nov
  `"fall"`, Dec-Feb `"winter"`. December belongs to the FOLLOWING year's
  winter, so one winter is never split across two years. An invalid date
  raises `ValueError`.
- `month_slug(value: str) -> str | None` - lower-cases and strips the value
  and a trailing `.`; if it has at least 3 letters and is the start of an
  English month name (`"June"`, `"jun"`, `"Sept"`, `"september"`), returns
  that month's slug, else None.
- `normalize_suggestions(raw) -> dict` - cleans a vision model's reply about
  a PLANT photo. A non-dict gives `{}`.
  - Single fields `category`, `lifecycle`, `sun`, `water` and
    `color_family` are kept only if the value maps to a slug (same mapping
    rule as glassinv: lower-cased with spaces and hyphens turned into `_` it
    is a slug, or its lower-cased form is a SYNONYMS key for that
    vocabulary).
  - `bloom_months` keeps the values `month_slug` maps, without duplicates,
    in calendar order. `wildlife` keeps the mapped values in order, without
    duplicates. Non-strings are dropped; an empty result drops the key.
  - `color_hex` as in glassinv.
  - `name` and `botanical_name` (at most 200 characters) and `description`
    (at most 2000) are kept if non-blank, with whitespace runs collapsed.
  - Every other key is dropped - including `origin`: a photo cannot show
    where a plant is native.

### Storage - `Store(data_dir: str)`

SQLite at `<data_dir>/garden.db`, media under `<data_dir>/media/`, both
created if missing. Opening an existing store keeps its data. Each kind in
SCHEMAS gets its own table; photos belong to one record of any kind.
`ValidationError(field, message)` subclasses `ValueError`.

Validation as glassinv (unknown field, blank name on create, text limits,
enum and multi slugs, real dates, finite non-negative numbers, inches, hex,
tags, bools, refs to existing ids), plus:
- `degrees`: a finite number from -60 to 130 inclusive (negatives allowed).
- `multi:month` values are stored in calendar order (other multi fields keep
  the given order); duplicates dropped.
- `parent_id` may not be the plant itself or any of its descendants
  (ValidationError on field `parent_id`), so lineage never loops.

Records are dicts with `id` plus every schema field; unset fields are `None`,
or `[]` for multi and tags fields.

- `create(kind, data) -> int`, `update(kind, id, data)` (partial; `None`
  clears a field except `name`; unknown id raises `KeyError`).
- `get(kind, id) -> dict | None`. Every kind includes `photos` (each
  `{"id", "caption", "taken", "primary", "full_url", "thumb_url"}`, primary
  first, then upload order) and `thumb_url` (the primary's, or None).
  - A plant also includes:
    - `area`: `{"id", "name"}` or None; `seed`: `{"id", "name"}` or None;
    - `parent`: `{"id", "name", "thumb_url"}` or None;
    - `children`: plants whose `parent_id` is this one, `{"id", "name", "thumb_url"}`, by id;
    - `lineage`: its ancestors from the oldest down to its parent, `{"id", "name"}` each (`[]` if none);
    - `journal`: its entries `{"id", "date", "name", "entry_type", "quantity", "unit", "notes"}`,
      newest date first (missing dates last), ties to the higher id;
    - `harvests`: totals of its `harvest` entries that have a quantity,
      grouped by (year of the date, unit), as `{"year", "unit", "quantity"}`,
      newest year first, then unit A-Z;
    - `timeline`: its photos grouped for the season view:
      `[{"year", "seasons": [{"season", "photos": [<photo dicts>]}]}]` -
      years newest first; within a year the seasons in the order winter,
      spring, summer, fall, leaving out empty ones; photos by (`taken`, id).
      Year and season come from `season_of(taken)`.
  - A seed also includes `plants` grown from it, and an area includes the
    `plants` in it: `{"id", "name", "thumb_url"}` each, by name then id.
- `delete(kind, id)`: removes the record and its photos (and their files).
  References to it are cleared, never cascaded: deleting a plant sets
  `parent_id` on its children and `plant_id` on its journal entries to None;
  deleting a seed or area clears `seed_id` / `area_id` on plants.
- `query(kind, params: dict[str, list[str]]) -> list[dict]`: summaries
  (`id`, `name`, `thumb_url`; plants also `botanical_name`, `color_hex`,
  `color_family`, `status`, `bloom_months`, `harvest_months`) in result order:
  - `q`: every whitespace-separated word must appear, case-insensitive, in
    the record's text, longtext and tags fields combined;
  - any enum field: OR within the field, AND across fields;
  - any multi field: the record matches if it has ANY of the given values
    (`bloom_months=oct` is "blooms in October");
  - any ref field (`area_id=1`, `parent_id=2`, `seed_id=2`, `plant_id=5`):
    equals that id;
  - any bool field: `=1` matches true only, `=0` matches false or unset;
  - plants only: `alive=1` (status not died, removed or given_away; unset
    counts as alive), `alive=0` (one of those), and `planted_year=<YYYY>`
    (planted in that year; OR when repeated);
  - `sort`: `added` (default, newest id first) and `name` for every kind;
    plants and seeds also `botanical` (botanical_name A-Z, missing last, then
    name); plants also `planted` (newest first, missing last) and `hue` (by
    `hue_key`, missing colours last); journal also `date` (newest first,
    missing last); supplies also `purchased` (newest first, missing last).
    Ties go to the lower id, except `added`. An unknown sort for that kind
    raises ValidationError on `sort`.

  Unknown parameter names are ignored.
- Photos as glassinv (JPEG, PNG and WebP by signature, for every kind; PDF is
  not accepted anywhere), plus a `taken` date per photo:
  - `add_photo(kind, id, data, content_type, caption=None, taken=None) -> int`;
    `taken` defaults to today's local date and must be a real `YYYY-MM-DD`
    (ValidationError on `taken`).
  - `update_photo(photo_id, caption=..., taken=..., primary=...)` changes
    any of those given.
  - `set_thumb`, `delete_photo` (the next by upload order becomes primary)
    and `media_path` unchanged.
- `export_zip() -> bytes`: `inventory.json` as
  `{"version": 1, "records": {<kind>: [...]}}` (records with photo metadata
  including `taken`) and every media file under `media/`.
- `add_usage` / `usage` and the `claude_usage` table, unchanged from
  glassinv.

### HTTP - `make_app(data_dir, describer=None, password=None, max_upload=25*1024*1024, assets_dir=None, title="Garden notebook", subtitle="", banner_alt="", garden=None, assistant=None, assistant_budget=None)`

`garden` is the dict from Garden settings below (None means all blank).

JSON API (errors `{"error", "field"}`; status codes 400/404/405/413/415 and
Basic auth exactly as glassinv, realm `"gardeninv"`):
- `GET /api/meta` -> `{"vocab", "schemas", "plural", "vision": bool, "claude": bool, "garden": <garden dict>}`.
- `GET /api/<plural>?<params>` -> `{"items", "total"}`; `POST /api/<plural>`
  -> 201 `{"id"}`; `GET` / `PUT` / `DELETE /api/<plural>/<id>`.
- `POST /api/<plural>/<id>/photos[?caption=...&taken=YYYY-MM-DD]` with the
  raw file -> 201 `{"id", "full_url", "thumb_url", "taken"}`.
  `POST /api/photos/<pid>/thumb`; `PUT /api/photos/<pid>` with any of
  `{"primary": true, "caption", "taken"}`; `DELETE /api/photos/<pid>`.
- `GET /media/<pid>/full|thumb`, `GET /static/img/<name>`,
  `POST /api/intake/describe`, `GET /api/export.zip` and `GET /healthz` as
  glassinv.
- `GET /api/dashboard[?month=<slug>]` -> the dashboard lists below as JSON:
  `{"month", "blooming", "harvest", "sow", "cover"}`, each list of
  `{"id", "name", "thumb_url"}`. `month` defaults to the current local
  month; an unknown slug is 400 (field `month`).

Dashboard lists, for month M:
- `blooming`: alive plants with M in `bloom_months`, in hue order;
- `harvest`: alive plants with M in `harvest_months`, in hue order;
- `sow`: seeds with M in `sow_months`, by name;
- `cover` ("Cover before a freeze"): alive plants with `frost_tender` true
  whose area is not of kind `indoor` (no area counts as outdoors), by name.
  It does not depend on M.

HTML pages (server-rendered, `text/html; charset=utf-8`, one shared layout
with the theme below):
- `/` - the dashboard: counts per kind; "Blooming in <Month>", "Harvest in
  <Month>", "Sow in <Month>" (the full English month name, e.g. "October") (thumbnails, `data-id` per item) and "Cover
  before a freeze" (a list); the garden facts line (zone and frost dates,
  only the parts set); the 8 most recently added plant thumbnails; every
  record flagged `reorder`. `?month=<slug>` shows another month, with
  previous/next month links.
- `/plants` - the browser. The API's parameters plus `view` = `grid`
  (default) | `cards` | `list` | `swatch` | `calendar`, and `size` = `s` |
  `m` | `l`. Results sit in one element with `data-view="<view>"`; each item
  has `data-id`.
  - `grid`, `cards` and `list` as glassinv (list: a `<table>` with sort
    links). Cards show name, botanical name in italics, the origin label and
    `months_label` of bloom months.
  - `swatch` always sorts by hue, grouped by colour family.
  - `calendar`: a `<table>` with one row per plant (`<tr data-id=...>`) and
    twelve month columns Jan-Dec; a cell is marked `class="bloom"` for a
    bloom month and `class="harvest"` for a harvest month (both classes if
    both). Each row also carries
    `data-bloom="<slugs space-separated in calendar order>"`.
  - The filter panel lists each enum, multi and bool field's values with
    counts in the current results, plus Area (by name) and "Alive only".
    Every control is a plain link or GET form.
- `/plants/<id>` - detail:
  - the primary photo large, the other photos as thumbnails;
  - every field with VOCAB labels, `months_label` for month lists,
    `format_inches` for sizes and `<n:g>°F` for min_temp_f (`10°F`);
  - "Through the seasons": the `timeline`, one row per year, one column per
    season, photos with their `taken` date;
  - "Family": the lineage as a breadcrumb of links ending at this plant,
    then its children, and "Grown from <seed>" when `seed_id` is set;
  - the journal with an inline "add entry" form, and harvest totals
    (`<quantity:g> <unit> in <year>`);
  - "Propagate": a button that opens `/plants/new?parent=<id>`.
- `/plants/new` - photo-first intake as glassinv (camera input, browser
  thumbnail and colour, vision suggestions marked "suggested"). The photo's
  `taken` defaults to today and can be changed. `?parent=<id>` pre-fills a
  propagation from that plant: the parent's name, botanical name, variety,
  category, origin, lifecycle, sun, water, colour, months and wildlife, with
  `parent_id` set and propagation `cutting`; `?seed=<id>` pre-fills from a
  seed (name without the word "seed", botanical name, variety, category,
  `seed_id`, propagation `seed`).
- `/seeds`, `/areas`, `/journal` (newest date first, with an entry-type
  filter) and `/supplies`, each with detail and new/edit forms built from
  SCHEMAS. A seed's page lists the plants grown from it and has "Sow some" ->
  `/plants/new?seed=<id>`; an area's page shows its plants as a grid.
- `/assistant` when Claude is enabled (below).
- `GET /static/app.css`, `/static/app.js` with `makeThumbnail` and
  `dominantColor` as glassinv.

### Theme - Wendy's garden

The same mechanism as glassinv's theme (assets_dir, `/static/img/<name>`,
files checked once at start-up, missing files simply left out, title in
every `<title>` and `<h1>`, subtitle on the dashboard only, favicon, the
mark on empty pages and in the footer), with garden images and a garden
palette:
- Wendy's values (compose.yaml):
  - `GARDENINV_TITLE`: [[FILL IN: e.g. "Wendy's Garden Notebook"]]
  - `GARDENINV_SUBTITLE`: [[FILL IN: e.g. "Natives, vegetables and houseplants - inspected daily by Rue"]]
  - `GARDENINV_BANNER_ALT`: [[FILL IN: e.g. "Wendy and Rue among the bluebonnets"]]
- Images: `garden-banner.png` (1536 x 512, header, compact 96 px strip
  except on the dashboard), `garden-tile.png` (768 x 768, seamless, `body`
  background with the same paper column and dark-mode dimming as
  glassinv), `rue-mark.png` (512 x 512; favicon, empty pages at 96 px with
  alt "Rue", footer at 24 px).
- Palette "field notebook" (CSS variables):
  - limestone paper `--paper: #f3efe4` and ink `--ink: #23261f`;
  - sage `--sage: #7d8f69` for rules and borders;
  - bluebonnet `--bluebonnet: #2f5fd0` for links and focus;
  - Indian paintbrush `--paintbrush: #d4502a` for accents and the bloom
    marks;
  - cedar `--cedar: #5a3e2b` for the header band (the title sits on the
    banner in a cedar band, as glassinv's lead band);
  - a dark mode via `prefers-color-scheme`.
- Grid view: tiles with rounded 6 px corners and 8 px gaps on paper (like
  pressed specimens on a notebook page), not glassinv's lead gutters.
  Botanical names are always italic.
- Headings keep glassinv's serif stack; interface `system-ui, sans-serif`.
- Everything works at 375 px wide; intake happens in the garden on a phone.

### Garden settings

`config_from_env` returns `garden`:
`{"zone": GARDENINV_ZONE, "last_frost": GARDENINV_LAST_FROST, "first_frost": GARDENINV_FIRST_FROST}`,
each a string, `""` when unset. They are shown on the dashboard and sent to
Claude, and nowhere else. Wendy's values (compose.yaml):
- zone [[FILL IN: e.g. "9a" - from the USDA Plant Hardiness Zone Map for her ZIP]];
- last_frost [[FILL IN: e.g. "mid-March"]];
- first_frost [[FILL IN: e.g. "late November"]].

### Vision - `vision_describer(url, model, api_key=None, extra=None, post=None)`

As glassinv, but the system message describes a PLANT photo and lists the
keys `name`, `botanical_name`, `category`, `lifecycle`, `sun`, `water`,
`color_family`, `color_hex` (the flower, or the foliage if not in bloom),
`bloom_months`, `wildlife` and `description`, with every allowed value
from VOCAB for the enum keys. The user text is "Identify this plant."

### Ask Claude

The glassinv Claude client is kept unchanged: `claude_client`, `ClaudeError`,
`claude_describer` (system text as the vision describer above, user text
"Identify this plant."), the budget (429), 503 when not configured and 502
on ClaudeError, `GET /api/claude`, and the `claude` config read from
`GARDENINV_CLAUDE_KEY_FILE`, `_MODEL`, `_MONTHLY_TOKENS` (default
[[FILL IN: e.g. 2000000]]), `_WEB_SEARCH`, `_CITY`, `_REGION`, `_COUNTRY`
(default `US`), `_TIMEZONE` and `_VISION`. Wendy's location values: city
[[FILL IN: e.g. Austin]], region [[FILL IN: e.g. Texas]], country US,
timezone [[FILL IN: e.g. America/Chicago]]. City level only.

The glass routes are replaced by:
- `POST /api/claude/advice` with
  `{"request"?: str <= 1000 chars, "plant_ids"?: [int], "month"?: <slug>}`:
  - User content, one text part, in this order:
    1. Wendy's request, or "What should I plant, sow and do in the garden this month?";
    2. `MONTH: <full month name> <year>` (the given month in the current
       year, else the current month);
    3. `GARDEN SETTINGS: zone <zone>; last frost <last_frost>; first frost <first_frost>`
       with unset parts left out (the line left out if all are unset);
    4. `PLANTS:` then one line per ALIVE plant, by name:
       `- <name> (<botanical_name>): <category label>, <origin label>, <sun label>, water: <water label>; <area name>; planted <planted>; blooms <months_label>; harvest <months_label>; frost tender; <status label>`
       Missing parts are left out (with their separator); `frost tender`
       appears only when true; the `(<botanical_name>)` only when set;
    5. `SEEDS:` then one line per seed, by name:
       `- <name> (<botanical_name>): packed <packed_year>; sow <months_label>`
       (missing parts left out);
    6. if `plant_ids` are given, `ASKING ABOUT: <names, comma-separated, in the order given>`.
  - System text: a gardening advisor for a master gardener at the location
    and hardiness zone given; prefer native and adapted plants for the
    region; time advice to the month and the frost dates; name her own
    plants and seeds when they fit; be specific about timing, spacing and
    watering; plain text, short paragraphs, no markdown tables.
  - Web search on (if enabled) - planting calendars and extension guidance
    are worth citing. `max_tokens` 1500.
  - 200 `{"answer", "sources", "usage"}`. An unknown plant id is 404; a
    too-long request is 400 (field `request`); an unknown month is 400
    (field `month`).
- `POST /api/claude/find` with `{"query": str (1-300 chars), "plant_id"?: int, "seed_id"?: int}`:
  - the query is prefixed by that plant's or seed's
    `<name> <variety> <botanical_name>` (missing parts left out; plant_id
    wins if both are given);
  - system text: find current places to buy this plant or seed; prefer
    native-plant nurseries and sellers near or shipping to the user's
    location; give prices and sizes when the pages show them; say plainly
    when something is out of season or sold out; plain text;
  - web search on, `max_tokens` 1500; 200 `{"answer", "sources", "usage"}`;
    a blank or too-long query is 400 (field `query`); an unknown id is 404.
- Pages: `/assistant` (404 when disabled; nav link only when enabled) with
  "Garden advice" (a textarea, an optional month picker and the alive plants
  as toggle thumbnails for `plant_ids`), "Find plants & seeds", and the
  month's usage. Answers are built with DOM methods and `textContent`,
  sources as `<a rel="noopener noreferrer" target="_blank">`.
  `/plants/<id>` gains "Ask Claude about this" (`/assistant?advice_plant=<id>`)
  and "Find more of this" (`/assistant?find_plant=<id>`); `/seeds/<id>`
  gains "Find more of this" (`/assistant?find_seed=<id>`). Only when enabled.
- Privacy: Claude receives only the request, the month, the garden settings,
  the plant and seed lines above (names, botanical names, labels, area
  names, dates), or one photo for the describer. Never the key, paths,
  journal notes, other photos or any address.

### Running - `config_from_env(env: dict) -> dict` and `serve()`

`config_from_env` returns:
- `data_dir` (`GARDENINV_DATA`, default `/data`), `port` (`GARDENINV_PORT`,
  int, default 8091), `password` (`GARDENINV_PASSWORD`, None if unset or
  empty), `max_upload` (`GARDENINV_MAX_UPLOAD_MB` x 1048576, default 25 MB);
- `vision` as glassinv from `GARDENINV_VISION_URL`, `_MODEL`, `_KEY_FILE`,
  `_EXTRA`;
- `assets_dir` (`GARDENINV_ASSETS`, default `/app/assets`), `title`
  (`GARDENINV_TITLE`, default `Garden notebook`), `subtitle`
  (`GARDENINV_SUBTITLE`, default `""`), `banner_alt`
  (`GARDENINV_BANNER_ALT`, default `""`);
- `garden` and `claude` as above.

`serve()` builds the config from `os.environ`, the describer (local vision
first, else Claude vision when `claude.vision`), the Claude client when
configured, makes the app and runs a threading `wsgiref` server on
`0.0.0.0:<port>` forever.

### Deployment - `build_outputs(out_dir: str) -> list[str]`

Writes four files and returns their paths sorted, and creates
`<out_dir>/assets/` (forge copies the rendered images into it):
- `gardeninv.py`: this module's exact source.
- `Dockerfile`: `FROM python:3.12-slim`; non-root uid 1000;
  `WORKDIR /app`; copy `gardeninv.py` and `COPY assets/ /app/assets/`;
  `ENV GARDENINV_DATA=/data` and `ENV GARDENINV_ASSETS=/app/assets`;
  `EXPOSE 8091`; a python-urllib `HEALTHCHECK` on
  `http://127.0.0.1:8091/healthz`; `USER`; and
  `CMD ["python", "-c", "import gardeninv; gardeninv.serve()"]`.
- `compose.yaml`: one service `gardeninv`: `build: .`, ports `"8091:8091"`,
  `./data:/data`, `restart: unless-stopped`; Wendy's title, subtitle, banner
  alt and garden settings (each quoted); commented-out vision settings for
  Gemini as glassinv; commented-out Claude lines (the
  `./secrets/anthropic-key:/run/secrets/anthropic-key:ro` mount,
  `GARDENINV_CLAUDE_KEY_FILE=/run/secrets/anthropic-key`, model, budget and
  the four location variables).
- `README.md`: build and run; the data directory is what to back up; vision;
  "Theme" (the three images, the variables); "Ask Claude" (what it does, the
  key file setup, that the key's owner pays per use, the monthly budget,
  what is sent, never commit `secrets/`); no login unless
  `GARDENINV_PASSWORD` is set, so keep it on the LAN.

## Acceptance

Floats compare within 1e-6. "Fixture app" means
`app = make_app(tempfile.mkdtemp()); load_fixture(app)`. Query items
compare the `id`s in order. JPEG = `b"\xff\xd8\xff\xe0" + b"0" * 100`,
PNG = `b"\x89PNG\r\n\x1a\n" + b"0" * 100`.

Helpers
1. `hex_to_hsl("#2f5fd0")` is (222.111801, 0.631373, 0.5) within 1e-6.
   `hue_key("#c8102e") == (0, 5.217391, 0.423529)`,
   `hue_key("#f2c12e") == (0, 60.0, 0.564706)`. `format_inches(36.0)` is
   `'36"'` and `parse_inches("1 1/2")` is 1.5.
2. `months_label`:
   - `["mar","apr"]` -> `"Mar–Apr"`; `["may","jun","jul","aug","sep","oct"]` -> `"May–Oct"`;
   - `["dec","jan","feb","mar"]` -> `"Dec–Mar"`; `["oct"]` -> `"Oct"`;
   - `["jul","jan"]` -> `"Jan, Jul"`; `["jan","mar","apr","dec"]` -> `"Dec–Jan, Mar–Apr"`;
   - `["nov","dec","jan","jun"]` -> `"Nov–Jan, Jun"`;
   - all twelve -> `"All year"`; `[]` -> `""`; `["oct","octo"]` raises `ValueError`.
3. `season_of`: `"2026-12-05"` -> (2027, "winter"); `"2026-01-21"` and
   `"2026-02-28"` -> (2026, "winter"); `"2026-03-01"` and `"2026-05-31"` ->
   (2026, "spring"); `"2026-06-01"` and `"2026-08-31"` -> (2026, "summer");
   `"2026-09-01"` and `"2026-11-30"` -> (2026, "fall"). `"2026-02-30"`
   raises `ValueError`.
4. `month_slug`: `"June"` -> "jun", `"Sept."` -> "sep", `"september"` ->
   "sep", `"ma"` -> None, `"Juny"` -> None.
5. `normalize_suggestions({"name":"  Turk's   cap ","botanical_name":"Malvaviscus  arboreus","category":"Shrub","lifecycle":"Perennials","sun":"Partial Shade","water":"Drought tolerant","color_family":"Maroon","bloom_months":["June","may","Sept","Juny","jun",7],"wildlife":["Hummingbirds","deer"],"color_hex":"C8102E","origin":"texas_native","description":"x"})`
   is exactly
   `{"category":"shrub","lifecycle":"perennial","sun":"part_shade","water":"very_low","color_family":"red","bloom_months":["may","jun","sep"],"wildlife":["hummingbirds"],"color_hex":"#c8102e","name":"Turk's cap","botanical_name":"Malvaviscus arboreus","description":"x"}`.
   `normalize_suggestions({"category":"fern","sun":None,"bloom_months":"june","color_hex":"#12345"})`
   and `normalize_suggestions([1])` both give `{}`.

Records
6. On the fixture app `GET /api/plants/7` returns name "Rosemary Arp",
   `bloom_months` `["jan","feb","mar","dec"]` (calendar order), `area`
   `{"id": 1, "name": "Front bed"}`, `seed` None, `parent` None,
   `children` [], `lineage` [], `photos` [], `thumb_url` None and
   `timeline` [].
7. `GET /api/plants/3`: `parent` id 2, `lineage` ids [2]. `GET /api/plants/2`:
   `children` ids [3]. `GET /api/plants/9`: `lineage` ids [8].
   `GET /api/plants/1`: `seed` `{"id": 1, "name": "Bluebonnet seed (saved)"}`.
8. `GET /api/plants/5`: `journal` ids [2, 1] and `harvests`
   `[{"year": 2026, "unit": "lb", "quantity": 6.5}]`. `GET /api/plants/6`:
   `harvests` `[{"year": 2026, "unit": "peppers", "quantity": 30}]`.
   `GET /api/plants/1`: `harvests` [] and `journal` ids [4].
9. `GET /api/areas/1`: `plants` ids [10, 4, 7, 1, 3]. `GET /api/seeds/2`:
   `plants` ids [5].
10. `POST /api/plants` returns 400 with `field` naming the bad field for:
    `{"name":""}`; `{"name":"x","origin":"martian"}`;
    `{"name":"x","planted":"2026-02-30"}`; `{"name":"x","bloom_months":["octo"]}`;
    `{"name":"x","bloom_months":"oct"}`; `{"name":"x","min_temp_f":-70}`;
    `{"name":"x","min_temp_f":131}`; `{"name":"x","area_id":99}`;
    `{"name":"x","glass":"blue"}`. `{"name":"x","min_temp_f":-5}` returns
    201. A non-JSON body is 400 with field null.
11. `PUT /api/plants/2` with `{"parent_id": 3}` -> 400 field "parent_id"
    (3 descends from 2); with `{"parent_id": 2}` -> 400. `PUT /api/plants/8`
    with `{"parent_id": 1}` -> 200. `PUT /api/plants/99` -> 404.
12. `PUT /api/plants/4` with `{"tags": "Fall, Pink ,fall", "acquired_from": null}`
    returns tags `["fall","pink"]`; the name is unchanged.
13. Deleting: `DELETE /api/plants/2` -> 200; plant 3's `parent_id` is then
    None. `DELETE /api/plants/5` leaves journal entries 1 and 2 with
    `plant_id` None. `DELETE /api/areas/4` leaves plants 8 and 9 with
    `area_id` None.

Queries (fixture app, `GET /api/plants?...` unless noted)
14. `category=vegetable` -> [6, 5]. `origin=texas_native` -> [10, 4, 3, 2, 1].
    `sun=part_shade&sun=part_sun` -> [3, 2].
    `water=very_low&water=low&category=perennial` -> [3, 2].
15. `bloom_months=oct` -> [10, 4, 3, 2]. `bloom_months=dec&bloom_months=jan`
    -> [7]. `wildlife=hummingbirds` -> [10, 3, 2].
16. `area_id=1` -> [10, 7, 4, 3, 1]. `area_id=4&sort=name` -> [8, 9].
    `parent_id=2` -> [3]. `seed_id=2` -> [5].
17. `frost_tender=1` -> [10, 9, 8, 6, 5]. `frost_tender=0` -> [7, 4, 3, 2, 1].
    `deer_resistant=1&sun=full_sun` -> [10, 7, 4, 1].
    `alive=1&status=harvested` -> [5]. `alive=0` -> [].
    `planted_year=2026` -> [9, 6, 5, 3]; with `&sort=planted` -> [6, 5, 3, 9].
18. `q=pothos` -> [9, 8]. `q=malvaviscus` -> [3, 2]. `q=front%20yard` -> [1].
    `q=heirloom` -> [5]. `q=gift` -> [9].
19. Sorts over everything:
    - `sort=added` (and no sort) -> [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    - `sort=name` -> [5, 10, 4, 6, 8, 9, 7, 1, 2, 3]
    - `sort=botanical` -> [6, 8, 9, 1, 2, 3, 4, 7, 5, 10]
    - `sort=planted` -> [6, 5, 3, 9, 1, 10, 2, 4, 7, 8]
    - `sort=hue` -> [5, 2, 3, 10, 8, 9, 6, 1, 7, 4]
20. Seeds: `sow_months=oct` -> [1]; `category=vegetable&sort=name` -> [2, 3];
    `saved=1` -> [1]; `reorder=1` -> [3]; `sort=botanical` -> [3, 1, 2];
    `q=seed` -> [3, 2, 1]. Journal: `plant_id=5` -> [2, 1];
    `sort=date` -> [3, 2, 1, 4, 5]; `entry_type=harvest` -> [3, 2, 1].
21. `/api/seeds?sort=hue` and `/api/plants?sort=sideways` return 400. `nonsense=1` is ignored. Every
    `total` equals the length of `items`.

Dashboard
22. `GET /api/dashboard?month=oct` -> blooming ids [2, 3, 10, 4], harvest
    [6], sow [1], cover [5, 10, 6].
    `month=mar` -> blooming [1, 7], harvest [], sow [].
    `month=jul` -> blooming [2, 3, 10], harvest [5, 6].
    `month=jan` -> blooming [7], sow [2, 3]. `month=octo` -> 400.
23. `GET /?month=oct` returns 200 HTML containing "Blooming in October",
    `data-id="4"`, "Cover before a freeze" and "Esperanza".

Photos and the season timeline
24. On the fixture app, upload six JPEGs to plant 1 with `taken` =
    2025-12-20, 2026-04-02, 2026-03-15, 2026-07-04, 2025-10-01, 2026-04-02
    (photo ids 1-6 in that order); each returns 201 with that `taken`.
    `GET /api/plants/1` then has `timeline` (as photo ids)
    `[{"year": 2026, "seasons": [{"season": "winter", "photos": [1]}, {"season": "spring", "photos": [3, 2, 6]}, {"season": "summer", "photos": [4]}]}, {"year": 2025, "seasons": [{"season": "fall", "photos": [5]}]}]`
    and photo 1 is primary.
25. An upload with `?taken=2026-02-30` -> 400 (field "taken"); without
    `taken`, the photo's `taken` is today's local date.
    `PUT /api/photos/5` with `{"taken": "2026-09-10"}` moves photo 5 to 2026
    fall in the timeline. PNG thumbs, primary switching, deletion (files
    removed, next primary chosen) behave as glassinv's items 21-22.
26. Plain text -> 415; a PDF (`b"%PDF-1.7 ..."`) to a plant or seed -> 415;
    `/api/plants/99/photos` -> 404; with `make_app(dir, max_upload=1000)` a
    2000-byte JPEG -> 413.

HTML and static
27. `GET /plants` -> 200 HTML, `data-view="grid"`, `data-id` in order
    [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]. `GET /plants?view=swatch&sort=name` ->
    [5, 2, 3, 10, 8, 9, 6, 1, 7, 4]. `GET /plants?view=list&sort=name`
    contains `<table>` and lists [5, 10, 4, 6, 8, 9, 7, 1, 2, 3].
28. `GET /plants?view=calendar&sort=name` contains `data-view="calendar"`, a
    `<table>`, `data-id` in name order, `data-bloom="jan feb mar dec"`
    (plant 7) and at least one `class="harvest"` cell.
29. `GET /plants/7` contains "Rosemary Arp", "<i>Salvia rosmarinus</i>" (or
    the botanical name inside an element styled italic), "Dec–Mar",
    "Adapted (non-native, well suited)" and "10°F". `GET /plants/3` links to
    `/plants/2`. `GET /plants/5` contains "6.5 lb in 2026". `GET /plants/99`
    -> 404.
30. A plant named `<script>alert(1)</script>` appears on `/plants` escaped
    and the raw `<script>alert(1)` appears nowhere in the page.
31. Each returns 200 HTML: `/`, `/plants/new`, `/plants/new?parent=2`,
    `/plants/new?seed=2`, `/seeds`, `/seeds/1`, `/areas`, `/areas/1`,
    `/journal`, `/supplies`. `/plants/new?parent=2` contains
    "Malvaviscus arboreus var. drummondii" and `value="2"`.
32. `GET /static/app.css` is `text/css` and contains `--bluebonnet` and
    `prefers-color-scheme`. `GET /static/app.js` contains
    `function makeThumbnail` and `function dominantColor`. `GET /api/meta`
    has `vocab` equal to VOCAB, `schemas` with exactly the five kinds,
    `"vision": false`, `"claude": false` and `garden`
    `{"zone": "", "last_frost": "", "first_frost": ""}`. No page or route
    mentions "glass" (`GET /glass` is 404; the word "glass" appears in no
    served HTML, CSS or JS).

Theme
33. With `A` holding the three theme files and
    `make_app(d, assets_dir=A, title="Wendy's <Garden>", subtitle="Rue approves", banner_alt="Wendy and Rue")`:
    `/static/img/garden-banner.png` -> 200 `image/png` with the bytes and
    `max-age=86400`; `/static/img/..%2Fgardeninv.py` and
    `/static/img/Garden.PNG` -> 404; `GET /` contains
    `<title>Wendy&#x27;s &lt;Garden&gt;</title>`,
    `src="/static/img/garden-banner.png"` with `alt="Wendy and Rue"`,
    "Rue approves" and `<link rel="icon" href="/static/img/rue-mark.png">`;
    `GET /seeds` (no seeds) shows the mark with `alt="Rue"`;
    `/static/app.css` contains `/static/img/garden-tile.png`. With only
    `garden-tile.png` present, `/` has no banner and no mark.

Vision and Claude
34. With `describer=lambda b, ct: {"category":"Bush","sun":"partial shade","origin":"texas_native"}`,
    `POST /api/intake/describe` for an uploaded photo returns
    `{"suggestions": {"category": "shrub", "sun": "part_shade"}}`. Without a
    describer -> 503; a raising describer -> 502.
35. `vision_describer("http://v/v1", "m", post=fake)` sends a system message
    containing "botanical_name", "bloom_months" and "part_shade", and a user
    message whose text part is "Identify this plant."
36. Claude (with glassinv's `fake`, `R(...)` and `client`;
    `make_app(d, assistant=client, assistant_budget=100000, garden={"zone": "9a", "last_frost": "mid-March", "first_frost": ""})`
    plus `load_fixture`): `POST /api/claude/advice` with
    `{"request": "what to sow?", "month": "oct", "plant_ids": [1, 5]}` and
    reply `R("Sow bluebonnets now.")` returns 200 with that answer. The user
    text sent:
    - starts with "what to sow?" and contains `MONTH: October ` (then the
      current year);
    - contains `GARDEN SETTINGS: zone 9a; last frost mid-March` and not
      "first frost";
    - contains, exactly, the lines
      `- Texas Bluebonnet (Lupinus texensis): Wildflower (annual / biennial), Texas native, Full sun (6+ h), water: Low; Front bed; planted 2025-10-15; blooms Mar–Apr; Growing`,
      `- Esperanza (Tecoma stans): Shrub, Texas native, Full sun (6+ h), water: Low; Front bed; planted 2024-05-01; blooms May–Nov; frost tender; Growing`
      and
      `- Cherokee Purple Tomato (Solanum lycopersicum): Vegetable, Non-native, Full sun (6+ h), water: High; Vegetable beds; planted 2026-03-20; harvest Jun–Jul; frost tender; Harvested / done for the season`;
    - has the PLANTS lines in name order (Cherokee Purple Tomato first,
      Turk's Cap (division) last), then
      `- Bluebonnet seed (saved) (Lupinus texensis): packed 2025; sow Sep–Nov`
      under SEEDS;
    - ends with `ASKING ABOUT: Texas Bluebonnet, Cherokee Purple Tomato`;
    - does not contain "Died back" (journal notes are never sent).
    The request body has the web_search tool. `plant_ids: [99]` -> 404;
    `month: "octo"` -> 400.
37. `POST /api/claude/find` with `{"query": "where?", "seed_id": 2}` sends
    user text starting "Cherokee Purple tomato seed Cherokee Purple Solanum
    lycopersicum"; with `plant_id: 1` it starts "Texas Bluebonnet Lupinus
    texensis". Budget, 503 without a client, 502 on ClaudeError and the key
    never appearing in any page behave as glassinv's C5, C9-C11.
    `GET /plants/1` shows "Ask Claude about this" only with a client.

Config, export, deployment
38. `config_from_env({})`: data_dir "/data", port 8091, password None,
    max_upload 26214400, vision None, assets_dir "/app/assets", title
    "Garden notebook", subtitle "", banner_alt "", garden
    `{"zone": "", "last_frost": "", "first_frost": ""}`, claude None. With
    `GARDENINV_ZONE=9a` and `GARDENINV_PORT=9001`, garden zone is "9a" and
    port 9001. The `GARDENINV_CLAUDE_*` variables behave as glassinv's C13.
39. With a password set, `GET /plants` is 401 with `realm="gardeninv"`,
    200 with credentials; `/healthz` needs none.
40. On the fixture app with one JPEG on plant 2, `GET /api/export.zip` has
    `inventory.json` version 1 with records for all five kinds (10 plants,
    3 seeds, 4 areas, 5 journal entries, 0 supplies), plant 2's photo entry
    with its `taken`, and exactly one file under `media/`.
41. `build_outputs("/tmp/out_garden")` returns four sorted paths and creates
    `assets/`. `gardeninv.py` equals the module source; `Dockerfile`
    contains `FROM python:3.12-slim`, `EXPOSE 8091`, `HEALTHCHECK`, `USER`,
    `COPY assets/ /app/assets/` and
    `CMD ["python", "-c", "import gardeninv; gardeninv.serve()"]`;
    `compose.yaml` contains `8091:8091`, `./data:/data`, `unless-stopped`,
    `GARDENINV_TITLE`, `GARDENINV_ZONE` and `GARDENINV_CLAUDE_KEY_FILE`
    (commented out); `README.md` mentions `GARDENINV_PASSWORD` and
    `secrets/`.
42. Data survives a restart: create a plant, build a second `make_app` on
    the same directory, and `GET` returns the same record.

## Chunks (optional)

Suggested split (forge maps each onto the glassinv chunk it resembles):
- helpers and vocabulary (months_label, season_of, month_slug,
  normalize_suggestions);
- schema validation (degrees, month ordering, the parent cycle check);
- Store CRUD, relations (lineage, children, journal, harvests) and deletes;
- Store query;
- photos with `taken`, the timeline, and export;
- the WSGI router, API and dashboard API;
- HTML layout, theme and dashboard;
- the plant browser (five views) and plant detail;
- seeds, areas, journal and supplies pages, and plant intake;
- static CSS and JS;
- vision, Claude advice/find, config and serve;
- build_outputs.

Plan the query engine, validation, the timeline and the plant browser with
thinking.

## Visual review

Claude's review sees code only. Look for: every user string escaped; every
view reachable as a plain link; botanical names in italics; the calendar
legible on a phone (horizontal scroll inside the table only); no external
URLs; nothing left over from the glass domain.

## Assets

```json
[
  {
    "id": "theme-banner",
    "title": "Wendy and Rue in the garden",
    "filename": "garden-banner.png",
    "server": "vulcan",
    "width": 1536, "height": 512,
    "steps": 30, "cfg_scale": 2.5, "seed": 3611,
    "reference_images": ["refs/wendy/wendy-1.jpg", "refs/wendy/wendy-2.jpg", "refs/wendy/wendy-3.jpg",
                         "refs/wendy/rue-1.jpg", "refs/wendy/rue-2.jpg"],
    "prompt": "Warm cartoon illustration, wide banner. The woman from the reference photos, drawn as a friendly cartoon that keeps her likeness - [[FILL IN: same description as wendy-theme.md]] - kneels in a sunny Central Texas garden in a wide-brimmed straw hat and gardening gloves, smiling, planting a small seedling in a bed of blue Texas bluebonnets and orange-red Indian paintbrush. Beside her sits her cat Rue from the reference photos - [[FILL IN: same description as wendy-theme.md]] - peering into a terracotta pot with great interest. Behind them a low limestone wall, a red Turk's cap shrub with a hummingbird, tall pink Gulf muhly grass, a raised vegetable bed with tomato cages, and a clear blue sky. Soft outlines, picture-book style, cheerful and cosy. The left third of the image is calmer (sky and wall) so a title can sit over it.",
    "negative_prompt": "text, letters, words, watermark, signature, logo, photorealistic, extra fingers, deformed hands, two cats, extra people, blurry face, snow"
  },
  {
    "id": "theme-mark",
    "title": "Rue in a pot",
    "filename": "rue-mark.png",
    "server": "vulcan",
    "width": 512, "height": 512,
    "steps": 30, "cfg_scale": 2.5, "seed": 3612,
    "reference_images": ["refs/wendy/rue-1.jpg", "refs/wendy/rue-2.jpg"],
    "prompt": "Simple round badge illustration: the cat Rue from the reference photos - [[FILL IN: same description as wendy-theme.md]] - sitting upright in a terracotta flower pot, face forward, a single bluebonnet sprig beside her ear, inside a circle of sage green leaves on a cream background. Bold clean shapes and a clear silhouette that still reads at 24 pixels, centred, the circle filling the frame.",
    "negative_prompt": "text, letters, watermark, photorealistic, extra cats, busy background, cropped circle"
  },
  {
    "id": "theme-tile",
    "title": "Botanical background tile",
    "filename": "garden-tile.png",
    "server": "mercury",
    "tileable": true,
    "width": 768, "height": 768,
    "steps": 30, "cfg_scale": 2.5, "seed": 3613,
    "prompt": "Seamless botanical pattern seen flat, filling the whole frame: loose hand-drawn ink and watercolour sprigs of Texas wildflowers - bluebonnets, Indian paintbrush, black-eyed Susans, salvia leaves and grass seed heads - scattered evenly on a cream limestone-coloured paper, muted sage, soft blue and terracotta colours, low contrast and calm so it works as a website background behind a paper-coloured page. Even density everywhere, no focal point, no border.",
    "negative_prompt": "text, letters, watermark, border, frame, vignette, high contrast, animals, people, faces, central motif, photograph"
  }
]
```
