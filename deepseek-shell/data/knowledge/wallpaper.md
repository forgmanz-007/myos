# Wallpaper: pictures and video (live) wallpapers
Keywords: wallpaper, background, desktop background, video wallpaper, live wallpaper, animated wallpaper, moving wallpaper, gif wallpaper, lock screen, slideshow

## Set a wallpaper
- Use set_wallpaper with a picture or video file (or a direct link to one). It changes every screen.
- Videos loop, start muted, and pause while a window is maximized or fullscreen (saves power and
  keeps games fast). Options: sound=true, fit=true (whole video with bars), always_play=true.
- The user can also: right-click a video in File Explorer > Set as Wallpaper; right-click a
  picture > Set as Wallpaper; or right-click the desktop > Desktop and Wallpaper.
- Command line: `myos-set-wallpaper [--sound] [--fit] [--always-play] FILE_OR_LINK`

## More video settings
Right-click the desktop > Desktop and Wallpaper. Wallpaper type "Smart Video Wallpaper Reborn"
has a playlist (several videos, shuffle, change every N minutes), speed, volume, blur, pause rules,
and battery saving. Video wallpaper on the lock screen: System Settings > Screen Locking >
Appearance > Configure, then choose the same wallpaper type.

## Getting videos
- Good files: MP4 (H.264) or WebM, 1920x1080 (or your screen size), 10-60 seconds that loop well.
- Web links must point straight at the file (ending in .mp4/.webm). set_wallpaper saves
  downloads in ~/Videos/Wallpapers.
- YouTube and similar sites: install yt-dlp (admin: `apt-get install -y yt-dlp`), then
  `yt-dlp -f "bv*[height<=1080][ext=mp4]/bv*[height<=1080]" -o "$HOME/Videos/Wallpapers/%(title)s.%(ext)s" LINK`
  (video only, no sound track) and use the downloaded file. Only download videos the user may use.
- Cut or loop a clip (admin: `apt-get install -y ffmpeg`):
  `ffmpeg -ss 00:00:05 -t 20 -i in.mp4 -an -c:v libx264 -crf 20 out.mp4`

## Back to a normal picture
`myos-set-wallpaper /usr/share/wallpapers/myos.svg` (the original wallpaper) or any picture.

## Problems
- Black or frozen video: try another file (MP4 H.264 works everywhere). Check the plugin is installed:
  `ls /usr/share/plasma/wallpapers/luisbocanegra.smart.video.wallpaper.reborn`
- Without graphics acceleration (a virtual machine, or no graphics driver yet) set_wallpaper says
  so, and the video then pauses whenever a window is open. A picture is the fast choice there.
- High CPU use: hardware video decoding needs the graphics driver (NVIDIA: see the NVIDIA guide;
  AMD/Intel: package va-driver-all). Check with `vainfo` (admin: `apt-get install -y vainfo`).
  Lower resolution videos (1080p) also help.
- Messages from the plugin: in its settings switch on debug, then `journalctl --user -b | grep -i smart`.
