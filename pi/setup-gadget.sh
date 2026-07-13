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

# Device identity: PixArt OEM optical mouse - a real commodity-hardware ID.
# WARNING: do NOT use a vendor ID whose software is installed on the host
# (e.g. Logitech 046d with G HUB present): the vendor driver claims the
# device, its vendor requests go unanswered, and the HID endpoint is never
# polled - every report drops. Confirmed with G502 IDs against LGHUB.
ID_VENDOR="0x093a"
ID_PRODUCT="0x2510"
BCD_DEVICE="0x0100"
MANUFACTURER="PixArt"
PRODUCT="USB Optical Mouse"
SERIAL=""

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
echo "$BCD_DEVICE" > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "$MANUFACTURER" > strings/0x409/manufacturer
echo "$PRODUCT"      > strings/0x409/product
[ -n "$SERIAL" ] && echo "$SERIAL" > strings/0x409/serialnumber

# --- HID mouse function ---
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/subclass   # boot interface subclass
echo 2 > functions/hid.usb0/protocol   # mouse
echo 7 > functions/hid.usb0/report_length

# Report: buttons(5+3pad), X i16, Y i16, wheel i8, AC-pan i8 = 7 bytes.
# Written via python3: printf '%b' truncates at embedded NUL bytes (\x00),
# which silently corrupted the descriptor to 14 bytes and made Windows
# reject the HID interface entirely (enumerated but never polled).
python3 -c 'import sys; sys.stdout.buffer.write(bytes.fromhex(
    "05010902a1010901a100"
    "05091901290515002501950575018102"
    "950175038101"
    "05010930093116018026ff7f751095028106"
    "09381581257f750895018106"
    "050c0a38021581257f750895018106"
    "c0c0"))' > functions/hid.usb0/report_desc
DESC_LEN=$(wc -c < functions/hid.usb0/report_desc)
if [ "$DESC_LEN" -ne 79 ]; then
    echo "ERROR: report_desc is $DESC_LEN bytes, expected 79" >&2
    exit 1
fi

# --- Network function (wired link over the same USB cable) ---
# MACs pinned: random defaults make Windows see a NEW adapter every rebind,
# orphaning the DHCP lease (single-IP pool) and breaking relay->Pi routing.
mkdir -p "functions/$FUNC_NET.usb0"
echo "02:4d:42:00:00:02" > "functions/$FUNC_NET.usb0/dev_addr"
echo "02:4d:42:00:00:01" > "functions/$FUNC_NET.usb0/host_addr"

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
