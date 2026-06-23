"""TeamMemorySync: rsync-style differential A2A diff."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# TEAM MEMORY SYNC
# ══════════════════════════════════════════════════════════════════════════════
# Shares a team namespace across multiple Essence peers using the existing A2A
# HTTP channels.  Private sessions stay private; only the _TEAM_ID namespace
# is eligible for sync.
#
# Protocol:
#   1. PUSH  POST /a2a/team-memory/push   {namespace, facts: [SemanticFact]}
#   2. PULL  GET  /a2a/team-memory/pull?namespace=X&since=<epoch>
#
# Per-fact privacy flags: facts with source="private" are never synced.
# Conflict resolution: higher-confidence fact wins; ties resolved by recency.
#
# ENV:
#   Essence_TEAM_SYNC=1                Enable background sync (default: off)
#   Essence_TEAM_SYNC_INTERVAL=300     Sync interval in seconds (default: 5 min)
#   Essence_TEAM_ID=acme               Namespace to sync (required)

_TEAM_SYNC_ENABLED  = os.environ.get("Essence_TEAM_SYNC", "0") == "1"
_TEAM_SYNC_INTERVAL = int(os.environ.get("Essence_TEAM_SYNC_INTERVAL", "300"))


class TeamMemorySync:
    """
    Differential push/pull sync of SemanticStateStore facts across A2A peers.
    Runs as a background daemon thread when enabled.
    """

    def __init__(self, store: SemanticStateStore,
                 peer_urls: list[str],
                 namespace: str = "local") -> None:
        self._store     = store
        self._peers     = peer_urls
        self._namespace = namespace
        self._last_push = 0.0
        self._pending:  list[dict] = []   # undelivered facts buffered for retry
        self._stop      = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not _TEAM_SYNC_ENABLED or not self._peers:
            return
        if self._namespace == "local":
            log.debug("team_sync_skipped", extra={"reason": "namespace=local"})
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="essence-team-sync")
        self._thread.start()
        log.info("team_sync_started", extra={"peers": len(self._peers),
                                              "interval": _TEAM_SYNC_INTERVAL})

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(timeout=_TEAM_SYNC_INTERVAL):
            try:
                self._sync_cycle()
            except Exception as _e:
                log.warning("team_sync_error", extra={"error": str(_e)[:200]})

    def _sync_cycle(self) -> None:
        """One push→pull cycle. Undelivered facts are buffered for next cycle."""
        since = self._last_push
        new_facts = [
            f.to_dict() for f in self._store.query()
            if f.ts > since and f.source != "private"
        ]
        # Merge new facts with any previously undelivered ones
        to_push = self._pending + new_facts
        if to_push:
            delivered = self._push(to_push)
            self._pending = [] if delivered else to_push   # retry on failure
        # Pull from each peer
        for peer_url in self._peers:
            pulled = self._pull_from(peer_url, since)
            for fd in pulled:
                try:
                    f = SemanticFact.from_dict(fd)
                    if f.source == "private": continue
                    self._store.assert_fact(
                        f.entity, f.relation, f.attribute,
                        f.value, f.confidence, source="team_sync")
                except Exception:
                    pass
        self._last_push = time.time()
        log.debug("team_sync_cycle_done",
                  extra={"pushed": len(to_push), "pending": len(self._pending),
                         "peers": len(self._peers)})

    def _push(self, facts: list[dict]) -> bool:
        """Push facts to all peers. Returns True if ALL peers accepted."""
        payload = json.dumps({"namespace": self._namespace, "facts": facts}).encode()
        all_ok = True
        for peer_url in self._peers:
            try:
                req = urllib.request.Request(
                    f"{peer_url.rstrip('/')}/a2a/team-memory/push",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST")
                urllib.request.urlopen(req, timeout=10)
            except Exception as _e:
                log.debug("team_sync_push_error",
                           extra={"peer": peer_url, "error": str(_e)[:80]})
                all_ok = False
        return all_ok

    def _pull_from(self, peer_url: str, since: float) -> list[dict]:
        try:
            url = (f"{peer_url.rstrip('/')}/a2a/team-memory/pull"
                   f"?namespace={urllib.parse.quote(self._namespace)}&since={since}")
            return http_get_json(url, timeout=10).get("facts", [])
        except Exception:
            return []



# ══════════════════════════════════════════════════════════════════════════════
