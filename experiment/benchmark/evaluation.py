"""Corrected evaluation logic for QueryMind experiments.

Pure functions only (no database, Ollama or filesystem access), so everything
here is unit-testable.

What "correct" means in this module
-----------------------------------
``compare_results`` implements *execution-result equivalence on one database
state*: the generated query returns the same multiset of rows (or the same
sequence, when ordering matters) as the reference query. That is evidence for,
not proof of, semantic correctness. Two different queries can return identical
rows on this data and still behave differently on other data. Reports must
therefore call the primary metric "execution-result accuracy", never
"semantic accuracy".
"""
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, List, Optional, Sequence

# Outcome of one generation attempt. Exactly one applies per trial.
STATUS_RETRIEVAL_ERROR = "retrieval_error"      # RAG returned an error string
STATUS_GENERATION_ERROR = "generation_error"    # Ollama / LLM call failed
STATUS_VALIDATION_FAILURE = "validation_failure"  # blocked by the app's SQL guard or refusal
STATUS_DB_ERROR = "db_error"                    # validated SQL was rejected/failed in MariaDB
STATUS_EXECUTED = "executed"                    # ran; correctness is judged separately

ALL_STATUSES = (
    STATUS_RETRIEVAL_ERROR,
    STATUS_GENERATION_ERROR,
    STATUS_VALIDATION_FAILURE,
    STATUS_DB_ERROR,
    STATUS_EXECUTED,
)

_NUMERIC_TEXT = re.compile(r"^-?\d+(\.\d+)?$")
_QUANTUM = Decimal("1e-6")  # absorbs float noise from AVG()/SUM() on DOUBLE columns


@dataclass(frozen=True)
class Comparison:
    equivalent: bool
    reason: str  # ok | column_count_mismatch | row_count_mismatch | content_mismatch | order_mismatch
    both_empty: bool = False


def _canon_number(value) -> tuple:
    try:
        number = value if isinstance(value, Decimal) else Decimal(repr(value) if isinstance(value, float) else str(value))
        if not number.is_finite():
            return ("f", str(number))
        try:
            number = number.quantize(_QUANTUM)
        except InvalidOperation:
            pass  # too large to quantize; keep as is
        return ("n", number.normalize())
    except InvalidOperation:
        return ("s", str(value))


def canon_value(value: Any, loose: bool = False) -> tuple:
    """Canonical, hashable form of one cell.

    Numbers compare by value (5 == Decimal('5.00') == 5.0). Strings are kept
    verbatim (trailing spaces in the data are significant). With ``loose``,
    numeric-looking strings are treated as numbers; that is only used to check
    ``expected_rows`` (JSON, which stores DECIMAL as text) against live data,
    and is applied symmetrically to both sides.
    """
    if value is None:
        return ("z",)
    if isinstance(value, bool):
        return _canon_number(int(value))
    if isinstance(value, (int, float, Decimal)):
        return _canon_number(value)
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, (datetime, date, time)):
        return ("s", value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat())
    if isinstance(value, timedelta):
        return ("s", str(value))
    text = str(value)
    if loose and _NUMERIC_TEXT.match(text):
        return _canon_number(text)
    return ("s", text)


def canon_rows(rows: Iterable[Sequence], loose: bool = False) -> List[tuple]:
    return [tuple(canon_value(cell, loose) for cell in row) for row in rows]


def _group_by_key(rows: List[tuple], key_indices: Sequence[int]) -> List[Counter]:
    """Consecutive runs of rows sharing the same ORDER BY key, each as a multiset."""
    groups: List[Counter] = []
    previous_key = object()
    for row in rows:
        key = tuple(row[i] for i in key_indices)
        if key != previous_key:
            groups.append(Counter())
            previous_key = key
        groups[-1][row] += 1
    return groups


def compare_results(
    generated_rows: Sequence[Sequence],
    reference_rows: Sequence[Sequence],
    generated_ncols: int,
    reference_ncols: int,
    order_significant: bool = False,
    order_key_indices: Optional[Sequence[int]] = None,
    loose: bool = False,
) -> Comparison:
    """Compare two result sets without using column names.

    * Columns are matched by position, so aliases never matter. The column
      order and count must match the reference.
    * ``order_significant=False``: rows are compared as multisets, so row
      order is ignored but duplicate counts must match.
    * ``order_significant=True``: the row sequence must match. With
      ``order_key_indices`` (0-based positions of the ORDER BY columns in the
      reference output), rows tied on that key may appear in any order inside
      their tie group, because SQL does not define tie order. Without it the
      sequence must match exactly.
    """
    both_empty = len(generated_rows) == 0 and len(reference_rows) == 0
    if generated_ncols != reference_ncols:
        return Comparison(False, "column_count_mismatch", both_empty)
    if len(generated_rows) != len(reference_rows):
        return Comparison(False, "row_count_mismatch", both_empty)

    generated = canon_rows(generated_rows, loose)
    reference = canon_rows(reference_rows, loose)

    if not order_significant:
        if Counter(generated) == Counter(reference):
            return Comparison(True, "ok", both_empty)
        return Comparison(False, "content_mismatch", both_empty)

    if generated == reference:
        return Comparison(True, "ok", both_empty)
    if Counter(generated) != Counter(reference):
        return Comparison(False, "content_mismatch", both_empty)
    if order_key_indices:
        if _group_by_key(generated, order_key_indices) == _group_by_key(reference, order_key_indices):
            return Comparison(True, "ok", both_empty)
    return Comparison(False, "order_mismatch", both_empty)


def verify_reference(
    reference_rows: Sequence[Sequence],
    expected_rows: Optional[Sequence[Sequence]],
    order_significant: bool = False,
) -> dict:
    """Check that the live database reproduces the stored ``expected_rows``.

    Returns ``{"verified": bool, "reason": str}``. A question whose reference
    state cannot be verified must not be scored.
    """
    if expected_rows is None:
        return {"verified": False, "reason": "no_expected_rows"}
    if len(reference_rows) != len(expected_rows):
        return {"verified": False, "reason": "row_count_mismatch"}
    if not reference_rows:
        return {"verified": True, "reason": "ok_empty"}
    if len(reference_rows[0]) != len(expected_rows[0]):
        return {"verified": False, "reason": "column_count_mismatch"}
    comparison = compare_results(
        reference_rows, expected_rows,
        len(reference_rows[0]), len(expected_rows[0]),
        order_significant=order_significant, loose=True,
    )
    return {"verified": comparison.equivalent, "reason": comparison.reason}


def classify_generation(rag_context: str, raw_llm_output: str, extracted_sql: str) -> Optional[tuple]:
    """Map pre-execution failures to ``(status, detail)``; ``None`` if SQL may run.

    ``extracted_sql`` is the output of the application's own ``extract_sql``,
    which is the SQL guard the real app uses.
    """
    if not rag_context or rag_context.startswith(("ERROR:", "Error")):
        return STATUS_RETRIEVAL_ERROR, rag_context
    if raw_llm_output.startswith("LLM Error:"):
        return STATUS_GENERATION_ERROR, raw_llm_output
    if raw_llm_output.startswith("Error:"):
        return STATUS_VALIDATION_FAILURE, raw_llm_output  # question keyword filter in ask_llm
    if extracted_sql.startswith(("Error:", "LLM Error:")):
        return STATUS_VALIDATION_FAILURE, extracted_sql   # refusal / non-SELECT / unsafe keyword
    return None


_TABLE_LINE = re.compile(r"^-- Table:\s*(\S+)\s*$", re.MULTILINE)


def parse_retrieved_tables(rag_context: str) -> List[str]:
    """Table names in retrieval order, from ``retrieve_schema_context`` output."""
    if not rag_context:
        return []
    return _TABLE_LINE.findall(rag_context)


def recall_at_k(required: Sequence[str], retrieved: Sequence[str], k: int) -> Optional[float]:
    """Fraction of required tables found in the first ``k`` retrieved tables."""
    required_set = {name.lower() for name in required}
    if not required_set:
        return None
    top = {name.lower() for name in retrieved[:k]}
    return len(required_set & top) / len(required_set)


# One implementation of the read-only privilege check, shared with the application (Layer 1 in security.py).
from security import parse_grants  # noqa: E402,F401
