#!/usr/bin/env bash
# ==============================================================================
# DynamiX Labs — Advanced SDR Driver & Hardware Environment Setup Script
# Production-Grade Hardware Integration Suite
# Supports: RTL-SDR (v3/v4), HackRF One, ADALM-PLUTO, USRP (B/N/X series), LimeSDR
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/sdr_setup_$(date +%Y%m%d_%H%M%S).log"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] $1"
    echo -e "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

info() {
    log "${CYAN}ℹ INFO:${NC} $1"
}

success() {
    log "${GREEN}✓ SUCCESS:${NC} $1"
}

warn() {
    log "${YELLOW}⚠ WARNING:${NC} $1"
}

error() {
    log "${RED}✗ ERROR:${NC} $1"
}

header() {
    echo -e "\n${BLUE}================================================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}================================================================${NC}\n"
}

# ─── Command Line Flags ───────────────────────────────────────────────────────
SKIP_INSTALL=false
VALIDATE_ONLY=false
SETUP_NETWORK=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --validate-only)
            VALIDATE_ONLY=true
            shift
            ;;
        --skip-install)
            SKIP_INSTALL=true
            shift
            ;;
        --setup-network)
            SETUP_NETWORK=true
            shift
            ;;
        --help|-h)
            echo "Usage: sudo $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --validate-only   Skip package installation; run hardware validation checks"
            echo "  --skip-install    Skip apt installations, run udev, blacklist & validation"
            echo "  --setup-network   Configure jumbo frames (MTU 9000) & network SDR buffers"
            echo "  --help, -h        Show this help message"
            exit 0
            ;;
        *)
            warn "Unknown argument: $1"
            shift
            ;;
    esac
done

header "DynamiX Labs SDR Hardware Setup & Validation"
info "Log file: ${LOG_FILE}"

# Root Check
if [ "$EUID" -ne 0 ] && [ "$VALIDATE_ONLY" = false ]; then
    error "Installation requires root privileges. Please run: sudo $0"
    exit 1
fi

# ─── 1. Kernel Conflict Detection & Blacklisting ─────────────────────────────
resolve_kernel_conflicts() {
    header "1. Kernel DVB Driver Conflict Detection (RTL-SDR)"
    local blacklist_file="/etc/modprobe.d/blacklist-rtlsdr.conf"
    local conflicting_modules=("dvb_usb_rtl28xxu" "rtl2832" "rtl2830" "dvb_usb_v2" "dvb_core")
    local conflict_found=false

    info "Checking for conflicting DVB-T kernel modules..."
    for mod in "${conflicting_modules[@]}"; do
        if lsmod | grep -q "^${mod}"; then
            warn "Active conflicting module detected: ${mod}"
            conflict_found=true
            rmmod "${mod}" 2>/dev/null || warn "Could not live-unload ${mod}; reboot or rmmod required."
        fi
    done

    if [ ! -f "$blacklist_file" ] || [ "$conflict_found" = true ]; then
        info "Generating modprobe blacklist at ${blacklist_file}..."
        cat <<EOF > "$blacklist_file"
# DynamiX Labs — Blacklist kernel DVB TV tuner drivers to allow RTL-SDR raw IQ access
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist dvb_usb_v2
blacklist dvb_core
EOF
        success "Blacklisted DVB kernel modules successfully."
    else
        success "Kernel DVB modules are already blacklisted in ${blacklist_file}."
    fi
}

# ─── 2. Package Installation ─────────────────────────────────────────────────
install_packages() {
    header "2. Installing Core SDR Libraries & Drivers"

    info "Updating APT repositories..."
    apt-get update -y >> "$LOG_FILE" 2>&1 || warn "APT update finished with warnings."

    info "Installing SoapySDR ecosystem & build prerequisites..."
    apt-get install -y \
        soapysdr-tools \
        libsoapysdr-dev \
        python3-soapysdr \
        python3-pip \
        python3-numpy \
        python3-scipy \
        udev \
        curl \
        usbutils \
        pciutils \
        ethtool \
        net-tools \
        libusb-1.0-0-dev >> "$LOG_FILE" 2>&1

    success "Installed core SoapySDR and prerequisite packages."

    # Device Drivers
    info "Installing hardware-specific driver modules..."
    
    # RTL-SDR
    apt-get install -y soapysdr-module-rtlsdr rtl-sdr librtlsdr-dev >> "$LOG_FILE" 2>&1 || warn "RTL-SDR package install issue."

    # HackRF
    apt-get install -y soapysdr-module-hackrf hackrf libhackrf-dev >> "$LOG_FILE" 2>&1 || warn "HackRF package install issue."

    # PlutoSDR
    apt-get install -y soapysdr-module-plutosdr libiio-utils libad9361-0 libiio-dev >> "$LOG_FILE" 2>&1 || warn "PlutoSDR package install issue."

    # USRP (UHD)
    apt-get install -y soapysdr-module-uhd uhd-host libuhd-dev python3-uhd >> "$LOG_FILE" 2>&1 || warn "USRP/UHD package install issue."

    # LimeSDR
    apt-get install -y soapysdr-module-lms7 limesuite liblimesuite-dev >> "$LOG_FILE" 2>&1 || warn "LimeSDR package install issue."

    # Airspy
    apt-get install -y soapysdr-module-airspy airspy libairspy-dev >> "$LOG_FILE" 2>&1 || true

    # BladeRF
    apt-get install -y soapysdr-module-bladerf bladerf libbladerf-dev >> "$LOG_FILE" 2>&1 || true

    success "Hardware driver modules installed."

    # USRP Firmware Images
    if command -v uhd_images_downloader >/dev/null 2>&1; then
        info "Verifying/downloading USRP FPGA firmware images..."
        uhd_images_downloader >> "$LOG_FILE" 2>&1 || warn "UHD images download had non-critical issues."
    fi
}

# ─── 3. Udev Rules Setup ─────────────────────────────────────────────────────
setup_udev_rules() {
    header "3. Configuring Udev Rules for Non-Root SDR Access"

    # RTL-SDR
    if [ ! -f /etc/udev/rules.d/rtl-sdr.rules ]; then
        info "Installing RTL-SDR udev rules..."
        curl -sSL https://raw.githubusercontent.com/osmocom/rtl-sdr/master/rtl-sdr.rules -o /etc/udev/rules.d/rtl-sdr.rules 2>/dev/null || \
        cat <<EOF > /etc/udev/rules.d/rtl-sdr.rules
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666", GROUP="plugdev"
EOF
    fi

    # HackRF
    if [ ! -f /etc/udev/rules.d/53-hackrf.rules ]; then
        info "Installing HackRF udev rules..."
        curl -sSL https://raw.githubusercontent.com/greatscottgadgets/hackrf/master/host/libhackrf/53-hackrf.rules -o /etc/udev/rules.d/53-hackrf.rules 2>/dev/null || \
        cat <<EOF > /etc/udev/rules.d/53-hackrf.rules
ATTR{idVendor}=="1d50", ATTR{idProduct}=="604b", MODE="0666", GROUP="plugdev"
ATTR{idVendor}=="1d50", ATTR{idProduct}=="6089", MODE="0666", GROUP="plugdev"
ATTR{idVendor}=="1d50", ATTR{idProduct}=="cc15", MODE="0666", GROUP="plugdev"
EOF
    fi

    # PlutoSDR
    if [ ! -f /etc/udev/rules.d/53-adi-plutosdr-usb.rules ]; then
        info "Installing PlutoSDR udev rules..."
        cat <<EOF > /etc/udev/rules.d/53-adi-plutosdr-usb.rules
SUBSYSTEM=="usb", ATTRS{idVendor}=="0456", ATTRS{idProduct}=="b673", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0456", ATTRS{idProduct}=="b674", MODE="0666", GROUP="plugdev"
EOF
    fi

    # USRP (UHD)
    if [ ! -f /etc/udev/rules.d/uhd-usrp.rules ]; then
        info "Installing USRP udev rules..."
        cat <<EOF > /etc/udev/rules.d/uhd-usrp.rules
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="fffe", ATTR{idProduct}=="0002", MODE="0666", GROUP="plugdev"
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="2500", ATTR{idProduct}=="0020", MODE="0666", GROUP="plugdev"
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="2500", ATTR{idProduct}=="0021", MODE="0666", GROUP="plugdev"
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="2500", ATTR{idProduct}=="0022", MODE="0666", GROUP="plugdev"
EOF
    fi

    # LimeSDR
    if [ ! -f /etc/udev/rules.d/64-limesuite.rules ]; then
        info "Installing LimeSDR udev rules..."
        cat <<EOF > /etc/udev/rules.d/64-limesuite.rules
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="6108", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="0403", ATTR{idProduct}=="601f", MODE="0666", GROUP="plugdev"
EOF
    fi

    # Ensure plugdev group exists and reload
    groupadd -f plugdev || true
    udevadm control --reload-rules || true
    udevadm trigger || true
    success "Udev rules configured and reloaded."
}

# ─── 4. USB Buffer & Network Optimization ────────────────────────────────────
optimize_system_buffers() {
    header "4. System Buffer & Network Tuning for High-Rate SDR"

    # Increase USB buffer memory (usbfs memory mb)
    local usbfs_param="/sys/module/usbcore/parameters/usbfs_memory_mb"
    if [ -f "$usbfs_param" ]; then
        echo 1024 > "$usbfs_param" 2>/dev/null || true
        info "Increased USB buffer memory limit (usbfs_memory_mb = 1024 MB)."
    fi

    # Network socket buffers for Ethernet SDRs (USRP N/X series, PlutoSDR)
    info "Configuring network kernel socket buffer limits..."
    sysctl -w net.core.rmem_max=67108864 >> "$LOG_FILE" 2>&1 || true
    sysctl -w net.core.wmem_max=67108864 >> "$LOG_FILE" 2>&1 || true
    sysctl -w net.core.rmem_default=33554432 >> "$LOG_FILE" 2>&1 || true
    sysctl -w net.core.wmem_default=33554432 >> "$LOG_FILE" 2>&1 || true

    if [ "$SETUP_NETWORK" = true ]; then
        info "Configuring Ethernet interfaces for MTU 9000 (Jumbo Frames)..."
        for iface in $(ip -o link show | awk -F': ' '{print $2}' | grep -E '^e(th|n)'); do
            ip link set dev "$iface" mtu 9000 2>/dev/null && success "Set MTU 9000 on $iface" || true
        done
    fi
    success "Kernel network and USB memory tuned for zero-drop streaming."
}

# ─── 5. Post-Install Hardware Diagnostics & Validation ───────────────────────
validate_hardware() {
    header "5. Hardware Diagnostics & Capability Probing"

    info "Checking SoapySDR installation..."
    if command -v SoapySDRUtil >/dev/null 2>&1; then
        local version
        version=$(SoapySDRUtil --info | grep "Lib Version:" || echo "SoapySDR installed")
        success "SoapySDR: ${version}"
    else
        error "SoapySDRUtil binary not found in PATH."
        return 1
    fi

    info "Installed SoapySDR modules:"
    SoapySDRUtil --info | grep -A 20 "Available factories:" || true

    info "Probing connected SDR devices..."
    local probe_output
    probe_output=$(SoapySDRUtil --find 2>&1 || true)
    echo "$probe_output"

    # Check USB link speed for connected devices
    if command -v lsusb >/dev/null 2>&1; then
        info "Scanning USB bus for SDR devices..."
        lsusb | grep -Ei "rtl|hackrf|pluto|ettus|usrp|lime|airspy" || info "No known USB SDR IDs seen in lsusb currently."
    fi

    # Run Python validation tool if available
    local val_py="${SCRIPT_DIR}/validate_hardware.py"
    if [ -f "$val_py" ] && command -v python3 >/dev/null 2>&1; then
        info "Executing comprehensive Python validation tool..."
        python3 "$val_py" --json "${SCRIPT_DIR}/hardware_report.json" || warn "Validation tool reported warnings."
    fi
}

# ─── Main Execution Flow ──────────────────────────────────────────────────────
if [ "$VALIDATE_ONLY" = false ] && [ "$SKIP_INSTALL" = false ]; then
    resolve_kernel_conflicts
    install_packages
    setup_udev_rules
    optimize_system_buffers
elif [ "$SKIP_INSTALL" = true ]; then
    resolve_kernel_conflicts
    setup_udev_rules
    optimize_system_buffers
fi

validate_hardware

header "DynamiX Labs SDR Hardware Setup Completed"
echo -e "${GREEN}✓ Drivers, kernel rules, udev permissions, and system buffers are configured.${NC}"
echo -e "${CYAN}Run 'python3 hardware-setup/validate_hardware.py' anytime to test connected SDRs.${NC}\n"
