"""Regression test for the 2026-10-07 live-daemon crash: a laptop
sleep/resume killed the bee.exe process tree between kill_tree() entering
and psutil.Process.children() re-validating the parent is still alive,
raising NoSuchProcess from a place the old code didn't guard -- which
propagated out of stream_events()'s reconnect `finally` block and crashed
the entire daemon, not just that one reconnect attempt."""

import psutil
import pytest

from recall.bee.process import kill_tree


class _FakeProcessDiesInChildren:
    """Simulates a process that exists when Process(pid) is constructed,
    but has died by the time .children() is called -- psutil.Process.
    children() internally re-checks liveness and raises NoSuchProcess
    itself in that case."""

    def __init__(self, pid):
        self.pid = pid

    def children(self, recursive=True):
        raise psutil.NoSuchProcess(self.pid)


def test_kill_tree_tolerates_process_dying_between_lookup_and_children(monkeypatch):
    monkeypatch.setattr("recall.bee.process.psutil.Process", _FakeProcessDiesInChildren)
    kill_tree(12345)  # must not raise


class _FakeProcessDiesDuringWait:
    def __init__(self, pid):
        self.pid = pid

    def children(self, recursive=True):
        return []

    def terminate(self):
        pass


def test_kill_tree_tolerates_process_dying_during_wait(monkeypatch):
    monkeypatch.setattr("recall.bee.process.psutil.Process", _FakeProcessDiesDuringWait)

    def _raise_wait_procs(procs, timeout=None):
        raise psutil.NoSuchProcess(12345)

    monkeypatch.setattr("recall.bee.process.psutil.wait_procs", _raise_wait_procs)
    kill_tree(12345)  # must not raise


def test_kill_tree_is_a_noop_when_process_already_gone(monkeypatch):
    def _raise_no_such_process(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr("recall.bee.process.psutil.Process", _raise_no_such_process)
    kill_tree(12345)  # must not raise (pre-existing behavior, still covered)
