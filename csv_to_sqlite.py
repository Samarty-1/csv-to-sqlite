"""A script that processes the input CSV files and copies them into a SQLite database."""
import csv
import sqlite3
import os
import sys
import click
import time
from contextlib import closing


__version__ = '2.1.3'

def write_out(msg):
    if write_out.verbose:
        print(msg)


class CsvOptions:
    def __init__(self, 
                 typing_style="quick", 
                 drop_tables=False, 
                 delimiter=",",
                 encoding="utf8",
                 bracket_style="all"):
        self.typing_style = typing_style
        self.drop_tables = drop_tables
        self.delimiter = delimiter
        self.encoding = encoding
        self.bracket_style = bracket_style


class CsvImportError(Exception):
    """One or more files failed; successful files have already been committed.

    failures contains (file_name, exception) pairs. rows_inserted counts only
    rows from successful imports, not rows rolled back for failed files.
    """
    def __init__(self, failures, rows_inserted):
        self.failures = failures
        self.rows_inserted = rows_inserted
        super().__init__("{0} file(s) failed to import; {1} rows committed from successful files"
                         .format(len(failures), rows_inserted))


class CsvFileInfo:
    def __init__(self, path, options = None):
        self.path = path
        self.columnNames = None
        self.columnTypes = None
        self.csvfile = None
        self.reader = None
        self.options = options
        if not options:
            self.options = CsvOptions()
        self.lb, self.rb = ("[", "]") if options.bracket_style == "all" else ("", "")

    def get_table_name(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    def get_minimal_type(self, value):
        try:
            int(value)
            return "integer"
        except ValueError:
            pass
        try:
            float(value)
            return "real"
        except ValueError:
            pass
        return "text"

    def __enter__(self):
        self.csvfile = open(self.path, encoding=self.options.encoding) 
        self.reader = csv.reader(self.csvfile, delimiter=self.options.delimiter)
        return self

    def __exit__(self, *args):
        if self.csvfile:
            self.csvfile.close()

    def get_restarted_reader(self):
        self.csvfile.seek(0)
        return self.reader

    def determine_types(self):
        write_out("Determining types")
        rdr = self.get_restarted_reader()
        self.columnNames = [name for name in next(rdr)]
        cols = len(self.columnNames)
        if self.options.typing_style == 'none':
            self.columnTypes = ["text"] * cols
            return
        self.columnTypes = ["integer"] * cols
        for row in rdr:
            for col in range(cols):
                if self.columnTypes[col] == "text":
                    continue
                col_type = self.get_minimal_type(row[col])
                if self.columnTypes[col] != col_type:
                    if col_type == "text" or \
                            (col_type == "real" and self.columnTypes[col] == "integer"):
                        self.columnTypes[col] = col_type
            if self.options.typing_style == 'quick':
                break

    def save_to_db(self, connection):
        write_out("Writing table " + self.get_table_name())
        cols = len(self.columnNames)
        if self.options.drop_tables:
            try:
                write_out("Dropping table " + self.get_table_name())
                connection.execute('drop table [{tableName}]'.format(tableName=self.get_table_name()))
            except:
                pass
        createQuery = 'create table [{tableName}] (\n'.format(tableName=self.get_table_name()) \
            + ',\n'.join("\t%s%s%s %s" % (self.lb, i[0], self.rb, i[1]) for i in zip(self.columnNames, self.columnTypes)) \
            + '\n);'
        write_out(createQuery)
        connection.execute(createQuery)
        linesTotal = 0
        currentBatch = 0
        reader = self.get_restarted_reader()
        buf = []
        maxL = 10000
        next(reader) #skip headers
        for line in reader:
            buf.append(line)
            currentBatch += 1
            if currentBatch == maxL:
                write_out("Inserting {0} records into {1}".format(maxL, self.get_table_name()))
                connection.executemany('insert into [{tableName}] values ({cols})'
                                    .format(tableName=self.get_table_name(), cols=','.join(['?'] * cols)),
                                    buf)
                linesTotal += currentBatch
                currentBatch = 0
                buf = []
        if len(buf) > 0:
            write_out("Flushing the remaining {0} records into {1}".format(len(buf), self.get_table_name()))
            connection.executemany('insert into [{tableName}] values ({cols})'
                                   .format(tableName=self.get_table_name(), cols=','.join(['?'] * cols)),
                                   buf)
            linesTotal += len(buf)
        return linesTotal


@click.command()
@click.option("--file", "-f",
              type=click.Path(exists=True),
              help="A file to copy into the database. \nCan be specified multiple times. \n"
                   "All the files are processed, including file names piped from standard input.",
              multiple=True)
@click.option("--output", "-o", help="The output database path",
              type=click.Path(),
              default=os.path.basename(os.getcwd()) + ".db")
@click.option('--typing', "-t", 
              type=click.Choice(['full', 'quick', 'none']),
              help="""Determines whether the script should guess the column type (int/float/string supported).
quick: only base the types on the first line
full: read the entire file
none: no typing, every column is string""",
              default='quick')
@click.option("--drop-tables/--no-drop-tables", "-D",
              help="Determines whether the tables should be dropped before creation, if they already exist"
                   " (BEWARE OF DATA LOSS)",
              default=False)
@click.option("--verbose", "-v",
              is_flag=True,
              help="Determines whether progress reporting messages should be printed",
              default=False)
@click.option("--delimiter", "-x",
              help="Choose the CSV delimiter. Defaults to comma. Hint: for tabs, in Bash use $'\\t'.",
              default=",")
@click.option("--encoding", "-e",
              help="Choose the input CSV's file encoding. Use the string identifier Python uses to specify encodings, e.g. 'windows-1250'.",
              default="utf8")
@click.option('--bracket-style', 
              type=click.Choice(['all', 'none']),
              help="""Determines whether all the column names should be wrapped in brackets, or none of them should be.
Keep in mind that if you select 'none', it is up to you to ensure the CSV's column names are also valid SQLite column names.

all: wrap all.
none: no brackets""",
              default='all')

def start(file, output, typing, drop_tables, verbose, delimiter, encoding, bracket_style):
    """A script that processes the input CSV files and copies them into a SQLite database.
    Each file is copied into a separate table. Column names are taken from the headers (first row) in the csv file.

    If file names are passed both via the --file option and standard input, all of them are processed.

    For example in PowerShell, if you want to copy all the csv files in the current folder
    into a database called "out.db", type:

        ls *.csv | % FullName | csv-to-sqlite -o out.db
    """
    write_out.verbose = verbose
    files = list(file)
    if not sys.stdin.isatty():
        files.extend(list(sys.stdin))
    if not files:
        print("No files were specified. Exiting.")
        return
    options = CsvOptions(typing_style=typing, drop_tables=drop_tables, delimiter=delimiter, encoding=encoding, bracket_style=bracket_style)
    try:
        write_csv(files, output, options, raise_on_error=True)
    except CsvImportError as exc:
        raise click.ClickException(str(exc)) from exc


def write_csv(files, output, options, *, raise_on_error=False):
    """Import each CSV atomically and return the number of committed rows.

    Failed files leave their destination tables unchanged; other files are
    still attempted. Set raise_on_error=True to raise CsvImportError after
    committing successful files. Connection and commit errors always propagate.
    """
    write_out("Output file: " + output)
    write_out("Typing style: " + options.typing_style)
    totalRowsInserted = 0
    tablesInserted = 0
    failures = []
    startTime = time.perf_counter()
    with closing(sqlite3.connect(output)) as conn:
        with conn:
            # Start before any DDL: sqlite3's implicit transaction only starts
            # on DML, which otherwise leaves DROP/CREATE outside rollback.
            conn.execute('BEGIN')
            with click.progressbar(files) as _files:
                actual = files if write_out.verbose else _files
                for file in actual:
                    conn.execute('SAVEPOINT csv_import')
                    try:
                        file = file.strip()
                        write_out("Processing " + file)
                        with CsvFileInfo(file, options) as info:
                            info.determine_types()
                            rowsInserted = info.save_to_db(conn)
                    except Exception as exc:
                        conn.execute('ROLLBACK TO SAVEPOINT csv_import')
                        conn.execute('RELEASE SAVEPOINT csv_import')
                        failures.append((file, exc))
                        print("Error on table {0}: \n {1}".format(file, exc))
                    else:
                        conn.execute('RELEASE SAVEPOINT csv_import')
                        totalRowsInserted += rowsInserted
                        tablesInserted += 1
    print("Written {0} rows into {1} tables ({2} failed) in {3:.3f} seconds"
          .format(totalRowsInserted, tablesInserted, len(failures), time.perf_counter() - startTime))
    if failures and raise_on_error:
        raise CsvImportError(failures, totalRowsInserted)
    return totalRowsInserted

if __name__ == "__main__":
    start()
else:
    write_out.verbose = False
