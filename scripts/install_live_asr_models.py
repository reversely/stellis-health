"""Install pinned local models for the incremental Rust recording preview."""

import argparse
from pathlib import Path

from stellis.live_asr_models import install

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    install(parser.parse_args().destination.expanduser())
