# llamalog

<!--
First forge test. Small on purpose: 4-6 chunks, one tricky parsing chunk that
should be planned with thinking, and one image chunk so Mercury is exercised
too. Every number below was checked against a reference implementation.
-->

## Goal

A small command-line tool that reads llama-server logs (a saved
`journalctl --user -u llama-server` dump) and reports prompt-processing and
generation speed. I use it to compare quants and settings on Gemini and
Apollo: run a workload, dump the journal, run llamalog on the dump.

## Constraints

- Language / runtime: Python 3.12, standard library only
- Module name: `llamalog`
- No network access. The CLI reads a file path; tests write their sample
  files under /tmp.
- Speeds are always computed from the ms and token counts. The rate llama.cpp
  prints in parentheses is ignored, because it is rounded and sometimes
  missing.

## Interfaces

- `Timing(kind: str, ms: float, tokens: int)` - a frozen dataclass. `kind` is
  `"prompt"` or `"eval"`.
- `Timing.tok_per_s` - a property: `tokens / (ms / 1000)`, or `0.0` when
  `ms <= 0`.
- `parse_line(line: str) -> Timing | None` - parses one log line, returns
  `None` for anything that is not a prompt-eval or eval timing line.
  - A prompt line contains `prompt eval time = <ms> ms / <n> tokens`.
  - An eval line contains `eval time = <ms> ms / <n> tokens` or
    `eval time = <ms> ms / <n> runs` (older llama.cpp builds say "runs")
    and is NOT a prompt line. Note that every prompt line also contains the
    text "eval time"; a prompt line must never be returned as kind "eval".
  - Anything may come before the timing text (journalctl prefixes such as
    `Oct 15 23:07:01 gemini llama-server[1234]: `), and spacing around `=`
    and `/` varies.
  - `total time = ...` lines, blank lines and other text return `None`.
- `parse_log(text: str) -> list[Timing]` - every timing in the text, in the
  order it appears.
- `summarize(timings: list[Timing]) -> dict` - returns exactly two keys,
  `"prompt"` and `"eval"`. Each maps to a dict with:
  - `count: int` - number of timings of that kind
  - `tokens: int` - sum of tokens
  - `ms: float` - sum of ms
  - `tok_per_s: float` - aggregate rate, `tokens / (ms / 1000)`, `0.0` when
    `ms` is 0
  - `median_tok_per_s: float` - `statistics.median` of the per-timing
    `tok_per_s` values, `0.0` when there are none
- `format_report(summary: dict) -> str` - exactly two lines joined by `\n`,
  with no trailing newline, prompt first:
  `<kind>: <count> requests, <tokens> tokens, <tok_per_s:.1f> tok/s (median <median:.1f>)`
- `main(argv: list[str]) -> int` - `argv` is the argument list without the
  program name; `argv[0]` is the log file path.
  - Prints `format_report(...)` to stdout and returns 0.
  - File has no timing lines: prints `no timing lines found` to stdout and
    returns 1.
  - File does not exist, or `argv` is empty: prints a one-line error to
    stderr and returns 2. Never raises.

## Acceptance

Sample log used by several checks (save it to a file under /tmp for the CLI
checks). Its noise lines must be skipped, and the parenthesised rate on the
second prompt line is deliberately wrong (999.00) to prove it is ignored:

```
Oct 15 23:07:01 gemini llama-server[1234]: srv  update_slots: id  0 | task 12 | new prompt, n_ctx_slot = 163840
Oct 15 23:07:02 gemini llama-server[1234]: prompt eval time =    1000.00 ms /  1500 tokens (    0.67 ms per token,  1500.00 tokens per second)
Oct 15 23:07:06 gemini llama-server[1234]:        eval time =    4000.00 ms /   200 tokens (   20.00 ms per token,    50.00 tokens per second)
Oct 15 23:07:06 gemini llama-server[1234]:       total time =    5000.00 ms /  1700 tokens
prompt eval time =    2000.00 ms /  3200 tokens (    0.62 ms per token,   999.00 tokens per second)
       eval time =    6000.00 ms /   240 runs   (   25.00 ms per token,    40.00 tokens per second)

Oct 15 23:09:40 gemini llama-server[1234]: prompt eval time=500.00 ms/700 tokens (0.71 ms per token, 1400.00 tokens per second)
Oct 15 23:09:42 gemini llama-server[1234]:        eval time =    2000.00 ms /   110 tokens (   18.18 ms per token,    55.00 tokens per second)
Oct 15 23:09:42 gemini llama-server[1234]: srv  log_server_r: request: POST /v1/chat/completions 192.168.50.96 200
```

1. `parse_line("prompt eval time =    1234.50 ms /  2000 tokens (    0.62 ms per token,  1620.09 tokens per second)")`
   -> `Timing("prompt", 1234.5, 2000)`, and its `tok_per_s` is 1620.089
   within 0.01.
2. `parse_line("       eval time =    5000.00 ms /   250 runs   (   20.00 ms per token,    50.00 tokens per second)")`
   -> `Timing("eval", 5000.0, 250)`, `tok_per_s == 50.0`.
3. The same prompt line with the prefix
   `Oct 15 23:07:01 gemini llama-server[1234]: ` parses to the same Timing
   as in check 1, and its kind is `"prompt"`, not `"eval"`.
4. `parse_line` returns `None` for
   `"      total time =    6234.50 ms /  2250 tokens"`, for `""`, and for
   `"srv  log_server_r: request: GET /health 127.0.0.1 200"`.
5. `Timing("eval", 0.0, 10).tok_per_s == 0.0`. Assigning to a field of a
   Timing raises `dataclasses.FrozenInstanceError`.
6. `parse_log(<sample>)` returns exactly 6 timings, kinds in order
   `prompt, eval, prompt, eval, prompt, eval`, with tokens
   `1500, 200, 3200, 240, 700, 110`.
7. `summarize(parse_log(<sample>))`:
   - prompt: count 3, tokens 5400, ms 3500.0, tok_per_s 1542.857 within
     0.001, median_tok_per_s 1500.0 within 0.001
   - eval: count 3, tokens 550, ms 12000.0, tok_per_s 45.833 within 0.001,
     median_tok_per_s 50.0 within 0.001
8. `summarize([])` -> both kinds have count 0, tokens 0, ms 0.0,
   tok_per_s 0.0, median_tok_per_s 0.0.
9. `format_report(summarize(parse_log(<sample>)))` is exactly:
   ```
   prompt: 3 requests, 5400 tokens, 1542.9 tok/s (median 1500.0)
   eval: 3 requests, 550 tokens, 45.8 tok/s (median 50.0)
   ```
10. `format_report(summarize([Timing("prompt", 1000.0, 800)]))` is exactly:
    ```
    prompt: 1 requests, 800 tokens, 800.0 tok/s (median 800.0)
    eval: 0 requests, 0 tokens, 0.0 tok/s (median 0.0)
    ```
11. `main(["/tmp/<sample file>"])` returns 0 and prints the check-9 report.
12. `main` on a /tmp file containing only `hello\nworld\n` prints
    `no timing lines found` and returns 1.
13. `main(["/tmp/does-not-exist.log"])` returns 2 without raising, and so
    does `main([])`.

## Chunks (optional)

Leave the decomposition to the planner, but the `parse_line` chunk should be
planned with thinking: the prompt/eval overlap and the varying spacing are
where a first attempt usually goes wrong.

## Assets (optional)

- Icon for the project README: prompt "flat minimalist app icon, a stopwatch
  overlapping a dark terminal window with a green cursor, simple geometric
  shapes, soft gradient background, no text", size 1024x1024, filename
  `icon.png`.
