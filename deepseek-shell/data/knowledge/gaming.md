# Gaming: Steam, Proton, Epic/GOG, controllers
Keywords: game, games, gaming, steam, proton, epic, gog, heroic, lutris, controller, gamepad, fps, mangohud, gamemode, anti-cheat

## Graphics driver first
NVIDIA cards need the NVIDIA driver (see the NVIDIA guide). AMD and Intel work out of the box.

## Steam (recommended: Flatpak)
Admin: `flatpak install -y flathub com.valvesoftware.Steam`
In Steam: Settings > Compatibility > "Enable Steam Play for all other titles" (uses Proton).
Proton versions: Steam's own, plus GE-Proton built into this system (Steam on apt sees
/usr/share/steam/compatibilitytools.d; for Flatpak Steam install GE-Proton separately:
`flatpak install -y flathub com.valvesoftware.Steam.CompatibilityTool.Proton-GE`).
Alternative (apt, admin): `apt-get install -y steam-installer` (32-bit support is already enabled).

## Epic Games, GOG, Amazon
Heroic Games Launcher (admin): `flatpak install -y flathub com.heroicgameslauncher.hgl`

## Will my game run?
Check https://www.protondb.com. Games with kernel anti-cheat (e.g. Valorant, Fortnite) do not run.

## Extras
- Performance overlay: MangoHud (admin: `apt-get install -y mangohud`), then launch option `mangohud %command%`.
- GameMode (admin): `apt-get install -y gamemode`, launch option `gamemoderun %command%`.
- Controllers: Xbox (USB) and PlayStation controllers work. Steam Input handles most others.
