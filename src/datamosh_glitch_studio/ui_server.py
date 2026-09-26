"""
Datamosh Studio UI & REST API Server (design influenced by Material 3 tokens).
Zero third-party runtime dependencies.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from datamosh_glitch_studio.bitplane_glitch import (
    BitplaneExtractor,
    BitplaneGlitchEngine,
)
from datamosh_glitch_studio.compat import safe_join
from datamosh_glitch_studio.frame_io import ImageFrame, export_frame_sequence_html
from datamosh_glitch_studio.glitch_core import DatamoshEngine, corrupt_byte_stream
from datamosh_glitch_studio.motion_vector_engine import (
    MotionVectorEngine,
    render_motion_vectors_ascii,
    render_motion_vectors_svg,
)
from datamosh_glitch_studio.presets import PRESETS, get_preset, list_presets

SERVER_START_TIME = time.time()

EMBEDDED_STUDIO_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Datamosh Studio | Visual Artifact Synthesizer</title>
  <style>
    :root {
      --g-blue: #1a73e8;
      --g-blue-dark: #1557b0;
      --g-blue-light: #e8f0fe;
      --surface: #ffffff;
      --surface-variant: #f8f9fa;
      --border: #dadce0;
      --text: #202124;
      --text-secondary: #5f6368;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Google Sans", "Segoe UI", Roboto, sans-serif; background: var(--surface-variant); color: var(--text); display: flex; flex-direction: column; height: 100vh; }
    header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 0.75rem 1.5rem; display: flex; align-items: center; justify-content: space-between; }
    .logo { font-size: 1.25rem; font-weight: 600; color: var(--g-blue); display: flex; align-items: center; gap: 0.5rem; }
    .badge { background: var(--g-blue-light); color: var(--g-blue); padding: 0.25rem 0.5rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 500; }
    .container { display: grid; grid-template-columns: 340px 1fr; flex: 1; overflow: hidden; }
    aside { background: var(--surface); border-right: 1px solid var(--border); padding: 1.5rem; overflow-y: auto; display: flex; flex-direction: column; gap: 1rem; }
    main { padding: 2rem; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1rem; overflow-y: auto; }
    .form-group { display: flex; flex-direction: column; gap: 0.35rem; }
    label { font-size: 0.85rem; font-weight: 500; color: var(--text-secondary); }
    select, input, button { padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid var(--border); font-size: 0.9rem; }
    button.btn { background: var(--g-blue); color: white; border: none; font-weight: 600; cursor: pointer; transition: background 0.15s; }
    button.btn:hover { background: var(--g-blue-dark); }
    .preview-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem; box-shadow: 0 1px 3px rgba(60,64,67,0.15); max-width: 640px; width: 100%; text-align: center; }
    canvas, img { max-width: 100%; height: auto; border-radius: 8px; border: 1px solid var(--border); image-rendering: pixelated; }
  </style>
</head>
<body>
  <header>
    <div class="logo">
      <span>📼</span> Datamosh Studio
      <span class="badge">I-Frame Synthesizer • Material 3 Influenced</span>
    </div>
  </header>
  <div class="container">
    <aside>
      <div class="form-group">
        <label>Glitch Preset</label>
        <select id="preset-select">
          <option value="h264_iframe_drop" selected>H.264 I-Frame Drop (Classic Datamosh)</option>
          <option value="cyberpunk_vcr">Cyberpunk VCR Tracking Glitch</option>
          <option value="rgb_split_overdrive">RGB Chromatic Aberration Overdrive</option>
          <option value="analog_tape_decay">Analog Tape Decay & Static</option>
          <option value="quantum_matrix_tear">Quantum Matrix Tear</option>
        </select>
      </div>
      <div class="form-group">
        <label>Input Image / Test Pattern</label>
        <input type="file" id="image-input" accept="image/*">
      </div>
      <button class="btn" onclick="applyGlitch()">Synthesize Glitch</button>
      <button class="btn" style="background:#475569;" onclick="downloadGlitch()">Download BMP Frame</button>
    </aside>
    <main>
      <div class="preview-card">
        <img id="glitch-img" alt="Glitch Output">
      </div>
    </main>
  </div>
  <script>
    let currentBmpB64 = '';
    async function applyGlitch() {
      const preset = document.getElementById('preset-select').value;
      const resp = await fetch('/api/mosh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset, width: 480, height: 320 })
      });
      const data = await resp.json();
      currentBmpB64 = data.image_base64;
      document.getElementById('glitch-img').src = 'data:image/bmp;base64,' + data.image_base64;
    }
    function downloadGlitch() {
      if (!currentBmpB64) return;
      const a = document.createElement('a');
      a.href = 'data:image/bmp;base64,' + currentBmpB64;
      a.download = 'datamosh_glitch.bmp';
      a.click();
    }
    applyGlitch();
  </script>
</body>
</html>"""


class DatamoshHTTPHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Studio Web UI and REST API."""

    def _set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._send_json({
                "status": "ok",
                "service": "datamosh-glitch-studio",
                "uptime_seconds": round(time.time() - SERVER_START_TIME, 2),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            })
            return

        elif path == "/api/presets":
            self._send_json({"presets": list_presets()})
            return

        elif path == "/api/diagnostics":
            self._send_json({
                "platform": sys.platform,
                "python": sys.version,
                "presets_count": len(PRESETS),
                "status": "HEALTHY"
            })
            return

        elif path == "/api/bitplane-mosaic":
            qs = urllib.parse.parse_qs(parsed.query)
            channel = qs.get("channel", ["luminance"])[0]
            w = int(qs.get("width", ["320"])[0])
            h = int(qs.get("height", ["240"])[0])
            frame = ImageFrame.create(w, h)
            colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
            bw = w // len(colors)
            for y in range(h):
                for x in range(w):
                    frame.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])
            svg_code = BitplaneGlitchEngine.render_bitplane_mosaic_svg(frame, channel=channel)
            self._send_json({
                "status": "success",
                "channel": channel,
                "svg_mosaic": svg_code,
            })
            return

        # Serve UI
        public_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "public"))
        index_file = os.path.join(public_dir, "index.html")

        if os.path.isfile(index_file) and path in ("/", "/index.html"):
            with open(index_file, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(content)
            return

        # Embedded UI fallback
        body = EMBEDDED_STUDIO_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b"{}"

        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            self._send_json({"error": "Invalid JSON"}, status=400)
            return

        engine = DatamoshEngine()

        if path == "/api/mosh":
            preset_name = body.get("preset", "h264_iframe_drop")
            preset = get_preset(preset_name)
            w = body.get("width", 320)
            h = body.get("height", 240)
            seed = body.get("seed")

            img_b64 = body.get("image_base64")
            if img_b64:
                raw_img = base64.b64decode(img_b64)
                if raw_img.startswith(b"BM"):
                    frame = ImageFrame.from_bmp(raw_img)
                else:
                    frame = ImageFrame.from_ppm(raw_img)
            else:
                # Color bars
                frame = ImageFrame.create(w, h)
                colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
                bw = w // len(colors)
                for y in range(h):
                    for x in range(w):
                        frame.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

            glitched = engine.process_frame(frame, preset=preset, seed=seed)
            bmp_bytes = glitched.to_bmp()

            self._send_json({
                "status": "success",
                "preset": preset.name,
                "width": glitched.width,
                "height": glitched.height,
                "image_base64": base64.b64encode(bmp_bytes).decode("ascii")
            })
            return

        elif path == "/api/sequence":
            preset_name = body.get("preset", "h264_iframe_drop")
            preset = get_preset(preset_name)
            w = int(body.get("width", 320))
            h = int(body.get("height", 240))
            count = min(30, max(2, int(body.get("frames_count", 8))))
            base_seed = int(body.get("seed", 42))

            # Generate base frames (moving color bars or geometric shapes)
            base_frames = []
            colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
            for i in range(count):
                f = ImageFrame.create(w, h)
                shift = (i * 24) % w
                bw = max(1, w // len(colors))
                for y in range(h):
                    for x in range(w):
                        col_idx = ((x + shift) // bw) % len(colors)
                        f.set_pixel(x, y, colors[col_idx])
                base_frames.append(f)

            glitched_seq = engine.process_video_sequence(base_frames, preset=preset, seed=base_seed)
            b64_list = [base64.b64encode(gf.to_bmp()).decode("ascii") for gf in glitched_seq]

            self._send_json({
                "status": "success",
                "preset": preset.name,
                "frames_count": len(b64_list),
                "frames": b64_list
            })
            return

        elif path == "/api/corrupt":
            data_b64 = body.get("data_base64", "")
            rate = float(body.get("rate", 0.002))
            hdr_skip = int(body.get("header_skip", 64))
            raw = base64.b64decode(data_b64)
            corrupted = corrupt_byte_stream(raw, rate=rate, header_skip=hdr_skip)

            self._send_json({
                "status": "success",
                "corrupted_base64": base64.b64encode(corrupted).decode("ascii")
            })
            return

        elif path == "/api/motion-estimate":
            w = int(body.get("width", 160))
            h = int(body.get("height", 120))
            bs = int(body.get("block_size", 16))
            shift_dx = int(body.get("shift_dx", 4))
            shift_dy = int(body.get("shift_dy", 2))

            ref = ImageFrame.create(w, h)
            colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
            bw = w // len(colors)
            for y in range(h):
                for x in range(w):
                    ref.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

            tgt = ImageFrame.create(w, h)
            for y in range(h):
                for x in range(w):
                    src_x = (x - shift_dx) % w
                    src_y = (y - shift_dy) % h
                    tgt.set_pixel(x, y, ref.get_pixel(src_x, src_y))

            mv_engine = MotionVectorEngine(block_size=bs)
            mv_field = mv_engine.estimate_motion(ref, tgt)

            self._send_json({
                "status": "success",
                "width": w,
                "height": h,
                "block_size": bs,
                "total_blocks": len(mv_field.vectors),
                "average_magnitude": round(mv_field.average_magnitude, 3),
                "ascii_flow_grid": render_motion_vectors_ascii(mv_field),
                "svg_vector_map": render_motion_vectors_svg(mv_field),
            })
            return

        elif path == "/api/liquid-melt":
            w = int(body.get("width", 160))
            h = int(body.get("height", 120))
            steps = min(20, max(2, int(body.get("steps", 6))))
            acc = float(body.get("acceleration", 1.2))

            ref = ImageFrame.create(w, h)
            colors = [(255, 0, 128), (0, 255, 255), (255, 255, 0), (0, 255, 0)]
            bw = w // len(colors)
            for y in range(h):
                for x in range(w):
                    ref.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

            mv_engine = MotionVectorEngine(block_size=16)
            frames = mv_engine.datamosh_liquid_melt(ref, steps=steps, acceleration=acc)
            b64_list = [base64.b64encode(f.to_bmp()).decode("ascii") for f in frames]

            self._send_json({
                "status": "success",
                "steps": steps,
                "frames_count": len(b64_list),
                "frames": b64_list,
            })
            return

        elif path == "/api/bitplane-slice":
            keep_bits = body.get("keep_bits", [7, 6, 5])
            invert_bits = body.get("invert_bits", [])
            channel = body.get("channel", "all")
            w = int(body.get("width", 320))
            h = int(body.get("height", 240))
            img_b64 = body.get("image_base64")

            if img_b64:
                raw_img = base64.b64decode(img_b64)
                frame = ImageFrame.from_bmp(raw_img) if raw_img.startswith(b"BM") else ImageFrame.from_ppm(raw_img)
            else:
                frame = ImageFrame.create(w, h)
                colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
                bw = w // len(colors)
                for y in range(h):
                    for x in range(w):
                        frame.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

            sliced = BitplaneGlitchEngine.slice_bitplanes(frame, keep_bits=keep_bits, channel=channel)
            if invert_bits:
                sliced = BitplaneGlitchEngine.invert_bitplanes(sliced, invert_bits=invert_bits, channel=channel)

            bmp_bytes = sliced.to_bmp()
            self._send_json({
                "status": "success",
                "width": sliced.width,
                "height": sliced.height,
                "keep_bits": keep_bits,
                "invert_bits": invert_bits,
                "channel": channel,
                "image_base64": base64.b64encode(bmp_bytes).decode("ascii")
            })
            return

        elif path == "/api/sierpinski-xor":
            scale = float(body.get("scale", 1.0))
            blend = float(body.get("blend", 0.5))
            formula = str(body.get("formula", "xor"))
            w = int(body.get("width", 320))
            h = int(body.get("height", 240))
            img_b64 = body.get("image_base64")

            if img_b64:
                raw_img = base64.b64decode(img_b64)
                frame = ImageFrame.from_bmp(raw_img) if raw_img.startswith(b"BM") else ImageFrame.from_ppm(raw_img)
            else:
                frame = ImageFrame.create(w, h)
                colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
                bw = w // len(colors)
                for y in range(h):
                    for x in range(w):
                        frame.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

            glitched = BitplaneGlitchEngine.xor_sierpinski_glitch(frame, scale=scale, blend=blend, formula=formula)
            bmp_bytes = glitched.to_bmp()
            self._send_json({
                "status": "success",
                "formula": formula,
                "scale": scale,
                "blend": blend,
                "width": glitched.width,
                "height": glitched.height,
                "image_base64": base64.b64encode(bmp_bytes).decode("ascii")
            })
            return

        self._send_json({"error": f"Endpoint not found: {path}"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def run_ui_server(host: str = "0.0.0.0", port: int = 8081) -> ThreadingHTTPServer:
    """Launch UI HTTP Server."""
    server = ThreadingHTTPServer((host, port), DatamoshHTTPHandler)
    return server
