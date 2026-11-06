# sql-lab-inventory

Toolchain: sql
License: MIT
Prior art: none

<!--
The lab's parts and loans, in PostgreSQL:
- machines, GPUs, drives and boards;
- who borrowed what;
- a view of what is free;
- a function that checks a part out safely when two people try at once.
It tries the sql toolchain:
- numbered migrations;
- pgTAP tests of the schema, the constraints, a view and the function;
- index use checked with EXPLAIN;
- sqlfluff.
-->

## Goal

The schema records parts and loans. It keeps the invariants: a part is
lent to at most one person at a time, and loans have sane dates. It
answers "what is free" and "what is overdue" quickly.

## Constraints

PostgreSQL 17 throughout.

- **`migrations/001_parts.sql`.** Table `parts`:
  - `id` (identity PK);
  - `kind` (text; CHECK in 'gpu', 'drive', 'board', 'machine', 'cable',
    'other');
  - `label` (text, unique, not empty);
  - `machine_id` (nullable FK to parts.id, ON DELETE SET NULL, for parts
    installed in a machine);
  - `added_at` (timestamptz, default now()).
- **`migrations/002_loans.sql`.** Table `loans`:
  - `id` (identity PK);
  - `part_id` (FK, ON DELETE CASCADE);
  - `borrower` (text, not empty);
  - `out_at` (timestamptz, default now());
  - `due_at` (timestamptz, not null);
  - `back_at` (timestamptz, null while out).
  It has these constraints:
  - `due_at > out_at`;
  - `back_at IS NULL OR back_at >= out_at`;
  - a partial unique index so a part has at most one open loan:
    `UNIQUE (part_id) WHERE back_at IS NULL`.
  It also has an index on `due_at` for open loans.
- **`migrations/003_views.sql`:**
  - view `free_parts` (parts with no open loan): `id, kind, label`;
  - view `overdue` (open loans past due): `loan_id, label, borrower,
    days_late`, where days_late is whole days, rounded down.
- **`migrations/004_checkout.sql`.** Function `checkout(p_label text,
  p_borrower text, p_days int) RETURNS bigint`, the new loan's id:
  - it raises `no such part` (SQLSTATE P0002) for an unknown label;
  - it raises `already out` (P0001) when the part is lent;
  - `p_days` must be 1-60 (`22023` otherwise);
  - two sessions racing for one part must not both succeed. The unique
    index guarantees this; the function turns the violation into
    `already out`.
- **`migrations/005_return.sql`.** Function `checkin(p_label text) RETURNS
  boolean`: true if a loan was closed.

## Acceptance

1. `game.pg_apply()` applies the five migrations in order to a fresh
   database.
2. `game.pg_test().assert_ok()` passes at least 20 pgTAP assertions:
   - **Tables.** `has_table`, `col_type_is`, `col_not_null` and `fk_ok`
     for both tables.
   - **Constraints.** `throws_ok` with SQLSTATE 23514 for a bad kind and
     for `due_at <= out_at`.
   - **Checkout.** It returns an id. A second checkout of the same part
     raises P0001 with "already out". An unknown part raises P0002, and
     `p_days` 0 or 61 raises 22023.
   - **Checkin.** It returns true, then false.
   - **Views.** `results_eq` on `free_parts` before and after a
     checkout, and on `overdue` with a loan back-dated 3.5 days past due
     (days_late 3).
3. With 50,000 parts and 20,000 loans inserted:
   - `game.plan_nodes(game.explain("SELECT * FROM overdue"))` shows the
     open-loan due index, not a sequential scan of loans;
   - `SELECT checkout('gpu-00001', 'paul', 7)` takes under 50 ms.
4. Two concurrent sessions (two psycopg connections in threads) call
   `checkout` for the same label: exactly one succeeds, and the other
   gets "already out".
5. `game.sqlfluff()` returns no findings for `migrations/`.
