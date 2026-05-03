"""
Multi-SDR Coherent Combiner
Manages multiple SDR devices in parallel for simultaneous multi-band reception.
"""
import numpy as np
import threading
import logging
from typing import Dict, Optional
from collections import deque

log = logging.getLogger("satsdr.combiner")


class SDRStream:
    """Thread-safe ring buffer for a single SDR device stream."""

    def __init__(self, device_id: str, buffer_size: int = 1_000_000):
        self.device_id = device_id
        self.buffer = deque(maxlen=buffer_size)
        self.lock = threading.Lock()
        self.active = False
        self.frequency = 0.0
        self.sample_rate = 0.0

    def push(self, samples: np.ndarray):
        with self.lock:
            self.buffer.extend(samples)

    def pull(self, n: int) -> Optional[np.ndarray]:
        with self.lock:
            if len(self.buffer) < n:
                return None
            out = np.array([self.buffer.popleft() for _ in range(n)], dtype=np.complex64)
            return out


class CoherentCombiner:
    """
    Manages multiple SDR devices simultaneously, each tuned to a different
    frequency band. Provides a unified interface for multi-band reception.
    """

    def __init__(self):
        self.streams: Dict[str, SDRStream] = {}
        self._threads: Dict[str, threading.Thread] = {}

    def add_device(self, device_id: str, hardware_type: str, frequency: float,
                   sample_rate: float, gain: float = 30.0):
        """Register and configure an SDR device."""
        from .hardware import HardwareManager

        stream = SDRStream(device_id)
        stream.frequency = frequency
        stream.sample_rate = sample_rate

        try:
            hw = HardwareManager(hardware_type)
            hw.configure(frequency=frequency, sample_rate=sample_rate, gain=gain)
            stream.active = True
            self.streams[device_id] = stream

            # Start capture thread
            t = threading.Thread(
                target=self._capture_loop,
                args=(device_id, hw, stream),
                daemon=True
            )
            self._threads[device_id] = t
            t.start()
            log.info(f"Added device '{device_id}' @ {frequency/1e6:.3f} MHz")
        except Exception as e:
            log.error(f"Failed to add device '{device_id}': {e}")

    def _capture_loop(self, device_id: str, hw, stream: SDRStream, chunk: int = 65536):
        """Background capture thread for one SDR device."""
        log.debug(f"Capture loop started for {device_id}")
        while stream.active:
            try:
                samples = hw.read_samples(chunk)
                stream.push(samples)
            except Exception as e:
                log.error(f"Capture error on {device_id}: {e}")
                stream.active = False
                break

    def read_stream(self, device_id: str, n: int = 65536) -> Optional[np.ndarray]:
        """Read samples from a specific device stream."""
        if device_id not in self.streams:
            return None
        return self.streams[device_id].pull(n)

    def list_devices(self) -> list:
        """List all active devices and their configurations."""
        return [
            {
                "id": did,
                "frequency_mhz": s.frequency / 1e6,
                "sample_rate_ksps": s.sample_rate / 1e3,
                "active": s.active,
                "buffer_depth": len(s.buffer)
            }
            for did, s in self.streams.items()
        ]

    def stop_all(self):
        """Stop all capture threads."""
        for stream in self.streams.values():
            stream.active = False
        for t in self._threads.values():
            t.join(timeout=2.0)
        log.info("All SDR streams stopped.")
