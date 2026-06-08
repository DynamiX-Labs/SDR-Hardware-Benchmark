import pytest
import numpy as np
from src.benchmarks.dsp_benchmark import DSPBenchmark, SDRHardwareBenchmark

@pytest.fixture
def benchmark():
    # Use very small sizes for fast testing
    return DSPBenchmark(n_samples=1024, iterations=5)

def test_bench_fft(benchmark):
    res = benchmark.bench_fft()
    assert res.name.startswith("FFT")
    assert res.iterations == 5
    assert res.mean_ms > 0

def test_bench_decimate(benchmark):
    res = benchmark.bench_decimate(factor=4)
    assert res.name == "Decimate 4×"
    assert res.mean_ms > 0

def test_bench_agc(benchmark):
    res = benchmark.bench_agc()
    assert res.name == "AGC (loop)"
    assert res.mean_ms > 0

def test_hardware_stub_init():
    # It should not crash even if SoapySDR is not installed
    hw = SDRHardwareBenchmark(driver="dummy")
    # If SoapySDR is not installed, hw.sdr will be None.
    # We just ensure it runs without raising ImportError.
    assert hasattr(hw, "sdr")
