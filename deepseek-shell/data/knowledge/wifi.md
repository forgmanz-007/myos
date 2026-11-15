# Wi-Fi not working
Keywords: wifi, wi-fi, wireless, wlan, no networks, network adapter, iwlwifi, realtek, broadcom, mediatek, firmware, internet

## Check, in this order
1. `nmcli radio` shows whether Wi-Fi is enabled. If disabled: `nmcli radio wifi on`.
2. `rfkill list`: "Hard blocked: yes" means a physical switch, Fn key or BIOS setting.
   "Soft blocked: yes": admin `rfkill unblock wifi`.
3. `nmcli device`: if there is no "wifi" device, the driver or firmware is missing.
   Identify the chip with `lspci -nnk | grep -iA3 net` (or `lsusb` for USB adapters) and check
   system_report for "Missing firmware".

## Firmware (admin: apt-get install -y PACKAGE, then restart)
- Intel: firmware-iwlwifi
- Realtek: firmware-realtek
- Qualcomm/Atheros: firmware-atheros
- MediaTek: firmware-mediatek
- Broadcom: firmware-brcm80211; for BCM43xx chips that need the "wl" driver: broadcom-sta-dkms linux-headers-amd64
- Anything else: firmware-misc-nonfree
Brand-new chips (Wi-Fi 7) may need the newest kernel from backports:
`apt-get install -y -t trixie-backports linux-image-amd64 linux-headers-amd64 firmware-iwlwifi` then restart.

## Connect
- List networks: `nmcli device wifi list`
- Connect: `nmcli device wifi connect "NETWORK NAME" password "PASSWORD"` (ask the user for the password).

## Drops or is slow
Turn off Wi-Fi power saving: write /etc/NetworkManager/conf.d/wifi-powersave.conf (admin) with
```
[connection]
wifi.powersave = 2
```
then admin `systemctl restart NetworkManager`.
