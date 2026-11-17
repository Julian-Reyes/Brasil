"""Decode an unreal-mcp CaptureViewport JSON result into a PNG (resized for review).

Usage:  .venv/bin/python scripts/decode_capture.py <capture.json|txt> <out.png> [max_width]
"""
import base64
import io
import json
import sys

from PIL import Image

raw = open(sys.argv[1]).read()
data = json.loads(raw)
data = data.get("returnValue", data)
if isinstance(data, str):
    data = json.loads(data)
img = Image.open(io.BytesIO(base64.b64decode(data.get("image", data)["data"])))
max_w = int(sys.argv[3]) if len(sys.argv) > 3 else 1600
if img.width > max_w:
    img = img.resize((max_w, int(img.height * max_w / img.width)), Image.LANCZOS)
img.convert("RGB").save(sys.argv[2])
print(sys.argv[2], img.size)
