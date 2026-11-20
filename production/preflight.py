#!/usr/bin/env python3
import shutil, subprocess, sys
from pathlib import Path

errors = []
print(f"Python {sys.version.split()[0]}")
if sys.version_info[:2] != (3, 12):
    errors.append(f"Expected Python 3.12, got {sys.version_info[:2]}")

for mod in ("requests", "PIL", "boto3", "cv2", "arabic_reshaper", "bidi"):
    try:
        __import__(mod)
    except Exception as e:
        errors.append(f"import {mod} failed: {e}")

try:
    from PIL import features
    print("raqm:", features.check("raqm"))
except Exception as e:
    errors.append(f"Pillow feature check failed: {e}")

for b in ("ffmpeg", "ffprobe", "fc-list"):
    if not shutil.which(b):
        errors.append(f"missing binary: {b}")

root = Path(__file__).resolve().parent
for f in ("models/deploy.prototxt", "models/res10_300x300_ssd_iter_140000.caffemodel"):
    if not (root / f).exists():
        errors.append(f"missing model file: production/{f}")

fonts = subprocess.run(["fc-list"], capture_output=True, text=True).stdout.lower()
if not any(x in fonts for x in ("amiri", "scheherazade", "naskh")):
    errors.append("no Arabic font installed")

if errors:
    print("PREFLIGHT FAILED:")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("PREFLIGHT PASSED")
