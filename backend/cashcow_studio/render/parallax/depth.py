"""Depth maps for 2.5D parallax: Depth Anything V2 Small through onnxruntime on the CPU.

docs/TRANSITIONS-V2-CONTRACT.md section 2.

* **The model** is optional. It is downloaded once, on request (Settings > Render > Depth
  model > Download, which calls :func:`start_depth_model_download`), to
  ``<app_data_dir>/models/``; the file's SHA-256 must match ``config/render.yaml``
  (``parallax.depth_model``) or it is thrown away. Default: the ONNX export of
  Depth Anything V2 Small (Apache-2.0) published by onnx-community on Hugging Face, pinned
  to one revision. :func:`depth_model_status` is what the settings screen shows.
* **onnxruntime** is an optional extra (``pip install -e .[parallax]``). Some onnxruntime
  builds crash the whole process on import when the PC's Microsoft Visual C++ runtime is
  older than the one they were built with, so the first use imports it in a short child
  process (:func:`runtime_check`) and only imports it here when that worked.
* **Depth maps** are saved next to each picture as ``scene_NN.depth.png``: 16-bit greyscale
  at the picture's size, white = near (the model predicts relative inverse depth). A PNG text
  chunk ``ccs:depth`` records the picture's fingerprint and the model, so a replaced picture
  gets a new map; a depth PNG without that chunk was put there by a person and is used as is.

Without the model (or without onnxruntime) nothing fails: :func:`ensure_depth_maps` reports
a plain-English ``fallback_reason`` and the scene is drawn as an eased Ken Burns move.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, PngImagePlugin, UnidentifiedImageError

from ..ffmpeg import NO_WINDOW

log = logging.getLogger(__name__)

MODELS_DIR = "models"
DEPTH_SUFFIX = ".depth.png"
META_KEY = "ccs:depth"
VERIFIED_SUFFIX = ".verified.json"
PROBE_TIMEOUT_S = 120
USER_AGENT = "CashCowStudio (depth model download)"

ProgressFn = Callable[[int, int], None]
"""``progress(done, total)``: bytes for a download, pictures for :func:`ensure_depth_maps`."""


class DepthModelError(Exception):
    """The depth model could not be downloaded, checked or run (plain-English message)."""


# The model ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DepthModelSpec:
    """``parallax.depth_model`` in ``config/render.yaml``."""

    id: str = "depth-anything-v2-small"
    label: str = "Depth Anything V2 Small"
    file: str = "depth_anything_v2_small.onnx"
    url: str = (
        "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/"
        "4472b7362082ad9968fee890ca0f1e5aca36b93d/onnx/model.onnx"
    )
    sha256: str = "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c"
    size_bytes: int = 99_060_839
    license: str = "Apache-2.0"
    source: str = "https://huggingface.co/onnx-community/depth-anything-v2-small"
    input_size: int = 518
    """The short side the picture is resized to for the model (a multiple of 14)."""
    threads: int = 0
    """CPU threads for the model; 0 = half the logical cores, at most 4."""

    @property
    def tag(self) -> str:
        """What a depth map records about the model that made it."""
        return f"{self.id}:{(self.sha256 or 'custom')[:12]}"


def depth_model_spec(data: dict[str, Any] | None = None) -> DepthModelSpec:
    """The spec from ``config/render.yaml`` (or from ``data``, the parsed file)."""
    if data is None:
        from ...llm.config import load_yaml

        data = load_yaml("render.yaml")
    parallax = data.get("parallax") if isinstance(data, dict) else None
    raw = parallax.get("depth_model") if isinstance(parallax, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    base = DepthModelSpec()

    def text(key: str, fallback: str) -> str:
        value = raw.get(key)
        return str(value).strip() if value is not None else fallback

    def number(key: str, fallback: int) -> int:
        try:
            return int(raw.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    file_name = Path(text("file", base.file)).name or base.file
    return DepthModelSpec(
        id=text("id", base.id) or base.id,
        label=text("label", base.label) or base.label,
        file=file_name,
        url=text("url", base.url),
        sha256=text("sha256", base.sha256).lower(),
        size_bytes=max(number("size_bytes", base.size_bytes), 0),
        license=text("license", base.license),
        source=text("source", base.source),
        input_size=min(max(number("input_size", base.input_size), 14), 2048),
        threads=min(max(number("threads", base.threads), 0), 64),
    )


def app_data_dir_of(target: Any) -> Path:
    """``Settings`` (anything with ``app_data_dir``) or a folder -> the app data folder."""
    folder = getattr(target, "app_data_dir", target)
    if folder is None:
        raise ValueError("an app data folder is needed")
    return Path(folder)


def model_path(target: Any, spec: DepthModelSpec | None = None) -> Path:
    return app_data_dir_of(target) / MODELS_DIR / (spec or depth_model_spec()).file


def file_sha256(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            if cancel is not None and cancel.is_set():
                raise DepthModelError("Checking the depth model was stopped.")
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verified_marker(path: Path) -> Path:
    return path.with_name(path.name + VERIFIED_SUFFIX)


def verify_model_file(path: Path, spec: DepthModelSpec, *, use_marker: bool = True) -> bool:
    """True when ``path`` is the model the spec names (SHA-256; size first). The result is
    remembered in ``<file>.verified.json`` so the 100 MB file is hashed only once."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return False
    if spec.size_bytes and stat.st_size != spec.size_bytes and spec.sha256:
        return False
    if not spec.sha256:
        return stat.st_size > 0
    marker = _verified_marker(path)
    if use_marker:
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if (
                data.get("sha256") == spec.sha256
                and data.get("size") == stat.st_size
                and data.get("mtime_ns") == stat.st_mtime_ns
            ):
                return True
        except (OSError, ValueError, AttributeError):
            pass
    try:
        ok = file_sha256(path) == spec.sha256
    except OSError:
        return False
    if ok:
        try:
            marker.write_text(
                json.dumps({"sha256": spec.sha256, "size": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns}),
                encoding="utf-8",
            )
        except OSError:
            pass
    return ok


# onnxruntime ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeCheck:
    ok: bool
    version: str | None = None
    message: str = ""


NOT_INSTALLED = (
    "The 2.5D parallax add-on (onnxruntime) is not installed on this computer, so scenes use "
    "the Ken Burns move. Install it with: pip install -e .[parallax]"
)
CRASHED = (
    "The 2.5D parallax add-on (onnxruntime) could not start on this computer, so scenes use "
    "the Ken Burns move. Installing the latest Microsoft Visual C++ Redistributable (x64) "
    "usually fixes this."
)


def _probe_import() -> RuntimeCheck:
    command = [
        sys.executable,
        "-c",
        "import sys, onnxruntime; sys.stdout.write(onnxruntime.__version__)",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            creationflags=NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("onnxruntime probe could not run: %s", exc)
        return RuntimeCheck(False, None, CRASHED)
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        log.warning(
            "onnxruntime failed to import in a child process (exit code %s): %s",
            completed.returncode, detail[-1] if detail else "no message",
        )
        return RuntimeCheck(False, None, CRASHED)
    return RuntimeCheck(True, (completed.stdout or "").strip() or None, "")


@lru_cache(maxsize=1)
def _cached_runtime_check() -> RuntimeCheck:
    if "onnxruntime" in sys.modules:
        module = sys.modules["onnxruntime"]
        return RuntimeCheck(True, getattr(module, "__version__", None), "")
    try:
        found = importlib.util.find_spec("onnxruntime") is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        return RuntimeCheck(False, None, NOT_INSTALLED)
    if getattr(sys, "frozen", False):
        # A packaged app cannot start a child Python; the installer ships a matching runtime.
        return RuntimeCheck(True, None, "")
    return _probe_import()


def runtime_check(refresh: bool = False) -> RuntimeCheck:
    """Whether onnxruntime can be used in this process (checked once, in a child process)."""
    if refresh:
        _cached_runtime_check.cache_clear()
    return _cached_runtime_check()


# Status and download (the settings endpoint calls these) -----------------------------------

DepthModelState = Literal[
    "ready", "not_downloaded", "downloading", "failed", "invalid", "unavailable"
]


@dataclass
class DepthModelStatus:
    """What Settings > Render shows for the depth model."""

    state: DepthModelState
    ready: bool
    downloaded: bool
    can_download: bool
    message: str
    path: str
    size_bytes: int | None = None
    expected_size_bytes: int = 0
    downloaded_bytes: int | None = None
    progress: float | None = None
    runtime_ok: bool = False
    runtime_version: str | None = None
    model: str = ""
    license: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Download:
    thread: threading.Thread | None = None
    done_bytes: int = 0
    total_bytes: int = 0
    error: str | None = None
    finished: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)


_downloads: dict[str, _Download] = {}
_downloads_lock = threading.Lock()


def _download_key(path: Path) -> str:
    # abspath, not resolve(): on Windows resolve() can return a "\\?\" path while the
    # download thread is creating the folder, and the job would no longer be found.
    return os.path.normcase(os.path.abspath(str(path)))


def depth_model_status(
    target: Any, *, spec: DepthModelSpec | None = None, check_runtime: bool = True
) -> DepthModelStatus:
    """The depth model's state for ``target`` (``Settings`` or the app data folder).

    ``state`` is ``ready`` (file verified and onnxruntime works), ``downloading`` (with
    ``progress``), ``not_downloaded``, ``failed`` (the last download did not finish; see
    ``message``), ``invalid`` (a file is there but is not the expected model) or
    ``unavailable`` (onnxruntime is missing or cannot start)."""
    spec = spec or depth_model_spec()
    path = model_path(target, spec)
    runtime = runtime_check() if check_runtime else RuntimeCheck(True)
    with _downloads_lock:
        job = _downloads.get(_download_key(path))
    size: int | None
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    common: dict[str, Any] = {
        "path": str(path),
        "size_bytes": size,
        "expected_size_bytes": spec.size_bytes,
        "runtime_ok": runtime.ok,
        "runtime_version": runtime.version,
        "model": spec.label,
        "license": spec.license,
        "source": spec.source,
    }
    if job is not None and not job.finished:
        total = job.total_bytes or spec.size_bytes
        pct = round(100.0 * job.done_bytes / total, 1) if total else None
        return DepthModelStatus(
            state="downloading", ready=False, downloaded=False, can_download=False,
            message=f"Downloading the depth model ({pct or 0:g}%).",
            downloaded_bytes=job.done_bytes, progress=pct, **common,
        )
    downloaded = size is not None and verify_model_file(path, spec)
    if downloaded and not runtime.ok:
        return DepthModelStatus(
            state="unavailable", ready=False, downloaded=True, can_download=False,
            message=runtime.message, **common,
        )
    if downloaded:
        return DepthModelStatus(
            state="ready", ready=True, downloaded=True, can_download=False,
            message=f"{spec.label} is ready. Scenes set to 2.5D parallax get real depth.",
            **common,
        )
    if not runtime.ok:
        return DepthModelStatus(
            state="unavailable", ready=False, downloaded=False, can_download=False,
            message=runtime.message, **common,
        )
    if size is not None:
        return DepthModelStatus(
            state="invalid", ready=False, downloaded=False, can_download=True,
            message=(
                f"The file {path.name} in the models folder is not the expected depth model. "
                "Download it again."
            ),
            **common,
        )
    if job is not None and job.error:
        return DepthModelStatus(
            state="failed", ready=False, downloaded=False, can_download=True,
            message=job.error, **common,
        )
    megabytes = spec.size_bytes / 1_000_000 if spec.size_bytes else 0
    return DepthModelStatus(
        state="not_downloaded", ready=False, downloaded=False, can_download=True,
        message=(
            f"Not downloaded. 2.5D parallax needs {spec.label} "
            f"({megabytes:.0f} MB, one-time download); until then those scenes use the "
            "Ken Burns move."
        ),
        **common,
    )


def depth_model_ready(target: Any, spec: DepthModelSpec | None = None) -> bool:
    return depth_model_status(target, spec=spec).ready


def download_depth_model(
    target: Any,
    *,
    spec: DepthModelSpec | None = None,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    client: Any = None,
    timeout_s: float = 60.0,
) -> Path:
    """Download the model to ``<app_data_dir>/models/`` and check its SHA-256 (blocking).

    ``client`` is an ``httpx.Client`` (tests pass one with a mock transport). Raises
    :class:`DepthModelError` with a plain-English message; a partial file never stays."""
    import httpx

    spec = spec or depth_model_spec()
    if not spec.url:
        raise DepthModelError("No download address is set for the depth model in render.yaml.")
    path = model_path(target, spec)
    if verify_model_file(path, spec):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    own_client = client is None
    http = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_s, connect=20.0),
        headers={"User-Agent": USER_AGENT},
    )
    digest = hashlib.sha256()
    done = 0
    try:
        with http.stream("GET", spec.url) as response:
            if response.status_code != 200:
                raise DepthModelError(
                    f"The depth model could not be downloaded (the server answered "
                    f"{response.status_code})."
                )
            total = int(response.headers.get("content-length") or spec.size_bytes or 0)
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    if cancel is not None and cancel.is_set():
                        raise DepthModelError("The depth model download was stopped.")
                    handle.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
        if spec.size_bytes and done != spec.size_bytes:
            raise DepthModelError(
                f"The depth model download was incomplete ({done} of {spec.size_bytes} bytes). "
                "Try again."
            )
        actual = digest.hexdigest()
        if spec.sha256 and actual != spec.sha256:
            raise DepthModelError(
                "The downloaded depth model did not match its expected fingerprint and was "
                "deleted. Try again later."
            )
        os.replace(partial, path)
        stat = path.stat()
        _verified_marker(path).write_text(
            json.dumps({"sha256": spec.sha256 or actual, "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns}),
            encoding="utf-8",
        )
        log.info("Depth model saved to %s (%d bytes)", path, done)
        return path
    except httpx.HTTPError as exc:
        raise DepthModelError(
            f"The depth model could not be downloaded: {type(exc).__name__}. Check the internet "
            "connection and try again."
        ) from exc
    except OSError as exc:
        raise DepthModelError(
            f"The depth model could not be saved to {path.parent}: {exc}"
        ) from exc
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        if own_client:
            http.close()


def start_depth_model_download(
    target: Any,
    *,
    spec: DepthModelSpec | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> DepthModelStatus:
    """Start the download in a background thread (or say it is ready / already running) and
    return the status at once. ``POST /api/settings/models/depth`` calls this."""
    spec = spec or depth_model_spec()
    path = model_path(target, spec)
    status = depth_model_status(target, spec=spec)
    if status.state in ("ready", "downloading", "unavailable") or (
        status.downloaded and status.state != "invalid"
    ):
        return status
    key = _download_key(path)
    job = _Download(total_bytes=spec.size_bytes)

    def run() -> None:
        def on_progress(done: int, total: int) -> None:
            job.done_bytes, job.total_bytes = done, total or job.total_bytes

        client = client_factory() if client_factory is not None else None
        try:
            if status.state == "invalid":
                try:
                    path.unlink()
                except OSError:
                    pass
            download_depth_model(target, spec=spec, progress=on_progress, cancel=job.cancel,
                                 client=client)
        except DepthModelError as exc:
            job.error = str(exc)
            log.warning("Depth model download failed: %s", exc)
        except Exception as exc:  # noqa: BLE001 - a background thread must not die silently
            job.error = f"The depth model could not be downloaded: {exc}"
            log.exception("Depth model download failed")
        finally:
            if client is not None and hasattr(client, "close"):
                client.close()
            job.finished = True

    with _downloads_lock:
        running = _downloads.get(key)
        if running is None or running.finished:
            _downloads[key] = job
            job.thread = threading.Thread(target=run, name="depth-model-download", daemon=True)
            job.thread.start()
    return depth_model_status(target, spec=spec)


def wait_for_depth_model_download(target: Any, timeout_s: float = 600.0,
                                  spec: DepthModelSpec | None = None) -> DepthModelStatus:
    """Block until a running download ends (for the command line and tests)."""
    path = model_path(target, spec or depth_model_spec())
    with _downloads_lock:
        job = _downloads.get(_download_key(path))
    if job is not None and job.thread is not None:
        job.thread.join(timeout_s)
    return depth_model_status(target, spec=spec)


def cancel_depth_model_download(target: Any, spec: DepthModelSpec | None = None) -> None:
    path = model_path(target, spec or depth_model_spec())
    with _downloads_lock:
        job = _downloads.get(_download_key(path))
    if job is not None:
        job.cancel.set()


# Inference -------------------------------------------------------------------------------------


def model_input_size(
    height: int, width: int, size: int = 518, multiple: int = 14
) -> tuple[int, int]:
    """``(h, w)`` the picture is resized to for the model: the DPT rule of Depth Anything
    (keep the aspect ratio, scale as little as possible towards ``size`` x ``size``, round
    each side to a multiple of 14)."""
    scale_h, scale_w = size / max(height, 1), size / max(width, 1)
    if abs(1 - scale_w) < abs(1 - scale_h):
        scale_h = scale_w
    else:
        scale_w = scale_h

    def fit(value: float) -> int:
        result = round(value / multiple) * multiple
        return max(int(result), multiple)

    return fit(scale_h * height), fit(scale_w * width)


_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def normalize_depth(raw: np.ndarray) -> np.ndarray:
    """Relative inverse depth -> 0..1 (1 = nearest), ignoring the extreme 1 % at each end."""
    data = np.asarray(raw, dtype=np.float32)
    lo, hi = np.percentile(data, [1.0, 99.0])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-6:
        return np.full(data.shape, 0.5, dtype=np.float32)
    return np.clip((data - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)


class DepthEstimator:
    """One onnxruntime session of the depth model (CPU). Close it (or use ``with``) when the
    batch is done: the session holds a few hundred MB."""

    def __init__(self, model: Path | str, *, threads: int = 0, input_size: int = 518,
                 session: Any = None) -> None:
        import cv2  # noqa: F401 - fail early if OpenCV is missing

        self.input_size = int(input_size)
        if session is None:
            check = runtime_check()
            if not check.ok:
                raise DepthModelError(check.message)
            import onnxruntime as ort

            options = ort.SessionOptions()
            cores = os.cpu_count() or 2
            options.intra_op_num_threads = threads or max(1, min(4, cores // 2))
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            try:
                session = ort.InferenceSession(
                    str(model), sess_options=options, providers=["CPUExecutionProvider"]
                )
            except Exception as exc:  # noqa: BLE001 - onnxruntime raises its own types
                raise DepthModelError(
                    f"The depth model could not be loaded ({type(exc).__name__}). Download it "
                    "again in Settings > Render."
                ) from exc
        self.session = session
        first = session.get_inputs()[0]
        self.input_name = first.name
        shape = list(getattr(first, "shape", []) or [])
        fixed = shape[2:4] if len(shape) >= 4 else []
        self.fixed_size: tuple[int, int] | None = (
            (int(fixed[0]), int(fixed[1]))
            if len(fixed) == 2 and all(isinstance(v, int) and v > 0 for v in fixed)
            else None
        )
        self.output_name = session.get_outputs()[0].name

    def __enter__(self) -> DepthEstimator:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self.session = None

    def predict(self, rgb: np.ndarray) -> np.ndarray:
        """Depth for an RGB ``uint8`` picture: ``float32`` 0..1 at the picture's size, 1 = near."""
        import cv2

        if self.session is None:
            raise DepthModelError("The depth model was closed.")
        height, width = rgb.shape[:2]
        in_h, in_w = self.fixed_size or model_input_size(height, width, self.input_size)
        resized = cv2.resize(rgb, (in_w, in_h), interpolation=cv2.INTER_CUBIC)
        tensor = (resized.astype(np.float32) / 255.0 - _MEAN) / _STD
        tensor = np.ascontiguousarray(tensor.transpose(2, 0, 1)[None])
        output = self.session.run([self.output_name], {self.input_name: tensor})[0]
        raw = np.squeeze(np.asarray(output, dtype=np.float32))
        if raw.ndim != 2:
            raise DepthModelError("The depth model gave an answer of an unexpected shape.")
        raw = cv2.resize(raw, (width, height), interpolation=cv2.INTER_CUBIC)
        return normalize_depth(raw)


# Depth map files -----------------------------------------------------------------------------


def depth_map_path(image: Path | str) -> Path:
    """``06_images/scene_03.png`` -> ``06_images/scene_03.depth.png``."""
    image = Path(image)
    return image.with_name(image.stem + DEPTH_SUFFIX)


def image_fingerprint(image: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(image).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:24]


def load_rgb(path: Path | str) -> np.ndarray:
    """A picture as ``uint8`` RGB (no EXIF rotation: the timeline's sizes are the raw ones)."""
    try:
        with Image.open(path) as image:
            image.load()
            if image.mode not in ("RGB",):
                image = image.convert("RGB")
            return np.asarray(image, dtype=np.uint8).copy()
    except (OSError, UnidentifiedImageError) as exc:
        raise DepthModelError(f"The picture {Path(path).name} could not be read: {exc}") from exc


def write_depth_map(path: Path | str, depth: np.ndarray, meta: dict[str, Any]) -> Path:
    """Save 0..1 depth as a 16-bit PNG with the ``ccs:depth`` text chunk (atomic)."""
    path = Path(path)
    data = np.clip(np.asarray(depth, dtype=np.float32), 0.0, 1.0)
    pixels = np.round(data * 65535.0).astype(np.uint16)
    info = PngImagePlugin.PngInfo()
    info.add_text(META_KEY, json.dumps(meta, sort_keys=True))
    partial = path.with_name(path.name + ".part.png")
    Image.fromarray(pixels).save(partial, format="PNG", pnginfo=info)
    os.replace(partial, path)
    return path


def read_depth_map(path: Path | str) -> tuple[np.ndarray, dict[str, Any] | None] | None:
    """``(depth 0..1 float32, meta or None)``; ``None`` when there is no readable map. 8-bit
    and 16-bit greyscale PNGs both work (so a person may paint one)."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            image.load()
            raw_meta = image.info.get(META_KEY) if isinstance(image.info, dict) else None
            if image.mode in ("I;16", "I;16B", "I;16L", "I"):
                pixels = np.asarray(image, dtype=np.float32)
                depth = pixels / 65535.0
            else:
                grey = image.convert("L")
                depth = np.asarray(grey, dtype=np.float32) / 255.0
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        log.warning("The depth map %s could not be read: %s", path, exc)
        return None
    meta: dict[str, Any] | None = None
    if raw_meta:
        try:
            parsed = json.loads(raw_meta)
            meta = parsed if isinstance(parsed, dict) else None
        except ValueError:
            meta = None
    return np.clip(depth, 0.0, 1.0).astype(np.float32), meta


def _cached_map_ok(image: Path, depth_path: Path, fingerprint: str) -> bool:
    found = read_depth_map(depth_path)
    if found is None:
        return False
    depth, meta = found
    if meta is None:
        log.info("Using the depth map %s as it is (it was not made by this app)", depth_path)
        return True
    return meta.get("source") == fingerprint and depth.ndim == 2


@dataclass
class DepthResult:
    image: Path
    path: Path | None
    reused: bool = False
    made: bool = False
    seconds: float = 0.0
    error: str | None = None


@dataclass
class DepthBatch:
    results: list[DepthResult]
    fallback_reason: str | None = None
    """Plain English, set when some pictures have no depth map (they use Ken Burns)."""

    @property
    def maps(self) -> dict[Path, Path]:
        return {r.image: r.path for r in self.results if r.path is not None}

    @property
    def made(self) -> int:
        return sum(1 for r in self.results if r.made)

    @property
    def reused(self) -> int:
        return sum(1 for r in self.results if r.reused)


def ensure_depth_maps(
    images: Sequence[Path | str],
    target: Any,
    *,
    spec: DepthModelSpec | None = None,
    cancel: threading.Event | None = None,
    progress: ProgressFn | None = None,
    estimator: DepthEstimator | None = None,
    force: bool = False,
) -> DepthBatch:
    """A depth map next to every picture, reusing the ones that are still right.

    Pictures whose map is missing get one from the model when it is downloaded and
    onnxruntime works; otherwise they keep no map and ``fallback_reason`` says why (those
    scenes are drawn as Ken Burns moves). ``target`` is ``Settings`` or the app data folder."""
    spec = spec or depth_model_spec()
    unique: list[Path] = []
    for item in images:
        path = Path(item)
        if path not in unique:
            unique.append(path)
    results: dict[Path, DepthResult] = {}
    todo: list[tuple[Path, str]] = []
    for image in unique:
        depth_path = depth_map_path(image)
        try:
            fingerprint = image_fingerprint(image)
        except OSError as exc:
            results[image] = DepthResult(
                image, None, error=f"{image.name} could not be read: {exc}"
            )
            continue
        if not force and _cached_map_ok(image, depth_path, fingerprint):
            results[image] = DepthResult(image, depth_path, reused=True)
        else:
            todo.append((image, fingerprint))
    reason: str | None = None
    if todo:
        own = estimator is None
        if own:
            status = depth_model_status(target, spec=spec)
            if not status.ready:
                reason = status.message
        if reason is None:
            try:
                active = estimator or DepthEstimator(
                    model_path(target, spec), threads=spec.threads, input_size=spec.input_size
                )
            except DepthModelError as exc:
                reason = str(exc)
                active = None
            if active is not None:
                try:
                    for number, (image, fingerprint) in enumerate(todo, start=1):
                        if cancel is not None and cancel.is_set():
                            reason = "Making depth maps was stopped."
                            break
                        started = time.monotonic()
                        try:
                            depth = active.predict(load_rgb(image))
                            out = write_depth_map(
                                depth_map_path(image), depth,
                                {"source": fingerprint, "model": spec.tag,
                                 "width": int(depth.shape[1]), "height": int(depth.shape[0])},
                            )
                            results[image] = DepthResult(
                                image, out, made=True,
                                seconds=round(time.monotonic() - started, 3),
                            )
                        except (DepthModelError, OSError, ValueError) as exc:
                            results[image] = DepthResult(image, None, error=str(exc))
                            reason = reason or (
                                f"The depth of {image.name} could not be worked out ({exc})."
                            )
                        if progress is not None:
                            progress(number, len(todo))
                finally:
                    if own:
                        active.close()
        for image, _fingerprint in todo:
            results.setdefault(image, DepthResult(image, None, error=reason))
    return DepthBatch([results[image] for image in unique if image in results], reason)


def depth_for_image(image: Path | str) -> np.ndarray | None:
    """The saved depth map of a picture (0..1, the picture's size), or ``None``."""
    found = read_depth_map(depth_map_path(image))
    return found[0] if found is not None else None
