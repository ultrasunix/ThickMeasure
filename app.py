#!/usr/bin/env python3
from __future__ import annotations

import queue
import csv
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from argparse import Namespace
from collections import deque
from datetime import datetime
from pathlib import Path
import math

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np
from PIL import Image, ImageTk


APP_DIR = Path.home() / "thickapp"
LIT3RICK_DIR = APP_DIR / "lit3rick" / "py_fpga"
LOGO_PATH = APP_DIR / "logo.png"
SAVE_DIR = APP_DIR / "sdata-ThickMeasure"
PROGRAM_DIR = APP_DIR / "lit3rick" / "program"
PROGRAM_SCRIPT = PROGRAM_DIR / "prog_ram.sh"
sys.path.insert(0, str(LIT3RICK_DIR))

KIOSK_FULLSCREEN = True
TOUCHSCREEN_GEOMETRY = "800x480"
PLOT_BG = "#111317"
PLOT_FG = "#ffe66d"
PLOT_ENV = "#4fd1c5"
PLOT_GRID = "#6b7280"
PLOT_AXIS = "#d6d3c4"
RIBBON_BG = "#23262d"
RIBBON_FG = "#f5e9b8"
BUTTON_BG = "#343944"
BUTTON_ACTIVE_BG = "#4b5563"
ENTRY_BG = "#111317"
ENTRY_FG = "#ffe66d"

from lit3rick_thickness_live import (  # noqa: E402
    EchoResult,
    FS_HZ,
    acquire_trace,
    analytic_envelope,
    build_fpga,
    close_fpga,
    detect_backwall_echoes,
    detect_backwall_echoes_guided,
    refine_echo_spacing_by_correlation,
    robust_median,
    smooth,
)


class ThicknessApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ThickMeasure")
        self.screen_width = self.root.winfo_screenwidth()
        self.screen_height = self.root.winfo_screenheight()
        self.ui_scale = self.compute_ui_scale()
        self.root.geometry(f"{self.screen_width}x{self.screen_height}")
        self.root.minsize(480, 320)
        if KIOSK_FULLSCREEN:
            self.root.attributes("-fullscreen", True)

        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.velocity_m_s: float | None = None

        self.args = self.make_args()
        self.thickness_history: deque[float] = deque(maxlen=self.args.history)
        self.frame_history: deque[int] = deque(maxlen=self.args.history)
        self.saved_thickness: deque[dict[str, float | int | str]] = deque(maxlen=10)
        self.recent_raw_thickness: deque[float] = deque(maxlen=self.args.thickness_median_frames)
        self.filtered_thickness_mm: float | None = None
        self.calibration_selecting = False
        self.calibration_clicks: list[int] = []
        self.latest_calibration_env: np.ndarray | None = None
        self.latest_calibration_trace: np.ndarray | None = None
        self.latest_calibration_result: EchoResult | None = None
        self.calibrated_first_idx: int | None = None
        self.calibrated_second_idx: int | None = None
        self.calibrated_spacing_samples: int | None = None
        self.keypad_window: tk.Toplevel | None = None
        self.keypad_target: tk.StringVar | None = None

        self.status_var = tk.StringVar(value="Ready")
        self.thickness_var = tk.StringVar(value=f"{self.args.known_thickness_mm:.1f}")
        self.approx_velocity_var = tk.StringVar(value=f"{self.args.approximate_velocity_m_s:.0f}")
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.start_board_programming)
        self.root.after(50, self.process_messages)

    def make_args(self) -> Namespace:
        acq_us = 60.0
        return Namespace(
            known_thickness_mm=20.0,
            fs_hz=FS_HZ,
            acq_us=acq_us,
            samples=int(FS_HZ * acq_us * 1e-6),
            calibration_frames=15,
            gate_start_us=2.0,
            gate_end_us=60.0,
            display_start_us=0.0,
            display_end_us=60.0,
            min_echo_spacing_us=3.0,
            threshold=8.0,
            smooth_points=7,
            interval_s=0.10,
            capture_wait_s=0.001,
            highpass_hz=20_000.0,
            history=250,
            frames=100,
            no_plot=False,
            simulate=False,
            i2c_bus=1,
            spi_bus=0,
            spi_device=0,
            spi_speed_hz=1_000_000,
            pdelay=1,
            phv_time=5,
            pnhv_time=5,
            pdamp_time=8,
            hilo=1,
            dac=300,
            tgc_start=250,
            tgc_end=455,
            tgc_points=16,
            thickness_ymin_mm=15.0,
            thickness_ymax_mm=30.0,
            thickness_median_frames=9,
            thickness_ema_alpha=0.25,
            timing_method="xcorr",
            xcorr_half_window_us=0.9,
            approximate_velocity_m_s=5850.0,
            first_echo_min_us=4.0,
            second_search_fraction=0.20,
            min_velocity_ratio=0.85,
            max_velocity_ratio=1.15,
        )

    def compute_ui_scale(self) -> float:
        base = min(self.screen_width / 800.0, self.screen_height / 480.0)
        return max(1.35, min(2.0, base * 1.6))

    def build_ui(self) -> None:
        font_size = int(10 * self.ui_scale)
        entry_font_size = int(12 * self.ui_scale)
        button_height = max(2, int(round(1.4 * self.ui_scale)))
        button_font = tkfont.Font(family="TkDefaultFont", size=font_size)
        entry_font = tkfont.Font(family="TkDefaultFont", size=entry_font_size)
        pad = int(4 * self.ui_scale)

        toolbar = tk.Frame(self.root, bg=RIBBON_BG)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=pad, pady=pad)
        controls = tk.Frame(toolbar, bg=RIBBON_BG)
        controls.pack(side=tk.TOP, fill=tk.X)
        status_row = tk.Frame(toolbar, bg=RIBBON_BG)
        status_row.pack(side=tk.TOP, fill=tk.X, pady=(pad, 0))

        tk.Label(controls, text="Ref. Thickness", font=button_font, bg=RIBBON_BG, fg=RIBBON_FG).pack(side=tk.LEFT, padx=(0, pad))
        self.thickness_entry = tk.Entry(controls, textvariable=self.thickness_var, width=6, font=entry_font, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG, justify="center")
        self.thickness_entry.bind("<Button-1>", lambda _event: self.open_keypad(self.thickness_var, "Ref. Thickness"))
        self.thickness_entry.pack(side=tk.LEFT)
        tk.Label(controls, text="mm", font=button_font, bg=RIBBON_BG, fg=RIBBON_FG).pack(side=tk.LEFT, padx=(pad, pad))

        tk.Label(controls, text="Ref. Velocity", font=button_font, bg=RIBBON_BG, fg=RIBBON_FG).pack(side=tk.LEFT, padx=(0, pad))
        self.approx_velocity_entry = tk.Entry(controls, textvariable=self.approx_velocity_var, width=6, font=entry_font, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG, justify="center")
        self.approx_velocity_entry.bind("<Button-1>", lambda _event: self.open_keypad(self.approx_velocity_var, "Ref. Velocity"))
        self.approx_velocity_entry.pack(side=tk.LEFT)
        tk.Label(controls, text="m/s", font=button_font, bg=RIBBON_BG, fg=RIBBON_FG).pack(side=tk.LEFT, padx=(pad, pad))

        self.calibration_button = tk.Button(
            controls,
            text="Calibration",
            command=self.start_calibration,
            width=10,
            height=button_height,
            font=button_font,
            bg=BUTTON_BG,
            fg=RIBBON_FG,
            activebackground=BUTTON_ACTIVE_BG,
            activeforeground=RIBBON_FG,
        )
        self.calibration_button.pack(side=tk.LEFT, padx=(0, pad))

        self.start_button = tk.Button(
            controls,
            text="Start",
            command=self.start_measurement,
            width=8,
            height=button_height,
            font=button_font,
            bg=BUTTON_BG,
            fg=RIBBON_FG,
            activebackground=BUTTON_ACTIVE_BG,
            activeforeground=RIBBON_FG,
        )
        self.start_button.pack(side=tk.LEFT, padx=(0, pad))

        self.stop_button = tk.Button(
            controls,
            text="Stop",
            command=self.stop_measurement,
            width=8,
            height=button_height,
            font=button_font,
            bg=BUTTON_BG,
            fg=RIBBON_FG,
            activebackground=BUTTON_ACTIVE_BG,
            activeforeground=RIBBON_FG,
            state=tk.DISABLED,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(0, pad))

        self.save_button = tk.Button(
            controls,
            text="Save",
            command=self.save_recent_data,
            width=8,
            height=button_height,
            font=button_font,
            bg=BUTTON_BG,
            fg=RIBBON_FG,
            activebackground=BUTTON_ACTIVE_BG,
            activeforeground=RIBBON_FG,
            state=tk.DISABLED,
        )
        self.save_button.pack(side=tk.LEFT)

        status = tk.Label(status_row, textvariable=self.status_var, anchor="w", font=button_font, bg=RIBBON_BG, fg=RIBBON_FG)
        status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.add_logo(controls)

        fig_width = max(5.0, self.screen_width / 100.0)
        fig_height = max(3.0, (self.screen_height - int(92 * self.ui_scale)) / 100.0)
        self.fig = Figure(figsize=(fig_width, fig_height), constrained_layout=True)
        self.fig.patch.set_facecolor(PLOT_BG)
        self.ax_rf = self.fig.add_subplot(1, 1, 1)
        self.ax_rf.set_facecolor(PLOT_BG)

        plot_text_size = 20
        self.ax_rf.set_xlabel("Time (\u03bcs)", fontsize=plot_text_size, color=PLOT_FG)
        self.ax_rf.set_ylabel("Amplitude (~)", fontsize=plot_text_size, color=PLOT_FG)
        self.ax_rf.tick_params(axis="both", labelsize=plot_text_size, colors=PLOT_FG)
        for spine in self.ax_rf.spines.values():
            spine.set_color(PLOT_AXIS)
            spine.set_linewidth(1.0)
        self.ax_rf.grid(True, which="major", axis="both", color=PLOT_GRID, linestyle="--", linewidth=0.55, alpha=0.65)

        time_us = np.arange(self.args.samples) / self.args.fs_hz * 1e6
        self.rf_line, = self.ax_rf.plot(time_us, np.zeros(self.args.samples), color=PLOT_FG, lw=0.9)
        self.env_line, = self.ax_rf.plot(time_us, np.zeros(self.args.samples), color=PLOT_ENV, lw=1.3)
        self.peak_lines = [
            self.ax_rf.axvline(0, color="#ff6b6b", ls="--", lw=1.0, alpha=0.9),
            self.ax_rf.axvline(0, color="#ff6b6b", ls="--", lw=1.0, alpha=0.9),
        ]
        self.readout = self.ax_rf.text(
            0.01,
            0.95,
            "",
            transform=self.ax_rf.transAxes,
            va="top",
            fontsize=plot_text_size,
            color=PLOT_FG,
        )

        self.ax_rf.set_xlim(self.args.display_start_us, self.args.display_end_us)
        self.ax_rf.set_ylim(-1.25, 1.25)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        self.canvas.mpl_connect("button_press_event", self.on_plot_click)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def add_logo(self, parent: tk.Frame) -> None:
        if not LOGO_PATH.exists():
            return

        image = Image.open(LOGO_PATH)
        logo_width = int(120 * self.ui_scale)
        logo_height = int(34 * self.ui_scale)
        image.thumbnail((logo_width, logo_height), Image.Resampling.LANCZOS)
        self.logo_image = ImageTk.PhotoImage(image)
        logo = tk.Label(parent, image=self.logo_image, borderwidth=0, cursor="hand2")
        logo.bind("<Button-1>", lambda _event: self.on_close())
        logo.pack(side=tk.RIGHT, padx=(int(8 * self.ui_scale), 0))

    def open_keypad(self, target: tk.StringVar, title: str) -> str:
        if self.keypad_window is not None and self.keypad_window.winfo_exists():
            self.keypad_target = target
            self.keypad_window.lift()
            return "break"

        self.keypad_target = target
        window = tk.Toplevel(self.root)
        self.keypad_window = window
        window.title(title)
        window.configure(bg=RIBBON_BG)
        window.transient(self.root)

        keypad_font = tkfont.Font(family="TkDefaultFont", size=int(16 * self.ui_scale))
        display_font = tkfont.Font(family="TkDefaultFont", size=int(18 * self.ui_scale))
        value_var = tk.StringVar(value=target.get())

        display = tk.Entry(
            window,
            textvariable=value_var,
            font=display_font,
            bg=ENTRY_BG,
            fg=ENTRY_FG,
            insertbackground=ENTRY_FG,
            justify="center",
            width=12,
        )
        display.grid(row=0, column=0, columnspan=3, padx=10, pady=10, sticky="nsew")

        def append_char(char: str) -> None:
            value = value_var.get()
            if char == "." and "." in value:
                return
            value_var.set(value + char)

        def backspace() -> None:
            value_var.set(value_var.get()[:-1])

        def accept() -> None:
            target.set(value_var.get())
            close_keypad()

        def cancel() -> None:
            close_keypad()

        def close_keypad() -> None:
            self.keypad_target = None
            self.keypad_window = None
            if window.winfo_exists():
                window.destroy()

        keys = [
            ("1", 1, 0), ("2", 1, 1), ("3", 1, 2),
            ("4", 2, 0), ("5", 2, 1), ("6", 2, 2),
            ("7", 3, 0), ("8", 3, 1), ("9", 3, 2),
            (".", 4, 0), ("0", 4, 1), ("⌫", 4, 2),
        ]
        for label, row, col in keys:
            if label == "⌫":
                command = backspace
            else:
                command = lambda char=label: append_char(char)
            tk.Button(
                window,
                text=label,
                command=command,
                font=keypad_font,
                bg=BUTTON_BG,
                fg=RIBBON_FG,
                activebackground=BUTTON_ACTIVE_BG,
                activeforeground=RIBBON_FG,
                width=4,
                height=2,
            ).grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

        tk.Button(
            window,
            text="Clear",
            command=lambda: value_var.set(""),
            font=keypad_font,
            bg=BUTTON_BG,
            fg=RIBBON_FG,
            activebackground=BUTTON_ACTIVE_BG,
            activeforeground=RIBBON_FG,
        ).grid(row=5, column=0, padx=6, pady=8, sticky="nsew")
        tk.Button(
            window,
            text="Cancel",
            command=cancel,
            font=keypad_font,
            bg=BUTTON_BG,
            fg=RIBBON_FG,
            activebackground=BUTTON_ACTIVE_BG,
            activeforeground=RIBBON_FG,
        ).grid(row=5, column=1, padx=6, pady=8, sticky="nsew")
        tk.Button(
            window,
            text="OK",
            command=accept,
            font=keypad_font,
            bg="#4d7c0f",
            fg="#fff7c2",
            activebackground="#65a30d",
            activeforeground="#fff7c2",
        ).grid(row=5, column=2, padx=6, pady=8, sticky="nsew")

        for col in range(3):
            window.grid_columnconfigure(col, weight=1)
        for row in range(6):
            window.grid_rowconfigure(row, weight=1)

        window.update_idletasks()
        x = self.root.winfo_x() + max(0, (self.root.winfo_width() - window.winfo_width()) // 2)
        y = self.root.winfo_y() + max(0, (self.root.winfo_height() - window.winfo_height()) // 2)
        window.geometry(f"+{x}+{y}")
        window.protocol("WM_DELETE_WINDOW", cancel)
        return "break"

    def update_rf_axis_limits(self, result) -> None:
        self.ax_rf.set_xlim(0.0, self.args.display_end_us)
        self.ax_rf.set_ylim(-1.25, 1.25)

    def update_thickness_axis_limits(self, thickness_mm: float) -> None:
        return

    def detect_tracked_backwall_echoes(self, env: np.ndarray, trace: np.ndarray) -> EchoResult | None:
        if self.calibrated_first_idx is None or self.calibrated_spacing_samples is None:
            return None

        spacing = self.calibrated_spacing_samples
        first_half_width = max(int(0.18 * spacing), int(0.8e-6 * self.args.fs_hz))
        second_half_width = max(int(0.22 * spacing), int(0.9e-6 * self.args.fs_hz))

        first_idx = self.peak_in_window(
            env,
            self.calibrated_first_idx - first_half_width,
            self.calibrated_first_idx + first_half_width,
        )
        if first_idx is None:
            return None

        expected_second = first_idx + spacing
        second_idx = self.peak_in_window(
            env,
            expected_second - second_half_width,
            expected_second + second_half_width,
        )
        if second_idx is None or second_idx <= first_idx:
            return None

        result = EchoResult(
            first_idx=first_idx,
            second_idx=second_idx,
            dt_s=(second_idx - first_idx) / self.args.fs_hz,
            first_pos=float(first_idx),
            second_pos=float(second_idx),
        )
        if self.args.timing_method == "xcorr":
            result = refine_echo_spacing_by_correlation(
                trace,
                result,
                self.args.fs_hz,
                self.args.xcorr_half_window_us,
            )
        return result

    def peak_in_window(self, env: np.ndarray, left: int, right: int) -> int | None:
        left = max(0, int(left))
        right = min(env.size, int(right) + 1)
        if right <= left:
            return None
        return left + int(np.argmax(env[left:right]))

    def start_measurement(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if self.velocity_m_s is None:
            self.status_var.set("Run Calibration before Start")
            return

        self.thickness_history.clear()
        self.frame_history.clear()
        self.saved_thickness.clear()
        self.recent_raw_thickness.clear()
        self.filtered_thickness_mm = None
        self.stop_event.clear()
        self.calibration_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.save_button.config(state=tk.DISABLED)
        self.status_var.set("Starting hardware...")

        self.worker = threading.Thread(target=self.measurement_worker, daemon=True)
        self.worker.start()

    def stop_measurement(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopping...")
        self.stop_button.config(state=tk.DISABLED)

    def save_recent_data(self) -> None:
        if not self.saved_thickness:
            self.status_var.set("No thickness data to save")
            return

        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"sdata-thickness-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.csv"
        path = SAVE_DIR / filename
        fieldnames = [
            "timestamp",
            "frame",
            "thickness_mm",
            "raw_thickness_mm",
            "velocity_m_s",
            "echo_spacing_us",
            "first_echo_us",
            "second_echo_us",
        ]
        rows = list(self.saved_thickness)[-10:]
        while len(rows) < 10:
            rows.append(
                {
                    "timestamp": "",
                    "frame": 0,
                    "thickness_mm": 0,
                    "raw_thickness_mm": 0,
                    "velocity_m_s": 0,
                    "echo_spacing_us": 0,
                    "first_echo_us": 0,
                    "second_echo_us": 0,
                }
            )

        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        self.save_button.config(state=tk.DISABLED)
        self.status_var.set(f"Saved 10 rows to {path.name}")

    def start_board_programming(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        self.calibration_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Programming lit3rick board...")

        self.worker = threading.Thread(target=self.board_programming_worker, daemon=True)
        self.worker.start()

    def board_programming_worker(self) -> None:
        try:
            result = subprocess.run(
                ["sudo", "-n", str(PROGRAM_SCRIPT)],
                cwd=str(PROGRAM_DIR),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown error").strip()
                raise RuntimeError(f"Board programming failed: {detail}")
            self.messages.put(("board_programmed", None))
        except Exception as exc:
            self.messages.put(("error", str(exc)))
            self.messages.put(("board_programming_stopped", None))

    def start_calibration(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.update_calibration_inputs():
            return

        self.stop_event.clear()
        self.velocity_m_s = None
        self.calibration_selecting = False
        self.calibration_clicks.clear()
        self.latest_calibration_env = None
        self.latest_calibration_trace = None
        self.latest_calibration_result = None
        self.calibration_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Starting calibration...")

        self.worker = threading.Thread(target=self.calibration_worker, daemon=True)
        self.worker.start()

    def update_calibration_inputs(self) -> bool:
        try:
            thickness_value = float(self.thickness_var.get())
        except ValueError:
            self.status_var.set("Calibration thickness must be a number")
            return False

        if thickness_value <= 0:
            self.status_var.set("Calibration thickness must be greater than 0 mm")
            return False

        try:
            velocity_value = float(self.approx_velocity_var.get())
        except ValueError:
            self.status_var.set("Approx. velocity must be a number")
            return False

        if velocity_value <= 0:
            self.status_var.set("Approx. velocity must be greater than 0 m/s")
            return False

        self.args.known_thickness_mm = thickness_value
        self.args.approximate_velocity_m_s = velocity_value
        return True

    def calibration_worker(self) -> None:
        fpga = None
        try:
            fpga = build_fpga(self.args)
            self.run_calibration_capture(fpga)
            self.messages.put(("calibration_pick_ready", None))
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        finally:
            if fpga is not None:
                close_fpga(fpga)
            self.messages.put(("calibration_stopped", None))

    def measurement_worker(self) -> None:
        fpga = None
        try:
            fpga = build_fpga(self.args)
            velocity_m_s = self.velocity_m_s
            if velocity_m_s is None:
                raise RuntimeError("Run Calibration before Start.")
            frame = 0

            while not self.stop_event.is_set():
                frame += 1
                trace = acquire_trace(fpga, self.args)
                env = smooth(analytic_envelope(trace), self.args.smooth_points)
                result = self.detect_tracked_backwall_echoes(env, trace)
                self.messages.put(("frame", (frame, trace, env, result, velocity_m_s)))
                time.sleep(self.args.interval_s)
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        finally:
            if fpga is not None:
                close_fpga(fpga)
            self.messages.put(("stopped", None))

    def run_calibration_capture(self, fpga) -> None:
        known_thickness_m = self.args.known_thickness_mm / 1000.0
        usable_frames = 0

        for frame_no in range(self.args.calibration_frames):
            if self.stop_event.is_set():
                raise RuntimeError("Measurement stopped during calibration.")

            trace = acquire_trace(fpga, self.args)
            env = smooth(analytic_envelope(trace), self.args.smooth_points)
            result = detect_backwall_echoes_guided(
                env,
                trace,
                self.args.fs_hz,
                self.args.gate_start_us,
                self.args.gate_end_us,
                known_thickness_m,
                self.args.approximate_velocity_m_s,
                self.args.threshold,
                self.args.first_echo_min_us,
                self.args.second_search_fraction,
                refine_with_correlation=False,
                min_velocity_ratio=self.args.min_velocity_ratio,
                max_velocity_ratio=self.args.max_velocity_ratio,
            )
            if result is not None:
                usable_frames += 1
            self.messages.put(("calibration_frame", (trace, env, result, usable_frames, frame_no + 1)))
            time.sleep(self.args.interval_s)

    def process_messages(self) -> None:
        needs_draw = False
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "board_programmed":
                    self.status_var.set("Board ready - run Calibration")
                    self.calibration_button.config(state=tk.NORMAL)
                    self.start_button.config(state=tk.NORMAL if self.velocity_m_s is not None else tk.DISABLED)
                    self.stop_button.config(state=tk.DISABLED)
                elif kind == "board_programming_stopped":
                    self.calibration_button.config(state=tk.NORMAL)
                    self.start_button.config(state=tk.NORMAL if self.velocity_m_s is not None else tk.DISABLED)
                    self.stop_button.config(state=tk.DISABLED)
                elif kind == "calibrated":
                    self.velocity_m_s = float(payload)
                    self.status_var.set(f"Calibration complete - velocity {self.velocity_m_s:.1f} m/s")
                elif kind == "calibration_frame":
                    self.update_calibration_plot(*payload)
                    needs_draw = True
                elif kind == "calibration_pick_ready":
                    if self.finish_auto_calibration():
                        self.calibration_selecting = True
                        self.status_var.set(
                            f"Calibration complete - velocity {self.velocity_m_s:.1f} m/s. Press Start or tap two echoes to adjust."
                        )
                    else:
                        self.calibration_selecting = True
                        self.calibration_clicks.clear()
                        self.status_var.set("Click first back-wall echo, then second back-wall echo")
                elif kind == "calibration_stopped":
                    self.calibration_button.config(state=tk.NORMAL)
                    self.start_button.config(state=tk.NORMAL if self.velocity_m_s is not None else tk.DISABLED)
                    self.stop_button.config(state=tk.DISABLED)
                elif kind == "error":
                    self.status_var.set(f"Error: {payload}")
                    self.calibration_button.config(state=tk.NORMAL)
                    self.start_button.config(state=tk.NORMAL if self.velocity_m_s is not None else tk.DISABLED)
                    self.stop_button.config(state=tk.DISABLED)
                elif kind == "stopped":
                    self.calibration_button.config(state=tk.NORMAL)
                    self.start_button.config(state=tk.NORMAL)
                    self.stop_button.config(state=tk.DISABLED)
                    self.save_button.config(state=tk.NORMAL if self.saved_thickness else tk.DISABLED)
                    if self.stop_event.is_set():
                        self.status_var.set("Stopped")
                elif kind == "frame":
                    self.update_plot(*payload)
                    needs_draw = True
        except queue.Empty:
            pass

        if needs_draw:
            self.canvas.draw_idle()
        self.root.after(50, self.process_messages)

    def update_plot(self, frame: int, trace: np.ndarray, env: np.ndarray, result, velocity_m_s: float) -> None:
        if result is None:
            display_scale = np.max(np.abs(trace)) or 1.0
            self.rf_line.set_ydata(trace / display_scale)
            self.env_line.set_ydata(env / display_scale)
            self.ax_rf.set_ylim(-1.25, 1.25)
            self.readout.set_text("echoes not detected - adjust gate/threshold")
            return

        display_scale = abs(env[result.first_idx]) or 1.0
        self.rf_line.set_ydata(trace / display_scale)
        self.env_line.set_ydata(env / display_scale)

        raw_thickness_mm = velocity_m_s * result.dt_s * 500.0
        if self.filtered_thickness_mm is not None and abs(raw_thickness_mm - self.filtered_thickness_mm) > 0.8:
            self.readout.set_text(
                f"velocity: {velocity_m_s:.1f} m/s\n"
                f"rejected echo jump\n"
                f"thickness: {self.filtered_thickness_mm:.3f} mm\n"
                f"raw: {raw_thickness_mm:.3f} mm"
            )
            self.status_var.set(f"Measuring - thickness {self.filtered_thickness_mm:.3f} mm")
            return

        self.update_rf_axis_limits(result)
        self.recent_raw_thickness.append(raw_thickness_mm)
        median_thickness_mm = float(np.median(self.recent_raw_thickness))

        if self.filtered_thickness_mm is None:
            self.filtered_thickness_mm = median_thickness_mm
        else:
            alpha = self.args.thickness_ema_alpha
            self.filtered_thickness_mm = (
                alpha * median_thickness_mm + (1.0 - alpha) * self.filtered_thickness_mm
            )

        thickness_mm = self.filtered_thickness_mm
        self.thickness_history.append(thickness_mm)
        self.frame_history.append(frame)
        self.saved_thickness.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "frame": frame,
                "thickness_mm": thickness_mm,
                "raw_thickness_mm": raw_thickness_mm,
                "velocity_m_s": velocity_m_s,
                "echo_spacing_us": result.dt_s * 1e6,
                "first_echo_us": result.first_idx / self.args.fs_hz * 1e6,
                "second_echo_us": result.second_idx / self.args.fs_hz * 1e6,
            }
        )

        for line, idx in zip(self.peak_lines, (result.first_idx, result.second_idx)):
            x_us = idx / self.args.fs_hz * 1e6
            line.set_xdata([x_us, x_us])

        self.readout.set_text(
            f"velocity: {velocity_m_s:.1f} m/s\n"
            f"echo spacing: {result.dt_s * 1e6:.3f} us\n"
            f"thickness: {thickness_mm:.3f} mm\n"
            f"raw: {raw_thickness_mm:.3f} mm"
        )
        self.status_var.set(f"Measuring - thickness {thickness_mm:.3f} mm")

    def update_calibration_plot(
        self,
        trace: np.ndarray,
        env: np.ndarray,
        result,
        usable_frames: int,
        total_frames: int,
    ) -> None:
        self.latest_calibration_trace = trace
        self.latest_calibration_env = env
        if result is not None:
            self.latest_calibration_result = result

        if result is not None:
            display_scale = abs(env[result.first_idx]) or 1.0
        else:
            display_scale = np.max(np.abs(trace)) or 1.0

        self.rf_line.set_ydata(trace / display_scale)
        self.env_line.set_ydata(env / display_scale)
        self.ax_rf.set_xlim(0.0, self.args.display_end_us)
        self.ax_rf.set_ylim(-1.25, 1.25)

        if result is not None:
            for line, idx in zip(self.peak_lines, (result.first_idx, result.second_idx)):
                x_us = idx / self.args.fs_hz * 1e6
                line.set_xdata([x_us, x_us])

        self.readout.set_text("Select first and second back-wall echoes after calibration")
        self.status_var.set(f"Calibrating... {usable_frames}/{total_frames} usable frames")

    def finish_auto_calibration(self) -> bool:
        result = self.latest_calibration_result
        if result is None:
            return False

        self.calibration_clicks = [result.first_idx, result.second_idx]
        self.finish_manual_calibration(manual=False)
        return True

    def on_plot_click(self, event) -> None:
        if not self.calibration_selecting or event.inaxes != self.ax_rf or event.xdata is None:
            return
        if self.latest_calibration_env is None:
            return

        if len(self.calibration_clicks) >= 2:
            self.calibration_clicks.clear()
            for line in self.peak_lines:
                line.set_xdata([0, 0])

        idx = self.snap_click_to_envelope_peak(event.xdata)
        if idx is None:
            return

        if self.calibration_clicks and idx <= self.calibration_clicks[0]:
            self.status_var.set("Second echo must be after first echo")
            return

        self.calibration_clicks.append(idx)
        line = self.peak_lines[len(self.calibration_clicks) - 1]
        x_us = idx / self.args.fs_hz * 1e6
        line.set_xdata([x_us, x_us])

        if len(self.calibration_clicks) == 1:
            self.status_var.set("First echo selected - click second back-wall echo")
            self.canvas.draw_idle()
            return

        self.finish_manual_calibration(manual=True)
        self.canvas.draw_idle()

    def snap_click_to_envelope_peak(self, x_us: float) -> int | None:
        env = self.latest_calibration_env
        if env is None:
            return None

        center = int(round(x_us * 1e-6 * self.args.fs_hz))
        window = max(4, int(0.35e-6 * self.args.fs_hz))
        left = max(0, center - window)
        right = min(env.size, center + window + 1)
        if right <= left:
            return None
        return left + int(np.argmax(env[left:right]))

    def finish_manual_calibration(self, manual: bool = True) -> None:
        first_idx, second_idx = self.calibration_clicks[:2]
        dt_s = (second_idx - first_idx) / self.args.fs_hz
        if dt_s <= 0:
            self.status_var.set("Invalid echo selection")
            return

        known_thickness_m = self.args.known_thickness_mm / 1000.0
        self.velocity_m_s = 2.0 * known_thickness_m / dt_s
        self.calibrated_first_idx = first_idx
        self.calibrated_second_idx = second_idx
        self.calibrated_spacing_samples = second_idx - first_idx
        self.calibration_selecting = False
        self.start_button.config(state=tk.NORMAL)
        self.calibration_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.readout.set_text(
            f"velocity: {self.velocity_m_s:.1f} m/s\n"
            f"echo spacing: {dt_s * 1e6:.3f} us\n"
            f"{'manual' if manual else 'auto'} calibration"
        )
        self.status_var.set(f"Calibration complete - velocity {self.velocity_m_s:.1f} m/s")

    def on_close(self) -> None:
        self.stop_event.set()
        self.root.after(150, self.root.destroy)


def main() -> None:
    root = tk.Tk()
    ThicknessApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
