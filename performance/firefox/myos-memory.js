// MyOS: when memory runs low, Firefox unloads tabs you have not looked at for a
// while (they load again when you click them). Firefox does this by itself on
// Windows and macOS; on Linux it is switched off unless asked for.
pref("browser.tabs.unloadOnLowMemory", true);
