# Protocol: MariaDB dialect-compatibility follow-up for the SQLCoder baseline

Written and frozen **before** the follow-up evaluation is run (the run records the SHA-256 of this file and of
`dialect_translation.py`). Status of the analysis it defines: **secondary, exploratory**. The primary,
pre-specified comparison (QueryMind 87/96 = 90.6% versus SQLCoder 51/96 = 53.1%) stays unchanged.

## Why

34 of the 39 database errors of the preserved SQLCoder run come from the PostgreSQL-only ordering modifier
`NULLS FIRST` / `NULLS LAST`, which MariaDB rejects. The question is how much of the accuracy gap this single
dialect difference explains, answered without changing what the model generated.

## Scope (strict)

Only `NULLS FIRST` / `NULLS LAST` modifiers on `ORDER BY` items are translated. Nothing else is touched: no
function, quoting, identifier-case (e.g. `Products` instead of `products`), alias or syntax repair. Trials without
such a modifier keep their original outcome unchanged.

## Semantics

PostgreSQL treats NULL as larger than every value: ascending puts NULLs last, descending puts them first, unless
`NULLS FIRST/LAST` says otherwise. MariaDB treats NULL as smaller than every value: ascending puts NULLs first,
descending puts them last, and has no modifier. A translation is therefore only valid if it reproduces PostgreSQL's
placement exactly. Simply deleting the modifier is *not* equivalent in two of the four cases:

| Direction / modifier | Translation to MariaDB | Why |
|---|---|---|
| `ASC NULLS FIRST` (or implicit ASC) | remove the modifier | MariaDB ASC already puts NULLs first |
| `DESC NULLS LAST` | remove the modifier | MariaDB DESC already puts NULLs last |
| `ASC NULLS LAST` (and `expr NULLS LAST`, implicit ASC) | `(expr IS NULL) ASC, expr ASC` | NULLs must sort after all values |
| `DESC NULLS FIRST` | `(expr IS NULL) DESC, expr DESC` | NULLs must sort before all values |

The expression is copied verbatim into the added sort key.

## When the translation is NOT applied

If any `NULLS` item of a statement cannot be translated with guaranteed equivalence, the whole statement is left
unchanged (and therefore fails as it did originally). Not translated: expressions that are an integer position or a
numeric literal (`ORDER BY 2 NULLS LAST`; a position cannot be repeated inside `IS NULL`); expressions containing a
subquery, a window function (`OVER`), a user variable, a non-deterministic function (`RAND`, `UUID`, `SLEEP`, ...)
or PostgreSQL's `USING` operator; unterminated quotes or comments; overlapping rewrites; a `NULLS` modifier that
is not part of a parsed `ORDER BY` item.

## Independent validation (required before use)

1. Unit tests of the translator (`tests/test_dialect_translation.py`).
2. A live validation on disposable MariaDB data containing NULL and non-NULL values
   (`experiment/dialect_translation_validation.py`, run against a private temporary server): all four
   direction/modifier combinations plus implicit direction, multi-key orderings, qualified names, aliases,
   expressions, strings, and `LIMIT`. The expected row order is computed by an **independent oracle** (a Python
   implementation of PostgreSQL's documented NULL ordering, not derived from the translator). A negative control
   shows where naive deletion of the modifier would have produced a different order.
3. The translation is used in the evaluation only if every supported case matches the oracle and every
   unsupported case is refused.

## Evaluation

A **post-processing evaluation of the preserved SQL outputs**, not a new generation experiment: the 96 SQLCoder
trials (32 questions x 3 repeats) saved in `exp_runs/20261018T074339Z_sqlcoder-8b-baseline` are re-processed with
the same database (`classicmodels_gold`), the same read-only account, the same SQL guard (`extract_sql`) and the
same execution-result-equivalence scorer. No model is run, no prompt is changed.

## Reporting

For SQLCoder (original), SQLCoder (dialect-adjusted) and QueryMind (P5): accuracy, 95% question-level bootstrap
interval, paired bootstrap difference, and exact McNemar test (a question counts as solved if more than half of its
repeats are correct). The adjusted comparison is labelled secondary and exploratory in every table. The earlier
strip-only diagnostic (`*_sqlcoder-dialect-diagnostic`) is superseded by this procedure and reported only as an
initial look.

## Unchanged by this follow-up

QueryMind, the security implementation, benchmark questions, gold answers, database contents, the 480 + 75 trials,
the original 96 SQLCoder trials and all earlier comparison outputs.
