#!/usr/bin/env python3
"""Run the packaged PipeWire AEC setup from a workspace checkout."""
from pathlib import Path
import sys

# Keep the standalone setup command usable before the ROS package is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/kufibot_interaction'))
from kufibot_interaction.voice_aec import main  # noqa: E402


if __name__ == '__main__':
    main()
