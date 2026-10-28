"""Explicit server startup, private port selection, release identity and persistent credentials."""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cadgen.engine import client, daemon, previous, registry


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class EnginePortTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='cadgen-port-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.port = free_port()
        patch = mock.patch.dict(os.environ, {
            'CADGEN_ENGINE_DIR': str(self.root / 'engine'), 'CADGEN_STATE_DIR': str(self.root / 'state'),
            'CADGEN_CACHE_DIR': str(self.root / 'cache'), 'CADGEN_PORT': str(self.port),
            'CADGEN_LAUNCHER_DIR': str(self.root / 'bin'),
            'CADGEN_ENGINE_STANDBY': '0', 'CADGEN_VIEWER_WARM': '0',
        })
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.stop)

    def stop(self):
        answer = client.request({'op': 'stop'}, start=False)
        if answer:
            self.assertTrue(client.wait_gone())

    def start(self):
        connection = client.connect(start=True)
        self.assertIsNotNone(connection)
        connection.close()
        return client.request({'op': 'status'})

    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, '-m', 'cadgen.cli', *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_missing_server_is_actionable_and_normal_requests_never_start_it(self):
        with mock.patch.object(client, '_start') as start:
            with self.assertRaisesRegex(ConnectionError, f'cadgen server --port {self.port}'):
                client.request({'op': 'pid'})
            self.assertIsNone(client.connect())
            self.assertIsNone(client.hold())
            self.assertIsNone(client.request({'op': 'status'}, start=False))
            start.assert_not_called()
        self.assertFalse(daemon.address().exists())
        viewer = self.cli('viewer', '--api-only', '--json')
        self.assertNotEqual(viewer.returncode, 0)
        self.assertIn('no cadgen server', viewer.stderr)
        self.assertFalse(daemon.address().exists())

    def test_door_is_selected_by_port_not_python_installation_or_release_label(self):
        before = daemon.address()
        with mock.patch.object(sys, 'prefix', '/different/python'), \
                mock.patch.dict(os.environ, {'CADGEN_INSTALLATION_VERSION': '99.0.0'}):
            self.assertEqual(daemon.address(), before)
        with mock.patch.dict(os.environ, {'CADGEN_PORT': str(self.port + 1 if self.port < 65535 else self.port - 1)}):
            self.assertNotEqual(daemon.address(), before)
        with mock.patch.dict(os.environ, {'CADGEN_ENGINE_DIR': str(self.root / 'other')}):
            self.assertNotEqual(daemon.address(), before)
        with mock.patch.dict(os.environ, {'CADGEN_PORT': ''}):
            self.assertEqual(daemon.selected_port(), 3245)
        for bad in ('0', '-1', '65536', 'abc'):
            with self.subTest(port=bad), mock.patch.dict(os.environ, {'CADGEN_PORT': bad}):
                with self.assertRaisesRegex(ValueError, 'between 1 and 65535'):
                    daemon.address()

    def test_diagnostics_probe_only_hello_and_preserve_the_owned_engine(self):
        from cadgen._internal import diagnostics

        first = self.start()
        found = diagnostics.engine(self.port)
        self.assertEqual(found['status'], 'compatible', found)
        self.assertEqual(found['release'], previous.version())
        with mock.patch.object(previous, 'version', return_value='99.0.0'):
            refused = diagnostics._probe_port(self.port)
        self.assertEqual(refused['status'], 'incompatible', refused)
        self.assertEqual(refused['release'], previous.version())
        self.assertEqual(refused['port'], self.port)
        self.assertNotIn('instance', refused)
        self.assertEqual(client.request({'op': 'pid'})['pid'], first['pid'])

    def test_start_binds_immediately_and_compatible_bootstrap_reuses_it(self):
        first = self.start()
        self.assertEqual(first['serving']['port'], self.port)
        with socket.create_connection(('127.0.0.1', self.port), timeout=5):
            pass
        if os.name != 'nt':
            with mock.patch.object(socket, 'create_connection', side_effect=PermissionError('sandbox blocks sockets')):
                self.assertEqual(client.request({'op': 'pid'})['pid'], first['pid'])
        with mock.patch.object(client, '_start') as start:
            with client.connect(start=True):
                pass
            with client.hold(start=True):
                self.assertEqual(client.request({'op': 'status'})['holders'], 1)
            start.assert_not_called()
        server = self.cli('server', '--port', str(self.port))
        self.assertEqual(server.returncode, 0, server.stderr)
        self.assertEqual(client.request({'op': 'pid'})['pid'], first['pid'])
        viewer = self.cli('viewer', '--api-only', '--json', '--port', str(self.port))
        self.assertEqual(viewer.returncode, 0, viewer.stderr)
        self.assertEqual(json.loads(viewer.stdout)['port'], self.port)
        self.assertEqual(self.cli('server', '--port', '0').returncode, 2)

    def test_exact_release_is_checked_and_a_label_cannot_override_it(self):
        first = self.start()
        with mock.patch.dict(os.environ, {'CADGEN_INSTALLATION_VERSION': '99.0.0'}):
            self.assertEqual(client.request({'op': 'pid'})['pid'], first['pid'])
        major, minor, patch = previous.version().split('.')
        other = f'{major}.{minor}.{int(patch) + 1}'
        with mock.patch.object(previous, 'version', return_value=other), mock.patch.object(client, '_start') as start:
            with self.assertRaisesRegex(ConnectionError, 'exactly the same cadgen release'):
                client.connect(start=True)
            start.assert_not_called()
        self.assertEqual(client.request({'op': 'pid'})['pid'], first['pid'])

    def test_two_explicit_ports_are_independent_and_neither_retires_the_other(self):
        from cadgen.viewer.engine_client import EngineClient

        first = self.start()
        http = EngineClient()
        self.assertEqual(http.connect()['port'], self.port)
        other = free_port()
        with mock.patch.dict(os.environ, {'CADGEN_PORT': str(other), 'CADGEN_INSTALLATION_VERSION': '99.0.0'}):
            try:
                second = self.start()
                self.assertNotEqual(second['pid'], first['pid'])
                self.assertEqual(second['serving']['port'], other)
                self.assertEqual(http.connect()['port'], other)
            finally:
                self.stop()
        self.assertEqual(client.request({'op': 'pid'})['pid'], first['pid'])
        self.assertEqual(http.connect()['port'], self.port)

    def test_an_occupied_port_fails_without_rolling_or_stopping_its_owner(self):
        with socket.socket() as held:
            held.bind(('127.0.0.1', self.port))
            held.listen(5)
            result = self.cli('server')
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(f'port {self.port} is held', result.stderr)
            self.assertFalse(daemon.address().exists())
            from cadgen._internal import diagnostics
            self.assertEqual(diagnostics.engine(self.port)['status'], 'conflicting_listener')
            with socket.create_connection(('127.0.0.1', self.port), timeout=5):
                pass

    def test_ports_share_the_latest_saved_document_across_restarts_and_delete(self):
        import build123d as bd
        from cadgen.engine import api

        self.start()
        first = api.hand_over(bd.Box(1, 1, 1), 'port_persistence')
        second_port = free_port()
        with mock.patch.dict(os.environ, {'CADGEN_PORT': str(second_port)}):
            try:
                self.start()
                second = api.hand_over(bd.Box(2, 2, 2), 'port_persistence')
                self.assertEqual(first.id, second.id)
            finally:
                self.stop()
        self.stop()
        self.start()
        restored = api.Document(first.name, first.script, first.report, first.cwd)
        self.assertAlmostEqual(restored.shape.volume, 8)
        with mock.patch.dict(os.environ, {'CADGEN_PORT': str(second_port)}):
            try:
                self.start()
                restored = api.Document(second.name, second.script, second.report, second.cwd)
                self.assertAlmostEqual(restored.shape.volume, 8)
            finally:
                self.stop()
        api.delete([first.id])
        self.assertIsNone(registry.current().get(first.id))
        with mock.patch.dict(os.environ, {'CADGEN_PORT': str(second_port)}):
            try:
                self.start()
                self.assertIsNone(registry.current().get(second.id))
                self.assertNotIn(second.id, [row['id'] for row in client.request({'op': 'status'})['models']])
                self.assertFalse(registry.current().cache.path(f'documents/{second.id}/document.xbf').exists())
            finally:
                self.stop()

    def test_token_survives_restart_and_rotation_requires_stopping_servers(self):
        self.start()
        first = client.request({'op': 'http'})['token']
        refused = self.cli('server', '--rotate-token')
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(client.request({'op': 'http'})['token'], first)
        self.stop()
        self.start()
        self.assertEqual(client.request({'op': 'http'})['token'], first)
        self.stop()
        daemon.rotate_token()
        self.start()
        self.assertNotEqual(client.request({'op': 'http'})['token'], first)

    @unittest.skipIf(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0), 'POSIX file permissions')
    def test_unreadable_token_is_never_replaced(self):
        held = registry.current()
        held.put_token('t' * 43)
        path = self.root / 'state' / 'engines' / 'token'
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        with self.assertRaisesRegex(ValueError, 'page token.*cannot be read'):
            daemon.bind('127.0.0.1', self.port, lambda *_: self.fail('must not bind'))
        path.chmod(0o600)
        self.assertEqual(path.read_text(encoding='utf-8').strip(), 't' * 43)


class HandshakeTest(unittest.TestCase):
    def test_pythonpath_empty_entries_are_the_callers_current_folder(self):
        cwd = os.getcwd()
        for path in ('', os.pathsep.join(('relative', '', '/absolute'))):
            with self.subTest(path=path), mock.patch.dict(os.environ, {'PYTHONPATH': path}):
                self.assertEqual(client.environment()['PYTHONPATH'], os.pathsep.join(
                    item if os.path.isabs(item) else os.path.join(cwd, item) for item in path.split(os.pathsep)))

    def test_no_work_runs_before_release_and_instance_are_verified(self):
        engine = SimpleNamespace(token='a' * 32, release_version='0.8.0', port=45001, instance='instance-one',
                                 unsaid='', refusal=None, handle=mock.Mock())
        identity = {'protocol': daemon.PROTOCOL, 'release': engine.release_version, 'port': engine.port,
                    'instance': engine.instance}
        hello = {'op': 'hello', **{k: v for k, v in identity.items() if k != 'instance'}}
        for windows in (False, True):
            def asked(*messages):
                out = io.BytesIO()
                data = ''.join(json.dumps({'token': engine.token, **message}) + '\n' for message in messages)
                with mock.patch.object(daemon, 'WINDOWS', windows):
                    daemon._serve(engine, io.BytesIO(data.encode()), out)
                return [json.loads(row) for row in out.getvalue().splitlines()]

            self.assertIn('handshake', asked({'op': 'build'})[0]['failed'])
            refused = asked({**hello, 'release': '0.8.1'}, {'op': 'build'})
            self.assertIn('mismatch', refused[0]['failed'])
            self.assertEqual({key: refused[0][key] for key in ('protocol', 'release', 'port')},
                             {key: identity[key] for key in ('protocol', 'release', 'port')})
            self.assertNotIn('instance', refused[0])
            self.assertEqual(len(refused), 1, 'a refused hello never admits the queued work')
            self.assertIn('instance', asked(hello, {'op': 'build', 'engineIdentity': {**identity, 'instance': 'old'}})[1]['failed'])
            engine.handle.assert_not_called()
            asked(hello, {'op': 'build', 'engineIdentity': identity})
            engine.handle.assert_called_once()
            engine.handle.reset_mock()


if __name__ == '__main__':
    unittest.main()
