## ThickMeasureCN v0.1.0

First Raspberry Pi Linux installer release for the Chinese ThickMeasure application.

### Installer

- Adds `ThickMeasureCN_0.1.0_all.deb` for Raspberry Pi OS / Debian-based Linux.
- Installs the Chinese `ThickMeasureCN` touchscreen app.
- Installs required Linux and Python packages through `apt`.
- Builds WiringPi automatically when `libwiringPi.so` is missing.
- Builds and installs `lit3prog` into `/usr/local/bin/lit3prog`.
- Enables SPI and I2C with `raspi-config` when available.
- Installs and enables the `thickmeasure-lit3rick.service` boot service so `prog_ram.sh` runs when the Raspberry Pi starts.
- Creates the `ThickMeasureCN` desktop launcher and autostart entry.

### Install

Download `ThickMeasureCN_0.1.0_all.deb` from this release, then run:

```bash
sudo apt install ./ThickMeasureCN_0.1.0_all.deb
sudo reboot
```

After reboot, check that the lit3rick board appears on I2C:

```bash
i2cdetect -y 1
```

The expected lit3rick address is `0x25`.

### Notes

- The Pi needs internet access during installation because WiringPi may be cloned and built automatically.
- Hardware wiring, probe coupling, and lit3rick board connection must be correct for `0x25` to appear.
