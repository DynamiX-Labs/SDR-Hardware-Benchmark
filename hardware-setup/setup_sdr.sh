#!/usr/bin/env bash
# DynamiX Labs — Automated SDR Driver & Hardware Setup Script
# Supports: RTL-SDR, HackRF, PlutoSDR, USRP, LimeSDR

set -e

echo "=== DynamiX Labs SDR Hardware Setup ==="
echo "This script will install drivers and libraries for all supported SDR hardware."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (using sudo)"
  exit 1
fi

echo "Updating package lists..."
apt-get update

echo "Installing SoapySDR and general prerequisites..."
apt-get install -y soapysdr-tools libsoapysdr-dev python3-soapysdr udev curl

echo "Installing drivers and SoapySDR modules..."

# 1. RTL-SDR
echo "Configuring RTL-SDR..."
apt-get install -y soapysdr-module-rtlsdr rtl-sdr
# Download udev rules for RTL-SDR
if [ ! -f /etc/udev/rules.d/rtl-sdr.rules ]; then
  echo "Downloading RTL-SDR udev rules..."
  curl -sSL https://raw.githubusercontent.com/osmocom/rtl-sdr/master/rtl-sdr.rules -o /etc/udev/rules.d/rtl-sdr.rules || true
fi

# 2. HackRF
echo "Configuring HackRF..."
apt-get install -y soapysdr-module-hackrf hackrf
if [ ! -f /etc/udev/rules.d/53-hackrf.rules ]; then
  echo "Downloading HackRF udev rules..."
  curl -sSL https://raw.githubusercontent.com/greatscottgadgets/hackrf/master/host/libhackrf/53-hackrf.rules -o /etc/udev/rules.d/53-hackrf.rules || true
fi

# 3. PlutoSDR
echo "Configuring PlutoSDR..."
apt-get install -y soapysdr-module-plutosdr libiio-utils libad9361-0

# 4. USRP (UHD)
echo "Configuring USRP..."
apt-get install -y soapysdr-module-uhd uhd-host
echo "Downloading USRP FPGA firmware images..."
uhd_images_downloader || true

# 5. LimeSDR
echo "Configuring LimeSDR..."
apt-get install -y soapysdr-module-lms7 limesuite

# Reload udev rules to apply permissions without rebooting
echo "Reloading udev rules..."
udevadm control --reload-rules
udevadm trigger

echo "=== Setup Complete! ==="
echo "To verify your SDR is detected, plug it in and run:"
echo "  SoapySDRUtil --find"
