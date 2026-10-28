"""The Python environment a model asks the shared engine to execute in.

Executable paths stay lexical: resolving a venv's Python symlink would silently run its base
interpreter. Installed dependencies are captured once at worker startup for saved build reuse;
installing or upgrading them requires an explicit engine restart. Project Python source is
reloaded and keyed by the build loader, independently of the environment's identity.
"""
from __future__ import annotations

import functools
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

STARTUP = ("PYTHONPATH", "PYTHONHOME", "PYTHONSAFEPATH", "PYTHONNOUSERSITE", "PYTHONUSERBASE", "PYTHONPLATLIBDIR")
_METADATA = ("METADATA", "RECORD", "direct_url.json")
_LEGACY_METADATA = ("PKG-INFO", "SOURCES.txt", "installed-files.txt", "requires.txt",
                    "entry_points.txt", "top_level.txt", "dependency_links.txt")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _stamp(path: Path | str) -> list[Any]:
    path = Path(path)
    try:
        found = path.stat()
        content = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return [str(path), None]
    return [str(path), found.st_dev, found.st_ino, content]


@functools.cache
def dependencies() -> str:
    """One process-local installation snapshot, taken before a worker imports its kernel.

    Only build-closure reuse needs this snapshot; client identity and requests never inspect
    package inventories. Install or upgrade dependencies, then restart the selected engine.
    """
    from cadgen.engine.previous import version

    rows = [["cadgen", version(), str(Path(__file__).resolve().parent.parent)]]
    seen = set()
    for dist in metadata.distributions():
        location = getattr(dist, "_path", None)
        if location is None:
            rows.append(["installation", str(dist.locate_file("")),
                         [[entry, dist.read_text(entry)] for entry in _METADATA]])
            continue
        path = Path(location).resolve()
        token = str(path)
        if token in seen:
            continue
        seen.add(token)
        inventory = _LEGACY_METADATA if token.endswith(".egg-info") else _METADATA
        installed = [_stamp(path)] if path.is_file() else [_stamp(path / entry) for entry in inventory]
        rows.append(["installation", token, installed])
    return digest(sorted(rows))


def compatibility() -> dict[str, Any]:
    """Native document interchange is independent of cadgen's Python release."""
    from cadgen.engine.previous import FORMAT

    ocp = None
    spec = importlib.util.find_spec("OCP")
    origin = Path(spec.origin).resolve() if spec is not None and spec.origin else None
    for provider in ("cadquery-ocp", "cadquery-ocp-novtk"):
        try:
            dist = metadata.distribution(provider)
            if origin == Path(dist.locate_file("OCP/__init__.py")).resolve():
                ocp = dist.version
                break
        except metadata.PackageNotFoundError:
            pass
    return {"format": FORMAT, "system": platform.system(), "arch": platform.machine(), "OCP": ocp}


def check_native(found: Any) -> None:
    """Refuse native bytes before reading them, including a kernel already loaded in this client."""
    if found != compatibility():
        raise ValueError("the Python client and document have incompatible native kernel/document formats")
    loaded = sys.modules.get("OCP")
    if loaded is not None:
        actual, installed = getattr(loaded, "__version__", None), found.get("OCP")
        if not isinstance(actual, str) or not isinstance(installed, str) or not (
                installed == actual or installed.startswith(actual + ".")):
            raise ValueError("the imported native kernel differs from the installed OCP distribution")



def describe(environment: dict[str, str] | None = None) -> dict[str, Any]:
    from cadgen.engine.previous import version

    env = os.environ if environment is None else environment
    path = env.get("PYTHONPATH")
    startup = {name: env.get(name, "") for name in STARTUP}
    if path is not None:
        startup["PYTHONPATH"] = os.pathsep.join(os.path.abspath(item or os.curdir) for item in path.split(os.pathsep))
    found = {"python": os.path.abspath(sys.executable), "prefix": os.path.abspath(sys.prefix),
             "root": str(Path(__file__).resolve().parents[2]), "release": version(),
             "checkout": (Path(__file__).resolve().parents[3] / "pyproject.toml").is_file(),
             "startup": startup, "compatibility": compatibility()}
    found["fingerprint"] = digest(found)
    return found


def validate(found: Any, *, server: bool = True) -> dict[str, Any]:
    from cadgen.engine.previous import version

    if not isinstance(found, dict) or set(found) != {
            "python", "prefix", "root", "release", "checkout", "startup", "compatibility", "fingerprint"}:
        raise ValueError("a build requires its Python execution environment")
    if any(not isinstance(found[name], str) or not os.path.isabs(found[name]) for name in ("python", "prefix", "root")):
        raise ValueError("the model's Python, prefix and cadgen import root must be absolute paths")
    if found["release"] != version():
        raise ValueError(f"model Python runs cadgen {found['release']}; server runs cadgen {version()}: install the same release")
    if found["fingerprint"] != digest({name: value for name, value in found.items() if name != "fingerprint"}):
        raise ValueError("the model's Python execution fingerprint is invalid")
    if server and found["compatibility"] != compatibility():
        raise ValueError("the model's Python and server have incompatible native kernel/document formats")
    return found


def key(script: str, execution: dict[str, Any] | None) -> str:
    """One slot per script/interpreter; the pool replaces changed startup environments."""
    identity = {name: execution[name] for name in ("python", "prefix", "root")} if execution is not None else None
    return os.path.normcase(script) + ("::" + digest(identity) if identity is not None else "")



def closure() -> dict[str, Any]:
    """The worker's startup snapshot, also captured once for explicit offline builds."""
    return {"dependencies": dependencies(), "python": platform.python_version()}
