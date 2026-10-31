#!/usr/bin/env python3
"""Entry point; the code lives in yourtube/ (see yourtube/README.md)."""
import os, runpy, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "yourtube"))
runpy.run_module("dashboard", run_name="__main__")
