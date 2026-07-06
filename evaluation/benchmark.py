# evaluation/benchmark.py
# ------------------------------------------------------
# Measures whether the agent's self-correction loop actually helps. Runs a fixed
# set of natural-language questions against the sample datasets and records, per
# task, whether the FIRST generated program ran, whether the FINAL program ran
# (i.e. after self-correction), and how many attempts it took.
#
# "Success" here means "the generated code executed without error" -- the loop's
# own success criterion. It does NOT verify the answer is semantically correct
# (that needs human grading); this measures executable-code rate and, crucially,
# the self-correction RESCUE rate: tasks the model got wrong on the first try but
# fixed on its own.
#
# Run:  python -m evaluation.benchmark
# ------------------------------------------------------

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from main import agent_app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "Sample Datasets")

# difficulty "easy" = straightforward, well-specified. "hard" = phrasings whose
# most obvious pandas solution hits a pandas-3.0 gotcha (e.g. naive df.corr() /
# groupby().mean() now RAISE on mixed-type frames, where pandas 2.x silently
# dropped non-numeric columns) -- designed to exercise the self-correction loop.
BENCHMARK = [
    ("Student Dataset.csv", "What is the average math_score for each gender?", "easy"),
    ("Student Dataset.csv", "How many students are in the dataset?", "easy"),
    ("Student Dataset.csv", "What is the correlation between reading_score and writing_score?", "easy"),
    ("Student Dataset.csv", "Plot the distribution of math_score as a histogram.", "easy"),
    ("Student Dataset.csv", "Which parental_level_of_education has the highest average writing_score?", "easy"),
    ("Student Dataset.csv", "What percentage of students completed the test_preparation_course?", "easy"),
    ("E-Commerce dataset.xlsx", "What is the total Sales for each Product Category?", "easy"),
    ("E-Commerce dataset.xlsx", "Which Region has the highest total Profit?", "easy"),
    ("E-Commerce dataset.xlsx", "Plot total Sales by Month as a bar chart.", "easy"),
    ("E-Commerce dataset.xlsx", "What is the average Discount for each Segment?", "easy"),
    # harder: obvious solution trips a pandas-3.0 gotcha
    ("Student Dataset.csv", "Show a correlation heatmap of the dataset.", "hard"),
    ("Student Dataset.csv", "For each gender, compute the mean of every column in the dataset.", "hard"),
    ("E-Commerce dataset.xlsx", "Create a correlation heatmap of all columns in the dataset.", "hard"),
    ("E-Commerce dataset.xlsx", "For each Region, show the average of every column.", "hard"),
    ("E-Commerce dataset.xlsx", "What is the average number of days between Order Date and Ship Date, per Ship Mode?", "hard"),
    ("E-Commerce dataset.xlsx", "Plot a pie chart of total Sales share by Segment with percentage labels.", "hard"),
]


def build_schema(path: str) -> str:
    df = pd.read_csv(path) if path.endswith(".csv") else pd.read_excel(path)
    return (
        f"Columns: {list(df.columns)}\n\n"
        f"Data Types:\n{df.dtypes.to_string()}\n\n"
        f"Sample Data (First 2 rows):\n{df.head(2).to_markdown()}"
    )


def run_task(file_name: str, question: str, difficulty: str) -> dict:
    path = os.path.join(DATA_DIR, file_name)
    state = {
        "request": question,
        "file_name": file_name,
        "data_file_path": path,
        "data_summary": build_schema(path),
        "code": "", "error": "", "output": "", "charts": [], "iterations": 0,
    }
    final = agent_app.invoke(state)
    error = final.get("error")
    iterations = final.get("iterations", 1)

    if error == "CLARIFICATION":
        outcome = "clarification"
    elif error is None:
        outcome = "first_try" if iterations <= 1 else "rescued_by_correction"
    else:
        outcome = "failed"

    return {"question": question, "dataset": file_name, "difficulty": difficulty,
            "outcome": outcome, "iterations": iterations}


def _rates(results: list) -> dict:
    n = len(results)
    if n == 0:
        return {}
    first_try = sum(r["outcome"] == "first_try" for r in results)
    rescued = sum(r["outcome"] == "rescued_by_correction" for r in results)
    failed = sum(r["outcome"] == "failed" for r in results)
    clar = sum(r["outcome"] == "clarification" for r in results)
    return {
        "n": n,
        "first_try_success": first_try,
        "rescued_by_self_correction": rescued,
        "final_success": first_try + rescued,
        "failed": failed,
        "clarification": clar,
        "first_try_success_rate": round(first_try / n, 3),
        "final_success_rate": round((first_try + rescued) / n, 3),
    }


RESULTS_PATH = os.path.join(ROOT, "evaluation", "benchmark_results.json")


def _write(results: list) -> None:
    """Checkpoint the current results (called after every task so an interrupted
    run loses at most the task in flight, and can resume)."""
    summary = {
        "overall": _rates(results),
        "easy": _rates([r for r in results if r["difficulty"] == "easy"]),
        "hard": _rates([r for r in results if r["difficulty"] == "hard"]),
        "results": results,
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump(summary, f, indent=2)


def _load_done() -> dict:
    """Load already-completed tasks (keyed by question) so a re-run resumes.
    Backfills the difficulty tag for results saved by an older format."""
    if not os.path.exists(RESULTS_PATH):
        return {}
    try:
        data = json.load(open(RESULTS_PATH))
    except Exception:  # noqa: BLE001
        return {}
    by_q = {q: d for _, q, d in BENCHMARK}
    done = {}
    for r in data.get("results", []):
        q = r.get("question")
        if q in by_q and r.get("outcome") not in (None, "crashed"):
            r["difficulty"] = by_q[q]
            done[q] = r
    return done


def main():
    done = _load_done()
    results = list(done.values())
    if done:
        print(f"Resuming: {len(done)} task(s) already complete, will skip them.\n")

    for i, (file_name, question, difficulty) in enumerate(BENCHMARK, 1):
        if question in done:
            print(f"[{i}/{len(BENCHMARK)}] (skip, done) {question}")
            continue
        print(f"[{i}/{len(BENCHMARK)}] ({difficulty}) {file_name}: {question}")
        try:
            res = run_task(file_name, question, difficulty)
        except Exception as e:  # noqa: BLE001
            res = {"question": question, "dataset": file_name, "difficulty": difficulty,
                   "outcome": "crashed", "iterations": None, "error": str(e)}
        print(f"    -> {res['outcome']} (iterations={res['iterations']})")
        results.append(res)
        _write(results)  # checkpoint after every task

    _write(results)
    ov, hard = _rates(results), _rates([r for r in results if r["difficulty"] == "hard"])
    print("\n=== SUMMARY ===")
    print(f"  Overall: {ov['final_success']}/{ov['n']} final success "
          f"({ov['first_try_success']} first-try, {ov['rescued_by_self_correction']} rescued by self-correction)")
    print(f"  Hard tasks: {hard['first_try_success']}/{hard['n']} first-try, "
          f"{hard['rescued_by_self_correction']} rescued, {hard['failed']} failed")
    print(f"\nSaved -> {RESULTS_PATH}")


if __name__ == "__main__":
    main()
