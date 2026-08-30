from __future__ import annotations

import signal
import sys
import time
from typing import Any

import schedule

from edgedash.config import load_config
from edgedash.orchestrator import run_cycle


class Scheduler:
    """Scheduled trigger for EdgeDash cycles using the schedule library."""
    
    def __init__(self, config: Any):
        self.config = config
        self.running = False
        
    def _run_cycle(self) -> None:
        """Run a single cycle and handle exceptions."""
        try:
            run_cycle(self.config)
        except Exception as exc:
            print(f"\nERROR: Cycle failed: {exc}")
    
    def _setup_schedule(self) -> None:
        """Setup the schedule based on configuration."""
        interval = getattr(self.config, "schedule_interval", "hourly")
        
        if interval == "hourly":
            schedule.every().hour.do(self._run_cycle)
        elif interval == "daily":
            schedule.every().day.do(self._run_cycle)
        elif interval == "daily_6am":
            schedule.every().day.at("06:00").do(self._run_cycle)
        elif interval == "weekly":
            schedule.every().week.do(self._run_cycle)
        elif interval.startswith("every_"):
            # Support custom intervals like "every_30_minutes"
            parts = interval.split("_")
            if len(parts) == 3 and parts[0] == "every":
                try:
                    value = int(parts[1])
                    unit = parts[2]
                    if unit == "minutes":
                        schedule.every(value).minutes.do(self._run_cycle)
                    elif unit == "hours":
                        schedule.every(value).hours.do(self._run_cycle)
                    else:
                        raise ValueError(f"Unsupported schedule unit: {unit}")
                except ValueError:
                    raise ValueError(f"Invalid schedule interval: {interval}")
            else:
                raise ValueError(f"Invalid schedule interval format: {interval}")
        else:
            raise ValueError(f"Unsupported schedule interval: {interval}")
    
    def start(self) -> None:
        """Start the scheduled runner."""
        self.running = True
        self._setup_schedule()
        
        # Setup signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        # SIGTERM not available on Windows
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, self._signal_handler)
        
        print(f"\nEdgeDash Scheduler started")
        print(f"Schedule: {getattr(self.config, 'schedule_interval', 'hourly')}")
        print("Press Ctrl+C to stop\n")
        
        # Run immediately on start
        print("Running initial cycle...")
        self._run_cycle()
        
        # Main loop
        while self.running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    
    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals gracefully."""
        print(f"\nReceived signal {signum}, shutting down...")
        self.running = False


def run_scheduler() -> None:
    """Entry point for the scheduled runner."""
    config = load_config()
    scheduler = Scheduler(config)
    scheduler.start()


if __name__ == "__main__":
    run_scheduler()
