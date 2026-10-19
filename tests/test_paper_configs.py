import glob
import json
import os
import unittest

import context  # noqa: F401

EXP = context.EXPERIMENT
ROOT = context.ROOT


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class PaperConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs = load(os.path.join(EXP, "benchmark", "paper_configs.json"))
        cls.summary = {item["file"]: item for item in load(os.path.join(ROOT, "archive", "pilot-evaluation", "results", "summary_report.json"))}

    def test_exactly_the_historical_configurations(self):
        historical = set()
        for path in glob.glob(os.path.join(ROOT, "archive", "pilot-evaluation", "results", "results_*.json")):
            row = load(path)[0]
            historical.add((row["main_llm"], row["embedding_model"]))
        declared = {(c["llm"], c["embedding"]) for c in self.configs["paper_configurations"]}
        self.assertEqual(declared, historical)
        self.assertEqual(len(declared), 5)

    def test_only_the_three_llms_and_two_embedders_of_the_paper(self):
        rows = self.configs["paper_configurations"] + self.configs["optional_additions"]
        self.assertEqual({r["llm"] for r in rows}, {"llama3.1:8b", "qwen3:1.7b", "gemma3:1b"})
        self.assertEqual({r["embedding"] for r in rows}, {"mxbai-embed-large:latest", "all-minilm:latest"})

    def test_values_match_table_iv_and_the_historical_summary(self):
        for config in self.configs["paper_configurations"]:
            summary = self.summary[config["historical_file"]]
            paper = config["paper_table_iv"]
            self.assertAlmostEqual(summary["result_match_rate"], paper["match_rate"], places=6, msg=config["id"])
            self.assertAlmostEqual(summary["avg_time_context"], paper["context_s"], places=3, msg=config["id"])
            self.assertAlmostEqual(summary["avg_time_generate"], paper["generation_s"], places=3, msg=config["id"])
            self.assertAlmostEqual(paper["context_s"] + paper["generation_s"], paper["total_s"], places=3)

    def test_the_sixth_configuration_is_flagged_as_not_in_the_paper(self):
        extra = self.configs["optional_additions"]
        self.assertEqual([(e["llm"], e["embedding"]) for e in extra], [("llama3.1:8b", "all-minilm:latest")])
        paper = {(c["llm"], c["embedding"]) for c in self.configs["paper_configurations"]}
        self.assertNotIn(("llama3.1:8b", "all-minilm:latest"), paper)
        self.assertIn("Not in the paper", extra[0]["note"])

    def test_script_covers_paper_configs_and_never_embeds_secrets(self):
        with open(os.path.join(EXP, "benchmark", "run_paper_benchmark.sh"), encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("--repeats", script)
        self.assertIn("benchmark_30.json", script)
        self.assertIn('--questions "$QUESTIONS"', script)
        self.assertNotIn("QM_DB_PASSWORD=", script)
        self.assertIn('stat -c %a "$ENV_FILE"', script)


if __name__ == "__main__":
    unittest.main()
