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
flowchart TB
    %% Styling Directives
    classDef hardware fill:#1f2937,stroke:#60a5fa,stroke-width:2px,color:#fff
    classDef tracking fill:#065f46,stroke:#34d399,stroke-width:2px,color:#fff
    classDef dsp fill:#1e3a8a,stroke:#93c5fd,stroke-width:2px,color:#fff
    classDef telemetry fill:#4c1d95,stroke:#c4b5fd,stroke-width:2px,color:#fff
    classDef database fill:#78350f,stroke:#fbbf24,stroke-width:2px,color:#fff

    subgraph RF_Layer [RF Frontend Layer]
        HW["fa:fa-broadcast-tower SDR Hardware<br>(RTL-SDR / HackRF / USRP)"]:::hardware
        BM("fa:fa-tachometer-alt SDR-Hardware-Benchmark<br>(DSP Profiling & Verification)"):::hardware
        COH("fa:fa-layer-group Coherent Combiner<br>(Ring Buffer & Synchronization)"):::hardware
        
        HW ==>|Raw IQ| BM
        BM ==>|Filtered IQ| COH
    end

    subgraph Auto_Tracking [Autonomous Tracking Engine]
        TLE[("fa:fa-database CelesTrak TLE<br>Orbit Ephemeris")]:::database
        DT["fa:fa-satellite Doppler-Auto-Tracker<br>(SGP4 / EMA Filter)"]:::tracking
        ANT("fa:fa-crosshairs Antenna Rotator<br>(Hamlib Az/El Control)"):::tracking

        TLE -.->|Update| DT
        DT ==>|Closed-Loop Tuning| HW
        DT ==>|Rotator Commands| ANT
    end

    subgraph DSP_Layer [DSP & Demodulation Pipeline]
        SU["fa:fa-microchip SatSDR-Universal<br>(Multi-Band Extractor)"]:::dsp
        SPEC["fa:fa-wave-square Spectral Intelligence<br>(Welch PSD / Cyclostationary)"]:::dsp
        SYNC["fa:fa-sync Gardner TED Sync<br>& Costas Carrier Recovery"]:::dsp
        FEC["fa:fa-random FEC Decoder<br>(Viterbi / Reed-Solomon)"]:::dsp

        COH ==>|Multi-Band IQ| SU
        SU ==>|SOI Detection| SPEC
        SPEC ==>|Modulation Guess| SYNC
        SYNC ==>|Soft Symbols| FEC
    end

    subgraph Telemetry_Layer [Aerospace Telemetry & Security]
        CTD["fa:fa-shield-alt CubeSat-Telemetry-Decoder<br>(Core Framework)"]:::telemetry
        CRYPTO["fa:fa-key XTEA Decryption<br>& CSP Plausibility"]:::telemetry
        ANOMALY["fa:fa-brain AI Anomaly Detection<br>(Isolation Forest / Thermal)"]:::telemetry
        FED["fa:fa-globe Global PKI Federation<br>(ECDSA SECP256R1 Node)"]:::telemetry

        FEC ==>|Raw Frames| CTD
        CTD ==>|Payload Validation| CRYPTO
        CRYPTO ==>|Sanitized Telemetry| ANOMALY
        CRYPTO ==>|Signed Packets| FED
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

## Formal Verification & Experimental Results

The DynamiX Labs architecture has undergone rigorous testing against live satellite passes. The following results demonstrate the system's ability to digitize, isolate, and dissect complex RF environments into actionable aerospace telemetry.

### Result I: Wideband Spectral Isolation
The spectral intelligence engine automatically identifies and isolates signals of interest (SOI) amidst high-noise RF environments. The waterfall capture below demonstrates real-time baseband filtering and decimation applied to a raw SDR stream.
<div align="center">
  <img src="docs/images/sdr_waterfall.jpg" width="800" alt="Wideband SDR Waterfall Result">
</div>

### Result II: Baseband Demodulation Architecture
For coherent phase tracking, the DSP pipeline utilizes 2nd-order Costas Loops and Gardner Timing Error Detectors. The flowgraph output below illustrates the software-defined translation from raw complex samples to soft-symbol output.
<div align="center">
  <img src="docs/images/grc_flowgraph.jpg" width="800" alt="GNU Radio Companion Decoder Flowgraph Result">
</div>

### Result III: Network-Layer Telemetry Dissection
Post-demodulation, raw frames are validated against the CubeSat Space Protocol (CSP). The capture below shows successful bit-alignment, CRC verification, and XTEA decryption yielding structured network packets ready for PKI federation.
<div align="center">
  <img src="docs/images/packet_decode.png" width="800" alt="Wireshark Packet Decode Result">
</div>

### Result IV: Narrowband Carrier Detection
Using Welch's PSD estimation and adaptive noise floor tracking, the system achieves sub-hertz accuracy on carrier peaks. This allows the Doppler auto-tracker to continuously lock onto drifting LEO satellites without manual frequency intervention.
<div align="center">
  <img src="docs/images/spectrum_peak.jpg" width="800" alt="Spectrum Analyzer Peak Result">
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
