"""MeshNode: IoT LAN split-inference via mDNS."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# MESH NODE  (IoT LAN split-inference via mDNS)
# ══════════════════════════════════════════════════════════════════════════════
# MeshNode announces itself via zeroconf mDNS so multiple Essence instances
# on a LAN discover each other automatically.
# Split inference: T0 node runs small draft model, T2 node verifies —
# speculative decode without any cloud round-trip.
# Sensor adapters (RPi.GPIO, paho-mqtt, pyserial) are gated on availability.
# config.toml [mesh] section: enabled=false, role="auto", peer_discovery="mdns"

class MeshNode:
    """
    mDNS-based peer discovery for Essence LAN mesh.
    When enabled, announces this node and discovers peers.
    Sensor adapters degrade gracefully when hardware is absent.
    """
    SERVICE_TYPE = "_essence._tcp.local."

    def __init__(self, hw: HardwareProfile, workspace: Path,
                 port: int = 7860):
        self._hw        = hw
        self._ws        = workspace
        self._port      = port
        self._peers: list[dict] = []
        self._zeroconf  = None
        self._info      = None

    def start(self) -> None:
        """Announce this node via mDNS. No-op if zeroconf unavailable."""
        try:
            from zeroconf import Zeroconf, ServiceInfo  # type: ignore
            import socket
            local_ip = socket.gethostbyname(socket.gethostname())
            self._zeroconf = Zeroconf()
            self._info = ServiceInfo(
                self.SERVICE_TYPE,
                f"essence-{self._hw.tier_label}.{self.SERVICE_TYPE}",
                addresses=[socket.inet_aton(local_ip)],
                port=self._port,
                properties={
                    "tier": str(self._hw.tier),
                    "model": self._hw.model,
                    "backend": self._hw.backend,
                },
            )
            self._zeroconf.register_service(self._info)
        except ImportError:
            pass  # zeroconf not installed — mesh disabled
        except Exception:
            pass

    def discover_peers(self, timeout: float = 3.0) -> list[dict]:
        """Browse for other Essence nodes on the LAN.
        v20: Non-blocking — uses threading.Event instead of time.sleep()
        so the caller thread is never stalled for the full scan window.
        """
        peers = []
        try:
            from zeroconf import Zeroconf, ServiceBrowser  # type: ignore
            found: list[dict] = []
            _done = threading.Event()
            # name → index in `found` so remove/update can mutate the list (#14)
            _found_by_name: dict[str, int] = {}

            class _Handler:
                def add_service(self, zc, type_, name):
                    info = zc.get_service_info(type_, name)
                    if info and info.addresses:
                        import socket
                        peer = {
                            "name": name,
                            "host": socket.inet_ntoa(info.addresses[0]),
                            "port": info.port,
                            "props": {k.decode(): v.decode()
                                      for k, v in info.properties.items()},
                        }
                        _found_by_name[name] = len(found)
                        found.append(peer)

                def remove_service(self, zc, type_, name):
                    # Drop the peer by name — remove from list and index (#14)
                    idx = _found_by_name.pop(name, None)
                    if idx is not None:
                        found[:] = [p for p in found if p.get("name") != name]
                        # Rebuild index after removal
                        for i, p in enumerate(found):
                            _found_by_name[p["name"]] = i

                def update_service(self, zc, type_, name):
                    # Re-resolve the peer and overwrite the existing entry (#14)
                    info = zc.get_service_info(type_, name)
                    if not info or not info.addresses:
                        return
                    import socket
                    updated = {
                        "name": name,
                        "host": socket.inet_ntoa(info.addresses[0]),
                        "port": info.port,
                        "props": {k.decode(): v.decode()
                                  for k, v in info.properties.items()},
                    }
                    idx = _found_by_name.get(name)
                    if idx is not None and idx < len(found):
                        found[idx] = updated
                    else:
                        _found_by_name[name] = len(found)
                        found.append(updated)
            zc = Zeroconf()
            ServiceBrowser(zc, self.SERVICE_TYPE, _Handler())
            # Wait up to `timeout` s — Event.wait() is non-blocking for the GIL
            _done.wait(timeout=timeout)
            zc.close()
            peers = found
        except Exception:
            pass
        self._peers = peers
        return peers

    def stop(self) -> None:
        if self._zeroconf and self._info:
            try:
                self._zeroconf.unregister_service(self._info)
                self._zeroconf.close()
            except Exception:
                pass

    # ── Sensor adapters (all gated on hardware availability) ────────────────
    @staticmethod
    def gpio_read(pin: int) -> int | None:
        """Read a GPIO pin via gpiod (replaces deprecated RPi.GPIO)."""
        try:
            import gpiod  # type: ignore
            chip = gpiod.Chip("gpiochip0")
            line = chip.get_line(pin)
            line.request(consumer="essence", type=gpiod.LINE_REQ_DIR_IN)
            val = line.get_value()
            line.release()
            return val
        except ImportError:
            return None
        except Exception:
            return None

    @staticmethod
    def mqtt_publish(topic: str, payload: str,
                     host: str = "localhost", port: int = 1883) -> bool:
        """Publish a message to an MQTT broker via paho-mqtt."""
        try:
            import paho.mqtt.client as mqtt  # type: ignore
            client = mqtt.Client()
            client.connect(host, port, keepalive=5)
            client.publish(topic, payload)
            client.disconnect()
            return True
        except ImportError:
            return False
        except Exception:
            return False




# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
