"""Delivery coordination on disposable SQLite only; no app, mail or network."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mail_delivery_gate as gate
from auth_security import fingerprint
from database import DatabaseUnavailable


class MailDeliveryGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / 'delivery.sqlite3')
        self.wall, self.elapsed, self.sleeps = [1000.0], [0.0], []
        def sleep(seconds):
            self.sleeps.append(seconds)
            self.wall[0] += seconds
            self.elapsed[0] += seconds
        clock = SimpleNamespace(time=lambda: self.wall[0], monotonic=lambda: self.elapsed[0], sleep=sleep)
        timer = patch.object(gate, 'time', clock)
        timer.start()
        self.addCleanup(timer.stop)
        self.secret, self.public_key = 'isolated-signing-secret', 'isolated-public-key'
        self.key = fingerprint(self.secret, 'mail-delivery', 'emailjs', self.public_key)
        with closing(self.get_db()) as conn:
            conn.execute('CREATE TABLE auth_limits (bucket TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires INTEGER NOT NULL)')
            conn.execute('CREATE INDEX idx_auth_limits_expires ON auth_limits(expires)')
            conn.commit()

    def get_db(self):
        conn = sqlite3.connect(self.path, timeout=1)
        conn.row_factory = sqlite3.Row
        return conn

    def row(self):
        with closing(self.get_db()) as conn:
            row = conn.execute('SELECT * FROM auth_limits WHERE bucket=?', (self.key,)).fetchone()
            return dict(row) if row else None

    def test_concurrent_connections_acquire_only_one_lease(self):
        barrier = threading.Barrier(8)
        def contender(_):
            barrier.wait(timeout=5)
            return gate.claim_delivery(self.get_db, self.key)
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(contender, range(8)))
        winners = [lease for lease, _ in results if lease is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(sum(lease is None for lease, _ in results), 7)
        self.assertEqual(self.row()['hits'], winners[0])
        self.assertEqual(self.row()['expires'], 1000 + gate.LEASE_SECONDS)
        self.assertNotIn(self.secret, str(self.row()))
        self.assertNotIn(self.public_key, str(self.row()))

    def test_failed_send_still_enforces_cooldown(self):
        self.wall[0] = 1000.75
        with self.assertRaisesRegex(RuntimeError, 'synthetic send failure'):
            with gate.emailjs_gate(self.get_db, self.secret, self.public_key):
                raise RuntimeError('synthetic send failure')
        self.assertEqual(self.row()['hits'], 0)
        self.assertEqual(self.row()['expires'], 1000 + gate.COOLDOWN_SECONDS)
        self.wall[0] = 1001.75
        lease, delay = gate.claim_delivery(self.get_db, self.key)
        self.assertIsNone(lease)
        self.assertAlmostEqual(delay, .25)
        self.wall[0] = 1002.0
        self.assertIsNotNone(gate.claim_delivery(self.get_db, self.key)[0])

    def test_stale_finish_cannot_release_renewed_lease(self):
        with patch.object(gate.secrets, 'randbelow', side_effect=[10, 20]):
            old, _ = gate.claim_delivery(self.get_db, self.key)
            self.wall[0] += gate.LEASE_SECONDS + 1
            new, _ = gate.claim_delivery(self.get_db, self.key)
        self.assertNotEqual(old, new)
        renewed = self.row()
        gate.finish_delivery(self.get_db, self.key, old)
        self.assertEqual(self.row(), renewed)
        self.assertIsNone(gate.claim_delivery(self.get_db, self.key)[0])
        gate.finish_delivery(self.get_db, self.key, new)
        self.assertEqual(self.row()['hits'], 0)

    def test_yield_does_not_hold_a_database_write_transaction(self):
        with gate.emailjs_gate(self.get_db, self.secret, self.public_key):
            with closing(self.get_db()) as other:
                other.execute('BEGIN IMMEDIATE')
                other.execute('INSERT INTO auth_limits VALUES(?,?,?)', ('unrelated-operation', 1, 2000))
                other.commit()
            self.assertIsNone(gate.claim_delivery(self.get_db, self.key)[0])
        self.assertEqual(self.row()['hits'], 0)

    def test_wait_is_bounded_without_sleeping_in_real_time_or_releasing_another_lease(self):
        with closing(self.get_db()) as conn:
            conn.execute('INSERT INTO auth_limits VALUES(?,?,?)', (self.key, 999, 2000))
            conn.commit()
        before = self.row()
        with patch.object(gate, 'MAX_WAIT_SECONDS', 2.5), self.assertRaises(DatabaseUnavailable):
            with gate.emailjs_gate(self.get_db, self.secret, self.public_key):
                self.fail('A busy lease must not yield')
        self.assertGreater(sum(self.sleeps), 0)
        self.assertLessEqual(sum(self.sleeps), 2.5)
        self.assertEqual(self.row(), before)

    def test_capacity_preserves_active_counters_and_expired_rows_free_space(self):
        with closing(self.get_db()) as conn:
            conn.executemany('INSERT INTO auth_limits VALUES(?,?,?)', [('other-one', 8, 2000), ('other-two', 7, 2000)])
            conn.commit()
        with patch.object(gate, 'MAX_COUNTERS', 2):
            with self.assertRaises(DatabaseUnavailable):
                gate.claim_delivery(self.get_db, self.key)
            self.assertIsNone(self.row())
            with closing(self.get_db()) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM auth_limits').fetchone()[0], 2)
            self.wall[0] = 2001.0
            self.assertIsNotNone(gate.claim_delivery(self.get_db, self.key)[0])


if __name__ == '__main__':
    unittest.main()
