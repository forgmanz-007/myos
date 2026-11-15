# Install the NVIDIA graphics driver
Keywords: nvidia, geforce, rtx, gtx, graphics card, gpu, driver, cuda, nouveau, black screen, ai slow, ollama gpu, games slow

Games, video acceleration and the AI engine need NVIDIA's own driver. Without it the open
"nouveau" driver is used: no CUDA (the AI runs on the CPU) and poor gaming speed.

## 1. Check
- `lspci -nnk | grep -A3 -iE 'vga|3d'` shows "Kernel driver in use: nvidia" when it is already fine.
  "nouveau" or no driver line means the driver is missing.
- `mokutil --sb-state`: if it says "SecureBoot enabled", step 4 is needed.
- Generation: RTX 50 series (Blackwell) needs driver 570 or newer. Debian 13's own driver is 550,
  which supports GTX 900 series up to RTX 40 series.

## 2a. GTX 900 up to RTX 40 series: Debian's driver (recommended)
Admin:
`apt-get update && apt-get install -y linux-headers-amd64 nvidia-kernel-dkms nvidia-driver firmware-misc-nonfree`
(contrib, non-free and non-free-firmware are already enabled on this system.)
For RTX 20 series and newer the open kernel module also works: use `nvidia-open-kernel-dkms`
instead of `nvidia-kernel-dkms`.
For 32-bit games and Proton also install (admin): `apt-get install -y nvidia-driver-libs:i386`

## 2b. RTX 50 series, or when Debian's driver is too old: NVIDIA's repository
Admin, one command at a time:
1. `cd /tmp && curl -fsSLO https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/cuda-keyring_1.1-1_all.deb`
2. `dpkg -i /tmp/cuda-keyring_1.1-1_all.deb && apt-get update`
3. `apt-get install -y linux-headers-amd64 nvidia-open`
   If nvidia-open is not found: `apt-cache policy nvidia-open cuda-drivers` and install `cuda-drivers`.
Do not mix Debian's and NVIDIA's packages. To switch, first remove Debian's (ask the user):
`apt-get purge -y 'nvidia-*' 'libnvidia-*' && apt-get autoremove -y`

## 3. Secure Boot (only if it is enabled)
DKMS signs the driver with this computer's own key, which must be enrolled once.
Agree on a simple one-time password with the user (8+ characters, e.g. 12345678), then admin:
`mokutil --generate-hash=12345678 > /root/mok.hash && mokutil --import /var/lib/dkms/mok.pub --hash-file /root/mok.hash`
Tell the user exactly what happens at the next restart: a blue "MOK management" screen appears.
Choose: Enroll MOK, Continue, Yes, type the password, Reboot. (If they miss it, run the command again.)

## 4. Restart and check
After the restart `nvidia-smi` must list the card. Then let the AI engine pick it up (admin):
`systemctl restart ollama` and check `journalctl -u ollama -b --no-pager | grep -iE 'cuda|inference compute'`.

## Trouble
- Black screen after installing: at the boot menu press e, add `nomodeset` at the end of the line
  that starts with "linux", press F10. Then look at `journalctl -b -1 -p err`.
- `dkms status` shows build state. The headers must match the kernel: `linux-headers-$(uname -r)`.
- Undo: `apt-get purge -y 'nvidia-*' 'libnvidia-*'` and restart (nouveau comes back).
