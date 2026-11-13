"""Satellite pass predictor (AOS/LOS/Max-El) with schedule exports and SatSDR handoff."""

import argparse
import csv
import io
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from rich.console import Console
from rich.table import Table

if __package__ in (None, ""):
    _repo_pkg_root = Path(__file__).resolve().parents[2]
    if str(_repo_pkg_root) not in sys.path:
        sys.path.insert(0, str(_repo_pkg_root))
    from src.tracker.doppler import DopplerCalculator
else:
    from .doppler import DopplerCalculator

console = Console()
log = logging.getLogger("doppler.predict")

MIN_ELEVATION = 5.0  # Minimum elevation for a visible pass


def _azel_from_tle(
    tle_line1: str,
    tle_line2: str,
    obs_lat: float,
    obs_lon: float,
    obs_alt: float,
    dt: datetime,
):
    """Compute Az/El/Range for satellite at given time. Returns (az, el, range_km)."""
    calc = DopplerCalculator(tle_line1, tle_line2, obs_lat, obs_lon, obs_alt)
    return calc.get_azel(dt)


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_iso_utc(value: Union[datetime, str, None]) -> Optional[str]:
    """Format datetime or ISO string as an ISO-8601 UTC string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return _ensure_utc(value).isoformat()


def _format_ics_utc(dt: Union[datetime, str]) -> str:
    """Format datetime or ISO string as RFC 5545 UTC timestamp."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return _ensure_utc(dt).strftime("%Y%m%dT%H%M%SZ")


def _escape_ics_text(text: str) -> str:
    """Escape special characters in RFC 5545 text properties."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _normalize_sat_name(name: str) -> str:
    """Normalize satellite identifiers for cross-config matching (e.g. 'NOAA 19' == 'NOAA-19')."""
    return "".join(ch for ch in name.upper() if ch.isalnum())


def _default_satsdr_config_path() -> Optional[Path]:
    """Locate SatSDR-Universal/configs/satellites.yaml relative to the monorepo root."""
    candidate = (
        Path(__file__).resolve().parents[3]
        / "SatSDR-Universal"
        / "configs"
        / "satellites.yaml"
    )
    return candidate if candidate.exists() else None


def resolve_satsdr_defaults(
    satellite_name: str,
    sat_config_path: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, Any]]:
    """Look up decoder and nominal frequency defaults from SatSDR-Universal's satellites.yaml."""
    cfg_path = Path(sat_config_path) if sat_config_path else _default_satsdr_config_path()
    if not cfg_path or not cfg_path.exists():
        return None

    with open(cfg_path, "r", encoding="utf-8") as fh:
        catalog = yaml.safe_load(fh) or {}

    target_norm = _normalize_sat_name(satellite_name)
    for group_entries in catalog.values():
        if not isinstance(group_entries, dict):
            continue
        for entry_name, entry_cfg in group_entries.items():
            if _normalize_sat_name(entry_name) == target_norm and isinstance(entry_cfg, dict):
                return {
                    "decoder": entry_cfg.get("decoder"),
                    "frequency": float(entry_cfg["frequency"]) if "frequency" in entry_cfg else None,
                    "modulation": entry_cfg.get("modulation"),
                }
    return None


def _load_satsdr_scheduler_cls():
    """Import SatSDR-Universal PassScheduler without colliding with local 'src' package."""
    satsdr_src = Path(__file__).resolve().parents[3] / "SatSDR-Universal" / "src"
    if satsdr_src.exists() and str(satsdr_src) not in sys.path:
        sys.path.insert(0, str(satsdr_src))
    from scheduler.pass_scheduler import PassScheduler

    return PassScheduler


def _safe_azel(calc: DopplerCalculator, dt: datetime):
    """Compute (az, el, range_km) or return None if SGP4 propagation fails."""
    try:
        return calc.get_azel(dt)
    except Exception:
        return None


def _finalize_pass(current_pass: Dict[str, Any], los_dt: datetime) -> Dict[str, Any]:
    """Populate LOS, duration, and quality fields on a completed pass dictionary."""
    current_pass["los"] = los_dt
    duration = (los_dt - current_pass["aos"]).total_seconds()
    current_pass["duration_s"] = int(duration)
    current_pass["quality"] = "★" if current_pass["max_el"] >= 50 else ""
    return current_pass


def _serialize_pass(pass_info: Dict[str, Any], include_doppler_curve: bool = True) -> Dict[str, Any]:
    """Convert pass dictionary fields into JSON-serializable primitives."""
    serialized: Dict[str, Any] = {
        "satellite": pass_info["satellite"],
        "norad": pass_info.get("norad"),
        "aos": _format_iso_utc(pass_info["aos"]),
        "los": _format_iso_utc(pass_info["los"]),
        "duration_s": pass_info["duration_s"],
        "max_el": round(float(pass_info["max_el"]), 2),
        "max_el_time": _format_iso_utc(pass_info.get("max_el_time")),
        "max_el_az": round(float(pass_info["max_el_az"]), 2) if "max_el_az" in pass_info else None,
        "quality": pass_info.get("quality", ""),
    }
    for optional_key in ("nominal_freq_hz", "max_doppler_shift_hz", "aos_doppler_shift_hz", "los_doppler_shift_hz"):
        if optional_key in pass_info:
            serialized[optional_key] = pass_info[optional_key]
    if include_doppler_curve and "doppler_curve" in pass_info:
        serialized["doppler_curve"] = pass_info["doppler_curve"]
    return serialized


def _write_if_requested(content: str, output_path: Optional[Union[str, Path]]) -> None:
    """Write text content to disk when output_path is provided."""
    if output_path is None:
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class PassPredictor:
    """Predicts satellite passes visible from a ground station."""

    def __init__(
        self,
        obs_lat: float,
        obs_lon: float,
        obs_alt: float = 0.0,
        min_elevation: float = MIN_ELEVATION,
    ):
        if not (-90.0 <= obs_lat <= 90.0):
            raise ValueError(
                f"Invalid latitude {obs_lat}. Must be between -90 and +90."
            )
        if not (-180.0 <= obs_lon <= 180.0):
            raise ValueError(
                f"Invalid longitude {obs_lon}. Must be between -180 and +180."
            )

        self.obs_lat = obs_lat
        self.obs_lon = obs_lon
        self.obs_alt = obs_alt
        self.min_elevation = min_elevation

    def predict(
        self,
        tle: dict,
        hours_ahead: float = 24.0,
        time_step_s: float = 10.0,
        start_time: Optional[datetime] = None,
        nominal_freq_hz: Optional[float] = None,
        doppler_step_s: float = 5.0,
    ) -> List[Dict]:
        """Find all visible passes within the prediction window."""
        if time_step_s <= 0:
            raise ValueError("time_step_s must be positive.")

        calc = DopplerCalculator(
            tle["line1"], tle["line2"], self.obs_lat, self.obs_lon, self.obs_alt
        )
        now = _ensure_utc(start_time) if start_time is not None else datetime.now(timezone.utc)
        end = now + timedelta(hours=hours_ahead)
        step = timedelta(seconds=time_step_s)

        passes: List[Dict] = []
        dt = now
        in_pass = False
        current_pass: Dict[str, Any] = {}
        max_el = 0.0

        while dt < end:
            azel = _safe_azel(calc, dt)
            if azel is None:
                dt += step
                continue

            az, el, _ = azel
            if el >= self.min_elevation:
                if not in_pass:
                    in_pass = True
                    max_el = el
                    current_pass = {
                        "satellite": tle["name"],
                        "norad": tle.get("norad"),
                        "aos": dt,
                        "max_el": el,
                        "max_el_time": dt,
                        "max_el_az": az,
                    }
                elif el > max_el:
                    max_el = el
                    current_pass["max_el"] = el
                    current_pass["max_el_time"] = dt
                    current_pass["max_el_az"] = az
            elif in_pass:
                in_pass = False
                passes.append(_finalize_pass(current_pass, dt))

            dt += step

        if in_pass:
            passes.append(_finalize_pass(current_pass, dt))

        if nominal_freq_hz is not None and passes:
            self.attach_doppler_curves(tle, passes, nominal_freq_hz, step_s=doppler_step_s)

        return passes

    def compute_doppler_curve(
        self,
        tle: dict,
        pass_info: Dict[str, Any],
        nominal_freq_hz: float,
        step_s: float = 5.0,
    ) -> List[Dict[str, Any]]:
        """Precompute a time-series Doppler curve across a single pass from AOS to LOS."""
        if step_s <= 0:
            raise ValueError("step_s must be positive.")
        if nominal_freq_hz <= 0:
            raise ValueError("nominal_freq_hz must be positive.")

        aos = _ensure_utc(pass_info["aos"])
        los = _ensure_utc(pass_info["los"])
        if los < aos:
            raise ValueError("Pass LOS must not precede AOS.")

        calc = DopplerCalculator(
            tle["line1"], tle["line2"], self.obs_lat, self.obs_lon, self.obs_alt
        )
        curve: List[Dict[str, Any]] = []
        step = timedelta(seconds=step_s)
        dt = aos

        while dt <= los:
            curve.append(self._sample_doppler_point(calc, aos, dt, nominal_freq_hz))
            dt += step

        if not curve or datetime.fromisoformat(curve[-1]["timestamp"]) < los:
            curve.append(self._sample_doppler_point(calc, aos, los, nominal_freq_hz))

        return curve

    @staticmethod
    def _sample_doppler_point(
        calc: DopplerCalculator,
        aos: datetime,
        dt: datetime,
        nominal_freq_hz: float,
    ) -> Dict[str, Any]:
        """Compute one Doppler curve sample at timestamp dt."""
        az, el, rng_km = calc.get_azel(dt)
        range_rate_mps = calc.range_rate(dt)
        shift_hz = -nominal_freq_hz * range_rate_mps / 299_792_458.0
        return {
            "timestamp": dt.isoformat(),
            "offset_s": round((dt - aos).total_seconds(), 2),
            "azimuth_deg": round(az, 2),
            "elevation_deg": round(el, 2),
            "range_km": round(rng_km, 2),
            "range_rate_mps": round(range_rate_mps, 2),
            "doppler_shift_hz": round(shift_hz, 2),
            "corrected_freq_hz": round(nominal_freq_hz + shift_hz, 2),
        }

    def attach_doppler_curves(
        self,
        tle: dict,
        passes: List[Dict[str, Any]],
        nominal_freq_hz: float,
        step_s: float = 5.0,
    ) -> List[Dict[str, Any]]:
        """Attach precomputed Doppler curves and summary shift metrics to pass dicts."""
        for pass_info in passes:
            curve = self.compute_doppler_curve(tle, pass_info, nominal_freq_hz, step_s=step_s)
            pass_info["nominal_freq_hz"] = float(nominal_freq_hz)
            pass_info["doppler_curve"] = curve
            if curve:
                max_point = max(curve, key=lambda pt: abs(pt["doppler_shift_hz"]))
                pass_info["max_doppler_shift_hz"] = max_point["doppler_shift_hz"]
                pass_info["aos_doppler_shift_hz"] = curve[0]["doppler_shift_hz"]
                pass_info["los_doppler_shift_hz"] = curve[-1]["doppler_shift_hz"]
        return passes

    def export_json(
        self,
        passes: List[Dict[str, Any]],
        output_path: Optional[Union[str, Path]] = None,
        station_name: str = "",
        include_doppler_curve: bool = True,
    ) -> str:
        """Export predicted passes (and optional Doppler curves) as JSON."""
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "station": {
                "name": station_name,
                "latitude": "REDACTED",
                "longitude": "REDACTED",
                "altitude_m": self.obs_alt,
                "min_elevation_deg": self.min_elevation,
            },
            "pass_count": len(passes),
            "passes": [
                _serialize_pass(p, include_doppler_curve=include_doppler_curve)
                for p in passes
            ],
        }
        rendered = json.dumps(payload, indent=2)
        _write_if_requested(rendered, output_path)
        return rendered

    def export_csv(
        self,
        passes: List[Dict[str, Any]],
        output_path: Optional[Union[str, Path]] = None,
    ) -> str:
        """Export predicted passes as CSV."""
        fieldnames = [
            "satellite",
            "norad",
            "aos_utc",
            "los_utc",
            "duration_s",
            "max_el_deg",
            "max_el_time_utc",
            "max_el_az_deg",
            "quality",
            "nominal_freq_hz",
            "max_doppler_shift_hz",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for p in passes:
            writer.writerow(
                {
                    "satellite": p["satellite"],
                    "norad": p.get("norad", ""),
                    "aos_utc": _format_iso_utc(p["aos"]),
                    "los_utc": _format_iso_utc(p["los"]),
                    "duration_s": p["duration_s"],
                    "max_el_deg": round(float(p["max_el"]), 2),
                    "max_el_time_utc": _format_iso_utc(p.get("max_el_time")) or "",
                    "max_el_az_deg": (
                        round(float(p["max_el_az"]), 2) if "max_el_az" in p else ""
                    ),
                    "quality": p.get("quality", ""),
                    "nominal_freq_hz": p.get("nominal_freq_hz", ""),
                    "max_doppler_shift_hz": p.get("max_doppler_shift_hz", ""),
                }
            )
        rendered = buffer.getvalue()
        _write_if_requested(rendered, output_path)
        return rendered

    def export_ics(
        self,
        passes: List[Dict[str, Any]],
        output_path: Optional[Union[str, Path]] = None,
        station_name: str = "",
        calendar_name: str = "DynamiX Satellite Passes",
    ) -> str:
        """Export predicted passes as an RFC 5545 iCalendar (.ics) schedule."""
        dtstamp = _format_ics_utc(datetime.now(timezone.utc))
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//DynamiX Labs//Doppler-Auto-Tracker//EN",
            "CALSCALE:GREGORIAN",
            f"X-WR-CALNAME:{_escape_ics_text(calendar_name)}",
        ]

        for idx, p in enumerate(passes, 1):
            lines.extend(self._build_ics_event_lines(p, idx, dtstamp, station_name))

        lines.append("END:VCALENDAR")
        rendered = "\r\n".join(lines) + "\r\n"
        _write_if_requested(rendered, output_path)
        return rendered

    def _build_ics_event_lines(
        self,
        pass_info: Dict[str, Any],
        idx: int,
        dtstamp: str,
        station_name: str,
    ) -> List[str]:
        """Build VEVENT lines for a single pass."""
        aos_ics = _format_ics_utc(pass_info["aos"])
        los_ics = _format_ics_utc(pass_info["los"])
        sat = pass_info["satellite"]
        norad = pass_info.get("norad") or "unknown"
        uid_sat = _normalize_sat_name(sat) or "SAT"
        summary = f"{sat} Pass (Max El {pass_info['max_el']:.1f} deg)"
        desc_parts = [
            f"Satellite: {sat} (NORAD {norad})",
            f"Max Elevation: {pass_info['max_el']:.1f} deg",
            f"Duration: {pass_info['duration_s']}s",
        ]
        if "max_el_az" in pass_info:
            desc_parts.append(f"Azimuth at TCA: {pass_info['max_el_az']:.1f} deg")
        if "nominal_freq_hz" in pass_info:
            desc_parts.append(f"Nominal Freq: {pass_info['nominal_freq_hz'] / 1e6:.4f} MHz")
        if "max_doppler_shift_hz" in pass_info:
            desc_parts.append(f"Max Doppler Shift: {pass_info['max_doppler_shift_hz']:+.1f} Hz")

        event_lines = [
            "BEGIN:VEVENT",
            f"UID:{uid_sat}-{aos_ics}-{idx}@dynamixlabs.org",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{aos_ics}",
            f"DTEND:{los_ics}",
            f"SUMMARY:{_escape_ics_text(summary)}",
            f"DESCRIPTION:{_escape_ics_text(chr(10).join(desc_parts))}",
            f"GEO:{self.obs_lat:.6f};{self.obs_lon:.6f}",
        ]
        if station_name:
            event_lines.append(f"LOCATION:{_escape_ics_text(station_name)}")
        event_lines.append("END:VEVENT")
        return event_lines

    def to_satsdr_pass_events(
        self,
        passes: List[Dict[str, Any]],
        decoder: Optional[str] = None,
        frequency: Optional[float] = None,
        include_doppler_curve: bool = True,
        sat_config_path: Optional[Union[str, Path]] = None,
    ) -> List[Dict[str, Any]]:
        """Convert predicted passes into SatSDR-Universal PassScheduler job payloads."""
        jobs: List[Dict[str, Any]] = []
        for p in passes:
            defaults = resolve_satsdr_defaults(p["satellite"], sat_config_path=sat_config_path) or {}
            resolved_decoder = decoder or p.get("decoder") or defaults.get("decoder")
            resolved_freq = (
                frequency
                if frequency is not None
                else p.get("nominal_freq_hz") or defaults.get("frequency")
            )
            if not resolved_decoder or resolved_freq is None:
                raise ValueError(
                    f"Cannot resolve SatSDR decoder/frequency for '{p['satellite']}'. "
                    "Pass decoder and frequency explicitly or configure satellites.yaml."
                )

            event: Dict[str, Any] = {
                "satellite": p["satellite"],
                "aos": _format_iso_utc(p["aos"]),
                "tca": _format_iso_utc(p.get("max_el_time") or p["aos"]),
                "los": _format_iso_utc(p["los"]),
                "max_elevation_deg": round(float(p["max_el"]), 1),
                "duration_s": p["duration_s"],
            }
            if p.get("norad") is not None:
                event["norad"] = p["norad"]
            if "max_doppler_shift_hz" in p:
                event["max_doppler_shift_hz"] = p["max_doppler_shift_hz"]
            if include_doppler_curve and "doppler_curve" in p:
                event["doppler_curve"] = p["doppler_curve"]

            jobs.append(
                {
                    "pass_event": event,
                    "decoder": resolved_decoder,
                    "frequency": float(resolved_freq),
                }
            )
        return jobs

    def handoff_to_satsdr(
        self,
        passes: List[Dict[str, Any]],
        scheduler=None,
        decoder: Optional[str] = None,
        frequency: Optional[float] = None,
        include_doppler_curve: bool = True,
        sat_config_path: Optional[Union[str, Path]] = None,
    ):
        """Queue predicted passes directly into SatSDR-Universal's PassScheduler."""
        jobs = self.to_satsdr_pass_events(
            passes,
            decoder=decoder,
            frequency=frequency,
            include_doppler_curve=include_doppler_curve,
            sat_config_path=sat_config_path,
        )
        if scheduler is None:
            scheduler_cls = _load_satsdr_scheduler_cls()
            scheduler = scheduler_cls(self.obs_lat, self.obs_lon, self.obs_alt)

        for job in jobs:
            scheduler.queue_pass(
                job["pass_event"],
                decoder=job["decoder"],
                frequency=job["frequency"],
            )
        return scheduler

    def print_table(self, passes: List[Dict], station_name: str = "") -> None:
        """Print a clean table of upcoming passes using rich."""
        if not passes:
            console.print("[yellow]No passes found.[/yellow]")
            return

        name = passes[0].get("satellite", "Unknown")

        table = Table(
            title=(
                f"Upcoming Passes for {name}\n"
                f"Station: {station_name} ({self.obs_lat:.4f}°N, {self.obs_lon:.4f}°E)"
            ),
            style="cyan",
        )
        table.add_column("#", justify="right", style="cyan")
        table.add_column("AOS (UTC)", style="magenta")
        table.add_column("LOS (UTC)", style="magenta")
        table.add_column("Max El", justify="right", style="green")
        table.add_column("Duration", justify="right", style="yellow")

        for i, p in enumerate(passes, 1):
            aos = p["aos"].strftime("%Y-%m-%d %H:%M:%S")
            los = p["los"].strftime("%Y-%m-%d %H:%M:%S")
            dur_m = p["duration_s"] // 60
            dur_s = p["duration_s"] % 60
            quality = p.get("quality", "")

            table.add_row(
                str(i),
                aos,
                los,
                f"{p['max_el']:.1f}°",
                f"{dur_m}m {dur_s:02d}s {quality}",
            )

        console.print(table)
        console.print()


def _load_station_settings(config_path: str) -> Dict[str, Any]:
    """Load station configuration YAML if available."""
    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def _resolve_tle_for_cli(sat_name: str, tle_file: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetch or load a TLE dictionary for CLI usage."""
    from src.tle.fetcher import TLEFetcher

    fetcher = TLEFetcher()
    if tle_file:
        fetcher.load_file(tle_file)
    else:
        for group in ("amateur", "weather", "stations"):
            fetcher.fetch_group(group)
    return fetcher.get_satellite(sat_name)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for pass prediction, export, and SatSDR handoff."""
    parser = argparse.ArgumentParser(description="Satellite Pass Predictor & Exporter")
    parser.add_argument("--sat", required=True, help="Satellite name (e.g. 'NOAA 19')")
    parser.add_argument("--hours", type=float, default=24.0, help="Hours ahead to predict")
    parser.add_argument("--step", type=float, default=10.0, help="Prediction step in seconds")
    parser.add_argument("--config", default="configs/station.yaml", help="Station YAML config")
    parser.add_argument("--tle-file", default=None, help="Optional local TLE file")
    parser.add_argument("--lat", type=float, default=None, help="Observer latitude override")
    parser.add_argument("--lon", type=float, default=None, help="Observer longitude override")
    parser.add_argument("--alt", type=float, default=None, help="Observer altitude (m) override")
    parser.add_argument("--min-el", type=float, default=None, help="Minimum elevation (deg)")
    parser.add_argument("--nominal-freq", type=float, default=None, help="Nominal freq in Hz")
    parser.add_argument("--doppler-step", type=float, default=5.0, help="Doppler curve step (s)")
    parser.add_argument(
        "--export-format",
        choices=("table", "json", "csv", "ics"),
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument("--output", default=None, help="Output file path for exported schedule")
    parser.add_argument(
        "--handoff-satsdr",
        action="store_true",
        help="Queue predicted passes directly into SatSDR-Universal PassScheduler",
    )
    parser.add_argument("--decoder", default=None, help="Decoder name for SatSDR handoff")
    args = parser.parse_args(argv)

    cfg = _load_station_settings(args.config)
    station_cfg = cfg.get("station", {})
    tracking_cfg = cfg.get("tracking", {})

    lat = args.lat if args.lat is not None else station_cfg.get("latitude", 0.0)
    lon = args.lon if args.lon is not None else station_cfg.get("longitude", 0.0)
    alt = args.alt if args.alt is not None else station_cfg.get("altitude", 0.0)
    min_el = args.min_el if args.min_el is not None else tracking_cfg.get("min_elevation", MIN_ELEVATION)
    station_name = station_cfg.get("name", "Ground Station")

    tle = _resolve_tle_for_cli(args.sat, args.tle_file)
    if not tle:
        log.error("Satellite '%s' not found in TLE catalog.", args.sat)
        return 1

    predictor = PassPredictor(lat, lon, alt, min_elevation=min_el)
    passes = predictor.predict(
        tle,
        hours_ahead=args.hours,
        time_step_s=args.step,
        nominal_freq_hz=args.nominal_freq,
        doppler_step_s=args.doppler_step,
    )

    if args.export_format == "json":
        text = predictor.export_json(passes, output_path=args.output, station_name=station_name)
        if not args.output:
            print("Export generated in-memory. Use --output to write full JSON payload to a file.")
    elif args.export_format == "csv":
        text = predictor.export_csv(passes, output_path=args.output)
        if not args.output:
            print("Export generated in-memory. Use --output to write full CSV payload to a file.")
    elif args.export_format == "ics":
        text = predictor.export_ics(passes, output_path=args.output, station_name=station_name)
        if not args.output:
            print("Export generated in-memory. Use --output to write full ICS payload to a file.")
    else:
        predictor.print_table(passes, station_name=station_name)

    if args.handoff_satsdr and passes:
        scheduler = predictor.handoff_to_satsdr(
            passes, decoder=args.decoder, frequency=args.nominal_freq
        )
        log.info("Queued %d pass(es) into SatSDR PassScheduler.", len(scheduler.pass_queue))

    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    sys.exit(main())
