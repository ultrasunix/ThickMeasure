#!/usr/bin/env bash
set -euo pipefail

APP_NAME="ThickMeasure"
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
INSTALL_DIR="$TARGET_HOME/ThickMeasure"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$TARGET_HOME/Desktop"
AUTOSTART_DIR="$TARGET_HOME/.config/autostart"
SUDOERS_FILE="/etc/sudoers.d/thickmeasure-prog-ram"
SYSTEMD_SERVICE="/etc/systemd/system/thickmeasure-lit3rick.service"

if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
  echo "Could not determine home directory for user: $TARGET_USER" >&2
  exit 1
fi

INSTALL_DIR_RESOLVED="$(mkdir -p "$INSTALL_DIR" && cd "$INSTALL_DIR" && pwd)"

echo "Installing system packages..."
sudo apt-get update
sudo apt-get install -y \
  python3 \
  python3-tk \
  python3-numpy \
  python3-scipy \
  python3-matplotlib \
  python3-pil \
  python3-smbus2 \
  python3-spidev \
  i2c-tools \
  git \
  build-essential

if command -v raspi-config >/dev/null 2>&1; then
  echo "Enabling SPI and I2C..."
  sudo raspi-config nonint do_spi 0 || true
  sudo raspi-config nonint do_i2c 0 || true
else
  echo "raspi-config not found; please enable SPI and I2C manually if needed."
fi

echo "Checking WiringPi runtime for lit3prog..."
if ! ldconfig -p 2>/dev/null | grep -q 'libwiringPi\.so'; then
  WIRINGPI_BUILD_DIR="$(mktemp -d)"
  echo "libwiringPi.so not found; building WiringPi in $WIRINGPI_BUILD_DIR..."
  git clone --depth 1 https://github.com/WiringPi/WiringPi.git "$WIRINGPI_BUILD_DIR"
  (
    cd "$WIRINGPI_BUILD_DIR"
    ./build
  )
  rm -rf "$WIRINGPI_BUILD_DIR"
  sudo ldconfig
else
  echo "WiringPi library already available."
fi

echo "Installing lit3rick GPIO programming helper..."
sudo tee /usr/local/bin/gpio >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

map_wiringpi_to_bcm() {
  case "$1" in
    0) echo 17 ;; 1) echo 18 ;; 2) echo 27 ;; 3) echo 22 ;; 4) echo 23 ;;
    5) echo 24 ;; 6) echo 25 ;; 7) echo 4 ;; 8) echo 2 ;; 9) echo 3 ;;
    10) echo 8 ;; 11) echo 7 ;; 12) echo 10 ;; 13) echo 9 ;; 14) echo 11 ;;
    15) echo 14 ;; 16) echo 15 ;; 21) echo 5 ;; 22) echo 6 ;; 23) echo 13 ;;
    24) echo 19 ;; 25) echo 26 ;; 26) echo 12 ;; 27) echo 16 ;; 28) echo 20 ;;
    29) echo 21 ;;
    *) echo "Unsupported WiringPi pin: $1" >&2; exit 2 ;;
  esac
}

set_pin() {
  local bcm="$1"
  local mode="$2"
  if command -v pinctrl >/dev/null 2>&1; then
    pinctrl set "$bcm" "$mode"
  elif command -v raspi-gpio >/dev/null 2>&1; then
    case "$mode" in
      op) raspi-gpio set "$bcm" op ;;
      ip) raspi-gpio set "$bcm" ip ;;
      a0) raspi-gpio set "$bcm" a0 ;;
      dh) raspi-gpio set "$bcm" dh ;;
      dl) raspi-gpio set "$bcm" dl ;;
      *) echo "Unsupported GPIO mode: $mode" >&2; exit 2 ;;
    esac
  else
    echo "Neither pinctrl nor raspi-gpio is available; cannot control GPIO pins." >&2
    exit 127
  fi
}

if [[ $# -lt 3 ]]; then
  echo "Usage: gpio mode <wiringpi-pin> <IN|OUT|alt0> | gpio write <wiringpi-pin> <0|1>" >&2
  exit 2
fi

command_name="$1"
pin="$2"
bcm="$(map_wiringpi_to_bcm "$pin")"

case "$command_name" in
  mode)
    case "${3,,}" in
      out) set_pin "$bcm" op ;;
      in) set_pin "$bcm" ip ;;
      alt0) set_pin "$bcm" a0 ;;
      *) echo "Unsupported gpio mode: $3" >&2; exit 2 ;;
    esac
    ;;
  write)
    case "$3" in
      1) set_pin "$bcm" dh ;;
      0) set_pin "$bcm" dl ;;
      *) echo "Unsupported gpio write value: $3" >&2; exit 2 ;;
    esac
    ;;
  *)
    echo "Unsupported gpio command: $command_name" >&2
    exit 2
    ;;
esac
EOF
sudo chown root:root /usr/local/bin/gpio
sudo chmod 0755 /usr/local/bin/gpio
echo "Installed /usr/local/bin/gpio compatibility wrapper for lit3rick prog_ram.sh."

echo "Installing application into $INSTALL_DIR..."
if [[ "$SOURCE_DIR" == "$INSTALL_DIR_RESOLVED" ]]; then
  echo "Source directory is already $INSTALL_DIR; installing in place."
else
  sudo install -d -o "$TARGET_USER" -g "$TARGET_USER" "$INSTALL_DIR"
  sudo rm -rf "$INSTALL_DIR/app.py" "$INSTALL_DIR/app_ch.py" "$INSTALL_DIR/logo.png" "$INSTALL_DIR/logo-app.png" "$INSTALL_DIR/run_ThickMeasure.sh" "$INSTALL_DIR/run_ThickMeasure_CH.sh" "$INSTALL_DIR/ThickMeasure.desktop" "$INSTALL_DIR/ThickMeasure_CH.desktop" "$INSTALL_DIR/lit3rick"
  sudo cp -a "$SOURCE_DIR/app.py" "$SOURCE_DIR/app_ch.py" "$SOURCE_DIR/logo.png" "$SOURCE_DIR/logo-app.png" "$SOURCE_DIR/run_ThickMeasure.sh" "$SOURCE_DIR/run_ThickMeasure_CH.sh" "$SOURCE_DIR/ThickMeasure.desktop" "$SOURCE_DIR/ThickMeasure_CH.desktop" "$SOURCE_DIR/lit3rick" "$INSTALL_DIR/"
  sudo chown -R "$TARGET_USER:$TARGET_USER" "$INSTALL_DIR"
fi

chmod +x "$INSTALL_DIR/run_ThickMeasure.sh" "$INSTALL_DIR/run_ThickMeasure_CH.sh"
chmod +x "$INSTALL_DIR/lit3rick/program/prog_ram.sh" "$INSTALL_DIR/lit3rick/program/prog_flash.sh" || true
chmod +x "$INSTALL_DIR/lit3rick/program/lit3prog" "$INSTALL_DIR/lit3rick/program/utilities/lit3prog" || true

echo "Building and installing lit3rick FPGA programmer..."
if [[ ! -f "$INSTALL_DIR/lit3rick/program/lit3prog.cc" ]]; then
  echo "Could not find lit3prog.cc source." >&2
  exit 1
fi
(
  cd "$INSTALL_DIR/lit3rick/program"
  gcc -o lit3prog -Wall -Os lit3prog.cc -lwiringPi -lrt -lstdc++
)
sudo install -o root -g root -m 0755 "$INSTALL_DIR/lit3rick/program/lit3prog" /usr/local/bin/lit3prog
sudo chown root:root /usr/local/bin/lit3prog
sudo chmod 0755 /usr/local/bin/lit3prog
if [[ ! -x /usr/local/bin/lit3prog ]]; then
  echo "/usr/local/bin/lit3prog is not executable after installation." >&2
  exit 1
fi
if command -v ldd >/dev/null 2>&1 && ldd /usr/local/bin/lit3prog 2>/dev/null | grep -q 'not found'; then
  echo "/usr/local/bin/lit3prog has a missing shared library. Check WiringPi installation." >&2
  ldd /usr/local/bin/lit3prog >&2 || true
  exit 1
fi

echo "Allowing the app to program the lit3rick board without storing a password..."
echo "$TARGET_USER ALL=(root) NOPASSWD: $INSTALL_DIR/lit3rick/program/prog_ram.sh" | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" >/dev/null

echo "Creating boot-time lit3rick programming service..."
sudo tee "$SYSTEMD_SERVICE" >/dev/null <<EOF
[Unit]
Description=Program lit3rick FPGA RAM for ThickMeasure
After=local-fs.target
Wants=local-fs.target

[Service]
Type=oneshot
WorkingDirectory=$INSTALL_DIR/lit3rick/program
ExecStart=$INSTALL_DIR/lit3rick/program/prog_ram.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable thickmeasure-lit3rick.service

echo "Creating desktop launcher and autostart entry..."
install_english_desktop_file() {
  local output_path="$1"
  cat > "$output_path" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Ultrasonic thickness measurement
Exec=$INSTALL_DIR/run_ThickMeasure.sh
Path=$INSTALL_DIR
Icon=$INSTALL_DIR/logo.png
Terminal=false
Categories=Science;
EOF
  chmod +x "$output_path"
  chown "$TARGET_USER:$TARGET_USER" "$output_path"
}

install_chinese_desktop_file() {
  local output_path="$1"
  cat > "$output_path" <<EOF
[Desktop Entry]
Type=Application
Name=ThickMeasureCN
Comment=Chinese ultrasonic thickness measurement
Exec=$INSTALL_DIR/run_ThickMeasure_CH.sh
Path=$INSTALL_DIR
Icon=$INSTALL_DIR/logo.png
Terminal=false
Categories=Science;
EOF
  chmod +x "$output_path"
  chown "$TARGET_USER:$TARGET_USER" "$output_path"
}

sudo install -d -o "$TARGET_USER" -g "$TARGET_USER" "$DESKTOP_DIR" "$AUTOSTART_DIR"
install_english_desktop_file "$INSTALL_DIR/ThickMeasure.desktop"
install_english_desktop_file "$DESKTOP_DIR/ThickMeasure.desktop"
install_english_desktop_file "$AUTOSTART_DIR/ThickMeasure.desktop"
install_chinese_desktop_file "$INSTALL_DIR/ThickMeasure_CH.desktop"
install_chinese_desktop_file "$DESKTOP_DIR/ThickMeasureCN.desktop"

echo "Checking Python files..."
python3 -m py_compile "$INSTALL_DIR/app.py" "$INSTALL_DIR/app_ch.py" "$INSTALL_DIR/lit3rick/py_fpga/lit3rick_thickness_live.py" "$INSTALL_DIR/lit3rick/py_fpga/py_fpga.py"

echo
echo "Installation complete."
echo "Reboot recommended if SPI/I2C was just enabled:"
echo "  sudo reboot"
echo
echo "After reboot, ThickMeasure should start automatically."
echo "Manual launch:"
echo "  $INSTALL_DIR/run_ThickMeasure.sh"
echo "Chinese manual launch:"
echo "  $INSTALL_DIR/run_ThickMeasure_CH.sh"
