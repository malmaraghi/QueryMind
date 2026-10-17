import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import context  # noqa: F401
import evaluation as ev
import resilience
import run_benchmark
from resilience import BlockedError, ResilientConnection


class FakeConnection:
    def __init__(self, alive=True):
        self.alive, self.closed = alive, False

    def ping(self):
        if not self.alive:
            raise RuntimeError("server has gone away")

    def cursor(self):
        return "cursor"

    def close(self):
        self.closed = True


class ResilientConnectionTests(unittest.TestCase):
    def make(self, connections, **kwargs):
        pending = list(connections)
        calls = []

        def connect():
            calls.append(1)
            item = pending.pop(0)
            if isinstance(item, Exception):
                raise item
            return item, {"grants": ["GRANT SELECT ..."]}

        sleeps = []
        wrapper = ResilientConnection(connect, sleep=sleeps.append, **kwargs)
        return wrapper, calls, sleeps

    def test_healthy_connection_is_not_replaced(self):
        wrapper, calls, _ = self.make([FakeConnection()])
        self.assertFalse(wrapper.recover())
        self.assertEqual((wrapper.cursor(), wrapper.reconnects, len(calls)), ("cursor", 0, 1))

    def test_dropped_connection_is_re_established(self):
        dead, fresh = FakeConnection(alive=False), FakeConnection()
        wrapper, calls, _ = self.make([FakeConnection(), fresh])
        wrapper._connection = dead
        self.assertTrue(wrapper.recover())
        self.assertEqual((wrapper.reconnects, len(calls), dead.closed), (1, 2, True))
        self.assertEqual(wrapper.cursor(), "cursor")

    def test_retries_then_stops_the_run(self):
        wrapper, calls, sleeps = self.make([FakeConnection(), OSError("down"), OSError("down"), OSError("down")],
                                           retries=3, backoff=2.0)
        wrapper._connection = FakeConnection(alive=False)
        with self.assertRaises(BlockedError):
            wrapper.recover()
        self.assertEqual((len(calls), sleeps), (4, [2.0, 4.0, 6.0]))

    def test_recovers_on_a_later_attempt(self):
        wrapper, calls, _ = self.make([FakeConnection(), OSError("down"), FakeConnection()])
        wrapper._connection = FakeConnection(alive=False)
        self.assertTrue(wrapper.recover())
        self.assertEqual(wrapper.reconnects, 1)

    def test_losing_read_only_status_is_never_retried(self):
        wrapper, calls, _ = self.make([FakeConnection(), BlockedError("not read-only")], retries=3)
        wrapper._connection = FakeConnection(alive=False)
        with self.assertRaises(BlockedError):
            wrapper.recover()
        self.assertEqual(len(calls), 2)


class ExecuteWithRecoveryTests(unittest.TestCase):
    def test_mid_query_drop_is_retried_once(self):
        connection = mock.Mock()
        connection.recover.return_value = True
        outcomes = [RuntimeError("Lost connection"), (1, [(1,)])]

        def fake_execute(_conn, _sql):
            result = outcomes.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(run_benchmark, "execute_sql", side_effect=fake_execute):
            self.assertEqual(run_benchmark.execute_with_recovery(connection, "SELECT 1"), (1, [(1,)], True))

    def test_reconnect_before_the_query_is_flagged_on_the_trial(self):
        class Counting:
            reconnects = 0

        connection = Counting()

        def fake_execute(conn, _sql):
            conn.reconnects += 1  # the wrapper reconnected inside cursor()
            return 1, [(1,)]

        question = {"id": 1, "question": "q", "gold_sql": "SELECT 1", "required_tables": ["customers"],
                    "order_significant": False}
        reference = {"ncols": 1, "rows": [(1,)], "legacy": "x"}
        with mock.patch("chroma_rag.retrieve_schema_context", return_value="-- Table: customers\n..."), \
                mock.patch("llm_engine.ask_llm", return_value="SELECT 1"), \
                mock.patch.object(run_benchmark, "execute_sql", side_effect=fake_execute):
            trial = run_benchmark.run_trial(question, 1, {"llm": "m", "embedding": "e"}, connection, reference,
                                            "/unused", {}, 5, False)
        self.assertTrue(trial["db_reconnected"])
        self.assertTrue(trial["result_correct"])

    def test_real_sql_error_is_not_masked(self):
        connection = mock.Mock()
        connection.recover.return_value = False
        with mock.patch.object(run_benchmark, "execute_sql", side_effect=RuntimeError("Unknown column")):
            with self.assertRaisesRegex(RuntimeError, "Unknown column"):
                run_benchmark.execute_with_recovery(connection, "SELECT x")

    def test_failed_reconnect_stops_the_run_without_a_fake_db_error(self):
        question = {"id": 1, "question": "q", "gold_sql": "SELECT 1", "required_tables": ["customers"],
                    "order_significant": False}
        reference = {"ncols": 1, "rows": [(1,)], "legacy": "x"}
        with mock.patch("chroma_rag.retrieve_schema_context", return_value="-- Table: customers\n..."), \
                mock.patch("llm_engine.ask_llm", return_value="SELECT 1"), \
                mock.patch.object(run_benchmark, "execute_with_recovery", side_effect=BlockedError("lost")):
            with self.assertRaises(BlockedError):
                run_benchmark.run_trial(question, 1, {"llm": "m", "embedding": "e"}, object(), reference,
                                        "/unused", {}, 5, False)


class SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(4)

    def log_message(self, *args):
        pass


class GenerationTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_slow_model_is_recorded_as_generation_error(self):
        import ollama

        client = ollama.Client(host=f"http://127.0.0.1:{self.server.server_port}", timeout=1)
        question = {"id": 1, "question": "List customers", "gold_sql": "SELECT 1", "required_tables": ["customers"],
                    "order_significant": False}
        reference = {"ncols": 1, "rows": [(1,)], "legacy": "x"}
        started = time.perf_counter()
        with mock.patch("chroma_rag.retrieve_schema_context", return_value="-- Table: customers\nCREATE TABLE c (x);"):
            trial = run_benchmark.run_trial(question, 1, {"llm": "m", "embedding": "e"}, object(), reference,
                                            "/unused", {}, 5, False, client=client)
        self.assertLess(time.perf_counter() - started, 3.5)
        self.assertEqual(trial["status"], ev.STATUS_GENERATION_ERROR)
        self.assertTrue(trial["generation_timed_out"])
        self.assertFalse(trial["result_correct"])
        self.assertFalse(trial["executed"])


class TrialLogTests(unittest.TestCase):
    def write(self, directory, text):
        path = os.path.join(directory, "trials.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def trial(self, question_id, repeat=1, llm="m", emb="e"):
        return {"llm": llm, "embedding": emb, "question_id": question_id, "repeat": repeat}

    def test_reads_complete_log(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "\n".join(json.dumps(self.trial(i)) for i in (1, 2)) + "\n")
            trials, tail = resilience.read_trials(path)
            self.assertEqual((len(trials), tail), (2, None))

    def test_missing_file_means_nothing_done(self):
        self.assertEqual(resilience.read_trials("/nonexistent/trials.jsonl"), ([], None))

    def test_truncated_last_line_is_quarantined_not_lost(self):
        with tempfile.TemporaryDirectory() as d:
            good = json.dumps(self.trial(1))
            path = self.write(d, good + "\n" + '{"llm": "m", "embedding": "e", "quest')
            trials, tail = resilience.read_trials(path)
            self.assertEqual(len(trials), 1)
            self.assertTrue(tail.startswith('{"llm"'))
            moved = resilience.quarantine_truncated_tail(path, tail)
            with open(moved) as handle:
                self.assertEqual(handle.read().strip(), tail)
            with open(path) as handle:
                self.assertEqual(handle.read(), good + "\n")
            self.assertEqual(resilience.read_trials(path)[1], None)

    def test_corruption_in_the_middle_refuses_to_continue(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, json.dumps(self.trial(1)) + "\nNOT JSON\n" + json.dumps(self.trial(2)) + "\n")
            with self.assertRaises(BlockedError):
                resilience.read_trials(path)

    def test_pending_keys_skip_done_and_keep_order(self):
        configs = [{"llm": "a", "embedding": "x"}, {"llm": "b", "embedding": "x"}]
        done = {("a", "x", 1, 1), ("a", "x", 2, 2)}
        keys = resilience.pending_keys(configs, [1, 2], 2, done)
        self.assertEqual(keys, [("a", "x", 1, 2), ("a", "x", 2, 1),
                                ("b", "x", 1, 1), ("b", "x", 1, 2), ("b", "x", 2, 1), ("b", "x", 2, 2)])
        self.assertEqual(resilience.pending_keys(configs, [1, 2], 2, set(keys) | done), [])


class ResumeSafetyTests(unittest.TestCase):
    def meta(self):
        return {
            "files": {"questions": {"sha256": "q"}, "annotations": None, "llm_engine_py": "l",
                      "query_executor_py": "e", "chroma_rag_py": "c"},
            "generation": {"temperature": 0.0, "seed": 42, "timeout_s": 300},
            "retrieval": {"top_k": 5}, "repeats": 3, "question_ids": [1, 2],
            "configs": [{"llm": "m", "embedding": "e"}], "models": {"m": {"digest": "d1"}, "e": {"digest": "d2"}},
        }

    def test_identical_setup_may_resume(self):
        self.assertEqual(resilience.resume_mismatches(self.meta(), self.meta()), [])

    def test_every_relevant_change_blocks_resume(self):
        def changed(mutate):
            new = self.meta()
            mutate(new)
            return resilience.resume_mismatches(self.meta(), new)

        self.assertTrue(changed(lambda m: m["models"]["m"].update(digest="other")))
        self.assertTrue(changed(lambda m: m["files"]["questions"].update(sha256="x")))
        self.assertTrue(changed(lambda m: m["files"].update(llm_engine_py="x")))
        self.assertTrue(changed(lambda m: m["generation"].update(seed=1)))
        self.assertTrue(changed(lambda m: m["generation"].update(timeout_s=10)))
        self.assertTrue(changed(lambda m: m["retrieval"].update(top_k=3)))
        self.assertTrue(changed(lambda m: m.update(repeats=5)))
        self.assertTrue(changed(lambda m: m.update(question_ids=[1])))
        self.assertTrue(changed(lambda m: m.update(configs=[])))

    def test_changed_database_blocks_resume(self):
        base = {"schema_sha256": "s", "tables": {"orders": {"rows": 326, "checksum": 1}}}
        self.assertEqual(resilience.dataset_mismatches(base, json.loads(json.dumps(base))), [])
        edited = json.loads(json.dumps(base))
        edited["tables"]["orders"]["checksum"] = 2
        self.assertTrue(resilience.dataset_mismatches(base, edited))
        self.assertTrue(resilience.dataset_mismatches(base, {**base, "schema_sha256": "t"}))

    def test_run_directory_cannot_be_used_by_two_processes(self):
        with tempfile.TemporaryDirectory() as d:
            first = resilience.acquire_lock(d)
            with self.assertRaises(BlockedError):
                resilience.acquire_lock(d)
            first.close()
            resilience.acquire_lock(d).close()

    def test_atomic_save_replaces_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.json")
            resilience.save_json_atomic(path, {"a": 1})
            resilience.save_json_atomic(path, {"a": 2})
            with open(path) as handle:
                self.assertEqual(json.load(handle), {"a": 2})
            self.assertEqual(os.listdir(d), ["m.json"])

    def test_old_run_directories_without_settings_cannot_resume(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "run_metadata.json"), "w") as handle:
                json.dump({"started_utc": "x"}, handle)
            self.assertEqual(run_benchmark.main(["--resume", d]), run_benchmark.EXIT_BLOCKED_AT_START)
            self.assertEqual(run_benchmark.main(["--resume", os.path.join(d, "missing")]),
                             run_benchmark.EXIT_BLOCKED_AT_START)

    def test_resume_needs_no_other_arguments_but_normal_runs_do(self):
        self.assertEqual(run_benchmark.parse_args(["--resume", "x"]).resume, "x")
        with self.assertRaises(SystemExit):
            run_benchmark.parse_args([])


if __name__ == "__main__":
    unittest.main()
