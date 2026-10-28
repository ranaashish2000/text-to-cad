"""Caller Python identities, mutable dependencies and shared-server workers across venvs."""
from __future__ import annotations

import io
import json
import os
import subprocess
import socket
import sys
import tempfile
import time
import unittest
import venv
from pathlib import Path
from unittest import mock

from cadgen.engine import runtime
from cadgen.engine.daemon import Pool, Worker
from tests.python.packages.cadgen.engine.test_pool import Fake


class RuntimeTest(unittest.TestCase):
    def test_actual_interpreter_is_lexical_and_not_virtual_env(self):
        prefix = str(Path(tempfile.gettempdir()) / "venv")
        python = str(Path(prefix) / "bin" / "python")
        with mock.patch.object(sys, "executable", python), mock.patch.object(sys, "prefix", prefix), \
                mock.patch.object(runtime.metadata, "distributions", side_effect=AssertionError("client scanned packages")):
            found = runtime.describe({"VIRTUAL_ENV": "/wrong", "PYTHONPATH": os.pathsep.join(("src", ""))})
        self.assertEqual(found["python"], python)
        self.assertEqual(found["prefix"], prefix)
        self.assertNotIn("dependencies", found)
        self.assertEqual(found["root"], str(Path(runtime.__file__).resolve().parents[2]))
        self.assertEqual(found["startup"]["PYTHONPATH"], os.pathsep.join((str(Path.cwd() / "src"), str(Path.cwd()))))

    def test_release_and_native_document_compatibility_are_independent(self):
        found = runtime.describe()
        for field, value, words in (("release", found["release"] + ".1", "same release"),
                                    ("compatibility", {"format": "other"}, "native kernel/document")):
            wrong = {**found, field: value}
            wrong["fingerprint"] = runtime.digest({key: item for key, item in wrong.items() if key != "fingerprint"})
            with self.assertRaisesRegex(ValueError, words):
                runtime.validate(wrong)

    def test_installation_snapshot_is_held_until_restart_without_source_inventory(self):
        from importlib import metadata

        self.addCleanup(runtime.dependencies.cache_clear)
        for inventory in ("modern", "legacy", "single-file-egg"):
            with self.subTest(inventory=inventory), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / "custom.py"
                source.write_text("WIDTH=1\n", encoding="utf-8")
                info = root / ("custom-1.dist-info" if inventory == "modern" else "custom.egg-info")
                if inventory == "single-file-egg":
                    info.write_text("Name: custom\nVersion: 1\nSummary: old\n", encoding="utf-8")
                    metadata_file = info
                else:
                    info.mkdir()
                    metadata_file = info / ("METADATA" if inventory == "modern" else "PKG-INFO")
                    metadata_file.write_text("Name: custom\nVersion: 1\nSummary: old\n", encoding="utf-8")
                dist = metadata.Distribution.at(info)
                runtime.dependencies.cache_clear()
                with mock.patch.object(runtime.metadata, "distributions", return_value=[dist]) as discovered, \
                        mock.patch.object(type(dist), "files", mock.PropertyMock(side_effect=AssertionError("file inventory read"))), \
                        mock.patch.object(runtime.os, "walk", side_effect=AssertionError("source tree scanned")):
                    first = runtime.closure()["dependencies"]
                    source.write_text("WIDTH=2\n", encoding="utf-8")
                    stamp = metadata_file.stat()
                    replacement = metadata_file.with_name(metadata_file.name + ".reinstall")
                    replacement.write_bytes(metadata_file.read_bytes())
                    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    replacement.replace(metadata_file)
                    self.assertEqual(first, runtime.closure()["dependencies"], "a living worker retains its startup snapshot")
                    discovered.assert_called_once()
                    runtime.dependencies.cache_clear()  # a fresh process after explicit restart
                    restarted = runtime.closure()["dependencies"]
                    self.assertNotEqual(first, restarted, "same-version reinstallation invalidates saved build reuse")
                    runtime.dependencies.cache_clear()
                    self.assertEqual(restarted, runtime.closure()["dependencies"], "unchanged restart keeps saved builds reusable")
                    frozen = metadata_file.stat()
                    stat = os.stat
                    metadata_file.write_bytes(metadata_file.read_bytes().replace(b"Summary: old", b"Summary: new"))
                    def unchanged_stat(path, *args, **kwargs):
                        return frozen if path == metadata_file or path == str(metadata_file) else stat(path, *args, **kwargs)
                    with mock.patch.object(runtime.os, "stat", side_effect=unchanged_stat):
                        runtime.dependencies.cache_clear()
                        self.assertNotEqual(restarted, runtime.closure()["dependencies"],
                                            "changed metadata bytes survive completely unchanged stat fields")
                    self.assertEqual(discovered.call_count, 4)

    def test_pool_keeps_project_interpreters_warm(self):
        def start(script, environment, cwd, execution):
            worker = Fake(script, environment, cwd)
            worker.execution = execution
            return worker

        pool = Pool(4, start)
        self.addCleanup(pool.stop)
        first = runtime.describe()
        second = {**first, "prefix": "/other", "fingerprint": "other"}
        a = pool.lease("a.py", {}, execution=first)
        pool.release(a)
        b = pool.lease("a.py", {}, execution=second)
        pool.release(b)
        self.assertIs(pool.lease("a.py", {}, execution=first), a)
        pool.release(a)
        self.assertIs(pool.lease("a.py", {}, execution=second), b)
        pool.release(b)
        self.assertFalse(a.stopped)
        self.assertFalse(b.stopped)
        changed = {**first, "startup": {**first["startup"], "PYTHONPATH": "/changed"}}
        changed["fingerprint"] = runtime.digest({name: value for name, value in changed.items() if name != "fingerprint"})
        replacement = pool.lease("a.py", {"PYTHONPATH": "/changed"}, execution=changed)
        pool.release(replacement)
        self.assertIsNot(replacement, a, "startup changes replace the same interpreter's worker")
        self.assertTrue(a.stopped)
        self.assertFalse(b.stopped, "another project interpreter remains warm")
        self.assertEqual(len(pool.workers()), 2)

    def test_worker_checks_actual_import_before_model_execution_and_never_injects_server_code(self):
        found = runtime.describe()
        found = {**found, "checkout": False}
        found["fingerprint"] = runtime.digest({key: value for key, value in found.items() if key != "fingerprint"})
        other = {**found, "prefix": "/wrong"}
        other["fingerprint"] = runtime.digest({key: value for key, value in other.items() if key != "fingerprint"})
        process = mock.Mock(stdout=io.BytesIO((json.dumps({"ready": 123, "execution": other}) + "\n").encode()),
                            stderr=io.BytesIO(), stdin=io.BytesIO())
        with mock.patch("cadgen.engine.daemon.subprocess.Popen", return_value=process) as launched:
            with self.assertRaisesRegex(ValueError, "actual Python environment"):
                Worker("model.py", {"CADGEN_ENGINE_WORKER": "spoof"}, execution=found)
        self.assertEqual(launched.call_args.args[0][:4], [found["python"], "-P", "-m", "cadgen.engine.worker"])
        self.assertNotIn("PYTHONPATH", launched.call_args.kwargs["env"])
        self.assertEqual(launched.call_args.kwargs["env"]["CADGEN_ENGINE_WORKER"], "1")

    def test_an_interpreter_without_cadgen_fails_without_source_injection(self):
        from cadgen.engine.daemon import WorkerGone

        with tempfile.TemporaryDirectory(prefix="cadgen-missing-python-") as folder:
            root = Path(folder)
            location = root / "venv"
            venv.EnvBuilder(with_pip=False).create(location)
            python = location / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
            env.update(CADGEN_ENGINE_DIR=str(root / "engine"), CADGEN_CACHE_DIR=str(root / "cache"),
                       CADGEN_STATE_DIR=str(root / "state"))
            with mock.patch.dict(os.environ, env, clear=True):
                found = runtime.describe(env)
                found.update(python=str(python), prefix=str(location), checkout=False)
                found["fingerprint"] = runtime.digest({name: value for name, value in found.items() if name != "fingerprint"})
                with self.assertRaisesRegex(WorkerGone, "No module named 'cadgen'"):
                    Worker(str(root / "never-executed.py"), env, cwd=str(root), execution=found)

    def test_native_handover_and_returned_shape_refuse_before_reading_bytes(self):
        from cadgen.engine import api, daemon

        engine = daemon.Daemon(cap=1)
        self.addCleanup(engine.pool.stop)
        execution = runtime.describe()
        wrong = {**execution, "compatibility": {"format": "incompatible"}}
        wrong["fingerprint"] = runtime.digest({name: value for name, value in wrong.items() if name != "fingerprint"})
        answers = []
        with mock.patch.object(engine.models, "hand_over", side_effect=AssertionError("native bytes were read")):
            engine.handle({"op": "shape", "execution": wrong, "xbf": "not-base64"}, answers.append)
        self.assertIn("native kernel/document", answers[-1]["failed"])
        handle = api.Document("cube", Path("cube.py"), {"id": "cube"}, Path.cwd())
        with mock.patch.object(api, "_ask", return_value={"done": {"compatibility": wrong["compatibility"]},
                                                        "payload": "not-base64"}):
            with self.assertRaisesRegex(ValueError, "native kernel/document"):
                handle._transferred()

    def test_restored_shape_uses_server_document_worker_without_pythonpath(self):
        from tests.python.support import engine_daemon

        with tempfile.TemporaryDirectory(prefix="cadgen-restored-shape-") as folder:
            root = Path(folder)
            env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
            env.update(CADGEN_PORT=str(engine_daemon.free_port()), CADGEN_ENGINE_DIR=str(root / "engine"),
                       CADGEN_STATE_DIR=str(root / "state"), CADGEN_CACHE_DIR=str(root / "cache"),
                       CADGEN_ENGINE_STANDBY="0")
            program = """import build123d as bd
from cadgen.engine import api, client
try:
    with client.connect(start=True): pass
    original = api.hand_over(bd.Box(1, 1, 1), 'restored')
    client.request({'op': 'stop'}, start=False)
    assert client.wait_gone()
    with client.connect(start=True): pass
    restored = api.Document(original.name, original.script, original.report, original.cwd)
    assert abs(restored.shape.volume - 1) < 1e-9
finally:
    client.request({'op': 'stop'}, start=False)
    client.wait_gone()
"""
            result = engine_daemon.run([sys.executable, "-c", program], env=env, cwd=Path.cwd())
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_live_result_refuses_changed_installed_kernel_before_serializing(self):
        from types import SimpleNamespace
        from cadgen.engine import build, worker

        current = {**runtime.compatibility(), "OCP": "7.10.0"}
        source = mock.Mock(structure={"prototypes": [{"key": "part"}]})
        source.shape_data.side_effect = AssertionError("old native objects serialized")
        with mock.patch.object(build, "held", return_value=source), \
                mock.patch.object(runtime, "compatibility", return_value=current), \
                mock.patch.dict(sys.modules, {"OCP": SimpleNamespace(__version__="7.9.3.1")}):
            with self.assertRaisesRegex(ValueError, "imported native kernel"):
                worker._result({"script": "model.py", "name": "cube"})
        source.shape_data.assert_not_called()



class CrossVenvTest(unittest.TestCase):
    def test_two_project_venvs_share_server_and_documents_but_keep_their_dependencies(self):
        from cadgen.engine import client
        from tests.python.support import engine_daemon

        with tempfile.TemporaryDirectory(prefix="cadgen-project-python-") as folder:
            root = Path(folder)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            private = {"CADGEN_ENGINE_DIR": str(root / "engine"), "CADGEN_STATE_DIR": str(root / "state"),
                       "CADGEN_CACHE_DIR": str(root / "cache"), "CADGEN_PORT": str(port)}
            env = {**os.environ, **private, "PYTHONPATH": str(Path(runtime.__file__).resolve().parents[2]),
                   "CADGEN_ENGINE_STANDBY": "0", "CADGEN_ENGINE_WORKERS": "4", "CADGEN_ENGINE_WORKER_RSS_MB": "0"}
            server = subprocess.Popen([sys.executable, "-m", "cadgen.engine", "server"], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: server.kill() if server.poll() is None else None)
            with mock.patch.dict(os.environ, env, clear=True):
                deadline = time.monotonic() + 60
                while not client.request({"op": "status"}, start=False):
                    if server.poll() is not None:
                        self.fail(server.stderr.read())
                    if time.monotonic() > deadline:
                        self.fail("private test server did not answer")
                    time.sleep(.01)
                try:
                    project = root / "model.py"
                    helper, editable = root / "helper.py", root / "editable/parts.py"
                    helper.write_text("SCALE=1\n", encoding="utf-8")
                    editable.parent.mkdir()
                    editable.write_text("HEIGHT=1\n", encoding="utf-8")
                    unrelated = root / "unrelated.py"
                    unrelated.write_text("VALUE=1\n", encoding="utf-8")
                    project.write_text("from cadgen import model\nimport json\nfrom helper import SCALE\n"
                                       "from editable_parts import HEIGHT\n@model\ndef cube():\n"
                                       "    import build123d as bd\n    from custom_dependency import WIDTH\n"
                                       "    return bd.Box(WIDTH*SCALE,HEIGHT,1)\n"
                                       "if __name__ == '__main__':\n    r=cube.build()\n"
                                       "    print(json.dumps({'id':r.models[0]['id'],'fresh':r.fresh,"
                                       "'key':r.models[0]['closure']['key'],'cache':r.models[0]['cache'],"
                                       "'bounds':r.models[0].get('delta',{}).get('bounds',{}).get('now')}))\n", encoding="utf-8")
                    interpreters, sites = [], []
                    for index in (1, 2):
                        location = root / f"venv{index}"
                        venv.EnvBuilder(with_pip=False).create(location)
                        python = location / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                        site = Path(subprocess.check_output([str(python), "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"], text=True).strip())
                        # Share immutable heavy CAD wheels without installing/downloading them twice.
                        import sysconfig
                        (site / "cad-test-dependencies.pth").write_text(sysconfig.get_path("purelib") + "\n", encoding="utf-8")
                        (site / "custom_dependency.py").write_text(f"WIDTH={index}\n", encoding="utf-8")
                        info = site / f"custom_dependency-{index}.dist-info"
                        info.mkdir()
                        (info / "METADATA").write_text(f"Name: custom-dependency\nVersion: {index}\n", encoding="utf-8")
                        (info / "RECORD").write_text(f"custom_dependency.py,,\ncustom_dependency-{index}.dist-info/METADATA,,\n", encoding="utf-8")
                        # A real PEP660-style meta-path hook, rather than a source-path .pth.
                        (site / "fixture_editable_hook.py").write_text(
                            "import sys\nfrom importlib.abc import MetaPathFinder\n"
                            "from importlib.util import spec_from_file_location\n"
                            "class Finder(MetaPathFinder):\n"
                            "    def find_spec(self, fullname, path=None, target=None):\n"
                            f"        if fullname == 'editable_parts': return spec_from_file_location(fullname, {str(editable)!r})\n"
                            "def install(): sys.meta_path.append(Finder())\n", encoding="utf-8")
                        (site / "fixture_editable.pth").write_text(
                            "import fixture_editable_hook; fixture_editable_hook.install()\n", encoding="utf-8")
                        installed = site / "editable_parts-1.dist-info"
                        installed.mkdir()
                        (installed / "METADATA").write_text("Name: editable-parts\nVersion: 1\n", encoding="utf-8")
                        (installed / "direct_url.json").write_text(json.dumps({
                            "url": editable.parent.as_uri(), "dir_info": {"editable": True}}), encoding="utf-8")
                        interpreters.append(python)
                        sites.append(site)
                    def build(python):
                        result = engine_daemon.run([str(python), str(project)], env=env, cwd=root)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        return json.loads(result.stdout.splitlines()[-1])
                    def workers():
                        status = client.request({"op": "status"}, start=False)
                        self.assertEqual(status["pid"], server.pid)
                        return {row["pid"] for row in status["workers"]}
                    a = build(interpreters[0])
                    first_workers = workers()
                    b = build(interpreters[1])
                    self.assertEqual(a["id"], b["id"])
                    self.assertNotEqual(a["key"], b["key"])
                    self.assertEqual((a["bounds"], b["bounds"]), ([1, 1, 1], [2, 1, 1]))
                    self.assertTrue(a["fresh"] and b["fresh"])
                    again = build(interpreters[0])
                    self.assertFalse(again["fresh"])
                    self.assertEqual(a["key"], again["key"])
                    self.assertTrue(again["cache"]["used"])
                    warm_workers = workers()
                    # Source belongs to the build loader, not environment identity. Both
                    # ordinary helpers and editable-hook modules reload inside the same worker.
                    for target, text, bounds in ((helper, "SCALE=4\n", [4, 1, 1]),
                                                  (editable, "HEIGHT=2\n", [4, 2, 1])):
                        import py_compile
                        py_compile.compile(str(target), doraise=True)
                        stamp = target.stat()
                        target.write_text(text, encoding="utf-8")
                        os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                        edited = build(interpreters[0])
                        self.assertFalse(edited["fresh"])
                        self.assertEqual(edited["id"], a["id"])
                        self.assertEqual(edited["bounds"], bounds, {"helper": str(target), "report": edited})
                        self.assertEqual(workers(), warm_workers)
                    # Comments and unreachable files do not change the build's semantic key.
                    helper.write_text("# comment only\nSCALE=4\n", encoding="utf-8")
                    unrelated.write_text("VALUE=2\n", encoding="utf-8")
                    reused = build(interpreters[0])
                    self.assertEqual(reused["key"], edited["key"])
                    self.assertTrue(reused["cache"]["used"])
                    self.assertEqual(workers(), warm_workers)
                    def restart():
                        nonlocal server
                        client.request({"op": "stop"}, start=False)
                        server.communicate(timeout=30)
                        server = subprocess.Popen([sys.executable, "-m", "cadgen.engine", "server"], env=env,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                        deadline = time.monotonic() + 60
                        while not client.request({"op": "status"}, start=False):
                            if server.poll() is not None:
                                self.fail(server.stderr.read())
                            if time.monotonic() > deadline:
                                self.fail("restarted private server did not answer")
                            time.sleep(.01)

                    # An unchanged explicit restart still reuses the saved document's build key.
                    restart()
                    restored = build(interpreters[0])
                    self.assertTrue(restored["fresh"])
                    self.assertTrue(restored["cache"]["used"])
                    self.assertEqual(restored["key"], reused["key"])
                    # A managed same-version reinstall takes effect after explicit restart.
                    # Its new worker must not take the previous installation's saved geometry.
                    changed_file = sites[0] / "custom_dependency.py"
                    stamp = changed_file.stat()
                    changed_file.write_text("WIDTH=3\n", encoding="utf-8")
                    os.utime(changed_file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    metadata_file = sites[0] / "custom_dependency-1.dist-info/METADATA"
                    stamp = metadata_file.stat()
                    replacement = metadata_file.with_name(metadata_file.name + ".reinstall")
                    replacement.write_bytes(metadata_file.read_bytes())
                    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    replacement.replace(metadata_file)
                    restart()
                    changed = build(interpreters[0])
                    self.assertTrue(changed["fresh"])
                    self.assertEqual(changed["id"], a["id"])
                    self.assertEqual(changed["bounds"], [12, 2, 1])
                    self.assertFalse(changed["cache"]["used"])
                    self.assertNotEqual(changed["key"], reused["key"])
                    self.assertFalse(warm_workers & workers())
                    self.assertEqual(len(workers()), 1)
                finally:
                    client.request({"op": "stop"}, start=False)
                    server.communicate(timeout=30)
