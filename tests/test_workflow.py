"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Heartbeat scheduler ───────────────────────────────────────────────

def test_parse_interval_seconds():
    assert _parse_interval('30s') == 30.0


def test_parse_interval_minutes():
    assert _parse_interval('5m') == 300.0


def test_parse_interval_hours():
    assert _parse_interval('2h') == 7200.0


def test_parse_interval_days():
    assert _parse_interval('1d') == 86400.0


def test_parse_interval_invalid_returns_none():
    assert _parse_interval('invalid') is None


def test_heartbeat_scheduler_add_and_list(tmp_path):
    sched = HeartbeatScheduler(tmp_path, lambda m: 'HEARTBEAT_OK')
    sched.add('test-job', 'do something', '30m')
    jobs  = sched.list_jobs()
    assert any(j.name == 'test-job' for j in jobs)


def test_heartbeat_scheduler_remove_job(tmp_path):
    sched   = HeartbeatScheduler(tmp_path, lambda m: 'HEARTBEAT_OK')
    sched.add('job1', 'msg', '1h')
    removed = sched.remove('job1')
    assert removed is True
    assert not any(j.name == 'job1' for j in sched.list_jobs())


def test_heartbeat_job_serialises_with_model_dump():
    job = HeartbeatJob(name='j', message='m', schedule='1h')
    d   = job.model_dump()
    assert d['name'] == 'j' and d['enabled'] is True


