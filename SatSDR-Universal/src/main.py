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


if __name__ == "__main__":
    cli()
