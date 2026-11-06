Change: glassinv

<!--
Run AFTER wendy-theme.md is accepted (so this builds on it):
    forge change glassinv claude-integration.md

BEFORE YOU RUN IT - fill in the [[FILL IN: ...]] values in Decisions:
  * the monthly token budget;
  * the location used to localise supplier searches - city level only.
    Do NOT put a street address here: the API only takes an approximate
    location (city, region, country, time zone), and nothing else needs it.

Wendy's key never goes into this spec, the code, the database or git. On
the machine that runs the container:
    mkdir -p secrets && printf '%s' 'sk-ant-...' > secrets/anthropic-key
    chmod 600 secrets/anthropic-key
and uncomment the Claude lines in compose.yaml. The key is billed to her
Anthropic account: tokens at the model's current rate, plus web searches
($10 per 1,000 at the time of writing - check Anthropic's pricing page).
-->

# Ask Claude

## What changes

With her own Anthropic API key configured, Wendy can ask Claude for project
and pattern ideas built from the glass she actually has, and ask it to find
where to buy more of a glass or supply (Claude searches the web and cites
its sources). Optionally, Claude can also make the photo suggestions on the
new-glass page. A monthly token budget stops runaway costs. Without a key,
nothing changes and no "Ask Claude" link appears.

## Decisions

Configuration (`config_from_env` gains a `claude` key)
- `claude` is None unless `GLASSINV_CLAUDE_KEY_FILE` is set. Then it is:
  - `api_key`: that file's contents, stripped;
  - `model`: `GLASSINV_CLAUDE_MODEL`, default `claude-sonnet-5-5`
    (`claude-haiku-5-5` is cheaper and fine for ideas);
  - `monthly_tokens`: int(`GLASSINV_CLAUDE_MONTHLY_TOKENS`), default
    [[FILL IN: e.g. 2000000]] - input plus output tokens per calendar month;
  - `web_search`: false only if `GLASSINV_CLAUDE_WEB_SEARCH` is `off`;
  - `location`: a dict of the non-empty values among
    `GLASSINV_CLAUDE_CITY`, `GLASSINV_CLAUDE_REGION`,
    `GLASSINV_CLAUDE_COUNTRY` (default `US`) and
    `GLASSINV_CLAUDE_TIMEZONE`, under the keys city, region, country,
    timezone. Wendy's values, set in compose.yaml:
    city [[FILL IN: e.g. Austin]], region [[FILL IN: e.g. Texas]],
    country US, timezone [[FILL IN: e.g. America/Chicago]];
  - `vision`: true only if `GLASSINV_CLAUDE_VISION` is `on`.
- `serve()` builds the client below when `claude` is set, and passes it as
  `make_app(..., assistant=<client>, assistant_budget=<monthly_tokens>)`
  (both default None). If `claude.vision` is true and no local vision URL is
  configured, the describer is `claude_describer(<client>)`.

The client - `claude_client(api_key, model="claude-sonnet-5-5", web_search=True, location=None, post=None, max_searches=5)`
- Returns an object with attributes `model` and `web_search` and a method
  `ask(system: str, content: list, search: bool = False, max_tokens: int = 2000) -> dict`.
- `ask` sends `POST https://api.anthropic.com/v1/messages` with headers
  `x-api-key: <key>`, `anthropic-version: 2023-06-01` and
  `content-type: application/json`, and JSON body:
  - `model`, `max_tokens`, `system`;
  - `messages`: `[{"role": "user", "content": content}]`;
  - only when `search` and `web_search` are both true: `tools` =
    `[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}]`,
    plus `"user_location": {"type": "approximate", **location}` in that tool
    when `location` is non-empty.
- If the reply's `stop_reason` is `pause_turn` (a long search paused), it
  appends `{"role": "assistant", "content": <the reply's content>}` to the
  messages and sends again, at most 3 extra times.
- It returns:
  - `text`: every `text` block of the final reply, joined in order with no
    separator;
  - `sources`: `[{"title", "url"}]` from the `web_search_result_location`
    citations on those text blocks, in order, each url once. If there are no
    citations, the `web_search_result` entries of any
    `web_search_tool_result` block across the replies instead;
  - `usage`: `input_tokens`, `output_tokens` and `web_search_requests`
    (`usage.server_tool_use.web_search_requests`, 0 if absent), summed over
    every request it sent.
- `post(url, body: dict, headers: dict) -> dict` defaults to a urllib JSON
  POST with a 300 s timeout. An HTTP error raises
  `ClaudeError(message)` (a new `RuntimeError` subclass) carrying the API's
  `error.message` when present.

The describer - `claude_describer(client) -> callable`
- `describe(data, content_type)` calls `client.ask` with the same system text
  as `vision_describer` (the JSON keys and every allowed VOCAB value) and
  content `[{"type": "image", "source": {"type": "base64", "media_type": content_type, "data": <base64>}}, {"type": "text", "text": "Describe this sheet of glass."}]`,
  `max_tokens` 600, no search. It parses the text from the first `{` to the
  last `}` as JSON and returns it (`{}` if that fails); the existing
  `/api/intake/describe` route still applies `normalize_suggestions`.

Budget
- `Store.add_usage(month: str, input_tokens: int, output_tokens: int, searches: int)`
  adds to that month's row (table `claude_usage`: month TEXT PRIMARY KEY,
  input_tokens, output_tokens, searches, all INTEGER), and
  `Store.usage(month) -> {"input_tokens", "output_tokens", "searches"}`
  (zeros for a month with no row). `month` is `YYYY-MM`, local time.
- Before any Claude request, if this month's input + output tokens are at or
  above `assistant_budget`, the route returns 429
  `{"error": "This month's Claude budget is used up (<used> of <budget> tokens).", "field": null}`
  without calling Claude. After every request, its usage is added.

Routes
- `GET /api/claude` -> `{"enabled", "model", "month", "tokens_used", "token_budget", "searches", "web_search"}`
  (model and token_budget null when disabled). `/api/meta` gains
  `"claude": bool`.
- `POST /api/claude/ideas` with `{"request"?: str <= 1000 chars, "glass_ids"?: [int], "project_id"?: int}`:
  - The user content is one text part: Wendy's request (or "Suggest three
    projects I could make with what I have."), then `INVENTORY:` with one
    line per IN-STOCK glass item, in hue order:
    `- <name> (<brand>): <colour family label>, <base type label>, <colour mix label>, <texture label><; effects: labels><; tags>. Stock: <stock_summary>`
    (missing values left out), then `PATTERNS:` with one line per pattern
    `- <name>: <technique label>, difficulty <n>, <format_inches width> x <height>`
    (missing parts left out). If `glass_ids` are given, a line
    `WANTS TO USE: <names>`; if `project_id`, `PROJECT: <name> - <description>`.
  - System text: a stained-glass project advisor for a hobbyist; suggest
    designs that suit the glass she owns, name the specific pieces to use,
    say when a pattern she owns fits, flag when she would need to buy more;
    plain text, short paragraphs, no markdown tables.
  - No web search. `max_tokens` 1500.
  - 200 `{"answer": text, "usage": {...}}`. An unknown glass or project id
    is 404; a too-long request is 400 (`field` "request").
- `POST /api/claude/find` with `{"query": str (1-300 chars), "glass_id"?: int}`:
  - with `glass_id`, the query is prefixed by that glass's
    `<brand> <product_code> <name>` (missing parts left out);
  - system text: find current places to buy this stained-glass item; prefer
    sellers that ship to or are near the user's location; give prices and
    sheet sizes when the pages show them; say plainly when something looks
    discontinued; plain text;
  - web search on (if enabled), `max_tokens` 1500;
  - 200 `{"answer", "sources", "usage"}`. A blank or too-long query is 400
    (`field` "query"); an unknown glass id is 404.
- Both POST routes: 503 `{"error": "Claude is not configured", ...}` when
  `assistant` is None; 502 with the ClaudeError text when Claude fails.

Pages
- `/assistant` (only linked in the nav when enabled; 404 when disabled):
  - "Project ideas": a textarea, plus the in-stock glass as small toggle
    thumbnails to choose `glass_ids`;
  - "Find supplies": a text field;
  - the month's usage, e.g. "12,345 of 2,000,000 tokens this month".
  - Answers appear below the form. The JS builds them with DOM methods
    and `textContent` (never `innerHTML`): one `<p>` per blank-line
    paragraph, then the sources as `<a href rel="noopener noreferrer" target="_blank">`
    whose text is the title.
- `/glass/<id>` gains a "Find more of this" link to `/assistant?find_glass=<id>`,
  which pre-fills and runs the search. `/projects/<id>` gains "Suggest glass
  for this" to `/assistant?ideas_project=<id>`. Both only when enabled.

Privacy
- Claude receives only what each request needs: the inventory summary
  above (names, brands, colours, types, stock), the typed request, or one
  photo when the describer runs. Never the key, the data directory path,
  other photos or any address.

Deployment
- `compose.yaml` gains commented-out lines: the secrets mount
  `./secrets/anthropic-key:/run/secrets/anthropic-key:ro`,
  `GLASSINV_CLAUDE_KEY_FILE=/run/secrets/anthropic-key`, the model, the
  monthly budget and the four location variables with Wendy's values.
- `README.md` gains "Ask Claude": what it does, the key file setup above,
  that the key's owner pays per use, the monthly budget, and what is sent.
- `.gitignore`-style note in the README: never commit `secrets/`.

## Acceptance

Tests use `fake`, a `post` stand-in that records `(url, body, headers)` and
returns queued replies. `R(text, stop="end_turn", inp=1000, out=200, searches=0, extra=())`
means a reply `{"content": [*extra, {"type": "text", "text": text}], "stop_reason": stop, "usage": {"input_tokens": inp, "output_tokens": out, "server_tool_use": {"web_search_requests": searches}}}`.
`client = claude_client("sk-ant-test", post=fake, location={"city": "Austin", "region": "Texas", "country": "US"})`.
Fixture app with a client: `make_app(d, assistant=client, assistant_budget=100000)`
plus `load_fixture`. `MONTH = time.strftime("%Y-%m")`.

C1. `client.ask("sys", [{"type": "text", "text": "hi"}])` with reply
    `R("hello")` returns text "hello", sources [], usage
    `{"input_tokens": 1000, "output_tokens": 200, "web_search_requests": 0}`.
    `fake` got url `https://api.anthropic.com/v1/messages`, headers with
    `x-api-key: sk-ant-test` and `anthropic-version: 2023-06-01`, and a body
    with model `claude-sonnet-5-5`, max_tokens 2000, system "sys" and no
    `tools` key.
C2. With `search=True` the body's tools are exactly
    `[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5, "user_location": {"type": "approximate", "city": "Austin", "region": "Texas", "country": "US"}}]`.
    With `claude_client(..., web_search=False)` and `search=True` there is no
    `tools` key.
C3. Pause and sources: first reply
    `{"content": [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "x"}}, {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [{"type": "web_search_result", "url": "https://a.example/g", "title": "A Glass"}]}], "stop_reason": "pause_turn", "usage": {"input_tokens": 1000, "output_tokens": 50, "server_tool_use": {"web_search_requests": 1}}}`,
    then
    `{"content": [{"type": "text", "text": "Delphi has it ", "citations": [{"type": "web_search_result_location", "url": "https://b.example/d", "title": "Delphi", "cited_text": "x"}]}, {"type": "text", "text": "for $30."}], "stop_reason": "end_turn", "usage": {"input_tokens": 2000, "output_tokens": 100}}`.
    `ask(..., search=True)` sends twice (the second body has two messages,
    the second with role "assistant") and returns text
    `"Delphi has it for $30."`, sources `[{"title": "Delphi", "url": "https://b.example/d"}]`
    and usage `{"input_tokens": 3000, "output_tokens": 150, "web_search_requests": 1}`.
C4. A reply with no citations after a search result block gives sources
    from that block: `[{"title": "A Glass", "url": "https://a.example/g"}]`.
C5. `fake` raising `urllib.error.HTTPError` with body
    `{"error": {"message": "invalid x-api-key"}}` makes `ask` raise
    `ClaudeError` whose text contains "invalid x-api-key"; through
    `POST /api/claude/ideas` that is 502.
C6. On the fixture app, `POST /api/claude/ideas` with `{"request": "a window for the kitchen", "glass_ids": [4, 1]}`
    and reply `R("Try a tulip panel.")` returns 200 with answer
    "Try a tulip panel.". The user text sent contains
    "a window for the kitchen", "Cobalt Blue Cathedral",
    "1 full sheet · 2 scraps", "WANTS TO USE: Teal Ring Mottle, Cobalt Blue Cathedral"
    and "INVENTORY:", and does NOT contain "Amber Granite" (out of stock).
    Within INVENTORY the in-stock items appear in hue order: "Red Streaky
    Iridized" before "Green Glue Chip" before "Teal Ring Mottle" before
    "Cobalt Blue Cathedral" before "Plum Fracture-Streamer".
C7. After C6, `GET /api/claude` returns enabled true, model
    "claude-sonnet-5-5", month MONTH, tokens_used 1200, token_budget 100000,
    searches 0, web_search true. `store.usage(MONTH)` agrees.
C8. `POST /api/claude/find` with `{"query": ""}` -> 400 field "query";
    with `{"query": "x" * 301}` -> 400; with `{"query": "more", "glass_id": 99}`
    -> 404; with `{"query": "same sheet", "glass_id": 2}` and reply C3's pair
    -> 200 with the C3 answer and sources, and the user text sent starts
    with "Wissmach FX-102 Wispy White Opal".
C9. Budget: after `store.add_usage(MONTH, 100000, 0, 0)`, both POST routes
    return 429 with "budget is used up" in the error, and `fake` is not
    called.
C10. Without a client (`make_app(d)`): both POST routes return 503,
    `GET /api/claude` has enabled false, `/api/meta` has `"claude": false`,
    `GET /assistant` is 404, and `GET /glass/1` has no "Find more of this".
C11. The key never leaks: with the fixture app, "sk-ant-test" appears in
    none of `GET /api/claude`, `/api/meta`, `/`, `/assistant`, `/glass/1`.
C12. `claude_describer(client)(JPEG, "image/jpeg")` with reply
    `R("Here:\n```json\n{\"color_family\": \"Teal\"}\n```")` returns
    `{"color_family": "Teal"}`; the request's content has an image block with
    media_type "image/jpeg" and data starting `/9j/`, and max_tokens 600.
    Reply `R("no idea")` gives `{}`.
C13. `config_from_env({})` has `claude` None. With
    `GLASSINV_CLAUDE_KEY_FILE` pointing to a file containing `"sk-ant-x\n"`,
    `GLASSINV_CLAUDE_CITY=Austin` and `GLASSINV_CLAUDE_VISION=on`, `claude`
    is `{"api_key": "sk-ant-x", "model": "claude-sonnet-5-5", "monthly_tokens": <the default above>, "web_search": true, "location": {"city": "Austin", "country": "US"}, "vision": true}`.
C14. `GET /assistant` on the fixture app returns 200 HTML containing
    `textContent` in the served JS (`/static/app.js`) and no `innerHTML`
    assignment anywhere in that JS's assistant code.
C15. `build_outputs` still returns four paths; `compose.yaml` contains
    `GLASSINV_CLAUDE_KEY_FILE` and `anthropic-key` (commented out);
    `README.md` contains `GLASSINV_CLAUDE_MONTHLY_TOKENS` and `secrets/`.
C16. Every earlier acceptance item (the original 1-36 and T1-T10) still
    passes.
