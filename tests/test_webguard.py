import os
import tempfile
import unittest

import context  # noqa: F401
import webguard
from webguard import HostPolicyError, RateLimiter, SessionStore, resolve_db_host


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class SessionStoreTests(unittest.TestCase):
    def test_create_get_delete_and_unknown_ids(self):
        store = SessionStore()
        sid = store.create({"a": 1})
        self.assertEqual(store.get(sid), {"a": 1})
        self.assertIsNone(store.get("nope"))
        self.assertIsNone(store.get(None))
        self.assertIsNone(store.get("x" * 500))
        store.delete(sid)
        self.assertIsNone(store.get(sid))

    def test_ids_are_unique_and_long(self):
        store = SessionStore()
        ids = {store.create({}) for _ in range(50)}
        self.assertEqual(len(ids), 50)
        self.assertTrue(all(len(i) >= 40 for i in ids))

    def test_idle_and_absolute_timeouts_call_cleanup(self):
        clock, expired = Clock(), []
        store = SessionStore(idle_seconds=10, absolute_seconds=30, clock=clock, on_expire=expired.append)
        idle = store.create({"n": "idle"})
        clock.now = 11
        self.assertIsNone(store.get(idle))
        busy = store.create({"n": "busy"})
        for _ in range(3):
            clock.now += 9
            self.assertIsNotNone(store.get(busy))
        clock.now += 9                                  # 36 s after creation (limit 30), but only 9 s idle
        self.assertIsNone(store.get(busy))
        self.assertEqual([d["n"] for d in expired], ["idle", "busy"])

    def test_capacity_evicts_the_least_recently_used(self):
        clock, expired = Clock(), []
        store = SessionStore(max_sessions=2, clock=clock, on_expire=expired.append)
        first = store.create({"n": 1}); clock.now = 1
        second = store.create({"n": 2}); clock.now = 2
        store.get(first); clock.now = 3
        store.create({"n": 3})
        self.assertIsNone(store.get(second))
        self.assertIsNotNone(store.get(first))

    def test_sessions_are_isolated(self):
        store = SessionStore()
        a, b = store.create({"user": "a"}), store.create({"user": "b"})
        store.get(a)["user"] = "changed"
        self.assertEqual(store.get(b), {"user": "b"})


class RateLimiterTests(unittest.TestCase):
    def test_blocks_after_max_failures_and_recovers(self):
        clock = Clock()
        limiter = RateLimiter(max_failures=3, window=60, clock=clock)
        for _ in range(3):
            self.assertEqual(limiter.blocked_for("k"), 0)
            limiter.failure("k")
        self.assertGreater(limiter.blocked_for("k"), 0)
        self.assertEqual(limiter.blocked_for("other"), 0)
        clock.now = 61
        self.assertEqual(limiter.blocked_for("k"), 0)

    def test_success_clears_the_key(self):
        limiter = RateLimiter(max_failures=2, window=60)
        limiter.failure("k"); limiter.failure("k")
        limiter.success("k")
        self.assertEqual(limiter.blocked_for("k"), 0)

    def test_memory_is_bounded(self):
        limiter = RateLimiter(max_failures=2, window=60, max_keys=5)
        for i in range(50):
            limiter.failure(f"k{i}")
        self.assertLessEqual(len(limiter._failures), 5)


def fake_resolver(mapping):
    def resolver(host, port, type=None):
        return [(2, 1, 6, "", (ip, 0)) for ip in mapping[host]]
    return resolver


class HostPolicyTests(unittest.TestCase):
    def test_loopback_is_allowed_by_default(self):
        self.assertEqual(resolve_db_host("localhost"), "localhost")
        self.assertEqual(resolve_db_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(resolve_db_host("[::1]"), "::1")

    def test_dangerous_addresses_are_always_refused(self):
        for host in ("169.254.169.254", "0.0.0.0", "224.0.0.1", "fe80::1", "::", "::ffff:169.254.169.254", "240.0.0.1"):
            with self.assertRaises(HostPolicyError, msg=host):
                resolve_db_host(host, ["0.0.0.0/0", host])

    def test_private_and_public_hosts_need_the_allowlist(self):
        for host in ("10.1.2.3", "192.168.0.5", "172.16.0.9", "8.8.8.8"):
            with self.assertRaises(HostPolicyError, msg=host):
                resolve_db_host(host)
        self.assertEqual(resolve_db_host("10.1.2.3", ["10.0.0.0/8"]), "10.1.2.3")
        self.assertEqual(resolve_db_host("8.8.8.8", ["8.8.8.8"]), "8.8.8.8")

    def test_names_are_resolved_once_and_the_checked_address_is_used(self):
        resolver = fake_resolver({"db.example.org": ["203.0.113.7"], "evil.example.org": ["169.254.169.254"],
                                  "mixed.example.org": ["203.0.113.7", "169.254.169.254"]})
        self.assertEqual(resolve_db_host("db.example.org", ["db.example.org"], resolver), "203.0.113.7")
        self.assertEqual(resolve_db_host("db.example.org", ["203.0.113.0/24"], resolver), "203.0.113.7")
        with self.assertRaises(HostPolicyError):
            resolve_db_host("db.example.org", [], resolver)
        with self.assertRaises(HostPolicyError):
            resolve_db_host("evil.example.org", ["evil.example.org"], resolver)
        with self.assertRaises(HostPolicyError):
            resolve_db_host("mixed.example.org", ["mixed.example.org"], resolver)

    def test_malformed_hosts_and_unresolvable_names(self):
        for host in ("", "a b", "host;rm", "http://x", "x" * 300, "a/b"):
            with self.assertRaises(HostPolicyError, msg=host):
                resolve_db_host(host)
        with self.assertRaises(HostPolicyError):
            resolve_db_host("nx.example.org", [], lambda *a, **k: (_ for _ in ()).throw(OSError()))


class ValidationTests(unittest.TestCase):
    def test_identifier(self):
        for ok in ("customers", "order_details", "T1", "a$b"):
            self.assertTrue(webguard.is_identifier(ok))
        for bad in ("", "a`b", "a b", "a;b", "../x", "a" * 65, "x'--", "ünï", None):
            self.assertFalse(webguard.is_identifier(bad), bad)

    def test_login_fields(self):
        good = {"db_host": " localhost ", "db_port": "3306", "db_user": "u", "db_password": "p", "db_name": "db_1"}
        fields, problem = webguard.validate_login_fields(good)
        self.assertIsNone(problem)
        self.assertEqual((fields["host"], fields["port"]), ("localhost", 3306))
        for change in ({"db_port": "0"}, {"db_port": "70000"}, {"db_port": "x"}, {"db_user": ""}, {"db_name": "a;b"},
                       {"db_name": "a`b"}, {"db_user": "a\nb"}, {"db_password": "x" * 300}, {"db_password": "a\x00b"}):
            fields, problem = webguard.validate_login_fields({**good, **change})
            self.assertIsNone(fields, change)


class TokenAndIndexTests(unittest.TestCase):
    def test_tokens(self):
        token = webguard.new_token()
        self.assertTrue(webguard.tokens_match(token, token))
        self.assertFalse(webguard.tokens_match(token, token + "x"))
        self.assertFalse(webguard.tokens_match(token, ""))
        self.assertFalse(webguard.tokens_match(None, None))
        self.assertNotEqual(token, webguard.new_token())

    def test_index_dirs_are_unique_and_removal_is_confined(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as outside:
            a, b = webguard.new_index_path(base), webguard.new_index_path(base)
            self.assertNotEqual(a, b)
            os.makedirs(a); os.makedirs(b)
            self.assertTrue(webguard.remove_index_dir(a, base))
            self.assertFalse(os.path.exists(a))
            self.assertTrue(os.path.exists(b))
            self.assertFalse(webguard.remove_index_dir(outside, base))          # outside the base
            self.assertFalse(webguard.remove_index_dir(base, base))             # the base itself
            self.assertFalse(webguard.remove_index_dir(os.path.join(base, "..", os.path.basename(outside)), base))
            keep = os.path.join(base, "legacy1234"); os.makedirs(keep)
            self.assertFalse(webguard.remove_index_dir(keep, base))             # not a sess- directory
            self.assertTrue(os.path.exists(keep) and os.path.exists(outside))

    def test_stale_purge_only_touches_session_dirs(self):
        with tempfile.TemporaryDirectory() as base:
            old, fresh, legacy = (os.path.join(base, n) for n in ("sess-old", "sess-new", "abcd1234"))
            for path in (old, fresh, legacy):
                os.makedirs(path)
            os.utime(old, (1, 1)); os.utime(legacy, (1, 1))
            self.assertEqual(webguard.purge_stale_index_dirs(base, 3600), 1)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(fresh) and os.path.exists(legacy))


class HeaderTests(unittest.TestCase):
    def test_headers(self):
        class R:
            headers = {}
        response = webguard.apply_headers(R(), "abc", https=False)
        self.assertIn("'nonce-abc'", response.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertNotIn("'unsafe-inline'", response.headers["Content-Security-Policy"].split("script-src")[1].split(";")[0])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Strict-Transport-Security", response.headers)
        R.headers = {}
        self.assertIn("Strict-Transport-Security", webguard.apply_headers(R(), "abc", https=True).headers)


if __name__ == "__main__":
    unittest.main()
