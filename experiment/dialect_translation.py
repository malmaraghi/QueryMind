"""Semantics-preserving translation of PostgreSQL ``NULLS FIRST|LAST`` ordering modifiers to MariaDB.

Specified in experiment/dialect_followup_protocol.md. Only these modifiers are touched; a statement with any
modifier that cannot be translated with guaranteed equivalence is returned unchanged (``ok=False``).

PostgreSQL: NULL sorts as the largest value (ASC -> NULLs last, DESC -> NULLs first) unless NULLS FIRST/LAST is given.
MariaDB:    NULL sorts as the smallest value (ASC -> NULLs first, DESC -> NULLs last); no modifier exists.

    ASC  NULLS FIRST  ->  expr ASC                              (MariaDB default already matches)
    DESC NULLS LAST   ->  expr DESC                             (MariaDB default already matches)
    ASC  NULLS LAST   ->  (expr IS NULL) ASC, expr ASC
    DESC NULLS FIRST  ->  (expr IS NULL) DESC, expr DESC
"""
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from security import _TOKEN  # noqa: E402  (the same tokenizer the SQL validator uses)

CLAUSE_END = {"LIMIT", "UNION", "INTERSECT", "EXCEPT", "FOR", "PROCEDURE", "OFFSET", "INTO", "LOCK", "WINDOW"}
UNSAFE_IN_EXPRESSION = {"SELECT", "OVER", "RAND", "UUID", "UUID_SHORT", "SLEEP", "LAST_INSERT_ID", "USING", "NEXTVAL"}


@dataclass
class Translation:
    ok: bool                                   # False: a NULLS modifier was found that could not be translated
    sql: str                                   # translated SQL (original text when unchanged or not ok)
    changed: bool = False
    rewrites: List[dict] = field(default_factory=list)
    reason: str = ""


def _scan(sql: str) -> Optional[List[Tuple[str, str, int, int]]]:
    tokens = []
    for match in _TOKEN.finditer(sql):
        kind, text = match.lastgroup, match.group()
        if kind in ("ws", "lcomment", "bcomment"):
            continue
        if kind == "op" and text in ("'", '"', "`"):
            return None                        # unterminated quote
        tokens.append((kind, text, match.start(), match.end()))
    for first, second in zip(tokens, tokens[1:]):
        if first[:2] == ("op", "/") and second[:2] == ("op", "*") and first[3] == second[2]:
            return None                        # "/*" that the tokenizer could not close: unterminated comment
    return tokens


def _up(token) -> str:
    return token[1].upper() if token[0] == "word" else ""


def _clauses(tokens) -> List[Tuple[int, int]]:
    """Token index ranges (start, end) of every ORDER BY item list."""
    out = []
    n = len(tokens)
    for i in range(n - 1):
        if _up(tokens[i]) == "ORDER" and _up(tokens[i + 1]) == "BY":
            depth, k, start = 0, i + 2, i + 2
            items = []
            while k < n:
                kind, text = tokens[k][0], tokens[k][1]
                if kind == "op" and text == "(":
                    depth += 1
                elif kind == "op" and text == ")":
                    if depth == 0:
                        break
                    depth -= 1
                elif kind == "op" and text == "," and depth == 0:
                    items.append((start, k))
                    start = k + 1
                elif kind == "op" and text == ";" and depth == 0:
                    break
                elif depth == 0 and _up(tokens[k]) in CLAUSE_END:
                    break
                k += 1
            items.append((start, k))
            out.extend(items)
    return out


def translate_nulls_ordering(sql: str) -> Translation:
    tokens = _scan(sql)
    if tokens is None:
        return Translation(False, sql, reason="unterminated quote or comment")
    nulls_pairs = [i for i in range(len(tokens) - 1)
                   if _up(tokens[i]) == "NULLS" and _up(tokens[i + 1]) in ("FIRST", "LAST")]
    if not nulls_pairs:
        return Translation(True, sql)

    replacements = []  # (start_char, end_char, new_text, description)
    handled = set()
    for start, end in _clauses(tokens):
        if end - start >= 2 and _up(tokens[end - 2]) == "NULLS" and _up(tokens[end - 1]) in ("FIRST", "LAST"):
            nulls = _up(tokens[end - 1])
            handled.add(end - 2)
            expr_end = end - 2
            explicit = _up(tokens[expr_end - 1]) in ("ASC", "DESC") if expr_end > start else False
            direction = _up(tokens[expr_end - 1]) if explicit else "ASC"
            if explicit:
                expr_end -= 1
            if expr_end <= start:
                return Translation(False, sql, reason="empty ORDER BY expression")
            expr_tokens = tokens[start:expr_end]
            if len(expr_tokens) == 1 and expr_tokens[0][0] == "number":
                return Translation(False, sql, reason="ORDER BY position or literal cannot be translated")
            if any(t[0] == "var" for t in expr_tokens):
                return Translation(False, sql, reason="user or system variable in ORDER BY expression")
            unsafe = [t[1].upper() for t in expr_tokens if t[0] == "word" and t[1].upper() in UNSAFE_IN_EXPRESSION]
            if unsafe:
                return Translation(False, sql, reason=f"expression contains {unsafe[0]}: duplication not guaranteed equivalent")
            expr = sql[expr_tokens[0][2]:expr_tokens[-1][3]]
            first_char, last_char = tokens[start][2], tokens[end - 1][3]
            if (direction == "ASC" and nulls == "FIRST") or (direction == "DESC" and nulls == "LAST"):
                new = expr + (f" {direction}" if explicit else "")
                case = f"{direction} NULLS {nulls}: modifier removed"
            else:
                new = f"({expr} IS NULL) {direction}, {expr} {direction}"
                case = f"{direction} NULLS {nulls}: IS NULL key added"
            replacements.append((first_char, last_char, new, {"case": case, "expression": expr}))
    if any(i not in handled for i in nulls_pairs):
        return Translation(False, sql, reason="NULLS modifier outside a parsed ORDER BY item")
    replacements.sort(key=lambda r: r[0])
    for before, after in zip(replacements, replacements[1:]):
        if after[0] < before[1]:
            return Translation(False, sql, reason="overlapping rewrites")
    out = sql
    for first, last, new, _ in reversed(replacements):
        out = out[:first] + new + out[last:]
    return Translation(True, out, changed=True, rewrites=[r[3] for r in replacements])
