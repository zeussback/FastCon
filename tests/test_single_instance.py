"""Tests for the Windows named-mutex single-instance lock and activation signal."""

from __future__ import annotations

import sys
import threading
import uuid

import pytest

from src.single_instance import SingleInstanceLock, signal_existing_instance

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Named kernel objects are Windows-only"
)


def _unique_name() -> str:
    return f"FastConTest-{uuid.uuid4().hex}"


def test_second_lock_with_same_name_is_rejected_until_release() -> None:
    """Only one FastCon instance may own the lock; it becomes free after release."""
    name = _unique_name()
    first = SingleInstanceLock(name)
    second = SingleInstanceLock(name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False

        first.release()
        assert second.acquire() is True
    finally:
        first.release()
        second.release()


def test_signal_existing_instance_triggers_activation_callback() -> None:
    """A second launch signals the running instance to bring its window to front."""
    name = _unique_name()
    lock = SingleInstanceLock(name)
    activated = threading.Event()
    try:
        assert lock.acquire() is True
        lock.start_listener(activated.set)

        assert signal_existing_instance(name) is True
        assert activated.wait(timeout=3.0) is True
    finally:
        lock.release()


def test_signal_without_running_instance_returns_false() -> None:
    """Signalling when nothing is running fails gracefully instead of raising."""
    assert signal_existing_instance(_unique_name()) is False


def test_release_is_idempotent_and_stops_listener() -> None:
    """Releasing twice is safe and the listener thread terminates."""
    lock = SingleInstanceLock(_unique_name())
    assert lock.acquire() is True
    lock.start_listener(lambda: None)
    lock.release()
    lock.release()
    assert lock.listener_alive is False
