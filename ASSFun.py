from __future__ import annotations

import io
import json
import mimetypes
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

import customtkinter as ctk
import requests
import tkinter
import tkinter.font as tkfont
from fontTools import subset
from fontTools.ttLib import TTCollection, TTFont
from PIL import Image, ImageDraw, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD
from pymediainfo import MediaInfo


@dataclass(frozen=True, slots=True)
class AppConstants:
    name: str = "ASSFun"
    title: str = "ASSFun - KyokuSai"
    font_file: str = "NotoSansSC-Medium.otf"
    icon_file: str = "favicon.ico"
    gear_file: str = "gear.png"
    dark_titlebar: bool = True


@dataclass(frozen=True, slots=True)
class WindowMetrics:
    main: str = "780x740"
    settings: str = "860x680"
    select: str = "720x580"
    cover: str = "500x190"


@dataclass(frozen=True, slots=True)
class LayoutMetrics:
    anim_ms: int = 14
    anim_steps: int = 10
    setting_key_width: int = 124
    setting_side_control_width: int = 60
    setting_control_height: int = 30
    setting_switch_slot_width: int = 58


APP = AppConstants()
WINDOW = WindowMetrics()
LAYOUT = LayoutMetrics()

THEME: dict[str, str] = {
    "bg": "#111217",
    "panel": "#1B1D25",
    "panel_2": "#242733",
    "panel_3": "#272B35",
    "border": "#303442",
    "border_soft": "#2A2E38",
    "text": "#F2F3F5",
    "muted": "#A6AAB8",
    "muted_2": "#7F8796",
    "accent": "#FA4276",
    "accent_hover": "#FF6E96",
    "accent_soft": "#472433",
    "danger": "#D84646",
    "success": "#4EBC73",
    "disabled": "#474B57",
    "input": "#15171D",
    "log_bg": "#0F1117",
}

APP_NAME = APP.name
APP_TITLE = APP.title
FONT_FILE = APP.font_file
ICON_FILE = APP.icon_file
GEAR_FILE = APP.gear_file
MAIN_WINDOW_SIZE = WINDOW.main
SETTINGS_WINDOW_SIZE = WINDOW.settings
SELECT_WINDOW_SIZE = WINDOW.select
COVER_WINDOW_SIZE = WINDOW.cover
ANIM_MS = LAYOUT.anim_ms
ANIM_STEPS = LAYOUT.anim_steps
SETTING_KEY_WIDTH = LAYOUT.setting_key_width
SETTING_SIDE_CONTROL_WIDTH = LAYOUT.setting_side_control_width
SETTING_CONTROL_HEIGHT = LAYOUT.setting_control_height
SETTING_SWITCH_SLOT_WIDTH = LAYOUT.setting_switch_slot_width

BG = THEME["bg"]
PANEL = THEME["panel"]
PANEL_2 = THEME["panel_2"]
TEXT = THEME["text"]
TEXT_MUTED = THEME["muted"]
ACCENT = THEME["accent"]
ACCENT_HOVER = THEME["accent_hover"]
BORDER = THEME["border"]
DANGER = THEME["danger"]
SUCCESS = THEME["success"]

APP_FONT_FILE = FONT_FILE
APP_FONT_FAMILY = ""
APP_DARK_TITLEBAR = APP.dark_titlebar
APP_FONT_WEIGHT_WORDS = {
    "thin",
    "extralight",
    "extra light",
    "light",
    "regular",
    "normal",
    "medium",
    "semibold",
    "semi bold",
    "bold",
    "extrabold",
    "extra bold",
    "black",
    "heavy",
}

MediaKind = Literal["av", "video", "audio"]


class FatalProcessError(RuntimeError):
    pass


class DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(relative_path: str | Path) -> str:
    base = Path(getattr(sys, "_MEIPASS", app_dir()))
    return str(base / relative_path)


def parse_size(value: str) -> tuple[int, int]:
    width, height = str(value).lower().split("x", 1)
    return int(width), int(height)


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def choose_output_dir(
    configured: str, input_file: Path, root: Path, log: Callable[[str], None]
) -> Path:
    candidates: list[Path] = []
    if configured.strip():
        candidates.append(Path(configured).expanduser())
    candidates.extend([input_file.parent, root])
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            ensure_dir(path)
            probe = path / ".assfun_write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            if path != candidates[0].resolve() and configured.strip():
                log(f"输出路径不可用，已回退到：{path}")
            return path
        except OSError as exc:
            log(f"输出路径不可用：{path}（{exc}）")
    raise OSError("没有可写的输出路径。")


def sanitize_filename(name: str, replacement: str = "_") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', replacement, str(name))
    name = name.strip().strip(".")
    return name or "untitled"


def split_csv(value: str | None) -> list[str]:
    if value is None:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def parse_drop_paths(data: str) -> list[Path]:
    matches = re.findall(r"\{(.*?)\}|([^\s]+)", data)
    return [Path(a or b) for a, b in matches if (a or b)]


def atomic_write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name, suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8-sig", errors="replace")


def write_text(path: str | Path, content: str) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8-sig", errors="replace")


def normalize_exception(exc: BaseException) -> str:
    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ).rstrip()


def safe_after_cancel_all(window) -> None:
    try:
        for job in window.tk.call("after", "info"):
            try:
                window.after_cancel(job)
            except Exception:
                pass
    except Exception:
        pass


def creationflags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def pick_default_track(tracks: Sequence[Any], track_type: str) -> Any | None:
    candidates = [t for t in tracks if getattr(t, "track_type", None) == track_type]
    if not candidates:
        return None
    for track in candidates:
        if str(getattr(track, "default", "")).lower() == "yes":
            return track
    return candidates[0]


def get_media_info(
    path: str | Path, mkvmerge_path: str | None = None
) -> dict[str, str | int | None]:
    p = Path(path)
    if MediaInfo is not None:
        try:
            info = MediaInfo.parse(str(p))
            video = pick_default_track(info.tracks, "Video")
            audio = pick_default_track(info.tracks, "Audio")
            return {
                "width": getattr(video, "width", None) if video else None,
                "height": getattr(video, "height", None) if video else None,
                "vcodec": getattr(video, "format", None) if video else None,
                "acodec": getattr(audio, "format", None) if audio else None,
                "bitdepth": getattr(video, "bit_depth", None) if video else None,
            }
        except Exception:
            pass
    exe = mkvmerge_path or shutil.which("mkvmerge") or shutil.which("mkvmerge.exe")
    if not exe:
        return {
            "width": None,
            "height": None,
            "vcodec": None,
            "acodec": None,
            "bitdepth": None,
        }
    cp = subprocess.run(
        [exe, "-J", str(p)],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags(),
    )
    if cp.returncode != 0:
        return {
            "width": None,
            "height": None,
            "vcodec": None,
            "acodec": None,
            "bitdepth": None,
        }
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError:
        return {
            "width": None,
            "height": None,
            "vcodec": None,
            "acodec": None,
            "bitdepth": None,
        }
    video = next((t for t in data.get("tracks", []) if t.get("type") == "video"), {})
    audio = next((t for t in data.get("tracks", []) if t.get("type") == "audio"), {})
    vp = video.get("properties", {}) if video else {}
    ap = audio.get("properties", {}) if audio else {}
    width = (
        vp.get("pixel_dimensions", "x").split("x")[0]
        if vp.get("pixel_dimensions")
        else None
    )
    height = (
        vp.get("pixel_dimensions", "x").split("x")[-1]
        if vp.get("pixel_dimensions")
        else None
    )
    return {
        "width": int(width) if str(width).isdigit() else None,
        "height": int(height) if str(height).isdigit() else None,
        "vcodec": video.get("codec") or video.get("codec_id"),
        "acodec": audio.get("codec") or audio.get("codec_id"),
        "bitdepth": vp.get("bits_per_channel") or vp.get("bit_depth"),
    }


def replace_template(template: str, data: Mapping[str, Any]) -> str:
    out = template
    for key, value in data.items():
        out = out.replace("{" + key + "}", "Unknown" if value is None else str(value))
    return out


# 进程管理器
class ProcessManager:
    def __init__(self, log: Callable[[str], None]) -> None:
        self.log = log
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()

    def popen(self, args: Sequence[str | Path], **kwargs: Any) -> subprocess.Popen:
        proc = subprocess.Popen(
            [str(x) for x in args],
            encoding="utf-8",
            errors="replace",
            text=True,
            creationflags=creationflags(),
            **kwargs,
        )
        with self._lock:
            self._processes.add(proc)
        return proc

    def run_capture(self, args: Sequence[str | Path]) -> subprocess.CompletedProcess:
        cp = subprocess.run(
            [str(x) for x in args],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=creationflags(),
        )
        return cp

    def run_stream(
        self, args: Sequence[str | Path], *, cwd: str | Path | None = None
    ) -> int:
        self.log("执行命令：" + " ".join(str(x) for x in args))
        proc = self.popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.log(line.rstrip())
            return proc.wait()
        finally:
            with self._lock:
                self._processes.discard(proc)

    def terminate_all(self) -> None:
        with self._lock:
            processes = list(self._processes)
        for proc in processes:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        time.sleep(0.15)
        for proc in processes:
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass


@dataclass(frozen=True, slots=True)
class TrackRule:
    index: int
    language: str | None = None
    name: str | None = None
    delay_ms: int | None = None
    default: bool | None = None


@dataclass(frozen=True, slots=True)
class MediaInputSpec:
    path: str | Path
    kind: MediaKind = "av"
    select_video: Sequence[int] | None = None
    select_audio: Sequence[int] | None = None
    video_rules: Sequence[TrackRule] = field(default_factory=tuple)
    audio_rules: Sequence[TrackRule] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SubtitleInputSpec:
    path: str | Path
    select: Sequence[int] | None = None
    rules: Sequence[TrackRule] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class FontAttachmentSpec:
    path: str | Path
    name: str | None = None


@dataclass(frozen=True, slots=True)
class CoverAttachmentSpec:
    path: str | Path
    name: str | None = None
    description: str = "cover"


@dataclass(slots=True)
class MuxPlan:
    inputs: list[MediaInputSpec] = field(default_factory=list)
    subtitles: list[SubtitleInputSpec] = field(default_factory=list)
    fonts: list[FontAttachmentSpec] = field(default_factory=list)
    cover: CoverAttachmentSpec | None = None
    title: str | None = None
    output: Path | None = None
    ui_language: str = "zh_CN"

    def clone(self) -> "MuxPlan":
        return MuxPlan(
            inputs=list(self.inputs),
            subtitles=list(self.subtitles),
            fonts=list(self.fonts),
            cover=self.cover,
            title=self.title,
            output=self.output,
            ui_language=self.ui_language,
        )

    def clear(self) -> None:
        self.inputs.clear()
        self.subtitles.clear()
        self.fonts.clear()
        self.cover = None
        self.title = None
        self.output = None
        self.ui_language = "zh_CN"


@dataclass(slots=True)
class _ResolvedTrack:
    track_id: int
    rel_index: int
    inherited_default: bool
    desired_default: bool
    default_explicit: bool
    language: str | None
    name: str | None
    delay_ms: int | None


@dataclass(slots=True)
class _ResolvedInput:
    path: Path
    kind: MediaKind
    video_tracks: list[_ResolvedTrack]
    audio_tracks: list[_ResolvedTrack]


@dataclass(slots=True)
class _ResolvedSubtitle:
    path: Path
    subs_tracks: list[_ResolvedTrack]


# mkvmerge 参数生成器
class MkvMergeMuxer:
    def __init__(
        self,
        mkvmerge_path: str | None,
        process_manager: ProcessManager,
        log: Callable[[str], None],
    ) -> None:
        self.mkvmerge = (
            mkvmerge_path or shutil.which("mkvmerge") or shutil.which("mkvmerge.exe")
        )
        self.process_manager = process_manager
        self.log = log
        self._plan = MuxPlan()
        if not self.mkvmerge or not Path(self.mkvmerge).exists():
            found = shutil.which("mkvmerge") or shutil.which("mkvmerge.exe")
            if found:
                self.mkvmerge = found
            else:
                raise FileNotFoundError(
                    "未找到 mkvmerge。请在设置中填写 mkvmerge.exe 的完整路径，或把 MKVToolNix 加入 PATH。"
                )

    def clear_plan(self) -> "MkvMergeMuxer":
        self._plan.clear()
        return self

    def add(
        self,
        *,
        clear: bool = False,
        inputs: MediaInputSpec | Sequence[MediaInputSpec] | None = None,
        subtitles: SubtitleInputSpec | Sequence[SubtitleInputSpec] | None = None,
        fonts: FontAttachmentSpec | Sequence[FontAttachmentSpec] | None = None,
        cover: CoverAttachmentSpec | None = None,
        title: str | None = None,
        output: str | Path | None = None,
        ui_language: str | None = None,
    ) -> "MkvMergeMuxer":
        if clear:
            self._plan.clear()
        self._extend(self._plan.inputs, inputs)
        self._extend(self._plan.subtitles, subtitles)
        self._extend(self._plan.fonts, fonts)
        if cover is not None:
            self._plan.cover = cover
        if title is not None:
            self._plan.title = title
        if output is not None:
            self._plan.output = Path(output)
        if ui_language is not None:
            self._plan.ui_language = ui_language
        return self

    @staticmethod
    def _extend(dst: list[Any], x: Any) -> None:
        if x is None:
            return
        if isinstance(x, Sequence) and not isinstance(x, (str, bytes, Path)):
            dst.extend(x)
        else:
            dst.append(x)

    def _probe_tracks_json(self, media_path: Path) -> dict[str, Any]:
        cp = self.process_manager.run_capture([self.mkvmerge, "-J", media_path])
        if cp.returncode != 0:
            raise RuntimeError(
                f"mkvmerge 无法读取轨道信息：{media_path}\n"
                f"ExitCode={cp.returncode}\nSTDERR:\n{cp.stderr.strip()}\nSTDOUT:\n{cp.stdout.strip()}"
            )
        try:
            return json.loads(cp.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"mkvmerge -J 输出不是有效 JSON：{media_path}\n{exc}"
            ) from exc

    @staticmethod
    def _iter_enabled_tracks(
        probe: Mapping[str, Any], want_type: str
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for track in probe.get("tracks", []) or []:
            if track.get("type") != want_type:
                continue
            props = track.get("properties", {}) or {}
            if props.get("enabled_track", True) is False:
                continue
            out.append(track)
        return out

    @staticmethod
    def _track_id(track: Mapping[str, Any]) -> int:
        return int(track.get("id"))

    @staticmethod
    def _track_inherited_default(track: Mapping[str, Any]) -> bool:
        return (track.get("properties", {}) or {}).get("default_track") is True

    @staticmethod
    def _make_rules_map(rules: Sequence[TrackRule]) -> dict[int, TrackRule]:
        out: dict[int, TrackRule] = {}
        for rule in rules:
            if rule.index < 0:
                raise ValueError(f"TrackRule.index 必须 >= 0：{rule.index}")
            if rule.index in out:
                raise ValueError(f"重复的 TrackRule.index：{rule.index}")
            out[rule.index] = rule
        return out

    @staticmethod
    def _normalize_select(select: Sequence[int] | None, max_count: int) -> list[int]:
        if select is None:
            return list(range(max_count))
        uniq = sorted(set(int(x) for x in select))
        for i in uniq:
            if i < 0 or i >= max_count:
                raise ValueError(
                    f"选择的轨道序号越界：{i}，有效范围 0..{max_count - 1}"
                )
        return uniq

    def _resolve_tracks_of_type(
        self,
        probe: Mapping[str, Any],
        want_type: Literal["video", "audio", "subtitles"],
        select: Sequence[int] | None,
        rules: Sequence[TrackRule],
        *,
        require_non_empty: bool,
        debug_label: str,
    ) -> list[_ResolvedTrack]:
        tracks = self._iter_enabled_tracks(probe, want_type)
        if require_non_empty and not tracks:
            raise ValueError(f"{debug_label} 找不到 {want_type} 轨道。")
        rules_map = self._make_rules_map(rules)
        selected = self._normalize_select(select, len(tracks))
        if select is not None:
            selected_set = set(selected)
            for idx in rules_map:
                if idx not in selected_set:
                    raise ValueError(
                        f"{debug_label} TrackRule.index={idx} 未包含在 select 中。"
                    )
        resolved: list[_ResolvedTrack] = []
        for rel_idx in selected:
            track = tracks[rel_idx]
            rule = rules_map.get(rel_idx)
            inherited = self._track_inherited_default(track)
            if rule is None or rule.default is None:
                desired = inherited
                explicit = False
            else:
                desired = bool(rule.default)
                explicit = True
            resolved.append(
                _ResolvedTrack(
                    track_id=self._track_id(track),
                    rel_index=rel_idx,
                    inherited_default=inherited,
                    desired_default=desired,
                    default_explicit=explicit,
                    language=rule.language if rule else None,
                    name=rule.name if rule else None,
                    delay_ms=rule.delay_ms if rule else None,
                )
            )
        return resolved

    @staticmethod
    def _csv_track_ids(tracks: Sequence[_ResolvedTrack]) -> str:
        return ",".join(str(t.track_id) for t in tracks)

    @staticmethod
    def _guess_cover_mime(path: Path) -> str:
        mime, _ = mimetypes.guess_type(str(path))
        return mime or "image/jpeg"

    @staticmethod
    def _guess_font_mime(path: Path) -> str:
        ext = path.suffix.lower()
        if ext in (".ttf", ".ttc"):
            return "application/x-truetype-font"
        if ext == ".otf":
            return "application/vnd.ms-opentype"
        if ext == ".woff2":
            return "font/woff2"
        mime, _ = mimetypes.guess_type(str(path))
        return mime or "application/octet-stream"

    @staticmethod
    def _ensure_default_tracks(resolved_inputs: Sequence[_ResolvedInput]) -> None:
        all_video: list[_ResolvedTrack] = []
        all_audio: list[_ResolvedTrack] = []
        for inp in resolved_inputs:
            if inp.kind in ("av", "video"):
                all_video.extend(inp.video_tracks)
            if inp.kind in ("av", "audio"):
                all_audio.extend(inp.audio_tracks)
        if all_video and not any(t.desired_default for t in all_video):
            all_video[0].desired_default = True
            all_video[0].default_explicit = True
        if all_audio and not any(t.desired_default for t in all_audio):
            all_audio[0].desired_default = True
            all_audio[0].default_explicit = True

    def _build_track_overrides_args(
        self, tracks: Sequence[_ResolvedTrack]
    ) -> list[str]:
        args: list[str] = []
        for track in tracks:
            if track.language:
                args += ["--language", f"{track.track_id}:{track.language}"]
            if track.name is not None:
                args += ["--track-name", f"{track.track_id}:{track.name}"]
            if track.delay_ms is not None:
                args += ["--sync", f"{track.track_id}:{track.delay_ms}"]
            if track.default_explicit:
                args += [
                    "--default-track",
                    f"{track.track_id}:{'yes' if track.desired_default else 'no'}",
                ]
        return args

    def _build_media_input_args(self, inp: _ResolvedInput) -> list[str]:
        args = [
            "--no-subtitles",
            "--no-attachments",
            "--no-chapters",
            "--no-track-tags",
            "--no-global-tags",
        ]
        if inp.kind == "video":
            args.append("--no-audio")
        elif inp.kind == "audio":
            args.append("--no-video")
        if inp.kind in ("av", "video"):
            args += (
                ["--video-tracks", self._csv_track_ids(inp.video_tracks)]
                if inp.video_tracks
                else ["--no-video"]
            )
        if inp.kind in ("av", "audio"):
            args += (
                ["--audio-tracks", self._csv_track_ids(inp.audio_tracks)]
                if inp.audio_tracks
                else ["--no-audio"]
            )
        args += self._build_track_overrides_args(inp.video_tracks)
        args += self._build_track_overrides_args(inp.audio_tracks)
        args.append(str(inp.path))
        return args

    def _build_subtitle_input_args(self, sub: _ResolvedSubtitle) -> list[str]:
        args = [
            "--no-video",
            "--no-audio",
            "--no-attachments",
            "--no-chapters",
            "--no-track-tags",
            "--no-global-tags",
        ]
        if sub.subs_tracks:
            args += ["--subtitle-tracks", self._csv_track_ids(sub.subs_tracks)]
        args += self._build_track_overrides_args(sub.subs_tracks)
        args.append(str(sub.path))
        return args

    def mux(
        self,
        *,
        inputs: Sequence[MediaInputSpec] | None = None,
        output: str | Path | None = None,
        title: str | None = None,
        subtitles: Sequence[SubtitleInputSpec] | None = None,
        fonts: Sequence[FontAttachmentSpec] | None = None,
        cover: CoverAttachmentSpec | None = None,
        ui_language: str | None = None,
    ) -> Path:
        plan = self._plan.clone()
        if inputs is not None:
            plan.inputs = list(inputs)
        if subtitles is not None:
            plan.subtitles = list(subtitles)
        if fonts is not None:
            plan.fonts = list(fonts)
        if cover is not None:
            plan.cover = cover
        if title is not None:
            plan.title = title
        if output is not None:
            plan.output = Path(output)
        if ui_language is not None:
            plan.ui_language = ui_language
        if not plan.inputs:
            raise ValueError("未指定混流输入文件。")
        if plan.output is None:
            raise ValueError("未指定输出路径。")
        out = plan.output
        ensure_dir(out.parent)
        self.log("解析音视频轨道信息")

        resolved_inputs: list[_ResolvedInput] = []
        for i, spec in enumerate(plan.inputs):
            p = Path(spec.path)
            if not p.exists():
                raise FileNotFoundError(f"输入文件不存在：{p}")
            probe = self._probe_tracks_json(p)
            label = f"[inputs[{i}] {p.name}]"
            v_tracks = self._resolve_tracks_of_type(
                probe,
                "video",
                spec.select_video,
                spec.video_rules,
                require_non_empty=spec.kind in ("av", "video"),
                debug_label=label,
            )
            a_tracks = self._resolve_tracks_of_type(
                probe,
                "audio",
                spec.select_audio,
                spec.audio_rules,
                require_non_empty=spec.kind in ("av", "audio"),
                debug_label=label,
            )
            resolved_inputs.append(_ResolvedInput(p, spec.kind, v_tracks, a_tracks))
        self._ensure_default_tracks(resolved_inputs)

        self.log("解析字幕轨道信息")
        resolved_subs: list[_ResolvedSubtitle] = []
        for i, spec in enumerate(plan.subtitles):
            p = Path(spec.path)
            if not p.exists():
                raise FileNotFoundError(f"字幕文件不存在：{p}")
            probe = self._probe_tracks_json(p)
            tracks = self._resolve_tracks_of_type(
                probe,
                "subtitles",
                spec.select,
                spec.rules,
                require_non_empty=True,
                debug_label=f"[subtitles[{i}] {p.name}]",
            )
            resolved_subs.append(_ResolvedSubtitle(p, tracks))

        cmd = [
            self.mkvmerge,
            "--ui-language",
            plan.ui_language,
            "--priority",
            "lower",
            "-o",
            str(out),
        ]
        if plan.title:
            cmd += ["--title", plan.title]
        for inp in resolved_inputs:
            cmd += self._build_media_input_args(inp)
        for sub in resolved_subs:
            cmd += self._build_subtitle_input_args(sub)

        if plan.cover is not None:
            cpath = Path(plan.cover.path)
            if not cpath.exists():
                raise FileNotFoundError(f"封面文件不存在：{cpath}")
            attach_name = plan.cover.name or f"cover{cpath.suffix.lower() or '.jpg'}"
            cmd += [
                "--attachment-name",
                attach_name,
                "--attachment-mime-type",
                self._guess_cover_mime(cpath),
                "--attachment-description",
                plan.cover.description,
                "--attach-file",
                str(cpath),
            ]

        for font in plan.fonts:
            fpath = Path(font.path)
            if not fpath.exists():
                raise FileNotFoundError(f"字体附件不存在：{fpath}")
            cmd += [
                "--attachment-name",
                font.name or fpath.name,
                "--attachment-mime-type",
                self._guess_font_mime(fpath),
                "--attach-file",
                str(fpath),
            ]

        self.log("开始调用 mkvmerge 混流")
        code = self.process_manager.run_stream(cmd)
        if code != 0:
            raise RuntimeError(f"mkvmerge 混流失败，ExitCode={code}")
        return out


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def blend_color(start: str, end: str, ratio: float) -> str:
    s = hex_to_rgb(start)
    e = hex_to_rgb(end)
    ratio = max(0.0, min(1.0, ratio))
    return rgb_to_hex(tuple(int(s[i] + (e[i] - s[i]) * ratio) for i in range(3)))


def set_hand_cursor(widget: tkinter.Misc) -> None:
    try:
        widget.configure(cursor="hand2")
    except Exception:
        pass


def bind_tree(
    widget: tkinter.Misc, sequence: str, callback: Callable[..., Any]
) -> None:
    try:
        widget.bind(sequence, callback, add="+")
    except Exception:
        pass
    try:
        children = widget.winfo_children()
    except Exception:
        children = []
    for child in children:
        bind_tree(child, sequence, callback)


def make_clickable(widget: tkinter.Misc, callback: Callable[..., Any]) -> None:
    set_hand_cursor(widget)
    try:
        widget.bind("<Button-1>", callback, add="+")
    except Exception:
        pass
    try:
        children = widget.winfo_children()
    except Exception:
        children = []
    for child in children:
        make_clickable(child, callback)


def _font_name_candidates_from_file(font_file: str) -> list[str]:
    stem = Path(font_file).stem.strip()
    if not stem:
        return []
    candidates: list[str] = []
    raw_names = [stem, stem.replace("-", " "), stem.replace("_", " ")]
    for name in raw_names:
        name = re.sub(r"\s+", " ", name).strip()
        if name and name not in candidates:
            candidates.append(name)
        parts = [part for part in re.split(r"[-_\s]+", name) if part]
        while parts and parts[-1].lower() in APP_FONT_WEIGHT_WORDS:
            parts.pop()
        trimmed = " ".join(parts).strip()
        if trimmed and trimmed not in candidates:
            candidates.append(trimmed)
    return candidates


def _available_font_families(root=None) -> set[str]:
    try:
        return (
            set(tkfont.families(root=root))
            if root is not None
            else set(tkfont.families())
        )
    except Exception:
        return set()


def detect_font_family_from_file(font_path: Path) -> str | None:
    font = TTFont(str(font_path), lazy=True)
    try:
        preferred: list[str] = []
        family_names: list[str] = []
        for record in font["name"].names:
            if record.nameID not in {1, 4, 16}:
                continue
            value = record.toUnicode().strip()
            if not value:
                continue
            if record.nameID == 16:
                preferred.append(value)
            elif record.nameID == 1:
                family_names.append(value)
        return (preferred or family_names or [None])[0]
    finally:
        font.close()


def configure_app_font(
    root=None, *, font_file: str = APP_FONT_FILE, preferred_family: str | None = None
) -> str:
    global APP_FONT_FAMILY
    font_path = Path(resource_path(font_file))
    ctk.FontManager.load_font(str(font_path))
    family = preferred_family or detect_font_family_from_file(font_path)
    if not family:
        raise RuntimeError(f"无法从字体文件读取字体族名称：{font_path}")
    available = _available_font_families(root)
    normalized = {name.lower(): name for name in available}
    exact = normalized.get(family.lower())
    if exact:
        APP_FONT_FAMILY = exact
        return APP_FONT_FAMILY
    candidates = [family, *_font_name_candidates_from_file(font_file)]
    for candidate in candidates:
        if not candidate:
            continue
        for available_family in available:
            low = available_family.lower()
            c_low = candidate.lower()
            if c_low == low or c_low in low or low in c_low:
                APP_FONT_FAMILY = available_family
                return APP_FONT_FAMILY

    APP_FONT_FAMILY = family
    return APP_FONT_FAMILY


def _safe_window_exists(window) -> bool:
    try:
        return window is not None and bool(window.winfo_exists())
    except Exception:
        return False


def _hex_to_colorref(hex_color: str) -> int:
    try:
        color = str(hex_color).lstrip("#")
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        return r | (g << 8) | (b << 16)
    except Exception:
        return 0x171211


def _get_window_hwnd(window) -> int | None:
    if os.name != "nt" or not _safe_window_exists(window):
        return None
    try:
        import ctypes

        window.update_idletasks()
        hwnd = int(window.winfo_id())
        if not hwnd:
            return None
        user32 = ctypes.windll.user32
        GA_ROOT = 2
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        return int(root_hwnd if user32.IsWindow(root_hwnd) else hwnd)
    except Exception:
        return None


def apply_window_chrome(window, *, dark: bool | None = None) -> None:
    if os.name != "nt" or not _safe_window_exists(window):
        return
    if dark is None:
        dark = APP_DARK_TITLEBAR
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = _get_window_hwnd(window)
        if hwnd is None:
            return
        state = (hwnd, bool(dark))
        if getattr(window, "_assfun_chrome_state", None) == state:
            return
        dwmapi = ctypes.windll.dwmapi
        enabled = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            try:
                dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd),
                    ctypes.c_int(attr),
                    ctypes.byref(enabled),
                    ctypes.sizeof(enabled),
                )
            except Exception:
                pass
        if dark:
            for attr, value in (
                (35, ctypes.c_int(_hex_to_colorref(BG))),
                (36, ctypes.c_int(_hex_to_colorref(TEXT))),
                (34, ctypes.c_int(_hex_to_colorref(BORDER))),
            ):
                try:
                    dwmapi.DwmSetWindowAttribute(
                        wintypes.HWND(hwnd),
                        ctypes.c_int(attr),
                        ctypes.byref(value),
                        ctypes.sizeof(value),
                    )
                except Exception:
                    pass
        setattr(window, "_assfun_chrome_state", state)
    except Exception:
        pass


def apply_window_icon(window, *, default: bool = False) -> None:
    if not _safe_window_exists(window):
        return
    icon_path = os.path.abspath(resource_path(ICON_FILE))
    if not os.path.exists(icon_path):
        return
    try:
        if os.name == "nt":
            try:
                window.wm_iconbitmap(icon_path)
            except Exception:
                pass
            window.iconbitmap(icon_path)
            if default:
                try:
                    window.iconbitmap(default=icon_path)
                except Exception:
                    pass
            setattr(window, "_assfun_icon_path", icon_path)
            return
        photo = tkinter.PhotoImage(file=icon_path, master=window)
        window.iconphoto(default, photo)
        setattr(window, "_assfun_icon_photo", photo)
    except Exception:
        pass


def schedule_window_icon(window, *, default: bool = False) -> None:
    apply_window_icon(window, default=default)
    for delay in (260, 520, 900):
        try:
            window.after(
                delay, lambda w=window, d=default: apply_window_icon(w, default=d)
            )
        except Exception:
            pass


def screen_center_geometry(window, width: int, height: int) -> str:
    try:
        window.update_idletasks()
        screen_w = int(window.winfo_screenwidth())
        screen_h = int(window.winfo_screenheight())
        x = max((screen_w - int(width)) // 2, 0)
        y = max((screen_h - int(height)) // 2, 0)
        return f"{int(width)}x{int(height)}+{x}+{y}"
    except Exception:
        return f"{int(width)}x{int(height)}"


def setup_fixed_window(
    window, width: int, height: int, *, resizable: bool = False, center: bool = True
) -> None:
    try:
        window.geometry(
            screen_center_geometry(window, width, height)
            if center
            else f"{width}x{height}"
        )
    except Exception:
        try:
            window.geometry(f"{width}x{height}")
        except Exception:
            pass
    try:
        window.minsize(width, height)
    except Exception:
        pass
    try:
        window.resizable(resizable, resizable)
    except Exception:
        pass


def focus_existing_toplevel(window, *, force_later: bool = True) -> bool:
    if not _safe_window_exists(window):
        return False

    def _focus(force: bool = False) -> None:
        if not _safe_window_exists(window):
            return
        for action in (window.deiconify, window.lift, window.focus_set):
            try:
                action()
            except Exception:
                pass
        if force:
            try:
                window.focus_force()
            except Exception:
                pass

    _focus(force=True)
    if force_later:
        try:
            window.after(80, lambda: _focus(force=True))
        except Exception:
            pass
    return True


def show_centered_toplevel(
    window, width: int, height: int, *, focus: bool = True, resizable: bool = False
) -> None:
    if not _safe_window_exists(window):
        return
    try:
        window.withdraw()
    except Exception:
        pass
    setup_fixed_window(window, width, height, resizable=resizable, center=True)
    apply_window_icon(window, default=False)

    def _show() -> None:
        if not _safe_window_exists(window):
            return
        apply_window_icon(window, default=False)
        try:
            window.update_idletasks()
            window.deiconify()
        except Exception:
            pass
        apply_window_chrome(window)
        if focus:
            focus_existing_toplevel(window, force_later=True)
        schedule_window_icon(window, default=False)

    try:
        window.after(280, _show)
    except Exception:
        _show()


def show_main_window(window) -> None:
    schedule_window_icon(window, default=True)
    try:
        window.update_idletasks()
        window.deiconify()
    except Exception:
        pass
    apply_window_chrome(window)
    focus_existing_toplevel(window, force_later=False)


DISABLED_CURSOR = ""


def _widget_is_interactive(widget: tkinter.Misc) -> bool:
    try:
        current = widget
        while current is not None:
            if getattr(current, "enabled", True) is False:
                return False
            try:
                state = current.cget("state")
            except Exception:
                state = None
            if str(state).lower() == "disabled":
                return False
            current = getattr(current, "master", None)
    except Exception:
        return True
    return True


def _configure_cursor(widget: tkinter.Misc, cursor: str) -> None:
    """只在目标指针发生变化时写入，避免 CTk 在悬停期间反复重绘。"""
    try:
        current = getattr(widget, "_assfun_cursor", None)
        if current == cursor:
            return
        widget.configure(cursor=cursor)
        setattr(widget, "_assfun_cursor", cursor)
    except Exception:
        pass


def apply_interactive_cursor(widget: tkinter.Misc, cursor: str = "hand2") -> None:
    _configure_cursor(
        widget, cursor if _widget_is_interactive(widget) else DISABLED_CURSOR
    )


def set_pointer_cursor(widget: tkinter.Misc, cursor: str = "hand2") -> None:
    """绑定轻量指针反馈。

    不在 <Motion> 中刷新 cursor，也不递归配置子控件，避免 CustomTkinter 控件
    在悬停期间持续触发 redraw，造成尺寸抖动或闪烁。
    """
    try:
        widget.bind(
            "<Enter>", lambda _event: apply_interactive_cursor(widget, cursor), add="+"
        )
        widget.bind("<Leave>", lambda _event: _configure_cursor(widget, ""), add="+")
    except Exception:
        pass
    _configure_cursor(widget, "")


def _is_descendant_widget(widget: tkinter.Misc, root: tkinter.Misc) -> bool:
    try:
        current = widget
        while current is not None:
            if current == root:
                return True
            current = current.master
    except Exception:
        return False
    return False


def widget_contains_pointer(widget: tkinter.Misc) -> bool:
    """判断鼠标是否仍严格位于控件或其子控件上。"""
    try:
        if not widget.winfo_exists():
            return False
        x = widget.winfo_pointerx()
        y = widget.winfo_pointery()
        left = widget.winfo_rootx()
        top = widget.winfo_rooty()
        right = left + max(0, widget.winfo_width())
        bottom = top + max(0, widget.winfo_height())
        if not (left <= x < right and top <= y < bottom):
            return False
        containing = widget.winfo_containing(x, y)
        if containing is None:
            return False
        return _is_descendant_widget(containing, widget)
    except Exception:
        return False


def widget_area(widget: tkinter.Misc) -> int:
    try:
        return max(1, int(widget.winfo_width())) * max(1, int(widget.winfo_height()))
    except Exception:
        return 1 << 30


def widget_under_pointer(widget: tkinter.Misc) -> tkinter.Misc | None:
    """返回当前鼠标下方的实际 Tk 控件。"""
    try:
        if not widget.winfo_exists():
            return None
        return widget.winfo_containing(widget.winfo_pointerx(), widget.winfo_pointery())
    except Exception:
        return None


def ancestor_distance(widget: tkinter.Misc | None, ancestor: tkinter.Misc) -> int:
    """返回 widget 到 ancestor 的父级距离；不是祖先时返回极大值。"""
    if widget is None:
        return 1 << 30
    try:
        distance = 0
        current = widget
        while current is not None:
            if current == ancestor:
                return distance
            current = current.master
            distance += 1
    except Exception:
        pass
    return 1 << 30


class TooltipRegistry:
    """全局悬停提示仲裁器。

    同一时间只允许一个 Tooltip 存在。父容器与子控件都有提示时，
    按鼠标下方实际控件到提示根控件的父级距离排序：
    鼠标在大容器空白处时显示大容器提示，鼠标进入小控件时显示小控件提示。
    面积只作为距离相同的兜底排序，不再作为主要判断依据。
    """

    active: "HoverTooltip | None" = None
    pending: list["HoverTooltip"] = []
    counter: int = 0

    @classmethod
    def owner_from_pointer(cls, reference: tkinter.Misc) -> "HoverTooltip | None":
        """返回鼠标实际命中链路上最近的 Tooltip 所有者。"""
        pointer_widget = widget_under_pointer(reference)
        current = pointer_widget
        try:
            while current is not None:
                tip = getattr(current, "_assfun_tooltip_owner", None)
                if isinstance(tip, HoverTooltip) and tip._is_available():
                    return tip
                current = getattr(current, "master", None)
        except Exception:
            return None
        return None

    @classmethod
    def schedule(cls, tip: "HoverTooltip") -> None:
        cls.counter += 1
        tip._schedule_order = cls.counter
        if tip not in cls.pending:
            cls.pending.append(tip)
        if cls.active is not None and cls.active is not tip:
            cls.active._force_hide(unregister=True)

    @classmethod
    def discard(cls, tip: "HoverTooltip") -> None:
        cls.pending = [item for item in cls.pending if item is not tip]
        if cls.active is tip:
            cls.active = None

    @classmethod
    def best(cls) -> "HoverTooltip | None":
        candidates: list["HoverTooltip"] = []
        for tip in list(cls.pending):
            if tip._is_available() and widget_contains_pointer(tip.root):
                candidates.append(tip)
            else:
                cls.discard(tip)
        if not candidates:
            return None

        pointer_widget = widget_under_pointer(candidates[0].root)
        return min(
            candidates,
            key=lambda tip: (
                ancestor_distance(pointer_widget, tip.root),
                widget_area(tip.root),
                -tip._schedule_order,
            ),
        )

    @classmethod
    def activate(cls, tip: "HoverTooltip") -> None:
        if cls.active is not None and cls.active is not tip:
            cls.active._force_hide(unregister=True)
        cls.active = tip
        cls.pending = [item for item in cls.pending if item is not tip]


# 悬停提示
class HoverTooltip:
    def __init__(
        self,
        widget: tkinter.Misc,
        text: str,
        *,
        delay_ms: int = 420,
        root: tkinter.Misc | None = None,
        bind: bool = True,
        font_size: int = 11,
        check_ms: int = 80,
    ) -> None:
        self.widget = widget
        self.root = root or widget
        self.text = str(text or "")
        self.delay_ms = delay_ms
        self.font_size = font_size
        self.check_ms = check_ms
        self._job: str | None = None
        self._watch_job: str | None = None
        self._tip: tkinter.Toplevel | None = None
        self._schedule_order = 0
        self._last_pointer_xy: tuple[int, int] | None = None
        try:
            setattr(self.root, "_assfun_tooltip_owner", self)
        except Exception:
            pass
        if bind:
            self.bind_to(widget)

    def bind_to(self, widget: tkinter.Misc) -> None:
        try:
            widget.bind("<Enter>", self._schedule, add="+")
            widget.bind("<Leave>", self._hide, add="+")
            widget.bind("<Motion>", self._watch_pointer, add="+")
            widget.bind("<ButtonPress>", self._force_hide, add="+")
            widget.bind("<Destroy>", self._force_hide, add="+")
            widget.winfo_toplevel().bind("<FocusOut>", self._force_hide, add="+")
            widget.winfo_toplevel().bind("<Unmap>", self._force_hide, add="+")
        except Exception:
            pass

    def _capture_pointer(self, event=None) -> tuple[int, int]:
        """记录并返回当前鼠标屏幕坐标。

        优先使用事件自带的 x_root / y_root；这比用某个绑定控件读取
        winfo_pointerx/y 更稳定，尤其是图标按钮、Canvas 子部件和 DPI 缩放场景。
        """
        try:
            if (
                event is not None
                and hasattr(event, "x_root")
                and hasattr(event, "y_root")
            ):
                xy = (int(event.x_root), int(event.y_root))
                self._last_pointer_xy = xy
                return xy
        except Exception:
            pass
        try:
            xy = (int(self.root.winfo_pointerx()), int(self.root.winfo_pointery()))
            self._last_pointer_xy = xy
            return xy
        except Exception:
            pass
        if self._last_pointer_xy is not None:
            return self._last_pointer_xy
        return (0, 0)

    def _is_available(self) -> bool:
        return bool(
            self.text
            and _widget_is_interactive(self.widget)
            and _widget_is_interactive(self.root)
        )

    def _schedule(self, event=None) -> None:
        self._capture_pointer(event)
        if not self._is_available():
            self._force_hide()
            return
        owner = TooltipRegistry.owner_from_pointer(self.root)
        if owner is not None and owner is not self:
            self._force_hide()
            return
        self._cancel_job()
        TooltipRegistry.schedule(self)
        try:
            self._job = self.widget.after(self.delay_ms, self._show)
        except Exception:
            self._job = None

    def _show(self) -> None:
        self._job = None
        if (
            self._tip is not None
            or not self._is_available()
            or not widget_contains_pointer(self.root)
        ):
            TooltipRegistry.discard(self)
            return
        owner = TooltipRegistry.owner_from_pointer(self.root)
        if owner is not None and owner is not self:
            TooltipRegistry.discard(self)
            return
        if TooltipRegistry.best() is not self:
            TooltipRegistry.discard(self)
            return
        try:
            TooltipRegistry.activate(self)
            self._tip = tkinter.Toplevel(self.widget)
            self._tip.withdraw()
            self._tip.wm_overrideredirect(True)
            try:
                self._tip.attributes("-topmost", True)
            except Exception:
                pass
            label = tkinter.Label(
                self._tip,
                text=self.text,
                bg=THEME["panel_2"],
                fg=THEME["text"],
                bd=1,
                relief="solid",
                padx=10,
                pady=6,
                justify="left",
                wraplength=420,
                font=(APP_FONT_FAMILY, self.font_size),
            )
            label.pack()
            self._position_tip()
            self._tip.deiconify()
            self._watch_pointer()
        except Exception:
            self._tip = None
            TooltipRegistry.discard(self)

    def _tooltip_position(self) -> tuple[int, int]:
        px, py = self._capture_pointer()
        try:
            self._tip.update_idletasks()
        except Exception:
            pass
        tip_w = max(1, self._tip.winfo_reqwidth())
        tip_h = max(1, self._tip.winfo_reqheight())

        try:
            screen_left = int(self.widget.winfo_vrootx())
            screen_top = int(self.widget.winfo_vrooty())
            screen_right = screen_left + int(self.widget.winfo_vrootwidth())
            screen_bottom = screen_top + int(self.widget.winfo_vrootheight())
        except Exception:
            screen_left = 0
            screen_top = 0
            screen_right = max(1, int(self.widget.winfo_screenwidth()))
            screen_bottom = max(1, int(self.widget.winfo_screenheight()))

        gap_x = 12
        gap_y = 14
        x = px + gap_x
        y = py - tip_h - gap_y

        if y < screen_top + 4:
            y = py + gap_y + 4

        if screen_top <= py <= screen_bottom and y + tip_h > screen_bottom - 4:
            candidate = py - tip_h - gap_y
            if candidate >= screen_top + 4:
                y = candidate

        if screen_right > screen_left and x + tip_w > screen_right - 4:
            x = px - tip_w - gap_x
        if x < screen_left + 4:
            x = screen_left + 4
        if y < screen_top + 4:
            y = screen_top + 4
        return int(x), int(y)

    def _position_tip(self) -> None:
        if self._tip is None:
            return
        x, y = self._tooltip_position()
        self._tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None) -> None:
        try:
            self.widget.after(24, self._hide_if_outside)
        except Exception:
            self._force_hide()

    def _hide_if_outside(self) -> None:
        if not widget_contains_pointer(self.root):
            self._force_hide()

    def _watch_pointer(self, event=None) -> None:
        self._capture_pointer(event)
        if self._tip is None:
            return
        owner = TooltipRegistry.owner_from_pointer(self.root)
        if owner is not None and owner is not self:
            self._force_hide()
            try:
                owner._schedule()
            except Exception:
                pass
            return
        if not self._is_available() or not widget_contains_pointer(self.root):
            self._force_hide()
            return
        try:
            self._position_tip()
        except Exception:
            self._force_hide()
            return
        self._cancel_watch_job()
        try:
            self._watch_job = self.widget.after(self.check_ms, self._watch_pointer)
        except Exception:
            self._watch_job = None

    def _force_hide(self, _event=None, *, unregister: bool = True) -> None:
        self._cancel_job()
        self._cancel_watch_job()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None
        if unregister:
            TooltipRegistry.discard(self)

    def _cancel_job(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _cancel_watch_job(self) -> None:
        if self._watch_job is not None:
            try:
                self.widget.after_cancel(self._watch_job)
            except Exception:
                pass
            self._watch_job = None


def attach_tooltip_tree(widget: tkinter.Misc, tooltip: str | None) -> None:
    if not tooltip:
        return
    refs = getattr(widget, "_hover_tooltip_refs", None)
    if refs is None:
        refs = []
        try:
            setattr(widget, "_hover_tooltip_refs", refs)
        except Exception:
            pass
    tip = HoverTooltip(widget, tooltip, root=widget, bind=False)
    refs.append(tip)

    def walk(target: tkinter.Misc) -> None:
        existing_owner = getattr(target, "_assfun_tooltip_owner", None)
        if (
            target is not widget
            and isinstance(existing_owner, HoverTooltip)
            and existing_owner is not tip
        ):
            return
        tip.bind_to(target)
        try:
            children = target.winfo_children()
        except Exception:
            children = []
        for child in children:
            walk(child)

    walk(widget)


def attach_interactive_feedback(widget, tooltip: str | None = None) -> None:
    set_pointer_cursor(widget)
    if tooltip:
        refs = getattr(widget, "_hover_tooltip_refs", None)
        if refs is None:
            refs = []
            try:
                setattr(widget, "_hover_tooltip_refs", refs)
            except Exception:
                pass
        refs.append(HoverTooltip(widget, tooltip))


class UIFactory:
    def __init__(self, colors: dict[str, str], font: ctk.CTkFont):
        self.colors = dict(colors)
        self.base_font = font

    def font(self, size: int = 15, weight: str = "normal") -> ctk.CTkFont:
        family = getattr(self.base_font, "_family", APP_FONT_FAMILY)
        return ctk.CTkFont(family=family, size=size, weight=weight)

    def card(
        self, master, *, radius: int = 16, fg_color: str | None = None
    ) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            master,
            fg_color=fg_color or self.colors["panel"],
            border_color=self.colors["border_soft"],
            border_width=1,
            corner_radius=radius,
        )

    def button(
        self,
        master,
        text: str,
        command,
        *,
        variant: str = "primary",
        height: int = 34,
        width: int | None = None,
        image=None,
        tooltip: str | None = None,
    ) -> ctk.CTkButton:
        if variant == "secondary":
            fg_color, hover_color, border_width, border_color = (
                self.colors["panel_3"],
                self.colors["border"],
                1,
                self.colors["border"],
            )
        elif variant == "ghost":
            fg_color, hover_color, border_width, border_color = (
                "transparent",
                self.colors["panel_2"],
                1,
                self.colors["border"],
            )
        else:
            fg_color, hover_color, border_width, border_color = (
                self.colors["accent"],
                self.colors["accent_hover"],
                0,
                self.colors["accent"],
            )
        kwargs = {"width": width} if width is not None else {}
        btn = ctk.CTkButton(
            master,
            text=text,
            image=image,
            command=command,
            hover=True,
            font=self.font(15, "bold" if variant == "primary" else "normal"),
            fg_color=fg_color,
            hover_color=hover_color,
            text_color=self.colors["text"],
            border_color=border_color,
            border_width=border_width,
            corner_radius=10,
            height=height,
            border_spacing=4,
            **kwargs,
        )
        attach_interactive_feedback(
            btn, tooltip if tooltip is not None else (text or None)
        )
        return btn


# 平滑滚动
class SmoothScrollableFrame(ctk.CTkScrollableFrame):
    _active_scroll_owner = None

    def __init__(
        self,
        *args,
        scroll_speed: float = 0.26,
        decay: float = 0.70,
        frame_ms: int = 12,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._smooth_scroll_velocity = 0.0
        self._smooth_scroll_job = None
        self._scroll_speed = scroll_speed
        self._scroll_decay = decay
        self._scroll_frame_ms = frame_ms
        self.bind("<Enter>", self._bind_smooth_mousewheel)
        self.bind("<Leave>", self._unbind_smooth_mousewheel)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _bind_smooth_mousewheel(self, _event=None) -> None:
        if (
            SmoothScrollableFrame._active_scroll_owner is not None
            and SmoothScrollableFrame._active_scroll_owner is not self
        ):
            try:
                SmoothScrollableFrame._active_scroll_owner._unbind_smooth_mousewheel()
            except Exception:
                pass
        SmoothScrollableFrame._active_scroll_owner = self
        self.bind_all("<MouseWheel>", self._on_smooth_mousewheel)
        self.bind_all("<Button-4>", self._on_smooth_mousewheel)
        self.bind_all("<Button-5>", self._on_smooth_mousewheel)

    def _unbind_smooth_mousewheel(self, _event=None) -> None:
        if SmoothScrollableFrame._active_scroll_owner is not self:
            return
        SmoothScrollableFrame._active_scroll_owner = None
        try:
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")
        except Exception:
            pass

    def _on_destroy(self, _event=None) -> None:
        self._unbind_smooth_mousewheel()
        if self._smooth_scroll_job is not None:
            try:
                self.after_cancel(self._smooth_scroll_job)
            except Exception:
                pass
            self._smooth_scroll_job = None
        self._smooth_scroll_velocity = 0.0

    def _event_direction(self, event) -> int:
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        return -1 if getattr(event, "delta", 0) > 0 else 1

    def _on_smooth_mousewheel(self, event) -> str:
        if (
            SmoothScrollableFrame._active_scroll_owner is not self
            or not _safe_window_exists(self)
        ):
            return "break"
        self._smooth_scroll_velocity += (
            self._event_direction(event) * self._scroll_speed
        )
        self._smooth_scroll_velocity = max(-1.4, min(1.4, self._smooth_scroll_velocity))
        if self._smooth_scroll_job is None:
            self._animate_smooth_scroll()
        return "break"

    def _animate_smooth_scroll(self) -> None:
        if (
            SmoothScrollableFrame._active_scroll_owner is not self
            or not _safe_window_exists(self)
        ):
            self._smooth_scroll_job = None
            self._smooth_scroll_velocity = 0.0
            return
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is None:
            self._smooth_scroll_job = None
            return
        if abs(self._smooth_scroll_velocity) < 0.006:
            self._smooth_scroll_velocity = 0.0
            self._smooth_scroll_job = None
            return
        try:
            first, _last = canvas.yview()
            canvas.yview_moveto(
                max(0.0, min(1.0, first + self._smooth_scroll_velocity * 0.032))
            )
        except Exception:
            pass
        self._smooth_scroll_velocity *= self._scroll_decay
        self._smooth_scroll_job = self.after(
            self._scroll_frame_ms, self._animate_smooth_scroll
        )


def smooth_scroll_bind(
    widget: tkinter.Misc, scroll_target: Any | None = None, units: int = 3
) -> None:
    target = scroll_target or widget
    state = {"velocity": 0.0, "job": None, "active": False}

    def direction(event: Any) -> int:
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        return -1 if getattr(event, "delta", 0) > 0 else 1

    def animate() -> None:
        state["job"] = None
        if not state["active"] or abs(state["velocity"]) < 0.03:
            state["velocity"] = 0.0
            return
        try:
            target.yview_scroll(int(1 if state["velocity"] > 0 else -1), "units")
        except Exception:
            try:
                target._parent_canvas.yview_scroll(
                    int(1 if state["velocity"] > 0 else -1), "units"
                )
            except Exception:
                return
        state["velocity"] *= 0.72
        state["job"] = widget.after(12, animate)

    def on_wheel(event: Any) -> str:
        state["active"] = True
        state["velocity"] += direction(event) * max(1, units)
        state["velocity"] = max(-8.0, min(8.0, state["velocity"]))
        if state["job"] is None:
            animate()
        return "break"

    def on_enter(_event=None) -> None:
        state["active"] = True
        try:
            widget.bind_all("<MouseWheel>", on_wheel)
            widget.bind_all("<Button-4>", on_wheel)
            widget.bind_all("<Button-5>", on_wheel)
        except Exception:
            pass

    def on_leave(_event=None) -> None:
        state["active"] = False
        try:
            widget.unbind_all("<MouseWheel>")
            widget.unbind_all("<Button-4>")
            widget.unbind_all("<Button-5>")
        except Exception:
            pass

    def on_destroy(_event=None) -> None:
        on_leave()
        job = state.get("job")
        if job is not None:
            try:
                widget.after_cancel(job)
            except Exception:
                pass
            state["job"] = None

    try:
        widget.bind("<Enter>", on_enter, add="+")
        widget.bind("<Leave>", on_leave, add="+")
        widget.bind("<Destroy>", on_destroy, add="+")
    except Exception:
        pass


class ToggleSwitch(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        variable: ctk.StringVar,
        command,
        onvalue: str = "True",
        offvalue: str = "False",
        colors: dict[str, str],
        width: int = 56,
        height: int = 28,
        knob_size: int = 24,
        scale: int = 4,
        tooltip: str | None = None,
    ):
        self.colors = dict(colors)
        super().__init__(master, fg_color="transparent", width=width, height=height)
        self.variable = variable
        self.command = command
        self.onvalue = onvalue
        self.offvalue = offvalue
        self.track_width = width
        self.track_height = height
        self.knob_size = min(knob_size, height - 4)
        self.padding = max(2, round((height - self.knob_size) / 2))
        self.scale = max(2, int(scale))
        self._anim_job = None
        self._hover = False
        self._knob_x = float(self._target_x())
        self._image_ref = None
        self.configure(width=width, height=height)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = tkinter.Canvas(
            self,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            relief="flat",
            bg=self.colors["panel"],
        )
        self.canvas.grid(row=0, column=0)
        self.grid_propagate(False)
        self.canvas.bind("<Button-1>", self.toggle)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self.toggle)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        attach_interactive_feedback(self.canvas, tooltip)
        set_pointer_cursor(self)
        self.draw()

    def _is_on(self) -> bool:
        return self.variable.get() == self.onvalue

    def _target_x(self) -> int:
        return (
            self.track_width - self.padding - self.knob_size
            if self._is_on()
            else self.padding
        )

    def toggle(self, _event=None) -> None:
        self.variable.set(self.offvalue if self._is_on() else self.onvalue)
        self.animate_to(self._target_x())
        self.command()

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self.draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self.draw()

    def animate_to(self, target_x: int) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        distance = target_x - self._knob_x
        if abs(distance) < 0.35:
            self._knob_x = float(target_x)
            self.draw()
            return
        self._knob_x += distance * 0.32
        self.draw()
        self._anim_job = self.after(12, lambda: self.animate_to(target_x))

    def _make_switch_image(self) -> Image.Image:
        scale = self.scale
        image = Image.new(
            "RGBA", (self.track_width * scale, self.track_height * scale), (0, 0, 0, 0)
        )
        draw = ImageDraw.Draw(image)
        is_on = self._is_on()
        track = self.colors["accent"] if is_on else self.colors["panel_3"]
        if self._hover and not is_on:
            track = self.colors["border"]
        elif self._hover and is_on:
            track = self.colors["accent_hover"]
        draw.rounded_rectangle(
            (0, 0, self.track_width * scale, self.track_height * scale),
            radius=(self.track_height // 2) * scale,
            fill=track,
            outline=self.colors["accent"] if is_on else self.colors["border_soft"],
            width=max(1, scale),
        )
        knob_x = int(round(self._knob_x))
        knob = (
            knob_x * scale,
            self.padding * scale,
            (knob_x + self.knob_size) * scale,
            (self.padding + self.knob_size) * scale,
        )
        shadow = (knob[0], knob[1] + scale, knob[2], knob[3] + scale)
        draw.ellipse(shadow, fill=(0, 0, 0, 46))
        draw.ellipse(knob, fill="#FFFFFF", outline="#E7ECF3", width=max(1, scale))
        resample_filter = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        return image.resize((self.track_width, self.track_height), resample_filter)

    def draw(self) -> None:
        self.canvas.delete("all")
        self.canvas.configure(bg=self.colors["panel"])
        self._image_ref = ImageTk.PhotoImage(self._make_switch_image())
        self.canvas.create_image(0, 0, image=self._image_ref, anchor="nw")


class DropBox(ctk.CTkFrame):
    def __init__(
        self,
        master: "ASSFunUI",
        *,
        title: str,
        hint: str,
        exts: Sequence[str] | None,
        multiple: bool,
        height: int,
        on_change: Callable[[list[Path]], None],
        help_text: str = "",
    ) -> None:
        super().__init__(
            master,
            fg_color=THEME["panel"],
            corner_radius=14,
            border_width=1,
            border_color=THEME["border_soft"],
        )
        self.master_ui = master
        self.exts = tuple(e.lower() for e in exts) if exts else None
        self.multiple = multiple
        self.on_change = on_change
        self.paths: list[Path] = []
        self.hint = hint
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head,
            text=title,
            font=self.master_ui.font_mid,
            text_color=THEME["text"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        choose = ctk.CTkButton(
            head,
            text="选择",
            width=64,
            height=28,
            fg_color=THEME["panel_3"],
            hover_color=THEME["border"],
            command=self.pick,
            font=self.master_ui.font_small,
            text_color=THEME["text"],
            corner_radius=9,
        )
        clear = ctk.CTkButton(
            head,
            text="清空",
            width=64,
            height=28,
            fg_color=THEME["panel_3"],
            hover_color=THEME["border"],
            command=self.clear,
            font=self.master_ui.font_small,
            text_color=THEME["text"],
            corner_radius=9,
        )
        choose.grid(row=0, column=1, padx=(8, 0))
        clear.grid(row=0, column=2, padx=(8, 0))
        attach_interactive_feedback(choose, f"选择{title}")
        attach_interactive_feedback(clear, f"清空{title}")

        self.box = ctk.CTkTextbox(
            self,
            height=height,
            fg_color=THEME["input"],
            text_color=THEME["muted"],
            border_width=1,
            border_color=THEME["border_soft"],
            corner_radius=10,
            font=self.master_ui.font_small,
            wrap="none",
            border_spacing=7,
        )
        self.box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 12))
        self._render()
        smooth_scroll_bind(self.box, self.box, units=2)
        for widget in (self, self.box):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._drop)
            except Exception:
                pass
        self.bind(
            "<Enter>", lambda _e: self.configure(border_color=THEME["border"]), add="+"
        )
        self.bind(
            "<Leave>",
            lambda _e: self.configure(border_color=THEME["border_soft"]),
            add="+",
        )
        if help_text:
            attach_tooltip_tree(self, help_text)

    def set_paths(self, paths: Iterable[Path]) -> None:
        valid: list[Path] = []
        for path in paths:
            path = Path(path)
            if not path.exists():
                self.master_ui.log(f"忽略不存在的路径：{path}")
                continue
            if self.exts and path.suffix.lower() not in self.exts:
                self.master_ui.log(f"忽略不支持的文件类型：{path}")
                continue
            valid.append(path)
        if not self.multiple and valid:
            valid = [valid[-1]]
        self.paths = valid
        self._render()
        self.on_change(list(self.paths))

    def clear(self) -> None:
        self.paths = []
        self._render()
        self.on_change([])

    def pick(self) -> None:
        filetypes = (
            [("允许的文件", " ".join(f"*{e}" for e in self.exts))]
            if self.exts
            else [("全部文件", "*.*")]
        )
        filetypes.append(("全部文件", "*.*"))
        if self.multiple:
            selected = filedialog.askopenfilenames(
                parent=self, title="选择文件", filetypes=filetypes
            )
            if selected:
                self.set_paths(Path(x) for x in selected)
        else:
            selected = filedialog.askopenfilename(
                parent=self, title="选择文件", filetypes=filetypes
            )
            if selected:
                self.set_paths([Path(selected)])

    def _drop(self, event: Any) -> None:
        self.set_paths(parse_drop_paths(event.data))

    def _render(self) -> None:
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        if self.paths:
            self.box.configure(text_color=THEME["text"])
            self.box.insert("1.0", "\n".join(str(x) for x in self.paths))
        else:
            self.box.configure(text_color=THEME["muted"])
            self.box.insert("1.0", self.hint)
        self.box.configure(state="disabled")


# 选项卡
class ToggleCard(ctk.CTkFrame):
    def __init__(
        self,
        master: tkinter.Misc,
        app: "ASSFunUI",
        key: str,
        text: str,
        default: bool,
        help_text: str = "",
    ) -> None:
        super().__init__(
            master,
            fg_color=THEME["panel"],
            corner_radius=14,
            border_width=1,
            border_color=THEME["border_soft"],
        )
        self.master_ui = app
        self.key = key
        self.text = text
        self.enabled = True
        self._color = THEME["panel"]
        self._hovering = False
        self._animation_id: str | None = None
        if key not in app.values:
            app.values[key] = default
        self.grid_columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(
            self, text=text, font=app.font, text_color=THEME["text"], anchor="w"
        )
        self.state_label = ctk.CTkLabel(
            self, text="", font=app.font_small, text_color=THEME["muted"], anchor="w"
        )
        self.title_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(11, 0))
        self.state_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(1, 11))
        self._bind_click_tree(self)
        if help_text:
            attach_tooltip_tree(self, help_text)
        self.refresh(animate=False)

    def _bind_click_tree(self, widget: tkinter.Misc) -> None:
        try:
            widget.bind("<Button-1>", self._clicked, add="+")
            widget.bind("<Enter>", self._enter, add="+")
            widget.bind("<Leave>", self._leave, add="+")
            widget.configure(cursor="hand2" if self.enabled else "")
        except Exception:
            pass
        for child in widget.winfo_children():
            self._bind_click_tree(child)

    def _apply_cursor_tree(self, widget: tkinter.Misc | None = None) -> None:
        widget = widget or self
        try:
            widget.configure(cursor="hand2" if self.enabled else "")
        except tkinter.TclError:
            try:
                widget.configure(cursor="")
            except Exception:
                pass
        except Exception:
            pass
        try:
            children = widget.winfo_children()
        except Exception:
            children = []
        for child in children:
            self._apply_cursor_tree(child)

    def _clicked(self, _event: Any = None) -> str:
        if self.enabled:
            self.set_value(not self.master_ui.values.get(self.key, False), notify=True)
        return "break"

    def _enter(self, _event: Any = None) -> None:
        self._hovering = True
        if self.enabled and not self.master_ui.values.get(self.key, False):
            self._animate_to(THEME["panel_2"])

    def _leave(self, _event: Any = None) -> None:
        if widget_contains_pointer(self):
            return
        self._hovering = False
        if self.enabled:
            self.refresh(animate=True)

    def set_enabled(self, enabled: bool, *, animate: bool = True) -> None:
        enabled = bool(enabled)
        if self.enabled == enabled:
            self.refresh(animate=False if not animate else True)
            return
        self.enabled = enabled
        self._apply_cursor_tree()
        if not enabled:
            for ref in getattr(self, "_hover_tooltip_refs", []):
                try:
                    ref._force_hide()
                except Exception:
                    pass
        self.refresh(animate=animate)

    def set_value(
        self, value: bool, *, notify: bool = True, animate: bool = True
    ) -> None:
        """
        notify=False 用于主界面模式同步，避免 on_mode_changed 与 set_value 互相回调。
        """
        value = bool(value)
        old_value = bool(self.master_ui.values.get(self.key, False))
        self.master_ui.values[self.key] = value
        self.refresh(animate=animate and old_value != value)
        if notify and not getattr(self.master_ui, "_syncing_mode_state", False):
            self.master_ui.on_mode_changed()

    def refresh(self, animate: bool = True) -> None:
        value = bool(self.master_ui.values.get(self.key, False))
        if not self.enabled:
            target, border, text, state = (
                THEME["disabled"],
                THEME["border_soft"],
                THEME["muted_2"],
                "不可用",
            )
        elif value:
            target, border, text, state = (
                THEME["accent_soft"],
                THEME["accent"],
                THEME["text"],
                "已启用",
            )
        else:
            target, border, text, state = (
                THEME["panel"],
                THEME["border_soft"],
                THEME["muted"],
                "已关闭",
            )
        try:
            self.configure(border_color=border)
            self.title_label.configure(text_color=text)
            self.state_label.configure(
                text=state,
                text_color=(
                    THEME["accent"] if value and self.enabled else THEME["muted"]
                ),
            )
        except tkinter.TclError:
            return
        if animate:
            self._animate_to(target)
        else:
            self._cancel_animation()
            self._color = target
            try:
                self.configure(fg_color=target)
            except tkinter.TclError:
                pass

    def _cancel_animation(self) -> None:
        if self._animation_id:
            try:
                self.after_cancel(self._animation_id)
            except Exception:
                pass
            self._animation_id = None

    def _animate_to(self, target: str, step: int = 0) -> None:
        if step == 0:
            self._cancel_animation()
        if step >= ANIM_STEPS:
            self._color = target
            try:
                self.configure(fg_color=target)
            except tkinter.TclError:
                pass
            return
        color = blend_color(self._color, target, (step + 1) / ANIM_STEPS)
        try:
            self.configure(fg_color=color)
        except tkinter.TclError:
            return
        self._animation_id = self.after(
            ANIM_MS, lambda: self._animate_to(target, step + 1)
        )

    def destroy(self) -> None:
        self._cancel_animation()
        try:
            super().destroy()
        except Exception:
            pass


class SelectRow(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        index: int,
        text: str,
        selected: bool,
        app: "ASSFunUI",
        on_select,
    ):
        self.app = app
        self.index = index
        self.option_text = str(text)
        self.selected = bool(selected)
        self.on_select = on_select
        super().__init__(
            master,
            fg_color=self._row_bg_color(False),
            border_color=THEME["accent"] if self.selected else THEME["border_soft"],
            border_width=1,
            corner_radius=14,
        )
        self.grid_columnconfigure(0, weight=1)
        self.text_label = ctk.CTkLabel(
            self,
            text=self.option_text,
            text_color=THEME["text"],
            fg_color="transparent",
            font=app.font_small,
            anchor=ctk.W,
            justify="left",
            wraplength=590,
        )
        self.text_label.grid(row=0, column=0, padx=(16, 12), pady=13, sticky="ew")
        self._indicator_image_ref = None
        self.indicator = tkinter.Label(
            self,
            bd=0,
            highlightthickness=0,
            bg=self._row_bg_color(False),
            cursor="hand2",
        )
        self.indicator.grid(row=0, column=1, padx=(0, 16), pady=11, sticky="e")
        self._draw_indicator()
        self._bind_recursive(self)

    def _row_bg_color(self, hover: bool) -> str:
        if self.selected:
            return THEME["accent_soft"]
        return THEME["panel_3"] if hover else THEME["panel_2"]

    def _make_indicator_image(self) -> Image.Image:
        scale, size = 4, 24
        image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        pad = 2 * scale
        box = (pad, pad, size * scale - pad, size * scale - pad)
        if self.selected:
            draw.ellipse(
                box, fill=THEME["accent"], outline=THEME["accent"], width=scale
            )
            points = [
                (7 * scale, 12 * scale),
                (10 * scale, 15 * scale),
                (17 * scale, 8 * scale),
            ]
            draw.line(points, fill="#FFFFFF", width=2 * scale, joint="curve")
        else:
            draw.ellipse(box, fill=(0, 0, 0, 0), outline=THEME["border"], width=scale)
        resample_filter = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        return image.resize((size, size), resample_filter)

    def _draw_indicator(self) -> None:
        self._indicator_image_ref = ImageTk.PhotoImage(self._make_indicator_image())
        self.indicator.configure(
            image=self._indicator_image_ref, bg=self._row_bg_color(False)
        )

    def _bind_recursive(self, widget) -> None:
        try:
            widget.bind("<Button-1>", self._click, add="+")
            widget.bind("<Enter>", self._enter, add="+")
            widget.bind("<Leave>", self._leave, add="+")
            widget.configure(cursor="hand2")
        except Exception:
            pass
        for child in widget.winfo_children():
            self._bind_recursive(child)

    def _click(self, _event=None) -> str:
        self.on_select(self.index)
        return "break"

    def _enter(self, _event=None) -> None:
        self._set_visual_bg(True)

    def _leave(self, _event=None) -> None:
        self._set_visual_bg(False)

    def _set_visual_bg(self, hover: bool) -> None:
        bg = self._row_bg_color(hover)
        self.configure(fg_color=bg)
        try:
            self.indicator.configure(bg=bg)
        except Exception:
            pass

    def set_selected(self, selected: bool) -> None:
        self.selected = bool(selected)
        self.configure(
            fg_color=self._row_bg_color(False),
            border_color=THEME["accent"] if self.selected else THEME["border_soft"],
        )
        self.text_label.configure(font=self.app.font_small)
        self._draw_indicator()


class SelectDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master: "ASSFunUI",
        title: str,
        options: Sequence[str],
        default: str | None = None,
    ) -> None:
        super().__init__(master, fg_color=THEME["bg"])
        self.withdraw()
        self.master_ui = master
        self.title(title)
        self.options = list(map(str, options))
        if not self.options:
            raise ValueError("SelectDialog 至少需要一个选项。")
        self.selected_index = (
            self.options.index(default) if default in self.options else 0
        )
        self.result: str | None = None
        self.rows: list[SelectRow] = []
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._create_widgets()
        width, max_height = parse_size(SELECT_WINDOW_SIZE)
        show_centered_toplevel(
            self,
            width,
            min(max_height, 172 + max(len(self.options), 1) * 58),
            focus=True,
            resizable=True,
        )
        self.grab_set()

    def _create_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=22, pady=(22, 10), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text=self.title(),
            text_color=THEME["text"],
            fg_color="transparent",
            font=self.master_ui.font_mid,
            anchor=ctk.W,
        ).grid(row=0, column=0, sticky="ew")
        list_frame = SmoothScrollableFrame(
            self,
            fg_color="transparent",
            border_width=0,
            corner_radius=0,
            scrollbar_button_color=THEME["panel_3"],
            scrollbar_button_hover_color=THEME["border"],
            scroll_speed=0.30,
            decay=0.70,
        )
        list_frame.grid(row=1, column=0, padx=22, pady=(0, 12), sticky="nsew")
        list_frame.grid_columnconfigure(0, weight=1)
        for index, label in enumerate(self.options):
            row = SelectRow(
                list_frame,
                index=index,
                text=label,
                selected=index == self.selected_index,
                app=self.master_ui,
                on_select=self.select_index,
            )
            row.grid(
                row=index,
                column=0,
                padx=0,
                pady=(0 if index == 0 else 8, 8),
                sticky="ew",
            )
            self.rows.append(row)
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, padx=22, pady=(0, 22), sticky="ew")
        actions.grid_columnconfigure((0, 1), weight=1)
        ui = UIFactory(THEME, self.master_ui.font)
        ui.button(actions, "取消", self._cancel, variant="secondary", height=36).grid(
            row=0, column=0, padx=(0, 7), sticky="ew"
        )
        ui.button(actions, "确认", self._ok, height=36).grid(
            row=0, column=1, padx=(7, 0), sticky="ew"
        )

    def select_index(self, index: int) -> None:
        if index < 0 or index >= len(self.options):
            return
        self.selected_index = index
        for row_index, row in enumerate(self.rows):
            row.set_selected(row_index == self.selected_index)

    def _ok(self) -> None:
        self.result = self.options[self.selected_index]
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()

    def show(self) -> str | None:
        self.wait_window()
        return self.result


# 图片选择窗口
class CoverSelectDialog(ctk.CTkToplevel):
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

    def __init__(self, master: "ASSFunUI") -> None:
        super().__init__(master, fg_color=THEME["bg"])
        self.withdraw()
        self.master_ui = master
        self.result: Path | None = None
        self.title("选择封面图片")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._create_widgets()
        width, height = parse_size(COVER_WINDOW_SIZE)
        show_centered_toplevel(self, width, height, focus=True, resizable=False)
        self.grab_set()

    def _create_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.drop_area = ctk.CTkFrame(
            self,
            fg_color=THEME["panel"],
            border_color=THEME["border_soft"],
            border_width=1,
            corner_radius=16,
        )
        self.drop_area.grid(row=0, column=0, sticky="nsew", padx=18, pady=(18, 10))
        self.drop_area.grid_columnconfigure(0, weight=1)
        self.drop_area.grid_rowconfigure(0, weight=1)
        self.drop_label = ctk.CTkLabel(
            self.drop_area,
            text="拖入 JPG / PNG / WEBP 图片",
            text_color=TEXT_MUTED,
            fg_color="transparent",
            font=self.master_ui.font,
            justify="center",
        )
        self.drop_label.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        for widget in (self, self.drop_area, self.drop_label):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._drop)
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 18))
        actions.grid_columnconfigure(0, weight=1)
        UIFactory(THEME, self.master_ui.font).button(
            actions, "选择图片", self._choose, height=36
        ).grid(row=0, column=0, sticky="ew")

    def _choose(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="选择封面图片",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.webp"), ("全部文件", "*.*")],
        )
        if selected:
            self._accept(Path(selected))

    def _drop(self, event: Any) -> None:
        paths = parse_drop_paths(getattr(event, "data", ""))
        for path in paths:
            if self._is_valid_image(path):
                self._accept(path)
                return
        self.drop_label.configure(
            text="未识别到支持的图片文件，请拖入 JPG / PNG / WEBP。", text_color=DANGER
        )

    def _is_valid_image(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in self.IMAGE_EXTS

    def _accept(self, path: Path) -> None:
        if not self._is_valid_image(path):
            self.drop_label.configure(
                text=f"不支持的封面文件：{path}", text_color=DANGER
            )
            return
        self.result = path
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()

    def show(self) -> Path | None:
        self.wait_window()
        return self.result


# 设置项卡片
class ConfigOption(ctk.CTkFrame):
    def __init__(self, master: "ConfigWindow", key: str, label: str) -> None:
        super().__init__(
            master.content,
            fg_color=THEME["panel"],
            corner_radius=13,
            border_width=1,
            border_color=THEME["border_soft"],
        )
        self.window = master
        self.key = key
        self._save_after: str | None = None
        self._destroyed = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        value = master.master_ui.getconfig(key)
        self.value_type = type(value)
        title, description = self._split_label(label)

        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, padx=(14, 10), pady=(10, 6), sticky="ew")
        title_frame.grid_columnconfigure(0, weight=1)
        self.labelbox = ctk.CTkLabel(
            title_frame,
            text=title,
            text_color=THEME["text"],
            fg_color="transparent",
            font=self.window.master_ui.font_option_title,
            anchor=ctk.W,
            justify="left",
            wraplength=590,
        )
        self.labelbox.grid(row=0, column=0, sticky="ew")

        if description:
            self.description_label = ctk.CTkLabel(
                title_frame,
                text=description,
                text_color=THEME["muted_2"],
                fg_color="transparent",
                font=self.window.master_ui.font_tiny,
                anchor=ctk.W,
                justify="left",
                wraplength=590,
            )
            self.description_label.grid(row=1, column=0, pady=(2, 0), sticky="ew")

        self.key_button = ctk.CTkButton(
            self,
            text=key,
            height=26,
            width=SETTING_KEY_WIDTH,
            fg_color=THEME["panel_2"],
            hover_color=THEME["panel_3"],
            text_color=THEME["muted"],
            border_width=0,
            corner_radius=999,
            font=self.window.master_ui.font_tiny,
            command=lambda: self.window.copy_text(key),
        )
        self.key_button.grid(row=0, column=1, padx=(0, 14), pady=(10, 6), sticky="ne")
        attach_interactive_feedback(self.key_button, "点击复制键名")

        if isinstance(value, bool):
            self.var = ctk.StringVar(value="True" if value else "False")
            self.switch_slot = ctk.CTkFrame(
                self,
                fg_color="transparent",
                width=SETTING_SWITCH_SLOT_WIDTH,
                height=SETTING_CONTROL_HEIGHT,
            )
            self.switch_slot.grid(
                row=1, column=1, padx=(0, 14), pady=(0, 12), sticky="e"
            )
            self.switch_slot.grid_columnconfigure(0, weight=1)
            self.switch_slot.grid_rowconfigure(0, weight=1)
            self.switch_slot.grid_propagate(False)
            self.input = ToggleSwitch(
                self.switch_slot,
                variable=self.var,
                command=self._bool_changed,
                onvalue="True",
                offvalue="False",
                colors=THEME,
                width=56,
                height=28,
                knob_size=24,
                tooltip="点击切换",
            )
            self.input.grid(row=0, column=0)
        elif isinstance(value, str) and "\n" in value:
            self.input = ctk.CTkTextbox(
                self,
                height=min(200, max(92, 21 * (value.count("\n") + 3))),
                fg_color=THEME["input"],
                text_color=THEME["text"],
                border_width=1,
                border_color=THEME["border"],
                corner_radius=10,
                font=self.window.master_ui.font_small,
                wrap="none",
                border_spacing=6,
            )
            self.input.insert("1.0", value)
            self.input.grid(row=1, column=0, padx=(14, 10), pady=(0, 12), sticky="ew")
            self.input.bind("<KeyRelease>", self._schedule_save, add="+")
            smooth_scroll_bind(self.input, self.input, units=2)
            self._make_side_button(
                "复制", lambda: self.window.copy_text(self._value()), "点击复制当前值"
            )
        else:
            self.input = ctk.CTkEntry(
                self,
                text_color=THEME["text"],
                fg_color=THEME["input"],
                border_color=THEME["border"],
                corner_radius=10,
                font=self.window.master_ui.font_small,
                height=34,
            )
            self.input.insert(0, str(value))
            self.input.grid(row=1, column=0, padx=(14, 10), pady=(0, 12), sticky="ew")
            self.input.bind("<KeyRelease>", self._schedule_save, add="+")
            if (
                key.endswith("path")
                or key.endswith("dir")
                or key in {"mkvmerge_path", "aegisub_cli_path", "mkvoutputdir"}
            ):
                self._make_side_button("浏览", self._browse, "选择路径")
            else:
                self._make_side_button(
                    "复制",
                    lambda: self.window.copy_text(self._value()),
                    "点击复制当前值",
                )

    @staticmethod
    def _split_label(label: str) -> tuple[str, str]:
        parts = str(label).split("\n", 1)
        title = parts[0].strip()
        description = parts[1].strip() if len(parts) > 1 else ""
        return title, description

    def _make_side_button(
        self, text: str, command: Callable[[], None], tooltip: str
    ) -> None:
        self.side_button = ctk.CTkButton(
            self,
            text=text,
            width=SETTING_SIDE_CONTROL_WIDTH,
            height=SETTING_CONTROL_HEIGHT,
            fg_color=THEME["panel_3"],
            hover_color=THEME["border"],
            text_color=THEME["text"],
            border_color=THEME["border"],
            border_width=1,
            corner_radius=999,
            font=self.window.master_ui.font_tiny,
            command=command,
        )
        self.side_button.grid(row=1, column=1, padx=(0, 14), pady=(0, 12), sticky="e")
        attach_interactive_feedback(self.side_button, tooltip)

    def _value(self) -> bool | str:
        if isinstance(getattr(self, "input", None), ToggleSwitch):
            return self.var.get() == "True"
        if isinstance(self.input, ctk.CTkTextbox):
            return self.input.get("1.0", "end-1c")
        return self.input.get()

    def _bool_changed(self) -> None:
        self._save()

    def _schedule_save(self, _event: Any = None) -> None:
        if self._destroyed:
            return
        if self._save_after:
            self.after_cancel(self._save_after)
        self._save_after = self.after(350, self._save)

    def _save(self) -> None:
        self._save_after = None
        if self._destroyed:
            return
        self.window.master_ui.config[self.key] = self._value()
        self.window.master_ui.saveconfig()

    def _browse(self) -> None:
        current = str(self._value()).strip()
        if self.key.endswith("dir") or self.key == "mkvoutputdir":
            initial = (
                current
                if current and Path(current).exists()
                else str(self.window.master_ui.folder)
            )
            selected = filedialog.askdirectory(
                parent=self.window, title="选择文件夹", initialdir=initial
            )
        else:
            parent = Path(current).parent if current else self.window.master_ui.folder
            initial = (
                str(parent) if parent.exists() else str(self.window.master_ui.folder)
            )
            selected = filedialog.askopenfilename(
                parent=self.window, title="选择文件", initialdir=initial
            )
        if not selected:
            return
        if isinstance(self.input, ctk.CTkEntry):
            self.input.delete(0, "end")
            self.input.insert(0, selected)
            self._save()

    def destroy(self) -> None:
        self._destroyed = True
        if self._save_after:
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
            self._save_after = None
        super().destroy()


# 构建延迟显示窗口
class ConfigWindow(ctk.CTkToplevel):
    GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "视频混流",
            [
                ("mkvmerge_path", "mkvmerge 路径（通常在 MKVToolNix 安装目录内）"),
                (
                    "filename_ext",
                    "混流输出文件的视频属性标识\n支持 {height}、{vcodec}、{bitdepth}、{acodec}",
                ),
                ("mkvoutputdir", "混流输出路径\n留空则优先使用输入文件所在路径"),
                ("cover", "混流时选择并封入封面图片"),
                ("videotrack_lang", "视频轨语言"),
                ("videotrack_name", "视频轨名称"),
                ("audiotrack_lang", "音频轨语言"),
                ("audiotrack_name", "音频轨名称"),
            ],
        ),
        (
            "字幕轨道",
            [
                ("asschsjpntrack_symbol", "简中/简日字幕文件名标识"),
                ("asschsjpntrack_name", "简中/简日字幕轨名称"),
                ("asschtjpntrack_symbol", "繁中/繁日字幕文件名标识"),
                ("asschtjpntrack_name", "繁中/繁日字幕轨名称"),
                ("assjpntrack_symbol", "日文字幕文件名标识"),
                ("assjpntrack_name", "日文字幕轨名称"),
                ("assengtrack_symbol", "英文字幕文件名标识"),
                ("assengtrack_name", "英文字幕轨名称"),
                ("asstrackname_separator", "多样式字幕轨名称分隔符"),
                ("assmultistyle_defaulttrack", "多样式字幕默认轨样式名"),
            ],
        ),
        (
            "字幕清理",
            [
                (
                    "fontsubset_warning",
                    "子集化字体警告前缀\n如果不写警告很容易被用户误安装",
                ),
                ("clean_scriptinfo", "清理 Script Info"),
                ("scriptinfo", "Script Info 替换内容\n支持 {LANGUAGE}"),
                ("scriptinfo_language", "Script Info 语言替换值\n格式：简中,繁中,日语"),
                ("clean_garbage", "清除 Aegisub Project Garbage"),
                ("clean_furigana", "清除未使用的 furigana 样式"),
                ("clean_space", "清除行末空格"),
                ("clean_all_space", "清除所有重复空格"),
                (
                    "unicode_to_utf8",
                    "Unicode 码点转 UTF-8 字节\n例如将 \\u{3000} 转为 \\xE3\\x80\\x80",
                ),
                ("optional_styles", "额外保留样式\n清理样式时保留，用英文逗号分隔"),
            ],
        ),
        (
            "字幕生成",
            [
                ("generate_cht", "生成繁中字幕"),
                (
                    "generate_cht_styles",
                    "繁化样式范围\n只对这些样式进行繁化；留空则全部繁化",
                ),
                (
                    "generate_cht_keep_comment",
                    "保留非 karaoke 注释行，不进行繁化\n例如你想保留注释为简中以保持注释准确性",
                ),
                ("zhconvert_json", "繁化姬请求 JSON\n使用 {ASSCONTENT} 表示字幕内容"),
                ("generate_jpn", "生成日语字幕"),
                ("jpn_convert", "生成日语字幕时删除中文行"),
                ("jpn_convert_styles_to_delete", "删除的中文样式\n生成日语字幕时使用"),
                ("generate_multistyle", "生成多样式字幕"),
                ("generate_karaoke", "对生成字幕应用卡拉 OK 模板"),
                ("aegisub_cli_path", "aegisub-cli 路径"),
                (
                    "aegisub_cli_loglevel",
                    "aegisub-cli loglevel\n0 exception / 1 assert / 2 warning / 3 info / 4 debug",
                ),
                ("generate_language", "语言标识\n格式：简中,繁中,日语"),
                ("proxy", "HTTP 代理端口\n0 表示禁用"),
            ],
        ),
    ]

    def __init__(self, master: "ASSFunUI") -> None:
        self.master_ui = master
        super().__init__(master, fg_color=THEME["bg"])
        self.withdraw()
        self.title("ASSFun 设置 - KyokuSai")
        self.configure(fg_color=THEME["bg"])
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._create_widgets()
        width, height = parse_size(SETTINGS_WINDOW_SIZE)
        show_centered_toplevel(self, width, height, focus=True, resizable=True)

    def _create_widgets(self) -> None:
        self.content = SmoothScrollableFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=THEME["panel_3"],
            scrollbar_button_hover_color=THEME["border"],
            scroll_speed=0.20,
            decay=0.66,
        )
        self.content.grid(row=0, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self._render_content()

    def _render_content(self) -> None:
        row = 0
        header = ctk.CTkFrame(
            self.content,
            fg_color=THEME["panel"],
            border_color=THEME["border_soft"],
            border_width=1,
            corner_radius=14,
        )
        header.grid(row=row, column=0, padx=16, pady=(14, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="设置",
            text_color=THEME["text"],
            fg_color="transparent",
            font=self.master_ui.font_mid,
            anchor=ctk.W,
        ).grid(row=0, column=0, padx=16, pady=(13, 2), sticky="ew")
        ctk.CTkLabel(
            header,
            text="修改选项会自动保存。",
            text_color=THEME["muted"],
            fg_color="transparent",
            font=self.master_ui.font_small,
            anchor=ctk.W,
        ).grid(row=1, column=0, padx=16, pady=(0, 10), sticky="ew")
        row += 1

        for group, items in self.GROUPS:
            ctk.CTkLabel(
                self.content,
                text=group,
                text_color=THEME["text"],
                fg_color="transparent",
                font=self.master_ui.font_mid,
                anchor=ctk.W,
            ).grid(row=row, column=0, sticky="w", padx=22, pady=(13, 5))
            row += 1
            for key, label in items:
                if key not in self.master_ui.config:
                    self.master_ui.getconfig(key)
                option = ConfigOption(self, key, label)
                option.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 8))
                row += 1

    def copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(str(text))

    def destroy(self) -> None:
        master = getattr(self, "master", None)
        try:
            super().destroy()
        finally:
            if master is not None and getattr(master, "configwindow", None) is self:
                master.configwindow = None
            self.master_ui._apply_proxy()


# ASS 文本处理器
# 负责内容清理、繁化、日语版本、多样式与应用卡拉 OK 模板
class ASSGenerate:
    def __init__(self, app: "ASSFunUI", process_manager: ProcessManager) -> None:
        self.app = app
        self.process_manager = process_manager
        self.assoriginal_filename = ""
        self.asseng_filename = ""
        self.assoriginal = ""
        self.assoriginal_cht = ""
        self.assoriginal_jpn = ""
        self.assengoriginal = ""
        self.asschss: list[str] = []
        self.asschts: list[str] = []
        self.assjpns: list[str] = []
        self.results: list[Path] = []

    def readfile(self, assfile: Path) -> None:
        self.assoriginal_filename = assfile.name
        self.assoriginal = read_text(assfile)

    def readengfile(self, assfile: Path) -> None:
        self.asseng_filename = assfile.name
        self.assengoriginal = read_text(assfile)

    def clean_scriptinfo(self, content: str, language: str = "") -> str:
        comment_match = re.search(
            r"^(Comment\: Processed by 繁化姬.+)$", content, flags=re.MULTILINE
        )
        zh_comment = None
        if comment_match:
            zh_comment = re.sub(r"@[^\|]+", "", comment_match.group(1))
        content = re.sub(
            r"^Comment\: Processed by 繁化姬.*\n", "", content, flags=re.MULTILINE
        )
        if not self.app.getconfig("clean_scriptinfo"):
            self.app.log("跳过 Script Info 清理")
            if zh_comment:
                content = re.sub(
                    r"\[Script Info\]", f"[Script Info]\n{zh_comment}", content, count=1
                )
            return content

        scriptinfo = self.app.getconfig("scriptinfo")
        if "{LANGUAGE}" in scriptinfo:
            lang_values = split_csv(self.app.getconfig("scriptinfo_language"))
            lang_value = language
            if "CHS" in language.upper() and len(lang_values) >= 1:
                lang_value = lang_values[0]
            elif "CHT" in language.upper() and len(lang_values) >= 2:
                lang_value = lang_values[1]
            elif "JPN" in language.upper() and len(lang_values) >= 3:
                lang_value = lang_values[2]
            scriptinfo = scriptinfo.replace("{LANGUAGE}", lang_value)
        if re.search(r"\[Script Info\]", content, flags=re.IGNORECASE):
            replacement = f"{scriptinfo}\n\n"
            content = re.sub(
                r"\[Script Info\][\s\S]*?\n(?=\[)",
                lambda _m: replacement,
                content,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            content = f"{scriptinfo}\n\n{content}"
        if zh_comment:
            content = re.sub(
                r"\[Script Info\]", f"[Script Info]\n{zh_comment}", content, count=1
            )
        return content

    def clean_garbage(self, content: str) -> str:
        if not self.app.getconfig("clean_garbage"):
            self.app.log("跳过 Aegisub Project Garbage 清理")
            return content
        return re.sub(
            r"\[Aegisub Project Garbage\][\s\S]*?(?=\n\[|\Z)",
            "",
            content,
            flags=re.IGNORECASE,
        )

    def clean_furigana(self, content: str) -> str:
        if not self.app.getconfig("clean_furigana"):
            self.app.log("跳过 furigana 清理")
            return content
        styles = re.findall(
            r"^Style:\s*([^,]+?-furigana),", content, flags=re.MULTILINE
        )
        for style in styles:
            used = (
                re.search(
                    rf"^(Dialogue|Comment):.*,{re.escape(style)},",
                    content,
                    flags=re.MULTILINE,
                )
                is not None
            )
            if not used:
                content = re.sub(
                    rf"^Style:\s*{re.escape(style)},.*\n?",
                    "",
                    content,
                    flags=re.MULTILINE,
                )
        return content

    def clean_space(self, content: str) -> str:
        if not self.app.getconfig("clean_space"):
            self.app.log("跳过空格清理")
            return content
        content = re.sub(r" +$", "", content, flags=re.MULTILINE)
        if self.app.getconfig("clean_all_space"):
            content = re.sub(r" {2,}", " ", content)
        return content

    def unicode_to_utf8(self, content: str) -> str:
        if not self.app.getconfig("unicode_to_utf8"):
            self.app.log("跳过 Unicode 码点转换")
            return content

        def repl(match: re.Match[str]) -> str:
            code = int(match.group(1), 16)
            return "".join(f"\\\\x{b:02X}" for b in chr(code).encode("utf-8"))

        return re.sub(r"\\u\{([0-9a-fA-F]+)\}", repl, content)

    def getstyle(self, content: str) -> str | None:
        styles_match = re.search(
            r"^\[V4\+ Styles\]\n([\s\S]*?)(?=\n\[|\Z)", content, flags=re.MULTILINE
        )
        if not styles_match:
            return None
        stylestring = "[V4+ Styles]\n" + styles_match.group(1)
        assstyles = self.app.get_assstyles_interactive()
        for lang_styles in assstyles.values():
            if not isinstance(lang_styles, dict):
                continue
            for style, style_text in lang_styles.items():
                parts = [x for x in str(style_text).split("\n") if x.strip()]
                if parts and all(x in stylestring for x in parts):
                    return style
        return None

    def setstyle(self, content: str, style: str, lang: str = "CHS_JPN") -> str:
        lang_key = "CHS_JPN"
        if "CHT" in lang.upper():
            lang_key = "CHT_JPN"
        elif "JPN" in lang.upper():
            lang_key = "JPN"
        assstyles = self.app.get_assstyles_interactive()
        if lang_key not in assstyles or style not in assstyles[lang_key]:
            raise FatalProcessError(f"※样式表中找不到 {lang_key}/{style}")
        stylestring = "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        stylestring += assstyles[lang_key][style]
        for optional_style in split_csv(self.app.getconfig("optional_styles")):
            found = re.search(
                rf"^(Style:\s*{re.escape(optional_style)},.+?)$",
                content,
                flags=re.MULTILINE,
            )
            if found:
                stylestring += "\n" + found.group(1)
        stylestring += "\n\n"
        return re.sub(
            r"^\[V4\+ Styles\][\s\S]*?\n(?=\[)",
            lambda _m: stylestring,
            content,
            count=1,
            flags=re.MULTILINE,
        )

    def normalize_ass(self, content: str, language: str) -> str:
        content = self.clean_scriptinfo(content, language)
        content = self.clean_garbage(content)
        content = self.clean_furigana(content)
        content = self.clean_space(content)
        content = self.unicode_to_utf8(content)
        return content

    def chsconfirm(self) -> None:
        self.asschss.append(self.assoriginal)

    def zhconvert(self) -> None:
        if not self.app.getconfig("generate_cht"):
            self.app.log("跳过繁化")
            return
        self.app.log("请求繁化姬 API")
        try:
            json_data = json.loads(self.app.getconfig("zhconvert_json"))
        except json.JSONDecodeError as exc:
            raise FatalProcessError(f"※繁化姬 JSON 设置无法解析：{exc}") from exc
        for key, value in list(json_data.items()):
            if isinstance(value, str) and "{ASSCONTENT}" in value:
                json_data[key] = value.replace("{ASSCONTENT}", self.assoriginal)
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "origin": "https://zhconvert.org",
            "referer": "https://zhconvert.org/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        }
        try:
            response = requests.post(
                "https://api.zhconvert.org/convert",
                headers=headers,
                json=json_data,
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            self.assoriginal_cht = payload["data"]["text"]
        except Exception as exc:
            raise RuntimeError(f"繁化姬请求失败：{exc}") from exc

        self.assoriginal_cht = self.clean_scriptinfo(self.assoriginal_cht, "CHT_JPN")
        styles_to_convert = split_csv(self.app.getconfig("generate_cht_styles"))
        keep_comment = bool(self.app.getconfig("generate_cht_keep_comment"))

        if styles_to_convert or keep_comment:
            original_events = re.search(r"\[Events\]\n([\s\S]*)", self.assoriginal)
            converted_events = re.search(r"\[Events\]\n([\s\S]*)", self.assoriginal_cht)
            if original_events and converted_events:
                chs_lines = [
                    x for x in original_events.group(1).splitlines() if x.strip()
                ]
                cht_lines = [
                    x for x in converted_events.group(1).splitlines() if x.strip()
                ]
                if len(chs_lines) == len(cht_lines):
                    fmt_match = re.search(
                        r"^(Format:.+)$", "\n".join(chs_lines), flags=re.MULTILINE
                    )
                    fmt = fmt_match.group(1) if fmt_match else ""
                    for i, chs_line in enumerate(chs_lines):
                        if not chs_line.startswith(("Dialogue:", "Comment:")):
                            continue
                        if styles_to_convert and fmt:
                            style = self.app.get_assformat_by_key(
                                fmt, chs_line, "Style"
                            )
                            if style not in styles_to_convert:
                                cht_lines[i] = chs_line
                        if keep_comment and chs_line.startswith("Comment:") and fmt:
                            effect = self.app.get_assformat_by_key(
                                fmt, chs_line, "Effect"
                            ).lower()
                            if effect != "karaoke":
                                cht_lines[i] = chs_line
                    events_text = "[Events]\n" + "\n".join(cht_lines) + "\n"
                    self.assoriginal_cht = re.sub(
                        r"\[Events\]\n[\s\S]*",
                        lambda _m: events_text,
                        self.assoriginal_cht,
                    )
                else:
                    self.app.log(
                        f"繁化前后行数不一致，跳过逐行保护：原始 {len(chs_lines)} 行，繁化 {len(cht_lines)} 行"
                    )
        self.asschts.append(self.assoriginal_cht)

    def jpconvert(self) -> None:
        if not self.app.getconfig("generate_jpn"):
            self.app.log("跳过日文字幕生成")
            return
        self.assoriginal_jpn = self.clean_scriptinfo(self.assoriginal, "JPN")
        if self.app.getconfig("jpn_convert"):
            for style in split_csv(self.app.getconfig("jpn_convert_styles_to_delete")):
                self.assoriginal_jpn = re.sub(
                    rf"^Style:\s*{re.escape(style)},.*\n?",
                    "",
                    self.assoriginal_jpn,
                    flags=re.MULTILINE,
                )
                self.assoriginal_jpn = re.sub(
                    rf"^Style:\s*{re.escape(style)}-furigana,.*\n?",
                    "",
                    self.assoriginal_jpn,
                    flags=re.MULTILINE,
                )
                self.assoriginal_jpn = re.sub(
                    rf"^(Dialogue|Comment):.*,{re.escape(style)},.*\n?",
                    "",
                    self.assoriginal_jpn,
                    flags=re.MULTILINE,
                )
        self.assjpns.append(self.assoriginal_jpn)

    def generate_multistyle(self) -> None:
        if not self.app.getconfig("generate_multistyle"):
            self.app.log("跳过多样式生成")
            return
        assstyles = self.app.get_assstyles_interactive()
        groups = [
            ("CHS_JPN", self.asschss),
            ("CHT_JPN", self.asschts),
            ("JPN", self.assjpns),
        ]
        for lang, collection in groups:
            if not collection:
                continue
            base = collection[0]
            current = self.getstyle(base)
            for style in assstyles.get(lang, {}):
                if style == current:
                    continue
                collection.append(self.setstyle(base, style, lang))

    def generate_karaoke(self, work_dir: Path, mkv_files: Sequence[Path]) -> None:
        if not self.app.getconfig("generate_karaoke"):
            self.app.log("跳过卡拉 OK 模板化")
            return
        aegisub_cli = Path(self.app.getconfig("aegisub_cli_path"))
        if not aegisub_cli.exists():
            raise FileNotFoundError(f"aegisub-cli 不存在：{aegisub_cli}")
        ass_dir = work_dir / "ass"
        if ass_dir.exists():
            shutil.rmtree(ass_dir)
        ensure_dir(ass_dir)

        tmp_mkv: Path | None = None
        if mkv_files:
            tmp_mkv = ass_dir / f".{random_token()}.mkv"
            shutil.copy2(mkv_files[0], tmp_mkv)
        languages = split_csv(self.app.getconfig("generate_language"))
        script_langs = split_csv(self.app.getconfig("scriptinfo_language"))
        while len(languages) < 3:
            languages.append(languages[-1] if languages else "LANG")
        while len(script_langs) < 3:
            script_langs.append(script_langs[-1] if script_langs else "")

        try:
            for index, collection in enumerate(
                [self.asschss, self.asschts, self.assjpns]
            ):
                for ass in collection:
                    filename = self._generated_filename(ass, languages, index)
                    self.app.log(f"应用卡拉 OK 模板：{filename}")
                    tmp_base = random_token()
                    src = ass_dir / f".{tmp_base}.ass"
                    out = ass_dir / f".{tmp_base}.out.ass"
                    write_text(src, ass)
                    cmd: list[str | Path] = [aegisub_cli]
                    if tmp_mkv:
                        cmd += ["--video", tmp_mkv]
                    cmd += [
                        "--automation",
                        "kara-templater.lua",
                        "--loglevel",
                        str(self.app.getconfig("aegisub_cli_loglevel")),
                        src,
                        out,
                        "Apply karaoke template",
                    ]
                    code = self.process_manager.run_stream(cmd)
                    if code != 0:
                        raise RuntimeError(f"aegisub-cli 执行失败，ExitCode={code}")
                    self.clean_karaoke(out, ass_dir / filename, script_langs[index])
                    src.unlink(missing_ok=True)
                    out.unlink(missing_ok=True)
                    self.results.append(ass_dir / filename)
        finally:
            if tmp_mkv:
                tmp_mkv.unlink(missing_ok=True)

    def clean_karaoke(
        self, input_path: Path, output_path: Path, script_info_language: str
    ) -> None:
        content = read_text(input_path)
        content = self.clean_scriptinfo(content, script_info_language)
        content = self.clean_garbage(content)
        content = self.clean_furigana(content)
        write_text(output_path, content)

    def savefiles(self, work_dir: Path) -> None:
        ass_dir = ensure_dir(work_dir / "ass")
        if not self.app.getconfig("generate_karaoke"):
            for old in ass_dir.glob("*"):
                if old.is_file():
                    old.unlink(missing_ok=True)
        if self.assengoriginal:
            filename = self.asseng_filename or "English.ass"
            write_text(ass_dir / filename, self.assengoriginal)
            self.results.append(ass_dir / filename)
        if self.app.getconfig("generate_karaoke"):
            return
        languages = split_csv(self.app.getconfig("generate_language"))
        while len(languages) < 3:
            languages.append(languages[-1] if languages else "LANG")
        for index, collection in enumerate([self.asschss, self.asschts, self.assjpns]):
            for ass in collection:
                filename = self._generated_filename(ass, languages, index)
                write_text(ass_dir / filename, ass)
                self.results.append(ass_dir / filename)

    def _generated_filename(self, ass: str, languages: list[str], index: int) -> str:
        filename = self.assoriginal_filename
        if languages:
            filename = re.sub(
                re.escape(languages[0]), languages[index], filename, flags=re.IGNORECASE
            )
        original_style = self.getstyle(self.assoriginal) or ""
        current_style = self.getstyle(ass) or ""
        if original_style and current_style:
            filename = filename.replace(f".{original_style}.", f".{current_style}.")
        return sanitize_filename(filename)


# ASS 字体收集器
# 解析样式、正文、\fn覆写，合并实际用字
class ASSFont:
    def __init__(self, get_assformat_by_key: Callable[[str, str, str], str]) -> None:
        self.styles: dict[str, str] = {}
        self.dialogues: list[dict[str, str]] = []
        self.fonts: dict[str, str] = {}
        self.filecontent = ""
        self.get_assformat_by_key = get_assformat_by_key

    def readfile(self, assfile: Path) -> None:
        self.filecontent = read_text(assfile)

    def readstyles(self) -> None:
        for style in re.findall(r"^(Style:.+)$", self.filecontent, flags=re.MULTILINE):
            match = re.search(r"^Style:\s*([^,]*),([^,]*)", style)
            if match:
                self.styles[match.group(1)] = match.group(2)

    def readdialogues(self) -> None:
        events = re.search(r"\[Events\]\n([\s\S]*)", self.filecontent)
        if not events:
            return
        fmt_match = re.search(r"^(Format:.+)$", events.group(1), flags=re.MULTILINE)
        if not fmt_match:
            return
        fmt = fmt_match.group(1)
        for line in re.findall(
            r"^(Dialogue:.+)$", self.filecontent, flags=re.MULTILINE
        ):
            self.dialogues.append(
                {
                    "style": self.get_assformat_by_key(fmt, line, "Style"),
                    "content": self.get_assformat_by_key(fmt, line, "Text"),
                }
            )

    def mergefonts(self, fonts: Mapping[str, str]) -> None:
        for font, content in fonts.items():
            self.fonts[font] = self.fonts.get(font, "") + content

    def remove_duplicates(self) -> None:
        for font, content in list(self.fonts.items()):
            content += "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            if re.search(r"[０-９]", content):
                content += "０１２３４５６７８９"
            self.fonts[font] = "".join(sorted(set(content)))

    def addfont(self, fontname: str, content: str) -> None:
        content = re.sub(r"\{[^{}]*\}", "", content)
        if not content:
            return
        fontname = fontname.lstrip("@").strip()
        if not fontname:
            return
        self.fonts[fontname] = self.fonts.get(fontname, "") + content

    @staticmethod
    def cleandraw(content: str) -> str:
        content = re.sub(r"\{[^{}]*\\p[1-9][^{}]*\}.*?(?=\{[^{}]*\\p0)", "", content)
        content = re.sub(r"\{[^{}]*\\p[1-9][^{}]*\}.*$", "", content)
        return content

    def collectfontbypart(self, default_font: str, content: str) -> None:
        content = f"{{\\fn{default_font}}}{content}"
        parts = re.findall(r"\\fn.*?(?=\\fn|$)", content)
        for part in parts:
            font_match = re.search(r"\\fn([^\\}]*)", part)
            font = font_match.group(1).strip() if font_match else default_font
            if not font:
                font = default_font
            part = re.sub(r"^[^{}]*\}", "", part)
            part = re.sub(r"\{[^{}]*$", "", part)
            self.addfont(font, part)

    def collectfont(self) -> None:
        for dialogue in self.dialogues:
            style = dialogue["style"]
            default_font = self.styles.get(style)
            if not default_font:
                continue
            content = dialogue["content"]
            if r"\p" in content:
                content = self.cleandraw(content)
            if r"\fn" in content:
                self.collectfontbypart(default_font, content)
            else:
                self.addfont(default_font, content)


# 主处理器
# 串联缓存、字幕生成、字体处理、混流
class ASSProcessor:
    def __init__(
        self,
        app: "ASSFunUI",
        process_manager: ProcessManager,
        *,
        cover: Path | None,
        selected_default_style: str | None,
    ) -> None:
        self.app = app
        self.process_manager = process_manager
        self.cover = cover
        self.selected_default_style = selected_default_style
        self.root = app.work_dir
        self.result_dir = self.root / "result"
        self.cancel_event = app.cancel_event

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise FatalProcessError("※任务已取消。")

    def run(self) -> None:
        self.app.log(f"当前处理选项：{self.app.values}")
        self.app.log(f"MKV：{self.app.mkv}")
        self.app.log(f"字幕：{self.app.files}")
        if not self.app.files:
            raise FatalProcessError("※未指定字幕文件。")

        self._prepare_cache()
        self.check_cancelled()

        files = list(self.app.files)
        if self.app.values.get("assgenerate"):
            files = self._generate_ass_files()
            self.app.files = files
        self.check_cancelled()

        ass_files, font_files = self._process_fonts(files)
        self.check_cancelled()

        if self.app.mkv:
            output = self._mux(ass_files, font_files)
            self.app.log(f"混流完成：{output}")
        else:
            self.app.log(f"未指定 MKV，已完成字幕和字体处理：{self.result_dir}")

    def _prepare_cache(self) -> None:
        if self.app.values.get("usecache"):
            self.app.log("读取字体缓存")
            self.app.getcache()
            if not self.app.cache:
                self.app.log("字体缓存为空，改为重新扫描系统字体")
                self.app.generatecache()
                self.app.savecache()
        else:
            self.app.log("重新扫描系统字体")
            self.app.generatecache()
            self.app.savecache()

    def _generate_ass_files(self) -> list[Path]:
        self.app.log("开始字幕生成")
        gen = ASSGenerate(self.app, self.process_manager)
        source = self.app.files[0]
        self.app.log(f"读取原始字幕：{source}")
        gen.readfile(source)
        gen.assoriginal = gen.normalize_ass(gen.assoriginal, "CHS_JPN")
        gen.chsconfirm()
        self.check_cancelled()
        gen.zhconvert()
        self.check_cancelled()
        gen.jpconvert()
        self.check_cancelled()
        gen.generate_multistyle()
        self.check_cancelled()
        gen.generate_karaoke(self.root, self.app.mkv)
        if self.app.eng:
            eng = self.app.eng[0]
            self.app.log(f"处理英语字幕：{eng}")
            gen.readengfile(eng)
            gen.assengoriginal = gen.normalize_ass(gen.assengoriginal, "ENG")
        gen.savefiles(self.root)
        self.app.log(f"字幕生成完成：{len(gen.results)} 个文件")
        return list(gen.results)

    def _collect_fonts(self, files: Sequence[Path]) -> dict[str, str]:
        collector = ASSFont(self.app.get_assformat_by_key)
        for file in files:
            self.check_cancelled()
            self.app.log(f"分析字幕字体：{file}")
            item = ASSFont(self.app.get_assformat_by_key)
            item.readfile(file)
            item.readstyles()
            item.readdialogues()
            item.collectfont()
            collector.mergefonts(item.fonts)
        collector.remove_duplicates()
        return collector.fonts

    def _process_fonts(self, files: Sequence[Path]) -> tuple[list[Path], list[Path]]:
        fonts = self._collect_fonts(files)
        if self.result_dir.exists():
            shutil.rmtree(self.result_dir)
        ensure_dir(self.result_dir)
        output_ass: list[Path] = []
        output_fonts: list[Path] = []

        if self.app.values.get("subset"):
            self.app.log("开始字体子集化")
            warning = self.app.getconfig("fontsubset_warning")
            replacedict: dict[str, str] = {}
            for font, chars in fonts.items():
                self.check_cancelled()
                self.app.log(f"查找字体：{font}")
                font_path, _ = self.app.getfontfile(font)
                if not font_path:
                    raise FatalProcessError(f'※"{font}" 的字体文件未能找到。')
                token = random_token()
                new_font_name = f"{warning}{token}"
                replacedict[font] = new_font_name
                out_path = self.result_dir / f"{sanitize_filename(font)} - {token}.ttf"
                self.app.log(f"子集化字体：{font} -> {out_path.name}")
                self.app.subset_font(
                    font, Path(font_path), chars, new_font_name, out_path
                )
                output_fonts.append(out_path)
                write_text(self.result_dir / f".{sanitize_filename(font)}.txt", chars)
            for file in files:
                out_ass = self.result_dir / Path(file).name
                self.app.fix_subset_font_names(file, out_ass, replacedict)
                output_ass.append(out_ass)
            self.app.log(f"字体子集化完成，共 {len(fonts)} 个字体。")
        else:
            output_ass = [Path(x) for x in files]
            for font in fonts:
                self.check_cancelled()
                self.app.log(f"查找字体：{font}")
                font_path, font_file = self.app.getfontfile(font)
                if not font_path or not font_file:
                    raise FatalProcessError(f'※"{font}" 的字体文件未能找到。')
                dest = self.result_dir / sanitize_filename(font_file)
                shutil.copy2(font_path, dest)
                output_fonts.append(dest)
            self.app.log(f"字体复制完成，共 {len(fonts)} 个字体。")
        return sorted(output_ass, key=lambda x: x.name.lower()), output_fonts

    def _mux(self, ass_files: Sequence[Path], font_files: Sequence[Path]) -> Path:
        mkv = self.app.mkv[0]
        self.app.log("开始自动混流")
        mkvmerge_path = self.app.getconfig("mkvmerge_path")
        title = re.sub(r"\.mkv$", "", mkv.name, flags=re.IGNORECASE)
        vinfo = get_media_info(mkv, mkvmerge_path)
        filename_ext = replace_template(self.app.getconfig("filename_ext"), vinfo)
        output_dir = choose_output_dir(
            self.app.getconfig("mkvoutputdir"), mkv, self.root, self.app.log
        )
        output_name = f"{title} {filename_ext}.mkv"
        if title.endswith("]"):
            output_name = f"{title}{filename_ext}.mkv"
        output = output_dir / sanitize_filename(output_name)

        muxer = MkvMergeMuxer(mkvmerge_path, self.process_manager, self.app.log)
        muxer.add(title=title, output=output, ui_language="zh_CN")
        muxer.add(
            inputs=MediaInputSpec(
                mkv,
                video_rules=[
                    TrackRule(
                        0,
                        self.app.getconfig("videotrack_lang"),
                        self.app.getconfig("videotrack_name"),
                        0,
                        True,
                    )
                ],
                audio_rules=[
                    TrackRule(
                        0,
                        self.app.getconfig("audiotrack_lang"),
                        self.app.getconfig("audiotrack_name"),
                        None,
                        True,
                    )
                ],
            )
        )

        track_map = self.app.subtitle_track_map(include_eng=bool(self.app.eng))
        for ass in ass_files:
            lang, name, is_default = self._infer_subtitle_track(
                ass, track_map, ass_files
            )
            muxer.add(
                subtitles=SubtitleInputSpec(
                    ass, rules=[TrackRule(0, lang, name, None, is_default)]
                )
            )

        prefix = self.app.getconfig("fontsubset_warning")
        if prefix and not prefix.endswith((" ", "-")):
            prefix += "-"
        for font in font_files:
            muxer.add(
                fonts=FontAttachmentSpec(
                    font,
                    (
                        f"{prefix}{font.name}"
                        if self.app.values.get("subset")
                        else font.name
                    ),
                )
            )
        if self.cover:
            muxer.add(cover=CoverAttachmentSpec(self.cover, "cover", title))
        out = muxer.mux()
        if not out.exists():
            raise RuntimeError(f"mkvmerge 返回成功，但输出文件不存在：{out}")
        return out

    def _infer_subtitle_track(
        self,
        ass: Path,
        track_map: Mapping[str, tuple[str, str]],
        all_ass: Sequence[Path],
    ) -> tuple[str, str, bool]:
        basename = ass.name.lower()
        track_lang = "zh"
        track_name = ""
        track_default = False
        for symbol, (lang, name) in track_map.items():
            if symbol.lower() in basename:
                track_lang = lang
                track_name = name
                if sum(symbol.lower() in x.name.lower() for x in all_ass) < 2:
                    track_default = True
                break
        if not track_name:
            candidates = {
                "zh": ["zh", "ch", "sc", "tc"],
                "ja": ["ja", "jp", "jpn"],
                "en": ["en", "eng"],
            }
            for lang, markers in candidates.items():
                if any(k in basename for k in markers):
                    track_lang = lang
                    track_default = True
                    break
        real_name = track_name
        if self.app.values.get("assgenerate"):
            style = self._style_from_filename_or_file(ass)
            if style and real_name:
                real_name = (
                    f"{real_name}{self.app.getconfig('asstrackname_separator')}{style}"
                )
            if self.selected_default_style and style == self.selected_default_style:
                track_default = True
        else:
            track_default = True
        if track_lang == "en":
            track_default = True
        return track_lang, real_name, track_default

    def _style_from_filename_or_file(self, ass: Path) -> str:
        stem_parts = ass.stem.split(".")
        if len(stem_parts) >= 2:
            return stem_parts[-1]
        gen = ASSGenerate(self.app, self.process_manager)
        gen.readfile(ass)
        return gen.getstyle(gen.assoriginal) or ""


def random_token(length: int = 8) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choice(alphabet) for _ in range(length))


# 主界面
class ASSFunUI(DnDCTk):
    def __init__(self) -> None:
        super().__init__()
        self._exiting = False
        self._force_exit_timer: threading.Timer | None = None
        self.colors = dict(THEME)
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.cache: dict[str, list[str]] = {}
        self.config: dict[str, Any] = {}
        self.assstyles: dict[str, dict[str, str]] = {}
        self.values: dict[str, bool] = {}
        self.mkv: list[Path] = []
        self.files: list[Path] = []
        self.eng: list[Path] = []
        self.worker: threading.Thread | None = None
        self.configwindow: ConfigWindow | None = None

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.folder = app_dir()
        self.work_dir = self.folder
        self.data_dir = ensure_dir(self.folder / "data")
        self.cache_file = self.data_dir / "cache.json"
        self.config_file = self.data_dir / "config.json"
        self.process_manager = ProcessManager(self.log)

        self._init_fonts()
        self._init_window()
        self._create_widgets()
        self._init_runtime()
        self.after(80, self._drain_log_queue)

    def _init_fonts(self) -> None:
        self.font_family = configure_app_font(self, font_file=FONT_FILE)
        self.option_add("*Font", (self.font_family, 10))
        self.font_large = ctk.CTkFont(family=self.font_family, size=22, weight="bold")
        self.font_mid = ctk.CTkFont(family=self.font_family, size=16, weight="bold")
        self.font = ctk.CTkFont(family=self.font_family, size=15)
        self.font_small = ctk.CTkFont(family=self.font_family, size=13)
        self.font_option_title = ctk.CTkFont(
            family=self.font_family, size=13, weight="bold"
        )
        self.font_tiny = ctk.CTkFont(family=self.font_family, size=11)
        self.font_log = ctk.CTkFont(family=self.font_family, size=13)
        self.window_icon_image = self._load_ctk_image(ICON_FILE, (20, 20))
        self.gear_image = self._load_ctk_image(GEAR_FILE, (18, 18))

    def _load_ctk_image(self, filename: str, size: tuple[int, int]) -> ctk.CTkImage:
        path = Path(resource_path(filename))
        image = Image.open(path)
        return ctk.CTkImage(light_image=image, dark_image=image, size=size)

    def _init_window(self) -> None:
        self.title(APP_TITLE)
        w, h = parse_size(MAIN_WINDOW_SIZE)
        setup_fixed_window(self, w, h, resizable=True, center=True)
        self.configure(fg_color=THEME["bg"])
        apply_window_icon(self, default=True)
        apply_window_chrome(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

    def _create_widgets(self) -> None:
        self.mkvbox = DropBox(
            self,
            title="MKV 输入（可选）",
            hint="拖入一个 MKV 文件；不拖入则只处理字幕与字体。",
            exts=[".mkv"],
            multiple=False,
            height=54,
            on_change=self._set_mkv,
            help_text="用于最终混流，只需要字幕处理时可以留空。",
        )
        self.mkvbox.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 7))

        self.filebox = DropBox(
            self,
            title="字幕输入",
            hint="拖入 ASS 字幕文件。单个字幕可启用字幕生成，多个字幕将直接整理字体/混流。",
            exts=[".ass"],
            multiple=True,
            height=132,
            on_change=self._set_files,
            help_text="至少需要一个 ASS 文件。\n单文件时可以启用字幕生成，多文件时会自动禁用字幕生成。",
        )
        self.filebox.grid(row=1, column=0, sticky="ew", padx=16, pady=7)

        self.engbox = DropBox(
            self,
            title="英语字幕（可选）",
            hint="字幕生成时额外加入一条英文 ASS 字幕。",
            exts=[".ass"],
            multiple=False,
            height=54,
            on_change=self._set_eng,
            help_text="用于额外加入英文字幕轨。",
        )
        self.engbox.grid(row=2, column=0, sticky="ew", padx=16, pady=7)
        self.engbox.grid_remove()

        switches = ctk.CTkFrame(self, fg_color="transparent")
        switches.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 4))
        switches.grid_columnconfigure((0, 1, 2), weight=1)
        self.assgenerate_toggle = ToggleCard(
            switches,
            self,
            "assgenerate",
            "字幕生成",
            False,
            "生成简中/繁中/日文/多样式字幕，并可应用卡拉 OK 模板。",
        )
        self.subset_toggle = ToggleCard(
            switches,
            self,
            "subset",
            "子集化字体",
            True,
            "只保留字幕实际用到的字形，并改写字幕内字体名。",
        )
        self.cache_toggle = ToggleCard(
            switches,
            self,
            "usecache",
            "使用缓存",
            True,
            "使用已生成的系统字体名称缓存；关闭后会重新扫描系统字体。",
        )
        self.assgenerate_toggle.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.subset_toggle.grid(row=0, column=1, sticky="ew", padx=6)
        self.cache_toggle.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        self.logbox = ctk.CTkTextbox(
            self,
            fg_color="#151720",
            text_color=TEXT,
            scrollbar_button_color="#343846",
            scrollbar_button_hover_color="#454B5F",
            font=self.font_log,
            corner_radius=14,
            border_width=1,
            border_color=BORDER,
            wrap="word",
        )
        self.logbox.grid(row=4, column=0, sticky="nsew", padx=16, pady=(8, 10))
        self.logbox.configure(state="disabled")
        smooth_scroll_bind(self.logbox, self.logbox, units=2)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 16))
        bottom.grid_columnconfigure(1, weight=1)
        settings_kwargs: dict[str, Any] = {
            "text": "" if self.gear_image else "设置",
            "image": self.gear_image,
            "width": 38,
            "height": 38,
            "fg_color": PANEL_2,
            "hover_color": "#313545",
            "font": self.font_small,
            "command": self.openconfigwindow,
        }
        if self.gear_image is None:
            settings_kwargs.pop("image")
        self.settings_button = ctk.CTkButton(bottom, **settings_kwargs)
        self.settings_button.grid(row=0, column=0, sticky="w")
        attach_interactive_feedback(self.settings_button, "打开设置")

        self.status_label = ctk.CTkLabel(
            bottom,
            text="等待任务",
            text_color=TEXT_MUTED,
            font=self.font_small,
            anchor="w",
        )
        self.status_label.grid(row=0, column=1, sticky="ew", padx=12)
        self.start_button = ctk.CTkButton(
            bottom,
            text="开始处理",
            width=150,
            height=38,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=TEXT,
            text_color_disabled=TEXT_MUTED,
            font=self.font,
            command=self.start_request,
        )
        self.start_button.grid(row=0, column=2, sticky="e")

    def _init_runtime(self) -> None:
        self.log("-- 日志记录 --")
        self.log(f"程序目录：{self.folder}")
        self.log(f"工作目录：{self.work_dir}")
        self.log(f"数据目录：{self.data_dir}")
        sys.excepthook = self._global_exception
        self.setconfig()
        self.getcache(init=True)
        self._apply_proxy()
        self.on_mode_changed()

    def _set_mkv(self, paths: list[Path]) -> None:
        self.mkv = paths

    def _set_files(self, paths: list[Path]) -> None:
        self.files = paths
        if hasattr(self, "assgenerate_toggle"):
            if len(paths) == 1:
                self.assgenerate_toggle.set_value(True, notify=False)
            else:
                self.assgenerate_toggle.set_value(False, notify=False)
        self.on_mode_changed()

    def _set_eng(self, paths: list[Path]) -> None:
        self.eng = paths

    def on_mode_changed(self) -> None:
        required = ("assgenerate_toggle", "engbox")
        if any(not hasattr(self, name) for name in required):
            return
        if getattr(self, "_syncing_mode_state", False):
            return
        self._syncing_mode_state = True
        try:
            single_ass = len(self.files) == 1
            if single_ass:
                self.assgenerate_toggle.set_enabled(True)
            else:
                self.assgenerate_toggle.set_value(False, notify=False)
                self.assgenerate_toggle.set_enabled(False)

            if self.values.get("assgenerate") and single_ass:
                self.engbox.grid()
            else:
                self.engbox.grid_remove()
        finally:
            self._syncing_mode_state = False

    def log(self, text: str) -> None:
        text = str(text)
        if threading.current_thread() is threading.main_thread():
            self._append_log(text)
        else:
            self.log_queue.put(text)
        try:
            print(text)
        except Exception:
            pass

    def _append_log(self, text: str) -> None:
        if getattr(self, "_exiting", False):
            return
        logbox = getattr(self, "logbox", None)
        if logbox is None:
            print(text)
            return
        try:
            if not logbox.winfo_exists():
                return
            step_text = self._step_text_from_log(text)
            if step_text:
                self.set_current_step(step_text)
            logbox.configure(state="normal")
            logbox.insert("end", str(text) + "\n")
            logbox.yview("end")
            logbox.configure(state="disabled")
        except Exception:
            print(text)

    def _drain_log_queue(self) -> None:
        if getattr(self, "_exiting", False):
            return
        try:
            while True:
                try:
                    item = self.log_queue.get_nowait()
                except queue.Empty:
                    break
                self._append_log(item)
            while True:
                try:
                    action = self.ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    action()
                except Exception as exc:
                    self._append_log(normalize_exception(exc))
            if not getattr(self, "_exiting", False) and self.winfo_exists():
                self.after(80, self._drain_log_queue)
        except tkinter.TclError:
            return

    def call_ui(self, action: Callable[[], None]) -> None:
        if getattr(self, "_exiting", False):
            return
        if threading.current_thread() is threading.main_thread():
            action()
        else:
            self.ui_queue.put(action)

    def _step_text_from_log(self, text: str) -> str:
        text = str(text or "").strip()
        if not text or text.startswith("--"):
            return ""
        first_line = text.splitlines()[0].strip()
        first_line = re.sub(r"^[※\-\s]+", "", first_line)
        if not first_line:
            return ""
        if first_line.startswith(
            ("程序目录：", "工作目录：", "数据目录：", "使用代理：", "未启用代理")
        ):
            return ""
        if len(first_line) > 72:
            first_line = first_line[:69] + "..."
        return first_line

    def set_current_step(self, text: str) -> None:
        def apply() -> None:
            label = getattr(self, "status_label", None)
            if label is not None and label.winfo_exists():
                label.configure(text=text, text_color=TEXT_MUTED)

        self.call_ui(apply)

    def _global_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: Any,
    ) -> None:
        text = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        ).rstrip()
        self.log("※未捕获错误：")
        self.log(text)

    def default_config(self) -> dict[str, Any]:
        return {
            "mkvmerge_path": "D:/path/to/mkvmerge.exe",
            "filename_ext": "[{height}P][WEBRip][{vcodec} {bitdepth}bit {acodec}]",
            "mkvoutputdir": "",
            "cover": True,
            "videotrack_lang": "ja",
            "videotrack_name": "WEBRip by KyokuSaiYume",
            "audiotrack_lang": "ja",
            "audiotrack_name": "WEB-DL",
            "asschsjpntrack_symbol": "[CHS_JPN]",
            "asschsjpntrack_lang": "zh",
            "asschsjpntrack_name": "简日双语-CHS_JPN",
            "asschtjpntrack_symbol": "[CHT_JPN]",
            "asschtjpntrack_lang": "zh",
            "asschtjpntrack_name": "繁日雙語-CHT_JPN",
            "assjpntrack_symbol": "[JPN]",
            "assjpntrack_lang": "ja",
            "assjpntrack_name": "日本語-JPN",
            "assengtrack_symbol": "[ENG]",
            "assengtrack_lang": "en",
            "assengtrack_name": "English-ENG",
            "asstrackname_separator": " - ",
            "assmultistyle_defaulttrack": "kawaii",
            "fontsubset_warning": "请勿安装此子集化字体 - ",
            "clean_scriptinfo": True,
            "clean_garbage": True,
            "clean_furigana": True,
            "clean_space": True,
            "clean_all_space": False,
            "unicode_to_utf8": True,
            "scriptinfo": "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nLayoutResX: 1920\nLayoutResY: 1080\nWrapStyle: 0\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\nOriginal Script: 極彩花夢\nLanguage: {LANGUAGE}",
            "scriptinfo_language": "CHS_JPN,CHT_JPN,JPN",
            "optional_styles": "Sx-en,Ex-lrc | op_jp,Ex-lrc | op_zh,Ex-lrc | op_en,Ex-lrc | ed_jp,Ex-lrc | ed_zh,Ex-lrc | ed_en",
            "generate_cht": True,
            "generate_cht_styles": "Sx-zh,Rx-annotation",
            "generate_cht_keep_comment": True,
            "zhconvert_json": '{"text":"{ASSCONTENT}","apiKey":"","ignoreTextStyles":"Ex-KSY,Ex-invisible","jpTextStyles":"Sx-jp,*noAutoJpTextStyles","jpTextConversionStrategy":"protectOnlySameOrigin","jpStyleConversionStrategy":"protectOnlySameOrigin","modules":"{\\"ChineseVariant\\":\\"0\\",\\"Computer\\":\\"0\\",\\"EllipsisMark\\":\\"0\\",\\"EngNumFWToHW\\":\\"0\\",\\"GanToZuo\\":\\"-1\\",\\"Gundam\\":\\"0\\",\\"HunterXHunter\\":\\"0\\",\\"InternetSlang\\":\\"-1\\",\\"Mythbusters\\":\\"0\\",\\"Naruto\\":\\"0\\",\\"OnePiece\\":\\"0\\",\\"Pocketmon\\":\\"0\\",\\"ProperNoun\\":\\"-1\\",\\"QuotationMark\\":\\"0\\",\\"RemoveSpaces\\":\\"0\\",\\"Repeat\\":\\"-1\\",\\"RepeatAutoFix\\":\\"-1\\",\\"Smooth\\":\\"-1\\",\\"TengTong\\":\\"0\\",\\"TransliterationToTranslation\\":\\"0\\",\\"Typo\\":\\"-1\\",\\"Unit\\":\\"-1\\",\\"VioletEvergarden\\":\\"0\\"}","userPostReplace":"","userPreReplace":"","userProtectReplace":"","diffCharLevel":0,"diffContextLines":1,"diffEnable":0,"diffIgnoreCase":0,"diffIgnoreWhiteSpaces":0,"diffTemplate":"Inline","cleanUpText":0,"ensureNewlineAtEof":0,"translateTabsToSpaces":-1,"trimTrailingWhiteSpaces":0,"unifyLeadingHyphen":0,"converter":"Traditional"}',
            "generate_jpn": True,
            "jpn_convert": False,
            "jpn_convert_styles_to_delete": "Sx-zh,Rx-annotation",
            "generate_multistyle": True,
            "generate_karaoke": True,
            "generate_language": "CHS_JPN,CHT_JPN,JPN",
            "aegisub_cli_path": "D:/path/to/aegisub-cli.exe",
            "aegisub_cli_loglevel": "2",
            "proxy": "0",
        }

    def initconfig(self) -> None:
        self.config = self.default_config()
        self.saveconfig()

    def setconfig(self) -> None:
        if not self.config_file.exists():
            self.initconfig()
            return
        self.config = json.loads(read_text(self.config_file))
        self.merge_default_config(save=True)

    def merge_default_config(self, save: bool = True) -> None:
        defaults = self.default_config()
        changed = False
        for key, value in defaults.items():
            if key not in self.config:
                self.config[key] = value
                changed = True
        if changed and save:
            self.saveconfig()

    def getconfig(self, key: str) -> Any:
        if key == "assstyles":
            return self.get_assstyles_interactive()
        if key not in self.config:
            self.merge_default_config(save=True)
        return self.config[key]

    def saveconfig(self) -> None:
        atomic_write_json(self.config_file, self.config)

    def _apply_proxy(self) -> None:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)
        port = str(self.getconfig("proxy")).strip()
        if port and port != "0":
            proxy = f"http://127.0.0.1:{port}"
            os.environ["HTTP_PROXY"] = proxy
            os.environ["HTTPS_PROXY"] = proxy
            self.log(f"使用代理：{proxy}")
        else:
            self.log("未启用代理")

    def get_assstyles_interactive(self) -> dict[str, dict[str, str]]:
        if self.assstyles:
            return self.assstyles
        style_dir = self.folder / "assstyles"
        files = sorted(style_dir.rglob("*.json")) if style_dir.exists() else []
        if not files:
            raise FatalProcessError(f"※样式表文件不存在：{style_dir}")
        if len(files) == 1:
            selected = files[0]
        else:
            dialog = SelectDialog(self, "选择样式表", [x.name for x in files])
            result = dialog.show()
            if result is None:
                raise FatalProcessError("※未选择样式表。")
            selected = next(x for x in files if x.name == result)
        try:
            self.assstyles = json.loads(read_text(selected))
        except Exception as exc:
            raise FatalProcessError(
                f"※样式表读取失败：{selected}\n原因：{exc}"
            ) from exc
        if "CHS_JPN" in self.assstyles:
            for lang in list(self.assstyles.keys()):
                for style in list(self.assstyles[lang].keys()):
                    if not self.assstyles[lang][style]:
                        self.assstyles[lang][style] = self.assstyles["CHS_JPN"].get(
                            style, ""
                        )
        return self.assstyles

    def subtitle_track_map(self, include_eng: bool) -> dict[str, tuple[str, str]]:
        mapping = {
            self.getconfig("asschsjpntrack_symbol"): (
                self.getconfig("asschsjpntrack_lang"),
                self.getconfig("asschsjpntrack_name"),
            ),
            self.getconfig("asschtjpntrack_symbol"): (
                self.getconfig("asschtjpntrack_lang"),
                self.getconfig("asschtjpntrack_name"),
            ),
            self.getconfig("assjpntrack_symbol"): (
                self.getconfig("assjpntrack_lang"),
                self.getconfig("assjpntrack_name"),
            ),
        }
        if include_eng:
            mapping[self.getconfig("assengtrack_symbol")] = (
                self.getconfig("assengtrack_lang"),
                self.getconfig("assengtrack_name"),
            )
        return mapping

    def getcache(self, init: bool = False) -> None:
        if not self.cache_file.exists():
            if init:
                self.call_ui(
                    lambda: (
                        self.cache_toggle.set_value(False, notify=False),
                        self.cache_toggle.set_enabled(False),
                    )
                )
            self.cache = {}
            return
        try:
            data = json.loads(read_text(self.cache_file))
            self.cache = {
                str(k): list(v) for k, v in data.items() if isinstance(v, list)
            }
        except Exception as exc:
            self.log(f"字体缓存读取失败，将重新生成。原因：{exc}")
            self.cache = {}
            self.call_ui(lambda: self.cache_toggle.set_value(False, notify=False))

    def savecache(self) -> None:
        atomic_write_json(self.cache_file, self.cache)
        self.call_ui(
            lambda: (
                self.cache_toggle.set_value(True, notify=False),
                self.cache_toggle.set_enabled(True),
            )
        )

    def generatecache(self) -> None:
        self.cache = {}
        font_dir = Path(os.environ.get("SystemRoot", "C:/")) / "Fonts"
        font_paths = [
            Path(root) / f for root, _, files in os.walk(font_dir) for f in files
        ]
        total = 0
        for font_path in font_paths:
            if font_path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            try:
                names = self._read_font_names(font_path)
                if names:
                    self.cache[str(font_path)] = sorted(names)
                    total += 1
            except Exception as exc:
                self.log(f"字体读取错误：{font_path.name}\n原因：{exc}")
        self.log(f"字体缓存生成完成：{total} 个字体文件")

    @staticmethod
    def _read_font_names(font_path: Path) -> set[str]:
        def names_from_font(font: TTFont) -> set[str]:
            out: set[str] = set()
            for record in font["name"].names:
                if record.nameID not in {1, 4, 6, 16}:
                    continue
                try:
                    name = record.toUnicode().strip()
                except Exception:
                    continue
                if name:
                    out.add(name)
            return out

        names: set[str] = set()
        if font_path.suffix.lower() == ".ttc":
            collection = TTCollection(str(font_path))
            try:
                for font in collection.fonts:
                    names.update(names_from_font(font))
            finally:
                collection.close()
        else:
            font = TTFont(str(font_path), lazy=True)
            try:
                names.update(names_from_font(font))
            finally:
                font.close()
        return names

    def getfontfile(self, fontname: str) -> tuple[str | None, str | None]:
        target = fontname.strip().lower()
        for path, names in self.cache.items():
            if any(target == str(name).strip().lower() for name in names):
                return path, Path(path).name
        for path, names in self.cache.items():
            if any(target in str(name).strip().lower() for name in names):
                return path, Path(path).name
        return None, None

    def get_assformat_by_key(self, fmt: str, content: str, key: str) -> str:
        keys = [x.strip() for x in fmt.removeprefix("Format:").split(",")]
        if key not in keys:
            return ""
        idx = keys.index(key)
        prefix, sep, body = content.partition(":")
        fields = body.lstrip().split(",", maxsplit=len(keys) - 1)
        if idx >= len(fields):
            return ""
        return fields[idx].strip() if key != "Text" else fields[idx]

    def subset_font(
        self,
        fontname: str,
        fontfile: Path,
        characters: str,
        newname: str,
        outputpath: Path,
    ) -> None:
        source: str | io.BytesIO = str(fontfile)
        if fontfile.suffix.lower() == ".ttc":
            collection = TTCollection(str(fontfile))
            try:
                for font in collection.fonts:
                    names = {
                        record.toUnicode()
                        for record in font["name"].names
                        if record.nameID in {1, 4, 6, 16}
                    }
                    if fontname in names:
                        bio = io.BytesIO()
                        font.save(bio)
                        bio.seek(0)
                        source = bio
                        break
            finally:
                collection.close()
        options = subset.Options(name_languages="*")
        font = subset.load_font(source, options=options)
        try:
            sub = subset.Subsetter(options=options)
            sub.populate(text=characters)
            sub.subset(font)
            for record in font["name"].names:
                if record.nameID in {1, 4, 6, 16}:
                    try:
                        record.string = newname.encode(record.getEncoding())
                    except Exception:
                        record.string = newname.encode("utf-16be")
            font.flavor = None
            subset.save_font(font, str(outputpath), options)
        finally:
            font.close()

    def fix_subset_font_names(
        self, filepath: Path, outputpath: Path, replacedict: Mapping[str, str]
    ) -> None:
        content = read_text(filepath)
        match = re.search(
            r"([\s\S]+?)\n\[Events\]\n([\s\S]+)", content, flags=re.MULTILINE
        )
        if not match:
            write_text(outputpath, content)
            return
        header, events = match.group(1), match.group(2)
        for old in sorted(replacedict, key=len, reverse=True):
            new = replacedict[old]
            header = header.replace(old, new)
            events = events.replace(f"\\fn{old}", f"\\fn{new}")
        write_text(outputpath, f"{header}\n[Events]\n{events}")

    def start_request(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("ASSFun", "当前任务仍在运行。", parent=self)
            return
        if not self.files:
            messagebox.showerror(
                "ASSFun", "请先拖入至少一个 ASS 字幕文件。", parent=self
            )
            return

        self.set_current_step("准备开始处理")
        cover: Path | None = None
        selected_default_style: str | None = None
        try:
            if self.mkv and self.getconfig("cover"):
                self.set_current_step("选择封面图片")
                cover = CoverSelectDialog(self).show()
                if cover is not None:
                    self.log(f"封面图片：{cover}")
                else:
                    self.log("未选择封面图片，跳过封面封入")
            if self.values.get("assgenerate"):
                styles = self.get_assstyles_interactive()
                if self.getconfig("generate_multistyle") and self.mkv:
                    options = sorted(
                        set().union(
                            *(v.keys() for v in styles.values() if isinstance(v, dict))
                        )
                    )
                    if options:
                        dialog = SelectDialog(
                            self,
                            "选择默认字幕样式",
                            options,
                            default=self.getconfig("assmultistyle_defaulttrack"),
                        )
                        selected_default_style = dialog.show()
                        if selected_default_style is None:
                            self.log("取消默认字幕样式选择，任务未开始。")
                            return
        except Exception as exc:
            messagebox.showerror("ASSFun", str(exc), parent=self)
            self.log(normalize_exception(exc))
            return

        self.cancel_event.clear()
        self._set_running(True)
        self.set_current_step("任务运行中")
        processor = ASSProcessor(
            self,
            self.process_manager,
            cover=cover,
            selected_default_style=selected_default_style,
        )
        self.worker = threading.Thread(
            target=self._run_worker, args=(processor,), daemon=True
        )
        self.worker.start()

    def _run_worker(self, processor: ASSProcessor) -> None:
        try:
            processor.run()
            self.log("全部处理完成。")
        except FatalProcessError as exc:
            self.log(str(exc))
        except Exception as exc:
            self.log("※处理失败。")
            self.log(normalize_exception(exc))
        finally:
            self.assstyles = {}
            self.call_ui(lambda: self._set_running(False))

    def _set_running(self, running: bool) -> None:
        if getattr(self, "_exiting", False):
            return
        try:
            if hasattr(self, "start_button") and self.start_button.winfo_exists():
                if running:
                    self.start_button.configure(
                        state="disabled",
                        text="处理中…",
                        fg_color=THEME["disabled"],
                        hover_color=THEME["disabled"],
                        text_color_disabled=TEXT_MUTED,
                    )
                    apply_interactive_cursor(self.start_button)
                else:
                    self.start_button.configure(
                        state="normal",
                        text="开始处理",
                        fg_color=ACCENT,
                        hover_color=ACCENT_HOVER,
                        text_color=TEXT,
                    )
                    apply_interactive_cursor(self.start_button)
        except tkinter.TclError:
            return

    def openconfigwindow(self) -> None:
        old_window = getattr(self, "configwindow", None)
        if _safe_window_exists(old_window):
            old_window.destroy()
        self.configwindow = ConfigWindow(self)

    def _on_close(self) -> None:
        if getattr(self, "_exiting", False):
            return
        self._exiting = True
        self.cancel_event.set()
        try:
            if hasattr(self, "process_manager"):
                self.process_manager.terminate_all()
        except Exception:
            pass
        try:
            while not self.log_queue.empty():
                self.log_queue.get_nowait()
        except Exception:
            pass
        try:
            while not self.ui_queue.empty():
                self.ui_queue.get_nowait()
        except Exception:
            pass
        safe_after_cancel_all(self)
        try:
            for child in list(self.winfo_children()):
                try:
                    child.destroy()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.quit()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            try:
                self._force_exit_timer = threading.Timer(0.35, lambda: os._exit(0))
                self._force_exit_timer.daemon = False
                self._force_exit_timer.start()
            except Exception:
                os._exit(0)


def run_app() -> None:
    ui = ASSFunUI()
    ui.mainloop()


if __name__ == "__main__":
    run_app()
