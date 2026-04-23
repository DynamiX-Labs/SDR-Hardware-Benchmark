<div align="center">

# 📊 SDR Hardware Benchmark

**Performance Profiler for RTL-SDR · HackRF · PlutoSDR · USRP**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python)](https://python.org)
[![C++](https://img.shields.io/badge/C%2B%2B-17-00599C?style=for-the-badge&logo=cplusplus)](https://isocpp.org)
[![Benchmark](https://img.shields.io/badge/Benchmarks-DSP%20%7C%20RF%20%7C%20CPU-green?style=for-the-badge)]()
[![DynamiX Labs](https://img.shields.io/badge/DynamiX-Labs-blueviolet?style=for-the-badge)](https://github.com/DynamiX-Labs)

*Comprehensive SDR hardware performance benchmarking — throughput, latency, dynamic range, CPU load.*

</div>

---

## 📡 Overview

SDR-Hardware-Benchmark measures the real-world performance of SDR hardware and host platforms across multiple dimensions: sample throughput, DSP processing speed, phase noise, dynamic range, and CPU/memory load.

---

## 🏆 Sample Results (DynamiX Labs Reference)

```
═══════════════════════════════════════════════════════════════════════
  SDR Hardware Benchmark v1.0 — DynamiX Labs
═══════════════════════════════════════════════════════════════════════
  Platform: Raspberry Pi 5 (4-core A76 @ 2.4 GHz, 8GB RAM)
  OS: Ubuntu 24.04 ARM64 | Python 3.11 | NumPy 1.26
───────────────────────────────────────────────────────────────────────

HARDWARE       SAMPLE RATE   THROUGHPUT   CPU%   DROPPED   NOISE FIG
───────────────────────────────────────────────────────────────────────
RTL-SDR v3     2.4 MSPS      2.38 MSPS    38%    0.02%     6.2 dB
RTL-SDR v3     2.0 MSPS      1.99 MSPS    29%    0.00%     6.1 dB ✓
HackRF One     8.0 MSPS      7.89 MSPS    62%    0.41%     9.8 dB
HackRF One     4.0 MSPS      3.97 MSPS    41%    0.00%     9.6 dB ✓
ADALM-PLUTO    5.0 MSPS      4.96 MSPS    45%    0.00%     8.1 dB ✓
USRP B200      20.0 MSPS     19.8 MSPS    71%    0.08%     5.1 dB

DSP BENCHMARK (FFT, Filter, Decimation — pure NumPy/SciPy):
  FFT 65536-pt:     2.8 ms    (357 FFT/s)
  FIR 127-tap:      1.2 ms    (833 op/s)
  Decimate 8×:      0.4 ms    (2500 op/s)
  FM Demod:         0.9 ms    (1111 op/s)
═══════════════════════════════════════════════════════════════════════
```

---

## ⚡ Quick Start

```bash
git clone https://github.com/DynamiX-Labs/SDR-Hardware-Benchmark.git
cd SDR-Hardware-Benchmark
pip install -r requirements.txt

# Run full benchmark suite (all available hardware)
python src/main.py --all

# Benchmark specific hardware
python src/main.py --hardware rtlsdr --duration 60

# DSP-only benchmark (no hardware needed)
python src/main.py --dsp-only --iterations 1000

# Export results to CSV/JSON
python src/main.py --hardware rtlsdr --output results/pi5_rtlsdr.json

# Compare two result files
python src/reports/compare.py results/pi5_rtlsdr.json results/x86_rtlsdr.json
```

---

## 📋 Benchmark Suite

| Test | Measures | Duration |
|---|---|---|
| **Sample Throughput** | Max sustained SPS, drop rate | 30s |
| **DSP Pipeline** | FFT, FIR, decimation timing | 1000 iters |
| **Frequency Accuracy** | Offset vs reference | 30s |
| **Phase Noise** | SSB phase noise @ 10 kHz offset | 10s |
| **Dynamic Range** | SFDR, SNR, noise floor | 20s |
| **CPU Load** | CPU%, memory, temp vs sample rate | 60s |
| **Latency** | Input-to-output pipeline latency | 500 iters |

---

## 📄 License

MIT License — © 2025 DynamiX Labs
