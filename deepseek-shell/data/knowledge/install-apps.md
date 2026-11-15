# Install, set up, update or remove apps
Keywords: install, app, program, software, download, set up, setup, flatpak, flathub, apt, uninstall, remove, store, spotify, discord, chrome, vlc, office, zoom, telegram, obs, gimp, vscode, shortcut, pin, taskbar, startup

## How to install an app
Use install_app with the app's everyday name ("Spotify", "OBS", "File Explorer", "Notepad") or a
Flathub id. Flathub apps are installed just for this user (`flatpak install --user`), so no password
is needed. Apps appear in the Start menu right away.
Then, if the user wants: create_shortcut (desktop, taskbar, startup) and open_app.

## Setting an app up
- A Flathub app keeps its settings in ~/.var/app/APP_ID/config/ (and data in ~/.var/app/APP_ID/data/).
  Close the app before editing its files.
- Give a Flathub app access to a folder: `flatpak override --user --filesystem=/path/to/folder APP_ID`
  (for example a games drive for Steam). Show the current permissions: `flatpak info --show-permissions APP_ID`.
- Start an app automatically at login: create_shortcut with where="startup".
- Set the default app for a file type, e.g. VLC for videos: `xdg-mime default org.videolan.VLC.desktop video/mp4`.

## Flatpak
- Search: `flatpak search NAME`
- Install for this user (no password): `flatpak install --user -y flathub APP_ID`
- Install for all users (admin): `flatpak install -y flathub APP_ID`
- Update: `flatpak update --user -y` (your apps) and, as admin, `flatpak update -y` (system apps)
- Remove: `flatpak uninstall --user -y APP_ID` (add admin and drop --user for system apps); clean up: `flatpak uninstall --user -y --unused`
- List installed: `flatpak list --app`

Common app IDs:
| App | ID |
|---|---|
| Google Chrome | com.google.Chrome |
| Brave | com.brave.Browser |
| Discord | com.discordapp.Discord |
| Spotify | com.spotify.Client |
| VLC | org.videolan.VLC |
| LibreOffice | org.libreoffice.LibreOffice |
| OnlyOffice (looks like MS Office) | org.onlyoffice.desktopeditors |
| GIMP (like Photoshop) | org.gimp.GIMP |
| Krita (drawing) | org.kde.krita |
| OBS Studio | com.obsproject.Studio |
| Kdenlive (video editing) | org.kde.kdenlive |
| Visual Studio Code | com.visualstudio.code |
| Telegram | org.telegram.desktop |
| Signal | org.signal.Signal |
| Zoom | us.zoom.Zoom |
| Slack | com.slack.Slack |
| Thunderbird (email) | org.mozilla.Thunderbird |
| Steam | com.valvesoftware.Steam |
| Heroic (Epic / GOG games) | com.heroicgameslauncher.hgl |
| Lutris | net.lutris.Lutris |
| Bottles (Windows apps) | com.usebottles.bottles |
| Prism Launcher (Minecraft) | org.prismlauncher.PrismLauncher |
| Obsidian | md.obsidian.Obsidian |
| qBittorrent | org.qbittorrent.qBittorrent |
| 7-Zip-like archiver (Ark) | org.kde.ark |

## apt
- Search: `apt-cache search --names-only WORD`
- Install (admin): `apt-get install -y PACKAGE`
- Remove (admin): `apt-get remove -y PACKAGE`
- A downloaded .deb file (admin): `apt-get install -y ./file.deb` (only from trusted websites).

Never install from random scripts (`curl ... | sh`) without explaining the risk to the user first.
