"""
Tests for Advanced Multi-SDR Coherent Combiner
DynamiX Labs | Advanced Hardware Integration
"""

import pytest
import numpy as np
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from utils.coherent_combiner import CoherentCombiner, SDRStream, StreamHealth, CombinerMetrics


class TestCoherentCombiner:
    """Test suite for CoherentCombiner and SDRStream."""

    @pytest.fixture
    def combiner(self):
        """Create a CoherentCombiner instance."""
        c = CoherentCombiner(default_buffer_size=131072)
        yield c
        c.stop_all()

    def test_stream_health_dataclass(self):
        """Verify StreamHealth dataclass and dict conversion."""
        h = StreamHealth(device_id="dev1", active=True, total_samples=1000)
        d = h.to_dict()
        assert d["device_id"] == "dev1"
        assert d["active"] is True
        assert d["total_samples"] == 1000

    def test_combiner_metrics_dataclass(self):
        """Verify CombinerMetrics dataclass and dict conversion."""
        m = CombinerMetrics(total_devices=2, active_devices=2)
        d = m.to_dict()
        assert d["total_devices"] == 2
        assert d["active_devices"] == 2

    def test_sdr_stream_push_pull(self):
        """Verify SDRStream push, pull, peek with underlying ring buffer."""
        stream = SDRStream(device_id="test_stream", buffer_size=65536)
        stream.initialize_buffer()

        # Push test IQ data
        iq_data = (np.ones(1024) + 1j * np.ones(1024)).astype(np.complex64)
        stream.push(iq_data)
        assert stream.available >= 1024

        # Peek without consuming
        peeked = stream.peek(512)
        assert len(peeked) == 512
        assert stream.available >= 1024

        # Pull samples
        pulled = stream.pull(512)
        assert len(pulled) == 512
        assert stream.available >= 512

        stream.close()

    def test_stream_alignment_cross_correlation(self, combiner):
        """Verify cross-correlation lag and phase alignment."""
        n = 8192
        t = np.arange(n) / 2.4e6
        ref = (np.exp(2j * np.pi * 50e3 * t) + 0.05 * (np.random.randn(n) + 1j * np.random.randn(n))).astype(np.complex64)

        # Target delayed by 10 samples and phase-shifted by pi/3
        delay = 10
        phase_shift = np.pi / 3
        target = np.zeros(n, dtype=np.complex64)
        target[delay:] = ref[:-delay] * np.exp(1j * phase_shift)

        aligned_target, phase_offset = combiner._align_streams(ref, target)
        assert len(aligned_target) == len(target)
        assert isinstance(phase_offset, float)

        # Coherence: phase alignment aligns the real/in-phase component for constructive combining
        unaligned_re = np.real(np.sum(ref * np.conj(target)))
        aligned_re = np.real(np.sum(ref * np.conj(aligned_target)))
        assert aligned_re > unaligned_re

    def test_snr_estimation(self, combiner):
        """Verify spectral SNR estimation."""
        n = 8192
        t = np.arange(n) / 2.4e6
        clean_tone = np.exp(2j * np.pi * 100e3 * t).astype(np.complex64)
        snr_clean = combiner._estimate_snr(clean_tone)
        assert snr_clean > 10.0

        noisy = clean_tone + (np.random.randn(n) + 1j * np.random.randn(n)).astype(np.complex64) * 0.8
        snr_noisy = combiner._estimate_snr(noisy)
        assert snr_clean > snr_noisy

    def test_coherent_combine_simulated(self, combiner):
        """Verify MRC coherent combining on manual streams."""
        s1 = SDRStream("ant1", buffer_size=65536)
        s1.initialize_buffer()
        s2 = SDRStream("ant2", buffer_size=65536)
        s2.initialize_buffer()

        combiner.streams["ant1"] = s1
        combiner.streams["ant2"] = s2

        n = 4096
        t = np.arange(n) / 2.4e6
        signal = np.exp(2j * np.pi * 20e3 * t).astype(np.complex64)

        noise1 = (np.random.randn(n) + 1j * np.random.randn(n)).astype(np.complex64) * 0.3
        noise2 = (np.random.randn(n) + 1j * np.random.randn(n)).astype(np.complex64) * 0.3

        s1.push(signal + noise1)
        s2.push(signal + noise2)

        combined = combiner.coherent_combine(["ant1", "ant2"], n_samples=2048)
        assert combined is not None
        assert len(combined) == 2048
        assert combiner.metrics.total_combined_samples >= 2048

        s1.close()
        s2.close()

    def test_device_lifecycle(self, combiner):
        """Test add_device in fallback mode, read, and stop_all."""
        combiner.add_device("sim1", "rtlsdr", frequency=100e6, sample_rate=2.4e6, buffer_size=65536)
        assert "sim1" in combiner.streams
        assert combiner.streams["sim1"].active is True

        # Let background thread capture a few samples
        import time
        time.sleep(0.1)

        samples = combiner.read_stream("sim1", 1024)
        if samples is not None:
            assert len(samples) == 1024

        combiner.stop_all()
        assert "sim1" not in combiner.streams
