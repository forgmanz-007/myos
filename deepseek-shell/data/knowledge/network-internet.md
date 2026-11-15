# No internet (cable, DNS, VPN)
Keywords: internet, ethernet, cable, lan, no connection, dns, website not loading, ip address, vpn, network

## Check
- `nmcli general status` and `nmcli device` (is a device "connected"?)
- `ip -br addr` (does the device have an address?)
- `ping -c 3 1.1.1.1` works but `ping -c 3 debian.org` fails: a DNS problem.
- Firefox only: check Firefox's proxy settings (Settings > Network Settings).

## Fixes
- Reconnect: `nmcli networking off && nmcli networking on` (admin).
- Restart the network service (admin): `systemctl restart NetworkManager`.
- DNS: set a public DNS for the connection (admin):
  `nmcli connection modify "CONNECTION" ipv4.dns "1.1.1.1 9.9.9.9" ipv4.ignore-auto-dns yes && nmcli connection up "CONNECTION"`
  (Get CONNECTION from `nmcli -t -f NAME,DEVICE connection show --active`.)
- Ethernet port missing in `nmcli device`: check `lspci -nnk | grep -iA3 ethernet`. Realtek r8169 issues: admin
  `apt-get install -y firmware-realtek` and restart.
- VPN: the network icon in the taskbar supports OpenVPN and WireGuard once the plugins are installed
  (admin): `apt-get install -y network-manager-openvpn wireguard-tools`. Then import the provider's
  .ovpn or .conf file in System Settings > Wi-Fi & Networking (+ button).
  Many VPN providers also have a Flatpak or .deb app.
