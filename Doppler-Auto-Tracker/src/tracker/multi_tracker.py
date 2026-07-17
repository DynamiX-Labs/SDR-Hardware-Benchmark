"""
Multi-Satellite Tracker — Priority-based pass scheduling and tracking.

Inspired by:
  - YAMCS: Processor and link management
  - FoxTelem: Multi-satellite support and auto-tracking
  - SatDump: Multi-satellite automation

DynamiX Labs
"""

import time
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

from .doppler import DopplerCalculator
from src.tle.fetcher import TLEFetcher
from .predict import PassPredictor

log = logging.getLogger("tracker.multi")


@dataclass
class TrackTarget:
    satellite_name: str
    priority: int = 1  # Higher number = higher priority
    downlink_freq: float = 145.900e6
    uplink_freq: Optional[float] = None
    bandwidth: float = 25e3
    modulation: str = "FM"


@dataclass
class ActivePass:
    target: TrackTarget
    doppler_calc: DopplerCalculator
    aos_time: datetime
    los_time: datetime
    max_el: float
    is_active: bool = False


class MultiTracker:
    """
    Manages tracking and scheduling for multiple satellites.

    Features:
      - TLE auto-refresh
      - Priority-based conflict resolution
      - AOS/LOS event callbacks
      - Real-time Doppler updates
    """

    def __init__(self, lat: float, lon: float, alt: float = 0.0):
        self.lat = lat
        self.lon = lon
        self.alt = alt
        
        self.targets: Dict[str, TrackTarget] = {}
        self.fetcher = TLEFetcher()
        self.predictor = PassPredictor(lat, lon, alt)
        
        self.schedule: List[ActivePass] = []
        self.current_pass: Optional[ActivePass] = None
        
        # Callbacks
        self.on_aos: Optional[Callable[[ActivePass], None]] = None
        self.on_los: Optional[Callable[[ActivePass], None]] = None
        self.on_update: Optional[Callable[[ActivePass, float, float, float, float], None]] = None
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Initial TLE load
        for group in ["amateur", "weather", "cubesat"]:
            try:
                self.fetcher.fetch_group(group)
            except Exception as e:
                log.debug(f"Could not fetch TLE group {group}: {e}")

    def add_target(self, target: TrackTarget):
        """Add a satellite to the tracking list."""
        self.targets[target.satellite_name] = target
        log.info(f"Added target: {target.satellite_name} (Priority {target.priority})")
        self.update_schedule()

    def remove_target(self, name: str):
        """Remove a satellite from the tracking list."""
        if name in self.targets:
            del self.targets[name]
            log.info(f"Removed target: {name}")
            self.update_schedule()

    def update_schedule(self, hours_ahead: float = 12.0):
        """Recompute passes for all targets and resolve conflicts."""
        all_passes = []
        
        for name, target in self.targets.items():
            tle = self.fetcher.get_satellite(name)
            if not tle:
                log.warning(f"No TLE found for {name}, cannot schedule passes")
                continue
                
            passes = self.predictor.predict(tle, hours_ahead=hours_ahead)
            
            # Convert raw passes to ActivePass objects
            for p in passes:
                calc = DopplerCalculator(tle["line1"], tle["line2"], 
                                         self.lat, self.lon, self.alt)
                active = ActivePass(
                    target=target,
                    doppler_calc=calc,
                    aos_time=p["aos"],
                    los_time=p["los"],
                    max_el=p["max_el"]
                )
                all_passes.append(active)
                
        # Sort by AOS time
        all_passes.sort(key=lambda x: x.aos_time)
        
        # Conflict resolution (priority-based)
        resolved = []
        for p in all_passes:
            conflict = False
            for r in resolved:
                # Check for overlap
                if (p.aos_time < r.los_time) and (p.los_time > r.aos_time):
                    # Conflict found! Check priority.
                    if p.target.priority > r.target.priority:
                        # New pass wins, remove old
                        resolved.remove(r)
                    else:
                        # Old pass wins, skip new
                        conflict = True
                    break
                    
            if not conflict:
                resolved.append(p)
                
        self.schedule = resolved
        log.info(f"Schedule updated: {len(self.schedule)} passes over next {hours_ahead}h")
        for i, p in enumerate(self.schedule[:5]):
            log.debug(f"  [{i}] {p.target.satellite_name} @ {p.aos_time.strftime('%H:%M:%S')} (El: {p.max_el:.1f}°)")

    def start(self):
        """Start the background tracking thread."""
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(target=self._tracking_loop, daemon=True)
        self._thread.start()
        log.info("MultiTracker started")

    def stop(self):
        """Stop the background tracking thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        log.info("MultiTracker stopped")

    def _tracking_loop(self):
        """Background loop to manage active passes and emit updates."""
        while self._running:
            now = datetime.now(timezone.utc)
            
            # Check if current pass is over
            if self.current_pass:
                if now > self.current_pass.los_time:
                    log.info(f"LOS for {self.current_pass.target.satellite_name}")
                    self.current_pass.is_active = False
                    if self.on_los:
                        try:
                            self.on_los(self.current_pass)
                        except Exception as e:
                            log.error(f"Error in on_los callback: {e}")
                    self.current_pass = None
                else:
                    # Emit real-time update
                    self._emit_update(self.current_pass, now)
            
            # Check for new pass starting
            if not self.current_pass and self.schedule:
                next_pass = self.schedule[0]
                # Pre-AOS setup (e.g., 30 seconds before AOS)
                if (next_pass.aos_time - now).total_seconds() < 30.0:
                    self.current_pass = self.schedule.pop(0)
                    self.current_pass.is_active = True
                    log.info(f"AOS imminent for {self.current_pass.target.satellite_name}")
                    if self.on_aos:
                        try:
                            self.on_aos(self.current_pass)
                        except Exception as e:
                            log.error(f"Error in on_aos callback: {e}")
            
            # Check if schedule needs refresh (if empty or last pass is past)
            if not self.schedule and not self.current_pass:
                # Refresh every hour if idle
                self.update_schedule()
                
            time.sleep(0.5)

    def _emit_update(self, active_pass: ActivePass, now: datetime):
        """Calculate and emit real-time Az/El/Doppler."""
        if not self.on_update:
            return
            
        try:
            az, el, rng_km = active_pass.doppler_calc.get_azel(now)
            freq = active_pass.doppler_calc.corrected_frequency(
                active_pass.target.downlink_freq, now
            )
            self.on_update(active_pass, az, el, rng_km, freq)
        except Exception as e:
            log.error(f"Error computing/emitting update: {e}")
