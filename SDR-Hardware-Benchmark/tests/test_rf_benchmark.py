"""
Tests for RF Performance Benchmark Suite
DynamiX Labs | Advanced Hardware Integration
"""

import pytest
import numpy as np
from src.benchmarks.rf_benchmark import (
    RFBenchmarkSuite,
    RFBenchmarkReport,
    RFBenchmarkResult,
    THRESHOLDS,
)


class TestRFBenchmarkSuite:
    """Test suite for RFBenchmarkSuite in simulation mode."""

    @pytest.fixture
    def suite(self):
        """Create RF benchmark suite instance in simulation mode."""
        s = RFBenchmarkSuite(
            driver="rtlsdr",
            frequency=100e6,
            sample_rate=2.4e6,
            gain=30.0,
            n_samples=32768,
            simulate=True,
        )
        yield s
        s.close()

    def test_init_simulation(self, suite):
        """Verify simulation mode initialization and budget tier defaults."""
        assert suite._simulated is True
        assert suite.driver == "rtlsdr"
        assert suite._tier == "budget"
        assert suite._thresholds == THRESHOLDS["budget"]

    def test_simulate_samples(self, suite):
        """Verify simulated RF sample generation characteristics."""
        samples = suite._get_samples(16384)
        assert isinstance(samples, np.ndarray)
        assert len(samples) == 16384
        assert np.iscomplexobj(samples)
        # Power should be reasonable (not all zeros or NaNs)
        pwr = np.mean(np.abs(samples) ** 2)
        assert 0.0 < pwr < 10.0
        assert not np.isnan(pwr)

    def test_bench_snr(self, suite):
        """Verify SNR measurement returns valid RFBenchmarkResult."""
        res = suite.bench_snr()
        assert isinstance(res, RFBenchmarkResult)
        assert res.name == "Signal-to-Noise Ratio"
        assert res.unit == "dB"
        assert res.category == "signal_quality"
        assert "measurements" in res.details
        assert "min_dB" in res.details
        assert res.value > 0.0

    def test_bench_sfdr(self, suite):
        """Verify SFDR calculation."""
        res = suite.bench_sfdr()
        assert isinstance(res, RFBenchmarkResult)
        assert res.name == "SFDR"
        assert res.unit == "dB"
        assert res.category == "dynamic_range"
        assert res.value > 0.0

    def test_bench_enob(self, suite):
        """Verify Effective Number of Bits (ENOB) calculation."""
        res = suite.bench_enob()
        assert isinstance(res, RFBenchmarkResult)
        assert res.name == "ENOB"
        assert res.unit == "bits"
        assert res.value >= 0.0

    def test_bench_phase_noise(self, suite):
        """Verify phase noise at frequency offsets."""
        offsets = [1000.0, 10000.0]
        results = suite.bench_phase_noise(offsets_hz=offsets)
        assert len(results) == 2
        for r in results:
            assert isinstance(r, RFBenchmarkResult)
            assert "Phase Noise" in r.name
            assert r.unit == "dBc/Hz"
            assert r.value < 0.0  # Phase noise in dBc/Hz is negative

    def test_bench_frequency_accuracy(self, suite):
        """Verify frequency error measurement in PPM."""
        res = suite.bench_frequency_accuracy()
        assert isinstance(res, RFBenchmarkResult)
        assert res.name == "Frequency Accuracy"
        assert res.unit == "PPM"
        assert res.value >= 0.0

    def test_bench_iq_impairments(self, suite):
        """Verify DC offset and IQ imbalance estimation."""
        dc_res = suite.bench_dc_offset()
        assert isinstance(dc_res, RFBenchmarkResult)
        assert dc_res.name == "DC Offset"
        assert dc_res.unit == "V"

        iq_res = suite.bench_iq_imbalance()
        assert isinstance(iq_res, RFBenchmarkResult)
        assert iq_res.name == "IQ Amplitude Imbalance"
        assert iq_res.unit == "dB"

        img_res = suite.bench_image_rejection()
        assert isinstance(img_res, RFBenchmarkResult)
        assert img_res.name == "Image Rejection Ratio"
        assert img_res.unit == "dB"

    def test_run_all_and_report(self, suite):
        """Verify complete suite execution and report serialization."""
        report = suite.run_all()
        assert isinstance(report, RFBenchmarkReport)
        assert len(report.results) >= 10
        assert report.device_name != ""
        assert report.driver == "rtlsdr"

        # Check summary formatting
        summary = report.summary()
        assert "DynamiX Labs" in summary
        assert "Assessment" in summary

        # Check JSON-serializable dictionary
        d = report.to_dict()
        assert isinstance(d, dict)
        assert "results" in d
        assert len(d["results"]) == len(report.results)
        # Verify JSON compatibility
        import json
        serialized = json.dumps(d)
        assert len(serialized) > 100
