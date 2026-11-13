import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.tracker.predict import PassPredictor, main as predict_main, resolve_satsdr_defaults

ISS_TLE = {
    "name": "ISS (ZARYA)",
    "norad": 25544,
    "line1": "1 25544U 98067A   23285.45607560  .00015509  00000-0  28373-3 0  9997",
    "line2": "2 25544  51.6416 352.5517 0004381 292.0528  46.9248 15.49887754420078",
}

NOAA19_TLE = {
    "name": "NOAA 19",
    "norad": 33591,
    "line1": "1 33591U 09005A   23285.45607560  .00000100  00000-0  80000-4 0  9991",
    "line2": "2 33591  99.1000 300.0000 0014000 100.0000 260.0000 14.12500000750001",
}

START_DT = datetime(2023, 10, 12, 10, 0, 0, tzinfo=timezone.utc)


def test_predict_with_start_time_and_doppler_curve():
    predictor = PassPredictor(obs_lat=51.5, obs_lon=-0.1, min_elevation=5.0)
    passes = predictor.predict(
        ISS_TLE,
        hours_ahead=24.0,
        time_step_s=20.0,
        start_time=START_DT,
        nominal_freq_hz=145.800e6,
        doppler_step_s=15.0,
    )
    assert len(passes) >= 1

    first = passes[0]
    assert first["satellite"] == "ISS (ZARYA)"
    assert first["aos"] < first["los"]
    assert first["duration_s"] > 0
    assert first["nominal_freq_hz"] == pytest.approx(145.800e6)
    assert "doppler_curve" in first
    curve = first["doppler_curve"]
    assert len(curve) >= 2
    assert curve[0]["offset_s"] == 0.0
    assert curve[-1]["timestamp"] == first["los"].isoformat()
    # Doppler shift decreases from positive (approaching) toward negative (receding)
    assert curve[0]["doppler_shift_hz"] > curve[-1]["doppler_shift_hz"]
    assert first["max_doppler_shift_hz"] == max(
        curve, key=lambda pt: abs(pt["doppler_shift_hz"])
    )["doppler_shift_hz"]


def test_export_json_csv_ics(tmp_path):
    predictor = PassPredictor(obs_lat=13.0827, obs_lon=80.2707, obs_alt=6.0, min_elevation=5.0)
    passes = predictor.predict(
        ISS_TLE,
        hours_ahead=24.0,
        time_step_s=20.0,
        start_time=START_DT,
        nominal_freq_hz=145.800e6,
        doppler_step_s=30.0,
    )
    assert len(passes) >= 1

    json_path = tmp_path / "passes.json"
    json_str = predictor.export_json(
        passes, output_path=json_path, station_name="Chennai Station"
    )
    assert json_path.exists()
    payload = json.loads(json_str)
    assert payload["station"]["name"] == "Chennai Station"
    assert payload["pass_count"] == len(passes)
    assert len(payload["passes"][0]["doppler_curve"]) >= 2

    csv_path = tmp_path / "passes.csv"
    csv_str = predictor.export_csv(passes, output_path=csv_path)
    assert csv_path.exists()
    rows = list(csv.DictReader(io.StringIO(csv_str)))
    assert len(rows) == len(passes)
    assert rows[0]["satellite"] == "ISS (ZARYA)"
    assert float(rows[0]["nominal_freq_hz"]) == pytest.approx(145.800e6)

    ics_path = tmp_path / "passes.ics"
    ics_str = predictor.export_ics(
        passes, output_path=ics_path, station_name="Chennai, India"
    )
    assert ics_path.exists()
    assert "BEGIN:VCALENDAR\r\n" in ics_str
    assert "END:VCALENDAR\r\n" in ics_str
    assert ics_str.count("BEGIN:VEVENT") == len(passes)
    assert "LOCATION:Chennai\\, India" in ics_str


def test_resolve_satsdr_defaults_and_handoff(tmp_path):
    defaults = resolve_satsdr_defaults("NOAA 19")
    assert defaults is not None
    assert defaults["decoder"] == "apt"
    assert defaults["frequency"] == pytest.approx(137.100e6)

    predictor = PassPredictor(obs_lat=51.5, obs_lon=-0.1, min_elevation=5.0)
    passes = predictor.predict(
        NOAA19_TLE,
        hours_ahead=24.0,
        time_step_s=20.0,
        start_time=START_DT,
        nominal_freq_hz=137.100e6,
        doppler_step_s=30.0,
    )
    assert len(passes) >= 1

    # Direct handoff into SatSDR-Universal PassScheduler
    scheduler = predictor.handoff_to_satsdr(passes)
    assert len(scheduler.pass_queue) == len(passes)
    first_job = scheduler.get_next_pass()
    assert first_job is not None
    assert first_job["decoder"] == "apt"
    assert first_job["frequency"] == pytest.approx(137.100e6)
    assert first_job["status"] == "queued"
    assert "doppler_curve" in first_job

    # JSON roundtrip import into PassScheduler
    json_path = tmp_path / "noaa19_schedule.json"
    predictor.export_json(passes, output_path=json_path)

    satsdr_src = Path(__file__).resolve().parents[2] / "SatSDR-Universal" / "src"
    if str(satsdr_src) not in sys.path:
        sys.path.insert(0, str(satsdr_src))
    from scheduler.pass_scheduler import PassScheduler

    fresh_scheduler = PassScheduler(51.5, -0.1)
    imported_count = fresh_scheduler.import_passes(
        json_path, default_decoder="apt", default_frequency=137.100e6
    )
    assert imported_count == len(passes)
    assert len(fresh_scheduler.pass_queue) == len(passes)


def test_handoff_raises_for_unknown_satellite_without_overrides():
    predictor = PassPredictor(obs_lat=51.5, obs_lon=-0.1)
    fake_passes = [
        {
            "satellite": "UNKNOWN-SAT-99",
            "aos": START_DT,
            "los": START_DT,
            "max_el": 42.0,
            "max_el_time": START_DT,
            "duration_s": 0,
        }
    ]
    with pytest.raises(ValueError, match="Cannot resolve SatSDR decoder/frequency"):
        predictor.to_satsdr_pass_events(fake_passes)


def test_predict_cli_exports_json_from_tle_file(tmp_path):
    tle_file = tmp_path / "local.tle"
    tle_file.write_text(
        f"{ISS_TLE['name']}\n{ISS_TLE['line1']}\n{ISS_TLE['line2']}\n",
        encoding="utf-8",
    )
    out_json = tmp_path / "cli_passes.json"

    rc = predict_main(
        [
            "--sat",
            "ISS (ZARYA)",
            "--tle-file",
            str(tle_file),
            "--lat",
            "51.5",
            "--lon",
            "-0.1",
            "--hours",
            "24",
            "--step",
            "30",
            "--nominal-freq",
            "145800000",
            "--export-format",
            "json",
            "--output",
            str(out_json),
        ]
    )
    assert rc == 0
    assert out_json.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert "passes" in data
    assert data["station"]["latitude"] == pytest.approx(51.5)