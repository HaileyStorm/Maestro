"""Local Drama 3 speech integration. Built with Fish Audio."""

from pathlib import Path
import sys


def activate_fish_source():
    """Use only the shipped, pinned Fish package; never a caller-supplied path."""
    vendor = str(Path(__file__).resolve().parent / "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
