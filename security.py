"""Three layers that keep QueryMind read-only.

Layer 1 - database level:      the connected account must hold SELECT-only privileges
                               (``check_select_only_account``); this is what actually stops writes.
Layer 2 - application level:   user input is screened (``validate_question``) and the generated SQL is
                               validated before it is executed (``validate_sql``), with execution limits.
Layer 3 - post-execution:      every statement runs inside a transaction that is never committed; the
                               server-reported number of modified rows is inspected afterwards
                               (``execute_guarded``) and any modification is rolled back and reported.

Each layer is meant to work on its own; none of them relies on another having run.
See docs/SECURITY_LAYERS.md for what each layer guarantees and where it stops.
"""
import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("querymind.security")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


PRIVILEGE_POLICY = os.environ.get("QUERYMIND_PRIVILEGE_POLICY", "enforce").lower()  # enforce | warn | off
MAX_RESULT_ROWS = _env_int("QUERYMIND_MAX_RESULT_ROWS", 50_000)
STATEMENT_TIMEOUT_SECONDS = _env_int("QUERYMIND_STATEMENT_TIMEOUT", 60)
MAX_QUESTION_CHARS = _env_int("QUERYMIND_MAX_QUESTION_CHARS", 2000)
MAX_SQL_CHARS = 20_000


# =====================================================================================
# Layer 1 - database-level protection: SELECT-only account
# =====================================================================================

READ_ONLY_PRIVILEGES = {"SELECT", "USAGE", "SHOW VIEW"}

_GRANT_LINE = re.compile(r"^\s*GRANT\s+(.*?)\s+ON\s+(.*?)\s+TO\s+(.*)$", re.IGNORECASE | re.DOTALL)
_PASSWORD_PART = re.compile(r"(IDENTIFIED\s+(?:VIA\s+\w+\s+USING|BY(?:\s+PASSWORD)?)\s+)'[^']*'", re.IGNORECASE)


def redact_grant(line: str) -> str:
    """``SHOW GRANTS`` output contains the account's password hash; never keep it."""
    return _PASSWORD_PART.sub(r"\1'<redacted>'", line)


def parse_grants(grant_lines: Iterable[str]) -> dict:
    """Decide whether ``SHOW GRANTS`` output is strictly read-only.

    Allowed privileges: SELECT, USAGE, SHOW VIEW. Anything else, GRANT OPTION, or a role grant (whose
    privileges are not visible here) fails the check.
    """
    problems, seen = [], []
    for line in grant_lines:
        seen.append(line)
        match = _GRANT_LINE.match(line)
        if not match:
            problems.append(f"unparseable or role grant: {redact_grant(line)}")
            continue
        column_free = re.sub(r"\([^)]*\)", "", match.group(1))  # SELECT (a, b) -> SELECT
        privileges = [p.strip().upper() for p in column_free.split(",")]
        extra = [p for p in privileges if p not in READ_ONLY_PRIVILEGES]
        if extra:
            problems.append(f"write-capable privileges {extra} on {match.group(2)}")
        if re.search(r"WITH\s+GRANT\s+OPTION", match.group(3), re.IGNORECASE):
            problems.append(f"GRANT OPTION on {match.group(2)}")
    if not seen:
        problems.append("no grants returned")
    return {"read_only": not problems, "problems": problems, "grants": seen}


def check_select_only_account(connection) -> dict:
    """Run ``SHOW GRANTS`` for the connected account and report whether it is SELECT-only.

    The returned grants are redacted (no password hash).
    """
    cursor = connection.cursor()
    try:
        cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
        verdict = parse_grants(row[0] for row in cursor.fetchall())
    finally:
        cursor.close()
    verdict["grants"] = [redact_grant(g) for g in verdict["grants"]]
    return verdict


# =====================================================================================
# Layer 2 - application-level protection: input screening and SQL validation
# =====================================================================================

def validate_question(text: str) -> Tuple[bool, str]:
    """Basic screening of the natural-language input (the keyword filter in ``llm_engine`` also applies)."""
    if text is None:
        return False, "Please enter a question."
    if len(text) > MAX_QUESTION_CHARS:
        return False, f"Error: The question is too long (limit {MAX_QUESTION_CHARS} characters)."
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in text):
        return False, "Error: The question contains control characters."
    return True, ""


@dataclass(frozen=True)
class SqlCheck:
    ok: bool
    rule: str = ""
    message: str = ""


_TOKEN = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<lcomment>--(?=\s|$)[^\n]*|\#[^\n]*)
    | (?P<bcomment>/\*.*?\*/)
    | (?P<string>'(?:[^'\\]|\\.|'')*'|"(?:[^"\\]|\\.|"")*")
    | (?P<quoted>`(?:[^`]|``)*`)
    | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<var>@@?[A-Za-z0-9_.$]*|@`[^`]*`|@'[^']*')
    | (?P<word>[A-Za-z_\u0080-￿][A-Za-z0-9_$\u0080-￿]*)
    | (?P<op>.)
    """,
    re.VERBOSE | re.DOTALL,
)

# Words that must never appear outside quotes/strings in a read-only query.
FORBIDDEN_WORDS = {
    "INSERT", "UPDATE", "DELETE", "REPLACE", "DROP", "ALTER", "CREATE", "TRUNCATE", "RENAME", "GRANT", "REVOKE",
    "CALL", "EXECUTE", "PREPARE", "DEALLOCATE", "LOAD", "HANDLER", "LOCK", "UNLOCK", "SET", "FLUSH", "KILL",
    "SHUTDOWN", "RESET", "PURGE", "SAVEPOINT", "COMMIT", "ROLLBACK", "START", "BEGIN", "USE", "DO",
    "INTO", "OUTFILE", "DUMPFILE", "INFILE", "PROCEDURE",
}
# Legitimate SQL functions that share a name with a forbidden statement word: allowed only as ``NAME(``.
FUNCTION_LIKE = {"REPLACE", "INSERT"}
# Functions that read files, take locks, stall the server or reveal the account/server.
FORBIDDEN_FUNCTIONS = {
    "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS", "IS_FREE_LOCK",
    "IS_USED_LOCK", "MASTER_POS_WAIT", "SYS_EXEC", "SYS_EVAL", "USER", "CURRENT_USER", "SESSION_USER",
    "SYSTEM_USER", "VERSION", "DATABASE", "SCHEMA", "CONNECTION_ID", "LAST_INSERT_ID", "ROW_COUNT",
}
SYSTEM_SCHEMAS = {"INFORMATION_SCHEMA", "MYSQL", "PERFORMANCE_SCHEMA", "SYS"}
# Words after which the table section of a SELECT is over.
FROM_END = {"WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "UNION", "INTERSECT", "EXCEPT", "WINDOW", "FOR"}
NOT_AN_ALIAS = FROM_END | {"ON", "USING", "JOIN", "INNER", "LEFT", "RIGHT", "CROSS", "NATURAL", "STRAIGHT_JOIN",
                           "OUTER", "FULL", "SELECT", "AS", "OFFSET", "FETCH", "PARTITION", "FORCE", "IGNORE"}
FROM_INSIDE_FUNCTION = {"EXTRACT", "TRIM", "SUBSTRING", "SUBSTR", "POSITION", "CAST", "CONVERT"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_$.]")


def _safe(name: str) -> str:
    """Names echoed in messages are limited to harmless characters (messages are rendered as HTML)."""
    return _SAFE_NAME.sub("?", name)[:64]


def _tokens(sql: str) -> Optional[List[Tuple[str, str]]]:
    """Significant tokens as ``(kind, text)``; ``None`` if a quote or comment is unterminated."""
    out = []
    for match in _TOKEN.finditer(sql):
        kind, text = match.lastgroup, match.group()
        if kind in ("ws", "lcomment"):
            continue
        if kind == "bcomment":
            if text.startswith(("/*!", "/*M!")):  # executable comment: MariaDB runs its content
                out.append(("exec_comment", text))
            continue
        if kind == "op" and text in ("'", '"', "`"):
            return None
        out.append((kind, text))
    if "/*" in "".join(t for k, t in out if k == "op" or k == "word"):
        return None
    return out


def _upper(token: Tuple[str, str]) -> str:
    return token[1].upper() if token[0] == "word" else ""


def _ident(token: Tuple[str, str]) -> Optional[str]:
    if token[0] == "word":
        return token[1]
    if token[0] == "quoted":
        return token[1][1:-1].replace("``", "`")
    return None


def _cte_names(tokens: Sequence[Tuple[str, str]]) -> set:
    names, i = set(), 1
    if i < len(tokens) and _upper(tokens[i]) == "RECURSIVE":
        i += 1

    def skip_parens(j):
        depth = 0
        while j < len(tokens):
            if tokens[j][1] == "(" and tokens[j][0] == "op":
                depth += 1
            elif tokens[j][1] == ")" and tokens[j][0] == "op":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return j

    while i < len(tokens):
        name = _ident(tokens[i])
        if name is None:
            break
        names.add(name.lower())
        i += 1
        if i < len(tokens) and tokens[i] == ("op", "("):
            i = skip_parens(i)
        if i >= len(tokens) or _upper(tokens[i]) != "AS":
            break
        i += 1
        if i >= len(tokens) or tokens[i] != ("op", "("):
            break
        i = skip_parens(i)
        if i < len(tokens) and tokens[i] == ("op", ","):
            i += 1
            continue
        break
    return names


def _check_tables(tokens, allowed_tables: Iterable[str], database: Optional[str], ctes: set) -> Optional[SqlCheck]:
    allowed = {t.lower() for t in allowed_tables} | {"dual"}
    paren_is_function_from: List[bool] = []   # one flag per open parenthesis
    active_from: dict = {}                      # paren depth -> table list still open (after FROM)
    i, n = 0, len(tokens)

    def read_table_ref(j) -> Tuple[int, Optional[SqlCheck]]:
        if j >= n:
            return j, SqlCheck(False, "incomplete_query", "Query blocked: incomplete table reference.")
        if tokens[j] == ("op", "("):
            return j, None                       # derived table / subquery: scanned by the main loop
        parts = []
        while j < n:
            name = _ident(tokens[j])
            if name is None:
                break
            parts.append(name)
            j += 1
            if j < n and tokens[j] == ("op", ".") and j + 1 < n:
                j += 1
                continue
            break
        if not parts:
            return j, SqlCheck(False, "bad_table_reference", "Query blocked: unreadable table reference.")
        if len(parts) > 2:
            return j, SqlCheck(False, "cross_database", "Query blocked: references must be table or database.table.")
        if len(parts) == 2:
            if parts[0].upper() in SYSTEM_SCHEMAS or (database and parts[0].lower() != database.lower()):
                return j, SqlCheck(False, "cross_database",
                                   f"Query blocked: access to schema '{_safe(parts[0])}' is not allowed.")
        table = parts[-1].lower()
        if table not in allowed and table not in ctes:
            return j, SqlCheck(False, "unknown_table", f"Query blocked: unknown table '{_safe(parts[-1])}'.")
        # optional alias
        if j < n and _upper(tokens[j]) == "AS":
            j += 2
        elif j < n and _ident(tokens[j]) is not None and _upper(tokens[j]) not in NOT_AN_ALIAS:
            j += 1
        return j, None

    while i < n:
        kind, text = tokens[i]
        up = _upper(tokens[i])
        if kind == "op" and text == "(":
            prev = _upper(tokens[i - 1]) if i else ""
            paren_is_function_from.append(prev in FROM_INSIDE_FUNCTION)
        elif kind == "op" and text == ")":
            depth = len(paren_is_function_from)
            if paren_is_function_from:
                paren_is_function_from.pop()
            active_from.pop(depth, None)
        elif up in FROM_END:
            active_from.pop(len(paren_is_function_from), None)
        elif up in ("FROM", "JOIN", "STRAIGHT_JOIN"):
            if not (up == "FROM" and paren_is_function_from and paren_is_function_from[-1]):
                if up == "FROM":
                    active_from[len(paren_is_function_from)] = True
                i, problem = read_table_ref(i + 1)
                if problem:
                    return problem
                continue
        elif kind == "op" and text == "," and active_from.get(len(paren_is_function_from)):
            i, problem = read_table_ref(i + 1)
            if problem:
                return problem
            continue
        i += 1
    return None


def validate_sql(sql: str, allowed_tables: Iterable[str], database: Optional[str] = None) -> SqlCheck:
    """Decide whether a generated statement may be executed.

    Accepts a single read-only SELECT (optionally with a WITH clause or UNION) that only touches tables of
    the connected schema. Rejects stacked statements, executable comments, write/DDL/administrative
    keywords, ``SELECT ... INTO``, locking reads, user/system variables, file/lock/delay/identity functions
    and any table outside ``allowed_tables`` (including other schemas such as ``mysql`` or
    ``information_schema``).

    This is a lexical validator, not a full SQL parser: see docs/SECURITY_LAYERS.md for its limits.
    """
    allowed_tables = list(allowed_tables)
    if not isinstance(sql, str) or not sql.strip():
        return SqlCheck(False, "empty", "Query blocked: empty statement.")
    if len(sql) > MAX_SQL_CHARS:
        return SqlCheck(False, "too_long", "Query blocked: statement too long.")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in sql):
        return SqlCheck(False, "control_characters", "Query blocked: control characters in statement.")
    tokens = _tokens(sql)
    if tokens is None:
        return SqlCheck(False, "unterminated", "Query blocked: unterminated quote or comment.")
    if any(kind == "exec_comment" for kind, _ in tokens):
        return SqlCheck(False, "executable_comment", "Query blocked: executable comments are not allowed.")
    if not tokens:
        return SqlCheck(False, "empty", "Query blocked: empty statement.")

    # one statement only (a single trailing semicolon is fine)
    semicolons = [i for i, t in enumerate(tokens) if t == ("op", ";")]
    if semicolons:
        if semicolons != [len(tokens) - 1]:
            return SqlCheck(False, "multiple_statements", "Query blocked: only one statement is allowed.")
        tokens = tokens[:-1]
    if not tokens:
        return SqlCheck(False, "empty", "Query blocked: empty statement.")

    first = tokens[0]
    starts_with_paren = first == ("op", "(") and len(tokens) > 1 and _upper(tokens[1]) in ("SELECT", "WITH")
    if _upper(first) not in ("SELECT", "WITH") and not starts_with_paren:
        return SqlCheck(False, "not_select", "Query blocked: only SELECT statements are allowed.")

    for index, token in enumerate(tokens):
        kind, text = token
        up = _upper(token)
        followed_by_paren = index + 1 < len(tokens) and tokens[index + 1] == ("op", "(")
        if kind == "var":
            return SqlCheck(False, "variables", "Query blocked: user and system variables are not allowed.")
        if up in FORBIDDEN_WORDS and not (up in FUNCTION_LIKE and followed_by_paren):
            return SqlCheck(False, "forbidden_keyword", f"Query blocked: '{up}' is not allowed in a read-only query.")
        if up in FORBIDDEN_FUNCTIONS and followed_by_paren:
            return SqlCheck(False, "forbidden_function", f"Query blocked: function {up}() is not allowed.")
        if up in SYSTEM_SCHEMAS and index + 1 < len(tokens) and tokens[index + 1] == ("op", "."):
            return SqlCheck(False, "cross_database", "Query blocked: system schemas are not accessible.")
        if up == "FOR" and index + 1 < len(tokens) and _upper(tokens[index + 1]) in ("UPDATE", "SHARE"):
            return SqlCheck(False, "locking_read", "Query blocked: locking reads are not allowed.")
        if up == "LOCK" and index + 1 < len(tokens) and _upper(tokens[index + 1]) == "IN":
            return SqlCheck(False, "locking_read", "Query blocked: locking reads are not allowed.")

    ctes = _cte_names(tokens) if _upper(first) == "WITH" else set()
    problem = _check_tables(tokens, allowed_tables, database, ctes)
    if problem:
        return problem
    return SqlCheck(True)


# =====================================================================================
# Layer 3 - post-execution protection: transaction, modified-row check, rollback
# =====================================================================================

class ModificationDetected(RuntimeError):
    """A statement changed data. The transaction was rolled back."""

    def __init__(self, modified_rows: int):
        super().__init__(f"statement modified {modified_rows} row(s); rolled back")
        self.modified_rows = modified_rows


class ResultTooLarge(RuntimeError):
    pass


@dataclass
class GuardedResult:
    columns: Optional[List[str]]
    rows: list
    modified_rows: int          # rows changed according to the server (0 for a plain SELECT)
    returned_rows: int          # rows delivered to the client (a SELECT result size, NOT a modification count)


def execute_guarded(connection, sql: str, *, max_rows: int = None, statement_timeout_s: int = None,
                    read_only_transaction: bool = False) -> GuardedResult:
    """Run one statement inside a transaction that is never committed.

    1. ``START TRANSACTION`` (``READ ONLY`` if requested); the statement runs;
    2. the rows *modified* by the statement are read from the server with ``ROW_COUNT()``
       (``-1`` after a SELECT, the number of changed rows after INSERT/UPDATE/DELETE);
       the size of a SELECT result (``cursor.rowcount``) is deliberately not used;
    3. the transaction is rolled back in every case. If ``ROW_COUNT()`` is greater than zero,
       ``ModificationDetected`` is raised after the rollback.

    Rolling back reverts uncommitted changes to transactional (InnoDB) tables. It cannot undo statements
    that commit implicitly (DDL such as CREATE/ALTER/DROP/TRUNCATE) or changes to non-transactional
    tables (MyISAM); layers 1 and 2 exist to keep such statements from reaching the server.
    """
    max_rows = MAX_RESULT_ROWS if max_rows is None else max_rows
    timeout = STATEMENT_TIMEOUT_SECONDS if statement_timeout_s is None else statement_timeout_s
    cursor = connection.cursor()
    try:
        try:
            cursor.execute(f"SET SESSION max_statement_time={int(timeout)}")
        except Exception:  # noqa: BLE001 - older servers: the limit simply is not applied
            pass
        cursor.execute("START TRANSACTION READ ONLY" if read_only_transaction else "START TRANSACTION")
        cursor.execute(sql)
        columns, rows = None, []
        if cursor.description:
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(max_rows + 1)
            if len(rows) > max_rows:
                raise ResultTooLarge(f"result has more than {max_rows} rows")
        cursor.execute("SELECT ROW_COUNT()")
        reported = cursor.fetchone()[0]
        modified = reported if isinstance(reported, int) and reported > 0 else 0
        if modified:
            log.warning("blocked modification: %s row(s) changed by a guarded statement; rolling back", modified)
            raise ModificationDetected(modified)
        return GuardedResult(columns, rows, 0, len(rows))
    finally:
        try:
            cursor.close()
        finally:
            try:
                connection.rollback()   # never commit: reverts anything the statement left uncommitted
            except Exception:  # noqa: BLE001
                log.error("rollback failed")


def run_query_guarded(sql: str, connection):
    """Drop-in for ``query_executor.run_query``: same return shapes, with the post-execution guard."""
    try:
        result = execute_guarded(connection, sql)
    except ModificationDetected as error:
        return f"Error: Blocked - the query tried to change {error.modified_rows} row(s). The change was rolled back."
    except ResultTooLarge:
        return f"Error: The result is too large to display (more than {MAX_RESULT_ROWS} rows)."
    except Exception as error:  # noqa: BLE001
        return f"Error: {error}"
    if not result.rows:
        return "No records."
    return {"columns": result.columns, "rows": result.rows}
