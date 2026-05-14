#!/usr/bin/python3
import sys
sys.path.append("/home/pi/pic0env/lib/python3.13/site-packages")

import time
import signal
import numpy as np
import matplotlib.pyplot as plt
from smbus2 import SMBus
import spidev
from py_fpga import py_fpga

# -----------------------------
# User settings
# -----------------------------
I2C_BUS_ID = 1
SPI_BUS = 0
SPI_DEVICE = 0

FS_MHZ = 64.0
ACQ_US = 50.0
REFRESH_S = 0.5

N_SAMPLES = int(FS_MHZ * ACQ_US)  # 64 samples/us × 50 us = 3200

# Approx. 5 MHz bipolar pulse
PDELAY = 1
PHV_TIME = 5
PNHV_TIME = 5
PDAMP_TIME = 8

# Gain settings
HILO_VAL = 1
DAC_VAL = 300          # try 100–450 depending on signal amplitude
DAC_START = 250
DAC_END = 450
N_DAC_SEGMENTS = 16

running = True


def stop_handler(sig, frame):
    global running
    running = False


signal.signal(signal.SIGINT, stop_handler)


def main():
    print("Opening I2C and SPI...")

    i2c_bus = SMBus(I2C_BUS_ID)

    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = 1_000_000
    spi.mode = 0

    fpga = py_fpga(i2c_bus=i2c_bus, spi_bus=spi, py_audio=None)

    print("Configuring pulse waveform...")
    fpga.set_waveform(
        pdelay=PDELAY,
        PHV_time=PHV_TIME,
        PnHV_time=PNHV_TIME,
        PDamp_time=PDAMP_TIME
    )

    effective_freq_mhz = FS_MHZ / (PHV_TIME + PNHV_TIME)
    print(f"Approximate pulse centre frequency: {effective_freq_mhz:.3f} MHz")

    print("Configuring gain...")
    for i in range(N_DAC_SEGMENTS):
        dac_i = int(DAC_START + i * (DAC_END - DAC_START) / (N_DAC_SEGMENTS - 1))
        fpga.set_dac(dac_i, mem=i)

    fpga.set_HILO(HILO_VAL)
    fpga.set_dac(DAC_VAL)

    t_us = np.arange(N_SAMPLES) / FS_MHZ

    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 4))
    line, = ax.plot(t_us, np.zeros(N_SAMPLES))
    ax.set_xlabel("Time (µs)")
    ax.set_ylabel("ADC raw value")
    ax.set_title("lit3rick raw ultrasonic acquisition, SPI readout")
    ax.grid(True)

    print("Starting continuous acquisition. Press Ctrl+C to stop.")

    while running:
        t0 = time.time()

        # Trigger acquisition through I2C
        fpga.capture_signal()

        # Acquisition window is 128 us, but a short wait is safe
        time.sleep(0.001)

        # Read full 8192-sample memory through SPI
        raw = fpga.read_signal_through_spi()

        # Keep first 50 us
        data = np.asarray(raw[:N_SAMPLES], dtype=np.int16)

        # Remove DC offset for display only
        data_display = data - np.mean(data[-500:])

        line.set_ydata(data_display)

        ymin = np.min(data_display)
        ymax = np.max(data_display)
        if ymin == ymax:
            ymin -= 1
            ymax += 1

        ax.set_ylim(ymin * 1.2, ymax * 1.2)
        fig.canvas.draw()
        fig.canvas.flush_events()

        print(
            f"Peak-to-peak: {np.ptp(data_display):.0f}, "
            f"min: {np.min(data):.0f}, max: {np.max(data):.0f}"
        )

        elapsed = time.time() - t0
        time.sleep(max(0.0, REFRESH_S - elapsed))

    print("Stopping...")
    spi.close()
    i2c_bus.close()


if __name__ == "__main__":
    main()