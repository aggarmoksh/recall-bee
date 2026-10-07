"""Start/stop `bee proxy` and `bee stream` as subprocesses.

Windows pitfall (Phase 0.9): killing the immediate child with terminate()
can orphan the real bee.exe, because the npm shim spawns a child process.
Tree-kill children-then-parent via psutil, always in try/finally.
"""

import logging
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import psutil

from recall.config import settings

logger = logging.getLogger("recall.bee.process")

READY_POLL_TIMEOUT_S = 15
READY_POLL_INTERVAL_S = 0.5
KILL_TIMEOUT_S = 5


def resolve_bee_exe() -> str:
    bee_exe = shutil.which("bee")
    if bee_exe is None:
        raise RuntimeError("could not resolve 'bee' on PATH")
    return bee_exe


def find_bee_processes() -> list[dict]:
    """Orphan check: any process with 'bee' in its name, regardless of who started it."""
    found = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            if "bee" in name:
                found.append(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def warn_on_orphans() -> None:
    orphans = find_bee_processes()
    if orphans:
        logger.warning("found %d pre-existing bee process(es) before starting: %s", len(orphans), orphans)


def kill_tree(pid: int, timeout: float = KILL_TIMEOUT_S) -> None:
    """Terminate children first, then the parent, so nothing gets orphaned.

    Found live during the 2-day check (2026-10-07): a laptop sleep/resume
    can kill the bee.exe process tree out from under us between this
    function being entered and `parent.children(recursive=True)` actually
    running -- psutil.Process.children() internally re-validates the
    parent is still alive and raises NoSuchProcess itself if not, a SECOND
    place this can fail beyond the initial `psutil.Process(pid)` call
    below. The old code only guarded the first one; the second, uncaught,
    propagated out of `stream_events()`'s reconnect `finally` block and
    crashed the entire daemon process -- not just that one reconnect
    attempt, and before the sleep/wake recovery logic in recall/run.py
    ever got a chance to run. A NoSuchProcess anywhere in this function
    just means "already dead, nothing left to clean up here", never a
    reason to let the caller crash."""
    try:
        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    try:
        _, alive = psutil.wait_procs(procs, timeout=timeout)
    except psutil.NoSuchProcess:
        return
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def _popen(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def _wait_for_proxy_ready(port: int, timeout: float = READY_POLL_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/v1/me"
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(READY_POLL_INTERVAL_S)
    return False


class BeeProxy:
    """Context manager around `bee proxy --port <port>`."""

    def __init__(self, port: int | None = None):
        self.port = port or settings.bee_proxy_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "BeeProxy":
        warn_on_orphans()
        bee_exe = resolve_bee_exe()
        self.proc = _popen([bee_exe, "proxy", "--port", str(self.port)])
        if not _wait_for_proxy_ready(self.port):
            self.__exit__(None, None, None)
            raise RuntimeError(f"bee proxy did not become ready on port {self.port}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is not None:
            kill_tree(self.proc.pid)
            self.proc = None


class BeeStream:
    """Context manager around `bee stream --json --types all`.

    Yields the underlying Popen; callers read `proc.stdout` line by line.
    """

    def __init__(self, types: str = "all"):
        self.types = types
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> subprocess.Popen:
        warn_on_orphans()
        bee_exe = resolve_bee_exe()
        self.proc = _popen([bee_exe, "stream", "--json", "--types", self.types])
        return self.proc

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is not None:
            kill_tree(self.proc.pid)
            self.proc = None


@contextmanager
def bee_proxy(port: int | None = None):
    with BeeProxy(port) as proxy:
        yield proxy


@contextmanager
def bee_stream(types: str = "all"):
    with BeeStream(types) as proc:
        yield proc
