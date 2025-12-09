"""
=============================================================================
QueryMind - Command Line Interface (CLI) for Testing
=============================================================================

This script is for TESTING PURPOSES ONLY.

The main application is the web interface (app.py) which provides:
- User-friendly query form
- Dynamic database connection via login
- Schema explorer sidebar
- Result visualization
- Session-based isolation

To use the web application:
    python app.py
    # Then open http://localhost:5000 in your browser

To use this CLI for testing:
    1. Configure your database in db_config.py
    2. Run: python main.py
=============================================================================
"""

from chroma_rag import index_schema_in_chroma, retrieve_schema_context
from db_config import connect_db
from llm_engine import ask_llm
from query_executor import run_query
from schema_loader import load_schema

MAIN_LLM_MODEL = "llama3.1:8b"
EMBEDDING_MODEL = "mxbai-embed-large:latest"
PERSIST_PATH = "./chroma_db"


def main():
    schema_text = load_schema()
    index_schema_in_chroma(schema_text, persist_path=PERSIST_PATH, model=EMBEDDING_MODEL)
    conn = connect_db()

    try:
        while True:
            user_question = input("\nType your question (or 'exit'): ").strip()

            rag_context = retrieve_schema_context(user_question, persist_path=PERSIST_PATH, model=EMBEDDING_MODEL)
            sql_query = ask_llm(user_question, rag_context, model_name=MAIN_LLM_MODEL)
            print(f"\nGenerated SQL:\n{sql_query}\n")
            run_query(sql_query, conn)
    finally:
        conn.close()

if __name__ == "__main__":
    main()