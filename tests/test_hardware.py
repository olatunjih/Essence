"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Model registry ────────────────────────────────────────────────────

def test_models_for_tier_is_subset_of_higher():
    t1 = models_for_tier(1)
    t3 = models_for_tier(3)
    assert len(t1) <= len(t3)
    assert all(m.min_tier <= 1 for m in t1)


def test_best_fit_returns_model_spec():
    hw = HardwareProfile(
        os_name='Linux', arch='x86_64', cpu_cores=8,
        ram_gb=16.0, gpu_vendor='none', vram_gb=0.0,
        has_cuda=False, has_metal=False, has_rocm=False, has_vulkan=False,
        tier=1, tier_label='T1·Consumer', backend='ollama', model='qwen3:4b',
    )
    m = best_fit(hw)
    assert isinstance(m, ModelSpec)


