# No sound, wrong speaker, or microphone not working
Keywords: sound, audio, no sound, speakers, headphones, hdmi audio, microphone, mic, volume, pipewire, wireplumber, crackling

Sound uses PipeWire (with WirePlumber). Volume and output: the speaker icon in the taskbar.

## Check
- `wpctl status`: the "*" marks the default output (Sinks) and input (Sources).
- Muted? `wpctl get-volume @DEFAULT_AUDIO_SINK@`
- Is any sound card found? `aplay -l`. "no soundcards found" points to a driver/firmware issue.

## Fixes, from simple to deeper
1. Choose the right output: `wpctl set-default ID` (ID from `wpctl status`), then
   `wpctl set-mute @DEFAULT_AUDIO_SINK@ 0` and `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.7`.
2. Restart the sound system for this user: `systemctl --user restart pipewire pipewire-pulse wireplumber`.
3. HDMI / DisplayPort monitor speakers: in System Settings > Sound pick the HDMI profile of the
   graphics card. NVIDIA HDMI audio needs the NVIDIA driver.
4. Laptops with Intel "smart sound" (no sound card found): admin
   `apt-get install -y firmware-sof-signed` and restart.
5. Settings may be broken: `mv ~/.local/state/wireplumber ~/.local/state/wireplumber.bak` then step 2.
6. Crackling: create ~/.config/pipewire/pipewire.conf.d/10-quantum.conf with
   ```
   context.properties = { default.clock.min-quantum = 1024 }
   ```
   and restart PipeWire (step 2).

## Microphone
Set the default input with `wpctl set-default ID` (from the Sources list) and unmute it:
`wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0`. Firefox asks for permission per website.
