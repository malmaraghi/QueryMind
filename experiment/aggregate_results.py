import glob
import json
from statistics import mean


def analyze_results_file(filepath):
    with open(filepath) as file:
        data = json.load(file)
    
    total_cases = len(data)
    matches = sum(1 for record in data if record.get("result_match"))
    avg_context_time = mean([r.get("time_context_seconds", 0) for r in data]) if data else 0
    avg_generation_time = mean([r.get("time_generate_sql_seconds", 0) for r in data]) if data else 0
    
    return {
        "file": filepath,
        "total": total_cases,
        "result_match_rate": matches / total_cases if total_cases else 0,
        "avg_time_context": avg_context_time,
        "avg_time_generate": avg_generation_time
    }


def export_summary_json(summary, filename="summary_report.json"):
    with open(filename, "w") as file:
        json.dump(summary, file, indent=2)
    print(f"\nSummary exported to {filename}")


def main():
    result_files = glob.glob("**/results_*.json", recursive=True)
    
    if not result_files:
        print("No results_*.json files found.")
        return

    print("Found result files:")
    for filepath in result_files:
        print(f"  - {filepath}")

    summary = []
    for filepath in result_files:
        try:
            summary.append(analyze_results_file(filepath))
        except Exception as error:
            print(f"Failed to analyze {filepath}: {error}")

    for item in sorted(summary, key=lambda x: (-x["result_match_rate"], x["avg_time_generate"])):
        print(f"\nFile: {item['file']}")
        print(f"  Total cases: {item['total']}")
        print(f"  Result match rate: {item['result_match_rate']:.2f}")
        print(f"  Avg context time: {item['avg_time_context']:.3f}s")
        print(f"  Avg generation time: {item['avg_time_generate']:.3f}s")

    export_summary_json(summary)


if __name__ == '__main__':
    main()
