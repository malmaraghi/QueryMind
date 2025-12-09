import argparse
import json
import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from chroma_rag import index_schema_in_chroma, retrieve_schema_context
from db_config import connect_db
from llm_engine import ask_llm
from query_executor import run_query
from schema_loader import load_schema


def load_gold_questions(filename="gold_questions.json"):
    script_dir = os.path.dirname(__file__)
    filepath = os.path.join(script_dir, filename)
    with open(filepath, "r") as file:
        return json.load(file)


def run_experiment(llm_model, embedding_model):
    schema_text = load_schema()
    persist_path = f"./chroma_db_{embedding_model.replace(':', '_')}"
    
    print(f"\n=== LLM: {llm_model} ===")
    print(f"Indexing with {embedding_model}...")
    index_schema_in_chroma(schema_text, persist_path=persist_path, model=embedding_model)

    gold_cases = load_gold_questions()
    results = []
    conn = connect_db()

    for case in gold_cases:
        print(f"\nQuestion {case['id']}: {case['question']}")
        
        experiment_result = {
            "id": case["id"],
            "question": case["question"],
            "main_llm": llm_model,
            "embedding_model": embedding_model,
            "gold_sql": case["gold_sql"],
        }

        start_time = time.time()
        rag_context = retrieve_schema_context(
            case["question"], persist_path=persist_path, model=embedding_model
        )
        experiment_result["time_context_seconds"] = round(time.time() - start_time, 3)

        start_time = time.time()
        llm_sql = ask_llm(case["question"], rag_context, model_name=llm_model)
        experiment_result["llm_generated_sql"] = llm_sql
        experiment_result["time_generate_sql_seconds"] = round(time.time() - start_time, 3)

        print(f"Generated SQL:\n{llm_sql}")

        try:
            gold_result = run_query(case["gold_sql"], conn)
        except Exception:
            gold_result = None
        
        try:
            llm_result = run_query(llm_sql, conn)
        except Exception:
            llm_result = None

        experiment_result["result_match"] = (llm_result == gold_result)
        results.append(experiment_result)
        print(f"Result match: {experiment_result['result_match']}")

    output_filename = f"results_{llm_model.replace(':', '_')}_{embedding_model.replace(':', '_')}.json"
    with open(output_filename, "w") as file:
        json.dump(results, file, indent=2)
    print(f"\nResults saved to {output_filename}")

    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--llm', required=True, help='LLM model name (e.g., llama3.1:8b)')
    parser.add_argument('--embedding', required=True, help='Embedding model (e.g., all-minilm:latest)')
    args = parser.parse_args()

    run_experiment(args.llm, args.embedding)


if __name__ == "__main__":
    main()