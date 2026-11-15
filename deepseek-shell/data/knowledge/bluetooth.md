# Bluetooth not working or device won't pair
Keywords: bluetooth, headphones, earbuds, airpods, controller, mouse, keyboard, pair, pairing, bluez

## Check
- `rfkill list bluetooth`: if soft blocked, admin `rfkill unblock bluetooth`.
- `systemctl is-active bluetooth`: if not active, admin `systemctl enable --now bluetooth`.
- `bluetoothctl show` shows "Powered: yes" when the adapter works. No adapter at all: look for
  missing firmware in system_report (Intel: firmware-iwlwifi, Realtek: firmware-realtek,
  MediaTek: firmware-mediatek, Qualcomm: firmware-atheros), install it (admin) and restart.

## Pairing
The easiest way is System Settings > Bluetooth (or the Bluetooth icon in the taskbar): put the
device in pairing mode and click it. From the command line, `bluetoothctl` needs a sequence:
`bluetoothctl --timeout 15 scan on` then `bluetoothctl pair XX:XX:XX:XX:XX:XX`,
`bluetoothctl trust XX:XX:XX:XX:XX:XX`, `bluetoothctl connect XX:XX:XX:XX:XX:XX`.

## Headphones connect but no sound / bad quality
- Pick the output in the volume icon, or `wpctl status` then `wpctl set-default ID`.
- Bad "phone call" quality means the headset profile (HFP) is active; choose the A2DP / high
  fidelity profile in System Settings > Sound > device > Profile.

## Game controllers
Xbox controllers over Bluetooth work best with xpadneo (not in Debian; ask before installing from
GitHub). PlayStation controllers work out of the box.
