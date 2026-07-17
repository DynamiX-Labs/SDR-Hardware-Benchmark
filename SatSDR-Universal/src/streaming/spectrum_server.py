"""
WebSocket Live Spectrum Streaming Server
Async real-time RF spectrum distribution to browser & remote clients.
DynamiX Labs | Phase 3

Architecture:
    ┌──────────────┐      ┌──────────────┐
    │ SpectralEngine│─────►│ SpectrumServer│──► ws://host:8765
    │ (PSD/Detect) │      │  (asyncio)    │──► ws://host:8765
    └──────────────┘      └──────────────┘
                                │
                          ┌─────┴──────┐
                          │ HTTP /api/  │
                          │ health     │
                          └────────────┘

Usage:
    server = SpectrumServer(port=8765, spectral_engine=engine)
    await server.start()   # or  asyncio.run(server.start())
"""

import asyncio
import json
import time
import logging
from typing import Set, Dict, Optional, Any
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("satsdr.stream.server")

# Lazy imports for optional dependencies
try:
    import websockets  # type: ignore[import-untyped]
    from websockets.server import serve as ws_serve  # type: ignore
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

from .stream_protocol import SpectrumFrame, pack_frame, frame_to_json


# ---------------------------------------------------------------------------
# Client session
# ---------------------------------------------------------------------------
@dataclass
class ClientSession:
    """Tracks a single connected WebSocket client."""

    ws: Any                              # websockets.WebSocketServerProtocol
    client_id: str = ""
    subscriptions: Set[str] = field(default_factory=set)
    connected_at: float = 0.0
    frames_sent: int = 0
    binary_mode: bool = True             # False = JSON text mode
    authenticated: bool = False


# ---------------------------------------------------------------------------
# Spectrum Server
# ---------------------------------------------------------------------------
class SpectrumServer:
    """
    Async WebSocket server for live RF spectrum distribution.

    Streams spectral data (PSD, waterfall, signal detections) from the
    DSP pipeline to one or more connected clients in real time.

    Parameters
    ----------
    host : str
        Bind address (default "0.0.0.0").
    port : int
        WebSocket listen port (default 8765).
    spectral_engine : SpectralEngine, optional
        If provided, the server will periodically pull PSD data from it.
    gpu_backend : GPUBackend, optional
        For GPU metrics in system.status frames.
    auth_token : str, optional
        If set, clients must send {"auth": "<token>"} as their first message.
    max_fps : int
        Maximum spectral frame rate to clients (default 30).
    fft_size : int
        FFT size for PSD computation (default 4096).
    """

    # Available subscription channels
    CHANNELS = {
        "spectrum.psd",
        "spectrum.waterfall",
        "spectrum.detections",
        "system.status",
    }

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        spectral_engine=None,
        gpu_backend=None,
        auth_token: Optional[str] = None,
        max_fps: int = 30,
        fft_size: int = 4096,
    ):
        if not _WS_AVAILABLE:
            raise ImportError(
                "websockets library required: pip install websockets>=12.0"
            )

        self.host = host
        self.port = port
        self.engine = spectral_engine
        self.gpu = gpu_backend
        self.auth_token = auth_token
        self.max_fps = max_fps
        self.fft_size = fft_size

        self._clients: Dict[str, ClientSession] = {}
        self._running = False
        self._sample_buffer: Optional[np.ndarray] = None
        self._waterfall_buffer: list = []
        self._waterfall_depth = 100  # rows

        # Metrics
        self._total_frames_sent = 0
        self._start_time = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def start(self):
        """Start the WebSocket server (blocking coroutine)."""
        self._running = True
        self._start_time = time.time()

        log.info(f"Spectrum WebSocket server starting on ws://{self.host}:{self.port}")
        log.info(f"  FFT size: {self.fft_size} | Max FPS: {self.max_fps}")
        if self.auth_token:
            log.info("  Authentication: ENABLED")

        async with ws_serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=10,
            max_size=2 ** 22,  # 4 MB max message
            process_request=self._http_handler,
        ):
            # Start the background spectrum producer
            await self._spectrum_producer()

    def stop(self):
        """Signal the server to stop."""
        self._running = False
        log.info("Spectrum server shutting down")

    def push_samples(self, samples: np.ndarray):
        """
        Push IQ samples into the server's buffer.

        Call this from the SDR capture loop to feed live data
        into the streaming pipeline.
        """
        self._sample_buffer = samples

    def get_status(self) -> dict:
        """Return server status summary."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "running": self._running,
            "clients": len(self._clients),
            "total_frames_sent": self._total_frames_sent,
            "uptime_s": round(uptime, 1),
            "fps_target": self.max_fps,
        }

    # ------------------------------------------------------------------
    # HTTP health endpoint
    # ------------------------------------------------------------------
    async def _http_handler(self, path, request_headers):
        """Handle non-WebSocket HTTP requests (health check)."""
        if hasattr(path, 'path'):
            # websockets >= 12.0 passes a Request object
            url_path = path.path
        else:
            url_path = path

        if url_path == "/api/health":
            body = json.dumps({
                "status": "ok",
                "service": "SatSDR Spectrum Server",
                "uptime_s": round(time.time() - self._start_time, 1),
                "clients": len(self._clients),
            }).encode()
            return (
                200,
                [("Content-Type", "application/json"),
                 ("Access-Control-Allow-Origin", "*")],
                body,
            )
        # Return None to proceed with WebSocket upgrade
        return None

    # ------------------------------------------------------------------
    # Client handler
    # ------------------------------------------------------------------
    async def _handle_client(self, ws):
        """Handle a single WebSocket client lifecycle."""
        client_id = f"{id(ws):x}"
        session = ClientSession(
            ws=ws,
            client_id=client_id,
            connected_at=time.time(),
        )

        # Authentication gate
        if self.auth_token:
            try:
                auth_msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                auth_data = json.loads(auth_msg)
                if auth_data.get("auth") != self.auth_token:
                    await ws.send(json.dumps({"error": "authentication_failed"}))
                    await ws.close(4001, "Unauthorized")
                    log.warning(f"Client {client_id}: auth failed")
                    return
                session.authenticated = True
            except (asyncio.TimeoutError, Exception):
                await ws.close(4001, "Auth timeout")
                return
        else:
            session.authenticated = True

        self._clients[client_id] = session
        log.info(f"Client connected: {client_id} ({len(self._clients)} total)")

        # Send welcome
        await ws.send(json.dumps({
            "type": "welcome",
            "server": "SatSDR Spectrum Server",
            "channels": list(self.CHANNELS),
            "protocol_version": 1,
        }))

        try:
            async for message in ws:
                await self._process_client_message(session, message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            del self._clients[client_id]
            log.info(f"Client disconnected: {client_id} ({len(self._clients)} total)")

    async def _process_client_message(self, session: ClientSession, raw: str):
        """Process an incoming client command message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        cmd = msg.get("cmd")

        if cmd == "subscribe":
            channels = msg.get("channels", [])
            for ch in channels:
                if ch in self.CHANNELS:
                    session.subscriptions.add(ch)
            await session.ws.send(json.dumps({
                "type": "subscribed",
                "channels": list(session.subscriptions),
            }))
            log.debug(f"Client {session.client_id} subscribed to {session.subscriptions}")

        elif cmd == "unsubscribe":
            channels = msg.get("channels", [])
            for ch in channels:
                session.subscriptions.discard(ch)
            await session.ws.send(json.dumps({
                "type": "unsubscribed",
                "channels": list(session.subscriptions),
            }))

        elif cmd == "set_mode":
            mode = msg.get("mode", "binary")
            session.binary_mode = (mode == "binary")
            await session.ws.send(json.dumps({
                "type": "mode_set",
                "mode": "binary" if session.binary_mode else "json",
            }))

        elif cmd == "set_fft_size":
            size = msg.get("fft_size", 4096)
            if size in (256, 512, 1024, 2048, 4096, 8192, 16384):
                self.fft_size = size
                await session.ws.send(json.dumps({
                    "type": "config_updated",
                    "fft_size": self.fft_size,
                }))

        elif cmd == "get_status":
            await session.ws.send(json.dumps({
                "type": "status",
                **self.get_status(),
            }))

    # ------------------------------------------------------------------
    # Spectrum producer loop
    # ------------------------------------------------------------------
    async def _spectrum_producer(self):
        """
        Background loop that computes PSD and broadcasts to subscribers.

        Runs at `max_fps` rate.  If no samples are available, sends
        synthetic noise for demonstration purposes.
        """
        interval = 1.0 / self.max_fps

        while self._running:
            t0 = time.time()

            # Get IQ samples (live or synthetic)
            samples = self._sample_buffer
            if samples is None:
                # Generate synthetic noise + carrier for demo
                n = self.fft_size * 4
                t = np.arange(n, dtype=np.float32) / 250_000
                carrier = np.exp(2j * np.pi * 25_000 * t).astype(np.complex64)
                noise = (np.random.randn(n) + 1j * np.random.randn(n)).astype(np.complex64) * 0.3
                samples = carrier + noise

            # Compute PSD
            psd_data, sample_rate, center_freq = self._compute_psd(samples)

            # Broadcast to subscribers
            await self._broadcast_psd(psd_data, sample_rate, center_freq)
            await self._broadcast_waterfall(psd_data, sample_rate, center_freq)
            await self._broadcast_detections(samples, sample_rate, center_freq)
            await self._broadcast_status()

            # Frame rate control
            elapsed = time.time() - t0
            sleep_time = max(0, interval - elapsed)
            await asyncio.sleep(sleep_time)

    def _compute_psd(self, samples: np.ndarray):
        """Compute PSD from IQ samples, using GPU if available."""
        sample_rate = 250_000.0  # default
        center_freq = 0.0

        if self.engine is not None:
            sample_rate = self.engine.sample_rate

        if self.gpu is not None:
            freqs, psd_db = self.gpu.welch_psd(
                samples, fs=sample_rate, nperseg=self.fft_size
            )
        elif self.engine is not None:
            from scipy import signal as sig
            freqs, psd = sig.welch(
                samples, fs=sample_rate, nperseg=self.fft_size,
                return_onesided=False, scaling="density",
            )
            psd_db = 10 * np.log10(np.fft.fftshift(psd) + 1e-20)
        else:
            from scipy import signal as sig
            freqs, psd = sig.welch(
                samples, fs=sample_rate, nperseg=self.fft_size,
                return_onesided=False, scaling="density",
            )
            psd_db = 10 * np.log10(np.fft.fftshift(psd) + 1e-20)

        return psd_db.astype(np.float32), sample_rate, center_freq

    # ------------------------------------------------------------------
    # Broadcast methods
    # ------------------------------------------------------------------
    async def _broadcast_psd(self, psd_data, sample_rate, center_freq):
        """Send PSD frame to all spectrum.psd subscribers."""
        frame = SpectrumFrame(
            channel="spectrum.psd",
            timestamp=time.time(),
            fft_size=self.fft_size,
            sample_rate=sample_rate,
            center_freq=center_freq,
            payload=psd_data,
        )
        await self._send_to_channel("spectrum.psd", frame)

    async def _broadcast_waterfall(self, psd_data, sample_rate, center_freq):
        """Accumulate PSD rows into waterfall and broadcast."""
        self._waterfall_buffer.append(psd_data)
        if len(self._waterfall_buffer) > self._waterfall_depth:
            self._waterfall_buffer.pop(0)

        # Only send every 5th frame to reduce bandwidth
        if len(self._waterfall_buffer) % 5 != 0:
            return

        waterfall_matrix = np.array(self._waterfall_buffer, dtype=np.float32)
        frame = SpectrumFrame(
            channel="spectrum.waterfall",
            timestamp=time.time(),
            fft_size=self.fft_size,
            sample_rate=sample_rate,
            center_freq=center_freq,
            payload=waterfall_matrix,
        )
        await self._send_to_channel("spectrum.waterfall", frame)

    async def _broadcast_detections(self, samples, sample_rate, center_freq):
        """Run signal detection and broadcast results."""
        if self.engine is None:
            return

        # Only run detection every 30 frames (~1/sec at 30fps)
        self._total_frames_sent += 1
        if self._total_frames_sent % 30 != 0:
            return

        try:
            detections = self.engine.detect_signals(samples)
            if detections:
                frame = SpectrumFrame(
                    channel="spectrum.detections",
                    timestamp=time.time(),
                    fft_size=self.fft_size,
                    sample_rate=sample_rate,
                    center_freq=center_freq,
                    detections=detections,
                )
                await self._send_to_channel("spectrum.detections", frame)
        except Exception as exc:
            log.debug(f"Detection error: {exc}")

    async def _broadcast_status(self):
        """Broadcast system status periodically (~every 5 seconds)."""
        if self._total_frames_sent % (self.max_fps * 5) != 0:
            return

        status = self.get_status()
        if self.gpu is not None:
            status["gpu"] = self.gpu.metrics.snapshot()

        frame = SpectrumFrame(
            channel="system.status",
            timestamp=time.time(),
            status=status,
        )
        await self._send_to_channel("system.status", frame)

    async def _send_to_channel(self, channel: str, frame: SpectrumFrame):
        """Send a frame to all clients subscribed to the given channel."""
        subscribers = [
            s for s in self._clients.values()
            if channel in s.subscriptions
        ]
        if not subscribers:
            return

        # Pre-serialise once for all subscribers
        binary_msg = pack_frame(frame)
        json_msg = None  # Lazy — only compute if needed

        for session in subscribers:
            try:
                if session.binary_mode:
                    await session.ws.send(binary_msg)
                else:
                    if json_msg is None:
                        json_msg = frame_to_json(frame)
                    await session.ws.send(json_msg)
                session.frames_sent += 1
            except Exception:
                pass  # Client likely disconnecting
