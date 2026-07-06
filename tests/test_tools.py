
import pandas as pd

from tools import execute_python_code, execute_sql_query, sanitize_filename


def test_sanitize_filename():
    assert sanitize_filename("Average Math Score!") == "average_math_score"
    assert sanitize_filename("Sales by Region (2024)") == "sales_by_region_2024"
    assert sanitize_filename("  spaced  out  ") == "spaced_out"


def _make_csv(tmp_path):
    path = tmp_path / "data.csv"
    pd.DataFrame({"gender": ["m", "f", "m"], "score": [10, 20, 30]}).to_csv(path, index=False)
    return str(path)


def test_execute_success_and_captures_stdout(tmp_path):
    data = _make_csv(tmp_path)
    code = "import pandas as pd\ndf = pd.read_csv('data.csv')\nprint('total', df['score'].sum())"
    res = execute_python_code(code, data_file_path=data)
    assert res["status"] == "success"
    assert "total 60" in res["output"]


def test_execute_collects_charts_as_bytes(tmp_path):
    data = _make_csv(tmp_path)
    code = (
        "import pandas as pd\nimport matplotlib.pyplot as plt\n"
        "df = pd.read_csv('data.csv')\n"
        "df.groupby('gender')['score'].mean().plot(kind='bar')\n"
        "plt.savefig('output/' + sanitize_filename('Score By Gender') + '.png')"
    )
    res = execute_python_code(code, data_file_path=data)
    assert res["status"] == "success"
    assert len(res["charts"]) == 1
    name, png = res["charts"][0]
    assert name == "score_by_gender.png"
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG signature


def test_security_block_on_dunder_import(tmp_path):
    res = execute_python_code("__import__('os').system('echo hi')", data_file_path=_make_csv(tmp_path))
    assert res["status"] == "error"
    assert "SecurityViolation" in res["output"]


def test_timeout_kills_infinite_loop(tmp_path):
    res = execute_python_code("while True:\n    pass", data_file_path=_make_csv(tmp_path), timeout=2)
    assert res["status"] == "error"
    assert "TimeoutError" in res["output"]


def test_runtime_error_is_captured_not_raised(tmp_path):
    data = _make_csv(tmp_path)
    res = execute_python_code("import pandas as pd\npd.read_csv('data.csv')['NOPE']", data_file_path=data)
    assert res["status"] == "error"
    assert "KeyError" in res["output"]


def test_markdown_fences_are_stripped(tmp_path):
    data = _make_csv(tmp_path)
    code = "```python\nprint('hello')\n```"
    res = execute_python_code(code, data_file_path=data)
    assert res["status"] == "success"
    assert "hello" in res["output"]


def test_no_chart_leak_between_runs(tmp_path):
    """A run that produces no chart must return an empty chart list, even if a
    previous run produced one -- the old global-output-folder scan could leak."""
    data = _make_csv(tmp_path)
    res1 = execute_python_code(
        "import pandas as pd, matplotlib.pyplot as plt\n"
        "pd.read_csv('data.csv')['score'].plot()\nplt.savefig('output/x.png')",
        data_file_path=data,
    )
    res2 = execute_python_code("print('no chart here')", data_file_path=data)
    assert len(res1["charts"]) == 1
    assert res2["charts"] == []


# --- SQL mode -----------------------------------------------------------------

def _make_spaces_csv(tmp_path):
    path = tmp_path / "sp.csv"
    pd.DataFrame({"Order ID": ["A1", "A2"], "Sales": [10, 20]}).to_csv(path, index=False)
    return str(path)


def test_sql_success_returns_table(tmp_path):
    data = _make_csv(tmp_path)
    res = execute_sql_query("SELECT gender, SUM(score) AS total FROM data GROUP BY gender", data)
    assert res["status"] == "success"
    assert "total" in res["output"]
    assert "40" in res["output"]  # m: 10 + 30


def test_sql_bad_column_is_error_not_raised(tmp_path):
    res = execute_sql_query("SELECT nope FROM data", _make_csv(tmp_path))
    assert res["status"] == "error"
    assert "no such column" in res["output"].lower()


def test_sql_write_is_blocked(tmp_path):
    for q in ("DROP TABLE data", "DELETE FROM data", "UPDATE data SET score=0"):
        res = execute_sql_query(q, _make_csv(tmp_path))
        assert res["status"] == "error"


def test_sql_non_select_is_blocked(tmp_path):
    res = execute_sql_query("PRAGMA table_info(data)", _make_csv(tmp_path))
    assert res["status"] == "error"


def test_sql_unquoted_space_column_errors_but_quoted_works(tmp_path):
    """Reproduces the self-correction scenario: an unquoted column-with-space
    errors, and the quoted version succeeds (what the reflect step should produce)."""
    data = _make_spaces_csv(tmp_path)
    bad = execute_sql_query("SELECT Order ID FROM data", data)
    good = execute_sql_query('SELECT "Order ID" FROM data', data)
    assert bad["status"] == "error"
    assert good["status"] == "success"
