"""The images a job pulls, as Compose's JSON progress tells it layer by layer: how far each image is, and how fast the
downloads go. Compose writes one JSON object per line with `--progress json`; an image is `{"id": "Image redis:8",
"text": "Pulling"}` and each layer of it `{"id": "3f1c0a7d9e2b", "parent_id": "Image redis:8", "text": "Downloading",
"current": 1048576, "total": 1836303}`."""

import time
from collections import deque

# Over how many seconds the speed is measured: long enough not to jump, short enough to follow a download that slows
WINDOW = 4
# What a layer is doing, from its latest line
WAITING, DOWNLOADING, DOWNLOADED, EXTRACTING, DONE = "waiting", "downloading", "downloaded", "extracting", "done"
LAYERS = {"Pulling fs layer": WAITING, "Waiting": WAITING, "Downloading": DOWNLOADING, "Download complete": DOWNLOADED,
          "Verifying Checksum": DOWNLOADED, "Extracting": EXTRACTING, "Pull complete": DONE, "Already exists": DONE}
# What a person reads of an image: still downloading, downloaded and being unpacked, pulled, refused
PULLING, UNPACKING, PULLED, FAILED = "downloading", "extracting", "pulled", "failed"


class Pulls:
    """The images of a job, in the order Compose named them, with their layers. Not locked: the job's lock holds it."""

    def __init__(self, clock=time.monotonic):
        self._images, self._samples, self._clock = {}, deque(), clock

    def event(self, event, quiet=False):
        """Takes one line of Compose's progress. Answers the line a plain progress would have printed for it
        ("Image redis:8 Pulled"), None when it says nothing the output shows (a layer, an image already there).
        `quiet`: a pull that may fail without the job failing, since the command it comes before says why: an image
        refused is not said."""
        name, text, parent = event.get("id") or "", event.get("text") or "", event.get("parent_id") or ""
        if parent.startswith("Image "):
            self._layer(parent[6:], name, text, event)
            return None
        if not name.startswith("Image "):
            return None
        image = name[6:]
        if text == "Skipped":   # it is there already
            self._images.pop(image, None)
            return None
        if text == "Pulling":
            self._images.setdefault(image, {"layers": {}, "state": PULLING})
        elif text == "Pulled":
            self._images.setdefault(image, {"layers": {}})["state"] = PULLED
        elif text == "Error":
            self._images.setdefault(image, {"layers": {}})["state"] = FAILED
            if quiet:
                return None
            return f"{name} Error {' '.join((event.get('details') or '').split())}".rstrip()
        else:
            return None
        return f"{name} {text}"

    def _layer(self, image, name, text, event):
        found = self._images.setdefault(image, {"layers": {}, "state": PULLING})
        layer = found["layers"].setdefault(name, {"phase": WAITING, "current": 0, "total": 0})
        phase = LAYERS.get(text)
        if phase is None:
            return
        layer["phase"] = phase
        if phase == DOWNLOADING:
            layer["total"] = event.get("total") or layer["total"]
            layer["current"] = min(event.get("current") or 0, layer["total"]) if layer["total"] else 0
            self._sample()
        elif phase != WAITING:   # downloaded: all of it
            layer["current"] = layer["total"]

    def _sample(self):
        """The bytes downloaded so far, now: the speed is what they gained over the last seconds."""
        now = self._clock()
        self._samples.append((now, self._bytes()))
        while self._samples and self._samples[0][0] < now - WINDOW:
            self._samples.popleft()

    def _bytes(self):
        return sum(layer["current"] for image in self._images.values() for layer in image["layers"].values())

    def _state(self, image):
        state = image["state"]
        if state in (PULLED, FAILED):
            return state
        phases = {layer["phase"] for layer in image["layers"].values()}
        return UNPACKING if phases and not phases & {WAITING, DOWNLOADING} else PULLING

    def speed(self):
        """Bytes per second, over the last seconds; None when nothing was downloaded in them."""
        now = self._clock()
        samples = [sample for sample in self._samples if sample[0] >= now - WINDOW]
        if len(samples) < 2 or samples[-1][1] <= samples[0][1]:
            return None
        return round((samples[-1][1] - samples[0][1]) / max(samples[-1][0] - samples[0][0], 0.001))

    def fraction(self, image):
        """How far an image is by its bytes, None while its size is not known."""
        found = self._images.get(image)
        total = sum(layer["total"] for layer in found["layers"].values()) if found else 0
        return sum(layer["current"] for layer in found["layers"].values()) / total if total else None

    def view(self):
        """What the page draws: {"images": [{image, state, current, total}], "speed"}; None before any image."""
        if not self._images:
            return None
        images = []
        for image, found in self._images.items():
            layers = found["layers"].values()
            images.append({"image": image, "state": self._state(found),
                           "current": sum(layer["current"] for layer in layers),
                           "total": sum(layer["total"] for layer in layers)})
        return {"images": images, "speed": self.speed()}
