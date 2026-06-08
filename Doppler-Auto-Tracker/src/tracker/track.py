import argparse
import time
import logging
import yaml
from pathlib import Path
from datetime import datetime, timezone

from .doppler import DopplerCalculator
from src.tle.fetcher import TLEFetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("tracker")

def load_station_config(config_path="configs/station.yaml"):
    path = Path(config_path)
    if path.exists():
        with open(path, "r") as f:
            return yaml.safe_load(f)
    return {
        "station": {
            "latitude": 0.0,
            "longitude": 0.0,
            "altitude": 0.0,
            "name": "Default"
        }
    }

def main():
    parser = argparse.ArgumentParser(description="Real-time Doppler Tracker")
    parser.add_argument("--sat", type=str, required=True, help="Satellite name (e.g. 'NOAA 19')")
    parser.add_argument("--hardware", type=str, default="dummy", help="Hardware to control (e.g. 'rtlsdr')")
    parser.add_argument("--nominal-freq", type=float, required=True, help="Nominal frequency in Hz")
    parser.add_argument("--update-interval", type=float, default=1.0, help="Update interval in seconds")
    
    args = parser.parse_args()

    config = load_station_config()
    lat = config.get("station", {}).get("latitude", 0.0)
    lon = config.get("station", {}).get("longitude", 0.0)
    alt = config.get("station", {}).get("altitude", 0.0)
    
    log.info(f"Initializing tracker for {args.sat} at {lat:.4f}N, {lon:.4f}E")

    fetcher = TLEFetcher()
    for group in ["amateur", "weather", "stations"]:
        try:
            fetcher.fetch_group(group)
        except Exception as e:
            log.warning(f"Failed to fetch {group} TLEs: {e}")

    sat_tle = fetcher.get_satellite(args.sat)
    if not sat_tle:
        log.error(f"Satellite '{args.sat}' not found in TLE catalog.")
        return

    calc = DopplerCalculator(sat_tle["line1"], sat_tle["line2"], lat, lon, alt)
    
    log.info(f"Starting tracking loop. Hardware: {args.hardware}, Base Freq: {args.nominal_freq/1e6:.4f} MHz")
    
    try:
        while True:
            now = datetime.now(timezone.utc)
            az, el, rng = calc.get_azel(now)
            
            if el < 0:
                log.debug(f"Satellite below horizon (el={el:.1f}°), skipping")
                time.sleep(args.update_interval)
                continue
                
            corrected_freq = calc.corrected_frequency(args.nominal_freq, now)
            
            log.info(f"Az: {az:5.1f}° | El: {el:5.1f}° | Range: {rng:6.1f} km | "
                     f"Freq: {corrected_freq/1e6:.6f} MHz")
            
            time.sleep(args.update_interval)
            
    except KeyboardInterrupt:
        log.info("Tracking stopped by user.")

if __name__ == "__main__":
    main()
