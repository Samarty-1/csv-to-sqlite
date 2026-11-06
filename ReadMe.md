csv-to-sqlite
=============

This is a simple, **datatype-guessing** script that takes CSV files as input and copies their contents into a SQLite database.  
(Column names are taken from the headers (first row) in the csv file.)  
Intended for **Python 3**. (I know it *did* run on Py2 as some people tried, but I haven't tested it.)

## Links 

* [Source on GitHub](https://github.com/zblesk/csv-to-sqlite) 
* [PyPI page](https://pypi.org/project/csv-to-sqlite/) 
* [Introductory blog post with basic intro + a how-to](http://zblesk.net/blog/csv-to-sqlite/)
* [Other related blog posts](https://zblesk.net/blog/tag/csv-to-sqlite/) 

## Overview

Installs via 

```
 pip install csv-to-sqlite
```

To find out more, run

```
 csv-to-sqlite --help
```

If you've installed the package as a dependency for your own script, you can use it like this:

```python
import csv_to_sqlite 

# all the usual options are supported
options = csv_to_sqlite.CsvOptions(typing_style="full", encoding="windows-1250") 
input_files = ["abilities.csv", "moves.csv"] # pass in a list of CSV files
csv_to_sqlite.write_csv(input_files, "output.sqlite", options)
```

## Import failures and safe replacement

Each input file is imported atomically. If reading, type detection, table
creation, or row insertion fails, changes for that file are rolled back.
A failed new import leaves no partial table. With `--drop-tables`, a failed
replacement restores the previous table, including its rows, schema and indexes.
A successful replacement still drops the old table and its indexes as before.

Other input files are still processed. Successful imports are committed at the
end of the run; the summary counts successful imports and failed files separately.
The CLI exits with status **1** if any input file failed, so scheduled jobs can
detect incomplete runs. A run where all imports succeed exits with status **0**.

Python callers keep the existing default: `write_csv` prints per-file errors
and returns the number of rows committed from successful files. To receive
structured failures, opt in with `raise_on_error=True`:

```python
try:
    rows = csv_to_sqlite.write_csv(
        input_files, "output.sqlite", options, raise_on_error=True
    )
except csv_to_sqlite.CsvImportError as error:
    # Successful files are already committed; this is not an all-files rollback.
    print("Rows committed:", error.rows_inserted)
    for filename, cause in error.failures:
        print(filename, cause)
```

Connection, transaction and commit failures propagate rather than producing a
success summary. An interruption before the final commit rolls back the pending
run and closes the connection. A newly created, empty database file may remain.

When retrying, select failed inputs or deliberately use `--drop-tables` to
replace already imported tables; imports do not append to existing tables.

## Tests

From a source checkout:

```sh
python -m pip install click pytest
python -m pytest -q
```

Regression tests use real temporary CSV files and SQLite databases, including
malformed input after the 10,000-row batch boundary. See
[the design and verification notes](IMPROVEMENTS.md) for the reproduced failure
and the transaction design.

