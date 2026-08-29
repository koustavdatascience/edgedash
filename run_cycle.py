from __future__ import annotations

from edgedash.config import load_config
from edgedash.orchestrator import run_cycle


def main() -> None:
    run_cycle(load_config())


if __name__ == "__main__":
    main()
