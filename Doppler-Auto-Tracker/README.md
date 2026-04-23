<div align="center">

# 🎯 Doppler Auto-Tracker

**TLE-Based Satellite Pass Predictor & Real-Time Doppler Correction**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python)](https://python.org)
[![SGP4](https://img.shields.io/badge/SGP4-Orbital%20Mechanics-green?style=for-the-badge)]()
[![Hardware](https://img.shields.io/badge/Rotator-Hamlib%20Compatible-orange?style=for-the-badge)]()
[![DynamiX Labs](https://img.shields.io/badge/DynamiX-Labs-blueviolet?style=for-the-badge)](https://github.com/DynamiX-Labs)

*Automatic TLE-based Doppler compensation + antenna rotator control for satellite ground stations.*

</div>

---

## 📡 Overview

Doppler-Auto-Tracker combines orbital mechanics (SGP4 propagator) with real-time SDR frequency correction and optional antenna rotator control to keep your ground station locked on any LEO/MEO satellite throughout a full pass.

```
TLE Source (Celestrak/SpaceTrack)
         │
         ▼
   SGP4 Propagator  ←── Ground Station Location (lat/lon/alt)
         │
         ▼
   Pass Predictor ──────────────────────────────────┐
         │                                          │
         ▼                                          ▼
   Doppler Calculator                      Rotator Controller
   (real-time Δf)                          (Az/El via Hamlib)
         │                                          │
         ▼                                          ▼
   SDR Freq Correction                     Antenna Points at SAT
   (SoapySDR / rtl_fm)
```

---

## ⚡ Quick Start

```bash
git clone https://github.com/DynamiX-Labs/Doppler-Auto-Tracker.git
cd Doppler-Auto-Tracker
pip install -r requirements.txt

# Set your ground station location
cp configs/station.yaml.example configs/station.yaml
# Edit: latitude, longitude, altitude, callsign

# Fetch latest TLEs from Celestrak
python src/tle/fetcher.py --source celestrak --group amateur

# Predict next passes for a satellite
python src/tracker/predict.py --sat "NOAA 19" --hours 24

# Start real-time Doppler tracking
python src/tracker/track.py --sat "NOAA 19" --hardware rtlsdr --nominal-freq 137.1e6

# With rotator control (Hamlib)
python src/tracker/track.py --sat "ISS (ZARYA)" --rotator net --rotator-host 127.0.0.1:4533
```

---

## 📊 Pass Prediction Output

```
Ground Station: Chennai, India (13.0827°N, 80.2707°E, 6m ASL)
Satellite: NOAA-19  |  NORAD: 33591

Next 5 Passes:
─────────────────────────────────────────────────────────────────
 #  AOS (UTC)            LOS (UTC)            Max El  Duration
─────────────────────────────────────────────────────────────────
 1  2025-06-15 14:32:18  2025-06-15 14:46:51   68.2°  14m 33s ★
 2  2025-06-15 16:09:44  2025-06-15 16:22:07   41.5°  12m 23s
 3  2025-06-16 02:48:22  2025-06-16 03:01:04   29.1°  12m 42s
 4  2025-06-16 04:25:51  2025-06-16 04:36:12   12.3°  10m 21s
 5  2025-06-16 15:10:03  2025-06-16 15:24:39   72.1°  14m 36s ★

★ = High-quality pass (>50° elevation)
```

---

## 🌐 Doppler Correction Formula

```
f_corrected = f_nominal × (1 + v_radial / c)

Where:
  f_nominal  = Satellite downlink frequency
  v_radial   = Radial velocity (range-rate) observer→satellite [m/s]
  c          = Speed of light (299,792,458 m/s)

For NOAA-19 at 137.1 MHz:
  Max Doppler shift ≈ ±3.4 kHz (@ 7.8 km/s orbital velocity)
```

---

## 🔧 Hardware Integration

| Component | Supported Hardware | Interface |
|---|---|---|
| SDR Receiver | RTL-SDR, HackRF, PlutoSDR, USRP | SoapySDR |
| Antenna Rotator | Yaesu G-5500, GS-232B, Any Hamlib | Hamlib / rigctld |
| Frequency Control | rtl_fm, gqrx, SDR++ | TCP / D-Bus |
| TLE Source | Celestrak, SpaceTrack, local file | HTTP / File |

---

## 📄 License

MIT License — © 2025 DynamiX Labs
