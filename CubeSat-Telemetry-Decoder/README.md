<div align="center">

# CubeSat Telemetry Decoder

**Aerospace-Grade Headless Ground Station Framework**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python)](https://python.org)
[![Protocols](https://img.shields.io/badge/Protocols-AX.25%20%7C%20CCSDS%20%7C%20CSP%20%7C%20RAW-orange?style=for-the-badge)]()
[![Modulations](https://img.shields.io/badge/Modulations-BPSK%20%7C%20QPSK%20%7C%20GMSK%20%7C%20LoRa-blue?style=for-the-badge)]()
[![DynamiX Labs](https://img.shields.io/badge/DynamiX-Labs-blueviolet?style=for-the-badge)](https://github.com/DynamiX-Labs)

*A high-performance, API-first pipeline for receiving, tracking, and parsing telemetry beacons from amateur and university CubeSats. Designed for distributed ground station networks.*

</div>

---

## Architecture Overview

`CubeSat-Telemetry-Decoder` is a headless, professional-grade telemetry decoding framework. Moving beyond basic scripting, it provides a comprehensive digital signal processing (DSP) and network parsing pipeline. It integrates directly with Software Defined Radio (SDR) hardware, dynamically tracks orbital bodies, corrects Doppler shifts in real-time via ZeroMQ, applies Forward Error Correction (FEC), and parses complex space protocols.

Designed with an API-first architecture, it outputs clean JSON telemetry streams and validation endpoints, allowing engineers to build custom dashboards, Digital Twins, or integrate into larger mission control systems.

### Core Architecture Pipeline

```mermaid
graph TD
    subgraph RF & Hardware Layer
        A[SDR Hardware] --> B(GNU Radio Flowgraph)
        C[Hamlib / rotctl] -->|Antenna Az/El Tracking| A
    end

    subgraph Ground Station Core
        D[Orbit Engine SGP4/Skyfield] -->|Doppler ZMQ Feed| B
        B --> E[Demodulation & Baseband Classification]
    end

    subgraph Decoding & Parsing Layer
        E --> F[FEC: Viterbi / Reed-Solomon]
        F --> G[Protocol Parser: AX.25/CSP/CCSDS]
        G -->|Cryptographic Verification| H[XTEA / HMAC Engine]
    end

    subgraph Headless APIs & Federation Layer
        H --> I((JSON Telemetry REST API))
        H --> J((ADCS Validation Stream))
        H --> K[MQTT Federation Node]
        K -->|NTP Synced / Hash Deduplicated| L[(Global Packet Network)]
    end
```

---

## Visual Documentation & System Outputs

The framework integrates deeply with RF analysis tools. Below are examples of the pipeline stages from raw RF ingestion to packet decoding.

### RF Ingestion and Signal Processing

The system captures wideband spectrum data and isolates the narrow carrier signals. The GNU Radio layer handles initial baseband filtering and carrier recovery.

<div align="center">
  <img src="../docs/images/sdr_waterfall.jpg" height="300" alt="Wideband SDR Waterfall showing satellite pass">
  <br><i>Figure 1: Wideband SDR waterfall display capturing a LEO satellite pass.</i>
</div>

<br>

<div align="center">
  <img src="../docs/images/spectrum_peak.jpg" height="300" alt="Spectrum Analyzer Peak Isolation">
  <br><i>Figure 2: Baseband spectrum peak isolation prior to demodulation.</i>
</div>

### GNU Radio Integration & Packet Inspection

The ZMQ bridge allows for dynamic control of the GNU Radio flowgraph, updating variables such as center frequency based on the SGP4 Doppler calculations. Once demodulated and deframed, packets are passed to the parser for bit-level extraction.

<div align="center">
  <img src="../docs/images/grc_flowgraph.jpg" height="300" alt="GNU Radio Companion Decoder Flowgraph">
  <br><i>Figure 3: Core GNU Radio Companion flowgraph for QPSK/BPSK demodulation.</i>
</div>

<br>

<div align="center">
  <img src="../docs/images/packet_decode.png" height="300" alt="Packet Decode & Hex Inspection">
  <br><i>Figure 4: Bit-level inspection and hexadecimal output of parsed AX.25 frames.</i>
</div>

---

## Advanced Capabilities

This framework is built to handle the theoretical and practical realities of high-noise, high-Doppler space communications:

*   **Real-Time Doppler & Rig Control**: Uses `sgp4` and `skyfield` for live TLE propagation. Feeds sub-Hertz frequency corrections to GNU Radio via ZMQ, smoothed by an Exponential Moving Average (EMA) closed-loop filter to prevent tuning oscillation.
*   **Robust Signal Recovery**: Incorporates complex Forward Error Correction (FEC) layers (Viterbi and Reed-Solomon) with precise Sync Word detection and frame alignment for bit-slip correction.
*   **Cryptographic Security**: Implements strict XTEA decryption algorithms and HMAC verification for the CubeSat Space Protocol (CSP), followed by a semantic validation layer to reject structurally valid but physically impossible decrypted payloads.
*   **Federation PKI Architecture**: Facilitates the sharing and aggregation of decoded packets globally via MQTT. It enforces strict chronyc-based timestamping and utilizes a full asymmetric Public Key Infrastructure (ECDSA SECP256R1) to digitally sign and verify every telemetry packet, guaranteeing aerospace-grade trust.
*   **Headless Interfaces & Auto-Calibration**: Exposes raw telemetry vectors and ADCS validation streams. Includes an automated Signal Classifier with confidence scoring, and an Anomaly Detector that utilizes a strict learning mode to establish Z-score baselines.

---

## Supported Hardware & Protocols

| Category | Supported Technologies |
| :--- | :--- |
| **SDR Hardware** | RTL-SDR, HackRF, PlutoSDR, USRP, **LimeSDR**, **Airspy** |
| **Modulations** | AFSK, BPSK, **QPSK/8PSK**, **GMSK**, **LoRa** |
| **Protocols** | AX.25 (UI Frames), CCSDS (Space Packet), CSP (GomSpace), Custom RAW |
| **Error Correction** | Reed-Solomon (CCSDS Standard), Viterbi (r=1/2, K=7) |

---

## Quick Start Guide

```bash
git clone https://github.com/DynamiX-Labs/CubeSat-Telemetry-Decoder.git
cd CubeSat-Telemetry-Decoder
pip install -r requirements.txt

# Start the headless background services (Orbit Engine, Rig Control, ZMQ Bridge)
python src/main.py daemon --tle active.txt --rig 127.0.0.1:4532

# Decode from IQ file with auto-detection and automated FEC application
python src/main.py decode --file samples/funcube1.iq --fec auto

# Live decode from SDR with active Doppler correction loop
python src/main.py live --freq 435.800e6 --hardware airspy --satellite "LUCKY-7"

# Launch headless API endpoints (Telemetry, Health, ADCS Stream)
python src/ground_station/server.py --port 8080 --headless
```

---

## Engineering Roadmap

We are currently executing a rigorous three-phase architectural upgrade:

### Phase 1: Core System Foundation
- [x] Orbit & Doppler Engine (SGP4 + Hamlib + EMA Smoothing)
- [x] GNU Radio ZMQ control socket integration
- [x] CSP Cryptography hardened (XTEA/HMAC + Semantic Layer)
- [x] IQ Recording and Raw Replay pipeline

### Phase 2: Reliability & Signal Integrity
- [x] Forward Error Correction (FEC) wrappers & Frame Sync
- [x] MQTT Ground Station Federation (Chrony timing, ECDSA PKI)
- [x] Multi-Protocol Auto-Detection & Confidence Scoring
- [x] Headless Ground Station Health Monitoring API

### Phase 3: Advanced Analytics
- [x] Statistical Telemetry Anomaly Detection (Z-Score baselines + Learning Mode)
- [x] ADCS Validation Data Stream (comparing telemetry vs expected orbit frame)

---

## License

MIT License — Copyright 2026 DynamiX Labs

