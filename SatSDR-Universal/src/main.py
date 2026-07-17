#!/usr/bin/env python3
"""
SatSDR-Universal - Main Entry Point
DynamiX Labs | https://github.com/DynamiX-Labs
"""

import click
import logging
import sys
from pathlib import Path

import colorlog

# Setup Aerospace-Grade Console Logging
handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s [%(levelname)-8s] %(cyan)s%(name)s%(reset)s: %(message_log_color)s%(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    reset=True,
    log_colors={
        'DEBUG':    'cyan',
        'INFO':     'green',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'red,bg_white',
    },
    secondary_log_colors={
        'message': {
            'ERROR':    'red',
            'CRITICAL': 'red'
        }
    }
))

logger = logging.getLogger()
logger.addHandler(handler)
logger.setLevel(logging.INFO)

log = logging.getLogger("satsdr")


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cli(verbose):
    """SatSDR-Universal: Satellite Signal Decoder Framework"""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
@click.option("--decoder", "-d", required=True,
              type=click.Choice(["apt", "lrpt", "adsb", "ax25", "acars", "hrpt"]),
              help="Decoder to use")
@click.option("--freq", "-f", type=float, default=None, help="Center frequency in Hz")
@click.option("--hardware", "-hw",
              type=click.Choice(["rtlsdr", "hackrf", "pluto", "usrp_b200", "usrp_b210"]),
              default="rtlsdr", help="SDR hardware")
@click.option("--iq-file", type=click.Path(exists=True), default=None, help="IQ file to decode")
@click.option("--rate", type=float, default=250000, help="Sample rate in SPS")
@click.option("--gain", type=float, default=30, help="RF gain in dB")
@click.option("--output", "-o", type=click.Path(), default="./output", help="Output directory")
def decode(decoder, freq, hardware, iq_file, rate, gain, output):
    """Decode satellite signals from live SDR or IQ file."""
    from .decoders import get_decoder
    from .utils.hardware import HardwareManager

    log.info(f"Starting decoder: {decoder}")
    log.info(f"Hardware: {hardware} | Rate: {rate/1e3:.1f} kSPS | Gain: {gain} dB")

    Path(output).mkdir(parents=True, exist_ok=True)

    dec_cls = get_decoder(decoder)
    dec = dec_cls(sample_rate=rate, output_dir=output)

    if iq_file:
        log.info(f"Processing IQ file: {iq_file}")
        dec.decode_file(iq_file)
    else:
        if freq is None:
            freq = dec.FREQUENCY
            log.info(f"Using default frequency: {freq/1e6:.3f} MHz")
        hw = HardwareManager(hardware)
        hw.configure(frequency=freq, sample_rate=rate, gain=gain)
        log.info(f"Starting live decode on {freq/1e6:.3f} MHz...")
        dec.decode_live(hw)


@cli.command()
@click.option("--hardware", "-hw",
              type=click.Choice(["rtlsdr", "hackrf", "pluto", "usrp_b200"]),
              default="rtlsdr")
@click.option("--duration", type=int, default=30, help="Benchmark duration in seconds")
def benchmark(hardware, duration):
    """Run DSP performance benchmark."""
    from .dsp.benchmark import Benchmark
    bm = Benchmark(hardware=hardware, duration=duration)
    bm.run()
    bm.report()


@cli.command()
def list_decoders():
    """List all available decoders."""
    from .decoders import DECODER_REGISTRY
    click.echo("\nAvailable Decoders:\n" + "─" * 50)
    for name, cls in DECODER_REGISTRY.items():
        click.echo(f"  {name:12s} | {cls.FREQUENCY/1e6:.3f} MHz | {cls.MODULATION}")
    click.echo()


# ──────────────────────────────────────────────────────────────────────
# Phase 3: WebSocket Spectrum Streaming
# ──────────────────────────────────────────────────────────────────────
@cli.command()
@click.option("--host", default="0.0.0.0", help="WebSocket bind address")
@click.option("--port", "-p", type=int, default=8765, help="WebSocket port")
@click.option("--fps", type=int, default=30, help="Max frame rate")
@click.option("--fft-size", type=int, default=4096, help="FFT size for PSD")
@click.option("--gpu/--no-gpu", default=True, help="Enable GPU acceleration")
@click.option("--auth-token", default=None, help="Optional auth token for clients")
def stream(host, port, fps, fft_size, gpu, auth_token):
    """Start the WebSocket live spectrum streaming server."""
    import asyncio
    from .dsp.gpu_backend import GPUBackend
    from .dsp.spectral_engine import SpectralEngine
    from .streaming.spectrum_server import SpectrumServer

    log.info("Initialising spectrum streaming server...")

    gpu_backend = None
    if gpu:
        gpu_backend = GPUBackend()

    engine = SpectralEngine(
        sample_rate=250_000, fft_size=fft_size, gpu_backend=gpu_backend
    )

    server = SpectrumServer(
        host=host, port=port,
        spectral_engine=engine,
        gpu_backend=gpu_backend,
        auth_token=auth_token,
        max_fps=fps,
        fft_size=fft_size,
    )

    asyncio.run(server.start())


# ──────────────────────────────────────────────────────────────────────
# Phase 3: Distributed Decoder Broker
# ──────────────────────────────────────────────────────────────────────
@cli.command()
@click.option("--frontend", default="tcp://*:5555", help="Client-facing ROUTER address")
@click.option("--backend", default="tcp://*:5556", help="Worker-facing ROUTER address")
@click.option("--monitor", default="tcp://*:5557", help="Monitor PUB address")
@click.option("--heartbeat", type=float, default=5.0, help="Heartbeat check interval (s)")
@click.option("--timeout", type=float, default=30.0, help="Worker timeout (s)")
def broker(frontend, backend, monitor, heartbeat, timeout):
    """Start the distributed decoder job broker."""
    from .cluster.broker import DecoderBroker

    log.info("Starting decoder broker...")
    brk = DecoderBroker(
        frontend_addr=frontend,
        backend_addr=backend,
        monitor_addr=monitor,
        heartbeat_interval=heartbeat,
        worker_timeout=timeout,
    )
    brk.start()


# ──────────────────────────────────────────────────────────────────────
# Phase 3: Distributed Decoder Worker
# ──────────────────────────────────────────────────────────────────────
@cli.command()
@click.option("--broker-addr", default="tcp://localhost:5556", help="Broker backend address")
@click.option("--worker-id", default=None, help="Worker identifier")
@click.option("--gpu/--no-gpu", default=True, help="Enable GPU acceleration")
@click.option("--concurrency", type=int, default=2, help="Max concurrent jobs")
@click.option("--capabilities", default="", help="Comma-separated decoder types (empty=all)")
def worker(broker_addr, worker_id, gpu, concurrency, capabilities):
    """Start a distributed decoder worker node."""
    from .dsp.gpu_backend import GPUBackend
    from .cluster.worker import DecoderWorker

    gpu_backend = None
    if gpu:
        gpu_backend = GPUBackend()

    caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []

    log.info("Starting decoder worker...")
    w = DecoderWorker(
        broker_addr=broker_addr,
        worker_id=worker_id,
        gpu_backend=gpu_backend,
        max_concurrent=concurrency,
        capabilities=caps,
    )
    w.start()


# ──────────────────────────────────────────────────────────────────────
# Phase 4: Federation Hub
# ──────────────────────────────────────────────────────────────────────
@cli.command("federation-hub")
@click.option("--bind", default="tcp://*:6000", help="Station ROUTER bind address")
@click.option("--pub", default="tcp://*:6001", help="Broadcast PUB bind address")
def federation_hub(bind, pub):
    """Start the federated ground station hub (API-only)."""
    from .federation import FederationHub

    log.info("Starting federation hub...")
    hub = FederationHub(bind_addr=bind, pub_addr=pub)
    hub.start()


# ──────────────────────────────────────────────────────────────────────
# Phase 4: Federation Node
# ──────────────────────────────────────────────────────────────────────
@cli.command("federation-node")
@click.option("--station-id", required=True, help="Unique station identifier")
@click.option("--lat", type=float, default=0.0, help="Station latitude")
@click.option("--lon", type=float, default=0.0, help="Station longitude")
@click.option("--hub", default="tcp://localhost:6000", help="Hub ROUTER address")
@click.option("--sub", default="tcp://localhost:6001", help="Hub PUB address")
@click.option("--capabilities", default="", help="Comma-separated decoder types")
def federation_node(station_id, lat, lon, hub, sub, capabilities):
    """Connect to the federation as a ground station node (API-only)."""
    from .federation import FederationNode

    caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []

    log.info(f"Starting federation node: {station_id}")
    node = FederationNode(
        station_id=station_id, lat=lat, lon=lon,
        capabilities=caps, hub_addr=hub, sub_addr=sub,
    )
    node.start()


# ──────────────────────────────────────────────────────────────────────
# Phase 4: CFDP File Delivery
# ──────────────────────────────────────────────────────────────────────
@cli.command("cfdp-send")
@click.option("--source", "-s", required=True, type=click.Path(exists=True),
              help="Source file to send")
@click.option("--dest-path", "-d", default=None, help="Destination filename")
@click.option("--entity-id", type=int, default=1, help="Local CFDP entity ID")
@click.option("--peer-id", type=int, default=2, help="Destination entity ID")
@click.option("--segment-size", type=int, default=1024, help="Segment size in bytes")
@click.option("--mode", type=click.Choice(["class1", "class2"]), default="class1",
              help="Transmission mode")
def cfdp_send(source, dest_path, entity_id, peer_id, segment_size, mode):
    """Send a file via CCSDS CFDP protocol (API-only, no GUI)."""
    from .protocols.cfdp import CFDPSender, TransmissionMode

    tx_mode = (TransmissionMode.UNACKNOWLEDGED if mode == "class1"
               else TransmissionMode.ACKNOWLEDGED)

    pdu_count = [0]
    def count_transport(pdu_bytes):
        pdu_count[0] += 1

    sender = CFDPSender(
        entity_id=entity_id, peer_id=peer_id,
        transport=count_transport,
        segment_size=segment_size,
        transmission_mode=tx_mode,
    )

    tx_id = sender.put(source, destination_path=dest_path)
    tx_info = sender.get_transaction(tx_id)

    log.info(f"CFDP transfer complete | TX: {tx_id}")
    log.info(f"  File: {source} ({tx_info['file_size']} bytes)")
    log.info(f"  Segments: {tx_info['segments_sent']} | PDUs: {pdu_count[0]}")
    log.info(f"  Mode: {mode} | Status: {tx_info['status']}")


if __name__ == "__main__":
    cli()
