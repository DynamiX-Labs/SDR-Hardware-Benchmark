<div align="center">

# DynamiX Labs — Satellite SDR Architecture

**Advanced Open-Source Satellite Communication & DSP Framework**

[![DynamiX Labs](https://img.shields.io/badge/DynamiX-Labs-blueviolet?style=for-the-badge)](https://github.com/DynamiX-Labs)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python)](https://python.org)
[![GNU Radio](https://img.shields.io/badge/GNU%20Radio-3.10+-orange?style=for-the-badge)](https://gnuradio.org)

*A comprehensive suite of aerospace-grade signal processing tools, autonomous tracking engines, and cryptography-hardened telemetry decoders built for embedded systems engineers and RF researchers.*

</div>

---

## Master Architecture

The DynamiX Labs suite consists of four interconnected repositories that form a complete, autonomous satellite ground station pipeline. The architecture is designed to handle everything from RF spectrum digitization to secure telemetry federation.

```mermaid
graph TD
    subgraph RF Frontend Layer
        HW[SDR Hardware<br>RTL-SDR / HackRF / USRP] --> BM(SDR-Hardware-Benchmark<br>DSP Profiling & Verification)
        BM --> COH(Coherent Combiner)
    end

    subgraph Autonomous Tracking Engine
        TLE[(CelesTrak TLE)] --> DT[Doppler-Auto-Tracker]
        DT -->|Closed-Loop Tuning| HW
        DT -->|Rotator Control| ANT(Antenna Rotator)
    end

    subgraph DSP & Demodulation Pipeline
        COH -->|Multi-Band IQ Streams| SU(SatSDR-Universal)
        SU -->|Spectral Intelligence| DEMOD[Modulation Classification]
        DEMOD -->|Costas BPSK / QPSK / FM| SYNC[Gardner TED Sync]
        SYNC -->|Soft Symbols| VITERBI[FEC Decoder]
    end

    subgraph Aerospace Telemetry & Security
        VITERBI -->|Raw Frames| CTD(CubeSat-Telemetry-Decoder)
        CTD -->|XTEA Decryption| CSP[CSP Parsing & Plausibility]
        CSP -->|ECDSA Signed PKI| FED[Global Ground Station Federation]
        CTD -->|Isolation Forest| ANOMALY[Anomaly Detection]
    end
```

---

## Core Repositories

| Project Subsystem | Engineering Purpose | Status |
| :--- | :--- | :--- |
| **[SatSDR-Universal](./SatSDR-Universal)** | A hardware-agnostic, spectral-intelligence-driven framework for decoding NOAA APT, ADS-B, CubeSat beacons, GPS, and more. Features autonomous pass scheduling and multi-SDR coherent combining. | Active |
| **[CubeSat-Telemetry-Decoder](./CubeSat-Telemetry-Decoder)** | An aerospace-grade AX.25 / CCSDS / CSP ground station decoder. Includes real-time Doppler EMA filtering, ECDSA PKI federation, and machine learning anomaly detection. | Active |
| **[Doppler-Auto-Tracker](./Doppler-Auto-Tracker)** | TLE-based continuous Doppler correction and closed-loop SDR tuning engine, integrated with Hamlib-compatible antenna rotator control. | Active |
| **[SDR-Hardware-Benchmark](./SDR-Hardware-Benchmark)** | Comprehensive performance benchmarking and DSP profiling tools for hardware including RTL-SDR, HackRF, PlutoSDR, and USRP series. | Active |

---

## System Telemetry & Signal Outputs

The suite provides deep visibility into both the raw RF spectrum and the decoded telemetry layer. Below are visual demonstrations of the system pipeline in production:

<div align="center">
  <img src="docs/images/sdr_waterfall.jpg" height="250" alt="Wideband SDR Waterfall">
  <img src="docs/images/grc_flowgraph.jpg" height="250" alt="GNU Radio Companion Decoder Flowgraph">
  <br><br>
  <img src="docs/images/packet_decode.png" height="250" alt="Wireshark Packet Decode & Hex">
  <img src="docs/images/spectrum_peak.jpg" height="250" alt="Spectrum Analyzer Peak">
</div>

---

## Hardware Support & Integration

The frameworks within this repository are designed to be hardware-agnostic, utilizing `SoapySDR` to interface seamlessly with a wide range of platforms:

- **RTL-SDR v3 / v4** (VHF/UHF Weather, ADS-B)
- **HackRF One** (Wideband scanning, Tx/Rx)
- **ADALM-PLUTO** (L-band, Tx/Rx capable)
- **USRP B200 / B210** (Full duplex, MIMO, HRPT)
- **USRP X310** (High-performance research)
- **LimeSDR Mini** (Multi-protocol)

---

## Supported Protocols & Signals

- **Weather**: NOAA APT (137 MHz), METEOR LRPT (137.1 MHz), NOAA HRPT
- **Aviation**: ADS-B 1090ES, ACARS (129.125 MHz)
- **Spacecraft**: CubeSat AX.25, CCSDS, CSP (CubeSat Space Protocol)
- **Navigation & Comms**: GPS L1 C/A, Inmarsat (AERO/STD-C), Iridium

---

## Authors & Contributors

- **[@ARYA-mgc](https://github.com/ARYA-mgc)** - *Lead Developer / DSP Engineer*
- **[@vishal-r07](https://github.com/vishal-r07)** - *Contributor*

---

<div align="center">
  © 2026 DynamiX Labs — Released under the MIT License
</div>
