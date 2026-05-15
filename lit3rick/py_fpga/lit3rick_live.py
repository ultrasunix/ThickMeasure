#!/usr/bin/env python3
"""
Live pulse-echo thickness measurement for a lit3rick controller.

The script acquires 8192 samples at 64 MS/s, calibrates the material velocity
from the first and second back-wall echoes of a known-thickness sample, then
plots the RF signal, its envelope, detected echo positions, and live thickness.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np


try:
    from scipy.signal import butter, find_peaks, hilbert, sosfiltfilt
except Exception:  # pragma: no cover - useful on small Raspberry Pi installs.
    butter = None
    find_peaks = None
    hilbert = None
    sosfiltfilt = None


FS_HZ = 64_000_000.0
DEFAULT_ACQ_US = 50.0


@dataclass
class EchoResult:
    first_idx: int
    second_idx: int
    dt_s: float
    thickness_m: float | None = None
    first_pos: float | None = None
    second_pos: float | None = None


def analytic_envelope(signal: np.ndarray) -> np.ndarray:
    """Return the amplitude envelope using SciPy if available, else FFT Hilbert."""
    x = np.asarray(signal, dtype=float)
    x = x - np.median(x)

    if hilbert is not None:
        return np.abs(hilbert(x))

    n = x.size
    spectrum = np.fft.fft(x)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1.0
        h[1 : n // 2] = 2.0
    else:
        h[0] = 1.0
        h[1 : (n + 1) // 2] = 2.0
    return np.abs(np.fft.ifft(spectrum * h))


def smooth(signal: np.ndarray, points: int) -> np.ndarray:
    if points <= 1:
        return signal
    kernel = np.ones(points, dtype=float) / points
    return np.convolve(signal, kernel, mode="same")


def simple_find_peaks(y: np.ndarray, height: float, distance: int) -> np.ndarray:
    candidates = np.flatnonzero((y[1:-1] > y[:-2]) & (y[1:-1] >= y[2:]) & (y[1:-1] >= height)) + 1
    if candidates.size == 0:
        return candidates

    selected: list[int] = []
    for idx in candidates[np.argsort(y[candidates])[::-1]]:
        if all(abs(idx - prev) >= distance for prev in selected):
            selected.append(int(idx))
    return np.array(sorted(selected), dtype=int)


def refined_peak_position(y: np.ndarray, idx: int, half_window: int = 18) -> float:
    """Estimate echo-envelope peak position with a weighted local centroid."""
    left = max(0, idx - half_window)
    right = min(y.size, idx + half_window + 1)
    window = y[left:right].astype(float)
    if window.size < 3:
        return float(idx)

    floor = float(np.min(window))
    weights = np.maximum(window - floor, 0.0) ** 2
    total = float(np.sum(weights))
    if total <= 0.0:
        return float(idx)

    positions = np.arange(left, right, dtype=float)
    return float(np.sum(positions * weights) / total)


def parabolic_peak_offset(y: np.ndarray, idx: int) -> float:
    if idx <= 0 or idx >= y.size - 1:
        return 0.0
    left = float(y[idx - 1])
    center = float(y[idx])
    right = float(y[idx + 1])
    denom = left - 2.0 * center + right
    if abs(denom) <= 1e-12:
        return 0.0
    return float(0.5 * (left - right) / denom)


def refine_echo_spacing_by_correlation(
    trace: np.ndarray,
    result: EchoResult,
    fs_hz: float,
    half_window_us: float = 0.9,
) -> EchoResult:
    half_window = max(16, int(half_window_us * 1e-6 * fs_hz))
    first = result.first_idx
    second = result.second_idx

    if first - half_window < 0 or second - half_window < 0:
        return result
    if first + half_window >= trace.size or second + half_window >= trace.size:
        return result

    echo_a = trace[first - half_window : first + half_window + 1].astype(float)
    echo_b = trace[second - half_window : second + half_window + 1].astype(float)
    echo_a -= np.mean(echo_a)
    echo_b -= np.mean(echo_b)

    taper = np.hanning(echo_a.size)
    echo_a *= taper
    echo_b *= taper

    norm = np.linalg.norm(echo_a) * np.linalg.norm(echo_b)
    if norm <= 1e-12:
        return result

    corr = np.correlate(echo_b, echo_a, mode="full") / norm
    peak_idx = int(np.argmax(np.abs(corr)))
    offset = parabolic_peak_offset(np.abs(corr), peak_idx)
    lag_samples = peak_idx - (echo_a.size - 1) + offset
    dt_samples = (second - first) + lag_samples
    if dt_samples <= 0:
        return result

    first_pos = float(first)
    second_pos = float(first + dt_samples)
    return EchoResult(
        first_idx=int(round(first_pos)),
        second_idx=int(round(second_pos)),
        dt_s=float(dt_samples / fs_hz),
        first_pos=first_pos,
        second_pos=second_pos,
    )


def robust_median(values: list[float]) -> float:
    data = np.asarray(values, dtype=float)
    if data.size < 4:
        return float(np.median(data))

    center = float(np.median(data))
    mad = float(np.median(np.abs(data - center)))
    if mad <= 1e-12:
        return center

    keep = np.abs(data - center) <= 2.5 * 1.4826 * mad
    if np.any(keep):
        data = data[keep]
    return float(np.median(data))


class ThicknessFilter:
    def __init__(self, median_frames: int, ema_alpha: float, max_jump_mm: float = 0.8) -> None:
        self.values: deque[float] = deque(maxlen=median_frames)
        self.ema_alpha = ema_alpha
        self.max_jump_mm = max_jump_mm
        self.filtered: float | None = None

    def reset(self) -> None:
        self.values.clear()
        self.filtered = None

    def update(self, value: float) -> float:
        if self.filtered is not None and abs(value - self.filtered) > self.max_jump_mm:
            return self.filtered
        self.values.append(value)
        median_value = float(np.median(self.values))
        if self.filtered is None:
            self.filtered = median_value
        else:
            self.filtered = self.ema_alpha * median_value + (1.0 - self.ema_alpha) * self.filtered
        return self.filtered


def detect_backwall_echoes(
    envelope: np.ndarray,
    fs_hz: float,
    gate_start_us: float,
    gate_end_us: float,
    min_echo_spacing_us: float,
    threshold: float,
) -> EchoResult | None:
    start = max(0, int(gate_start_us * 1e-6 * fs_hz))
    end = min(envelope.size, int(gate_end_us * 1e-6 * fs_hz))
    if end <= start + 3:
        return None

    gated = envelope[start:end]
    noise = np.median(gated)
    mad = np.median(np.abs(gated - noise)) + 1e-12
    height = noise + threshold * 1.4826 * mad
    distance = max(1, int(min_echo_spacing_us * 1e-6 * fs_hz))

    if find_peaks is not None:
        peaks, _ = find_peaks(gated, height=height, distance=distance)
    else:
        peaks = simple_find_peaks(gated, height=height, distance=distance)

    if peaks.size < 2:
        return None

    first_two = np.sort(peaks[:2] + start)
    first_pos = refined_peak_position(envelope, int(first_two[0]))
    second_pos = refined_peak_position(envelope, int(first_two[1]))
    dt_s = float((second_pos - first_pos) / fs_hz)
    return EchoResult(
        first_idx=int(round(first_pos)),
        second_idx=int(round(second_pos)),
        dt_s=dt_s,
        first_pos=first_pos,
        second_pos=second_pos,
    )


def detect_backwall_echoes_guided(
    envelope: np.ndarray,
    trace: np.ndarray,
    fs_hz: float,
    gate_start_us: float,
    gate_end_us: float,
    known_thickness_m: float,
    approximate_velocity_m_s: float,
    threshold: float,
    first_echo_min_us: float,
    second_search_fraction: float,
    refine_with_correlation: bool = True,
    min_velocity_ratio: float = 0.85,
    max_velocity_ratio: float = 1.15,
) -> EchoResult | None:
    expected_samples = 2.0 * known_thickness_m / approximate_velocity_m_s * fs_hz
    if expected_samples <= 0:
        return None
    min_spacing_samples = expected_samples / max_velocity_ratio
    max_spacing_samples = expected_samples / min_velocity_ratio

    start = max(int(gate_start_us * 1e-6 * fs_hz), int(first_echo_min_us * 1e-6 * fs_hz), 0)
    end = min(envelope.size, int(gate_end_us * 1e-6 * fs_hz))
    if end <= start + expected_samples:
        return None

    gated = envelope[start:end]
    noise = np.median(gated)
    mad = np.median(np.abs(gated - noise)) + 1e-12
    height = noise + threshold * 1.4826 * mad
    distance = max(1, int(0.75e-6 * fs_hz))

    if find_peaks is not None:
        peaks, _ = find_peaks(gated, height=height, distance=distance)
    else:
        peaks = simple_find_peaks(gated, height=height, distance=distance)
    if peaks.size == 0:
        return None

    peaks = peaks + start
    half_search = max(int(second_search_fraction * expected_samples), int(0.5e-6 * fs_hz))
    best: tuple[float, int, int] | None = None

    for first_idx in peaks:
        expected_second = int(round(first_idx + expected_samples))
        left = max(0, expected_second - half_search)
        right = min(envelope.size, expected_second + half_search + 1)
        if right <= left + 3:
            continue

        second_candidates = peaks[(peaks >= left) & (peaks <= right) & (peaks > first_idx)]
        for second_idx in second_candidates:
            spacing_samples = second_idx - first_idx
            if spacing_samples < min_spacing_samples or spacing_samples > max_spacing_samples:
                continue
            spacing_error = abs(spacing_samples - expected_samples) / expected_samples
            score = float(envelope[first_idx] * envelope[second_idx] / (1.0 + 6.0 * spacing_error))
            if best is None or score > best[0]:
                best = (score, int(first_idx), int(second_idx))

    if best is None:
        return None

    _, first_idx, second_idx = best
    result = EchoResult(
        first_idx=first_idx,
        second_idx=second_idx,
        dt_s=float((second_idx - first_idx) / fs_hz),
        first_pos=float(first_idx),
        second_pos=float(second_idx),
    )
    if refine_with_correlation:
        return refine_echo_spacing_by_correlation(trace, result, fs_hz)
    return result


def calibrate_velocity(
    fpga,
    known_thickness_m: float,
    args: argparse.Namespace,
) -> tuple[float, EchoResult]:
    print(f"Calibrating from {args.calibration_frames} frames on {known_thickness_m * 1e3:.3f} mm sample...")
    spacings = []
    last_result = None

    for frame_no in range(args.calibration_frames):
        trace = acquire_trace(fpga, args)
        env = smooth(analytic_envelope(trace), args.smooth_points)
        result = detect_backwall_echoes_guided(
            env,
            trace,
            args.fs_hz,
            args.gate_start_us,
            args.gate_end_us,
            known_thickness_m,
            args.approximate_velocity_m_s,
            args.threshold,
            args.first_echo_min_us,
            args.second_search_fraction,
            refine_with_correlation=False,
            min_velocity_ratio=args.min_velocity_ratio,
            max_velocity_ratio=args.max_velocity_ratio,
        )
        if result is not None:
            spacings.append(result.dt_s)
            last_result = result
        print(f"\r  usable calibration frames: {len(spacings)}/{frame_no + 1}", end="", flush=True)
        time.sleep(args.interval_s)

    print()
    if not spacings or last_result is None:
        raise RuntimeError("Could not find two back-wall echoes during calibration. Adjust the gate or threshold.")

    dt_s = robust_median(spacings)
    velocity_m_s = 2.0 * known_thickness_m / dt_s
    last_result.dt_s = dt_s
    last_result.thickness_m = known_thickness_m
    print(f"Velocity calibrated: {velocity_m_s:.1f} m/s from echo spacing {dt_s * 1e6:.3f} us")
    return velocity_m_s, last_result


def build_fpga(args: argparse.Namespace):
    """Initialize lit3rick using the common py_fpga interface."""
    if args.simulate:
        return SimulatedFpga(args.fs_hz, args.known_thickness_mm / 1000.0)

    try:
        from smbus2 import SMBus
        import spidev
    except ImportError as exc:
        raise RuntimeError(
            "Missing hardware dependencies. Install/enable smbus2 and spidev, "
            "or run with --simulate to test plotting."
        ) from exc

    try:
        from py_fpga import py_fpga as py_fpga_cls
    except ImportError as exc:
        raise RuntimeError(
            "Could not import py_fpga. Run this script from ~/lit3rick/py_fpga "
            "or add the lit3rick Python module to PYTHONPATH."
        ) from exc

    spi = spidev.SpiDev()
    spi.open(args.spi_bus, args.spi_device)
    spi.max_speed_hz = args.spi_speed_hz
    spi.mode = 0

    i2c_bus = SMBus(args.i2c_bus)
    fpga = py_fpga_cls(i2c_bus=i2c_bus, py_audio=None, spi_bus=spi)
    fpga._lit3rick_handles = (spi, i2c_bus)

    fpga.set_waveform(
        pdelay=args.pdelay,
        PHV_time=args.phv_time,
        PnHV_time=args.pnhv_time,
        PDamp_time=args.pdamp_time,
    )
    fpga.set_HILO(args.hilo)
    fpga.set_dac(args.dac)

    for i in range(args.tgc_points):
        val = int(args.tgc_start + (i * (args.tgc_end - args.tgc_start)) / max(1, args.tgc_points - 1))
        fpga.set_dac(val, mem=i)

    return fpga


def acquire_trace(fpga, args: argparse.Namespace | None = None) -> np.ndarray:
    if hasattr(fpga, "capture_signal"):
        fpga.capture_signal()
        if args is not None:
            time.sleep(args.capture_wait_s)

    data = fpga.read_signal_through_spi()
    trace = np.asarray(data, dtype=float)
    if trace.ndim > 1:
        trace = trace.ravel()
    if args is not None and args.samples > 0:
        trace = trace[: args.samples]
    baseline = np.mean(trace[-500:]) if trace.size > 500 else np.mean(trace)
    trace = trace - baseline
    if args is not None:
        low_hz = getattr(args, "bandpass_low_hz", 0.0)
        high_hz = getattr(args, "bandpass_high_hz", 0.0)
        if low_hz > 0.0 or high_hz > 0.0:
            trace = band_pass_filter(trace, args.fs_hz, low_hz, high_hz)
        elif getattr(args, "highpass_hz", 0.0) > 0.0:
            trace = high_pass_filter(trace, args.fs_hz, args.highpass_hz)
    return trace


def band_pass_filter(trace: np.ndarray, fs_hz: float, low_hz: float, high_hz: float) -> np.ndarray:
    nyquist = 0.5 * fs_hz
    low_hz = max(0.0, float(low_hz))
    high_hz = min(float(high_hz), nyquist * 0.98) if high_hz > 0.0 else nyquist * 0.98

    if trace.size < 16 or (low_hz <= 0.0 and high_hz >= nyquist * 0.98):
        return trace

    if butter is not None and sosfiltfilt is not None:
        if low_hz > 0.0:
            sos = butter(3, [low_hz, high_hz], btype="bandpass", fs=fs_hz, output="sos")
        else:
            sos = butter(3, high_hz, btype="lowpass", fs=fs_hz, output="sos")
        return sosfiltfilt(sos, trace)

    spectrum = np.fft.rfft(trace)
    freqs = np.fft.rfftfreq(trace.size, d=1.0 / fs_hz)
    if low_hz > 0.0:
        spectrum[freqs < low_hz] = 0.0
    spectrum[freqs > high_hz] = 0.0
    return np.fft.irfft(spectrum, n=trace.size)


def high_pass_filter(trace: np.ndarray, fs_hz: float, cutoff_hz: float) -> np.ndarray:
    if cutoff_hz <= 0.0 or trace.size < 4:
        return trace
    spectrum = np.fft.rfft(trace)
    freqs = np.fft.rfftfreq(trace.size, d=1.0 / fs_hz)
    spectrum[freqs < cutoff_hz] = 0.0
    return np.fft.irfft(spectrum, n=trace.size)


class SimulatedFpga:
    def __init__(self, fs_hz: float, thickness_m: float) -> None:
        self.fs_hz = fs_hz
        self.thickness_m = thickness_m
        self.velocity_m_s = 5900.0
        self.n = 8192
        self.frame = 0

    def read_signal_through_spi(self):
        self.frame += 1
        t = np.arange(self.n) / self.fs_hz
        carrier = 5.0e6
        sigma = 0.18e-6
        spacing = 2.0 * self.thickness_m / self.velocity_m_s
        first = 8.0e-6
        second = first + spacing
        drift = 0.08e-3 * np.sin(self.frame / 30.0)
        second += 2.0 * drift / self.velocity_m_s
        sig = np.random.normal(0.0, 0.025, self.n)
        for amp, center in ((1.0, first), (0.65, second), (0.35, second + spacing)):
            sig += amp * np.sin(2 * np.pi * carrier * (t - center)) * np.exp(-0.5 * ((t - center) / sigma) ** 2)
        return sig * 2048 + 2048


def setup_plot(args: argparse.Namespace):
    plt.ion()
    fig, (ax_rf, ax_thick) = plt.subplots(2, 1, figsize=(11, 7), constrained_layout=True)
    ax_env = ax_rf.twinx()

    ax_rf.set_title("lit3rick live thickness measurement")
    ax_rf.set_xlabel("time (us)")
    ax_rf.set_ylabel("RF amplitude")
    ax_env.set_ylabel("envelope")
    ax_thick.set_xlabel("frame")
    ax_thick.set_ylabel("thickness (mm)")

    time_us = np.arange(args.samples) / args.fs_hz * 1e6
    (rf_line,) = ax_rf.plot(time_us, np.zeros(args.samples), lw=0.8, label="RF")
    (env_line,) = ax_env.plot(time_us, np.zeros(args.samples), color="tab:orange", lw=1.1, label="Envelope")
    peak_lines = [
        ax_rf.axvline(0, color="tab:red", ls="--", lw=1.0, alpha=0.8),
        ax_rf.axvline(0, color="tab:red", ls="--", lw=1.0, alpha=0.8),
    ]
    (thick_line,) = ax_thick.plot([], [], color="tab:green", lw=1.5)
    text = ax_rf.text(0.01, 0.95, "", transform=ax_rf.transAxes, va="top")

    ax_rf.set_xlim(args.display_start_us, args.display_end_us)
    ax_thick.set_ylim(args.thickness_ymin_mm, args.thickness_ymax_mm)
    return fig, ax_rf, ax_env, ax_thick, rf_line, env_line, peak_lines, thick_line, text


def live_loop(fpga, velocity_m_s: float, args: argparse.Namespace) -> None:
    thickness_filter = ThicknessFilter(args.thickness_median_frames, args.thickness_ema_alpha)
    if args.no_plot:
        for frame in range(1, args.frames + 1):
            trace = acquire_trace(fpga, args)
            env = smooth(analytic_envelope(trace), args.smooth_points)
            result = detect_backwall_echoes(
                env,
                args.fs_hz,
                args.gate_start_us,
                args.gate_end_us,
                args.min_echo_spacing_us,
                args.threshold,
            )
            if result is not None and args.timing_method == "xcorr":
                result = refine_echo_spacing_by_correlation(trace, result, args.fs_hz, args.xcorr_half_window_us)
            if result is None:
                print(f"frame {frame}: echoes not detected")
            else:
                raw_thickness_mm = velocity_m_s * result.dt_s * 500.0
                thickness_mm = thickness_filter.update(raw_thickness_mm)
                print(
                    f"frame {frame}: thickness={thickness_mm:.3f} mm, "
                    f"raw={raw_thickness_mm:.3f} mm, "
                    f"dt={result.dt_s * 1e6:.3f} us, "
                    f"echoes=({result.first_idx}, {result.second_idx})"
                )
            time.sleep(args.interval_s)
        return

    fig, ax_rf, ax_env, ax_thick, rf_line, env_line, peak_lines, thick_line, text = setup_plot(args)
    thickness_history: deque[float] = deque(maxlen=args.history)
    frame_history: deque[int] = deque(maxlen=args.history)
    frame = 0

    while plt.fignum_exists(fig.number):
        frame += 1
        trace = acquire_trace(fpga, args)
        if trace.size != args.samples:
            args.samples = trace.size
            time_us = np.arange(args.samples) / args.fs_hz * 1e6
            rf_line.set_xdata(time_us)
            env_line.set_xdata(time_us)

        env = smooth(analytic_envelope(trace), args.smooth_points)
        result = detect_backwall_echoes(
            env,
            args.fs_hz,
            args.gate_start_us,
            args.gate_end_us,
            args.min_echo_spacing_us,
            args.threshold,
        )
        if result is not None and args.timing_method == "xcorr":
            result = refine_echo_spacing_by_correlation(trace, result, args.fs_hz, args.xcorr_half_window_us)

        rf_line.set_ydata(trace)
        env_line.set_ydata(env)
        ax_rf.relim()
        ax_rf.autoscale_view(scalex=False, scaley=True)
        ax_env.relim()
        ax_env.autoscale_view(scalex=False, scaley=True)

        if result is not None:
            raw_thickness_mm = velocity_m_s * result.dt_s * 500.0
            thickness_mm = thickness_filter.update(raw_thickness_mm)
            thickness_history.append(thickness_mm)
            frame_history.append(frame)
            for line, idx in zip(peak_lines, (result.first_idx, result.second_idx)):
                line.set_xdata([idx / args.fs_hz * 1e6, idx / args.fs_hz * 1e6])
            text.set_text(
                f"velocity: {velocity_m_s:.1f} m/s\n"
                f"echo spacing: {result.dt_s * 1e6:.3f} us\n"
                f"thickness: {thickness_mm:.3f} mm\n"
                f"raw: {raw_thickness_mm:.3f} mm"
            )
        else:
            text.set_text("echoes not detected - adjust gate/threshold")

        thick_line.set_data(list(frame_history), list(thickness_history))
        ax_thick.relim()
        ax_thick.autoscale_view(scalex=True, scaley=False)

        fig.canvas.draw_idle()
        plt.pause(max(0.001, args.interval_s))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--known-thickness-mm", type=float, default=20.0)
    parser.add_argument("--fs-hz", type=float, default=FS_HZ)
    parser.add_argument("--acq-us", type=float, default=DEFAULT_ACQ_US)
    parser.add_argument("--samples", type=int, default=0, help="samples to process; 0 means fs * acq-us")
    parser.add_argument("--calibration-frames", type=int, default=25)
    parser.add_argument("--gate-start-us", type=float, default=2.0)
    parser.add_argument("--gate-end-us", type=float, default=DEFAULT_ACQ_US)
    parser.add_argument("--display-start-us", type=float, default=0.0)
    parser.add_argument("--display-end-us", type=float, default=20.0)
    parser.add_argument("--min-echo-spacing-us", type=float, default=3.0)
    parser.add_argument("--threshold", type=float, default=8.0, help="MAD multiplier above the envelope noise floor")
    parser.add_argument("--smooth-points", type=int, default=7)
    parser.add_argument("--interval-s", type=float, default=0.10)
    parser.add_argument("--capture-wait-s", type=float, default=0.001)
    parser.add_argument("--highpass-hz", type=float, default=0.0)
    parser.add_argument("--bandpass-low-hz", type=float, default=800_000.0)
    parser.add_argument("--bandpass-high-hz", type=float, default=12_000_000.0)
    parser.add_argument("--history", type=int, default=250)
    parser.add_argument("--thickness-ymin-mm", type=float, default=15.0)
    parser.add_argument("--thickness-ymax-mm", type=float, default=30.0)
    parser.add_argument("--thickness-median-frames", type=int, default=9)
    parser.add_argument("--thickness-ema-alpha", type=float, default=0.25)
    parser.add_argument("--timing-method", choices=("envelope", "xcorr"), default="xcorr")
    parser.add_argument("--xcorr-half-window-us", type=float, default=0.9)
    parser.add_argument("--approximate-velocity-m-s", type=float, default=5850.0)
    parser.add_argument("--first-echo-min-us", type=float, default=4.0)
    parser.add_argument("--second-search-fraction", type=float, default=0.20)
    parser.add_argument("--min-velocity-ratio", type=float, default=0.85)
    parser.add_argument("--max-velocity-ratio", type=float, default=1.15)
    parser.add_argument("--frames", type=int, default=100, help="number of frames for --no-plot mode")
    parser.add_argument("--no-plot", action="store_true", help="print live measurements instead of opening matplotlib")
    parser.add_argument("--simulate", action="store_true")

    parser.add_argument("--i2c-bus", type=int, default=1)
    parser.add_argument("--spi-bus", type=int, default=0)
    parser.add_argument("--spi-device", type=int, default=0)
    parser.add_argument("--spi-speed-hz", type=int, default=1_000_000)
    parser.add_argument("--pdelay", type=int, default=1)
    parser.add_argument("--phv-time", type=int, default=5)
    parser.add_argument("--pnhv-time", type=int, default=5)
    parser.add_argument("--pdamp-time", type=int, default=8)
    parser.add_argument("--hilo", type=int, default=1)
    parser.add_argument("--dac", type=int, default=300)
    parser.add_argument("--tgc-start", type=int, default=250)
    parser.add_argument("--tgc-end", type=int, default=455)
    parser.add_argument("--tgc-points", type=int, default=16)
    args = parser.parse_args()
    if args.samples <= 0:
        args.samples = int(args.fs_hz * args.acq_us * 1e-6)
    return args


def close_fpga(fpga) -> None:
    handles = getattr(fpga, "_lit3rick_handles", ())
    for handle in handles:
        try:
            handle.close()
        except Exception:
            pass


def main() -> int:
    args = parse_args()
    try:
        fpga = build_fpga(args)
        velocity_m_s, _ = calibrate_velocity(fpga, args.known_thickness_mm / 1000.0, args)
        live_loop(fpga, velocity_m_s, args)
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if "fpga" in locals():
            close_fpga(fpga)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
