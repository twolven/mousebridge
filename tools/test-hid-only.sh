#!/bin/bash
# Diagnostic: rebuild the gadget with ONLY the HID function (no NCM) to
# isolate whether the composite (IAD) structure is what breaks HID on the
# Windows host. Run on the Pi as root. Re-run setup-gadget.sh to restore.
set -e

G=/sys/kernel/config/usb_gadget/mousebridge

if [ -d "$G" ]; then
    echo "" > "$G/UDC" 2>/dev/null || true
    find "$G/configs" -maxdepth 2 -type l -delete 2>/dev/null || true
    rmdir "$G"/configs/*/strings/* "$G"/configs/* "$G"/functions/* \
          "$G"/strings/* "$G" 2>/dev/null || true
fi

mkdir -p "$G"
cd "$G"

echo 0x093a > idVendor
echo 0x2510 > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "PixArt" > strings/0x409/manufacturer
echo "USB Optical Mouse" > strings/0x409/product

mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/subclass
echo 2 > functions/hid.usb0/protocol
echo 7 > functions/hid.usb0/report_length
printf '%b' \
'\x05\x01\x09\x02\xa1\x01\x09\x01\xa1\x00'\
'\x05\x09\x19\x01\x29\x05\x15\x00\x25\x01\x95\x05\x75\x01\x81\x02'\
'\x95\x01\x75\x03\x81\x01'\
'\x05\x01\x09\x30\x09\x31\x16\x01\x80\x26\xff\x7f\x75\x10\x95\x02\x81\x06'\
'\x09\x38\x15\x81\x25\x7f\x75\x08\x95\x01\x81\x06'\
'\x05\x0c\x0a\x38\x02\x15\x81\x25\x7f\x75\x08\x95\x01\x81\x06'\
'\xc0\xc0' > functions/hid.usb0/report_desc

mkdir -p configs/c.1/strings/0x409
echo "HID only" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower
ln -sf "$G/functions/hid.usb0" configs/c.1/

UDC_NAME=$(ls /sys/class/udc | head -n1)
echo "$UDC_NAME" > UDC
echo "HID-only gadget bound."
