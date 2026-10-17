"""Makes the project root and experiment/ importable for the tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPERIMENT = os.path.join(ROOT, "experiment")
for path in (ROOT, EXPERIMENT):
    if path not in sys.path:
        sys.path.insert(0, path)
