# Undo system changes (snapshots)
Keywords: undo, revert, rollback, snapshot, snapper, restore, broke, broken after change, go back, btrfs

When the system is on Btrfs (the installer's default), DeepSeek Shell saves a snapshot before
every administrator change. Its number is printed in the command output.

## Look
- List snapshots (admin): `snapper -c root list`
- What changed since snapshot N (admin): `snapper -c root status N..0`

## Undo
- Undo all file changes made after snapshot N (admin, ask the user first):
  `snapper -c root undochange N..0`
  Then restart if packages, drivers or the kernel were involved.
- Undo only one file: `snapper -c root undochange N..0 /etc/path/to/file`
Home folders (/home) are not part of system snapshots, so personal files are never touched.

## No snapshots?
`snapper -c root list` fails when the disk is not Btrfs (for example ext4). Then use the backup
copies that write_file keeps (FILE.bak-DATE) or reinstall the package (admin):
`apt-get install --reinstall -y PACKAGE`.
