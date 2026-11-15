// Windows 10-style desktop: one dark taskbar along the bottom with, from left to right,
// the Start menu, "Type here to search", Task View, the pinned and open programs, the
// tray, the clock, and the thin "Show desktop" strip in the corner.

var panel = new Panel;
panel.location = "bottom";
panel.floating = false;
panel.hiding = "none";
panel.lengthMode = "fill";
panel.height = 40;   // like Windows 10 (at 100 % scaling)

function has(widget) {
    return knownWidgetTypes.indexOf(widget) >= 0;
}

// Tiled Menu keeps its tiles as Base64 text. Our labels are plain ASCII.
function base64(text) {
    var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    var out = "";
    for (var i = 0; i < text.length; i += 3) {
        var n = (text.charCodeAt(i) << 16)
              | ((i + 1 < text.length ? text.charCodeAt(i + 1) : 0) << 8)
              | (i + 2 < text.length ? text.charCodeAt(i + 2) : 0);
        out += chars.charAt((n >> 18) & 63) + chars.charAt((n >> 12) & 63)
             + (i + 1 < text.length ? chars.charAt((n >> 6) & 63) : "=")
             + (i + 2 < text.length ? chars.charAt(n & 63) : "=");
    }
    return out;
}

// Start menu tiles: two groups of three medium tiles (only apps that are installed).
var tiles = [];
function group(y, label) {
    tiles.push({ "tileType": "group", "x": 0, "y": y, "w": 6, "h": 1, "label": label });
}
function tile(x, y, app, label) {
    if (!applicationExists(app)) {
        return;
    }
    var t = { "x": x, "y": y, "w": 2, "h": 2, "url": app };
    if (label) {
        t.label = label;
    }
    tiles.push(t);
}
group(0, "Everyday");
tile(0, 1, "org.kde.dolphin.desktop");
tile(2, 1, "firefox.desktop");
tile(4, 1, "deepseek-shell.desktop");
group(3, "System");
tile(0, 4, "systemsettings.desktop", "Settings");
tile(2, 4, "myos-security.desktop");
tile(4, 4, "myos-taskmanager.desktop");

var start;
if (has("com.github.zren.tiledmenu")) {
    // Windows 10's Start menu: app list A-Z on the left, tiles on the right; typing searches.
    start = panel.addWidget("com.github.zren.tiledmenu");
    start.currentConfigGroup = ["General"];
    start.writeConfig("icon", "myos-start");
    start.writeConfig("tileModel", base64(JSON.stringify(tiles)));
    start.writeConfig("defaultTileColor", "#0078d7");
    start.writeConfig("appDescription", "hidden");
    start.writeConfig("hideSearchField", true);
    start.writeConfig("taskManagerApp", "myos-taskmanager.desktop");
} else {
    start = panel.addWidget("org.kde.plasma.kickoff");
    start.currentConfigGroup = ["General"];
    start.writeConfig("icon", "myos-start");
}
start.currentConfigGroup = ["Shortcuts"];
start.writeConfig("global", "Alt+F1");

if (has("org.myos.searchbox")) {
    var search = panel.addWidget("org.myos.searchbox");
    search.currentConfigGroup = ["Shortcuts"];
    search.writeConfig("global", "Meta+S");
}
if (has("org.myos.taskview")) {
    panel.addWidget("org.myos.taskview");
}

var pinned = ["applications:firefox.desktop", "applications:deepseek-shell.desktop"];
if (applicationExists("org.kde.dolphin.desktop")) {
    pinned.unshift("applications:org.kde.dolphin.desktop");
}
var tasks = panel.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", pinned);

panel.addWidget("org.kde.plasma.systemtray");

var clock = panel.addWidget("org.kde.plasma.digitalclock");
clock.currentConfigGroup = ["Appearance"];
clock.writeConfig("showDate", true);
clock.writeConfig("dateDisplayFormat", 2);   // the date under the time
clock.writeConfig("autoFontAndSize", false);  // small text like Windows 10, not taskbar-high
clock.writeConfig("fontSize", 10);

panel.addWidget(has("org.myos.showdesktop") ? "org.myos.showdesktop" : "org.kde.plasma.showdesktop");

// Desktop: the MyOS wallpaper; icons (Recycle Bin, your files) in columns from the top left,
// medium size, like Windows 10.
var desktops = desktopsForActivity(currentActivity());
for (var i = 0; i < desktops.length; i++) {
    desktops[i].wallpaperPlugin = "org.kde.image";
    desktops[i].currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    desktops[i].writeConfig("Image", "file:///usr/share/wallpapers/myos.svg");
    desktops[i].currentConfigGroup = ["General"];
    desktops[i].writeConfig("arrangement", 1);
    desktops[i].writeConfig("iconSize", 2);
}
