# Atomic CSV imports

## Why this change matters

This tool is useful for turning CSV exports into queryable datasets. Analysts
need to distinguish a complete table from a partially loaded one, and scheduled
jobs need an exit status that reflects failures. The original importer caught
file errors and later committed the connection without undoing partial work.

A real-database regression demonstrated the consequence: replacing `sales`
with 10,000 valid CSV rows followed by one row with an extra field destroyed
the original data and committed the first 10,000 replacement rows. The summary
reported zero written rows, counted the failed table as imported, and the CLI
returned success. This reproduced with quick, full and disabled type inference.

## Design

An explicit outer transaction starts before any schema changes. Each file has
a savepoint covering its entire import, including DROP, CREATE and every batch.
On a file error, rollback restores that savepoint and processing continues.
Only successfully released imports contribute to row and table counts.
The outer transaction commits before the success summary or aggregate error.
The connection closes on both normal and exceptional exits.

This follows SQLite's [savepoint semantics](https://www.sqlite.org/lang_savepoint.html)
and Python's [connection context manager behavior](https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager).
The explicit BEGIN matters because implicit Python sqlite3 transactions do not
start for DROP or CREATE alone. Rolling back only INSERT statements would not
restore a dropped table.

Per-file atomicity preserves the existing best-effort, multiple-file workflow.
An all-files rollback would discard valid imports whenever one file is malformed.
The CLI signals failure after saving valid imports; the Python API offers the
same signal through an optional CsvImportError with filenames, underlying
exceptions and committed row count. Existing three-argument calls still return
an integer and continue past file errors.

## Verification

- The original upstream suite passed: 2 tests.
- Initial regression suite before implementation: 9 failed, 5 passed.
- After implementation and boundary checks: 18 passed locally on Windows.
- Tests inspect persisted rows and SQLite schema through fresh connections.
- Covered: failures before and after batch boundaries, failed replacements in
  all typing modes, schema creation errors, indexes restored on failure, good
  files on either side of a failure, empty/missing input, header-only input,
  successful reruns, API error details, CLI exit status, interruption rollback
  and immediate database access by another writer after interruption.

Run `python -m pytest -q` from the checkout. No new runtime dependencies are added.
The CI workflow runs on Windows and Linux with Python 3.9 and 3.13.

## Limits and compatibility

The CLI's nonzero status for failed files is intentional and may change shell
automation that previously assumed success. A nonzero exit does not mean all
files were rolled back: successful files are retained. Repeated filenames can
refer to the same destination; the summary counts successful file imports.

This change does not revise type inference, identifier quoting, schema design,
or CSV validation rules. Successful replacement still discards old indexes.
Imports retain the existing 10,000-row batching and end-of-run commit strategy;
large runs can still hold a write transaction for a long time. No performance
improvement or cross-version compatibility beyond executed tests is claimed.
