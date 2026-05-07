"""
WebSocket Spectrum Streaming — Wire Protocol
Binary-packed spectral frames with MessagePack serialization.
DynamiX Labs | Phase 3

Frame Format (binary):
    [4 bytes]  Magic  0x53445246  ("SDRF")
    [2 bytes]  Version (uint16, network order)
    [4 bytes]  Header length (uint32, network order)
    [N bytes]  MessagePack-encoded header dict
    [M bytes]  Raw payload (float32 PSD, uint8 waterfall, etc.)

The header dict contains:
    channel   : str   — "spectrum.psd" | "spectrum.waterfall" | "spectrum.detections" | "system.status"
    timestamp : float — UNIX epoch (UTC)
    fft_size  : int   — number of FFT bins
    sample_rate : float — current sample rate (Hz)
    center_freq : float — current center frequency (Hz)
    payload_dtype : str — numpy dtype string for the payload
    payload_shape : list[int] — shape of the payload array
    compressed : bool — whether payload is zlib-compressed
"""

import struct
import time
import zlib
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Any

import numpy as np

log = logging.getLogger("satsdr.stream.protocol")

# Try importing msgpack — fall back to JSON if unavailable
try:
    import msgpack  # type: ignore[import-untyped]
    _USE_MSGPACK = True
except ImportError:
    import json
    _USE_MSGPACK = False
    log.warning("msgpack not installed — using JSON fallback (slower)")

# Protocol constants
MAGIC = 0x53445246  # "SDRF"
PROTOCOL_VERSION = 1


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class SpectrumFrame:
    """A single spectrum data frame for WebSocket transmission."""

    channel: str                       # e.g. "spectrum.psd"
    timestamp: float = 0.0             # UNIX epoch UTC
    fft_size: int = 4096
    sample_rate: float = 250_000.0
    center_freq: float = 0.0
    payload_dtype: str = "float32"
    payload_shape: List[int] = field(default_factory=lambda: [0])
    compressed: bool = False
    payload: Optional[np.ndarray] = field(default=None, repr=False)

    # --- Extras (non-payload metadata) ---
    detections: Optional[List[dict]] = field(default=None, repr=False)
    status: Optional[dict] = field(default=None, repr=False)

    def header_dict(self) -> dict:
        """Return the header as a plain dict for serialisation."""
        return {
            "channel": self.channel,
            "timestamp": self.timestamp or time.time(),
            "fft_size": self.fft_size,
            "sample_rate": self.sample_rate,
            "center_freq": self.center_freq,
            "payload_dtype": self.payload_dtype,
            "payload_shape": list(self.payload_shape),
            "compressed": self.compressed,
        }


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def _encode_header(header: dict) -> bytes:
    if _USE_MSGPACK:
        return msgpack.packb(header, use_bin_type=True)
    else:
        return json.dumps(header).encode("utf-8")


def _decode_header(data: bytes) -> dict:
    if _USE_MSGPACK:
        return msgpack.unpackb(data, raw=False)
    else:
        return json.loads(data.decode("utf-8"))


# ---------------------------------------------------------------------------
# Pack / Unpack
# ---------------------------------------------------------------------------
def pack_frame(frame: SpectrumFrame, compress_threshold: int = 8192) -> bytes:
    """
    Serialise a SpectrumFrame into a binary wire-format message.

    Parameters
    ----------
    frame : SpectrumFrame
        The frame to serialise.
    compress_threshold : int
        Payload byte-count above which zlib compression is applied.

    Returns
    -------
    bytes
        Binary message ready for WebSocket `send()`.
    """
    # Prepare payload bytes
    if frame.payload is not None:
        payload_bytes = frame.payload.astype(frame.payload_dtype).tobytes()
        frame.payload_shape = list(frame.payload.shape)
    elif frame.detections is not None:
        # Pack detections as msgpack/json payload
        if _USE_MSGPACK:
            payload_bytes = msgpack.packb(frame.detections, use_bin_type=True)
        else:
            import json as _json
            payload_bytes = _json.dumps(frame.detections).encode("utf-8")
        frame.payload_dtype = "json"
        frame.payload_shape = [len(frame.detections)]
    elif frame.status is not None:
        if _USE_MSGPACK:
            payload_bytes = msgpack.packb(frame.status, use_bin_type=True)
        else:
            import json as _json
            payload_bytes = _json.dumps(frame.status).encode("utf-8")
        frame.payload_dtype = "json"
        frame.payload_shape = [1]
    else:
        payload_bytes = b""

    # Optional zlib compression
    if len(payload_bytes) > compress_threshold:
        payload_bytes = zlib.compress(payload_bytes, level=1)
        frame.compressed = True
    else:
        frame.compressed = False

    # Encode header
    header_bytes = _encode_header(frame.header_dict())

    # Assemble wire frame
    #   Magic(4) + Version(2) + HeaderLen(4) + Header(N) + Payload(M)
    msg = struct.pack("!IHI", MAGIC, PROTOCOL_VERSION, len(header_bytes))
    msg += header_bytes
    msg += payload_bytes
    return msg


def unpack_frame(data: bytes) -> SpectrumFrame:
    """
    Deserialise a binary wire-format message into a SpectrumFrame.

    Parameters
    ----------
    data : bytes
        Raw bytes received from WebSocket.

    Returns
    -------
    SpectrumFrame
        Reconstructed frame with payload as numpy array.

    Raises
    ------
    ValueError
        If magic bytes or version are invalid.
    """
    if len(data) < 10:
        raise ValueError("Frame too short")

    magic, version, header_len = struct.unpack("!IHI", data[:10])

    if magic != MAGIC:
        raise ValueError(f"Invalid magic: 0x{magic:08X} (expected 0x{MAGIC:08X})")
    if version > PROTOCOL_VERSION:
        log.warning(f"Protocol version {version} > {PROTOCOL_VERSION} — may be incompatible")

    header_bytes = data[10 : 10 + header_len]
    payload_bytes = data[10 + header_len :]

    header = _decode_header(header_bytes)

    frame = SpectrumFrame(
        channel=header["channel"],
        timestamp=header.get("timestamp", 0.0),
        fft_size=header.get("fft_size", 4096),
        sample_rate=header.get("sample_rate", 250_000.0),
        center_freq=header.get("center_freq", 0.0),
        payload_dtype=header.get("payload_dtype", "float32"),
        payload_shape=header.get("payload_shape", [0]),
        compressed=header.get("compressed", False),
    )

    # Decompress if needed
    if frame.compressed and payload_bytes:
        payload_bytes = zlib.decompress(payload_bytes)

    # Reconstruct payload
    if payload_bytes:
        if frame.payload_dtype == "json":
            if _USE_MSGPACK:
                frame.detections = msgpack.unpackb(payload_bytes, raw=False)
            else:
                import json as _json
                frame.detections = _json.loads(payload_bytes.decode("utf-8"))
        else:
            arr = np.frombuffer(payload_bytes, dtype=frame.payload_dtype)
            if frame.payload_shape and frame.payload_shape != [0]:
                try:
                    arr = arr.reshape(frame.payload_shape)
                except ValueError:
                    pass  # shape mismatch — keep flat
            frame.payload = arr

    return frame


# ---------------------------------------------------------------------------
# JSON helper for text-mode clients
# ---------------------------------------------------------------------------
def frame_to_json(frame: SpectrumFrame) -> str:
    """
    Serialise a SpectrumFrame as a JSON string (text-mode fallback).

    Payload is base64-encoded if present.  Useful for browser clients
    that prefer JSON over binary WebSocket messages.
    """
    import base64
    import json as _json

    d = frame.header_dict()
    if frame.payload is not None:
        d["payload_b64"] = base64.b64encode(
            frame.payload.astype(frame.payload_dtype).tobytes()
        ).decode("ascii")
    if frame.detections is not None:
        d["detections"] = frame.detections
    if frame.status is not None:
        d["status"] = frame.status
    return _json.dumps(d)
