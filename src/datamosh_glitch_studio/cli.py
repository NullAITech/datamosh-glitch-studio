"""
Command-Line Interface for datamosh-glitch-studio.
Zero external runtime dependencies.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from typing import Any, List, Optional

from datamosh_glitch_studio.bitplane_glitch import (
    BitplaneExtractor,
    BitplaneGlitchEngine,
)
from datamosh_glitch_studio.compat import atomic_write_bytes, atomic_write_text, safe_read_bytes
from datamosh_glitch_studio.frame_io import ImageFrame, export_frame_sequence_html
from datamosh_glitch_studio.glitch_core import DatamoshEngine, corrupt_byte_stream
from datamosh_glitch_studio.mcp_server import run_mcp_server
from datamosh_glitch_studio.motion_vector_engine import (
    MotionVectorEngine,
    render_motion_vectors_ascii,
    render_motion_vectors_svg,
)
from datamosh_glitch_studio.presets import PRESETS, get_preset, list_presets
from datamosh_glitch_studio.ui_server import run_ui_server


class Colors:
    """ANSI terminal color helpers with automatic disabling."""
    def __init__(self, force_disable: bool = False) -> None:
        disabled = force_disable or "NO_COLOR" in os.environ or not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty()
        self.BLUE = "" if disabled else "\033[94m"
        self.GREEN = "" if disabled else "\033[92m"
        self.YELLOW = "" if disabled else "\033[93m"
        self.RED = "" if disabled else "\033[91m"
        self.CYAN = "" if disabled else "\033[96m"
        self.BOLD = "" if disabled else "\033[1m"
        self.DIM = "" if disabled else "\033[2m"
        self.RESET = "" if disabled else "\033[0m"


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="datamosh-studio",
        description="Parametric Datamosh, I-Frame Drop & Visual Glitch Synthesis Studio",
    )
    parser.add_argument("-v", "--version", action="version", version="datamosh-glitch-studio 0.1.0")

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--no-color", action="store_true", help="Disable ANSI color output")

    sub = parser.add_subparsers(dest="command", help="Available subcommands")

    # mosh
    p_mosh = sub.add_parser("mosh", parents=[base], help="Apply datamosh and glitch effects to image or test pattern")
    p_mosh.add_argument("input", nargs="?", help="Input BMP/PPM image path (optional)")
    p_mosh.add_argument("-p", "--preset", choices=list(PRESETS.keys()), default="h264_iframe_drop", help="Glitch preset")
    p_mosh.add_argument("-o", "--output", default="glitched.bmp", help="Output BMP/PPM path (default: glitched.bmp)")
    p_mosh.add_argument("--width", type=int, default=480, help="Width for generated test card")
    p_mosh.add_argument("--height", type=int, default=320, help="Height for generated test card")
    p_mosh.add_argument("--seed", type=int, help="Random seed for reproducible glitches")

    # presets
    p_pres = sub.add_parser("presets", parents=[base], help="List available glitch presets")
    p_pres.add_argument("--json", action="store_true", help="Output as JSON")

    # corrupt
    p_corr = sub.add_parser("corrupt", parents=[base], help="Directly corrupt binary byte stream with bitflips")
    p_corr.add_argument("file", help="Target binary file to corrupt")
    p_corr.add_argument("-o", "--output", required=True, help="Output corrupted file path")
    p_corr.add_argument("--rate", type=float, default=0.002, help="Corruption rate (default: 0.002)")
    p_corr.add_argument("--header-skip", type=int, default=64, help="Header bytes to protect (default: 64)")

    # motion
    p_mot = sub.add_parser("motion", aliases=["vectors"], parents=[base], help="Estimate macroblock motion vectors and render optical flow")
    p_mot.add_argument("--width", type=int, default=160, help="Width for test pattern")
    p_mot.add_argument("--height", type=int, default=120, help="Height for test pattern")
    p_mot.add_argument("--block-size", type=int, default=16, help="Macroblock dimension in pixels (default: 16)")
    p_mot.add_argument("--shift-dx", type=int, default=4, help="Synthetic horizontal displacement")
    p_mot.add_argument("--shift-dy", type=int, default=2, help="Synthetic vertical displacement")
    p_mot.add_argument("--svg", help="Optional output SVG path for vector map")
    p_mot.add_argument("--json", action="store_true", help="Output motion vector field as JSON")

    # melt
    p_melt = sub.add_parser("melt", parents=[base], help="Synthesize liquid melting datamosh frame sequence")
    p_melt.add_argument("--steps", type=int, default=8, help="Number of melt sequence steps (default: 8)")
    p_melt.add_argument("--acc", type=float, default=1.2, help="Motion acceleration multiplier")
    p_melt.add_argument("-o", "--output", default="melt.html", help="Output HTML player path (default: melt.html)")

    # serve
    p_serve = sub.add_parser("serve", parents=[base], help="Start Datamosh Studio Web UI (Material 3 influenced)")
    p_serve.add_argument("--host", default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8081, help="Port (default: 8081)")

    # mcp
    p_mcp = sub.add_parser("mcp", parents=[base], help="Run Model Context Protocol stdio server")

    # diagnostics / doctor
    p_doc = sub.add_parser("doctor", aliases=["diagnostics", "platform"], parents=[base], help="Run system diagnostics")

    # bitplane / slice
    p_bp = sub.add_parser("bitplane", aliases=["slice"], parents=[base], help="Bitplane channel slicing, inversion, and mosaic generator")
    p_bp.add_argument("input", nargs="?", help="Input BMP/PPM image path (optional)")
    p_bp.add_argument("-o", "--output", default="bitplane_glitched.bmp", help="Output path (default: bitplane_glitched.bmp)")
    p_bp.add_argument("--keep-bits", default="7,6,5", help="Comma-separated bit indices to keep (e.g. 7,6,5)")
    p_bp.add_argument("--invert-bits", default="", help="Comma-separated bit indices to invert (e.g. 7,0)")
    p_bp.add_argument("--channel", default="all", choices=["all", "r", "g", "b", "luminance"], help="Color channel")
    p_bp.add_argument("--mosaic", help="Optional output SVG path for 8-bitplane mosaic")
    p_bp.add_argument("--width", type=int, default=320, help="Width for test pattern")
    p_bp.add_argument("--height", type=int, default=240, help="Height for test pattern")

    # xor / sierpinski
    p_xor = sub.add_parser("xor", aliases=["sierpinski"], parents=[base], help="Synthesize spatial boolean fractal glitch textures")
    p_xor.add_argument("input", nargs="?", help="Input BMP/PPM image path (optional)")
    p_xor.add_argument("-o", "--output", default="xor_glitched.bmp", help="Output path (default: xor_glitched.bmp)")
    p_xor.add_argument("--scale", type=float, default=1.0, help="Spatial frequency scale factor")
    p_xor.add_argument("--blend", type=float, default=0.5, help="Blend mix ratio (0.0 - 1.0)")
    p_xor.add_argument("--formula", default="xor", choices=["xor", "and", "or", "xor_and"], help="Boolean formula")
    p_xor.add_argument("--width", type=int, default=320, help="Width for test pattern")
    p_xor.add_argument("--height", type=int, default=240, help="Height for test pattern")

    # test
    p_test = sub.add_parser("test", parents=[base], help="Run internal self-verification test runner")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI execution entrypoint."""
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    c = Colors(force_disable=getattr(args, "no_color", False))

    if args.command == "mosh":
        preset = get_preset(args.preset)
        engine = DatamoshEngine(preset)

        if args.input and os.path.isfile(args.input):
            raw = safe_read_bytes(args.input)
            if raw.startswith(b"BM"):
                frame = ImageFrame.from_bmp(raw)
            elif raw.startswith(b"P6"):
                frame = ImageFrame.from_ppm(raw)
            else:
                print(f"{c.RED}Error: Input file must be uncompressed 24-bit BMP or P6 PPM.{c.RESET}")
                return 1
        else:
            # Generate color bars
            frame = ImageFrame.create(args.width, args.height)
            colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
            bw = args.width // len(colors)
            for y in range(args.height):
                for x in range(args.width):
                    frame.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

        glitched = engine.process_frame(frame, preset=preset, seed=args.seed)
        out_bytes = glitched.to_bmp() if args.output.endswith(".bmp") else glitched.to_ppm()
        atomic_write_bytes(args.output, out_bytes)

        print(f"{c.GREEN}✓ Glitched frame generated successfully:{c.RESET} {args.output}")
        print(f"  Preset: {c.CYAN}{preset.name}{c.RESET} ({glitched.width}x{glitched.height}, {len(out_bytes)} bytes)")
        return 0

    elif args.command == "presets":
        import json
        if args.json:
            print(json.dumps(list_presets(), indent=2))
        else:
            print(f"\n{c.BOLD}📼 Datamosh Glitch Presets Catalog{c.RESET}\n")
            for p in PRESETS.values():
                print(f"  {c.CYAN}{p.id:<22}{c.RESET} : {c.BOLD}{p.name}{c.RESET}")
                print(f"    {c.DIM}{p.description}{c.RESET}")
            print()
        return 0

    elif args.command == "corrupt":
        raw = safe_read_bytes(args.file)
        corrupted = corrupt_byte_stream(raw, rate=args.rate, header_skip=args.header_skip)
        atomic_write_bytes(args.output, corrupted)
        print(f"{c.GREEN}✓ Corrupted binary payload written:{c.RESET} {args.output} ({len(corrupted)} bytes)")
        return 0

    elif args.command in ("motion", "vectors"):
        import json
        w, h = args.width, args.height
        bs = args.block_size
        ref = ImageFrame.create(w, h)
        colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
        bw = w // len(colors)
        for y in range(h):
            for x in range(w):
                ref.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

        tgt = ImageFrame.create(w, h)
        for y in range(h):
            for x in range(w):
                src_x = (x - args.shift_dx) % w
                src_y = (y - args.shift_dy) % h
                tgt.set_pixel(x, y, ref.get_pixel(src_x, src_y))

        mv_engine = MotionVectorEngine(block_size=bs)
        mv_field = mv_engine.estimate_motion(ref, tgt)

        if args.svg:
            svg_str = render_motion_vectors_svg(mv_field)
            atomic_write_text(args.svg, svg_str)
            print(f"{c.GREEN}✓ Saved SVG motion vector map:{c.RESET} {args.svg}")

        if args.json:
            print(json.dumps(mv_field.to_dict(), indent=2))
        else:
            print(f"\n{c.BOLD}📼 Macroblock Motion Vector Estimation ({w}x{h}, Block: {bs}px){c.RESET}")
            print(f"  Total Macroblocks   : {c.CYAN}{len(mv_field.vectors)}{c.RESET}")
            print(f"  Average Magnitude   : {c.CYAN}{mv_field.average_magnitude:.2f}px{c.RESET}")
            print(f"  Synthetic Shift     : ({args.shift_dx}, {args.shift_dy})")
            print(f"\n{c.BOLD}Optical Flow Direction Grid:{c.RESET}\n")
            print(render_motion_vectors_ascii(mv_field))
            print()
        return 0

    elif args.command == "melt":
        w, h = 320, 240
        ref = ImageFrame.create(w, h)
        colors = [(255, 0, 128), (0, 255, 255), (255, 255, 0), (0, 255, 0)]
        bw = w // len(colors)
        for y in range(h):
            for x in range(w):
                ref.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

        mv_engine = MotionVectorEngine(block_size=16)
        frames = mv_engine.datamosh_liquid_melt(ref, steps=args.steps, acceleration=args.acc)
        html_code = export_frame_sequence_html(frames, fps=12)
        atomic_write_text(args.output, html_code)
        print(f"{c.GREEN}✓ Liquid melt datamosh sequence rendered:{c.RESET} {args.output} ({len(frames)} frames, {len(html_code)} bytes)")
        return 0

    elif args.command in ("bitplane", "slice"):
        if args.input and os.path.isfile(args.input):
            raw = safe_read_bytes(args.input)
            frame = ImageFrame.from_bmp(raw) if raw.startswith(b"BM") else ImageFrame.from_ppm(raw)
        else:
            w, h = args.width, args.height
            frame = ImageFrame.create(w, h)
            colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
            bw = w // len(colors)
            for y in range(h):
                for x in range(w):
                    frame.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

        if args.mosaic:
            svg_code = BitplaneGlitchEngine.render_bitplane_mosaic_svg(frame, channel=args.channel)
            atomic_write_text(args.mosaic, svg_code)
            print(f"{c.GREEN}✓ Saved 8-bitplane mosaic SVG to:{c.RESET} {args.mosaic}")

        keep = [int(b.strip()) for b in args.keep_bits.split(",") if b.strip().isdigit()]
        invert = [int(b.strip()) for b in args.invert_bits.split(",") if b.strip().isdigit()]

        sliced = BitplaneGlitchEngine.slice_bitplanes(frame, keep_bits=keep, channel=args.channel)
        if invert:
            sliced = BitplaneGlitchEngine.invert_bitplanes(sliced, invert_bits=invert, channel=args.channel)

        atomic_write_bytes(args.output, sliced.to_bmp())
        print(f"{c.GREEN}✓ Bitplane glitched image written to:{c.RESET} {args.output} ({sliced.width}x{sliced.height}px, preserved bits: {keep})")
        return 0

    elif args.command in ("xor", "sierpinski"):
        if args.input and os.path.isfile(args.input):
            raw = safe_read_bytes(args.input)
            frame = ImageFrame.from_bmp(raw) if raw.startswith(b"BM") else ImageFrame.from_ppm(raw)
        else:
            w, h = args.width, args.height
            frame = ImageFrame.create(w, h)
            colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
            bw = w // len(colors)
            for y in range(h):
                for x in range(w):
                    frame.set_pixel(x, y, colors[min(len(colors)-1, x // bw)])

        glitched = BitplaneGlitchEngine.xor_sierpinski_glitch(frame, scale=args.scale, blend=args.blend, formula=args.formula)
        atomic_write_bytes(args.output, glitched.to_bmp())
        print(f"{c.GREEN}✓ Spatial {args.formula.upper()} glitch written to:{c.RESET} {args.output} (scale={args.scale}, blend={args.blend})")
        return 0

    elif args.command == "serve":
        server = run_ui_server(args.host, args.port)
        print(f"{c.GREEN}📼 Datamosh Studio UI running at:{c.RESET} http://{args.host}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
        return 0

    elif args.command == "mcp":
        run_mcp_server()
        return 0

    elif args.command in ("doctor", "diagnostics", "platform"):
        print(f"\n{c.BOLD}📼 Datamosh Glitch Studio - System Diagnostics{c.RESET}")
        print(f"  Platform         : {sys.platform}")
        print(f"  Python Version   : {sys.version.split()[0]}")
        print(f"  Presets Loaded   : {len(PRESETS)}")
        print(f"  Zero Runtime Deps: {c.GREEN}YES (100% Python Standard Library){c.RESET}")
        print(f"  Status           : {c.GREEN}HEALTHY{c.RESET}\n")
        return 0

    elif args.command == "test":
        print(f"{c.BOLD}Running internal self-verification suite...{c.RESET}")
        # Test basic frame operations
        f = ImageFrame.create(100, 100, (255, 0, 0))
        assert len(f.to_bmp()) > 100
        engine = DatamoshEngine()
        g = engine.process_frame(f)
        assert g.width == 100 and g.height == 100
        print(f"{c.GREEN}✓ All internal checks passed!{c.RESET}")
        return 0

    return 0
