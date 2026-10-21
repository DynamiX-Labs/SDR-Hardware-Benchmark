"""
USB Transport Layer — Zero-Copy Ring Buffer & Bus Diagnostics
DynamiX Labs | Advanced Hardware Integration

Provides:
  - SharedRingBuffer: lock-free circular buffer backed by shared memory
    with atomic cursor management for zero-copy IQ sample transfer
  - USBTransportProfiler: USB bus diagnostics including speed detection,
    throughput estimation, and hub topology mapping
  - BufferHealthMonitor: overflow/underflow event tracking with
    microsecond-precision timestamps

Usage:
    ring = SharedRingBuffer(capacity_samples=2_000_000)
    ring.write(iq_chunk)
    samples = ring.read(65536)

    profiler = USBTransportProfiler()
    info = profiler.probe_device("rtlsdr")
"""

import logging
import platform
import struct
import time
from dataclasses import dataclass, field
from multiprocessing import shared_memory
from typing import List, Optional

import numpy as np

log = logging.getLogger("satsdr.usb_transport")


# ─── Data Classes ────────────────────────────────────────────────────────


@dataclass
class BufferEvent:
    """A single overflow or underflow event with timing metadata."""

    timestamp: float  # time.perf_counter() epoch
    event_type: str  # "overflow" | "underflow"
    samples_lost: int = 0
    buffer_utilization: float = 0.0  # 0.0 - 1.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "type": self.event_type,
            "samples_lost": self.samples_lost,
            "buffer_utilization": round(self.buffer_utilization, 4),
        }


@dataclass
class USBDeviceInfo:
    """USB bus characteristics for an SDR device."""

    usb_version: str = "Unknown"  # "2.0", "3.0", "3.1"
    usb_speed: str = "Unknown"  # "High Speed", "Super Speed"
    max_throughput_mbps: float = 0.0  # theoretical max
    actual_throughput_mbps: float = 0.0
    bus_id: str = ""
    port_path: str = ""
    is_hub: bool = False
    hub_depth: int = 0
    vendor_id: str = ""
    product_id: str = ""
    serial: str = ""

    def to_dict(self) -> dict:
        return {
            "usb_version": self.usb_version,
            "usb_speed": self.usb_speed,
            "max_throughput_mbps": round(self.max_throughput_mbps, 1),
            "actual_throughput_mbps": round(self.actual_throughput_mbps, 1),
            "bus_id": self.bus_id,
            "port_path": self.port_path,
            "is_hub": self.is_hub,
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "serial": self.serial,
        }


@dataclass
class TransportMetrics:
    """Accumulated transport-layer performance counters."""

    total_samples_written: int = 0
    total_samples_read: int = 0
    total_overflows: int = 0
    total_underflows: int = 0
    peak_buffer_utilization: float = 0.0
    avg_write_latency_us: float = 0.0
    avg_read_latency_us: float = 0.0
    events: List[BufferEvent] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "total_samples_written": self.total_samples_written,
            "total_samples_read": self.total_samples_read,
            "total_overflows": self.total_overflows,
            "total_underflows": self.total_underflows,
            "peak_buffer_utilization": round(self.peak_buffer_utilization, 4),
            "avg_write_latency_us": round(self.avg_write_latency_us, 2),
            "avg_read_latency_us": round(self.avg_read_latency_us, 2),
            "event_count": len(self.events),
        }


# ─── Lock-Free Shared Memory Ring Buffer ─────────────────────────────────


class SharedRingBuffer:
    """
    Lock-free circular buffer backed by shared memory for zero-copy
    IQ sample transfer between producer (SDR capture) and consumer
    (DSP pipeline) threads/processes.

    Uses atomic cursor indices stored in the first 16 bytes of the
    shared memory region:
      - bytes[0:8]  = write_cursor (uint64, little-endian)
      - bytes[8:16] = read_cursor  (uint64, little-endian)
      - bytes[16:]  = sample data  (complex64, 8 bytes per sample)

    The buffer is designed for single-producer, single-consumer (SPSC)
    operation. For multi-producer scenarios, use external synchronization.

    Parameters
    ----------
    capacity_samples : int
        Maximum number of complex64 samples the buffer can hold.
    name : str, optional
        Name for the shared memory segment. Auto-generated if not provided.
    """

    HEADER_SIZE = 16  # 8 bytes write_cursor + 8 bytes read_cursor
    SAMPLE_SIZE = 8  # complex64 = 4 bytes real + 4 bytes imag

    def __init__(self, capacity_samples: int = 2_000_000, name: str = None):
        self.capacity = capacity_samples
        self._name = name or f"sdr_ring_{id(self)}"
        self._total_size = self.HEADER_SIZE + (capacity_samples * self.SAMPLE_SIZE)

        # Create shared memory segment
        try:
            self._shm = shared_memory.SharedMemory(
                name=self._name, create=True, size=self._total_size
            )
        except FileExistsError:
            # Attach to existing segment
            self._shm = shared_memory.SharedMemory(name=self._name, create=False)

        self._buf = self._shm.buf
        self._data_view = np.ndarray(
            (capacity_samples,),
            dtype=np.complex64,
            buffer=self._shm.buf[self.HEADER_SIZE :],
        )

        # Initialize cursors to zero
        self._set_write_cursor(0)
        self._set_read_cursor(0)

        # Performance tracking
        self.metrics = TransportMetrics()
        self._write_latencies = []
        self._read_latencies = []

        log.debug(
            f"SharedRingBuffer created: {self.capacity} samples "
            f"({self._total_size / 1e6:.1f} MB), name='{self._name}'"
        )

    # ── Cursor operations (atomic via struct pack/unpack) ──

    def _get_write_cursor(self) -> int:
        return struct.unpack_from("<Q", self._buf, 0)[0]

    def _set_write_cursor(self, val: int):
        struct.pack_into("<Q", self._buf, 0, val)

    def _get_read_cursor(self) -> int:
        return struct.unpack_from("<Q", self._buf, 8)[0]

    def _set_read_cursor(self, val: int):
        struct.pack_into("<Q", self._buf, 8, val)

    # ── Public API ──

    @property
    def available_read(self) -> int:
        """Number of samples available for reading."""
        w = self._get_write_cursor()
        r = self._get_read_cursor()
        return w - r

    @property
    def available_write(self) -> int:
        """Number of samples that can be written without overflow."""
        return self.capacity - self.available_read

    @property
    def utilization(self) -> float:
        """Buffer utilization ratio (0.0 to 1.0)."""
        return self.available_read / self.capacity if self.capacity > 0 else 0.0

    def write(self, samples: np.ndarray) -> int:
        """
        Write complex64 samples into the ring buffer.

        Returns the number of samples actually written. If the buffer
        is full, excess samples are dropped and an overflow event is logged.
        """
        t0 = time.perf_counter()
        n = len(samples)
        space = self.available_write

        if n > space:
            # Overflow — drop excess samples
            dropped = n - space
            n = space
            samples = samples[:n]
            self.metrics.total_overflows += 1
            self.metrics.events.append(
                BufferEvent(
                    timestamp=t0,
                    event_type="overflow",
                    samples_lost=dropped,
                    buffer_utilization=self.utilization,
                )
            )
            log.warning(f"Ring buffer overflow: {dropped} samples dropped")

        if n == 0:
            return 0

        w = self._get_write_cursor()
        start = w % self.capacity
        end = start + n

        if end <= self.capacity:
            # Contiguous write
            self._data_view[start:end] = samples.astype(np.complex64)
        else:
            # Wrap-around write
            first = self.capacity - start
            self._data_view[start:] = samples[:first].astype(np.complex64)
            self._data_view[: n - first] = samples[first:].astype(np.complex64)

        self._set_write_cursor(w + n)
        self.metrics.total_samples_written += n

        # Track utilization peak
        util = self.utilization
        if util > self.metrics.peak_buffer_utilization:
            self.metrics.peak_buffer_utilization = util

        # Latency tracking
        dt_us = (time.perf_counter() - t0) * 1e6
        self._write_latencies.append(dt_us)
        if len(self._write_latencies) >= 100:
            self.metrics.avg_write_latency_us = np.mean(self._write_latencies[-100:])

        return n

    def read(self, n: int) -> Optional[np.ndarray]:
        """
        Read n complex64 samples from the ring buffer.

        Returns None if fewer than n samples are available (underflow).
        """
        t0 = time.perf_counter()
        available = self.available_read

        if available < n:
            self.metrics.total_underflows += 1
            self.metrics.events.append(
                BufferEvent(
                    timestamp=t0,
                    event_type="underflow",
                    samples_lost=n - available,
                    buffer_utilization=self.utilization,
                )
            )
            return None

        r = self._get_read_cursor()
        start = r % self.capacity
        end = start + n

        if end <= self.capacity:
            out = self._data_view[start:end].copy()
        else:
            first = self.capacity - start
            out = np.concatenate(
                [self._data_view[start:].copy(), self._data_view[: n - first].copy()]
            )

        self._set_read_cursor(r + n)
        self.metrics.total_samples_read += n

        # Latency tracking
        dt_us = (time.perf_counter() - t0) * 1e6
        self._read_latencies.append(dt_us)
        if len(self._read_latencies) >= 100:
            self.metrics.avg_read_latency_us = np.mean(self._read_latencies[-100:])

        return out

    def peek(self, n: int) -> Optional[np.ndarray]:
        """Read n samples without advancing the read cursor."""
        available = self.available_read
        if available < n:
            return None

        r = self._get_read_cursor()
        start = r % self.capacity
        end = start + n

        if end <= self.capacity:
            return self._data_view[start:end].copy()
        else:
            first = self.capacity - start
            return np.concatenate(
                [self._data_view[start:].copy(), self._data_view[: n - first].copy()]
            )

    def flush(self):
        """Discard all buffered data."""
        w = self._get_write_cursor()
        self._set_read_cursor(w)

    def close(self):
        """Release shared memory resources."""
        try:
            self._shm.close()
            self._shm.unlink()
        except Exception:
            pass

    def __del__(self):
        self.close()


# ─── USB Transport Profiler ──────────────────────────────────────────────


class USBTransportProfiler:
    """
    Probes USB bus characteristics for SDR devices.

    Detects USB version, estimates achievable throughput, maps hub topology,
    and identifies potential bottlenecks (e.g., SDR on USB 2.0 hub when
    USB 3.0 is required for full sample rate).
    """

    # Known USB VID:PID for common SDR devices
    KNOWN_DEVICES = {
        "0bda:2838": {"name": "RTL-SDR", "min_usb": "2.0"},
        "0bda:2832": {"name": "RTL-SDR (RTL2832U)", "min_usb": "2.0"},
        "1d50:6089": {"name": "HackRF One", "min_usb": "2.0"},
        "0456:b673": {"name": "ADALM-PLUTO", "min_usb": "2.0"},
        "2500:0020": {"name": "USRP B200", "min_usb": "3.0"},
        "2500:0022": {"name": "USRP B210", "min_usb": "3.0"},
        "1d50:6108": {"name": "LimeSDR Mini", "min_usb": "3.0"},
        "1d50:6001": {"name": "LimeSDR USB", "min_usb": "3.0"},
    }

    # USB speed theoretical maximums
    USB_SPEEDS = {
        "1.1": 12.0,  # Full Speed: 12 Mbps
        "2.0": 480.0,  # High Speed: 480 Mbps
        "3.0": 5000.0,  # Super Speed: 5 Gbps
        "3.1": 10000.0,  # Super Speed+: 10 Gbps
        "3.2": 20000.0,  # Super Speed++ : 20 Gbps
    }

    def __init__(self):
        self._os = platform.system()

    def enumerate_usb_devices(self) -> List[USBDeviceInfo]:
        """
        Enumerate all USB devices on the system and identify SDR hardware.

        On Linux, reads from /sys/bus/usb/devices/.
        On Windows, uses WMI queries.
        Falls back to SoapySDR enumeration.
        """
        devices = []

        if self._os == "Linux":
            devices = self._enumerate_linux()
        elif self._os == "Windows":
            devices = self._enumerate_windows()

        # Fallback: SoapySDR enumeration
        if not devices:
            devices = self._enumerate_soapy()

        return devices

    def _enumerate_linux(self) -> List[USBDeviceInfo]:
        """Enumerate USB devices via sysfs on Linux."""
        import os

        devices = []
        usb_path = "/sys/bus/usb/devices"

        if not os.path.exists(usb_path):
            return devices

        try:
            for dev_name in os.listdir(usb_path):
                dev_dir = os.path.join(usb_path, dev_name)
                vid_path = os.path.join(dev_dir, "idVendor")
                pid_path = os.path.join(dev_dir, "idProduct")
                speed_path = os.path.join(dev_dir, "speed")
                serial_path = os.path.join(dev_dir, "serial")
                version_path = os.path.join(dev_dir, "version")

                if not os.path.exists(vid_path):
                    continue

                vid = self._read_sysfs(vid_path)
                pid = self._read_sysfs(pid_path)
                vid_pid = f"{vid}:{pid}"

                if vid_pid not in self.KNOWN_DEVICES:
                    continue

                info = USBDeviceInfo()
                info.vendor_id = vid
                info.product_id = pid
                info.bus_id = dev_name
                info.serial = self._read_sysfs(serial_path)

                # Parse USB speed
                speed_mbps = self._read_sysfs(speed_path)
                if speed_mbps:
                    try:
                        speed = float(speed_mbps)
                        if speed >= 5000:
                            info.usb_version = "3.0"
                            info.usb_speed = "Super Speed"
                        elif speed >= 480:
                            info.usb_version = "2.0"
                            info.usb_speed = "High Speed"
                        else:
                            info.usb_version = "1.1"
                            info.usb_speed = "Full Speed"
                        info.max_throughput_mbps = speed
                    except ValueError:
                        pass

                # USB version from sysfs
                version_str = self._read_sysfs(version_path)
                if version_str:
                    info.usb_version = version_str.strip()

                # Check hub topology depth
                info.hub_depth = dev_name.count(".")
                info.port_path = dev_name

                devices.append(info)
        except PermissionError:
            log.debug("Insufficient permissions to read USB sysfs")

        return devices

    def _enumerate_windows(self) -> List[USBDeviceInfo]:
        """Enumerate USB devices via WMI on Windows."""
        devices = []
        try:
            import subprocess

            result = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "Get-PnpDevice -Class USB | Select-Object InstanceId, FriendlyName, Status | ConvertTo-Json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json

                pnp_devices = json.loads(result.stdout)
                if isinstance(pnp_devices, dict):
                    pnp_devices = [pnp_devices]

                for dev in pnp_devices:
                    instance_id = dev.get("InstanceId", "")
                    friendly = dev.get("FriendlyName", "")

                    # Extract VID/PID from instance ID
                    vid, pid = "", ""
                    if "VID_" in instance_id and "PID_" in instance_id:
                        try:
                            vid = instance_id.split("VID_")[1][:4].lower()
                            pid = instance_id.split("PID_")[1][:4].lower()
                        except IndexError:
                            continue

                    vid_pid = f"{vid}:{pid}"
                    if vid_pid in self.KNOWN_DEVICES:
                        info = USBDeviceInfo()
                        info.vendor_id = vid
                        info.product_id = pid
                        info.bus_id = instance_id
                        devices.append(info)

        except Exception as e:
            log.debug(f"Windows USB enumeration failed: {e}")

        return devices

    def _enumerate_soapy(self) -> List[USBDeviceInfo]:
        """Fallback: enumerate via SoapySDR."""
        devices = []
        try:
            import SoapySDR

            results = SoapySDR.Device.enumerate()
            for r in results:
                info = USBDeviceInfo()
                info.serial = r.get("serial", "")
                driver = r.get("driver", "")
                info.bus_id = f"soapy:{driver}"

                # Estimate USB version from device type
                if driver in ("uhd",):
                    info.usb_version = "3.0"
                    info.usb_speed = "Super Speed"
                    info.max_throughput_mbps = 5000.0
                else:
                    info.usb_version = "2.0"
                    info.usb_speed = "High Speed"
                    info.max_throughput_mbps = 480.0

                devices.append(info)
        except ImportError:
            log.debug("SoapySDR not available for fallback enumeration")
        except Exception as e:
            log.debug(f"SoapySDR enumeration failed: {e}")

        return devices

    def _read_sysfs(self, path: str) -> str:
        """Read a single-line sysfs file."""
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except (FileNotFoundError, PermissionError):
            return ""

    def probe_device(self, driver: str) -> USBDeviceInfo:
        """
        Probe a specific SDR device and return its USB bus characteristics.

        Parameters
        ----------
        driver : str
            SoapySDR driver name (e.g., "rtlsdr", "hackrf", "uhd")
        """
        devices = self.enumerate_usb_devices()

        # Map driver to known VID:PID patterns
        driver_vid_map = {
            "rtlsdr": ["0bda"],
            "hackrf": ["1d50"],
            "plutosdr": ["0456"],
            "uhd": ["2500"],
            "limesdr": ["1d50"],
        }

        target_vids = driver_vid_map.get(driver, [])
        for dev in devices:
            if dev.vendor_id in target_vids:
                return dev

        # Return default info based on driver
        info = USBDeviceInfo()
        info.bus_id = f"soapy:{driver}"
        if driver in ("uhd",):
            info.usb_version = "3.0"
            info.max_throughput_mbps = 5000.0
        else:
            info.usb_version = "2.0"
            info.max_throughput_mbps = 480.0
        return info

    def check_usb_bottleneck(self, driver: str, sample_rate: float) -> dict:
        """
        Check if the USB connection is a bottleneck for the requested sample rate.

        Parameters
        ----------
        driver : str
            SDR driver name
        sample_rate : float
            Requested sample rate in SPS

        Returns
        -------
        dict with keys:
            - is_bottleneck: bool
            - required_mbps: float
            - available_mbps: float
            - recommendation: str
        """
        info = self.probe_device(driver)

        # Each complex64 sample = 8 bytes = 64 bits
        # But most SDRs transfer as uint8 pairs (I/Q) = 2 bytes per sample
        bytes_per_sample = 2  # RTL-SDR, HackRF transfer as 8-bit I/Q
        if driver in ("uhd", "plutosdr"):
            bytes_per_sample = 4  # 16-bit I/Q

        required_bps = sample_rate * bytes_per_sample * 8  # bits per second
        required_mbps = required_bps / 1e6

        # USB overhead ~ 20% for isochronous transfers
        usable_mbps = info.max_throughput_mbps * 0.8

        is_bottleneck = required_mbps > usable_mbps

        recommendation = ""
        if is_bottleneck:
            recommendation = (
                f"USB {info.usb_version} ({info.max_throughput_mbps:.0f} Mbps) "
                f"cannot sustain {sample_rate/1e6:.1f} MSPS "
                f"(requires {required_mbps:.0f} Mbps). "
                f"Reduce sample rate or upgrade USB connection."
            )
        else:
            headroom = ((usable_mbps - required_mbps) / usable_mbps) * 100
            recommendation = (
                f"USB {info.usb_version} OK — "
                f"{required_mbps:.0f}/{usable_mbps:.0f} Mbps "
                f"({headroom:.0f}% headroom)"
            )

        return {
            "is_bottleneck": is_bottleneck,
            "required_mbps": round(required_mbps, 1),
            "available_mbps": round(usable_mbps, 1),
            "usb_version": info.usb_version,
            "recommendation": recommendation,
        }


# ─── Throughput Estimator ────────────────────────────────────────────────


class ThroughputEstimator:
    """
    Measures actual sustained IQ throughput from an SDR device.

    Streams samples for a configurable duration and measures:
    - Actual sustained samples per second
    - Sample drop rate (gaps in stream)
    - USB transfer latency distribution
    """

    def __init__(self, ring_buffer: SharedRingBuffer = None):
        self.ring = ring_buffer or SharedRingBuffer(capacity_samples=4_000_000)
        self._results = {}

    def measure(
        self, hw_manager, duration_s: float = 10.0, chunk_size: int = 65536
    ) -> dict:
        """
        Measure sustained throughput from an SDR device.

        Parameters
        ----------
        hw_manager : HardwareManager
            Configured and connected hardware manager instance
        duration_s : float
            Duration to stream for (seconds)
        chunk_size : int
            Number of samples to read per iteration

        Returns
        -------
        dict with throughput metrics
        """
        total_samples = 0
        total_reads = 0
        dropped_chunks = 0
        read_times = []
        last_time = None

        start = time.perf_counter()
        deadline = start + duration_s

        while time.perf_counter() < deadline:
            t0 = time.perf_counter()
            try:
                samples = hw_manager.read_samples(chunk_size)
                t1 = time.perf_counter()

                if samples is not None and len(samples) > 0:
                    actual_n = len(samples)
                    total_samples += actual_n
                    total_reads += 1
                    read_times.append((t1 - t0) * 1e6)  # microseconds

                    # Write to ring buffer for analysis
                    self.ring.write(samples)

                    # Check for timing gaps (potential dropped samples)
                    if last_time is not None:
                        gap = t0 - last_time
                        expected_gap = chunk_size / hw_manager.sample_rate
                        if gap > expected_gap * 2.0:
                            dropped_chunks += 1
                    last_time = t1
                else:
                    dropped_chunks += 1

            except Exception as e:
                log.warning(f"Read error during throughput test: {e}")
                dropped_chunks += 1

        elapsed = time.perf_counter() - start
        actual_rate = total_samples / elapsed if elapsed > 0 else 0
        requested_rate = getattr(hw_manager, "sample_rate", 0)
        efficiency = (actual_rate / requested_rate * 100) if requested_rate > 0 else 0

        read_times_arr = np.array(read_times) if read_times else np.array([0])

        self._results = {
            "duration_s": round(elapsed, 3),
            "total_samples": total_samples,
            "total_reads": total_reads,
            "actual_sps": round(actual_rate, 0),
            "requested_sps": requested_rate,
            "efficiency_pct": round(efficiency, 2),
            "dropped_chunks": dropped_chunks,
            "drop_rate_pct": round(dropped_chunks / max(total_reads, 1) * 100, 3),
            "read_latency_us": {
                "mean": round(float(np.mean(read_times_arr)), 1),
                "p50": round(float(np.percentile(read_times_arr, 50)), 1),
                "p95": round(float(np.percentile(read_times_arr, 95)), 1),
                "p99": round(float(np.percentile(read_times_arr, 99)), 1),
                "max": round(float(np.max(read_times_arr)), 1),
            },
            "ring_buffer_metrics": self.ring.metrics.snapshot(),
        }
        return self._results
