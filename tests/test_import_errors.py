import sqlite3

import pytest
from click.testing import CliRunner

import csv_to_sqlite


def test_csv_file_info_imports_with_default_options(tmp_path):
    source = tmp_path / "records.csv"
    source.write_text("first name,age\nAda,36\n", encoding="utf8")

    with sqlite3.connect(":memory:") as connection:
        with csv_to_sqlite.CsvFileInfo(str(source)) as info:
            info.determine_types()
            assert info.save_to_db(connection) == 1
        assert connection.execute("SELECT * FROM records").fetchall() == [("Ada", 36)]


@pytest.mark.parametrize("typing_style", ["quick", "none"])
def test_cli_returns_failure_for_incomplete_csv_rows(tmp_path, typing_style):
    source = tmp_path / "broken.csv"
    source.write_text("a,b\n1\n", encoding="utf8")

    result = CliRunner().invoke(csv_to_sqlite.start, [
        "--file", str(source), "--output", str(tmp_path / "out.db"),
        "--typing", typing_style,
    ])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Written" not in result.output


def test_failed_import_raises_and_rolls_back_all_tables(tmp_path):
    first = tmp_path / "first.csv"
    first.write_text("value\nnew\n", encoding="utf8")
    broken = tmp_path / "records.csv"
    broken.write_text("a,b\n1,2\n3\n", encoding="utf8")
    output = tmp_path / "out.db"
    with sqlite3.connect(str(output)) as connection:
        connection.execute("CREATE TABLE records (value TEXT)")
        connection.execute("INSERT INTO records VALUES ('original')")

    options = csv_to_sqlite.CsvOptions(typing_style="none", drop_tables=True)
    with pytest.raises(sqlite3.ProgrammingError):
        csv_to_sqlite.write_csv([str(first), str(broken)], str(output), options)

    with sqlite3.connect(str(output)) as connection:
        assert connection.execute("SELECT * FROM records").fetchall() == [("original",)]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == [("records",)]


def test_successful_cli_import_commits_rows(tmp_path):
    source = tmp_path / "records.csv"
    source.write_text("a,b\n1,2\n", encoding="utf8")
    output = tmp_path / "out.db"

    result = CliRunner().invoke(csv_to_sqlite.start, [
        "--file", str(source), "--output", str(output),
    ])

    assert result.exit_code == 0, result.output
    assert "Written 1 rows into 1 tables" in result.output
    with sqlite3.connect(str(output)) as connection:
        assert connection.execute("SELECT * FROM records").fetchall() == [(1, 2)]
