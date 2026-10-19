Label: secondary_exploratory_post_processing_evaluation_no_model_regeneration. The primary comparison (QueryMind vs SQLCoder original) is unchanged.

| Arm | Accuracy (96 trials) | 95% CI (question bootstrap) | SQL executed | Hallucinated identifiers |
|---|---|---|---|---|
| QueryMind (Llama 3.1 8B + mxbai) | 87/96 = 90.6% | 78.1% to 100.0% | 96.9% | 3.1% |
| SQLCoder-8B original (primary) | 51/96 = 53.1% | 37.5% to 71.9% | 59.4% | 3.1% |
| SQLCoder-8B dialect-adjusted (secondary, exploratory) | 83/96 = 86.5% | 74.0% to 96.9% | 92.7% | 4.2% |

| Paired comparison (SQLCoder minus QueryMind) | Accuracy difference | Questions only QueryMind / only SQLCoder | Exact McNemar p |
|---|---|---|---|
| original (primary) | -37.5 points (-56.2 to -15.6) | 14 / 2 | 0.0042 |
| dialect-adjusted (secondary) | -4.2 points (-17.7 to +9.4) | 3 / 2 | 1.0000 |
