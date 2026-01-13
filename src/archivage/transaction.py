"""
Twitter x-client-transaction-id header generation.

Reimplemented based on antibot.blog research:
- https://antibot.blog/posts/1741552025433
- https://antibot.blog/posts/1741552092462
- https://antibot.blog/posts/1741552163416
"""

import re
import math
import time
import random
import hashlib
import binascii
import itertools


# Text extraction helpers

def extractBetween(text: str, start: str, end: str, default: str = "") -> str:
    """Extract text between start and end markers."""
    try:
        i = text.index(start) + len(start)
        return text[i:text.index(end, i)]
    except ValueError:
        return default


def extractAll(text: str, start: str, end: str):
    """Yield all text segments between start and end markers."""
    pos = 0
    try:
        while True:
            i = text.index(start, pos) + len(start)
            j = text.index(end, i)
            yield text[i:j]
            pos = j + len(end)
    except ValueError:
        return


# Math helpers

def cubicCalc(a: float, b: float, m: float) -> float:
    """Cubic bezier calculation."""
    m1 = 1.0 - m
    return 3.0 * a * m1 * m1 * m + 3.0 * b * m1 * m * m + m * m * m


def cubicValue(curve: list, t: float) -> float:
    """Evaluate cubic bezier curve at time t."""
    if t <= 0.0:
        if curve[0] > 0.0:
            return (curve[1] / curve[0]) * t
        if curve[1] == 0.0 and curve[2] > 0.0:
            return (curve[3] / curve[2]) * t
        return 0.0

    if t >= 1.0:
        if curve[2] < 1.0:
            v = (curve[3] - 1.0) / (curve[2] - 1.0)
        elif curve[2] == 1.0 and curve[0] < 1.0:
            v = (curve[1] - 1.0) / (curve[0] - 1.0)
        else:
            v = 0.0
        return 1.0 + v * (t - 1.0)

    lo, hi = 0.0, 1.0
    while lo < hi:
        mid = (lo + hi) / 2.0
        est = cubicCalc(curve[0], curve[2], mid)
        if abs(t - est) < 0.00001:
            return cubicCalc(curve[1], curve[3], mid)
        if est < t:
            lo = mid
        else:
            hi = mid
    return cubicCalc(curve[1], curve[3], mid)


def interpolate(x: float, a: float, b: float) -> float:
    """Linear interpolation."""
    return a * (1.0 - x) + b * x


def rotationMatrix(deg: float) -> list:
    """2D rotation matrix from degrees."""
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return [c, -s, s, c]


def floatToHex(n: float) -> str:
    """Convert float to hex string."""
    i = int(n)
    frac = n - i
    if not frac:
        return hex(i)[2:]
    result = ["."]
    while frac > 0.0:
        frac *= 16.0
        digit = int(frac)
        frac -= digit
        result.append(chr(digit + 87) if digit > 9 else str(digit))
    return hex(i)[2:] + "".join(result)


def scale(val: float, vmin: float, vmax: float, rounding: bool) -> float:
    """Scale value from 0-255 to vmin-vmax range."""
    result = val * (vmax - vmin) / 255.0 + vmin
    return math.floor(result) if rounding else round(result, 2)


def jsRound(n: float) -> int:
    """JavaScript-style rounding."""
    floor = math.floor(n)
    return floor if (n - floor) < 0.5 else math.ceil(n)


class TransactionId:
    """Generates x-client-transaction-id headers for Twitter API."""

    def __init__(self):
        self.key_bytes = None
        self.animation_key = None

    def initialize(self, homepage: str, fetchJs):
        """
        Initialize from Twitter homepage HTML.

        Args:
            homepage: HTML content of x.com homepage
            fetchJs: Callable that fetches JS URL and returns text
        """
        # Extract verification key from meta tag
        pos = homepage.find('name="twitter-site-verification"')
        if pos == -1:
            raise ValueError("Could not find twitter-site-verification")
        beg = homepage.rfind("<", 0, pos)
        end = homepage.find(">", pos)
        key = extractBetween(homepage[beg:end], 'content="', '"')
        if not key:
            raise ValueError("Could not extract verification key")
        self.key_bytes = binascii.a2b_base64(key)

        # Extract ondemand.s hash and fetch JS for indices
        ondemand_s = extractBetween(homepage, '"ondemand.s":"', '"')
        if not ondemand_s:
            raise ValueError("Could not find ondemand.s")
        js_url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{ondemand_s}a.js"
        js_text = fetchJs(js_url)
        indices = [int(i) for i in re.findall(r"\(\w\[(\d\d?)\],\s*16\)", js_text)]
        if not indices:
            raise ValueError("Could not extract indices from JS")

        # Extract animation frames from SVG
        frames = list(extractAll(homepage, 'id="loading-x-anim-', "</svg>"))
        if not frames:
            raise ValueError("Could not extract animation frames")

        # Calculate animation key
        self.animation_key = self._calcAnimationKey(frames, indices)

    def _calcAnimationKey(self, frames: list, indices: list) -> str:
        """Calculate animation key from frames and indices."""
        kb = self.key_bytes
        frame = frames[kb[5] % 4]
        array = self._parse2dArray(frame)
        row = array[kb[indices[0]] % 16]

        # Calculate frame time
        frame_time = 1
        for idx in indices[1:]:
            frame_time *= kb[idx] % 16
        frame_time = jsRound(frame_time / 10) * 10
        target = frame_time / 4096

        return self._animate(row, target)

    def _parse2dArray(self, frame: str) -> list:
        """Parse SVG path data into 2D array."""
        path_data = extractBetween(frame, '</path><path d="', '"')
        return [
            [int(x) for x in re.split(r"[^\d]+", path) if x]
            for path in path_data[9:].split("C")
        ]

    def _animate(self, frames: list, target: float) -> str:
        """Animate frame data to produce key component."""
        # Build bezier curve from frame data
        curve = []
        for i, val in enumerate(frames[7:]):
            odd = -1.0 if i % 2 else 0.0
            curve.append(scale(float(val), odd, 1.0, False))
        cubic = cubicValue(curve, target)

        # Interpolate colors
        color_a = (float(frames[0]), float(frames[1]), float(frames[2]))
        color_b = (float(frames[3]), float(frames[4]), float(frames[5]))
        color = [
            max(0.0, min(255.0, interpolate(cubic, color_a[i], color_b[i])))
            for i in range(3)
        ]

        # Calculate rotation
        rotation_b = scale(float(frames[6]), 60.0, 360.0, True)
        rotation = interpolate(cubic, 0.0, rotation_b)
        matrix = rotationMatrix(rotation)

        # Build result
        parts = (
            hex(round(color[0]))[2:],
            hex(round(color[1]))[2:],
            hex(round(color[2]))[2:],
            floatToHex(abs(round(matrix[0], 2))),
            floatToHex(abs(round(matrix[1], 2))),
            floatToHex(abs(round(matrix[2], 2))),
            floatToHex(abs(round(matrix[3], 2))),
            "00",
        )
        return "".join(parts).replace(".", "").replace("-", "")

    def generate(self, method: str, path: str) -> str:
        """
        Generate transaction ID for a request.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path (e.g., /i/api/graphql/...)

        Returns:
            Base64-encoded transaction ID string
        """
        if self.key_bytes is None:
            raise RuntimeError("TransactionId not initialized")

        nowf = time.time()
        nowi = int(nowf)
        now = nowi - 1682924400  # Twitter epoch

        time_bytes = (
            now & 0xFF,
            (now >> 8) & 0xFF,
            (now >> 16) & 0xFF,
            (now >> 24) & 0xFF,
        )

        payload = f"{method}!{path}!{now}obfiowerehiring{self.animation_key}"
        hash_bytes = hashlib.sha256(payload.encode()).digest()[:16]

        num = (random.randrange(16) << 4) + int((nowf - nowi) * 16.0)
        result = bytes(
            byte ^ num
            for byte in itertools.chain(
                (0,), self.key_bytes, time_bytes, hash_bytes, (3,)
            )
        )
        return binascii.b2a_base64(result).rstrip(b"=\n").decode("ascii")
