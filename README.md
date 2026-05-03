<div align="center">

# DynamiX Labs — Satellite SDR Projects

**Advanced Open-Source Satellite Communication & SDR Tools**

[![DynamiX Labs](https://img.shields.io/badge/DynamiX-Labs-blueviolet?style=for-the-badge)](https://github.com/DynamiX-Labs)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python)](https://python.org)
[![GNU Radio](https://img.shields.io/badge/GNU%20Radio-3.10+-orange?style=for-the-badge)](https://gnuradio.org)

*A suite of aerospace-grade signal processing tools, decoders, and tracking engines built for embedded systems engineers and RF researchers.*

</div>

---

## 📦 Core Projects

Our repository encompasses four core projects designed to provide an end-to-end pipeline for satellite tracking, reception, and telemetry decoding:

| Project | Description | Status |
| :--- | :--- | :--- |
| **[SatSDR-Universal](./SatSDR-Universal)** | A hardware-agnostic, spectral-intelligence-driven framework for decoding NOAA APT, ADS-B, CubeSat beacons, GPS, and more. Features autonomous pass scheduling and multi-SDR coherent combining. | Active |
| **[CubeSat-Telemetry-Decoder](./CubeSat-Telemetry-Decoder)** | An aerospace-grade AX.25 / CCSDS / CSP ground station decoder. Includes real-time Doppler EMA filtering, ECDSA PKI federation, and machine learning anomaly detection. | Active |
| **[Doppler-Auto-Tracker](./Doppler-Auto-Tracker)** | TLE-based continuous Doppler correction and closed-loop SDR tuning engine, integrated with Hamlib-compatible antenna rotator control. | Active |
| **[SDR-Hardware-Benchmark](./SDR-Hardware-Benchmark)** | Comprehensive performance benchmarking and DSP profiling tools for hardware including RTL-SDR, HackRF, PlutoSDR, and USRP series. | Active |

---

## 🔬 System Capabilities & Outputs

The DynamiX Labs suite provides deep visibility into the RF spectrum and the decoded telemetry layer. Below are examples of our system outputs, ranging from raw DSP pipelines to decoded network frames.

<div align="center">
  <img src="docs/images/sdr_waterfall.jpg" height="250" alt="Wideband SDR Waterfall">
  <img src="docs/images/grc_flowgraph.jpg" height="250" alt="GNU Radio Companion Decoder Flowgraph">
  <br><br>
  <img src="docs/images/packet_decode.png" height="250" alt="Wireshark Packet Decode & Hex">
  <img src="docs/images/spectrum_peak.jpg" height="250" alt="Spectrum Analyzer Peak">
</div>

---

## 🔧 Hardware Support

The frameworks within this repository are designed to be hardware-agnostic, utilizing `SoapySDR` to interface seamlessly with a wide range of platforms:

- **RTL-SDR v3 / v4**
- **HackRF One**
- **ADALM-PLUTO (PlutoSDR)**
- **USRP B200 / B210**
- **USRP X310**
- **LimeSDR Mini**

---

## 📡 Supported Protocols & Signals

- **Weather**: NOAA APT (137 MHz), METEOR LRPT (137.1 MHz), NOAA HRPT
- **Aviation**: ADS-B 1090ES, ACARS (129.125 MHz)
- **Spacecraft**: CubeSat AX.25, CCSDS, CSP (CubeSat Space Protocol)
- **Navigation & Comms**: GPS L1 C/A, Inmarsat (AERO/STD-C), Iridium

---

## 👥 Authors & Contributors

- **[@ARYA-mgc](https://github.com/ARYA-mgc)** - *Lead Developer / DSP Engineer*
- **[@vishal-r07](https://github.com/vishal-r07)** - *Contributor*

---

<div align="center">
  © 2026 DynamiX Labs — Released under the MIT License
</div>
