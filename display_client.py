#!/usr/bin/env python3
"""Touchscreen client for the computer stats server."""

import argparse
import ctypes
import fcntl
from functools import lru_cache
import json
import mmap
import os
import select
import struct
import sys
import threading
import time
import urllib.error
import urllib.request


class BitField(ctypes.Structure):
    _fields_ = [("offset", ctypes.c_uint32), ("length", ctypes.c_uint32), ("msb_right", ctypes.c_uint32)]


class FBVarScreenInfo(ctypes.Structure):
    _fields_ = [
        ("xres", ctypes.c_uint32), ("yres", ctypes.c_uint32),
        ("xres_virtual", ctypes.c_uint32), ("yres_virtual", ctypes.c_uint32),
        ("xoffset", ctypes.c_uint32), ("yoffset", ctypes.c_uint32),
        ("bits_per_pixel", ctypes.c_uint32), ("grayscale", ctypes.c_uint32),
        ("red", BitField), ("green", BitField), ("blue", BitField), ("transp", BitField),
        ("nonstd", ctypes.c_uint32), ("activate", ctypes.c_uint32),
        ("height", ctypes.c_uint32), ("width", ctypes.c_uint32),
        ("accel_flags", ctypes.c_uint32), ("pixclock", ctypes.c_uint32),
        ("left_margin", ctypes.c_uint32), ("right_margin", ctypes.c_uint32),
        ("upper_margin", ctypes.c_uint32), ("lower_margin", ctypes.c_uint32),
        ("hsync_len", ctypes.c_uint32), ("vsync_len", ctypes.c_uint32),
        ("sync", ctypes.c_uint32), ("vmode", ctypes.c_uint32),
        ("rotate", ctypes.c_uint32), ("colorspace", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 4),
    ]


class FBFixScreenInfo(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_char * 16), ("smem_start", ctypes.c_ulong),
        ("smem_len", ctypes.c_uint32), ("type", ctypes.c_uint32),
        ("type_aux", ctypes.c_uint32), ("visual", ctypes.c_uint32),
        ("xpanstep", ctypes.c_uint16), ("ypanstep", ctypes.c_uint16),
        ("ywrapstep", ctypes.c_uint16), ("line_length", ctypes.c_uint32),
        ("mmio_start", ctypes.c_ulong), ("mmio_len", ctypes.c_uint32),
        ("accel", ctypes.c_uint32), ("capabilities", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16 * 2),
    ]


FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602


def log(message):
    print(message, file=sys.stderr, flush=True)


def choose_framebuffer(requested, width, height):
    if requested != "auto":
        return requested
    for path in ("/dev/fb1", "/dev/fb0"):
        if not os.path.exists(path):
            continue
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                var = FBVarScreenInfo()
                fcntl.ioctl(fd, FBIOGET_VSCREENINFO, var, True)
                if (var.xres, var.yres) == (width, height):
                    return path
            finally:
                os.close(fd)
        except OSError:
            continue
    existing = [path for path in ("/dev/fb1", "/dev/fb0") if os.path.exists(path)]
    return existing[0] if existing else "/dev/fb1"


class TouchReader:
    EV_KEY = 1
    BTN_TOUCH = 330

    def __init__(self, device="auto"):
        candidates = [device] if device != "auto" else sorted(
            f"/dev/input/event{index}" for index in range(16)
            if os.path.exists(f"/dev/input/event{index}")
        )
        self.files = []
        self.event_format = "llHHi"
        self.event_size = struct.calcsize(self.event_format)
        for path in candidates:
            try:
                handle = open(path, "rb", buffering=0)
                os.set_blocking(handle.fileno(), False)
                self.files.append(handle)
                log(f"Touch input candidate: {path}")
            except OSError as exc:
                log(f"Touch input unavailable ({path}): {exc}")
        if not self.files:
            log("Touch input: no readable /dev/input/event* devices")

    def poll_press(self):
        readable, _, _ = select.select(self.files, [], [], 0)
        pressed = False
        for handle in readable:
            try:
                while True:
                    event = handle.read(self.event_size)
                    if not event or len(event) != self.event_size:
                        break
                    _, _, event_type, code, value = struct.unpack(self.event_format, event)
                    if event_type == self.EV_KEY and code == self.BTN_TOUCH and value == 1:
                        pressed = True
            except (BlockingIOError, OSError):
                pass
        return pressed

    def close(self):
        for handle in self.files:
            handle.close()


def framebuffer_line_length(fd, bits_per_pixel, width):
    info = FBFixScreenInfo()
    try:
        fcntl.ioctl(fd, FBIOGET_FSCREENINFO, info, True)
        if info.line_length:
            return info.line_length
    except OSError:
        pass
    return width * max(1, (bits_per_pixel + 7) // 8)


class DirectFramebuffer:
    """Minimal framebuffer writer for SPI LCDs exposed as /dev/fbN."""

    def __init__(self, path, width, height):
        self.path = path
        self.fd = os.open(path, os.O_RDWR)
        var = FBVarScreenInfo()
        fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, var, True)
        self.device_width, self.device_height = var.xres, var.yres
        if self.device_width < 1 or self.device_height < 1:
            raise RuntimeError(f"{path} reported an invalid resolution")
        if (width, height) != (self.device_width, self.device_height):
            os.close(self.fd)
            raise RuntimeError(
                f"{path} is {self.device_width}x{self.device_height}; "
                f"use --width {self.device_width} --height {self.device_height}"
            )
        self.width, self.height = width, height
        self.bpp = var.bits_per_pixel
        if self.bpp not in (16, 24, 32):
            os.close(self.fd)
            raise RuntimeError(f"unsupported framebuffer depth: {self.bpp} bpp")
        self.line_length = framebuffer_line_length(self.fd, self.bpp, width)
        self.pixel_fields = (var.red, var.green, var.blue)
        try:
            import numpy
            self.numpy = numpy
        except ImportError as exc:
            self.close_file()
            raise RuntimeError("direct framebuffer mode requires numpy") from exc
        self.mapping = mmap.mmap(
            self.fd, self.line_length * self.height, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ
        )

    @staticmethod
    def channel(value, field):
        return (value * ((1 << field.length) - 1) // 255) << field.offset

    def present(self, surface, pygame):
        rgb = pygame.image.tostring(surface, "RGB")
        red, green, blue = self.pixel_fields
        pixels = self.numpy.frombuffer(rgb, dtype=self.numpy.uint8).reshape(self.height, self.width, 3)
        if self.bpp == 16:
            packed = (
                (pixels[:, :, 0].astype(self.numpy.uint32) >> (8 - red.length)) << red.offset
                | (pixels[:, :, 1].astype(self.numpy.uint32) >> (8 - green.length)) << green.offset
                | (pixels[:, :, 2].astype(self.numpy.uint32) >> (8 - blue.length)) << blue.offset
            ).astype("<u2")
            if self.line_length == self.width * 2:
                self.mapping[:] = packed.tobytes()
            else:
                for row in range(self.height):
                    start = row * self.line_length
                    self.mapping[start:start + self.width * 2] = packed[row].tobytes()
            return

        pixel_size = self.bpp // 8
        packed = (
            (pixels[:, :, 0].astype(self.numpy.uint32) >> (8 - red.length)) << red.offset
            | (pixels[:, :, 1].astype(self.numpy.uint32) >> (8 - green.length)) << green.offset
            | (pixels[:, :, 2].astype(self.numpy.uint32) >> (8 - blue.length)) << blue.offset
        )
        frame = packed.astype("<u4").tobytes() if pixel_size == 4 else packed.astype(self.numpy.uint32).astype("<u4").view(self.numpy.uint8).reshape(self.height, self.width, 4)[:, :, :3].tobytes()
        if self.line_length == self.width * pixel_size:
            self.mapping[:] = frame
        else:
            row_size = self.width * pixel_size
            for row in range(self.height):
                start = row * self.line_length
                self.mapping[start:start + row_size] = frame[row * row_size:(row + 1) * row_size]

    def close(self):
        self.mapping.close()
        os.close(self.fd)

    def close_file(self):
        os.close(self.fd)


class DisplayBackend:
    def __init__(self, pygame, args):
        self.pygame = pygame
        self.framebuffer = None
        args.fbdev = choose_framebuffer(args.fbdev, args.width, args.height)
        driver = args.display_driver
        if driver == "auto":
            driver = "framebuffer" if os.path.exists(args.fbdev) else "kmsdrm"
        if args.desktop and args.display_driver == "auto":
            driver = "sdl"
        log(f"Display backend: {driver}")
        if driver == "framebuffer":
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            self.screen = pygame.Surface((args.width, args.height))
            try:
                self.framebuffer = DirectFramebuffer(args.fbdev, args.width, args.height)
            except Exception as exc:
                raise RuntimeError(f"could not open SPI framebuffer {args.fbdev}: {exc}") from exc
            log(f"SPI framebuffer: {self.framebuffer.device_width}x{self.framebuffer.device_height}, {self.framebuffer.bpp} bpp ({args.fbdev})")
        else:
            if not args.desktop:
                os.environ.setdefault("SDL_VIDEODRIVER", driver)
                os.environ.setdefault("SDL_INPUT_LINUXEV", "0")
            self.screen = pygame.display.set_mode((args.width, args.height))
            log(f"SDL display: {pygame.display.get_driver()} at {args.width}x{args.height}")

    def flip(self):
        if self.framebuffer:
            self.framebuffer.present(self.screen, self.pygame)
        else:
            self.pygame.display.flip()

    def close(self):
        if self.framebuffer:
            self.framebuffer.close()


def arguments():
    parser = argparse.ArgumentParser(description="Raspberry Pi stats display")
    parser.add_argument("--url", default=os.environ.get("STATS_SERVER_URL", "http://192.168.1.53:8282/stats"))
    parser.add_argument("--token", default=os.environ.get("STATS_TOKEN", ""))
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=320)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--touch-device", default="auto", help="Linux event device, or auto to read all event devices")
    parser.add_argument("--display-driver", default="auto", choices=("auto", "framebuffer", "kmsdrm"))
    parser.add_argument("--fbdev", default="auto", help="framebuffer device, or auto (checks /dev/fb1 then /dev/fb0)")
    return parser.parse_args()


class Poller:
    def __init__(self, url, token):
        self.url, self.token = url, token
        self.data, self.error = None, "Connecting..."
        self.connected = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        log(f"Connecting to stats server: {self.url}")
        while not self.stop_event.is_set():
            try:
                headers = {"Accept": "application/json"}
                if self.token:
                    headers["X-Stats-Token"] = self.token
                request = urllib.request.Request(self.url, headers=headers)
                with urllib.request.urlopen(request, timeout=3) as response:
                    data = json.load(response)
                if not isinstance(data, dict):
                    raise ValueError("invalid server response")
                with self.lock:
                    was_connected = self.connected
                    self.data, self.error = data, ""
                    self.connected = True
                if not was_connected:
                    log(f"Connected to stats server: {self.url}")
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                with self.lock:
                    was_connected = self.connected
                    self.error = str(exc)
                    self.connected = False
                if was_connected is not False:
                    log(f"Stats server disconnected: {exc}")
            self.stop_event.wait(1.0)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def snapshot(self):
        with self.lock:
            return self.data, self.error


def human_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            return f"{int(value)} B" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024


def uptime(seconds):
    seconds = int(seconds or 0)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    return f"{days}d {hours:02d}h {minutes:02d}m" if days else f"{hours:02d}h {minutes:02d}m"


def shortened(value, limit=22):
    value = str(value)
    return value if len(value) <= limit else value[:limit - 1] + "…"


def main():
    args = arguments()
    try:
        import pygame
    except ImportError:
        print("pygame is required: python3 -m pip install pygame", file=sys.stderr)
        return 2

    if not args.desktop:
        probe_fbdev = choose_framebuffer(args.fbdev, args.width, args.height)
        if args.display_driver == "framebuffer" or (
            args.display_driver == "auto" and os.path.exists(probe_fbdev)
        ):
            os.environ["SDL_VIDEODRIVER"] = "dummy"
        else:
            os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
            os.environ["SDL_INPUT_LINUXEV"] = "1"
            spi_card = "/dev/dri/by-path/platform-3f204000.spi-cs-0-card"
            os.environ.setdefault("SDL_KMSDRM_DEVICE", spi_card if os.path.exists(spi_card) else "/dev/dri/card0")
    pygame.init()
    try:
        backend = DisplayBackend(pygame, args)
        screen = backend.screen
    except (pygame.error, RuntimeError, OSError) as exc:
        log(f"Could not open display: {exc}")
        pygame.quit()
        return 2
    pygame.mouse.set_visible(args.desktop)
    touch_reader = TouchReader(args.touch_device) if backend.framebuffer else None
    clock = pygame.time.Clock()
    font_name = pygame.font.match_font("dejavusansmono")
    title_font = pygame.font.Font(font_name, max(18, args.height // 16))
    heading_font = pygame.font.Font(font_name, max(16, args.height // 18))
    body_font = pygame.font.Font(font_name, max(15, args.height // 20))
    small_font = pygame.font.Font(font_name, max(12, args.height // 25))

    background, panel = (5, 4, 16), (16, 10, 30)
    text, muted = (218, 250, 255), (105, 121, 158)
    accent, blue, warn, error_color = (0, 255, 204), (0, 210, 255), (255, 214, 58), (255, 42, 166)
    magenta, grid = (255, 42, 166), (24, 18, 52)
    titles = ("SYSTEM", "TOP PROCESSES")
    page = 0
    poller = Poller(args.url, args.token)
    poller.start()

    @lru_cache(maxsize=256)
    def render_text(font, value, color):
        return font.render(str(value), True, color)

    def put(value, x, y, font=body_font, color=text):
        screen.blit(render_text(font, str(value), color), (x, y))

    def center(value, y, font=body_font, color=text):
        rendered = render_text(font, str(value), color)
        screen.blit(rendered, ((args.width - rendered.get_width()) // 2, y))

    def bar(x, y, width, height, percent, color):
        pygame.draw.rect(screen, (24, 18, 48), (x, y, width, height), border_radius=1)
        fill = max(0, min(width, int(width * float(percent or 0) / 100)))
        if fill:
            pygame.draw.rect(screen, color, (x, y, fill, height), border_radius=1)
            pygame.draw.line(screen, (235, 255, 255), (x, y), (x + fill - 1, y))

    def hud_panel(x, y, width, height, color):
        pygame.draw.rect(screen, panel, (x, y, width, height), border_radius=4)
        pygame.draw.rect(screen, color, (x, y, width, height), 2, border_radius=4)

    def draw_system(data):
        margin, gap = 18, 10
        card_width = (args.width - margin * 2 - gap) // 2
        positions = ((margin, 58), (margin + card_width + gap, 58), (margin, 154), (margin + card_width + gap, 154))
        memory, disk = data.get("memory", {}), data.get("disk", {})
        cards = (
            ("CPU", f"{data.get('cpu_percent', 0):.1f}%", data.get("cpu_percent", 0), blue),
            ("MEMORY", f"{memory.get('percent', 0):.1f}%", memory.get("percent", 0), accent),
            ("DISK", f"{disk.get('percent', 0):.1f}%", disk.get("percent", 0), warn),
            ("UPTIME", uptime(data.get("uptime", 0)), None, accent),
        )
        for (x, y), (name, value, percent, color) in zip(positions, cards):
            hud_panel(x, y, card_width, 86, color)
            put(name, x + 10, y + 9, small_font, muted)
            put(value, x + 10, y + 31, heading_font)
            if percent is not None:
                bar(x + 10, y + 66, card_width - 20, 8, percent, color)
        net = data.get("network", {})
        detail = f"{shortened(data.get('hostname', 'Computer'), 28)}  •  ↓ {human_bytes(net.get('bytes_recv'))}  ↑ {human_bytes(net.get('bytes_sent'))}"
        center(detail, 258, small_font, muted)

    def draw_apps(data):
        categories = (
            ("CPU", data.get("cpu_apps", []), "cpu", blue),
            ("MEMORY", data.get("memory_apps", []), "memory", accent),
            ("DISK I/O", data.get("io_apps", []), "io", warn),
        )
        for section, (label, apps, kind, color) in enumerate(categories):
            top = 56 + section * 76
            hud_panel(14, top - 4, args.width - 28, 70, color)
            put(label, 24, top + 1, heading_font, color)
            if not apps:
                put("No process data", 26, top + 27, small_font, muted)
                continue
            for index, app in enumerate(apps[:3]):
                y = top + 24 + index * 14
                put(index + 1, 27, y, small_font, muted)
                put(shortened(app.get("name", "unknown"), 22), 47, y, small_font)
                if kind == "cpu":
                    value = f"{app.get('cpu', 0):.1f}%"
                elif kind == "memory":
                    value = human_bytes(app.get("memory_bytes"))
                else:
                    value = f"R {human_bytes(app.get('read_bps'))}  W {human_bytes(app.get('write_bps'))}/s"
                rendered = render_text(small_font, value, color)
                screen.blit(rendered, (args.width - 26 - rendered.get_width(), y))

    running = True
    last_render_key = None
    while running:
        change = 1 if touch_reader and touch_reader.poll_press() else 0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key in (pygame.K_RIGHT, pygame.K_SPACE, pygame.K_RETURN):
                    change = 1
                elif event.key == pygame.K_LEFT:
                    change = -1
            elif event.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
                change = 1
        page = (page + change) % len(titles)

        data, fetch_error = poller.snapshot()
        age = (
            max(0, int(time.time() - float(data.get("timestamp", time.time()))))
            if data else 0
        )
        render_key = (
            page,
            data.get("timestamp") if data else None,
            fetch_error,
            age,
        )
        if render_key == last_render_key:
            clock.tick(15 if backend.framebuffer else 30)
            continue
        last_render_key = render_key

        screen.fill(background)
        put(titles[page], 16, 14, title_font, blue)
        dots = render_text(small_font, "  ".join("◆" if i == page else "◇" for i in range(len(titles))), accent)
        screen.blit(dots, ((args.width - dots.get_width()) // 2, 18))
        pygame.draw.line(screen, grid, (16, 48), (args.width - 16, 48))
        if data:
            if page == 0:
                draw_system(data)
            else:
                draw_apps(data)
            if fetch_error:
                put("Reconnecting...", 16, args.height - 27, small_font, error_color)
            else:
                put(f"updated {age:.0f}s ago", 16, args.height - 27, small_font, muted)
        else:
            center("Waiting for stats server", 105, heading_font)
            center(shortened(fetch_error, 62), 145, body_font, error_color)
            center(args.url, 185, small_font, muted)

        center("TAP TO SWITCH", args.height - 25, small_font, magenta)
        backend.flip()
        clock.tick(15 if backend.framebuffer else 30)

    poller.stop()
    if touch_reader:
        touch_reader.close()
    backend.close()
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
