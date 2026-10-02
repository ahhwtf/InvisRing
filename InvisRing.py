# Top-level shortcut to Src/InvisRing.py
import os
import sys
import subprocess

script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "InvisRing.py")
subprocess.run([sys.executable, script])