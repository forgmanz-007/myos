# Screen: resolution, scaling, refresh rate, multiple monitors, night light
Keywords: display, screen, monitor, resolution, scaling, too small, too big, refresh rate, hz, 144hz, multiple monitors, second monitor, tearing, night color, brightness, hdr

Easiest: System Settings > Display & Monitor. From the command line use kscreen-doctor:
- Show outputs: `kscreen-doctor -o`
- Resolution and refresh rate: `kscreen-doctor output.DP-1.mode.2560x1440@144`
- Scaling (text too small/big): `kscreen-doctor output.DP-1.scale.1.25`
- Main monitor: `kscreen-doctor output.HDMI-A-1.primary`
- Turn a monitor off/on: `kscreen-doctor output.HDMI-A-1.disable` / `.enable`
(Replace DP-1 / HDMI-A-1 with names from `kscreen-doctor -o`.)

## Problems
- High refresh rate missing: use a DisplayPort cable or a certified HDMI 2.1 cable; NVIDIA needs its driver.
- Screen tearing in games: enable VSync in the game; on Wayland (default) the desktop itself doesn't tear.
- Blurry old apps (X11 apps on Wayland with scaling): System Settings > Display & Monitor >
  Legacy Applications (X11) > "Apply scaling themselves".
- Night light like Windows: System Settings > Display & Monitor > Night Light.
- Black screen or flicker on NVIDIA: see the NVIDIA guide; as a test log in with "Plasma (X11)" on the login screen.
