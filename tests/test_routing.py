from langgraph.graph import END

from main import MAX_ITERATIONS, route_after_generate, route_execution


def _state(error, iterations):
    return {"error": error, "iterations": iterations}


def test_clarification_ends():
    assert route_execution(_state("CLARIFICATION", 1)) == END


def test_success_ends():
    assert route_execution(_state(None, 1)) == END


def test_error_below_max_reflects():
    assert route_execution(_state("some traceback", 1)) == "reflect"


def test_error_at_max_ends():
    assert route_execution(_state("some traceback", MAX_ITERATIONS)) == END


# --- route_after_generate (mode dispatch) ---

def test_excel_mode_ends_without_execution():
    assert route_after_generate({"mode": "excel", "error": ""}) == END


def test_sql_mode_goes_to_execute():
    assert route_after_generate({"mode": "sql", "error": ""}) == "execute"


def test_python_mode_goes_to_execute():
    assert route_after_generate({"mode": "python", "error": ""}) == "execute"


def test_clarification_ends_after_generate():
    assert route_after_generate({"mode": "python", "error": "CLARIFICATION"}) == END
