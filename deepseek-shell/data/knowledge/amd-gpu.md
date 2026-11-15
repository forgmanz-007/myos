# AMD Radeon graphics and AI acceleration
Keywords: amd, radeon, rx, gpu, graphics card, amdgpu, rocm, ollama gpu, vulkan, mesa, rdna

The screen and games work out of the box with the amdgpu driver and Mesa. Only the AI engine
needs an extra add-on (ROCm) to use an AMD card.

## Check
- `lspci -nnk | grep -A3 -iE 'vga|3d'` should show "Kernel driver in use: amdgpu".
- `vulkaninfo --summary | grep -i devicename` should list the Radeon card, not "llvmpipe".
- Missing firmware in the kernel log: admin `apt-get install -y firmware-amd-graphics`, restart.
- Very new cards (RX 9000 series) need a recent kernel and Mesa. Check what backports offers:
  `apt-cache policy mesa-vulkan-drivers` and, if newer there, admin:
  `apt-get install -y -t trixie-backports mesa-vulkan-drivers libgl1-mesa-dri mesa-vulkan-drivers:i386 libgl1-mesa-dri:i386`

## Let the AI use the AMD card (ROCm add-on for Ollama)
Admin:
`curl -fL https://ollama.com/download/ollama-linux-amd64-rocm.tar.zst | zstd -d | tar -x -C /usr/local && systemctl restart ollama`
If that download is not found, use the .tgz: `curl -fL https://ollama.com/download/ollama-linux-amd64-rocm.tgz | tar -xz -C /usr/local`
Make sure the engine may use the GPU: `usermod -aG render,video ollama && systemctl restart ollama`
Check (admin): `journalctl -u ollama -b --no-pager | grep -iE 'rocm|amdgpu|inference compute'`

## Card not supported by ROCm
Many consumer cards work with an override. Create /etc/systemd/system/ollama.service.d/override.conf (admin, write_file):
```
[Service]
Environment="HSA_OVERRIDE_GFX_VERSION=11.0.0"
```
Use 11.0.0 for RX 7000 (RDNA3), 10.3.0 for RX 6000 (RDNA2). Then `systemctl daemon-reload && systemctl restart ollama`.
