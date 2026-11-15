# The desktop: taskbar, Start menu, search box, Task View and the Windows 10 look
Keywords: start menu, start button, tiles, tile, pin to start, pin to menu, taskbar, search box, type here to search, task view, show desktop, look, theme, windows 10 look, icons, font, title bar, close button, dark mode, colors, accent color, recycle bin, desktop icons, clock, tray, reset taskbar

## What is where (like Windows 10)
- Taskbar along the bottom, left to right: Start button, the "Type here to search" box, the
  Task View button, pinned and open apps (a blue line under open ones, a lighter button for the
  one in front), the tray (network, sound, notifications, ^ for hidden icons), the clock with
  the date, and a thin "Show desktop" strip in the bottom-right corner.
- Start menu (Win key or the Start button): all apps A-Z on the left with "Most Used" on top,
  tiles on the right ("Everyday": File Explorer, Firefox, DeepSeek Shell; "System": Settings,
  ClamAV Security, Task Manager). The narrow column on the far left: account, Documents,
  Pictures, File Explorer, Settings, and power (shut down, restart, sleep). Typing while the
  menu is open searches. Right-click the Start button for a Win+X-style menu (Task Manager,
  System Settings, File Manager, Terminal, System Info).
- Search box (click it or Win+S): what you type shows in the box and the results appear above
  it: apps, settings, calculations (12*7), unit conversions, and files if file indexing is on
  (Settings > Search > File Search). Enter opens the first result, Esc clears, Esc again closes.
- Task View (the button next to the search box, or Win+Tab): all open windows and the virtual
  desktops; click a window to switch to it.
- Show desktop: click the strip in the bottom-right corner or press Win+D; click again to get
  the windows back.
- Recycle Bin on the desktop: made at the first login; if the user deleted it, it stays away.
  To bring it back, write recycle-bin.desktop into the desktop folder (`xdg-user-dir DESKTOP`)
  with these lines and make it executable (chmod +x):
  `[Desktop Entry]`, `Type=Link`, `Name=Recycle Bin`, `Icon=user-trash-full`,
  `EmptyIcon=user-trash`, `URL=trash:/`

## File Explorer
- Laid out like Windows 10's: the File, Home and View menus (where Windows has its ribbon tabs),
  below them back, forward, up, the address bar, Refresh and search. The title bar shows just
  the folder ("Downloads"). File > Options is Dolphin's settings window.
- Hide or show the File/Home/View row: Ctrl+M (View > Show Menubar). Without it, the menu
  button at the right end of the address row has everything.
- The layout is /usr/local/share/kxmlgui5/dolphin/dolphinui.rc (for everyone; a user's own
  changes from right-click on the toolbar > Configure Toolbars go to
  ~/.local/share/kxmlgui5/dolphin/). Deleting that file (as admin) brings back KDE's own layout.
- The title without " — Dolphin" comes from /usr/libexec/myos/title-bars (made again after
  every update; shows after the next login).

## Change the Start menu and the taskbar
- Tiles: right-click an app in the A-Z list > "Pin to Menu". Remove: right-click the tile >
  "Unpin from Menu". Drag tiles to move them; right-click > "Edit Tile" for size, colour or a
  picture.
- Taskbar: right-click an app > "Pin to Taskbar" (or ask: make_shortcut with where=taskbar).
  Unpin: right-click its taskbar button > "Unpin from Task Manager".
- Start menu settings: right-click the Start button > "Configure Tiled Menu..." (menu size,
  tile size, search field, the shortcuts in the left column).

## The look
- Made of: the dark taskbar and pop-ups ("MyOS 10" Plasma style), white windows with the blue
  #0078D7 accent ("MyOS 10" colours), Windows 10-style title bars ("MyOS 10" window decoration:
  wide minimize/maximize/close buttons, the close button turns red), Windows 10-style icons
  (We10X, with Microsoft's logos left out), the Selawik font (the letter widths of Windows'
  Segoe UI), and a white mouse pointer.
- Change any part: Settings > Colors & Themes (Global Theme, Colors, Plasma Style, Window
  Decorations, Icons, Cursors, Fonts).
- Dark windows: Settings > Colors & Themes > Colors > Breeze Dark (the taskbar is dark already).
- Accent colour: Settings > Colors & Themes > Colors > Accent color.
- Everything bigger (like 125 % in Windows): Settings > Display & Monitor > Scale. Only text:
  Settings > Colors & Themes > Fonts.
- KDE's own look instead: Settings > Colors & Themes > Global Theme > Breeze. Back to the
  Windows 10 look: the same page > "Windows 10 Style".
- Taskbar back to how MyOS set it up (removes the user's own pins and widgets, so ask first):
  `plasma-apply-lookandfeel -a org.myos.desktop --resetLayout`

## Problems
- Taskbar, Start menu or desktop frozen or gone: Task Manager > "Desktop, taskbar and Start
  menu" > Restart, or `systemctl --user restart plasma-plasmashell`.
- Title bars show KDE's icons (a blue Dolphin folder instead of the yellow File Explorer one):
  they are reloaded at every login; to reload now:
  `dbus-send --session --type=signal /KIconLoader org.kde.KIconLoader.iconChanged int32:0`
- The Start button does nothing after a big Plasma update: look for errors with
  `journalctl --user -u plasma-plasmashell -b | grep -i tiledmenu`. The menu (Tiled Menu) is
  in /usr/share/plasma/plasmoids/com.github.zren.tiledmenu. Meanwhile KDE's own menu works:
  right-click the Start button > Show Alternatives > Application Launcher.
