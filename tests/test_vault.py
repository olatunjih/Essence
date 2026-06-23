"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   Hardware probe ────────────────────────────────────────────────────

def test_hardware_profile_clamping():
    hw = HardwareProfile(
        os_name='Linux', arch='x86_64', cpu_cores=-1,
        ram_gb=-1.0, gpu_vendor='none', vram_gb=-5.0,
        has_cuda=False, has_metal=False, has_rocm=False, has_vulkan=False,
        tier=0, tier_label='T0·IoT', backend='ollama', model='qwen3:0.6b',
    )
    assert hw.cpu_cores >= 1
    assert hw.ram_gb >= 0.5
    assert hw.vram_gb >= 0.0


def test_hardware_effective_gb_uses_vram_when_present():
    hw = HardwareProfile(
        os_name='Linux', arch='x86_64', cpu_cores=8,
        ram_gb=32.0, gpu_vendor='nvidia', vram_gb=24.0,
        has_cuda=True, has_metal=False, has_rocm=False, has_vulkan=False,
        tier=3, tier_label='T3·Server', backend='vllm', model='qwen3:32b',
    )
    assert hw.effective_gb == 24.0


def test_hardware_effective_gb_falls_back_to_ram():
    hw = HardwareProfile(
        os_name='Linux', arch='x86_64', cpu_cores=4,
        ram_gb=16.0, gpu_vendor='none', vram_gb=0.0,
        has_cuda=False, has_metal=False, has_rocm=False, has_vulkan=False,
        tier=1, tier_label='T1·Consumer', backend='ollama', model='qwen3:4b',
    )
    assert abs(hw.effective_gb - 16.0 * 0.60) < 0.01


