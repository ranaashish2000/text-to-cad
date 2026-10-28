# Setup and troubleshooting

Read this for first-time setup, a missing command, or a version/connection error.
`cadgen` means the versioned CLI defined in this skill's `SKILL.md`; `python` means
an actual project interpreter, not cadgen's bootstrap environment.

## Install or find the CLI

Check `uv --version`. If uv is missing, follow its
[platform installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
The plugin's MCP startup normally downloads cadgen and registers the CLI. For
skills-only use, or if the command is missing or its release is not registered, run:

```bash
uvx --no-config --managed-python --python 3.13 --from cadgen==0.8.2 cadgen doctor
```

This downloads the pinned runtime, registers it, writes the CLI proxy and checks
the CAD kernel. It does not start an engine. Run outside a sandbox that refuses
its downloads or writes, or with that sandbox's permission.

Use the explicit CLI path so an activated project venv cannot shadow it:

- macOS/Linux: `~/.local/bin/cadgen +0.8.2`.
- Windows: `%USERPROFILE%\.local\bin\cadgen.cmd +0.8.2` (PowerShell: `$env:USERPROFILE`).
- If `CADGEN_LAUNCHER_DIR` moved the proxy, use `cadgen` or `cadgen.cmd` in that directory.

Keep that explicit version selection when appending verbs. `cadgen doctor <skill-dir>` checks
this skill's pin and the kernel and reports the selected engine. Each command's
`--help` lists its arguments.

## Project Python, when running scripts

Keep an existing project environment and its dependency manager. For a new
project, create a venv, for example `python -m venv .venv`. Use its interpreter
(`.venv/bin/python`, or `.venv\Scripts\python.exe` on Windows) wherever the skill
says `python`. Install the matching cadgen release and the project's dependencies
there; installing the CLI above does not install anything in a project venv:

```bash
python -m pip install cadgen==0.8.2
```

If that interpreter has no pip, use
`uv pip install --python <project-python> cadgen==0.8.2` instead. Keep the matching
requirement in the project's dependency declaration when it has one. Run scripts
with this Python, for example `python model.py`; the selected engine starts warm
workers using that interpreter and its dependencies.

XML validation through `cadgen urdf|sdf|srdf validate` needs only the installed CLI,
not a project venv or running engine. Start an engine for viewing or snapshots.

## Engine startup and selection

The CAD app's MCP server starts or attaches to the engine and advertises its
release and port at initialization. Use those values in the project environment.
Without MCP, start the engine explicitly and keep it running:

```bash
uvx --no-config --managed-python --python 3.13 --from cadgen==0.8.2 cadgen server
```

With the CLI already registered, `cadgen server` does the same job. Run the server
outside a sandbox that cannot bind its listener.

Clients select port 3245 by default; set `CADGEN_PORT` in both the server
and client environments to use another port (`cadgen server --port N` also selects it).
For GUI hosts, set it in the CAD MCP server's `env` settings too; a terminal export
alone does not configure the GUI server. Client, server and project worker cadgen
releases must match exactly. Bootstrap reuses a compatible selected engine and
fails on a conflicting listener. It never changes ports or stops another engine.
Ordinary Python calls and CLI document operations only attach; a missing engine
or version mismatch fails, with no automatic startup or local fallback.

## Diagnose a failure

For version or connection conflicts, use the actual project interpreter and this
skill's directory:

```bash
cadgen doctor --json --python <project-python> <skill-dir>
```

Omit `--python` when there is no project interpreter, or omit `--json` for a text
project report. These diagnostic modes do not register, prune, start or stop
anything. They compare the CLI, skill, project and selected engine, and report
registered installations and discoverable provider configuration. Discovery is
bounded; cached plugins do not prove what an agent host has loaded. Use the
findings to align releases or explicitly configure separate ports. Do not
silently switch releases or stop another provider's engine.

For installation or kernel-load problems, plain `cadgen doctor <skill-dir>` also
performs setup and checks the CAD kernel in a separate process.

A first launch downloads the runtime and may outlast the host's startup timeout.
The Codex plugin requests 300 seconds; Claude Code's `MCP_TIMEOUT` is in
milliseconds. Pre-download with the bootstrap doctor command, then restart the
host. If a command reports that the system denies the engine's folder, rerun it
with the sandbox's permission rather than repeating the refused call.

A model build cut off by the shell's timeout keeps running in the engine.
`cadgen status` reports its workers and documents; rerunning the same model call
waits for that build and replays it. Give a cold build a suitable shell timeout
or run it in the background. Stop only an engine you started.
