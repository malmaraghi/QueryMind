"""Collect reproducibility metadata for an experimental run.

Every collector is best-effort: a failure is recorded as an ``{"error": ...}``
value rather than aborting the run, except where the runner explicitly
requires a field (model digests).
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from importlib import metadata
from typing import Optional


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(args: list, cwd: Optional[str] = None, timeout: int = 10) -> Optional[str]:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def git_info(root: str) -> dict:
    status = _run(["git", "status", "--porcelain"], cwd=root)
    return {
        "commit": _run(["git", "rev-parse", "HEAD"], cwd=root),
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root),
        # Uncommitted changes mean the commit hash alone does not identify the code.
        "dirty": bool(status) if status is not None else None,
        "uncommitted_files": status.splitlines() if status else [],
    }


def python_info() -> dict:
    return {
        "version": sys.version,
        "executable": sys.executable,
        "implementation": platform.python_implementation(),
    }


def dependency_versions() -> dict:
    """All installed distributions in the running interpreter's environment."""
    versions = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            versions[name] = dist.version
    return dict(sorted(versions.items(), key=lambda item: item[0].lower()))


def ollama_version(host: Optional[str] = None) -> Optional[str]:
    base = host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if not base.startswith("http"):
        base = f"http://{base}"
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/api/version", timeout=5) as response:
            return json.load(response).get("version")
    except Exception as error:  # noqa: BLE001 - metadata only
        return f"unavailable: {error}"


def model_info(model_name: str) -> dict:
    """Name, digest and details of an installed Ollama model.

    Raises ``LookupError`` if the model is not installed: a run without a
    digest would not be reproducible, so the runner refuses to continue.
    """
    import ollama

    candidates = {model_name, model_name if ":" in model_name else f"{model_name}:latest"}
    for entry in ollama.list().models:
        if entry.model in candidates:
            details = entry.details
            return {
                "name": entry.model,
                "digest": entry.digest,
                "size_bytes": entry.size,
                "family": getattr(details, "family", None),
                "parameter_size": getattr(details, "parameter_size", None),
                "quantization_level": getattr(details, "quantization_level", None),
                "format": getattr(details, "format", None),
            }
    raise LookupError(f"Ollama model not installed: {model_name}")


def hardware_info() -> dict:
    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count_logical": os.cpu_count(),
    }
    try:
        with open("/proc/cpuinfo") as handle:
            for line in handle:
                if line.startswith("model name"):
                    info["cpu_model"] = line.split(":", 1)[1].strip()
                    break
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal"):
                    info["memory_total_kb"] = int(line.split()[1])
                    break
    except OSError:
        pass
    gpu = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    info["gpu"] = gpu.splitlines() if gpu else None
    return info


def database_info(connection, database_name: str) -> dict:
    """Identify the dataset: server version, per-table row counts and checksums, schema hash.

    Uses only SELECT-level statements (``SHOW``, ``COUNT``, ``CHECKSUM TABLE``).
    """
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT VERSION()")
        server_version = cursor.fetchone()[0]
        cursor.execute("SHOW TABLES")
        tables = sorted(row[0] for row in cursor.fetchall())
        schema_parts, table_details = [], {}
        for table in tables:
            cursor.execute(f"SHOW CREATE TABLE `{table}`")
            schema_parts.append(cursor.fetchone()[1])
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            row_count = cursor.fetchone()[0]
            cursor.execute(f"CHECKSUM TABLE `{table}`")
            table_details[table] = {"rows": row_count, "checksum": cursor.fetchone()[1]}
        return {
            "database": database_name,
            "server_version": server_version,
            "tables": table_details,
            "schema_sha256": hashlib.sha256("\n".join(schema_parts).encode()).hexdigest(),
        }
    finally:
        cursor.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
