#!/bin/bash
###############################################################################
# MouseBridge Pi installer - run as root on the Pi (Zero 2 W or any
# gadget-capable model) AFTER enabling dwc2 (see README):
#
#   sudo bash pi/install.sh
#
# Installs the gadget + pump + a DHCP server for the USB network link,
# enables everything at boot, and starts it now.
###############################################################################
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash pi/install.sh" >&2
    exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"

if ! grep -q '^dtoverlay=dwc2' /boot/firmware/config.txt /boot/config.txt 2>/dev/null; then
    echo "WARNING: dtoverlay=dwc2 not found in boot config - adding it."
    CFG=/boot/firmware/config.txt
    [ -f "$CFG" ] || CFG=/boot/config.txt
    echo "dtoverlay=dwc2" >> "$CFG"
    echo "A REBOOT is required before the gadget can bind."
fi

echo "Installing files to /opt/mousebridge..."
mkdir -p /opt/mousebridge
cp "$SRC/setup-gadget.sh" "$SRC/hidpump.py" /opt/mousebridge/
chmod +x /opt/mousebridge/setup-gadget.sh
cp "$SRC/mousebridge-gadget.service" "$SRC/hidpump.service" /etc/systemd/system/

echo "Installing dnsmasq (DHCP for the USB network link)..."
DEBIAN_FRONTEND=noninteractive apt-get install -y dnsmasq >/dev/null
cat > /etc/dnsmasq.d/mousebridge-usb0.conf <<'EOF'
interface=usb0
bind-interfaces
dhcp-range=10.66.0.1,10.66.0.1,255.255.255.0,12h
dhcp-option=option:router
dhcp-option=option:dns-server
EOF

echo "Enabling services..."
systemctl daemon-reload
systemctl enable dnsmasq mousebridge-gadget hidpump >/dev/null 2>&1

if [ -d /sys/class/udc ] && [ -n "$(ls /sys/class/udc 2>/dev/null)" ]; then
    systemctl restart mousebridge-gadget dnsmasq hidpump
    echo "Started. Gadget state: $(cat /sys/class/udc/*/state 2>/dev/null | head -1)"
else
    echo "No UDC available yet (dwc2 just added?) - reboot, services start automatically."
fi

echo "Done. The host PC should now see a USB mouse + network adapter."
