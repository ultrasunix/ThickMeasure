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

if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
  echo "Could not determine home directory for user: $TARGET_USER" >&2
  exit 1
fi

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
  git

if command -v raspi-config >/dev/null 2>&1; then
  echo "Enabling SPI and I2C..."
  sudo raspi-config nonint do_spi 0 || true
  sudo raspi-config nonint do_i2c 0 || true
else
  echo "raspi-config not found; please enable SPI and I2C manually if needed."
fi

echo "Installing application into $INSTALL_DIR..."
sudo install -d -o "$TARGET_USER" -g "$TARGET_USER" "$INSTALL_DIR"
sudo rm -rf "$INSTALL_DIR/app.py" "$INSTALL_DIR/logo.png" "$INSTALL_DIR/logo-app.png" "$INSTALL_DIR/run_ThickMeasure.sh" "$INSTALL_DIR/ThickMeasure.desktop" "$INSTALL_DIR/lit3rick"
sudo cp -a "$SOURCE_DIR/app.py" "$SOURCE_DIR/logo.png" "$SOURCE_DIR/logo-app.png" "$SOURCE_DIR/run_ThickMeasure.sh" "$SOURCE_DIR/ThickMeasure.desktop" "$SOURCE_DIR/lit3rick" "$INSTALL_DIR/"
sudo chown -R "$TARGET_USER:$TARGET_USER" "$INSTALL_DIR"

chmod +x "$INSTALL_DIR/run_ThickMeasure.sh"
chmod +x "$INSTALL_DIR/lit3rick/program/prog_ram.sh" "$INSTALL_DIR/lit3rick/program/prog_flash.sh" || true
chmod +x "$INSTALL_DIR/lit3rick/program/lit3prog" "$INSTALL_DIR/lit3rick/program/utilities/lit3prog" || true

echo "Allowing the app to program the lit3rick board without storing a password..."
echo "$TARGET_USER ALL=(root) NOPASSWD: $INSTALL_DIR/lit3rick/program/prog_ram.sh" | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" >/dev/null

echo "Creating desktop launcher and autostart entry..."
install_desktop_file() {
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

sudo install -d -o "$TARGET_USER" -g "$TARGET_USER" "$DESKTOP_DIR" "$AUTOSTART_DIR"
install_desktop_file "$INSTALL_DIR/ThickMeasure.desktop"
install_desktop_file "$DESKTOP_DIR/ThickMeasure.desktop"
install_desktop_file "$AUTOSTART_DIR/ThickMeasure.desktop"

echo "Checking Python files..."
python3 -m py_compile "$INSTALL_DIR/app.py" "$INSTALL_DIR/lit3rick/py_fpga/lit3rick_thickness_live.py" "$INSTALL_DIR/lit3rick/py_fpga/py_fpga.py"

echo
echo "Installation complete."
echo "Reboot recommended if SPI/I2C was just enabled:"
echo "  sudo reboot"
echo
echo "After reboot, ThickMeasure should start automatically."
echo "Manual launch:"
echo "  $INSTALL_DIR/run_ThickMeasure.sh"
