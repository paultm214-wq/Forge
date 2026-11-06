# frigate-digest

<!--
Second forge test, deliberately harder than llamalog: paging through an
injected API with retries, interval merging, a quiet-hours window that
crosses midnight, a stateful cooldown, and an integration chunk that ties
them together. Every expected value below was checked against a reference
implementation using the stub and sample exactly as written here.
-->

## Goal

Overnight security digest for Home Assistant. Pull Frigate events for a
time window, merge back-to-back detections into "visits", decide which
visits deserve a phone notification (right label, confident, during quiet
hours, not a repeat of one just sent) and build the Home Assistant notify
payloads plus a plain-text summary of everything that happened.

This module is pure logic. The HTTP call is injected as a `get` function,
so the real Frigate client and the HA automation that posts the payloads
are thin wrappers written later.

## Constraints

- Language / runtime: Python 3.12, standard library only
- Module name: `frigate_digest`
- No network, no real clock, no real sleeping. `get` and `sleep` are always
  injected in tests. Never call `time.time()`; `now` is a parameter.
- No `zoneinfo`: local time is UTC plus a fixed `utc_offset_min` (Central
  Daylight Time is -300).
- Tests must use the stub and sample under **Test fixtures** verbatim.

## Interfaces

### Events

- `Event(id: str, camera: str, label: str, start: float, end: float | None, score: float)`
  - a frozen dataclass. `end` is `None` while the event is still in progress.
- `Event.from_frigate(d: dict) -> Event` - a classmethod reading one row of
  Frigate's `/api/events` response:
  - `id`, `camera`, `label` must be non-empty strings and `start_time` a
    number (not bool), else `ValueError`. A non-dict also raises `ValueError`.
  - `end` comes from `end_time`; missing or `None` gives `None`.
  - `score` is `d["data"]["top_score"]` when `data` is a dict and that value
    is not `None`; otherwise top-level `d["top_score"]` when not `None`;
    otherwise `0.0`.

### Fetching

- `fetch_events(get, after: float, before: float, page_size: int = 50, max_pages: int = 100, retries: int = 3, sleep=time.sleep) -> list[Event]`
  - `get(path: str, params: dict) -> list[dict]`. Every call is
    `get("/api/events", {"after": after, "before": cursor, "limit": page_size, "include_thumbnails": 0})`.
    Frigate returns rows with `after <= start_time < before`, newest first.
  - The first page uses `cursor = before`. After each page,
    `cursor = (smallest start_time among that page's rows) + 0.001`. Count
    every row with a numeric `start_time`, valid event or not. The +0.001
    re-fetches rows that share the boundary start time instead of losing
    them; duplicates are dropped by `id` (first one seen wins).
  - Rows that `Event.from_frigate` rejects are skipped silently.
  - Stop when a page has fewer than `page_size` rows, or adds no new
    events (no valid event whose id was not already collected - this is
    what prevents an infinite loop when more than `page_size` rows share
    one start time), or has no row with a numeric `start_time`.
  - Before making a request, if `max_pages` pages have already been fetched,
    raise `RuntimeError`.
  - Retries: when `get` raises `ConnectionError`, call `sleep(0.5 * 2 ** k)`
    for the k-th retry (k = 0, 1, 2, ...) and try the same page again, up to
    `retries` retries per page. When they are used up, re-raise the last
    `ConnectionError`. Any other exception propagates at once with no retry
    and no sleep.
  - Returns events sorted by `(start, id)`.

### Visits

- `Visit(camera: str, label: str, start: float, end: float, event_ids: tuple[str, ...], peak_score: float)`
  - a frozen dataclass.
- `merge_visits(events: list[Event], gap: float = 30.0, now: float | None = None) -> list[Visit]`
  - An event with `end is None` uses `now` as its end; if `now` is `None`
    that raises `ValueError`. An end earlier than its start is treated as
    the start.
  - Events are grouped by `(camera, label)` and sorted by `(start, id)`.
    An event joins the current visit when `event.start <= visit.end + gap`
    (inclusive). The visit's end is the max of the ends, and `peak_score`
    is the max score.
  - `event_ids` are in `(start, id)` order.
  - Returns visits sorted by `(start, camera, label)`.

### Quiet hours

- `local_hhmm(ts: float, utc_offset_min: int) -> str` - local clock time
  `"HH:MM"`, with the seconds dropped (floor, not rounding).
- `in_quiet_hours(ts: float, start_hhmm: str, end_hhmm: str, utc_offset_min: int) -> bool`
  - The window is `[start, end)` in local minutes-of-day. When start > end
    it crosses midnight. When start == end the window is empty (always
    False).
  - `start_hhmm` and `end_hhmm` must match `HH:MM` with 00-23 and 00-59,
    two digits each, else `ValueError` (e.g. `"7:00"`, `"24:00"`, `"12:60"`).

### Alerts

- `AlertPolicy(labels: frozenset[str], cameras: frozenset[str] | None, min_score: float, quiet_start: str, quiet_end: str, utc_offset_min: int, cooldown: float)`
  - a frozen dataclass. `cameras=None` means every camera.
- `Notifier(policy: AlertPolicy)` - validates `quiet_start` and
  `quiet_end` in its constructor (`ValueError` on bad ones).
  - `consider(visit: Visit) -> dict | None`. Checks, in this order:
    1. If a previous visit with the same `(camera, label)` was alerted and
       this visit starts earlier than that one, raise `ValueError`
       (visits must arrive in start order).
    2. The label is in `labels`, the camera is in `cameras` (or `cameras`
       is `None`), `peak_score >= min_score`, and `visit.start` is in quiet
       hours. If any fails, return `None`.
    3. Cooldown: if this `(camera, label)` was alerted before and
       `visit.start - last_alerted_start < cooldown`, add 1 to
       `self.suppressed` and return `None`. Only alerted visits reset the
       cooldown; suppressed ones do not.
    4. Otherwise record this visit's start as the last alerted start for
       its `(camera, label)` and return the payload.
  - `suppressed: int` - starts at 0.
  - The payload, where n = `len(event_ids)`:
    ```
    {"title": "<Label> at <camera>",
     "message": "<n> detection(s) between <HH:MM start> and <HH:MM end> (peak <score as whole percent>)",
     "data": {"tag": "frigate-<camera>-<label>", "camera": <camera>, "visit_start": <visit.start>}}
    ```
    - `<Label>` is `label.capitalize()`.
    - It says "detection" when n == 1 and "detections" otherwise.
    - The times are `local_hhmm` of the visit's start and end.
    - The percent is formatted `f"{peak_score:.0%}"`.

### Summary and pipeline

- `digest(visits: list[Visit]) -> str` - `"no activity"` for an empty
  list. Otherwise, for each camera in alphabetical order, a line
  `<camera>: <k> visit(s), <m>m <s>s`. The total is
  `int(sum(end - start))` seconds of that camera's visits, and it says
  "visit" when k == 1. Each camera line is followed by one line per label
  in alphabetical order, `  <label>: <count>` (two-space indent). Lines are
  joined by `\n`, with no trailing newline.
- `run(get, policy: AlertPolicy, after: float, before: float, now: float, gap: float = 30.0, sleep=time.sleep) -> tuple[list[dict], str]`
  - `fetch_events(get, after, before, sleep=sleep)` with the other
    parameters at their defaults, then `merge_visits(..., gap=gap, now=now)`,
    then one `Notifier` over the visits in order.
  - Returns `(payloads that were not None, digest(visits))`.

## Test fixtures

Use these verbatim (copy them into each verify block that needs them).

```python
def make_stub(events, fail_first=0, exc=ConnectionError):
    """Fake Frigate /api/events. Returns events with after <= start_time < before,
    newest first (ties: larger id first), at most `limit`. Raises `exc` on the
    first `fail_first` calls. Every call's params are recorded in get.calls."""
    state = {"n": 0}
    def get(path, params):
        assert path == "/api/events", path
        get.calls.append(dict(params))
        state["n"] += 1
        if state["n"] <= fail_first:
            raise exc("stub failure")
        rows = [e for e in events
                if isinstance(e.get("start_time"), (int, float))
                and params["after"] <= e["start_time"] < params["before"]]
        rows.sort(key=lambda e: (e["start_time"], e["id"]), reverse=True)
        return [dict(r) for r in rows[:params["limit"]]]
    get.calls = []
    return get
```

```python
B = 1792119600  # 2026-10-16 03:00:00 UTC = 22:00 on Oct 15 at UTC-5
def ev(i, cam, lab, s, e, sc):
    return {"id": i, "camera": cam, "label": lab, "start_time": B + s,
            "end_time": None if e is None else B + e, "top_score": None,
            "data": {"top_score": sc}}
SAMPLE = [
    ev("e01", "driveway", "person", 600, 640, 0.82),
    ev("e02", "driveway", "person", 660, 700, 0.91),
    ev("e03", "driveway", "person", 760, 790, 0.70),
    ev("e04", "driveway", "car", 600, 900, 0.95),
    ev("e05", "backyard", "person", 3600, None, 0.88),
    ev("e06", "backyard", "person", 3650, 3680, 0.60),
    ev("e07", "porch", "person", -7200, -7100, 0.93),
    ev("e08", "porch", "person", 30000, 30030, 0.90),
    ev("e09", "porch", "person", 28500, 28560, 0.90),
    ev("e10", "porch", "person", 28650, 28700, 0.85),
    ev("e11", "driveway", "dog", 1200, 1260, 0.99),
    ev("e12", "backyard", "person", 10000, 10020, 0.40),
    {"id": "bad1", "label": "person", "start_time": B + 5000, "end_time": B + 5010},
]
NOW = B + 3700
AFTER, BEFORE = B - 86400, B + 86400
```

Tests for the alert and pipeline chunks also define this. Only those tests
should include it, because `AlertPolicy` does not exist yet when the
earlier chunks are tested:

```python
POLICY = AlertPolicy(frozenset({"person"}), None, 0.5, "22:00", "06:00", -300, 600)
```

## Acceptance

Offsets below are seconds relative to `B`. Compare floats within 1e-6.

Events
1. `Event.from_frigate(ev("x", "porch", "cat", 5, 9, 0.75))` ->
   `Event("x", "porch", "cat", B+5, B+9, 0.75)`.
2. `{"id": "y", "camera": "c", "label": "l", "start_time": 1.0, "top_score": 0.4}`
   gives score 0.4 and end `None`; with neither score source present the
   score is 0.0.
3. `from_frigate` raises `ValueError` for: the `bad1` row; `start_time`
   `"10"`; `start_time` `True`; `id` `""`; and the non-dict `None`.
4. Assigning a field of an `Event` or `Visit` raises
   `dataclasses.FrozenInstanceError`.

Fetching
5. `fetch_events(make_stub(SAMPLE), AFTER, BEFORE)` returns 12 events with
   ids in order
   `e07 e01 e04 e02 e03 e11 e05 e06 e12 e09 e10 e08`, using exactly 1 call
   whose params are
   `{"after": AFTER, "before": BEFORE, "limit": 50, "include_thumbnails": 0}`.
6. With `page_size=3` it returns the same 12 events in the same order using
   exactly 7 calls, whose `before - B` values are
   `86400, 28500.001, 5000.001, 3600.001, 760.001, 600.001, -7199.999`
   (within 1e-6).
7. With `page_size=3, max_pages=2` it raises `RuntimeError`.
8. Five rows all with `start_time` 100.0 and ids `t1`..`t5`, with
   `page_size=2`: returns exactly 2 events (`t4`, `t5`) after exactly 2
   calls - it must terminate, not loop.
9. `make_stub(SAMPLE, fail_first=2)` with a recording `sleep`: returns the
   same 12 events and the sleeps are `[0.5, 1.0]`.
10. `make_stub(SAMPLE, fail_first=4)`: raises `ConnectionError` after
    exactly 4 calls with sleeps `[0.5, 1.0, 2.0]`.
11. `make_stub(SAMPLE, fail_first=1, exc=ValueError)`: raises `ValueError`
    after 1 call with no sleeps.

Visits
12. `merge_visits(fetch_events(make_stub(SAMPLE), AFTER, BEFORE), now=NOW)`
    returns exactly these 10 visits, in this order
    (camera, label, start, end, event_ids, peak_score):
    ```
    porch    person  -7200  -7100  (e07,)      0.93
    driveway car       600    900  (e04,)      0.95
    driveway person    600    700  (e01, e02)  0.91
    driveway person    760    790  (e03,)      0.70
    driveway dog      1200   1260  (e11,)      0.99
    backyard person   3600   3700  (e05, e06)  0.88
    backyard person  10000  10020  (e12,)      0.40
    porch    person  28500  28560  (e09,)      0.90
    porch    person  28650  28700  (e10,)      0.85
    porch    person  30000  30030  (e08,)      0.90
    ```
13. The same call with `now=None` raises `ValueError` (e05 is open).
14. Gap boundary: two events on one camera/label, `[0, 10]` and `[40, 50]`,
    merge into one visit with the default gap=30; `[0, 10]` and
    `[40.5, 50]` make two visits.
15. An event with end 5 and start 10 gives a visit with start 10, end 10.

Quiet hours (offset -300, window "22:00"-"06:00")
16. `local_hhmm` at offsets 0, 600, 28799, 28800, -1 is
    `"22:00", "22:10", "05:59", "06:00", "21:59"`.
17. `in_quiet_hours` is True at 0, 600, 28799 and False at 28800, -1,
    -7200, 30000.
18. Same-day window "09:00"-"17:00" at offset 0, with `ts = 86400*k + m*60`:
    True at minutes 540 and 1019, False at 1020 and 539. Window
    "08:00"-"08:00" is always False.
19. `in_quiet_hours(0, "7:00", "08:00", 0)`, `"24:00"`, and `"12:60"` raise
    `ValueError`; so does `Notifier` built with `quiet_start="25:00"`.

Alerts
20. Feeding the visits from check 12 to `Notifier(POLICY)` in order gives
    exactly 3 payloads and `suppressed == 2`:
    ```
    {"title": "Person at driveway", "message": "2 detections between 22:10 and 22:11 (peak 91%)",
     "data": {"tag": "frigate-driveway-person", "camera": "driveway", "visit_start": B+600}}
    {"title": "Person at backyard", "message": "2 detections between 23:00 and 23:01 (peak 88%)",
     "data": {"tag": "frigate-backyard-person", "camera": "backyard", "visit_start": B+3600}}
    {"title": "Person at porch", "message": "1 detection between 05:55 and 05:56 (peak 90%)",
     "data": {"tag": "frigate-porch-person", "camera": "porch", "visit_start": B+28500}}
    ```
    The two suppressed visits are driveway e03 and porch e10. e07 and e08
    are outside quiet hours, e12 is below `min_score`, and car and dog are
    not in `labels`; none of those count as suppressed.
21. With `cameras=frozenset({"porch"})` the same visits give only the porch
    payload and `suppressed == 1`.
22. Cooldown boundary, cooldown 600: alerted visit at start S, then a visit
    at exactly S+600 is alerted (not suppressed); at S+599.9 it is
    suppressed.
23. After an alert for driveway/person at B+600, considering a
    driveway/person visit starting at B+500 raises `ValueError`.

Summary and pipeline
24. `digest(<visits from check 12>)` is exactly:
    ```
    backyard: 2 visits, 2m 0s
      person: 2
    driveway: 4 visits, 8m 10s
      car: 1
      dog: 1
      person: 2
    porch: 4 visits, 4m 0s
      person: 4
    ```
    `digest([])` is `"no activity"`, and a single 65-second visit gives
    `"<camera>: 1 visit, 1m 5s"` followed by its label line.
25. `run(make_stub(SAMPLE, fail_first=1), POLICY, AFTER, BEFORE, NOW, sleep=<recorder>)`
    returns the check-20 payloads and the check-24 digest, with sleeps
    `[0.5]`.

## Chunks (optional)

Leave the decomposition to the planner. Fetching (cursor, dedup, stop
conditions and retries), merging, quiet hours and the cooldown should all
be planned with thinking.
