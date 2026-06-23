"""self-update: fetch latest monolith from upstream."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SELF-UPDATE
# ══════════════════════════════════════════════════════════════════════════════

def self_update() -> None:
    """
    Safe self-update: download to essence.new.py, verify SHA256 against GitHub
    release manifest, then print a restart instruction.
    Never overwrites the running script directly (breaks on Windows file locks).
    Never exec() or subprocess-calls the new file automatically.
    """
    print(f"\n{bold('Checking for updates …')}")
    if not GITHUB_REPO:
        print(yellow("  self-update: Essence_GITHUB_REPO not set. Set env var to enable."))
        return
    api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        resp = json.loads(urllib.request.urlopen(api, timeout=8).read().decode("utf-8", errors="replace"))
        tag  = resp.get("tag_name", "").lstrip("v")
        if not tag or tag <= Essence_VERSION:
            print(f"  {green('✓')} Already at latest ({Essence_VERSION}).")
            return
        print(f"  New version: {green(tag)}  (current {Essence_VERSION})")
        for asset in resp.get("assets", []):
            if not asset.get("name", "").endswith(".py"):
                continue
            download_url = asset["browser_download_url"]
            # Look for SHA256 manifest asset alongside the .py
            sha_url = ""
            for a2 in resp.get("assets", []):
                if a2.get("name", "").endswith(".sha256"):
                    sha_url = a2["browser_download_url"]
                    break
            new_path = Path(sys.argv[0]).parent / "essence.new.py"
            print(f"  Downloading to {new_path} …")
            urllib.request.urlretrieve(download_url, new_path)
            # Verify SHA256 if manifest available
            if sha_url:
                try:
                    expected = urllib.request.urlopen(
                        sha_url, timeout=10).read().decode().split()[0].lower()
                    actual = hashlib.sha256(
                        new_path.read_bytes()).hexdigest().lower()
                    if actual != expected:
                        new_path.unlink(missing_ok=True)
                        print(red(f"  SHA256 mismatch — download aborted. "
                                  f"expected={expected[:16]}… got={actual[:16]}…"))
                        return
                    print(f"  {green('✓')} SHA256 verified.")
                except Exception as e:
                    print(yellow(f"  SHA256 check skipped: {e}"))
            print(f"  {green('✓')} Downloaded. Restart with:")
            print(f"    {cyan(f'mv {new_path} {sys.argv[0]} && python {sys.argv[0]}')}")
            return
        print(yellow("  No .py asset in release. Check GitHub manually."))
    except Exception as e:
        print(red(f"  Update check failed: {e}"))


# ══════════════════════════════════════════════════════════════════════════════
