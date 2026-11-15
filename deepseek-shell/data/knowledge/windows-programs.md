# Run Windows programs (.exe) with Wine or Proton
Keywords: windows, exe, msi, wine, proton, setup.exe, installer, windows program, windows app, compatibility, properties, ge-proton, umu, bottles, compress game, game compressor, compactgui, shrink game, game takes too much space

## How it works on this computer
Double-clicking an .exe (or `myos-run-exe FILE`) checks it for viruses the first time it runs
(ClamAV, plus VirusTotal and ANY.RUN when API keys are set in the ClamAV Security app; later
starts skip the check until the file changes) and then runs it:
- with Wine (default; good for normal programs), or
- with Proton (better for games: DXVK/VKD3D for DirectX), if switched on for that file:
  right-click the .exe > Properties > "Windows compatibility" tab > "Run this program with Proton".
  The same switch is in right-click > Windows program > Compatibility settings.
Built-in Proton version: GE-Proton in /usr/share/steam/compatibilitytools.d.
Programs get a Windows "C: drive" (a prefix) in ~/.local/share/myos/prefixes/.

## Installing a Windows program
1. Download the installer in Firefox, open it from the downloads list (or double-click it).
2. After installing, the program usually adds a Start menu entry. If not, run its .exe from
   ~/.local/share/myos/prefixes/<prefix>/drive_c/Program Files/... (or pfx/drive_c for Proton).

## Make a program or game take less disk space ("Game Compressor")
`myos-compress --text FOLDER_OR_EXE` says how big it is and what compressing would save;
`myos-compress --text --compress FOLDER_OR_EXE` does it (add `--strong` for the strongest setting),
`myos-compress --text --undo FOLDER_OR_EXE` reverses it. Given an .exe it takes the program's
whole folder (the game's folder when the .exe sits in bin/ or Binaries/Win64/; only the file
itself in Downloads, Desktop and the like, or with `--file-only`). The files stay in place and keep working:
the Btrfs file system unpacks them while they are read, and files added later (updates) are
compressed too. For the user: right-click the .exe > Properties > Windows compatibility >
"Compress to save disk space", or right-click a folder > "Compress to save disk space".
It only works on Btrfs drives (check: `findmnt -n -o FSTYPE -T FOLDER`); already packed data
(videos, most game archives) hardly shrinks. Close the program first. Files whose data is shared
with a copy (cp, File Explorer) or a snapshot are skipped, because compressing them would store
them twice; delete the copy or wait for the snapshot clean-up, then compress again.

Opening a downloaded .exe from Firefox, Chrome or any app that uses `xdg-open` works (same
check): /usr/local/bin/kde-open hands .exe/.msi/.bat/.cmd files to myos-run-exe and everything
else to KDE's /usr/bin/kde-open. If a KDE app still says "launching executables is not allowed in
this context", open the file from File Explorer (Dolphin) or run `myos-run-exe FILE.exe`.

## When a program does not work
- Check its rating on https://appdb.winehq.org (Wine) or https://www.protondb.com (games).
- Try the other runner (Wine vs Proton) in the file's Properties.
- Missing Windows components: `winetricks` (admin install: `apt-get install -y winetricks`), e.g.
  `WINEPREFIX=~/.local/share/myos/prefixes/wine-default winetricks -q vcrun2022 dotnet48 corefonts`.
- Bottles (Flathub: com.usebottles.bottles) is a friendly app for difficult programs.
- Programs that will NOT work: anti-cheat games that block Linux, Microsoft Office 365 (use the web
  version or OnlyOffice/LibreOffice), Adobe Creative Cloud, drivers and system tools for Windows.

## Logs
`myos-run-exe --debug FILE` runs in the foreground and prints Wine/Proton output.
