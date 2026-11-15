# Printers and scanners
Keywords: printer, print, printing, scanner, scan, cups, hp, canon, epson, brother, driverless, airprint

Not preinstalled (you chose a minimal system). Admin:
`apt-get install -y cups print-manager system-config-printer-common ipp-usb sane-airscan skanlite`
Then add the printer in System Settings > Printers. Most printers from the last 10 years work
"driverless" (IPP Everywhere / AirPrint) over Wi-Fi or USB.
- HP: `apt-get install -y hplip`
- Brother, Canon, Epson extra drivers: `apt-get install -y printer-driver-brlaser printer-driver-gutenprint`
  (others: the maker's Linux .deb, ask the user).
- Check: `lpstat -v` lists printers; `systemctl is-active cups`.
- Scanning: Skanlite (installed above) finds network and USB scanners.
