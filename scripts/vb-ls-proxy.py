#!/usr/bin/env python3
"""
LSP proxy for vb-ls that:
1. Filters window/logMessage notifications before the initialize response.
2. Stubs unsupported client methods so vb-ls doesn't abort project loading.
3. Defers text-document requests until the solution finishes loading,
   without blocking other client messages (deadlock-safe).
"""
import datetime
import json
import os
import subprocess
import sys
import threading
import re
import urllib.parse


LOG_PATH = os.path.join(os.path.expanduser("~"), ".claude", "logs", "vb-ls.log")

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "appsettings.json")

def _load_settings():
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid appsettings.json: {e}") from e

_settings = _load_settings()

MSBUILD_CONFIGURATION = _settings.get("msbuild_configuration", "Debug")
MSBUILD_PLATFORM = _settings.get("msbuild_platform", "x64")
# CWD for vb-ls: set to the project directory so it auto-discovers the .vbproj
# without needing a --solution argument.
VB_PROJECT_DIR = _settings.get("vb_project_dir", "")
VB_LS_EXE = os.path.expanduser(_settings.get("vb_ls_exe", ""))

# Methods the client doesn't support — proxy responds with success.
STUB_METHODS = {"client/registerCapability", "window/workDoneProgress/create"}

# Request methods that require the solution to be loaded before forwarding.
DEFERRED_METHODS = {
    "textDocument/documentSymbol",
    "textDocument/hover",
    "textDocument/definition",
    "textDocument/references",
    "textDocument/implementation",
    "textDocument/prepareCallHierarchy",
    "callHierarchy/incomingCalls",
    "callHierarchy/outgoingCalls",
}


_URI_RE = re.compile(r'(file://[^"\\]+)')

def decode_uris(msg_bytes):
    """Decode percent-encoded characters in file:// URIs within an LSP message."""
    try:
        text = msg_bytes.decode("utf-8")
        decoded = _URI_RE.sub(lambda m: urllib.parse.unquote(m.group(1)), text)
        if decoded != text:
            return decoded.encode("utf-8")
    except Exception:
        pass
    return msg_bytes


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def read_lsp_message(stream):
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.rstrip(b"\r\n")
        if not line:
            break
        if b":" in line:
            key, _, value = line.partition(b":")
            headers[key.strip().lower()] = value.strip()
    length = headers.get(b"content-length")
    if length is None:
        return None
    return stream.read(int(length))


def write_lsp_message(stream, content):
    stream.write(f"Content-Length: {len(content)}\r\n\r\n".encode("ascii") + content)
    stream.flush()

def main():
    args = [VB_LS_EXE] + sys.argv[1:]
    log(f"[vb-ls-proxy] Starting: {' '.join(args)}")
    log(f"[vb-ls-proxy] CWD: {VB_PROJECT_DIR}")
    log(f"[vb-ls-proxy] MSBuild: Configuration={MSBUILD_CONFIGURATION}, Platform={MSBUILD_PLATFORM}")

    env = os.environ.copy()
    env["Configuration"] = MSBUILD_CONFIGURATION
    env["Platform"] = MSBUILD_PLATFORM

    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=VB_PROJECT_DIR,
    )

    initialized = threading.Event()
    solution_loaded = threading.Event()
    loading_tokens = set()
    loading_lock = threading.Lock()
    stdin_lock = threading.Lock()  # serialize writes to proc.stdin

    def vb_write(msg):
        with stdin_lock:
            write_lsp_message(proc.stdin, msg)

    def pipe_stderr():
        for line in proc.stderr:
            log(f"[vb-ls stderr] {line.decode('utf-8', errors='replace').rstrip()}")

    def pipe_stdin():
        try:
            while True:
                msg = read_lsp_message(sys.stdin.buffer)
                if msg is None:
                    break

                method = ""
                try:
                    obj = json.loads(msg)
                    method = obj.get("method", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

                if method in DEFERRED_METHODS:
                    # Spawn a thread so the main stdin loop stays unblocked.
                    def deferred_send(m=msg, meth=method):
                        log(f"[vb-ls-proxy] deferring {meth}, waiting for solution load")
                        loaded = solution_loaded.wait(timeout=180)
                        if loaded:
                            log(f"[vb-ls-proxy] forwarding deferred {meth}")
                        else:
                            log(f"[vb-ls-proxy] timeout waiting for solution, forwarding {meth} anyway")
                        # NOTE: decode_uris disabled — keeping %20 encoding so vb-ls URI
                        # lookup matches its MSBuildWorkspace-derived paths.
                        log(f"[vb-ls-proxy -> vb-ls] {m[:500]}")
                        vb_write(m)
                    threading.Thread(target=deferred_send, daemon=True).start()
                else:
                    # NOTE: decode_uris disabled — keeping %20 encoding.
                    log(f"[vb-ls-proxy -> vb-ls] {msg[:500]}")
                    vb_write(msg)
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    def pipe_stdout():
        try:
            while True:
                msg = read_lsp_message(proc.stdout)
                if msg is None:
                    break

                try:
                    obj = json.loads(msg)
                except (json.JSONDecodeError, AttributeError):
                    obj = {}

                # Filter pre-init log noise.
                if not initialized.is_set():
                    if obj.get("method") == "window/logMessage":
                        log(f"[vb-ls-proxy filtered pre-init] {msg[:500]}")
                        continue
                    if "id" in obj and ("result" in obj or "error" in obj):
                        initialized.set()
                        log("[vb-ls-proxy] initialized")

                method = obj.get("method", "")
                msg_id = obj.get("id")

                # Stub unsupported client methods; capture loading tokens.
                if method in STUB_METHODS and msg_id is not None:
                    if method == "window/workDoneProgress/create":
                        token = obj.get("params", {}).get("token")
                        if token is not None:
                            with loading_lock:
                                loading_tokens.add(token)
                            log(f"[vb-ls-proxy] tracking progress token: {token}")
                    stub = json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": None}).encode("utf-8")
                    log(f"[vb-ls-proxy stubbed {method}] id={msg_id}")
                    vb_write(stub)
                    continue

                # Detect solution load completion via $/progress end.
                if method == "$/progress" and not solution_loaded.is_set():
                    params = obj.get("params", {})
                    token = params.get("token")
                    value = params.get("value", {})
                    with loading_lock:
                        is_tracked = token in loading_tokens
                    if is_tracked and value.get("kind") == "end":
                        end_msg = value.get("message", "")
                        if "could not be loaded" in end_msg:
                            log(f"[vb-ls-proxy] solution load FAILED: {end_msg[:300]}")
                        else:
                            log(f"[vb-ls-proxy] solution load complete")
                        solution_loaded.set()

                log(f"[vb-ls -> client] {msg[:500]}")
                write_lsp_message(sys.stdout.buffer, msg)
        except BrokenPipeError:
            pass

    threading.Thread(target=pipe_stderr, daemon=True).start()
    threading.Thread(target=pipe_stdin, daemon=True).start()
    pipe_stdout()
    proc.wait()
    log(f"[vb-ls-proxy] vb-ls exited with code {proc.returncode}")


if __name__ == "__main__":
    main()
