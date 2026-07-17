"""
Tests for GPU-Accelerated DSP Backend
DynamiX Labs | Phase 3

Tests CPU fallback path (always available) and verifies numerical
equivalence with NumPy/SciPy reference implementations.
"""

import numpy as np
import pytest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────
@pytest.fixture
def gpu_backend():
    """Create a GPUBackend instance (will use CPU fallback if no CUDA)."""
    from dsp.gpu_backend import GPUBackend
    return GPUBackend()


@pytest.fixture
def sample_iq():
    """Generate synthetic IQ test signal: carrier + noise."""
    np.random.seed(42)
    n = 16384
    fs = 250_000
    t = np.arange(n, dtype=np.float32) / fs
    carrier = np.exp(2j * np.pi * 25_000 * t).astype(np.complex64)
    noise = (np.random.randn(n) + 1j * np.random.randn(n)).astype(np.complex64) * 0.1
    return carrier + noise


@pytest.fixture
def fir_taps():
    """Generate a basic FIR lowpass filter."""
    from scipy.signal import firwin
    return firwin(63, 50_000 / (250_000 / 2)).astype(np.float32)


# ──────────────────────────────────────────────────────────────────────
# GPU Backend Initialisation
# ──────────────────────────────────────────────────────────────────────
class TestGPUBackendInit:
    """Test GPU backend construction and probing."""

    def test_backend_creates_without_error(self, gpu_backend):
        """Backend should instantiate regardless of CUDA availability."""
        assert gpu_backend is not None
        assert isinstance(gpu_backend.gpu_available, bool)

    def test_metrics_initialised(self, gpu_backend):
        """Metrics should be zero on fresh backend."""
        m = gpu_backend.metrics.snapshot()
        assert m["fft_calls"] == 0
        assert m["fir_calls"] == 0
        assert m["psd_calls"] == 0

    def test_context_manager(self):
        """Backend should work as context manager."""
        from dsp.gpu_backend import GPUBackend
        with GPUBackend() as gpu:
            assert gpu is not None


# ──────────────────────────────────────────────────────────────────────
# FFT Tests
# ──────────────────────────────────────────────────────────────────────
class TestFFT:
    """Test FFT correctness against NumPy reference."""

    def test_fft_basic(self, gpu_backend, sample_iq):
        """FFT output should match NumPy reference within tolerance."""
        result = gpu_backend.fft(sample_iq)
        reference = np.fft.fft(sample_iq)
        np.testing.assert_allclose(np.abs(result), np.abs(reference), rtol=1e-3)

    def test_fft_with_n(self, gpu_backend, sample_iq):
        """FFT with explicit length should work."""
        n = 8192
        result = gpu_backend.fft(sample_iq[:n], n=n)
        reference = np.fft.fft(sample_iq[:n], n=n)
        assert result.shape == reference.shape
        np.testing.assert_allclose(np.abs(result), np.abs(reference), rtol=1e-3)

    def test_ifft(self, gpu_backend, sample_iq):
        """IFFT should be the inverse of FFT."""
        spectrum = gpu_backend.fft(sample_iq)
        recovered = gpu_backend.ifft(spectrum)
        np.testing.assert_allclose(
            np.abs(recovered[:len(sample_iq)]),
            np.abs(sample_iq),
            rtol=1e-3
        )

    def test_fft_increments_counter(self, gpu_backend, sample_iq):
        """FFT call count metric should increment."""
        initial = gpu_backend.metrics.total_fft_calls
        gpu_backend.fft(sample_iq)
        assert gpu_backend.metrics.total_fft_calls == initial + 1


# ──────────────────────────────────────────────────────────────────────
# Batch FFT Tests
# ──────────────────────────────────────────────────────────────────────
class TestBatchFFT:
    """Test batch FFT processing."""

    def test_batch_fft_empty(self, gpu_backend):
        """Empty list should return empty list."""
        assert gpu_backend.batch_fft([]) == []

    def test_batch_fft_dimensions(self, gpu_backend, sample_iq):
        """Batch FFT should return same number of results as inputs."""
        chunks = [sample_iq[:1024], sample_iq[1024:2048], sample_iq[2048:3072]]
        results = gpu_backend.batch_fft(chunks)
        assert len(results) == 3

    def test_batch_fft_correctness(self, gpu_backend, sample_iq):
        """Each batch FFT result should match individual FFT."""
        chunk = sample_iq[:1024]
        batch_result = gpu_backend.batch_fft([chunk])[0]
        single_result = gpu_backend.fft(chunk)
        np.testing.assert_allclose(
            np.abs(batch_result[:1024]),
            np.abs(single_result[:1024]),
            rtol=1e-2
        )


# ──────────────────────────────────────────────────────────────────────
# FIR Filter Tests
# ──────────────────────────────────────────────────────────────────────
class TestFIRFilter:
    """Test FIR filtering correctness."""

    def test_fir_filter_output_shape(self, gpu_backend, sample_iq, fir_taps):
        """Filtered output should have same length as input (mode=same)."""
        result = gpu_backend.fir_filter(sample_iq, fir_taps)
        assert len(result) == len(sample_iq)

    def test_fir_filter_dtype_preserved(self, gpu_backend, sample_iq, fir_taps):
        """Output dtype should match input dtype."""
        result = gpu_backend.fir_filter(sample_iq, fir_taps)
        assert result.dtype == sample_iq.dtype

    def test_fir_filter_increments_counter(self, gpu_backend, sample_iq, fir_taps):
        """FIR call count metric should increment."""
        initial = gpu_backend.metrics.total_fir_calls
        gpu_backend.fir_filter(sample_iq, fir_taps)
        assert gpu_backend.metrics.total_fir_calls == initial + 1

    def test_fir_filter_reduces_noise(self, gpu_backend, fir_taps):
        """LPF should attenuate high-frequency noise."""
        np.random.seed(123)
        n = 4096
        t = np.arange(n, dtype=np.float32) / 250_000
        # Low-freq signal + high-freq noise
        signal = np.exp(2j * np.pi * 10_000 * t).astype(np.complex64)
        noise = np.exp(2j * np.pi * 100_000 * t).astype(np.complex64) * 0.5
        mixed = signal + noise

        filtered = gpu_backend.fir_filter(mixed, fir_taps)
        # After LPF, power at high freq should be reduced
        assert np.std(filtered) < np.std(mixed)


# ──────────────────────────────────────────────────────────────────────
# Welch PSD Tests
# ──────────────────────────────────────────────────────────────────────
class TestWelchPSD:
    """Test Welch PSD estimation."""

    def test_welch_psd_returns_two_arrays(self, gpu_backend, sample_iq):
        """Should return (freqs, psd_db) tuple."""
        freqs, psd_db = gpu_backend.welch_psd(sample_iq, fs=250_000)
        assert isinstance(freqs, np.ndarray)
        assert isinstance(psd_db, np.ndarray)

    def test_welch_psd_shape(self, gpu_backend, sample_iq):
        """PSD output shape should match FFT size."""
        nperseg = 1024
        freqs, psd_db = gpu_backend.welch_psd(sample_iq, fs=250_000, nperseg=nperseg)
        assert len(freqs) == nperseg
        assert len(psd_db) == nperseg

    def test_welch_psd_peak_detection(self, gpu_backend, sample_iq):
        """PSD should show a peak at the carrier frequency."""
        freqs, psd_db = gpu_backend.welch_psd(sample_iq, fs=250_000, nperseg=4096)
        peak_idx = np.argmax(psd_db)
        peak_freq = freqs[peak_idx]
        # Carrier is at 25 kHz
        assert abs(peak_freq - 25_000) < 500  # within 500 Hz

    def test_welch_psd_increments_counter(self, gpu_backend, sample_iq):
        """PSD call count metric should increment."""
        initial = gpu_backend.metrics.total_psd_calls
        gpu_backend.welch_psd(sample_iq, fs=250_000)
        assert gpu_backend.metrics.total_psd_calls == initial + 1


# ──────────────────────────────────────────────────────────────────────
# Correlation Tests
# ──────────────────────────────────────────────────────────────────────
class TestCorrelation:
    """Test cross-correlation."""

    def test_autocorrelation_peak(self, gpu_backend):
        """Autocorrelation of a signal should peak at zero lag."""
        np.random.seed(99)
        x = np.random.randn(256).astype(np.float32)
        result = gpu_backend.correlate(x, x, mode="full")
        peak_idx = np.argmax(np.abs(result))
        expected_peak = len(x) - 1  # zero-lag index in 'full' mode
        assert peak_idx == expected_peak


# ──────────────────────────────────────────────────────────────────────
# Metrics Snapshot
# ──────────────────────────────────────────────────────────────────────
class TestMetrics:
    """Test performance metrics reporting."""

    def test_snapshot_is_dict(self, gpu_backend):
        """Metrics snapshot should be a JSON-serialisable dict."""
        m = gpu_backend.metrics.snapshot()
        assert isinstance(m, dict)
        assert "fft_calls" in m
        assert "gpu" in m

    def test_cumulative_metrics(self, gpu_backend, sample_iq):
        """Metrics should accumulate across multiple calls."""
        gpu_backend.fft(sample_iq)
        gpu_backend.fft(sample_iq)
        assert gpu_backend.metrics.total_fft_calls >= 2
