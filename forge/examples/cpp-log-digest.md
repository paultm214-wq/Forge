# cpp-log-digest

Toolchain: c-cpp
License: MIT
Prior art: none

<!--
A fast command-line digest of the lab's llama-server and sd-server logs:
- requests per minute;
- token rates;
- the slowest requests;
- errors.
Written as a C++20 library with a thin CLI. It tries the c-cpp toolchain:
- CMake;
- GoogleTest through CTest;
- ASan/UBSan with GCC and Clang;
- valgrind;
- clang-tidy and cppcheck;
- coverage;
- Debian's fmt and nlohmann_json.
The log line formats are given below; the tests use only lines built from
them.
-->

## Goal

`logdigest FILE...` reads log files, or stdin when FILE is `-`, and
prints a summary. `--json` prints the same summary as JSON. A 1 GB log
streams through in constant memory.

## Constraints

- C++20, with the logic in a library and `main.cpp` thin.
- Libraries: fmt (find_package(fmt)), nlohmann_json and GoogleTest. No
  others.
- **Accepted lines** (anything else is counted as `unparsed`):
  - `[2026-11-05 14:03:22.123] request id=7f3a model=tiel-coder
    prompt_tokens=1234 gen_tokens=567 ms=8421 status=200`
  - `[2026-11-05 14:03:25.001] error id=7f3b msg="CUDA out of memory"`
  - Timestamps are local time. `id` is hex. The numbers are unsigned. A
    `status` of 400 or above counts as a failed request.
- Streaming: one line at a time, using only per-minute counters and a
  fixed-size top-N heap.

## Interfaces

`include/digest.hpp`, namespace `logdigest`:

- `std::optional<Request> parse_request(std::string_view line)` and
  `std::optional<Error> parse_error(std::string_view)`.
  - `Request{minute (int64 minutes since epoch, local), id, model,
    prompt_tokens, gen_tokens, ms, status}`
  - `Error{minute, id, msg}`
- `class Digest`:
  - `void add_line(std::string_view)`;
  - `Summary summary(std::size_t top_n = 5) const`.
  - `Summary` holds:
    - `requests`, `failed`, `errors`, `unparsed`;
    - `busiest_minute` (the "YYYY-MM-DD HH:MM" string) and its count;
    - `tokens_per_second` (total gen_tokens / total ms x 1000, 0 when
      there are no requests);
    - `slowest` (the top_n requests by ms, ties broken by the earlier
      line);
    - `error_messages` (msg -> count).
- `std::string to_text(const Summary&)` and `nlohmann::json
  to_json(const Summary&)`.

## Acceptance

1. `game.cc_build(werror=True)` is ok with GCC, and again with
   `compiler="clang"`.
2. `game.ctest().assert_ok()` with at least 15 GoogleTest tests:
   - each line kind parses, including a msg containing escaped `\"`;
   - malformed lines are rejected: a missing field, a negative number,
     a bad date, a truncated line;
   - the busiest minute;
   - top-N with ties;
   - tokens per second;
   - an empty input;
   - the JSON has the exact keys above.
3. `game.ctest(sanitize="address,undefined")` and the same with
   `compiler="clang"` both pass.
4. `game.cc_run("logdigest", ["-"], input=<200 generated lines>)` prints
   a summary that matches the counts the test computed.
   `game.valgrind(...)` on the same run reports no errors.
5. `game.clang_tidy()` and `game.cppcheck()` return nothing.
6. `game.ctest(coverage=True)` then `game.coverage()`: total line
   coverage of `src/` (main.cpp excluded) is at least 90%.
