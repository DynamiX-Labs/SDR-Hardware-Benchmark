"""
Tests for SDRHardwareBenchmark Suite
DynamiX Labs | Advanced Hardware Integration
"""

import pytest
import numpy as np
from src.benchmarks.dsp_benchmark import SDRHardwareBenchmark, BenchmarkResult


class TestSDRHardwareBenchmark:
    """Test suite for SDRHardwareBenchmark in simulation mode."""

    @pytest.fixture
    def bench(self):
        """Create benchmark instance in simulation mode."""
        b = SDRHardwareBenchmark(driver="rtlsdr", simulate=True)
        yield b
        b.close()

    def test_init_simulation(self, bench):
        """Verify initialization in simulation mode."""
        assert bench._simulated is True
        assert bench.driver == "rtlsdr"

    def test_get_samples(self, bench):
        """Verify simulated IQ sample generation."""
        samples = bench._get_samples(4096, 2.4e6)
        assert isinstance(samples, np.ndarray)
        assert len(samples) == 4096
        assert np.iscomplexobj(samples)
        # Verify samples are normalized and non-zero
        assert np.max(np.abs(samples)) > 0.0

    def test_bench_throughput(self, bench):
        """Test sustained IQ throughput benchmark."""
        res = bench.bench_throughput(rate=2.4e6, duration_s=0.5)
        assert isinstance(res, BenchmarkResult)
        assert "HW Throughput" in res.name
        assert res.throughput > 0
        assert res.mean_ms >= 0
        assert res.category == "hw_throughput"

    def test_bench_dropped_samples(self, bench):
        """Test dropped sample ratio measurement."""
        res = bench.bench_dropped_samples(rate=2.4e6, duration_s=0.5)
        assert isinstance(res, BenchmarkResult)
        assert "HW Drop Rate" in res.name
        assert res.mean_ms >= 0.0  # Drop rate percentage
        assert res.category == "hw_reliability"

    def test_bench_cpu_overhead(self, bench):
        """Test CPU overhead profiling."""
        res = bench.bench_cpu_overhead(rate=2.4e6, duration_s=0.5)
        assert isinstance(res, BenchmarkResult)
        assert "HW CPU Overhead" in res.name
        assert res.category == "hw_system"
        assert res.mean_ms >= 0.0

    def test_bench_memory_bandwidth(self, bench):
        """Test memory bandwidth estimation."""
        res = bench.bench_memory_bandwidth(rate=2.4e6, duration_s=0.5)
        assert isinstance(res, BenchmarkResult)
        assert "HW Memory Bandwidth" in res.name
        assert res.category == "hw_system"
        assert res.mean_ms >= 0.0  # MB/s

    def test_run_all(self, bench):
        """Test full hardware benchmark suite run."""
        results = bench.run_all(rate=2.4e6)
        assert isinstance(results, list)
        assert len(results) == 4
        for r in results:
            assert isinstance(r, BenchmarkResult)
            assert r.name.startswith("HW")

    def test_cleanup_close(self, bench):
        """Test resource release on close."""
        bench.close()
        # Closing multiple times should be safe
        bench.close()
