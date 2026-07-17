"""
Multi-Node Federated Ground Station Network
Peer-to-peer telemetry sharing, coordinated pass scheduling, and
aggregated decode results across geographically distributed stations.
DynamiX Labs | Phase 4

Architecture:
    Station-A <==> Federation Hub <==> Station-B
                       |
                   Station-C

API-only. No web interface. All federation data is exchanged via
ZeroMQ PUB/SUB + ROUTER/DEALER sockets and consumed programmatically.

Usage:
    node = FederationNode(station_id="GS-CHENNAI", lat=13.08, lon=80.27)
    node.join_federation("tcp://hub:6000")
    node.publish_telemetry(decoded_frame)
    node.start()  # Blocking event loop
"""

import time
import uuid
import threading
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Callable, Any
from collections import deque

log = logging.getLogger("satsdr.federation")

try:
    import zmq  # type: ignore[import-untyped]
    _ZMQ_AVAILABLE = True
except ImportError:
    _ZMQ_AVAILABLE = False

try:
    from ..cluster.models import serialize, deserialize
except (ImportError, ValueError):
    # Fallback for direct imports (test runner without package context)
    try:
        from cluster.models import serialize, deserialize
    except ImportError:
        import json as _json
        def serialize(obj):
            if hasattr(obj, "to_dict"):
                return _json.dumps(obj.to_dict()).encode("utf-8")
            return _json.dumps(obj).encode("utf-8")
        def deserialize(raw):
            return _json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class StationInfo:
    """Ground station descriptor for federation registration."""
    station_id: str
    latitude: float = 0.0
    longitude: float = 0.0
    altitude_m: float = 0.0
    capabilities: List[str] = field(default_factory=list)
    hardware: List[str] = field(default_factory=list)
    contact: str = ""
    online: bool = True
    last_seen: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StationInfo":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class TelemetryShare:
    """A telemetry frame shared across the federation."""
    frame_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    station_id: str = ""
    satellite: str = ""
    decoder: str = ""
    timestamp: float = field(default_factory=time.time)
    frequency_hz: float = 0.0
    snr_db: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)
    raw_hex: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TelemetryShare":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PassCoordination:
    """Coordinate satellite passes across federated stations."""
    satellite: str = ""
    requesting_station: str = ""
    aos_utc: str = ""
    los_utc: str = ""
    max_elevation_deg: float = 0.0
    decoder: str = ""
    frequency_hz: float = 0.0
    claimed_by: str = ""
    status: str = "available"  # available, claimed, active, completed

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PassCoordination":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Federation Hub
# ---------------------------------------------------------------------------
class FederationHub:
    """
    Central federation hub that coordinates multiple ground stations.

    API-only. Provides ZeroMQ endpoints for station registration,
    telemetry aggregation, and pass coordination. No web interface.

    Parameters
    ----------
    bind_addr : str
        ROUTER socket bind address for station connections.
    pub_addr : str
        PUB socket for broadcasting telemetry/events to all stations.
    """

    def __init__(
        self,
        bind_addr: str = "tcp://*:6000",
        pub_addr: str = "tcp://*:6001",
    ):
        if not _ZMQ_AVAILABLE:
            raise ImportError("pyzmq required: pip install pyzmq>=25.0")

        self.bind_addr = bind_addr
        self.pub_addr = pub_addr
        self._stations: Dict[str, StationInfo] = {}
        self._telemetry_log: deque = deque(maxlen=10_000)
        self._pass_queue: List[PassCoordination] = []
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        """Start the federation hub (blocking)."""
        self._running = True
        ctx = zmq.Context()

        router = ctx.socket(zmq.ROUTER)
        router.bind(self.bind_addr)

        pub = ctx.socket(zmq.PUB)
        pub.bind(self.pub_addr)

        poller = zmq.Poller()
        poller.register(router, zmq.POLLIN)

        log.info(f"Federation Hub started | ROUTER: {self.bind_addr} | PUB: {self.pub_addr}")

        try:
            while self._running:
                events = dict(poller.poll(timeout=1000))
                if router in events:
                    self._handle_message(router, pub)

                # Reap offline stations
                self._check_stations()

        except KeyboardInterrupt:
            log.info("Federation Hub interrupted")
        finally:
            self._running = False
            router.close()
            pub.close()
            ctx.term()
            log.info("Federation Hub shut down")

    def stop(self):
        self._running = False

    def get_stations(self) -> List[dict]:
        """List all registered stations."""
        with self._lock:
            return [s.to_dict() for s in self._stations.values()]

    def get_telemetry_log(self, limit: int = 100) -> List[dict]:
        """Get recent shared telemetry frames."""
        with self._lock:
            return [t.to_dict() for t in list(self._telemetry_log)[-limit:]]

    def _handle_message(self, router, pub):
        """Process station messages."""
        frames = router.recv_multipart()
        if len(frames) < 3:
            return

        station_addr = frames[0]
        cmd = frames[1]
        payload = deserialize(frames[2]) if len(frames) > 2 else {}

        with self._lock:
            if cmd == b"REGISTER":
                info = StationInfo.from_dict(payload)
                self._stations[info.station_id] = info
                log.info(f"Station registered: {info.station_id} "
                         f"({info.latitude:.2f}, {info.longitude:.2f})")
                router.send_multipart([
                    station_addr, b"ACK",
                    serialize({"status": "registered", "stations": len(self._stations)}),
                ])
                pub.send_multipart([
                    b"station.join", serialize(info.to_dict()),
                ])

            elif cmd == b"TELEMETRY":
                telem = TelemetryShare.from_dict(payload)
                self._telemetry_log.append(telem)
                # Update station last_seen
                if telem.station_id in self._stations:
                    self._stations[telem.station_id].last_seen = time.time()
                # Broadcast to all
                pub.send_multipart([
                    b"telemetry.new", serialize(telem.to_dict()),
                ])
                log.debug(f"Telemetry from {telem.station_id}: {telem.satellite}")

            elif cmd == b"HEARTBEAT":
                sid = payload.get("station_id", "")
                if sid in self._stations:
                    self._stations[sid].last_seen = time.time()

            elif cmd == b"PASS_ANNOUNCE":
                coord = PassCoordination.from_dict(payload)
                self._pass_queue.append(coord)
                pub.send_multipart([
                    b"pass.announced", serialize(coord.to_dict()),
                ])

            elif cmd == b"PASS_CLAIM":
                sat = payload.get("satellite", "")
                claimer = payload.get("station_id", "")
                for p in self._pass_queue:
                    if p.satellite == sat and p.status == "available":
                        p.claimed_by = claimer
                        p.status = "claimed"
                        pub.send_multipart([
                            b"pass.claimed", serialize(p.to_dict()),
                        ])
                        break

            elif cmd == b"GET_STATIONS":
                stations = [s.to_dict() for s in self._stations.values()]
                router.send_multipart([
                    station_addr, b"STATIONS", serialize(stations),
                ])

    def _check_stations(self):
        """Mark stations offline if no heartbeat received."""
        with self._lock:
            now = time.time()
            for sid, station in self._stations.items():
                if now - station.last_seen > 60.0:
                    station.online = False


# ---------------------------------------------------------------------------
# Federation Node (Ground Station Client)
# ---------------------------------------------------------------------------
class FederationNode:
    """
    A federated ground station node that connects to the hub.

    API-only. Publishes decoded telemetry, subscribes to federation
    events, and coordinates satellite pass assignments.

    Parameters
    ----------
    station_id : str
        Unique station identifier.
    lat, lon : float
        Station coordinates.
    hub_addr : str
        Federation hub ROUTER address.
    sub_addr : str
        Federation hub PUB address for subscriptions.
    """

    def __init__(
        self,
        station_id: str,
        lat: float = 0.0,
        lon: float = 0.0,
        alt_m: float = 0.0,
        capabilities: Optional[List[str]] = None,
        hardware: Optional[List[str]] = None,
        hub_addr: str = "tcp://localhost:6000",
        sub_addr: str = "tcp://localhost:6001",
    ):
        if not _ZMQ_AVAILABLE:
            raise ImportError("pyzmq required: pip install pyzmq>=25.0")

        self.station_id = station_id
        self.lat = lat
        self.lon = lon
        self.alt_m = alt_m
        self.capabilities = capabilities or []
        self.hardware = hardware or []
        self.hub_addr = hub_addr
        self.sub_addr = sub_addr

        self._running = False
        self._received_telemetry: deque = deque(maxlen=1000)
        self._event_callbacks: Dict[str, List[Callable]] = {}

    def on_event(self, event_type: str, callback: Callable):
        """Register a callback for federation events."""
        self._event_callbacks.setdefault(event_type, []).append(callback)

    def start(self):
        """Connect to hub and start event loop (blocking)."""
        self._running = True
        ctx = zmq.Context()

        # DEALER to hub
        dealer = ctx.socket(zmq.DEALER)
        dealer.identity = self.station_id.encode("utf-8")
        dealer.connect(self.hub_addr)

        # SUB for broadcasts
        sub = ctx.socket(zmq.SUB)
        sub.connect(self.sub_addr)
        sub.setsockopt(zmq.SUBSCRIBE, b"telemetry.")
        sub.setsockopt(zmq.SUBSCRIBE, b"station.")
        sub.setsockopt(zmq.SUBSCRIBE, b"pass.")

        # Register
        info = StationInfo(
            station_id=self.station_id,
            latitude=self.lat,
            longitude=self.lon,
            altitude_m=self.alt_m,
            capabilities=self.capabilities,
            hardware=self.hardware,
        )
        dealer.send_multipart([b"REGISTER", serialize(info.to_dict())])

        poller = zmq.Poller()
        poller.register(dealer, zmq.POLLIN)
        poller.register(sub, zmq.POLLIN)

        log.info(f"Federation node {self.station_id} connected to {self.hub_addr}")

        last_heartbeat = time.time()

        try:
            while self._running:
                events = dict(poller.poll(timeout=1000))

                if dealer in events:
                    frames = dealer.recv_multipart()
                    self._handle_reply(frames)

                if sub in events:
                    topic_frames = sub.recv_multipart()
                    self._handle_broadcast(topic_frames)

                if time.time() - last_heartbeat > 15.0:
                    dealer.send_multipart([
                        b"HEARTBEAT",
                        serialize({"station_id": self.station_id}),
                    ])
                    last_heartbeat = time.time()

        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            dealer.close()
            sub.close()
            ctx.term()
            log.info(f"Federation node {self.station_id} disconnected")

    def stop(self):
        self._running = False

    def publish_telemetry(
        self, satellite: str, decoder: str, payload: dict,
        frequency_hz: float = 0.0, snr_db: float = 0.0,
        _dealer=None,
    ):
        """
        Publish decoded telemetry to the federation.

        This method is designed to be called from decoder output handlers.
        If called outside the event loop, creates a temporary connection.
        """
        telem = TelemetryShare(
            station_id=self.station_id,
            satellite=satellite,
            decoder=decoder,
            frequency_hz=frequency_hz,
            snr_db=snr_db,
            payload=payload,
        )

        if _dealer is not None:
            _dealer.send_multipart([b"TELEMETRY", serialize(telem.to_dict())])
        else:
            # One-shot send
            ctx = zmq.Context()
            sock = ctx.socket(zmq.DEALER)
            sock.identity = self.station_id.encode("utf-8")
            sock.connect(self.hub_addr)
            sock.send_multipart([b"TELEMETRY", serialize(telem.to_dict())])
            sock.close()
            ctx.term()

    def announce_pass(
        self, satellite: str, aos_utc: str, los_utc: str,
        max_el: float, decoder: str, freq: float,
    ):
        """Announce an upcoming satellite pass to the federation."""
        coord = PassCoordination(
            satellite=satellite,
            requesting_station=self.station_id,
            aos_utc=aos_utc,
            los_utc=los_utc,
            max_elevation_deg=max_el,
            decoder=decoder,
            frequency_hz=freq,
        )
        ctx = zmq.Context()
        sock = ctx.socket(zmq.DEALER)
        sock.identity = self.station_id.encode("utf-8")
        sock.connect(self.hub_addr)
        sock.send_multipart([b"PASS_ANNOUNCE", serialize(coord.to_dict())])
        sock.close()
        ctx.term()

    def _handle_reply(self, frames):
        """Handle direct replies from hub."""
        if not frames:
            return
        cmd = frames[0]
        if cmd == b"ACK" and len(frames) > 1:
            data = deserialize(frames[1])
            log.info(f"Hub ACK: {data}")
        elif cmd == b"STATIONS" and len(frames) > 1:
            data = deserialize(frames[1])
            log.info(f"Federation stations: {len(data)}")

    def _handle_broadcast(self, frames):
        """Handle PUB/SUB broadcast messages."""
        if len(frames) < 2:
            return
        topic = frames[0].decode("utf-8")
        data = deserialize(frames[1])

        if topic == "telemetry.new":
            telem = TelemetryShare.from_dict(data)
            self._received_telemetry.append(telem)

        # Fire callbacks
        for cb in self._event_callbacks.get(topic, []):
            try:
                cb(data)
            except Exception as exc:
                log.error(f"Event callback error: {exc}")
