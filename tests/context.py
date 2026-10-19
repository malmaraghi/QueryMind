"""Makes the project root and the experiment sub-packages importable for the tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPERIMENT = os.path.join(ROOT, "experiment")
SUBFOLDERS = ("benchmark", "analysis", "baseline", "security_eval", "common", "pilot")
for path in [ROOT] + [os.path.join(EXPERIMENT, name) for name in SUBFOLDERS]:
    if path not in sys.path:
        sys.path.insert(0, path)
