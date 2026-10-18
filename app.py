import hashlib
import os
import time
import mariadb

from flask import Flask, g, render_template, request, session, redirect, url_for, make_response
from chroma_rag import index_schema_in_chroma, retrieve_schema_context
from llm_engine import ask_llm
from query_executor import extract_sql
from config import MAIN_LLM_MODEL, EMBEDDING_MODEL
import security
import webguard

# ---------------------------------------------------------------------------------------------------- configuration

app = Flask(__name__)

_secret = os.getenv("SECRET_KEY", "")
if len(_secret) < 16:
    raise RuntimeError("SECRET_KEY is missing or too short (at least 16 characters). Generate one with: "
                       "python -c \"import secrets; print(secrets.token_hex(32))\"")
app.secret_key = _secret

if app.debug and os.getenv("QUERYMIND_ALLOW_DEBUG") != "1":
    raise RuntimeError("Flask debug mode exposes an interactive console and is refused. "
                       "Unset FLASK_DEBUG / do not pass --debug (developers can set QUERYMIND_ALLOW_DEBUG=1).")

HTTPS = os.getenv("QUERYMIND_HTTPS") == "1"
app.config.update(
    SESSION_COOKIE_NAME="querymind_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=HTTPS,
    MAX_CONTENT_LENGTH=256 * 1024,
)

INDEX_BASE = os.path.expanduser(os.getenv("QUERYMIND_INDEX_DIR", "~/.querymind_chromadb"))
SESSION_IDLE_SECONDS = int(os.getenv("QUERYMIND_SESSION_IDLE", "1800"))
SESSION_MAX_SECONDS = int(os.getenv("QUERYMIND_SESSION_MAX", str(8 * 3600)))
ALLOWED_DB_HOSTS = webguard.parse_allowlist(os.getenv("QUERYMIND_ALLOWED_DB_HOSTS"))
CONNECT_TIMEOUT = int(os.getenv("QUERYMIND_CONNECT_TIMEOUT", "10"))
_MAX_FAILURES = int(os.getenv("QUERYMIND_LOGIN_MAX_FAILURES", "5"))
_WINDOW = int(os.getenv("QUERYMIND_LOGIN_WINDOW", "300"))

GENERIC_LOGIN_ERROR = "Invalid database credentials. Please check your information and try again."


def _drop_index(data):
    webguard.remove_index_dir(data.get("persist_path"), INDEX_BASE)


sessions = webguard.SessionStore(idle_seconds=SESSION_IDLE_SECONDS, absolute_seconds=SESSION_MAX_SECONDS,
                                 on_expire=_drop_index)
account_limiter = webguard.RateLimiter(max_failures=_MAX_FAILURES, window=_WINDOW)
ip_limiter = webguard.RateLimiter(max_failures=_MAX_FAILURES * 4, window=_WINDOW)

# Sessions live in this process only, so indexes left by an earlier run belong to nobody.
webguard.purge_stale_index_dirs(INDEX_BASE, SESSION_IDLE_SECONDS)


# ---------------------------------------------------------------------------------------------------- request hooks

def _csrf_token():
    if not session.get("csrf"):
        session["csrf"] = webguard.new_token()
    return session["csrf"]


@app.before_request
def _before():
    g.nonce = webguard.new_token()
    g.creds = sessions.get(session.get("sid"))
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not webguard.tokens_match(session.get("csrf"), supplied):
            return make_response("Invalid or missing security token. Reload the page and try again.", 400)
    return None


@app.context_processor
def _context():
    return {"csrf_token": _csrf_token(), "csp_nonce": g.get("nonce", ""),
            "llm_model": MAIN_LLM_MODEL, "embedding_model": EMBEDDING_MODEL}


@app.after_request
def _after(response):
    return webguard.apply_headers(response, g.get("nonce", ""), HTTPS, static=request.endpoint == "static")


# ---------------------------------------------------------------------------------------------------- database helpers

def connect_to_db():
    """Connect with the credentials held in the server-side session (never in the browser cookie)."""
    creds = g.get("creds")
    if not creds:
        return None
    try:
        return mariadb.connect(host=creds["connect_host"], port=creds["db_port"], user=creds["db_user"],
                               password=creds["db_password"], database=creds["db_name"],
                               connect_timeout=CONNECT_TIMEOUT)
    except Exception as e:
        app.logger.warning("Connection error (%s)", type(e).__name__)
        return None


def load_schema(conn):
    """Return (CREATE statements text, table names) for an open connection; (None, []) on error."""
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES;")
        tables = [row[0] for row in cursor.fetchall()]

        schema_text = ""
        for table in tables:
            cursor.execute("SHOW CREATE TABLE `{}`;".format(table.replace("`", "``")))
            result = cursor.fetchone()
            if result and len(result) > 1:
                schema_text += f"{result[1]};\n\n"
        cursor.close()
        return schema_text, tables
    except Exception as error:
        app.logger.warning("Schema load error (%s)", type(error).__name__)
        return None, []


def load_schema_from_session():
    conn = connect_to_db()
    if not conn:
        return None, []
    try:
        return load_schema(conn)
    finally:
        conn.close()


def _login_page(error="", status=200):
    return render_template("login.html", error=error), status


# ---------------------------------------------------------------------------------------------------- routes

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return _login_page()

    fields, problem = webguard.validate_login_fields(request.form)
    if fields is None:
        return _login_page(GENERIC_LOGIN_ERROR)

    ip_key = "ip:" + (request.remote_addr or "?")
    account_key = "acct:" + hashlib.sha256("{user}|{host}|{port}|{name}".format(**fields).lower().encode()).hexdigest()
    wait = max(ip_limiter.blocked_for(ip_key), account_limiter.blocked_for(account_key))
    if wait:
        response = make_response(*_login_page("Too many failed login attempts. Please try again later.", 429))
        response.headers["Retry-After"] = str(wait)
        return response

    def fail(message=GENERIC_LOGIN_ERROR):
        ip_limiter.failure(ip_key)
        account_limiter.failure(account_key)
        return _login_page(message)

    try:
        connect_host = webguard.resolve_db_host(fields["host"], ALLOWED_DB_HOSTS)
    except webguard.HostPolicyError as e:
        app.logger.warning("Login refused by the database host policy")
        ip_limiter.failure(ip_key)
        return _login_page(str(e))

    conn = None
    try:
        conn = mariadb.connect(host=connect_host, port=fields["port"], user=fields["user"],
                               password=fields["password"], database=fields["name"], connect_timeout=CONNECT_TIMEOUT)
        # Security layer 1: the account must be SELECT-only (QUERYMIND_PRIVILEGE_POLICY=enforce|warn|off)
        privileges = security.check_select_only_account(conn) if security.PRIVILEGE_POLICY != "off" else None
        if privileges and not privileges["read_only"]:
            app.logger.warning("Security: database account is not SELECT-only: %s", "; ".join(privileges["problems"]))
            if security.PRIVILEGE_POLICY == "enforce":
                return fail("This database account has more than read-only (SELECT) privileges. "
                            "QueryMind requires a SELECT-only account.")
        schema_text, _tables = load_schema(conn)
    except mariadb.Error as e:
        app.logger.warning("Login failed (errno=%s)", getattr(e, "errno", None))
        return fail()
    except Exception as e:
        app.logger.warning("Login error (%s)", type(e).__name__)
        return fail("Connection failed. Please verify your credentials and try again.")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    account_limiter.success(account_key)

    # A fresh server-side session and CSRF token for every login (prevents session fixation); the previous session,
    # if any, is ended and its private index removed.
    sessions.delete(session.get("sid"))
    persist_path = webguard.new_index_path(INDEX_BASE)
    if schema_text:
        try:
            index_schema_in_chroma(schema_text, persist_path=persist_path, model=EMBEDDING_MODEL)
        except Exception as e:
            app.logger.warning("ChromaDB indexing error (non-fatal, %s)", type(e).__name__)
    sid = sessions.create({"db_host": fields["host"], "connect_host": connect_host, "db_port": fields["port"],
                           "db_user": fields["user"], "db_password": fields["password"], "db_name": fields["name"],
                           "persist_path": persist_path})
    session.clear()
    session["sid"] = sid
    session["csrf"] = webguard.new_token()
    return redirect(url_for("home"))


@app.route("/logout", methods=["POST"])
def logout():
    sessions.delete(session.get("sid"))
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/table/<table_name>")
def get_table_metadata(table_name):
    """API endpoint to get table metadata (columns, types, keys, etc.)"""
    if not g.creds:
        return {"error": "Not authenticated"}, 401
    if not webguard.is_identifier(table_name):
        return {"error": "Invalid table name"}, 400

    conn = connect_to_db()
    if not conn:
        return {"error": "Could not connect to database"}, 502

    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES;")
        if table_name not in [row[0] for row in cursor.fetchall()]:
            return {"error": "Table not found"}, 404
        cursor.execute("DESCRIBE `{}`;".format(table_name.replace("`", "``")))
        columns = cursor.fetchall()

        metadata = []
        for col in columns:
            metadata.append({
                "column": col[0],
                "type": col[1],
                "null": col[2],
                "key": col[3] if col[3] else "-",
                "default": str(col[4]) if col[4] is not None else "-"
            })

        cursor.close()
        return {"table": table_name, "columns": metadata}
    except Exception as e:
        app.logger.warning("Table metadata error (%s)", type(e).__name__)
        return {"error": "Could not load table metadata"}, 500
    finally:
        conn.close()


def _render_home(user_input, error, tables):
    creds = g.creds
    return render_template(
        "home.html",
        user_input=user_input,
        error=error,
        db_name=creds['db_name'],
        db_user=creds['db_user'],
        db_host=creds['db_host'],
        db_port=creds['db_port'],
        tables=tables
    )


@app.route("/", methods=["GET", "POST"])
def home():
    creds = g.creds
    if not creds:
        return redirect(url_for('login'))

    error = ""
    user_input = ""

    schema_text, accessible_tables = load_schema_from_session()

    if request.method == "POST":
        user_input = request.form.get("user_input", "").strip()

        # Security layer 2a: screen the input before it reaches retrieval or the model
        input_ok, input_problem = security.validate_question(user_input)
        if not input_ok:
            return _render_home(user_input, input_problem, accessible_tables)

        time_start_rag = time.time()
        rag_context = retrieve_schema_context(
            user_input,
            persist_path=creds['persist_path'],
            model=EMBEDDING_MODEL
        )

        if rag_context.startswith("ERROR:") and schema_text:
            app.logger.info("Schema not indexed, re-indexing now...")
            try:
                index_schema_in_chroma(schema_text, persist_path=creds['persist_path'], model=EMBEDDING_MODEL)
                rag_context = retrieve_schema_context(
                    user_input,
                    persist_path=creds['persist_path'],
                    model=EMBEDDING_MODEL
                )
            except Exception as e:
                app.logger.warning("Re-indexing failed (%s)", type(e).__name__)

        time_end_rag = time.time()

        time_start_llm = time.time()
        llm_output = ask_llm(user_input, rag_context, model_name=MAIN_LLM_MODEL)
        time_end_llm = time.time()

        sql_query = extract_sql(llm_output)

        time_rag = round(time_end_rag - time_start_rag, 3)
        time_llm = round(time_end_llm - time_start_llm, 3)
        time_total_generation = round(time_rag + time_llm, 3)

        if sql_query.startswith("Error:"):
            return _render_home(user_input, sql_query, accessible_tables)

        # Security layer 2b: validate the generated SQL (single read-only SELECT on known tables) before execution
        sql_check = security.validate_sql(sql_query, accessible_tables or [], creds['db_name'])
        if not sql_check.ok:
            app.logger.info("Security: generated SQL blocked (%s)", sql_check.rule)
            return _render_home(user_input, f"Error: {sql_check.message}", accessible_tables)

        conn = connect_to_db()
        if not conn:
            return _render_home(user_input, "Could not connect to the database.", accessible_tables)

        try:
            time_start_exec = time.time()
            # Security layer 3: run inside a never-committed transaction and check the modified-row count
            results = security.run_query_guarded(sql_query, conn)
            if isinstance(results, str) and results.startswith("Error:"):
                # show the message (the result template only displays `error`; Jinja escapes it)
                error, results = results, ""
            time_end_exec = time.time()
            time_execution = round(time_end_exec - time_start_exec, 3)
        except Exception as e:
            app.logger.warning("Query execution error (%s)", type(e).__name__)
            error = "The query could not be executed."
            results = ""
            time_execution = 0
        finally:
            conn.close()

        return render_template(
            "result.html",
            user_input=user_input,
            sql_query=sql_query,
            results=results,
            error=error,
            db_name=creds['db_name'],
            db_user=creds['db_user'],
            db_host=creds['db_host'],
            db_port=creds['db_port'],
            time_rag=time_rag,
            time_llm=time_llm,
            time_generation=time_total_generation,
            time_execution=time_execution
        )

    return _render_home(user_input, error, accessible_tables)


if __name__ == "__main__":
    # Debug mode is never enabled here; the server listens on the loopback interface only.
    app.run(host=os.getenv("QUERYMIND_HOST", "127.0.0.1"), port=int(os.getenv("QUERYMIND_PORT", "5000")), debug=False)
