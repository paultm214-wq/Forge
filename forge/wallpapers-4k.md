# wallpapers-4k

<!--
Four 4K desktop wallpapers, one per host, designed as a set of engraved
star-chart plates. Vulcan paints a full-4K hero image for each host; Mercury
paints three shared art layers (star field, engraved rosette, paper grain)
used on all four;
Apollo writes the code that composes each wallpaper as an SVG; forge renders
the SVGs to 3840x2160 PNGs and Claude's review looks at the finished images.

Every layout number and expected value below was checked against a reference
implementation, and the design was rendered and looked at before this spec
was written.
-->

## Goal

Four wallpapers - Gemini, Apollo, Mercury, Vulcan - that read as plates from
one atlas. Each has:

- On the left, small refined type over a dark scrim: a plate number, the
  host's name in wide-spaced light capitals, its role, a hairline rule, six
  hardware rows, and a NETWORK block with the machine's IP address and the
  ports it serves.
- On the right, a fine-lined orrery of the whole lab drawn over the hero
  image: Gemini at the hub, the three workers on their orbits, dashed spokes
  for the forge links, a degree scale around the edge, and THIS machine
  highlighted in its accent colour.
- An inset hairline frame with accent corner marks, and a small legend for
  the orrery at the bottom left.
- Three shared art layers from Mercury, the same files on every plate: a
  star field screened over the hero, a faint paper grain over everything,
  and a guilloche rosette behind the orrery, tinted to the host's accent
  by an SVG colour-matrix filter.

Every wallpaper shows the same orrery geometry and the same type positions;
only the accent, the highlighted host, the hero and the text change.

The module writes the SVGs; forge renders them to PNG. The PNGs are the
wallpapers.

## Constraints

- Language / runtime: Python 3.12, standard library only
- Module name: `wallpapers`
- No network. Images are referenced, never read: the SVG points at
  `assets/<key>.png`, `assets/starfield.png`, `assets/grain.png` and
  `assets/rosette.png`, and forge places the generated images there.
- All visible text is SVG `<text>`. The generated images contain no text.
- Escape text for XML (`xml.sax.saxutils.escape`): Gemini's role contains `&`.
- Copy the host data block below into the module verbatim. It was checked
  against the machines; do not "correct" it.

## Host data (copy verbatim)

```python
W, H = 3840, 2160
BG, TEXT, MUTED = "#0a0e1a", "#e5e7eb", "#94a3b8"
SANS = "Inter, Noto Sans, DejaVu Sans, sans-serif"
MONO = "JetBrains Mono, Hack, Noto Sans Mono, DejaVu Sans Mono, monospace"
HUB = (2760, 1080)
ORBITS = {"apollo": (330, 300), "mercury": (540, 150), "vulcan": (750, 230)}
HOSTS = {
    "gemini": {
        "plate": "I", "name": "Gemini", "role": "Inference & orchestration", "accent": "#38bdf8",
        "ip": "192.168.50.96",
        "specs": [("CPU", "AMD Ryzen 5 9600X"), ("GPU", "2× AMD Radeon RX 9070 XT · 32 GB"),
                  ("Memory", "64 GB DDR5-6000"), ("Board", "ASRock X870 Taichi Creator"),
                  ("Model", "Qwen3.8 27B · UD-Q4_K_XL · MTP · vision"), ("System", "Kubuntu 26.04 · ROCm")],
        "ports": [(8080, "llama-server"), (3000, "Open WebUI"), (8888, "SearXNG"),
                  (10300, "Whisper"), (10200, "Piper"), (10400, "openWakeWord"), (22, "ssh")],
    },
    "apollo": {
        "plate": "II", "name": "Apollo", "role": "Coding worker", "accent": "#22c55e",
        "ip": "192.168.50.8",
        "specs": [("CPU", "AMD Ryzen 7 5800XT"), ("GPU", "AMD Radeon RX 9070 XT · 16 GB"),
                  ("Memory", "32 GB DDR4"), ("Board", "MSI B550M-VC WIFI"),
                  ("Model", "TielCoder 35B-A3B · IQ4_XS · MTP"), ("System", "Kubuntu 26.04 · ROCm")],
        "ports": [(8080, "llama-server"), (22, "ssh")],
    },
    "mercury": {
        "plate": "III", "name": "Mercury", "role": "Image generation", "accent": "#a78bfa",
        "ip": "192.168.50.129",
        "specs": [("CPU", "AMD Ryzen 7 5700X"), ("GPU", "AMD Radeon RX 9060 XT · 16 GB"),
                  ("Memory", "32 GB DDR4"), ("Board", "Gigabyte B550M K"),
                  ("Model", "Qwen-Image 2.1 · Q6_K"), ("System", "Kubuntu 26.04 · Vulkan")],
        "ports": [(7860, "sd-server"), (22, "ssh")],
    },
    "vulcan": {
        "plate": "IV", "name": "Vulcan", "role": "Image generation", "accent": "#f97316",
        "ip": "192.168.50.151",
        "specs": [("CPU", "AMD Ryzen 7 5800XT"), ("GPU", "NVIDIA GeForce RTX 4070 Ti Super · 16 GB"),
                  ("Memory", "64 GB DDR4"), ("Board", "Gigabyte B550I Aorus Pro AX"),
                  ("Model", "Qwen-Image 2.1 · Q6_K"), ("System", "Kubuntu 26.04 · CUDA")],
        "ports": [(7860, "sd-server"), (22, "ssh")],
    },
}
```

## Interfaces

### Geometry

- `text_width(text: str, size: float, spacing: float = 0.0) -> float` -
  `len(text) * size * 0.62 + max(len(text) - 1, 0) * spacing`. `spacing`
  is SVG letter-spacing, added between characters.
- `Box(x: float, y: float, w: float, h: float)` - a frozen dataclass with
  properties `right = x + w` and `bottom = y + h`, and
  `overlaps(other: Box) -> bool`: True only when the intersection has
  positive area. Boxes that merely touch do not overlap.
- `Text(x: float, baseline: float, size: float, text: str, spacing: float = 0.0)`
  - a frozen dataclass with property
  `box = Box(x, baseline - 0.8 * size, text_width(text, size, spacing), size)`.
- `Layout(plate: Text, name: Text, role: Text, rule: Box, rows: tuple[tuple[Text, Text], ...], net_label: Text, ip: Text, ports: tuple[tuple[Text, Text], ...], legend: tuple[Text, Text, Text])`
  - a frozen dataclass.
  - `elements() -> list[Box]` returns the boxes of plate, name, role, rule,
    net_label, ip, the three legend texts, and every row's and port's two
    texts.

### Text column - `compute_layout(key: str) -> Layout`

Unknown key: `KeyError`. A host without exactly 6 spec rows: `ValueError`.

- plate: `Text(240, 300, 28, "PLATE " + plate, 8)`
- name: `Text(240, 470, 150, name.upper(), 24)`
- role: `Text(240, 560, 44, role)`
- rule: `Box(240, 606, 1000, 2)`
- rows, i = 0..5: label `Text(240, 700 + 64*i, 26, label.upper(), 4)`,
  value `Text(480, 700 + 64*i, 34, value)`
- net_label: `Text(240, 1130, 26, "NETWORK", 4)`
- ip: `Text(240, 1210, 56, ip, 2)`
- ports, j = 0..: number `Text(240, 1290 + 48*j, 30, str(port))`, service
  `Text(420, 1290 + 48*j, 30, service)`
- legend: `Text(280, 1880, 22, "THIS MACHINE", 4)`,
  `Text(636, 1880, 22, "LAB HOST", 4)`, `Text(944, 1880, 22, "FORGE LINK", 4)`
- Then, `ValueError` if any box in `elements()` falls outside x >= 240,
  y >= 200, right <= 1500, bottom <= 1900, or if any two of them overlap.

### Orrery

- `HUB = (2760, 1080)` and `ORBITS` are in the host data. ORBITS maps each
  worker to (radius, angle in degrees). Angles are standard SVG ones:
  0 = pointing right, increasing clockwise, because y points down.
- `planet_xy(radius: float, angle_deg: float) -> tuple[float, float]` -
  `(HUB[0] + radius * cos(a), HUB[1] + radius * sin(a))`, where a is the
  angle in radians.
- `ticks() -> list[tuple[float, float, float, float]]` - 72 ticks,
  i = 0..71, at angle 5*i degrees. Each runs from `planet_xy(880, 5*i)` to
  `planet_xy(880 - L, 5*i)`, with L = 32 when i % 6 == 0 and 14 otherwise.
  Returns (x1, y1, x2, y2).

### Tint - `tint_matrix(accent: str) -> str`

The `values` of an feColorMatrix that turns a grey image into shades of
`accent`. With r, g, b = the accent's channels / 255:
`f"{r} 0 0 0 0 {g} 0 0 0 0 {b} 0 0 0 0 0 0 0 1 0"`, each of r, g, b
rounded to 3 decimals and written without trailing zeros. For example,
`tint_matrix("#f97316")` is `"0.976 0 0 0 0 0.451 0 0 0 0 0.086 0 0 0 0 0 0 0 1 0"`.
Every output channel reads the input's RED channel, which is fine because
the rosette is grey.

### SVG - `render_svg(key: str) -> str`

One `<svg>` root with `xmlns="http://www.w3.org/2000/svg"`, `width="3840"`,
`height="2160"` and `viewBox="0 0 3840 2160"`. The accent is the host's
accent. Its children, in this order:

1. `<defs>` holding `<linearGradient id="scrim" x1="0" y1="0" x2="1" y2="0">`
   with three stops, all with `stop-color` BG: offset 0 at opacity 0.9,
   offset 0.75 at opacity 0.9, and offset 1 at opacity 0. After it,
   `<filter id="tint" color-interpolation-filters="sRGB">` containing one
   `<feColorMatrix type="matrix" values="...">`, where the values are
   `tint_matrix(accent)`.
2. Background `<rect>`: x 0, y 0, width 3840, height 2160, fill BG.
3. Hero `<image>`: x 0, y 0, width 3840, height 2160,
   `href="assets/<key>.png"`, `preserveAspectRatio="xMidYMid slice"`. Use
   a plain `href`, not `xlink:href`, on every image.
4. Star field `<image>`: the same frame, `href="assets/starfield.png"`,
   the same preserveAspectRatio, `opacity="0.6"`,
   `style="mix-blend-mode:screen"`.
5. Scrim `<rect>`: x 0, y 0, width 2000, height 2160, `fill="url(#scrim)"`.
   It comes after the stars, so they fade out behind the text.
6. Grain `<image>`: the same frame, `href="assets/grain.png"`, the same
   preserveAspectRatio, `opacity="0.25"`, `style="mix-blend-mode:soft-light"`.
7. Frame `<rect>`: x 80, y 80, width 3680, height 2000, `fill="none"`,
   stroke MUTED, stroke-opacity 0.35, stroke-width 2.
8. Four corner marks: `<polyline>`s with `fill="none"`, stroke = accent and
   stroke-width 4. Their points are, in this order:
   `"150,80 80,80 80,150"`, `"3690,80 3760,80 3760,150"`,
   `"150,2080 80,2080 80,2010"` and `"3690,2080 3760,2080 3760,2010"`.
9. Rosette `<image>`: x 1840, y 160, width 1840, height 1840 (centred on
   HUB), `href="assets/rosette.png"`, `opacity="0.35"`,
   `filter="url(#tint)"`, `style="mix-blend-mode:screen"`. It has no
   preserveAspectRatio, because the rosette is square.
10. `<g id="orrery">`, containing in order:
   - the scale ring: a `<circle>` at HUB, r 880, fill none, stroke MUTED,
     stroke-opacity 0.5, stroke-width 2;
   - one `<line>` per tick, in `ticks()` order, with stroke MUTED,
     stroke-opacity 0.5 and stroke-width 2;
   - one orbit `<circle>` per ORBITS entry, in ORBITS order: at HUB with that
     radius, fill none and stroke-width 2. The host's own orbit has stroke
     = accent at stroke-opacity 0.7; the others have stroke MUTED at 0.3.
     Gemini, the hub, has no orbit, so on its plate all three are MUTED;
   - one spoke `<line>` per ORBITS entry, in ORBITS order, from HUB to
     `planet_xy(radius, angle)`, with stroke MUTED, stroke-opacity 0.4,
     stroke-width 2 and `stroke-dasharray="6 10"`;
   - markers, for gemini at HUB and then each ORBITS entry at its
     `planet_xy`. The host's own marker is TWO circles: r 44 with fill
     none, stroke = accent and stroke-width 2, then r 22 with fill =
     accent. Every other marker is one circle: r 12, fill BG, stroke MUTED,
     stroke-width 3;
   - name labels, in the same order as the markers:
     `Text(x + 64, y + 10, size, NAME.upper(), 6)`. The host's own label is
     size 30 with fill TEXT; the others are size 24 with fill MUTED;
   - degree labels: "0°", "90°", "180°", "270°" at `planet_xy(924, 270)`,
     `planet_xy(924, 0)`, `planet_xy(924, 90)` and `planet_xy(924, 180)`
     respectively. Each is `Text(x, y + 8, 22, label)` with fill MUTED and
     `text-anchor="middle"`.
11. The legend: `<circle cx="250" cy="1872" r="10">` filled with the
   accent; the "THIS MACHINE" text; `<circle cx="610" cy="1872" r="8">`
   with fill BG, stroke MUTED and stroke-width 3; the "LAB HOST" text;
   `<line x1="860" y1="1872" x2="920" y2="1872">` with stroke MUTED,
   stroke-opacity 0.8, stroke-width 2 and `stroke-dasharray="6 10"`; then
   the "FORGE LINK" text. All legend texts have fill MUTED.
12. The text column, in order:
   - plate (fill MUTED);
   - name (fill TEXT, `font-weight="300"`);
   - role (fill = accent);
   - the rule as a `<rect>` filled with the accent at `fill-opacity="0.6"`;
   - for each row, its label (fill MUTED) then its value (fill TEXT);
   - net_label (fill MUTED);
   - ip (fill = accent);
   - for each port, the number (fill TEXT) then the service (fill MUTED).

Every `<text>` has `x` and `y` (the baseline), `font-size` and
`font-family`, taken from its Text. When the Text's spacing is non-zero it
also has `letter-spacing` equal to it. The ip and the port numbers use
`font-family` MONO; every other text uses SANS. Write numbers so that
`float()` gives the value back within 0.001; whole numbers may be written
without a decimal point.

The only `#rrggbb` colours in a host's SVG are BG, TEXT, MUTED and that
host's accent. The tint filter's numbers are not colours in that sense.

### Output - `build_outputs(out_dir: str) -> list[str]`

Creates `out_dir` if needed, writes `<key>.svg` for every host (UTF-8, the
exact `render_svg` text) and returns the paths sorted. forge calls this
after the build, copies the generated images to `<out_dir>/assets/` and
renders each SVG to a PNG.

## Acceptance

Floats compare within 0.001. In checks 10-16, parse the SVG with
`xml.etree.ElementTree`, using namespace `{http://www.w3.org/2000/svg}`.

Geometry
1. `text_width("GEMINI", 150, 24)` is 678; `text_width("ab", 10)` is 12.4;
   `text_width("", 30)` is 0.
2. `Box(0, 0, 10, 10).overlaps(Box(10, 0, 5, 5))` is False (touching);
   `Box(0, 0, 10, 10).overlaps(Box(9, 9, 5, 5))` is True; both directions.
3. `Text(240, 470, 150, "GEMINI", 24).box == Box(240, 350, 678, 150)`
   (compare fields). Box and Text are frozen
   (`dataclasses.FrozenInstanceError` on assignment). Text's spacing
   defaults to 0.

Layout
4. `compute_layout("gemini")` gives:
   - plate `Text(240, 300, 28, "PLATE I", 8)`;
   - name `Text(240, 470, 150, "GEMINI", 24)`;
   - role `Text(240, 560, 44, "Inference & orchestration", 0)`;
   - rule `Box(240, 606, 1000, 2)`;
   - row labels "CPU", "GPU", "MEMORY", "BOARD", "MODEL", "SYSTEM" at
     baselines 700, 764, 828, 892, 956 and 1020;
   - ip `Text(240, 1210, 56, "192.168.50.96", 2)`;
   - 7 ports, the first `(Text(240, 1290, 30, "8080"), Text(420, 1290, 30, "llama-server"))`
     and the last `(Text(240, 1578, 30, "22"), Text(420, 1578, 30, "ssh"))`.
5. For every host, `compute_layout` succeeds. The rightmost element edge is
   1302.12 for Gemini, 1240 for Apollo and Mercury, and 1323.2 for Vulcan.
   The last port's bottom edge is 1584 for Gemini and 1344 for the others.
6. Set consistency: for all four hosts, plate, name and role (x, baseline,
   size, spacing), rule, every row's two texts (x, baseline, size,
   spacing), net_label, ip (x, baseline, size), the legend, and the first
   port's baselines are identical.
7. `compute_layout("nope")` raises `KeyError`. Each of these, added to
   `HOSTS` as a copy of apollo and removed again in a `finally`, raises
   `ValueError`:
   - only 5 spec rows;
   - a 60-character role (it goes past right 1500);
   - 13 ports (the last port overlaps the legend).

Orrery
8. `planet_xy(330, 300)` is (2925, 794.212); `planet_xy(540, 150)` is
   (2292.346, 1350); `planet_xy(750, 230)` is (2277.909, 505.467).
9. `ticks()` has 72 entries. The first is (3640, 1080, 3608, 1080), the
   second is (3636.651, 1156.697, 3622.705, 1155.477), and exactly 12 have
   length 32 (measured from their endpoints).

SVG
10. Every host's SVG parses. The root has width "3840", height "2160" and
    viewBox "0 0 3840 2160". Gemini's raw string contains
    `Inference &amp; orchestration`.
11. The first twelve root children are, in order: defs, rect (background),
    image (hero), image (starfield), rect (scrim, width 2000), image
    (grain), rect (frame), polyline x4, image (rosette). The thirteenth is
    `g` with id "orrery". The images' hrefs are "assets/<key>.png",
    "assets/starfield.png", "assets/grain.png" and "assets/rosette.png".
    The starfield has opacity 0.6 and style "mix-blend-mode:screen"; the
    grain has opacity 0.25 and style "mix-blend-mode:soft-light"; the
    rosette is at (1840, 160) with size 1840x1840, opacity 0.35,
    filter "url(#tint)" and style "mix-blend-mode:screen". The polylines'
    points are exactly the four strings in step 8.
11a. `tint_matrix("#f97316")` is exactly
    `"0.976 0 0 0 0 0.451 0 0 0 0 0.086 0 0 0 0 0 0 0 1 0"`, and
    `tint_matrix("#38bdf8")` is exactly
    `"0.22 0 0 0 0 0.741 0 0 0 0 0.973 0 0 0 0 0 0 0 1 0"`. In each SVG,
    defs contains a filter with id "tint" whose single feColorMatrix has
    type "matrix" and values `tint_matrix(accent)`.
12. Inside the orrery group: 1 + 3 + 5 = 9 circles on every plate (scale
    ring, 3 orbits, 3 small markers + 2 highlight circles); 72 + 3 = 75
    lines, of which exactly 3 have `stroke-dasharray="6 10"`; and 8 texts,
    which are "GEMINI", "APOLLO", "MERCURY", "VULCAN", "0°", "90°", "180°",
    "270°" in that order.
13. Highlighting: on the mercury plate the orbit circle with r 540 has
    stroke "#a78bfa" and stroke-opacity 0.7, and the other two orbits have
    stroke MUTED at 0.3. The circle with r 22 sits at
    `planet_xy(540, 150)` with fill "#a78bfa", and the "MERCURY" label has
    font-size 30 and fill TEXT. On the gemini plate all three orbits are
    MUTED and the r 22 circle sits at HUB.
14. Every `<text>` outside the orrery group appears in exactly this order:
    the 3 legend texts, plate, name, role, the 12 row texts, net_label, ip,
    then 2 per port. Each one's x, y, font-size and letter-spacing (absent
    meaning 0) match its Text in `compute_layout`.
15. Fonts and fills: the ip and port-number texts have font-family MONO
    and every other text has SANS. The name has font-weight "300". The role
    and the ip are filled with the accent; the name, row values and port
    numbers with TEXT; everything else with MUTED. The rule is a rect at
    (240, 606, 1000, 2) with fill = accent and fill-opacity 0.6.
16. Every `#rrggbb` in each SVG (case-insensitive) is one of BG, TEXT,
    MUTED or that host's own accent, and the host's accent does appear. The
    other three hosts' accents never appear.

Output
17. `build_outputs("/tmp/wp_out_test")` (remove the directory first) returns
    exactly the 4 paths `/tmp/wp_out_test/{apollo,gemini,mercury,vulcan}.svg`,
    sorted. Each file's contents equal `render_svg(key)`.

## Chunks (optional)

Leave the decomposition to the planner. The orrery geometry (planet_xy,
ticks) and the layout's bounds and overlap checks should be planned with
thinking. The host data and SVG string-building should not.

## Assets

Two machines, two kinds of image, all pinned with "server":

- **Vulcan** (CUDA, RTX 4070 Ti Super) paints the four heroes at full 4K:
  1920x1088 hires-fixed 2x to 3840x2176, which the SVG's `slice` crops to
  3840x2160. Before the first run, confirm Vulcan can finish a 2x pass with
  `mercury-hires-probe.py 2.0` pointed at it. If it cannot, set the heroes'
  hr_scale to 1.5 here (or in the run's plan.json).
- **Mercury** (Vulkan, RX 9060 XT) paints the three shared layers, which
  are smaller jobs it can finish. Mercury cannot finish a 2x 4K pass: the
  second pass needs ~9.8 GB of working memory per transformer block, and
  the GPU hits "device lost" mid-pass.

forge renders both machines' queues at once while Apollo writes the code.
If Vulcan is not up (booted into Bazzite), Mercury's layers still render,
and the run then stops with the heroes unrendered. Boot Vulcan into Kubuntu
and resume; the finished layers are kept.

The subject sits where the orrery's hub is, about three-quarters of the way
across, so the rings frame it.

The layers are grey or white on black on purpose. The star field is
screen-blended, so black disappears and only the stars remain. The rosette
is screen-blended and tinted per host. The grain is mid-grey, which is
neutral under soft-light.

Prompt rules:
- Name colours in words. Hex codes mean nothing to a diffusion model.
- Keep any mention of text out of the positive prompt. Writing "no text"
  there pulls the model toward text; it belongs in the negative prompt only.
- Keep the left third dark and empty, because the text column sits there.
- Avoid code, terminals and screens, which make the model draw fake glyphs.

```json
[
  {
    "id": "hero-gemini",
    "filename": "gemini.png",
    "prompt": "Two identical hexagonal cores of sky-blue light, mirror-symmetric and interlocking at their edges, joined by bright arcs of energy, slowly orbiting each other. Dark cinematic digital art. Deep charcoal and midnight-navy background with soft volumetric haze. The subject is centred in the right half of the frame, about three-quarters of the way across; the left third is calm, dark, nearly empty negative space. High contrast, fine detail, gentle bloom.",
    "negative_prompt": "text, letters, words, numbers, typography, writing, code, signage, logo, watermark, signature, user interface, screen, monitor, frame, border, people, face, hands, cluttered, blurry, oversaturated, washed out, low contrast",
    "width": 1920,
    "height": 1088,
    "steps": 30,
    "cfg_scale": 3.5,
    "seed": 2102,
    "enable_hr": true,
    "hr_scale": 2.0,
    "denoising_strength": 0.35,
    "hr_second_pass_steps": 20,
    "timeout": 5400,
    "server": "vulcan"
  },
  {
    "id": "hero-apollo",
    "filename": "apollo.png",
    "prompt": "Dozens of thin emerald-green light trails running in precise parallel lines, bending smoothly and converging into one bright glowing rectangular block of green light, like data flowing into a waiting cursor. Dark cinematic digital art. Deep charcoal and midnight-navy background with soft volumetric haze. The subject is centred in the right half of the frame, about three-quarters of the way across; the left third is calm, dark, nearly empty negative space. High contrast, fine detail, gentle bloom.",
    "negative_prompt": "text, letters, words, numbers, typography, writing, code, signage, logo, watermark, signature, user interface, screen, monitor, frame, border, people, face, hands, cluttered, blurry, oversaturated, washed out, low contrast",
    "width": 1920,
    "height": 1088,
    "steps": 30,
    "cfg_scale": 3.5,
    "seed": 2101,
    "enable_hr": true,
    "hr_scale": 2.0,
    "denoising_strength": 0.35,
    "hr_second_pass_steps": 20,
    "timeout": 5400,
    "server": "vulcan"
  },
  {
    "id": "hero-mercury",
    "filename": "mercury.png",
    "prompt": "An artist's palette whose paint dissolves into a spiralling cloud of fine violet and lavender particles, swirling outward like diffusing smoke. Dark cinematic digital art. Deep charcoal and midnight-navy background with soft volumetric haze. The subject is centred in the right half of the frame, about three-quarters of the way across; the left third is calm, dark, nearly empty negative space. High contrast, fine detail, gentle bloom.",
    "negative_prompt": "text, letters, words, numbers, typography, writing, code, signage, logo, watermark, signature, user interface, screen, monitor, frame, border, people, face, hands, cluttered, blurry, oversaturated, washed out, low contrast",
    "width": 1920,
    "height": 1088,
    "steps": 30,
    "cfg_scale": 3.5,
    "seed": 2103,
    "enable_hr": true,
    "hr_scale": 2.0,
    "denoising_strength": 0.35,
    "hr_second_pass_steps": 20,
    "timeout": 5400,
    "server": "vulcan"
  },
  {
    "id": "hero-vulcan",
    "filename": "vulcan.png",
    "prompt": "A blacksmith's anvil glowing along its edges with molten orange light, sparks and embers rising from it in slow spiralling arcs. Dark cinematic digital art. Deep charcoal and midnight-navy background with soft volumetric haze. The subject is centred in the right half of the frame, about three-quarters of the way across; the left third is calm, dark, nearly empty negative space. High contrast, fine detail, gentle bloom.",
    "negative_prompt": "text, letters, words, numbers, typography, writing, code, signage, logo, watermark, signature, user interface, screen, monitor, frame, border, people, face, hands, cluttered, blurry, oversaturated, washed out, low contrast",
    "width": 1920,
    "height": 1088,
    "steps": 30,
    "cfg_scale": 3.5,
    "seed": 2104,
    "enable_hr": true,
    "hr_scale": 2.0,
    "denoising_strength": 0.35,
    "hr_second_pass_steps": 20,
    "timeout": 5400,
    "server": "vulcan"
  },
  {
    "id": "layer-starfield",
    "filename": "starfield.png",
    "server": "mercury",
    "prompt": "A deep night sky filled with thousands of tiny, sharp, pin-point white and pale blue stars of varying brightness, scattered evenly across a pure black background, with a few slightly brighter stars. Astrophotography, very high detail.",
    "negative_prompt": "text, letters, words, numbers, typography, writing, code, signage, logo, watermark, signature, user interface, screen, monitor, frame, border, people, face, hands, cluttered, blurry, oversaturated, washed out, low contrast, nebula, clouds, galaxy, planets, moon, horizon, glow, haze, bokeh, lens flare",
    "width": 1920,
    "height": 1088,
    "steps": 30,
    "cfg_scale": 3.5,
    "seed": 2201,
    "enable_hr": true,
    "hr_scale": 1.5,
    "denoising_strength": 0.35,
    "hr_second_pass_steps": 20,
    "timeout": 5400
  },
  {
    "id": "layer-rosette",
    "filename": "rosette.png",
    "server": "mercury",
    "prompt": "A single intricate circular guilloche rosette, like the engraved security pattern on a banknote: hundreds of fine, thin, overlapping silver-grey lines forming symmetrical waves and petals around the centre, perfectly centred on a pure black background. Flat front view, high contrast, crisp line engraving.",
    "negative_prompt": "text, letters, words, numbers, typography, writing, code, signage, logo, watermark, signature, user interface, screen, monitor, frame, border, people, face, hands, cluttered, blurry, oversaturated, washed out, low contrast, coin, metal surface, shading, 3d, perspective, colour, gold",
    "width": 1344,
    "height": 1344,
    "steps": 30,
    "cfg_scale": 3.5,
    "seed": 2202,
    "timeout": 2700
  },
  {
    "id": "layer-grain",
    "filename": "grain.png",
    "server": "mercury",
    "prompt": "A flat, even texture of fine film grain and subtle paper fibres in neutral mid-grey, uniform across the whole frame.",
    "negative_prompt": "text, letters, words, numbers, typography, writing, code, signage, logo, watermark, signature, user interface, screen, monitor, frame, border, people, face, hands, cluttered, blurry, oversaturated, washed out, low contrast, objects, shapes, pattern, vignette, gradient, stains, colour",
    "width": 1920,
    "height": 1088,
    "steps": 30,
    "cfg_scale": 3.5,
    "seed": 2203,
    "timeout": 2700
  }
]
```

## Visual review

For Claude's review of `outputs/*.png` and `assets/*.png`:

- Heroes: no text, letters, glyph-like marks or watermark anywhere. The
  subject sits right of centre; the left third is dark and calm. The accent
  hue matches the host (sky blue, green, violet, ember orange). Not washed
  out, not blown out.
- Layers: the star field is small sharp points on black, with no nebula,
  planet or text. The rosette is a single centred, symmetrical line pattern
  on black, with no numerals or letters (a real astrolabe would have them;
  this must not). The grain is even and featureless.
- Wallpapers: every line of text is legible against the scrim, including
  the small legend and orrery labels. Nothing is clipped or overlapping.
  The orrery reads as a fine instrument over the hero, not as clutter. The
  four read as one set.
