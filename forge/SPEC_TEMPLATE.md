# <Project name>

<!--
Drop a filled-in copy of this into ~/forge/inbox/ on Gemini and it runs.

What makes a spec succeed is the ACCEPTANCE section. The planner turns it into
the verification tests every chunk must pass, and it REJECTS specs with nothing
checkable rather than guessing. Your RLC analyzer prompt worked because every
chunk had a numeric check with a tolerance - aim for that.

You do not need to split the work into chunks yourself; the planner does. Add
a Chunks section only when you want a particular decomposition.
-->

UI: auto
<!-- Does it have a user interface? web (a site or web app), gui (a desktop
     app), game, none, or auto (forge decides from the spec: HTML, WSGI,
     browser, dashboard... means web). With a UI, forge writes a design
     brief (design/DESIGN.md - Claude adapts the house style in
     ~/forge/design to this project), checks the UX after each build and
     writes an illustrated user guide at commit. -->

Serve: make_app(tmp)
<!-- web only, optional: a Python expression giving the WSGI app, after
     `from module import *` (`tmp` is an empty scratch folder, e.g. a data
     dir). Without it forge looks for make_app / create_app / app_factory, or
     `app`, or HTML files that build_outputs writes. Delete for non-web. -->

Help: auto
<!-- yes / no / auto: an indexed, searchable help screen inside the app,
     filled at commit from the user guide (help/help.json). auto = the
     design brief decides (anything beyond one or two plain screens gets
     one). -->

## Goal

One or two paragraphs: what this is and who uses it.

## Screens (optional - web)

The pages the UX check visits first (it follows links from them too), so
screenshots and the accessibility check cover them and the user guide can
show them:

```json
[{"name": "Home", "path": "/"}, {"name": "Settings", "path": "/settings"}]
```

## Constraints

- Language / runtime: Python 3.12, standard library only
- Packages allowed: (anything listed must be in the sandbox image -
  SANDBOX_PIP in install-gemini-llm.sh)
- No network access at verification time. Anything that talks to Home
  Assistant, Frigate or the web must be testable against a stub.

## Interfaces

Names and signatures other code will depend on. Be exact - the coder matches
them literally.

- `ClassName(field: type, ...)` - what it holds
- `function(arg: type) -> type` - what it returns, which errors it raises

## Verbatim code (optional)

Data or code the module must contain EXACTLY (vocabularies, schemas, lookup
tables) goes in a ```python block under a heading containing "copy verbatim",
e.g. `## Vocabulary (copy verbatim)`. forge inserts it into the module as its
own chunk; the coder never retypes it, and any chunk that redefines those
names has the redefinition removed. A heading that also has the WORD test,
tests, testing or fixture - e.g. `## Test fixture and helpers (copy
verbatim)` - is a test fixture: it is left out of the module and defined in
front of every chunk test instead. ("Latest rates" or "Testimonials" are not
fixtures.) ```py fences work too; a block that does not parse stops the run
before planning.

## Acceptance

Checkable facts, ideally with known answers and tolerances. Number them at the
start of the line ("1.", "2)", or labels like "A1:") - forge checks that every
numbered item is given to a chunk test, and REPORT.md lists any that are not:

1. <input> -> <exact expected output>
2. <edge case> -> <expected behaviour, e.g. raises ValueError>
3. <property> - e.g. "magnitude decreases monotonically over the sweep"

Make fixtures look like real data. frigate-digest's sample used whole-second
timestamps, so every test passed while real Frigate floats (1792119600.123)
crashed the alert formatter - only Claude's review caught it. Put realistic
types, junk rows and awkward positions (a bad row in the MIDDLE of a page,
not at the end) into the fixtures themselves.

## Chunks (optional)

Only if you want to fix the decomposition. For each: what it implements,
what it depends on, and whether it is tricky enough to need thinking.

## Assets (optional)

Images to generate on the image servers. Simplest: describe prompt, size and
filename in prose and the planner adds them. For control, put a ```json list
here instead - forge then uses it verbatim and the planner adds no images.
Keys: id, filename, prompt, negative_prompt, width, height, steps, cfg_scale,
sampler_name, seed, enable_hr, hr_scale, hr_upscaler, denoising_strength,
hr_second_pass_steps, timeout (seconds), server (pin to one image server).
See wallpapers-4k.md. Two more keys forge handles itself:

- `reference_images`: a list of photos the image should follow - a person's
  likeness, a style, an object. Paths are absolute, or relative to ~/forge
  (keep them in ~/forge/refs/<project>/). They stay on your own servers. Use
  photos of about 1-2 megapixels, and only of people who agreed to it.
- `tileable`: true makes a seamless tile (a page background): forge renders
  the image, repaints the seam where its edges meet, and keeps everything
  else exactly. Give width and height; `tile_strength` (default 0.6) sets how
  freely the seam is repainted. Skip hires for tiles.

For a geometric background (leaded glass, a grid), an SVG pattern written by
the code itself is often crisper, smaller and themeable - consider asking for
that instead.

Changing a finished project, or starting a new one from it: see
CHANGE_TEMPLATE.md and `forge change`.

If the module defines `build_outputs(out_dir)`, forge runs it after the
build, copies images to `out_dir/assets/`, renders any SVG to PNG, shows the
PNGs to Claude's review and commits everything under `outputs/`.
