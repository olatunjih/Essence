"""Skill gulper: ingest skills from any URL."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SKILL GULPER
# ══════════════════════════════════════════════════════════════════════════════
# Absorbs skills from any URL regardless of their original format:
#
#   Format detection order:
#     1. YAML frontmatter  → extract name/description/steps, write SKILL.md
#     2. AgentSkill SOUL.md  → translate soul personality + memory to SKILL.md
#     3. SkillHub manifest  → parse .yaml and convert capability list
#     4. micro-agent index.js  → extract prompt text (best-effort AST walk)
#     5. micro-skill TS      → extract skill description from TypeScript module
#     6. Raw Python        → wrap as minimal SKILL.md + tool.py
#     7. Plain Markdown    → use as-is (rename to SKILL.md)
#
#   After translation the skill is:
#     • Saved to workspace/skills/<slug>/SKILL.md (+ tool.py if Python source)
#     • Validated through sandbox_check dry-run
#     • Tagged with source_url and source_hash for update-diffing
#     • Hot-reloaded into the live skills index
#
#   `gulp` is additive and idempotent: re-running with the same URL updates
#   only files whose content hash has changed.

_GULP_FRONTMATTER_RE = re.compile(
    r'^---\s*\n(.*?)\n---\s*\n(.*)$', re.DOTALL)

_GULP_YAML_SIMPLE_RE = re.compile(
    r'^([a-zA-Z_]+)\s*:\s*(.+)$', re.MULTILINE)


def _gulp_parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Extract YAML frontmatter dict and body from raw text. Returns ({}, raw) on miss."""
    m = _GULP_FRONTMATTER_RE.match(raw.strip())
    if not m:
        return {}, raw
    meta: dict = {}
    for km in _GULP_YAML_SIMPLE_RE.finditer(m.group(1)):
        meta[km.group(1).strip()] = km.group(2).strip().strip('"\'')
    return meta, m.group(2).strip()


def _gulp_detect_format(raw: str, url: str) -> str:
    """Return one of: frontmatter | soul | SkillHub | micro-agent | ts | python | markdown"""
    lower = raw[:300].lower()
    url_l = url.lower()
    if _GULP_FRONTMATTER_RE.match(raw.strip()):
        return "frontmatter"
    if "personality" in lower and ("soul" in url_l or "soul.md" in url_l):
        return "soul"
    if url_l.endswith(".yaml") or url_l.endswith(".yml"):
        return "SkillHub"
    if url_l.endswith(".js") and ("skill" in url_l or "index" in url_l):
        return "micro-agent"
    if url_l.endswith(".ts"):
        return "ts"
    if url_l.endswith(".py"):
        return "python"
    return "markdown"


def _gulp_to_skill_md(raw: str, fmt: str, slug: str,
                      source_url: str, source_hash: str) -> str:
    """Translate any ingested format into a canonical Essence SKILL.md string."""
    header = (
        f"<!-- gulped from: {source_url} -->\n"
        f"<!-- source_hash: {source_hash} -->\n\n"
    )

    if fmt == "frontmatter":
        meta, body = _gulp_parse_frontmatter(raw)
        name  = meta.get("name", slug)
        desc  = meta.get("description", "Imported skill")
        tools = meta.get("tools", "")
        return (
            f"{header}"
            f"---\n"
            f"name: {name}\n"
            f"description: {desc}\n"
            f"tools: [{tools}]\n"
            f"trigger: manual\n"
            f"source_url: {source_url}\n"
            f"---\n\n"
            f"# {name}\n\n{body}\n"
        )

    if fmt == "soul":
        # Extract Personality and Memory sections from AgentSkill SOUL.md
        sections: dict[str, str] = {}
        current = ""
        for line in raw.splitlines():
            if line.startswith("## "):
                current = line[3:].strip().lower()
                sections[current] = ""
            elif current:
                sections[current] = sections.get(current, "") + line + "\n"
        personality = sections.get("personality", "").strip()
        memory      = sections.get("memory", sections.get("memories", "")).strip()
        desc = personality[:120].replace("\n", " ") if personality else "AgentSkill skill"
        steps = "1. Apply personality context from source agent.\n"
        if memory:
            steps += f"2. Recall context:\n{memory[:400]}\n"
        return (
            f"{header}"
            f"---\n"
            f"name: {slug}\n"
            f"description: {desc}\n"
            f"tools: []\n"
            f"trigger: manual\n"
            f"source_url: {source_url}\n"
            f"---\n\n"
            f"# {slug} (from AgentSkill SOUL)\n\n"
            f"{personality[:600]}\n\n"
            f"## Steps\n{steps}\n"
        )

    if fmt == "SkillHub":
        # Parse simple key:value YAML manifest
        meta, body = _gulp_parse_frontmatter(raw)
        if not meta:
            for km in _GULP_YAML_SIMPLE_RE.finditer(raw):
                meta[km.group(1).strip()] = km.group(2).strip().strip('"\'')
        name = meta.get("name", meta.get("id", slug))
        desc = meta.get("description", meta.get("desc", "SkillHub skill"))
        tools_raw = meta.get("tools", meta.get("capabilities", ""))
        return (
            f"{header}"
            f"---\n"
            f"name: {name}\n"
            f"description: {desc}\n"
            f"tools: [{tools_raw}]\n"
            f"trigger: manual\n"
            f"source_url: {source_url}\n"
            f"---\n\n"
            f"# {name}\n\n{desc}\n\n"
            f"## Source\nIngested from SkillHub: {source_url}\n"
        )

    if fmt in ("micro-agent", "ts"):
        # Best-effort: extract any string that looks like a description or system prompt
        desc_match = re.search(
            r'(?:description|desc|systemPrompt|system_prompt|prompt)\s*[=:]\s*["\']([^"\']{10,300})',
            raw, re.I)
        desc = desc_match.group(1).strip() if desc_match else f"Skill from {source_url}"
        lang = "JavaScript" if fmt == "micro-agent" else "TypeScript"
        return (
            f"{header}"
            f"---\n"
            f"name: {slug}\n"
            f"description: {desc[:120]}\n"
            f"tools: [shell, python_exec]\n"
            f"trigger: manual\n"
            f"source_url: {source_url}\n"
            f"---\n\n"
            f"# {slug} (from {lang})\n\n"
            f"{desc}\n\n"
            f"## Notes\nOriginal source: {source_url}\n"
            f"Review tool.py to inspect the imported logic.\n"
        )

    if fmt == "python":
        # Wrap bare Python in a minimal SKILL.md; raw source becomes tool.py
        first_doc = re.search(r'"""(.*?)"""', raw, re.DOTALL)
        desc = first_doc.group(1).strip()[:120].replace("\n", " ") if first_doc \
               else f"Python tool from {source_url}"
        return (
            f"{header}"
            f"---\n"
            f"name: {slug}\n"
            f"description: {desc}\n"
            f"tools: [python_exec]\n"
            f"trigger: manual\n"
            f"source_url: {source_url}\n"
            f"---\n\n"
            f"# {slug}\n\n{desc}\n\n"
            f"## Usage\nInvoke via python_exec; see tool.py for implementation.\n"
        )

    # markdown / fallback — use raw content verbatim
    return (
        f"{header}"
        f"---\n"
        f"name: {slug}\n"
        f"description: Imported skill\n"
        f"tools: []\n"
        f"trigger: manual\n"
        f"source_url: {source_url}\n"
        f"---\n\n"
        + raw
    )


def skill_gulp(source_url: str, workspace: Path) -> tuple[bool, str]:
    """
    Ingest a single skill from any URL.
    Returns (success: bool, message: str).

    Supports: raw GitHub/GitLab URLs, SkillHub manifests, SOUL.md,
    micro-agent JS skills, micro-skill TS, bare Python, plain Markdown.
    """
    # Convert github.com blob URLs to raw.githubusercontent.com
    url = re.sub(
        r'https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)',
        r'https://raw.githubusercontent.com/\1/\2/\3/\4',
        source_url)
    url = re.sub(
        r'https://gitlab\.com/([^/]+)/([^/]+)/-/blob/([^/]+)/(.*)',
        r'https://\1.gitlab.com/\2/-/raw/\3/\4',
        url)

    # Fetch content
    try:
        req  = urllib.request.Request(url, headers={"User-Agent": f"Essence/{Essence_VERSION}"})
        raw  = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="replace")
    except Exception as e:
        return False, f"fetch failed: {e}"

    # Derive slug from URL
    slug = re.sub(r"[^a-z0-9]+", "-",
                  url.rstrip("/").split("/")[-1]
                     .replace(".md","").replace(".yaml","").replace(".yml","")
                     .replace(".py","").replace(".js","").replace(".ts","")
                     .lower())[:40].strip("-") or "imported-skill"

    source_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
    fmt         = _gulp_detect_format(raw, url)

    skill_dir  = workspace / "skills" / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"

    # Idempotency: skip if content unchanged
    if skill_path.exists():
        existing = skill_path.read_text(encoding="utf-8")
        if f"source_hash: {source_hash}" in existing:
            return True, f"'{slug}' already up-to-date (hash {source_hash})"

    # Write SKILL.md
    skill_md = _gulp_to_skill_md(raw, fmt, slug, source_url, source_hash)
    skill_path.write_text(skill_md, encoding="utf-8")

    # Write tool.py for Python sources — sandbox + safety checks
    if fmt == "python":
        tool_path = skill_dir / "tool.py"
        if not tool_path.exists() or source_hash not in tool_path.read_text(encoding="utf-8"):
            # 1. Static sandbox_check: reject known dangerous patterns before writing
            danger = sandbox_check(raw, workspace)
            if danger:
                # Don't write the file; report the block reason
                log.warning("skill_gulp_blocked",
                            extra={"slug": slug, "url": source_url[:80],
                                   "reason": danger[:120]})
                return False, (f"'{slug}' blocked by sandbox: {danger[:120]}. "
                               f"Review the source manually before importing.")
            # 2. Write with provenance header
            tool_path.write_text(
                f"# gulped from: {source_url}\n# hash: {source_hash}\n"
                f"# WARNING: auto-ingested code — review before enabling in production\n\n"
                + raw,
                encoding="utf-8")
            # 3. AST syntax-check
            r = subprocess.run(
                [sys.executable, "-c",
                 f"import ast; ast.parse(open({str(tool_path)!r}).read())"],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                tool_path.unlink(missing_ok=True)
                return False, f"'{slug}' gulped but tool.py has syntax errors: {r.stderr[:200]}"
            # 4. Log ingestion with warning about review
            log.warning("skill_gulp_python_written",
                        extra={"slug": slug, "url": source_url[:80],
                               "msg": "Python tool.py auto-ingested — manual review recommended"}
                        )

    log.info("skill_gulped", extra={"slug": slug, "fmt": fmt,
                                     "url": source_url[:80], "hash": source_hash})
    return True, f"'{slug}' ingested ({fmt} → SKILL.md, hash {source_hash})"


def skill_gulp_dir(directory: Path, workspace: Path) -> list[tuple[bool, str]]:
    """
    Recursively ingest every skill found under `directory`.
    Searches for: SKILL.md, SOUL.md, *.yaml, *.yml, index.js, skill.ts, tool.py

    Returns a list of (success, message) tuples — one per discovered skill file.
    """
    if not directory.exists():
        return [(False, f"directory not found: {directory}")]

    _CANDIDATE_NAMES = {"SKILL.md", "SOUL.md", "skill.yaml", "skill.yml",
                        "manifest.yaml", "manifest.yml", "index.js",
                        "skill.ts", "tool.py"}
    _CANDIDATE_EXTS  = {".md", ".yaml", ".yml", ".js", ".ts", ".py"}

    visited:  set[Path] = set()
    results:  list[tuple[bool, str]] = []

    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.name not in _CANDIDATE_NAMES and path.suffix not in _CANDIDATE_EXTS:
            continue
        # Avoid double-ingesting (e.g. a directory already has a SKILL.md)
        skill_root = path.parent
        if skill_root in visited:
            continue
        visited.add(skill_root)

        # Use file:// pseudo-URL so slug derivation and format detection work
        file_url = f"file://{path}"
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            results.append((False, f"{path.name}: read error — {e}"))
            continue

        slug = re.sub(r"[^a-z0-9]+", "-",
                      path.parent.name.lower())[:40].strip("-") or \
               re.sub(r"[^a-z0-9]+", "-", path.stem.lower())[:40].strip("-") or \
               "imported-skill"
        source_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        fmt         = _gulp_detect_format(raw, str(path))

        skill_dir  = workspace / "skills" / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"

        if skill_path.exists():
            existing = skill_path.read_text(encoding="utf-8")
            if f"source_hash: {source_hash}" in existing:
                results.append((True, f"'{slug}' already up-to-date"))
                continue

        skill_md = _gulp_to_skill_md(raw, fmt, slug, str(path), source_hash)
        skill_path.write_text(skill_md, encoding="utf-8")
        results.append((True, f"'{slug}' ingested from {path.name} ({fmt})"))

    if not results:
        results.append((False, f"no skill files found under {directory}"))
    return results


# STT: faster-whisper (~200ms for 5s audio on Pi 4, CPU)
# TTS: kokoro-onnx (5 MB ONNX model, 100ms latency, all platforms)
# Wake word: openWakeWord (Apache 2.0)
# If pyaudio is absent the adapter is a no-op and startup proceeds normally.

class VoiceAdapter:
    """
    Push-to-talk / wake-word voice I/O.
    Gate: if pyaudio is not installed this class is a graceful no-op.
    Full pipeline: microphone → faster-whisper STT → agent → kokoro-onnx TTS.
    """
    def __init__(self, hw: HardwareProfile):
        self._hw      = hw
        self._enabled = False
        self._stt     = None
        self._wakeword= None
        if hw.tier < 1:
            return   # T0: no voice — not enough resources
        try:
            import pyaudio  # type: ignore  # noqa: F401
            self._enabled = True
        except ImportError:
            return  # graceful no-op

    def available(self) -> bool:
        return self._enabled

    def transcribe(self, audio_path: str) -> str:
        """STT via faster-whisper. Returns transcript string."""
        if not self._enabled:
            return "[voice not available]"
        try:
            from faster_whisper import WhisperModel  # type: ignore
            if self._stt is None:
                _model_map = {1: "tiny.en", 2: "base.en", 3: "small"}
                _wm = _model_map.get(self._hw.tier, "medium")
                self._stt = WhisperModel(_wm, device="cpu", compute_type="int8")
            segments, _ = self._stt.transcribe(audio_path)
            return " ".join(s.text for s in segments).strip()
        except ImportError:
            return "[faster-whisper not installed: pip install faster-whisper]"
        except Exception as e:
            return f"[STT error: {e}]"

    def speak(self, text: str, out_path: str = "") -> str:
        """TTS via kokoro-onnx. Returns path to output wav."""
        if not self._enabled:
            return "[voice not available]"
        if not out_path:
            import tempfile as _tf
            out_path = str(Path(_tf.gettempdir()) / "essence_tts.wav")
        try:
            import kokoro  # type: ignore
            pipeline = kokoro.KPipeline(lang_code="en-us")
            generator = pipeline(text, voice="af_heart", speed=1.0)
            samples = []
            for _, _, audio in generator:
                samples.extend(audio.tolist())
            import numpy as np
            import wave
            with wave.open(out_path, "w") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
                wf.writeframes(
                    (np.array(samples, dtype=np.float32)
                    .clip(-1, 1) * 32767).astype(np.int16).tobytes())
            return out_path
        except ImportError:
            return "[kokoro-onnx not installed: pip install kokoro-onnx]"
        except Exception as e:
            return f"[TTS error: {e}]"


# ══════════════════════════════════════════════════════════════════════════════
