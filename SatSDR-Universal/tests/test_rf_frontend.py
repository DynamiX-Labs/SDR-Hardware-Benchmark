"""
Tests for RF Frontend Characterization Engine
DynamiX Labs | Advanced Hardware Integration
"""

import pytest
import numpy as np
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from utils.hardware import HardwareManager
from utils.rf_frontend import (
    RFFrontendCharacterizer,
    NoiseFigureResult,
    GainFlatnessResult,
    IQImbalanceResult,
    SpuriousSignal,
    RFFrontendProfile,
)


class TestRFFrontend:
    """Test suite for RF Frontend Characterizer."""

    @pytest.fixture
    def characterizer(self):
        """Create characterizer with a simulated HardwareManager."""
        hw = HardwareManager("rtlsdr")
        hw.configure(frequency=100e6, sample_rate=2.4e6, gain=30.0)
        c = RFFrontendCharacterizer(hw, n_samples=16384)
        yield c
        hw.close()

    def test_noise_figure_dataclass(self):
        """Verify NoiseFigureResult dataclass."""
        nf = NoiseFigureResult(noise_figure_db=3.5, noise_temperature_k=350.0, noise_floor_dbm=-105.0)
        d = nf.to_dict()
        assert d["noise_figure_dB"] == 3.5
        assert d["noise_temperature_K"] == 350.0

    def test_estimate_noise_figure(self, characterizer):
        """Verify noise figure estimation."""
        res = characterizer.estimate_noise_figure()
        assert isinstance(res, NoiseFigureResult)
        assert res.noise_figure_db >= 0.0
        assert res.noise_temperature_k > 0.0

    def test_measure_iq_imbalance(self, characterizer):
        """Verify IQ imbalance measurement."""
        res = characterizer.measure_iq_imbalance()
        assert isinstance(res, IQImbalanceResult)
        assert abs(res.amplitude_imbalance_db) < 20.0
        assert abs(res.phase_imbalance_deg) < 90.0

    def test_scan_spurious_signals(self, characterizer):
        """Verify spurious signal scanner."""
        spurs = characterizer.scan_spurious_signals(threshold_dbfs=-60.0)
        assert isinstance(spurs, list)
        for s in spurs:
            assert isinstance(s, SpuriousSignal)
            assert s.power_dbfs <= 0.0

    def test_measure_sfdr(self, characterizer):
        """Verify Spurious-Free Dynamic Range measurement."""
        sfdr = characterizer.measure_sfdr()
        assert isinstance(sfdr, float)
        assert sfdr >= 0.0

    def test_gain_flatness_profile(self, characterizer):
        """Verify gain flatness sweep."""
        res = characterizer.gain_flatness_profile(
            start_freq=98e6, stop_freq=102e6, n_steps=3
        )
        assert isinstance(res, GainFlatnessResult)
        assert len(res.frequencies_mhz) == 3
        assert len(res.gain_db) == 3
        assert res.max_ripple_db >= 0.0

    def test_full_characterization(self, characterizer):
        """Verify full characterization run and summary output."""
        prof = characterizer.full_characterization()
        assert isinstance(prof, RFFrontendProfile)
        assert prof.noise_figure is not None
        assert prof.iq_imbalance is not None

        summary = prof.summary()
        assert "RF FRONTEND CHARACTERIZATION" in summary

        d = prof.to_dict()
        assert isinstance(d, dict)
        assert "sfdr_dB" in d
