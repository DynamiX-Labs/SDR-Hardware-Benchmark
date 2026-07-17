# SDR Hardware Setup Guide

This folder contains the setup guide and automated scripts to configure your ground station's SDR hardware drivers and libraries. 

To ensure the toolkit works seamlessly, your system needs to have **SoapySDR** and the respective hardware driver modules installed.

---

## Automated Installation (Linux / Ubuntu / Debian)

We provide an automated script to install drivers, SoapySDR modules, and configure `udev` rules for USB access (non-root) for all supported devices:

```bash
cd hardware-setup
sudo chmod +x setup_sdr.sh
sudo ./setup_sdr.sh
```

This single script configures all supported software/hardware:
1. **SoapySDR core utilities**
2. **RTL-SDR v3/v4** (drivers + udev rules)
3. **HackRF One** (drivers + udev rules)
4. **ADALM-PLUTO** (`libiio` + Pluto module)
5. **USRP B200/B210/X310** (UHD host drivers + automatic FPGA firmware downloader)
6. **LimeSDR Mini** (LimeSuite)

---

## Verification

Once the installation is complete, plug in your SDR device and verify that SoapySDR detects it correctly by running:

```bash
SoapySDRUtil --find
```

You should see your hardware listed (e.g., `driver=rtlsdr` or `driver=hackrf`).

---

## Windows Setup (Alternative)

If you are running on Windows, follow these quick steps:

1. **RTL-SDR / HackRF / LimeSDR**: Download and run [Zadig](https://zadig.akeo.ie/) to replace the default Windows USB driver with the `WinUSB` driver for your device.
2. **SoapySDR**: Install SoapySDR via the [PothosSDR Development Suite](https://github.com/pothosware/PothosSDR/wiki) installer, which pre-packages all required hardware modules and DLLs for Windows.
3. **USRP**: Install the UHD Windows binaries from the official Ettus Research site.
