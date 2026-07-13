#!/bin/bash
###############################################################################
# MouseBridge gadget setup v0.1 - Pi (gadget side)
#
# Builds a composite USB gadget via configfs:
#   - HID mouse (5 buttons, 16-bit relative X/Y, wheel, AC pan) -> /dev/hidg0
#   - NCM ethernet on the same cable (wired network to the host PC, no WiFi)
#
# Requires dtoverlay=dwc2 in /boot/firmware/config.txt and the data USB port.
# Run at boot via mousebridge-gadget.service.
###############################################################################
set -e

G=/sys/kernel/config/usb_gadget/mousebridge

# Generic defaults. If the target app fingerprints hardware, set these to a
# real mouse's IDs (lsusb on any PC with the real mouse attached).
ID_VENDOR="0x1d6b"
ID_PRODUCT="0x0104"
MANUFACTURER="MouseBridge"
PRODUCT="USB Optical Mouse"
SERIAL="MB000001"

# Network function: ncm (Windows 11 inbox driver), rndis (Windows 10),
# or ecm (Linux/mac hosts).
FUNC_NET="ncm"
USB_IP="10.66.0.2/24"

modprobe libcomposite

if [ -d "$G" ]; then
    echo "Gadget already exists, tearing down first..."
    echo "" > "$G/UDC" 2>/dev/null || true
    find "$G/configs" -maxdepth 2 -type l -delete 2>/dev/null || true
    rmdir "$G"/configs/*/strings/* "$G"/configs/* "$G"/functions/* \
          "$G"/strings/* "$G" 2>/dev/null || true
fi

mkdir -p "$G"
cd "$G"

echo "$ID_VENDOR"  > idVendor
echo "$ID_PRODUCT" > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "$SERIAL"       > strings/0x409/serialnumber
echo "$MANUFACTURER" > strings/0x409/manufacturer
echo "$PRODUCT"      > strings/0x409/product

# --- HID mouse function ---
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/subclass   # boot interface subclass
echo 2 > functions/hid.usb0/protocol   # mouse
echo 7 > functions/hid.usb0/report_length

# Report: buttons(5+3pad), X i16, Y i16, wheel i8, AC-pan i8 = 7 bytes
printf '%b' \
'\x05\x01\x09\x02\xa1\x01\x09\x01\xa1\x00'\
'\x05\x09\x19\x01\x29\x05\x15\x00\x25\x01\x95\x05\x75\x01\x81\x02'\
'\x95\x01\x75\x03\x81\x01'\
'\x05\x01\x09\x30\x09\x31\x16\x01\x80\x26\xff\x7f\x75\x10\x95\x02\x81\x06'\
'\x09\x38\x15\x81\x25\x7f\x75\x08\x95\x01\x81\x06'\
'\x05\x0c\x0a\x38\x02\x15\x81\x25\x7f\x75\x08\x95\x01\x81\x06'\
'\xc0\xc0' > functions/hid.usb0/report_desc

# --- Network function (wired link over the same USB cable) ---
mkdir -p "functions/$FUNC_NET.usb0"

# --- Bind both into one configuration ---
mkdir -p configs/c.1/strings/0x409
echo "HID mouse + $FUNC_NET" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower
ln -sf "$G/functions/hid.usb0" configs/c.1/
ln -sf "$G/functions/$FUNC_NET.usb0" configs/c.1/

# --- Attach to the USB device controller ---
UDC_NAME=$(ls /sys/class/udc | head -n1)
if [ -z "$UDC_NAME" ]; then
    echo "ERROR: no UDC found (dwc2 overlay missing? wrong USB port?)" >&2
    exit 1
fi
echo "$UDC_NAME" > UDC

# --- Bring up the USB network interface ---
sleep 1
IFACE=$(ls "functions/$FUNC_NET.usb0" | grep -x ifname >/dev/null 2>&1 \
        && cat "functions/$FUNC_NET.usb0/ifname" || echo usb0)
ip addr flush dev "$IFACE" 2>/dev/null || true
ip addr add "$USB_IP" dev "$IFACE"
ip link set "$IFACE" up

echo "Gadget up: HID at /dev/hidg0, network $IFACE at $USB_IP"
