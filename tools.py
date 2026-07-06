# tools.py
# ------------------------------------------------------
# Executes LLM-generated analysis code. The previous version ran the code with
# a bare exec() in this same process, guarded only by a keyword denylist -- which
# is trivially bypassable (e.g. __import__('os')) and, worse, had no timeout, so
# a single `while True:` from the model would hang the whole app forever.
#
# This version runs the code in an ISOLATED SUBPROCESS with:
#   - a hard timeout (kills runaway / infinite-loop code),
#   - a throwaway temp working directory (the data file is copied in, charts are
#     collected out; the model's code can't see or clobber the repo),
#   - the keyword denylist kept as cheap defense-in-depth (not the primary control).
#
# This is a real, honest improvement over in-process exec, but it is NOT a true
# security sandbox: the subprocess still runs with the user's OS permissions, so a
# determined payload could still touch the filesystem via an absolute path. True
# isolation would need a container / gVisor / firejail. See the README.
# ------------------------------------------------------

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import pandas as pd

EXECUTION_TIMEOUT_SECONDS = 30

# Cheap pre-filter (defense in depth). Now also blocks __import__/importlib, which
# were the obvious ways to bypass the old "import os" check.
FORBIDDEN_KEYWORDS = [
    "import os", "import sys", "import subprocess", "import shutil", "import socket",
    "importlib", "__import__", "os.", "sys.", "subprocess", "shutil", "socket.",
    "eval(", "exec(", "open(", "input(",
]

# Injected before the model's code so it can call sanitize_filename() without an
# import (the code runs in a temp dir where this project's modules aren't importable),
# and so matplotlib always uses a headless backend.
_PREAMBLE = """\
import matplotlib
matplotlib.use("Agg")
import re as _re


def sanitize_filename(title):
    s = str(title).lower()
    s = _re.sub(r"[^a-z0-9\\s]", "", s)
    s = _re.sub(r"\\s+", "_", s)
    return s.strip("_")
"""


def sanitize_filename(title: str) -> str:
    """Convert an arbitrary title to a safe, lowercase, underscore-separated name."""
    safe_name = str(title).lower()
    safe_name = re.sub(r"[^a-z0-9\s]", "", safe_name)
    safe_name = re.sub(r"\s+", "_", safe_name)
    return safe_name.strip("_")


def _strip_markdown(code: str) -> str:
    clean = re.sub(r"^```python\s*", "", code, flags=re.MULTILINE)
    clean = re.sub(r"^```\s*", "", clean, flags=re.MULTILINE)
    return clean.strip()


def execute_python_code(code: str, data_file_path: str | None = None, timeout: int = EXECUTION_TIMEOUT_SECONDS) -> dict:
    """Run generated code in an isolated subprocess.

    Returns {"status": "success"|"error", "output": str, "charts": [(name, png_bytes)]}.
    """
    clean_code = _strip_markdown(code)

    for keyword in FORBIDDEN_KEYWORDS:
        if keyword in clean_code:
            return {
                "status": "error",
                "output": f"SecurityViolation: use of '{keyword}' is not allowed in this environment.",
                "charts": [],
            }

    workdir = tempfile.mkdtemp(prefix="agent_exec_")
    try:
        out_dir = os.path.join(workdir, "output")
        os.makedirs(out_dir, exist_ok=True)

        # Copy the dataset in under the exact name the prompt told the model to use.
        if data_file_path and os.path.exists(data_file_path):
            shutil.copy(data_file_path, os.path.join(workdir, os.path.basename(data_file_path)))

        script_path = os.path.join(workdir, "_run.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(_PREAMBLE + "\n" + clean_code + "\n")

        try:
            proc = subprocess.run(
                [sys.executable, script_path],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "output": f"TimeoutError: code did not finish within {timeout} seconds (possible infinite loop).",
                "charts": [],
            }

        if proc.returncode != 0:
            return {"status": "error", "output": proc.stderr.strip() or proc.stdout.strip(), "charts": []}

        # Collect every chart the code produced (not just the first one).
        charts = []
        for name in sorted(os.listdir(out_dir)):
            if name.lower().endswith(".png"):
                with open(os.path.join(out_dir, name), "rb") as img:
                    charts.append((name, img.read()))

        return {"status": "success", "output": proc.stdout.strip(), "charts": charts}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# Only read-only queries are allowed. The DB is a throwaway in-memory copy, so a
# write couldn't harm the real file anyway, but rejecting non-SELECT keeps the
# feature clearly read-only and gives the model a crisp error to self-correct on.
_SQL_WRITE_KEYWORDS = ["drop ", "delete ", "update ", "insert ", "alter ", "attach ", "create ", "replace ", "pragma "]


def execute_sql_query(query: str, data_file_path: str) -> dict:
    """Run a read-only SQL query against the uploaded dataset.

    The dataset is loaded into an in-memory SQLite table named `data`, so the
    generated SQL actually executes and its errors (bad column, unquoted name,
    syntax) drive the same self-correction loop the Python mode uses.
    Returns {"status", "output", "charts": []}.
    """
    clean = _strip_markdown(query).rstrip(";").strip()
    low = clean.lower()

    if not (low.startswith("select") or low.startswith("with")):
        return {"status": "error", "output": "Only read-only SELECT queries are allowed.", "charts": []}
    for kw in _SQL_WRITE_KEYWORDS:
        if kw in low:
            return {"status": "error", "output": f"Write/DDL operation '{kw.strip()}' is not allowed (read-only).", "charts": []}

    try:
        if data_file_path.endswith(".csv"):
            df = pd.read_csv(data_file_path)
        else:
            df = pd.read_excel(data_file_path)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "output": f"Could not read dataset: {e}", "charts": []}

    con = sqlite3.connect(":memory:")
    try:
        df.to_sql("data", con, index=False)
        result = pd.read_sql_query(clean, con)
        return {"status": "success", "output": result.to_markdown(index=False), "charts": []}
    except Exception as e:  # noqa: BLE001 - surface SQL errors to the reflection step
        return {"status": "error", "output": f"{type(e).__name__}: {e}", "charts": []}
    finally:
        con.close()
