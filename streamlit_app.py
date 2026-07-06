# streamlit_app.py
# ------------------------------------------------------
# Streamlit UI for the self-correcting data-analyst agent: upload a dataset, ask a
# question in plain English, and the agent generates -> executes -> (if needed)
# self-corrects Python code to answer it.
#
# Charts now come back from the agent as in-memory PNG bytes (state["charts"]),
# so the UI no longer scans a shared output/ folder -- which previously showed an
# arbitrary/stale chart when multiple runs or multiple charts were involved.
# ------------------------------------------------------

import os
import tempfile

import pandas as pd
import streamlit as st

from main import agent_app

st.set_page_config(page_title="Agentic Data Analyst", page_icon="🤖", layout="wide")
st.title("🤖 Self-Correcting Data Analyst")
st.markdown(
    "Upload a dataset and ask a question in plain English. The agent writes, runs, "
    "and — if its code errors — rewrites its own Python until it works."
)

uploaded_file = st.file_uploader("Upload your dataset (.xlsx or .csv)", type=["xlsx", "csv"])

if uploaded_file is not None:
    file_name = uploaded_file.name

    # Save the upload to a temp file (not the repo root) and remember its path so the
    # executor can copy it into the sandbox.
    tmp_dir = tempfile.mkdtemp(prefix="agent_upload_")
    data_file_path = os.path.join(tmp_dir, file_name)
    with open(data_file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    try:
        if file_name.endswith(".csv"):
            df = pd.read_csv(data_file_path)
        else:
            df = pd.read_excel(data_file_path)

        st.success(f"File '{file_name}' loaded ({df.shape[0]} rows, {df.shape[1]} columns)")
        schema_info = (
            f"Columns: {list(df.columns)}\n\n"
            f"Data Types:\n{df.dtypes.to_string()}\n\n"
            f"Sample Data (First 2 rows):\n{df.head(2).to_markdown()}"
        )

        with st.expander("View extracted file schema"):
            st.text(schema_info)

        # Output mode: Python analysis (runs code), SQL (runs against in-memory
        # SQLite), or Excel formula (generated for you to paste into a sheet).
        MODES = {
            "🐍 Python analysis": "python",
            "🗃️ SQL query": "sql",
            "📊 Excel formula": "excel",
        }
        mode_label = st.radio("Answer with:", list(MODES.keys()), horizontal=True)
        mode = MODES[mode_label]

        user_query = st.chat_input("E.g. 'What is the average score by gender?' or 'Total sales by region'")

        if user_query:
            with st.chat_message("user"):
                st.write(user_query)

            with st.chat_message("assistant"):
                with st.spinner(f"Agent is generating and testing {mode.upper()}..."):
                    initial_state = {
                        "request": user_query,
                        "file_name": file_name,
                        "data_file_path": data_file_path,
                        "data_summary": schema_info,
                        "mode": mode,
                        "code": "",
                        "error": "",
                        "output": "",
                        "charts": [],
                        "iterations": 0,
                    }
                    final_state = agent_app.invoke(initial_state)

                if final_state.get("error") == "CLARIFICATION":
                    st.warning(final_state["code"])
                elif mode == "excel":
                    # Generate-only: the formula IS the answer (nothing is executed).
                    st.write("**Excel formula** (paste into your spreadsheet):")
                    st.code(final_state["code"], language="text")
                    st.caption("Note: Excel formulas are generated, not executed — verify in your sheet.")
                else:
                    st.write("**Execution complete**")

                    if final_state.get("output"):
                        st.info(f"**Result:**\n\n{final_state['output']}")

                    for name, png_bytes in final_state.get("charts", []):
                        title = name.replace(".png", "").replace("_", " ").title()
                        st.subheader(title)
                        st.image(png_bytes)

                    corrections = max(0, final_state.get("iterations", 1) - 1)
                    lang = "sql" if mode == "sql" else "python"
                    with st.expander(f"View final executed {lang.upper()}"):
                        st.code(final_state["code"], language=lang)
                        st.caption(f"Self-correction attempts used: {corrections}")

    except Exception as e:  # noqa: BLE001 - surface any file/parse error to the user
        st.error(f"Error reading file: {e}")
