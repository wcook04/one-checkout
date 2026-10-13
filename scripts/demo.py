#!/usr/bin/env python3
"""Forward the source-checkout demo command to the self-contained skill demo."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[1]
                      / "skills/one-checkout/scripts/demo.py"), run_name="__main__")
