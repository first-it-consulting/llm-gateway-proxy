#!/usr/bin/env python3
"""Start llm-gateway-proxy."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.main import main

if __name__ == "__main__":
    main()
