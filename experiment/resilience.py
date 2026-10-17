"""Helpers that make long experiment runs safe to interrupt, resume and repeat.

Kept free of database and Ollama imports so every function is unit-testable.
"""
import json
import os
import time
from typing import Callable, Iterable, List, Optional, Set, Tuple


class BlockedError(RuntimeError):
    """The run cannot continue safely (for example: database not verified read-only)."""


# ---------------------------------------------------------------- database connection

class ResilientConnection:
    """A database connection that re-establishes itself if the server drops it.

    ``connect`` must return ``(connection, access_info)`` and is expected to
    re-verify read-only privileges every time, so a reconnect can never end up
    on a more privileged account. If the connection cannot be restored the run
    is stopped (``BlockedError``) instead of recording every remaining trial as a
    database error.
    """

    def __init__(self, connect: Callable, retries: int = 3, backoff: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep):
        self._connect = connect
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep
        self._connection, self.access = connect()
        self.reconnects = 0

    def _alive(self) -> bool:
        try:
            self._connection.ping()
            return True
        except Exception:  # noqa: BLE001 - any failure means "not usable"
            return False

    def recover(self) -> bool:
        """``False`` if the connection is healthy; ``True`` if it had dropped and was re-established."""
        if self._alive():
            return False
        last_error = None
        for attempt in range(1, self._retries + 1):
            try:
                try:
                    self._connection.close()
                except Exception:  # noqa: BLE001
                    pass
                self._connection, self.access = self._connect()
                self.reconnects += 1
                return True
            except BlockedError:
                raise  # e.g. account no longer verified read-only: never retry past that
            except Exception as error:  # noqa: BLE001
                last_error = error
                self._sleep(self._backoff * attempt)
        raise BlockedError(f"Database connection lost; {self._retries} reconnect attempts failed: {last_error}")

    def cursor(self):
        self.recover()
        return self._connection.cursor()

    def close(self):
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- trial log and resume

def trial_key(trial: dict) -> Tuple:
    return (trial["llm"], trial["embedding"], trial["question_id"], trial["repeat"])


def read_trials(path: str) -> Tuple[List[dict], Optional[str]]:
    """Read ``trials.jsonl``.

    Returns ``(trials, truncated_tail)``. A damaged *last* line (a write cut
    short by a crash) is reported as ``truncated_tail`` and not returned; a
    damaged line anywhere else means the file is corrupt and raises.
    """
    if not os.path.exists(path):
        return [], None
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    # A well-formed file ends with a newline, so the final element is "".
    trials, tail = [], None
    last_index = max(i for i, line in enumerate(lines) if line.strip() != "") if any(l.strip() for l in lines) else -1
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            trials.append(json.loads(line))
        except json.JSONDecodeError:
            if index == last_index:
                tail = line
            else:
                raise BlockedError(f"{path} is corrupt at line {index + 1}; refusing to continue")
    return trials, tail


def quarantine_truncated_tail(path: str, tail: str) -> str:
    """Move a damaged final line aside (nothing is discarded) and cut it from the log."""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    quarantine = f"{path}.truncated-{stamp}"
    with open(quarantine, "w", encoding="utf-8") as handle:
        handle.write(tail + "\n")
    with open(path, encoding="utf-8") as handle:
        content = handle.read()
    kept = content[: content.rindex(tail)]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(kept)
    return quarantine


def pending_keys(configs: Iterable[dict], question_ids: Iterable[int], repeats: int,
                 done: Set[Tuple]) -> List[Tuple]:
    """Trial keys still to run, in the original execution order."""
    keys = []
    for config in configs:
        for question_id in question_ids:
            for repeat in range(1, repeats + 1):
                key = (config["llm"], config["embedding"], question_id, repeat)
                if key not in done:
                    keys.append(key)
    return keys


def resume_mismatches(old: dict, new: dict) -> List[str]:
    """Reasons a run directory must not be resumed with the current code/models/data."""
    problems = []

    def compare(label, a, b):
        if a != b:
            problems.append(f"{label} changed")

    compare("question file", old["files"]["questions"]["sha256"], new["files"]["questions"]["sha256"])
    old_ann, new_ann = old["files"].get("annotations"), new["files"].get("annotations")
    compare("annotations file", (old_ann or {}).get("sha256"), (new_ann or {}).get("sha256"))
    for name in ("llm_engine_py", "query_executor_py", "chroma_rag_py"):
        compare(name, old["files"][name], new["files"][name])
    for key in ("temperature", "seed"):
        compare(f"generation {key}", old["generation"][key], new["generation"][key])
    compare("generation timeout", old["generation"].get("timeout_s"), new["generation"].get("timeout_s"))
    compare("top_k", old["retrieval"]["top_k"], new["retrieval"]["top_k"])
    compare("repeats", old["repeats"], new["repeats"])
    compare("question ids", old["question_ids"], new["question_ids"])
    compare("configurations", old.get("configs"), new.get("configs"))
    for name, info in new["models"].items():
        compare(f"digest of {name}", (old["models"].get(name) or {}).get("digest"), info["digest"])
    return problems


def dataset_mismatches(old: dict, new: dict) -> List[str]:
    """Database must be byte-for-byte the same dataset as when the run started."""
    problems = []
    if old.get("schema_sha256") != new.get("schema_sha256"):
        problems.append("database schema changed")
    old_tables, new_tables = old.get("tables", {}), new.get("tables", {})
    for table in sorted(set(old_tables) | set(new_tables)):
        if old_tables.get(table) != new_tables.get(table):
            problems.append(f"table {table} rows/checksum changed")
    return problems


# ---------------------------------------------------------------- files and locking

def save_json_atomic(path: str, data) -> None:
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, default=str)
    os.replace(temporary, path)


def acquire_lock(run_dir: str):
    """Exclusive, crash-safe lock (released by the OS when the process exits)."""
    import fcntl

    handle = open(os.path.join(run_dir, "run.lock"), "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise BlockedError(f"Another process is using {run_dir}") from error
    handle.write(str(os.getpid()))
    handle.flush()
    return handle
