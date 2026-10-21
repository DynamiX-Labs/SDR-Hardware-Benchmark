"""
Tests for USB Transport Layer and SharedRingBuffer
DynamiX Labs | Advanced Hardware Integration
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from utils.usb_transport import (BufferEvent, SharedRingBuffer,
                                 TransportMetrics, USBDeviceInfo,
                                 USBTransportProfiler)


class TestUSBTransport:
    """Test suite for USB transport diagnostics and lock-free ring buffer."""

    def test_buffer_event_dataclass(self):
        """Verify BufferEvent dataclass serialization."""
        evt = BufferEvent(
            timestamp=100.0,
            event_type="overflow",
            samples_lost=1024,
            buffer_utilization=0.98,
        )
        d = evt.to_dict()
        assert d["type"] == "overflow"
        assert d["samples_lost"] == 1024
        assert d["buffer_utilization"] == 0.98

    def test_usb_device_info_dataclass(self):
        """Verify USBDeviceInfo dataclass serialization."""
        info = USBDeviceInfo(
            usb_version="3.0",
            usb_speed="Super Speed",
            max_throughput_mbps=5000.0,
            actual_throughput_mbps=480.0,
            vendor_id="2500",
            product_id="0020",
        )
        d = info.to_dict()
        assert d["usb_version"] == "3.0"
        assert d["max_throughput_mbps"] == 5000.0
        assert d["vendor_id"] == "2500"

    def test_transport_metrics_dataclass(self):
        """Verify TransportMetrics counters and snapshot."""
        m = TransportMetrics(
            total_samples_written=10000, total_samples_read=8000, total_overflows=1
        )
        snap = m.snapshot()
        assert snap["total_samples_written"] == 10000
        assert snap["total_samples_read"] == 8000
        assert snap["total_overflows"] == 1

    def test_shared_ring_buffer_write_read(self):
        """Verify basic write and read operations on SharedRingBuffer."""
        buf = SharedRingBuffer(capacity_samples=16384)

        # Write test samples
        data = (np.arange(1024) + 1j * np.arange(1024)).astype(np.complex64)
        written = buf.write(data)
        assert written == 1024
        assert buf.available_read >= 1024

        # Read samples
        read_data = buf.read(1024)
        assert len(read_data) == 1024
        np.testing.assert_array_equal(read_data, data)

        buf.close()

    def test_shared_ring_buffer_wraparound(self):
        """Verify circular wrap-around behavior across buffer boundary."""
        capacity = 1024
        buf = SharedRingBuffer(capacity_samples=capacity)

        # Write 800 samples
        chunk1 = (np.ones(800) + 1j * np.ones(800)).astype(np.complex64)
        buf.write(chunk1)

        # Read 600 samples
        r1 = buf.read(600)
        assert len(r1) == 600

        # Now write 600 samples (will wrap around end of circular buffer)
        chunk2 = (np.full(600, 2.0) + 1j * np.full(600, 2.0)).astype(np.complex64)
        written = buf.write(chunk2)
        assert written == 600

        # Read remaining 200 of chunk1 + 600 of chunk2
        r2 = buf.read(800)
        assert len(r2) == 800
        np.testing.assert_array_equal(r2[:200], chunk1[600:])
        np.testing.assert_array_equal(r2[200:], chunk2)

        buf.close()

    def test_usb_transport_profiler(self):
        """Verify USBTransportProfiler probe methods."""
        profiler = USBTransportProfiler()
        devices = profiler.enumerate_usb_devices()
        assert isinstance(devices, list)

        # Check bottleneck calculation
        check = profiler.check_usb_bottleneck(driver="rtlsdr", sample_rate=2.4e6)
        assert isinstance(check, dict)
        assert "is_bottleneck" in check
        assert "required_mbps" in check
        assert "available_mbps" in check
        assert "recommendation" in check

        # High sample rate on USB 2.0 device
        check_high = profiler.check_usb_bottleneck(driver="hackrf", sample_rate=20e6)
        assert isinstance(check_high, dict)
