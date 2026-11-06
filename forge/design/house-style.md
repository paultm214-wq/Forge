# forge house style

The default design standard for everything forge builds with a user
interface: web apps and sites, desktop GUIs and game menus. A project's
design brief (DESIGN.md, written by Claude at planning) adapts it to the
project; where the brief says nothing, this applies. Games add
`game-ui.md`. The colours, type and spacing are in `tokens.css`.

## Principles

1. **One job per screen.** Each screen has a clear purpose and one primary
   action, visibly the most prominent. Secondary actions are quieter.
2. **Show where people are.** Page title, current navigation item, breadcrumbs
   for depth beyond two levels, and the state of anything long-running.
3. **Speak the user's language.** Labels name things the way the user thinks
   of them, not the code's names. Buttons say what they do ("Save project",
   not "Submit" or "OK").
4. **Consistency.** The same thing looks and behaves the same everywhere:
   one style of button for one kind of action, one place for page actions,
   the same words for the same concepts.
5. **Prevent errors, then explain them.** Constrain input (pickers, sensible
   defaults, inline validation on blur), confirm destructive actions, offer
   undo where cheap. An error says what went wrong and how to fix it, next to
   the thing that caused it.
6. **Recognition over recall.** Show options instead of making people
   remember them; keep recent and frequent items close.
7. **Feedback within 100 ms.** Every action visibly does something: a pressed
   state, a spinner after ~1 s, a progress bar for long work, a confirmation
   when it finishes.
8. **Every state is designed**: empty (what goes here and the first step),
   loading, partial, error, offline/unavailable, and success. No blank
   screens, no raw exceptions, no "undefined".
9. **Calm visual design.** Generous whitespace on a 4 px grid, a clear type
   hierarchy, one accent colour, colour used for meaning (not decoration),
   and nothing that moves without reason.
10. **Accessible by default** (WCAG 2.2 AA, below). It is part of quality,
    not an extra.

## Layout

- Content width at most `--content-max` (72rem); text lines at most ~70
  characters (`--measure`).
- Page structure: skip link, header (product name, primary navigation),
  `<main>` with one `<h1>`, footer if useful. Landmarks (`header`, `nav`,
  `main`, `footer`) once each.
- Mobile first: works from 320 CSS px wide with no horizontal scrolling
  (tables collapse with `table.responsive`, toolbars wrap). Breakpoint at
  40rem; nothing hidden on small screens that the task needs.
- Group related things in cards or sections with headings; align to a grid;
  keep spacing consistent (`--space-*` only).

## Typography

- System font stack; body 16 px minimum (`--text-md`), line height 1.55.
- Headings in order (h1 > h2 > h3), never chosen for size alone.
- Sentence case for titles, labels and buttons. No ALL CAPS paragraphs.
- Numbers that are compared line up (tabular figures in tables).

## Colour

- Use the tokens only; no hard-coded colours in components.
- Text 4.5:1 against its background, large text and UI boundaries (input
  borders, focus ring, icons that carry meaning) 3:1 - the tokens already do.
- Never colour alone: an error has an icon or the word "Error"; a status has
  text; a chart has labels or patterns.
- Light and dark themes both work (the tokens switch with the system
  setting).

## Components

- **Buttons**: `.btn` (secondary), `.btn-primary` (one per view),
  `.btn-danger` (destructive, always confirmed). At least 44 px tall;
  icon-only buttons have an `aria-label` and a tooltip.
- **Links** look like links (underlined) and go somewhere; buttons do things.
- **Forms**: every input has a visible `<label>` (placeholder is not a
  label); hints under the label; required fields marked in text; errors
  inline with `aria-invalid="true"` and `aria-describedby`, plus a summary
  at the top for long forms; submit buttons name the action.
- **Tables**: header row with `<th scope="col">`, sortable columns say how
  they are sorted (`aria-sort`), empty table shows an empty state, long
  tables paginate or virtualise.
- **Dialogs**: the native `<dialog>` (focus moves in, Esc closes, focus
  returns); confirmations name the action and the thing ("Delete 3 photos?").
- **Notifications**: inline `.alert` for page-level messages, announced with
  `role="status"` (or `role="alert"` for errors).
- **Navigation**: the current page marked with `aria-current="page"`; the
  same order on every page.

## Accessibility (WCAG 2.2 AA)

- Everything works with the keyboard alone, in a logical order, with a
  visible focus ring (`:focus-visible`) that is never hidden behind sticky
  headers; no keyboard traps.
- Targets at least 24x24 CSS px (44 px preferred for touch).
- Images have `alt` (empty for decorative ones); icons that mean something
  have text.
- Page `lang` set; each page has a unique, descriptive `<title>`.
- Text can be enlarged to 200% without loss of content; reflow at 320 px.
- Motion respects `prefers-reduced-motion`; nothing flashes more than three
  times a second; no autoplaying audio.
- Status messages are announced (`role="status"`), dynamic content updates do
  not steal focus.
- Time limits can be extended or turned off.

## Desktop GUIs (Tk, Qt, GTK, Godot tools)

The same principles with platform conventions: a menu bar with File / Edit /
View / Help where the app has documents; standard shortcuts (Ctrl+S, Ctrl+Z,
Ctrl+F, F1 for help); keyboard focus visible on every control; dialogs with
OK/Cancel in the platform's order and Esc to cancel; window sizes remembered;
text follows the system's scaling.

## Help and documentation

- Apps with more than a couple of screens have an in-app **Help** entry
  (header link, `?` key or F1) that opens a searchable, indexed help page.
  Its content is `help/help.json` (schema below); forge writes the final
  content at commit from the user guide, so the app reads the file rather
  than embedding text.
- Contextual help: a short hint next to anything non-obvious, linking to
  the matching help section (`help#<section-id>`).
- `help/help.json`:

  ```json
  {"title": "App name help", "version": 1,
   "sections": [{"id": "getting-started", "title": "Getting started",
                 "keywords": ["first run", "setup"],
                 "body_md": "Markdown text; images as ![alt](images/x.png)"}]}
  ```

  The help viewer lists the sections (a table of contents), filters them by
  a search box over titles, keywords and text (as you type, case-insensitive,
  showing how many match), and links to each section by id. Python apps load
  `help/help.json` beside the module and fall back to built-in placeholder
  sections when it is missing.

## Implementer checklist (every UI chunk)

- [ ] Uses the tokens in `design/tokens.css` (copied in or served) - no
      stray colours, sizes or fonts.
- [ ] Every screen: one `<h1>`, title, primary action obvious, empty /
      loading / error states designed.
- [ ] Every control reachable and usable by keyboard, focus visible, labels
      on all inputs, alt text on images.
- [ ] Works at 320 px wide and at 200% text size.
- [ ] Error messages say what happened and what to do.
- [ ] Destructive actions confirm; long actions show progress.
