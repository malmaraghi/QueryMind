"""Security regression tests for the Flask application (no database, no model: both are replaced by fakes)."""
import base64
import json
import os
import re
import tempfile
import unittest
from unittest import mock

import context  # noqa: F401

os.environ["SECRET_KEY"] = "unit-test-secret-key-0123456789abcdef"
os.environ["QUERYMIND_INDEX_DIR"] = tempfile.mkdtemp(prefix="qm-test-index-")

import app as appmod  # noqa: E402
import security  # noqa: E402

PASSWORD = "Zq9-unit-test-PASSWORD-77"
TABLES = ["customers", "orders"]


class FakeCursor:
    def __init__(self):
        self.last = ""

    def execute(self, sql, *args):
        self.last = sql

    def fetchall(self):
        if self.last.startswith("SHOW TABLES"):
            return [(t,) for t in TABLES]
        if self.last.startswith("DESCRIBE"):
            return [("id", "int(11)", "NO", "PRI", None, ""), ("name", "varchar(50)", "YES", "", "x", "")]
        return []

    def fetchone(self):
        return ("t", "CREATE TABLE `t` (id int)")

    def close(self):
        pass


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def close(self):
        pass


def csrf_of(client, path="/login"):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def decode_cookie(value):
    payload = value.split(".")[0]
    payload = payload.lstrip(".")
    import zlib
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    try:
        raw = zlib.decompress(raw)
    except zlib.error:
        pass
    return raw.decode("utf-8", "replace")


class AppTestCase(unittest.TestCase):
    def setUp(self):
        appmod.app.config["TESTING"] = True
        appmod.account_limiter._failures.clear()
        appmod.ip_limiter._failures.clear()
        self.patches = [
            mock.patch.object(appmod.mariadb, "connect", lambda **kw: FakeConn()),
            mock.patch.object(security, "check_select_only_account", lambda conn: {"read_only": True, "problems": []}),
            mock.patch.object(appmod, "index_schema_in_chroma", lambda *a, **k: None),
            mock.patch.object(appmod, "retrieve_schema_context", lambda *a, **k: "context"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def login(self, client=None, user="reader", host="localhost", **extra):
        client = client or appmod.app.test_client()
        data = {"csrf_token": csrf_of(client), "db_host": host, "db_port": "3306", "db_user": user,
                "db_password": PASSWORD, "db_name": "shop"}
        data.update(extra)
        return client, client.post("/login", data=data)


class LoginAndSessionTests(AppTestCase):
    def test_login_works_and_cookie_holds_no_credentials(self):
        client, response = self.login()
        self.assertEqual(response.status_code, 302)
        cookies = [h for h in response.headers.getlist("Set-Cookie")]
        self.assertTrue(cookies)
        for cookie in cookies:
            self.assertNotIn(PASSWORD, cookie)
            decoded = decode_cookie(cookie.split("=", 1)[1].split(";")[0])
            for secret in (PASSWORD, "reader", "shop", "db_password", "persist_path"):
                self.assertNotIn(secret, decoded)
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("customers", page.get_data(as_text=True))

    def test_cookie_flags(self):
        _, response = self.login()
        cookie = response.headers.getlist("Set-Cookie")[0]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie)          # only when QUERYMIND_HTTPS=1
        self.assertFalse(appmod.app.config["SESSION_COOKIE_SECURE"] and not appmod.HTTPS)

    def test_https_mode_marks_cookies_secure(self):
        with mock.patch.dict(appmod.app.config, {"SESSION_COOKIE_SECURE": True}):
            client = appmod.app.test_client()
            response = client.get("/login", base_url="https://localhost")
            self.assertIn("Secure", response.headers.getlist("Set-Cookie")[0])

    def test_unauthenticated_access_is_refused(self):
        client = appmod.app.test_client()
        self.assertEqual(client.get("/").status_code, 302)
        self.assertEqual(client.get("/api/table/customers").status_code, 401)

    def test_forged_or_stale_session_cookie_is_not_authenticated(self):
        client, _ = self.login()
        with client.session_transaction() as s:
            s["sid"] = "forged-session-id"
        self.assertEqual(client.get("/").status_code, 302)

    def test_session_id_changes_at_login_and_logout_ends_the_session(self):
        client = appmod.app.test_client()
        with client.session_transaction() as s:
            s["sid"] = "attacker-chosen"
        self.login(client)
        with client.session_transaction() as s:
            sid = s["sid"]
        self.assertNotEqual(sid, "attacker-chosen")
        token = csrf_of(client, "/")
        before = len(appmod.sessions)
        self.assertEqual(client.post("/logout", data={"csrf_token": token}).status_code, 302)
        self.assertEqual(len(appmod.sessions), before - 1)
        self.assertIsNone(appmod.sessions.get(sid))
        self.assertEqual(client.get("/").status_code, 302)

    def test_two_users_are_independent(self):
        c1, _ = self.login(user="alice")
        c2, _ = self.login(user="bob")
        self.assertIn("alice", c1.get("/").get_data(as_text=True))
        self.assertIn("bob", c2.get("/").get_data(as_text=True))
        with c1.session_transaction() as a, c2.session_transaction() as b:
            sid1, sid2 = a["sid"], b["sid"]
        self.assertNotEqual(sid1, sid2)
        self.assertNotEqual(appmod.sessions.get(sid1)["persist_path"], appmod.sessions.get(sid2)["persist_path"])
        c1.post("/logout", data={"csrf_token": csrf_of(c1, "/")})
        self.assertIn("bob", c2.get("/").get_data(as_text=True))       # bob is unaffected by alice's logout

    def test_every_login_gets_its_own_index_directory(self):
        c1, _ = self.login(user="same")
        c2, _ = self.login(user="same")
        with c1.session_transaction() as a, c2.session_transaction() as b:
            self.assertNotEqual(appmod.sessions.get(a["sid"])["persist_path"], appmod.sessions.get(b["sid"])["persist_path"])

    def test_logout_removes_the_session_index(self):
        client, _ = self.login()
        with client.session_transaction() as s:
            path = appmod.sessions.get(s["sid"])["persist_path"]
        os.makedirs(path)
        client.post("/logout", data={"csrf_token": csrf_of(client, "/")})
        self.assertFalse(os.path.exists(path))


class CsrfTests(AppTestCase):
    def test_login_without_or_with_wrong_token_is_rejected(self):
        client = appmod.app.test_client()
        client.get("/login")
        data = {"db_host": "localhost", "db_port": "3306", "db_user": "u", "db_password": PASSWORD, "db_name": "shop"}
        self.assertEqual(client.post("/login", data=data).status_code, 400)
        self.assertEqual(client.post("/login", data={**data, "csrf_token": "wrong"}).status_code, 400)
        self.assertEqual(appmod.app.test_client().post("/login", data=data).status_code, 400)   # no cookie at all

    def test_logout_requires_post_and_token(self):
        client, _ = self.login()
        self.assertEqual(client.get("/logout").status_code, 405)
        self.assertEqual(client.post("/logout").status_code, 400)
        self.assertEqual(client.post("/logout", data={"csrf_token": "x"}).status_code, 400)
        self.assertEqual(client.get("/").status_code, 200)            # still logged in

    def test_question_post_requires_token_and_header_token_is_accepted(self):
        client, _ = self.login()
        self.assertEqual(client.post("/", data={"user_input": "list customers"}).status_code, 400)
        token = csrf_of(client, "/")
        with mock.patch.object(appmod, "ask_llm", lambda *a, **k: "SELECT 1"):
            ok = client.post("/", data={"user_input": "ok"}, headers={"X-CSRF-Token": token})
        self.assertNotEqual(ok.status_code, 400)

    def test_token_changes_with_login(self):
        client, _ = self.login()
        first = csrf_of(client, "/")
        self.login(client)
        self.assertNotEqual(first, csrf_of(client, "/"))

    def test_tokens_are_not_shared_between_users(self):
        c1, _ = self.login(user="a")
        c2, _ = self.login(user="b")
        self.assertNotEqual(csrf_of(c1, "/"), csrf_of(c2, "/"))
        self.assertEqual(c2.post("/logout", data={"csrf_token": csrf_of(c1, "/")}).status_code, 400)


class QueryFlowTests(AppTestCase):
    def ask(self, client, llm_output, question="list customers"):
        token = csrf_of(client, "/")
        with mock.patch.object(appmod, "ask_llm", lambda *a, **k: llm_output):
            return client.post("/", data={"user_input": question, "csrf_token": token}).get_data(as_text=True)

    def test_select_runs_through_the_guarded_executor(self):
        client, _ = self.login()
        result = type("R", (), {})()
        calls = []

        def guarded(sql, conn):
            calls.append(sql)
            return "No records."
        with mock.patch.object(security, "run_query_guarded", guarded):
            html = self.ask(client, "SELECT * FROM customers")
        self.assertEqual(calls, ["SELECT * FROM customers"])
        self.assertIn('data-qm="result-page"', html)

    def test_unauthorized_sql_is_still_blocked_before_execution(self):
        client, _ = self.login()
        executed = []
        with mock.patch.object(security, "run_query_guarded", lambda *a, **k: executed.append(a) or "No records."):
            for sql in ("DELETE FROM customers", "SELECT * FROM mysql.user", "UPDATE customers SET name='x'",
                        "SELECT SLEEP(5)", "SELECT * FROM customers INTO OUTFILE '/tmp/x'"):
                html = self.ask(client, sql)
                self.assertNotIn('data-qm="result-page"', html, sql)
        self.assertEqual(executed, [])

    def test_stacked_statement_never_reaches_the_database_intact(self):
        client, _ = self.login()
        executed = []
        with mock.patch.object(security, "run_query_guarded", lambda sql, conn: executed.append(sql) or "No records."):
            self.ask(client, "SELECT * FROM customers; DROP TABLE orders")
        self.assertTrue(all("DROP" not in sql.upper() for sql in executed))

    def test_dangerous_question_is_blocked_before_the_model(self):
        client, _ = self.login()
        with mock.patch.object(appmod, "ask_llm", side_effect=AssertionError("model must not be called")):
            token = csrf_of(client, "/")
            html = client.post("/", data={"user_input": "x" * 5000, "csrf_token": token}).get_data(as_text=True)
        self.assertIn('data-qm="error"', html)

    def test_error_text_is_escaped_not_rendered(self):
        client, _ = self.login()
        with mock.patch.object(security, "run_query_guarded", lambda *a, **k: "Error: <script>alert(1)</script>"):
            html = self.ask(client, "SELECT * FROM customers")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_internal_exception_text_is_not_shown(self):
        client, _ = self.login()

        def boom(*a, **k):
            raise RuntimeError("secret-internal-detail host=10.0.0.5")
        with mock.patch.object(security, "run_query_guarded", boom):
            html = self.ask(client, "SELECT * FROM customers")
        self.assertNotIn("secret-internal-detail", html)
        self.assertIn("could not be executed", html)

    def test_user_input_is_escaped_in_the_page(self):
        client, _ = self.login()
        html = self.ask(client, "Error: x", question='"><img src=x onerror=alert(1)>')
        self.assertNotIn("<img src=x", html)


class TableEndpointTests(AppTestCase):
    def test_valid_table(self):
        client, _ = self.login()
        data = client.get("/api/table/customers").get_json()
        self.assertEqual([c["column"] for c in data["columns"]], ["id", "name"])

    def test_invalid_and_unknown_names(self):
        client, _ = self.login()
        self.assertIn(client.get("/api/table/..%2Fx").status_code, (400, 404))
        for name in ("a%60b", "customers%60%3B%20DROP%20TABLE%20x", "x%27", "a" * 65):
            self.assertEqual(client.get("/api/table/" + name).status_code, 400, name)
        self.assertEqual(client.get("/api/table/not_a_table").status_code, 404)

    def test_database_errors_are_generic(self):
        client, _ = self.login()

        class Bad(FakeCursor):
            def execute(self, sql, *a):
                if sql.startswith("DESCRIBE"):
                    raise RuntimeError("secret-internal-detail")
                super().execute(sql)
        with mock.patch.object(FakeConn, "cursor", lambda self: Bad()):
            response = client.get("/api/table/customers")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret-internal-detail", response.get_data(as_text=True))


class LoginProtectionTests(AppTestCase):
    def test_failed_logins_are_rate_limited(self):
        def refuse(**kw):
            raise appmod.mariadb.Error("Access denied for user 'x'@'h'")
        with mock.patch.object(appmod.mariadb, "connect", refuse):
            client = appmod.app.test_client()
            statuses = [self.login(client)[1].status_code for _ in range(appmod._MAX_FAILURES + 2)]
        self.assertEqual(statuses[0], 200)
        self.assertEqual(statuses[-1], 429)
        response = self.login(client)[1]
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    def test_rate_limit_is_per_account(self):
        def refuse(**kw):
            raise appmod.mariadb.Error("denied")
        with mock.patch.object(appmod.mariadb, "connect", refuse):
            for _ in range(appmod._MAX_FAILURES):
                self.login(appmod.app.test_client(), user="victim")
        self.assertEqual(self.login(appmod.app.test_client(), user="victim")[1].status_code, 429)
        self.assertEqual(self.login(appmod.app.test_client(), user="other")[1].status_code, 302)

    def test_login_errors_do_not_leak_details(self):
        def refuse(**kw):
            raise appmod.mariadb.Error("Access denied for user 'reader'@'10.0.0.9' (using password: YES)")
        with mock.patch.object(appmod.mariadb, "connect", refuse):
            body = self.login()[1].get_data(as_text=True)
        self.assertNotIn("10.0.0.9", body)
        self.assertNotIn("Access denied for user", body)

    def test_internal_hosts_are_refused_without_a_connection_attempt(self):
        attempts = []
        with mock.patch.object(appmod.mariadb, "connect", lambda **kw: attempts.append(kw)):
            for host in ("169.254.169.254", "10.0.0.5", "192.168.1.1", "0.0.0.0", "metadata.invalid"):
                body = self.login(host=host)[1].get_data(as_text=True)
                self.assertIn('data-qm="error"', body, host)
        self.assertEqual(attempts, [])

    def test_allowlisted_private_host_is_accepted_and_pinned(self):
        seen = {}

        def connect(**kw):
            seen.update(kw)
            return FakeConn()
        with mock.patch.object(appmod.mariadb, "connect", connect), mock.patch.object(appmod, "ALLOWED_DB_HOSTS", ["10.0.0.0/8"]):
            response = self.login(host="10.0.0.5")[1]
        self.assertEqual(response.status_code, 302)
        self.assertEqual(seen["host"], "10.0.0.5")
        self.assertIn("connect_timeout", seen)

    def test_invalid_fields_are_rejected(self):
        for change in ({"db_port": "99999"}, {"db_name": "a`b"}, {"db_user": ""}):
            response = self.login(**change)[1]
            self.assertEqual(response.status_code, 200)

    def test_non_read_only_account_is_refused(self):
        with mock.patch.object(security, "check_select_only_account",
                               lambda c: {"read_only": False, "problems": ["INSERT"]}):
            response = self.login()[1]
        self.assertEqual(response.status_code, 200)
        self.assertIn("SELECT-only", response.get_data(as_text=True))


class HeadersAndTemplatesTests(AppTestCase):
    def test_security_headers_and_nonce(self):
        client, _ = self.login()
        for path in ("/login", "/"):
            response = client.get(path)
            csp = response.headers["Content-Security-Policy"]
            nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
            self.assertNotRegex(response.get_data(as_text=True), r"<script(?![^>]*\bsrc=)")      # no inline scripts at all
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotEqual(nonce, re.search(r"'nonce-([^']+)'", client.get("/").headers["Content-Security-Policy"]).group(1))

    def test_front_end_script_never_writes_html(self):
        text = open(os.path.join(context.ROOT, "static", "js", "app.js"), encoding="utf-8").read()
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_static_assets_referenced_by_the_pages_exist(self):
        client, _ = self.login()
        for path in ("/login", "/"):
            html = client.get(path).get_data(as_text=True)
            for url in re.findall(r'(?:href|src)="(/static/[^"]+)"', html):
                self.assertEqual(client.get(url).status_code, 200, url)

    def test_templates_have_no_unsafe_rendering(self):
        directory = os.path.join(context.ROOT, "templates")
        for name in os.listdir(directory):
            text = open(os.path.join(directory, name), encoding="utf-8").read()
            self.assertNotIn("|safe", text, name)
            self.assertNotIn("innerHTML", text, name)
            self.assertNotIn("onclick=", text, name)
            if name.endswith(".html"):
                self.assertNotRegex(text, r"<script(?![^>]*\bsrc=)", name)
                self.assertNotIn("style=", text.replace('style="display:contents"', ""), name)      # no inline styles

    def test_post_forms_carry_a_csrf_field(self):
        client, _ = self.login()
        for path in ("/login", "/"):
            html = client.get(path).get_data(as_text=True)
            forms = len(re.findall(r'<form[^>]*method="post"', html))
            self.assertEqual(forms, html.count('name="csrf_token"'), path)


class StartupTests(unittest.TestCase):
    def run_import(self, env):
        import subprocess
        import sys
        clean = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "FLASK_DEBUG", "QUERYMIND_ALLOW_DEBUG")}
        clean.update(env)
        return subprocess.run([sys.executable, "-c", "import app"], cwd=context.ROOT, env=clean,
                              capture_output=True, text=True)

    def test_missing_or_short_secret_key_fails(self):
        for env in ({}, {"SECRET_KEY": ""}, {"SECRET_KEY": "short"}):
            result = self.run_import(env)
            self.assertNotEqual(result.returncode, 0, env)
            self.assertIn("SECRET_KEY", result.stderr)

    def test_default_secret_is_gone(self):
        self.assertNotIn("supersecret", open(os.path.join(context.ROOT, "app.py"), encoding="utf-8").read())

    def test_debug_mode_is_refused(self):
        result = self.run_import({"SECRET_KEY": "x" * 32, "FLASK_DEBUG": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("debug", result.stderr.lower())

    def test_normal_start_succeeds_and_main_block_disables_debug(self):
        self.assertEqual(self.run_import({"SECRET_KEY": "x" * 32}).returncode, 0)
        self.assertIn("debug=False", open(os.path.join(context.ROOT, "app.py"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
