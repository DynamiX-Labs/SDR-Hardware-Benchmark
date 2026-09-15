"""
Tests for Advanced Hardware Abstraction Layer (HAL)
DynamiX Labs | Advanced Hardware Integration
"""

import pytest
import numpy as np
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from utils.hardware import HardwareManager, SDRDeviceProfile, GainStage


class TestHardwareManager:
    """Test suite for HardwareManager."""

    @pytest.fixture
    def hw(self):
        """Create HardwareManager instance in simulated mode (no physical device)."""
        mgr = HardwareManager(driver="rtlsdr")
        yield mgr
        mgr.close()

    def test_init(self, hw):
        """Verify HardwareManager initialization."""
        assert hw.driver == "rtlsdr"
        assert isinstance(hw.device_profile, SDRDeviceProfile)
        assert hw.device_profile.driver == "rtlsdr"

    def test_gain_stage_dataclass(self):
        """Verify GainStage dataclass and dictionary conversion."""
        stage = GainStage(name="LNA", min_db=0.0, max_db=49.6, step_db=1.0)
        d = stage.to_dict()
        assert d["name"] == "LNA"
        assert d["min_dB"] == 0.0
        assert d["max_dB"] == 49.6

    def test_device_profile_dataclass(self, hw):
        """Verify SDRDeviceProfile attributes and dictionary export."""
        prof = hw.device_profile
        d = prof.to_dict()
        assert isinstance(d, dict)
        assert "driver" in d
        assert "sample_rate_range" in d
        assert "freq_range_MHz" in d

    def test_configure(self, hw):
        """Verify configuration updates state."""
        hw.configure(frequency=144e6, sample_rate=2.048e6, gain=35.0)
        assert hw.frequency == 144e6
        assert hw.sample_rate == 2.048e6
        assert hw.gain == 35.0

    def test_read_samples_simulated(self, hw):
        """Verify reading samples when in simulation / fallback."""
        hw.configure(frequency=100e6, sample_rate=2.4e6, gain=30.0)
        samples = hw.read_samples(4096)
        assert isinstance(samples, np.ndarray)
        assert len(samples) == 4096
        assert np.iscomplexobj(samples)
        # Verify signal amplitude is non-zero
        assert np.max(np.abs(samples)) > 0.0

    def test_streaming_lifecycle(self, hw):
        """Verify start and stop streaming with ring buffer."""
        hw.configure(frequency=100e6, sample_rate=2.0e6, gain=25.0)
        hw.start_streaming(chunk_size=1024)
        assert hw.is_streaming is True

        # Read samples from active stream
        samples = hw.read_samples(1024)
        assert len(samples) == 1024

        hw.stop_streaming()
        assert hw.is_streaming is False

    def test_gain_calibration(self, hw):
        """Verify auto gain calibration sweep."""
        hw.configure(frequency=137.5e6, sample_rate=2.4e6)
        best_gain = hw.auto_calibrate_gain()
        assert isinstance(best_gain, float)
        assert best_gain >= 0.0

    def test_close(self, hw):
        """Verify proper shutdown and cleanup."""
        hw.close()
        assert hw.is_open is False
