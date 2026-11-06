"""Exercise persisted database state, including failures across batch boundaries."""
from contextlib import closing
import sqlite3

from click.testing import CliRunner
import pytest

import csv_to_sqlite as importer


def write_file(tmp_path, name, contents):
    target = tmp_path / name
    target.write_text(contents, encoding='utf8')
    return str(target)


def query(database, sql):
    with closing(sqlite3.connect(str(database))) as connection:
        return connection.execute(sql).fetchall()


def seed_database(database):
    with closing(sqlite3.connect(str(database))) as connection:
        connection.executescript("""
            CREATE TABLE sales (id INTEGER PRIMARY KEY, value TEXT);
            CREATE INDEX sales_value ON sales(value);
            INSERT INTO sales VALUES (42, 'original');
        """)


@pytest.mark.parametrize('valid_rows', [1, 10000, 10001])
def test_failed_new_table_leaves_no_partial_table(tmp_path, valid_rows):
    source = write_file(tmp_path, 'sales.csv', 'id,value\n' +
                        '1,new\n' * valid_rows + 'bad,row,extra\n')
    database = tmp_path / 'output.db'
    assert importer.write_csv([source], str(database), importer.CsvOptions()) == 0
    assert query(database, "SELECT name FROM sqlite_master WHERE name='sales'") == []


@pytest.mark.parametrize('typing_style', ['quick', 'full', 'none'])
def test_failed_replacement_restores_rows_schema_and_index(tmp_path, typing_style):
    database = tmp_path / 'output.db'
    seed_database(database)
    schema = query(database, 'SELECT type, name, sql FROM sqlite_master ORDER BY name')
    source = write_file(tmp_path, 'sales.csv', 'id,value\n' +
                        '1,new\n' * 10000 + 'bad,row,extra\n')
    options = importer.CsvOptions(drop_tables=True, typing_style=typing_style)
    assert importer.write_csv([source], str(database), options) == 0
    assert query(database, 'SELECT * FROM sales') == [(42, 'original')]
    assert query(database, 'SELECT type, name, sql FROM sqlite_master ORDER BY name') == schema


def test_valid_files_before_and_after_failure_are_committed(tmp_path, capsys):
    first = write_file(tmp_path, 'first.csv', 'id\n1\n2\n')
    broken = write_file(tmp_path, 'broken.csv', 'id\n3\n4,extra\n')
    last = write_file(tmp_path, 'last.csv', 'id\n5\n')
    database = tmp_path / 'output.db'
    assert importer.write_csv([first, broken, last], str(database), importer.CsvOptions()) == 3
    assert query(database, 'SELECT * FROM first') == [(1,), (2,)]
    assert query(database, 'SELECT * FROM last') == [(5,)]
    assert query(database, "SELECT name FROM sqlite_master WHERE name='broken'") == []
    summary = capsys.readouterr().out
    assert '3 rows into 2 tables' in summary
    assert '1 failed' in summary


def test_strict_api_reports_failures_after_committing_successes(tmp_path):
    good = write_file(tmp_path, 'good.csv', 'id\n1\n')
    bad = write_file(tmp_path, 'bad.csv', 'id\n2,extra\n')
    database = tmp_path / 'output.db'
    with pytest.raises(importer.CsvImportError) as caught:
        importer.write_csv([bad, good], str(database), importer.CsvOptions(), raise_on_error=True)
    assert caught.value.rows_inserted == 1
    assert len(caught.value.failures) == 1
    assert caught.value.failures[0][0] == bad
    assert isinstance(caught.value.failures[0][1], Exception)
    assert query(database, 'SELECT * FROM good') == [(1,)]


def test_cli_returns_failure_without_losing_successful_files(tmp_path):
    good = write_file(tmp_path, 'good.csv', 'id\n1\n')
    bad = write_file(tmp_path, 'bad.csv', 'id\n2\n3,extra\n')
    database = tmp_path / 'output.db'
    result = CliRunner().invoke(importer.start, ['-f', bad, '-f', good, '-o', str(database)])
    assert result.exit_code == 1
    assert 'Error:' in result.output
    assert query(database, 'SELECT * FROM good') == [(1,)]
    assert query(database, "SELECT name FROM sqlite_master WHERE name='bad'") == []


def test_successful_replacement_and_rerun_are_not_duplicated(tmp_path):
    database = tmp_path / 'output.db'
    seed_database(database)
    source = write_file(tmp_path, 'sales.csv', 'id,value\n1,new\n2,newer\n')
    options = importer.CsvOptions(drop_tables=True)
    for _ in range(2):
        assert importer.write_csv([source], str(database), options) == 2
        assert query(database, 'SELECT * FROM sales') == [(1, 'new'), (2, 'newer')]


def test_cli_success_returns_zero(tmp_path):
    good = write_file(tmp_path, 'good.csv', 'id\n1\n')
    result = CliRunner().invoke(importer.start, ['-f', good, '-o', str(tmp_path / 'out.db')])
    assert result.exit_code == 0


def test_empty_and_missing_files_preserve_existing_data(tmp_path):
    database = tmp_path / 'output.db'
    seed_database(database)
    empty = write_file(tmp_path, 'sales.csv', '')
    absent = str(tmp_path / 'absent.csv')
    assert importer.write_csv([empty, absent], str(database), importer.CsvOptions(drop_tables=True)) == 0
    assert query(database, 'SELECT * FROM sales') == [(42, 'original')]


def test_schema_creation_failure_restores_dropped_table(tmp_path):
    database = tmp_path / 'output.db'
    seed_database(database)
    source = write_file(tmp_path, 'sales.csv', 'id,id\n1,2\n')
    assert importer.write_csv([source], str(database), importer.CsvOptions(drop_tables=True)) == 0
    assert query(database, 'SELECT * FROM sales') == [(42, 'original')]
    assert query(database, "SELECT name FROM sqlite_master WHERE name='sales_value'") == [('sales_value',)]


def test_existing_table_without_drop_is_preserved(tmp_path):
    database = tmp_path / 'output.db'
    seed_database(database)
    source = write_file(tmp_path, 'sales.csv', 'id,value\n1,new\n')
    assert importer.write_csv([source], str(database), importer.CsvOptions()) == 0
    assert query(database, 'SELECT * FROM sales') == [(42, 'original')]


def test_header_only_file_is_a_successful_empty_table(tmp_path, capsys):
    source = write_file(tmp_path, 'empty.csv', 'id,value\n')
    database = tmp_path / 'output.db'
    assert importer.write_csv([source], str(database), importer.CsvOptions(), raise_on_error=True) == 0
    assert query(database, 'SELECT * FROM empty') == []
    assert '0 rows into 1 tables (0 failed)' in capsys.readouterr().out


def test_interrupted_run_rolls_back_and_releases_database(tmp_path):
    database = tmp_path / 'output.db'
    seed_database(database)
    source = write_file(tmp_path, 'sales.csv', 'id,value\n1,new\n')

    def interrupted_files():
        yield source
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        importer.write_csv(interrupted_files(), str(database), importer.CsvOptions(drop_tables=True))
    assert query(database, 'SELECT * FROM sales') == [(42, 'original')]
    # Another writer must be able to acquire the database after interruption.
    with closing(sqlite3.connect(str(database), timeout=0)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.rollback()
