# One-time changes for systems installed from an older ISO

`updates/apply.sh` runs every `*.sh` file in this folder once on each installed MyOS, in
name order and as admin, after it installed the new version's files. It notes each one in
`/var/lib/myos/update/migrations`. The ISO build notes all of them as done, because a new
ISO has those changes already.

Add one when a new version needs more than new files, for example:

- a Debian package added to `iso/config/package-lists/`;
- a service or timer to switch on (the build hooks do that for new ISOs);
- a setting to change on systems that already exist.

Name it by date so the order is clear, for example `2026-12-01-install-kate.sh`. It must
be safe to run twice, and a small problem should not stop the whole update:

```bash
#!/bin/bash
# Kate (Notepad) is part of MyOS from now on.
apt-get install -y -q --no-install-recommends kate || true
```
