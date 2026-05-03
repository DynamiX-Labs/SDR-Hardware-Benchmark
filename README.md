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
    %% Core Styling Directives
    classDef hardware fill:#0f172a,stroke:#3b82f6,stroke-width:2px,color:#e2e8f0,stroke-dasharray: 5 5
    classDef tracking fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#e2e8f0
    classDef dsp fill:#1e3a8a,stroke:#60a5fa,stroke-width:2px,color:#e2e8f0
    classDef telemetry fill:#312e81,stroke:#a78bfa,stroke-width:2px,color:#e2e8f0
    classDef ai fill:#4a044e,stroke:#f472b6,stroke-width:2px,color:#e2e8f0
    classDef database fill:#451a03,stroke:#fbbf24,stroke-width:2px,color:#e2e8f0
    classDef external fill:#171717,stroke:#a3a3a3,stroke-width:2px,color:#d4d4d4

    %% External Systems
    subgraph External_Network [Global & Space Interfaces]
        direction LR
        SAT((fa:fa-satellite Low Earth Orbit<br>Satellites)):::external
        TLE[("fa:fa-cloud CelesTrak / Space-Track<br>REST API (HTTPS)")]:::database
        FED_NET(("fa:fa-network-wired DynamiX Federation<br>Decentralized Nodes")):::external
    end

    %% RF Frontend
    subgraph RF_Layer [L0: RF Frontend & Digitization]
        direction TB
        ANT("fa:fa-satellite-dish Az/El Yagi Array<br>(VHF/UHF/L-Band)"):::hardware
        LNA("fa:fa-bolt Low Noise Amplifier<br>(NF < 0.5dB)"):::hardware
        HW["fa:fa-microchip SDR Digitizer<br>(RTL-SDR / HackRF / USRP)"]:::hardware
        BM("fa:fa-stopwatch Hardware Benchmark<br>Zero-Copy Memory Access"):::hardware
        COH("fa:fa-layer-group Coherent Combiner<br>Ring Buffer (ZMQ IPC)"):::hardware
        
        ANT -- RF Analog --> LNA
        LNA -- Amplified RF --> HW
        HW == "Complex64 IQ (USB 3.0)" ==> BM
        BM == "Stream Filter" ==> COH
    end

    %% Tracking Engine
    subgraph Auto_Tracking [L1: Autonomous Pass Engine]
        direction TB
        DT["fa:fa-compass Doppler-Auto-Tracker<br>Skyfield / SGP4 Predictor"]:::tracking
        PID["fa:fa-cogs PID Rotator Controller<br>(Hamlib Protocol)"]:::tracking
        EMA["fa:fa-wave-square EMA Doppler Filter<br>Continuous Tuning"]:::tracking

        DT -->|Target Vector| PID
        DT -->|Shift Hz| EMA
    end

    %% DSP Pipeline
    subgraph DSP_Layer [L2: Digital Signal Processing]
        direction TB
        SU["fa:fa-filter SatSDR-Universal<br>Multi-Band Channelizer"]:::dsp
        SPEC["fa:fa-chart-bar Spectral Engine<br>Welch PSD & Auto-Detect"]:::dsp
        SYNC["fa:fa-sync Carrier/Symbol Sync<br>Costas Loop & Gardner TED"]:::dsp
        FEC["fa:fa-random FEC Decoder<br>Viterbi / Reed-Solomon"]:::dsp

        COH == "Multi-Band IQ (20 MSPS)" ==> SU
        SU == "Isolated Baseband" ==> SPEC
        SPEC == "Modulation Class" ==> SYNC
        SYNC == "Soft Symbols" ==> FEC
    end

    %% Telemetry & Security
    subgraph Telemetry_Layer [L3: Telemetry, AI, & Security]
        direction TB
        CTD["fa:fa-shield-alt CubeSat-Telemetry-Decoder<br>Deframer (Sync Word)"]:::telemetry
        CRYPTO["fa:fa-key Cryptography Engine<br>XTEA Decryption & CSP"]:::telemetry
        ANOMALY["fa:fa-brain AI Anomaly Detection<br>Isolation Forest (TensorFlow)"]:::ai
        PKI["fa:fa-lock ECDSA PKI Signer<br>SECP256R1 Private Key"]:::telemetry

        FEC == "Raw Bitstream" ==> CTD
        CTD == "KISS / CSP Frames" ==> CRYPTO
        CRYPTO == "Parsed Telemetry" ==> ANOMALY
        CRYPTO == "Verified Payload" ==> PKI
    end

    %% Cross-Layer Integrations
    SAT -. "137MHz - 2.4GHz" .-> ANT
    TLE -. "Daily Sync" .-> DT
    EMA == "Freq Offset" ==> HW
    PID == "Az/El Serial" ==> ANT
    PKI == "Signed JSON / ZMQ" ==> FED_NET
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
