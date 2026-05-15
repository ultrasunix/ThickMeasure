# ThickMeasure

ThickMeasure is a Raspberry Pi touchscreen app for pulse-echo ultrasonic thickness measurement using a lit3rick single-channel ultrasonic board.

The app can:

- program the lit3rick FPGA RAM automatically when the app opens
- acquire ultrasonic RF traces from lit3rick over SPI/I2C
- show a high-contrast outdoor-friendly live plot
- calibrate sound velocity from a reference sample
- track the selected first and second back-wall echoes during measurement
- save the last 10 thickness readings to CSV

## Hardware

- Raspberry Pi 5, or another Raspberry Pi with SPI and I2C enabled
- lit3rick ultrasonic controller board
- ultrasonic probe connected to the lit3rick board
- optional 5.5 inch or 7 inch Raspberry Pi touchscreen

## Repository Contents

```text
app.py                         ThickMeasure GUI app
run_thickapp.sh                app launcher
setup.sh                       Raspberry Pi installer
logo.png                       blue GMRI logo used by the desktop launcher
logo-app.png                   yellow GMRI logo used inside the app as Close
thickapp.desktop               desktop launcher template
lit3rick/program               lit3rick FPGA programming files
lit3rick/py_fpga               lit3rick Python control and acquisition files
```

## Install On A New Raspberry Pi

Clone this repository on the Raspberry Pi:

```bash
cd ~
git clone https://github.com/ultrasunix/ThickMeasure.git
cd ThickMeasure
```

Run the setup script:

```bash
bash setup.sh
```

The installer will:

- install required Python and Raspberry Pi packages
- enable SPI and I2C using `raspi-config` when available
- copy the app into `/home/<user>/thickapp`
- copy the bundled lit3rick `program` and `py_fpga` folders
- create a passwordless sudo rule only for `prog_ram.sh`
- create a desktop launcher
- create an autostart entry so ThickMeasure opens after desktop login

Reboot after installation:

```bash
sudo reboot
```

## Manual Launch

If the app does not open automatically, run:

```bash
~/thickapp/run_thickapp.sh
```

or double-click the `ThickMeasure` desktop icon.

## Typical Use

1. Connect the lit3rick board, probe, and sample.
2. Open ThickMeasure.
3. Wait for the status message `Board ready - run Calibration`.
4. Enter `Ref. Thickness` in mm.
5. Enter `Ref. Velocity` in m/s. This is only a reference value to help locate echoes.
6. Press `Calibration`.
7. Check the two red cursors on the first and second back-wall echoes.
8. If needed, tap the first and second back-wall echoes on the plot to adjust the cursors.
9. Press `Start`.
10. Press `Stop` when finished.
11. Press `Save` to save the most recent thickness readings.

## Touchscreen Numeric Keypad

Tap the `Ref. Thickness` or `Ref. Velocity` input box to open the built-in numeric keypad.

The keypad supports:

- digits
- decimal point
- backspace
- clear
- cancel
- OK

## Saved Data

CSV files are saved in:

```text
/home/<user>/thickapp/sdata-ThickMeasure
```

File names use this format:

```text
sdata-thickness-YYYY-MM-DD-HH-MM-SS.csv
```

Each CSV contains exactly 10 rows. If fewer than 10 measurements were collected, the remaining rows are filled with zeros.

## Notes On Calibration

For pulse-echo measurement, the ultrasound travels to the back wall and returns, so the path length is:

```text
2 * thickness
```

The calibrated velocity is calculated from:

```text
velocity = 2 * reference_thickness / echo_spacing_time
```

The reference velocity does not need to be exact. It only helps the app locate a reasonable first and second back-wall echo pair.

## Troubleshooting

### Remote I/O error

This usually means the lit3rick board is not programmed or the I2C/SPI connection is not ready.

The app tries to run this automatically:

```bash
sudo /home/<user>/thickapp/lit3rick/program/prog_ram.sh
```

You can test it manually:

```bash
cd ~/thickapp/lit3rick/program
sudo ./prog_ram.sh
```

Successful programming should show:

```text
cdone: high
```

### SPI or I2C not enabled

Run:

```bash
sudo raspi-config
```

Then enable:

- Interface Options > SPI
- Interface Options > I2C

Reboot afterwards.

### App does not start on boot

Check the autostart entry:

```bash
ls ~/.config/autostart/ThickMeasure.desktop
```

Manual launch:

```bash
~/thickapp/run_thickapp.sh
```

### No echoes detected

Check:

- probe connection
- couplant
- lit3rick programming status
- gain/contact condition
- whether the first and second back-wall cursors are correctly selected after calibration
