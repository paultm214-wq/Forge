# devices-photo-ingest

Toolchain: devices
License: MIT
Prior art: none

<!--
One tool that pulls new photos and videos from whatever is plugged in, so
they can go to Ziggurat:
- an Android phone over ADB;
- a camera over PTP (gphoto2);
- an SD card or USB stick (mass storage, a mounted folder).
It tries the devices toolchain against its fakes:
- the fake adb server with a scripted phone;
- a folder that gphoto2 treats as a camera;
- disk images for the card.
No real device is touched in tests. The tool itself never deletes from a
device.
-->

## Goal

`ingest.py` copies new media from each source into
`<dest>/<YYYY>/<YYYY-MM-DD>/<original name>`. It skips files it already
copied: it keeps a SQLite index of (source id, path, size) in
`<dest>/.ingest.sqlite`. It prints a summary per source.

## Constraints

- Python 3.12 with the standard library only, plus `adb` and `gphoto2` as
  commands. Use subprocess with argument lists, never a shell string.
- **Sources** are given on the command line:
  - `--android [SERIAL]` (every device in the `device` state when no
    serial is given);
  - `--camera` (gphoto2's auto-detected camera; tests pass extra gphoto2
    arguments through `--gphoto2-args`);
  - `--folder PATH` (a mounted card or stick).
- **Media files** are those with the extensions .jpg .jpeg .heic .png .dng
  .cr3 .nef .arw .mp4 .mov, case-insensitive.
- **Dates:**
  - Android: the folder files come from, `/sdcard/DCIM/Camera` by default,
    and their mtime from `adb shell stat -c '%Y %s %n' <files>`.
  - Camera: gphoto2's file listing.
  - Folders: the file's mtime.
  - Local time throughout.
- **Never delete or change anything on a source.** Copy to `<name>.part`,
  then rename. A failed copy leaves no partial file.
- **The index** is a table `copied(source TEXT, path TEXT, size INTEGER,
  dest TEXT, at TEXT, PRIMARY KEY (source, path, size))`.
  - The source id is `android:<serial>`, `camera:<model>` or
    `folder:<absolute path>`.
- **Exit codes:**
  - 0: everything copied or already there;
  - 1: some files failed (each one named on stderr);
  - 2: no source was found or the command line is wrong.

## Interfaces

- `android_files(serial, folder="/sdcard/DCIM/Camera") -> list[tuple[str,
  int, int]]`: (remote path, size, mtime) for each media file.
- `camera_files(gphoto2_args) -> list[tuple[str, int]]`: (camera path,
  size).
- `folder_files(root) -> list[tuple[Path, int, int]]`: recursive, media
  only, skipping dot-folders.
- `day_folder(dest, epoch_seconds) -> Path`.
- `ingest(sources, dest) -> dict[source id, {"copied": n, "skipped": n,
  "failed": n}]`.
- `main(argv) -> int`.

## Acceptance

1. Android, with `game.adb_fake` and one FakeAndroid:
   - **Setup.** The device has three files under `/sdcard/DCIM/Camera`:
     two .jpg and one .mp4. It also has a `notes.txt`.
   - **The stat command.** A `shell` entry answers `stat -c '%Y %s %n'
     *` with the three lines for the media files.
   - **First run.** `ingest` copies the 3 media files into the right day
     folders; notes.txt is not copied.
   - **Second run.** It copies nothing and reports 3 skipped.
   - **Read-only.** The device's `.calls` contain no `rm`, push or
     `shell` command that writes.
2. With two FakeAndroid devices, `--android` copies from both, and their
   source ids differ.
3. With `game.camera_dir` holding two JPEGs and `--camera --gphoto2-args`
   set to the returned args: both are copied, and a second run skips
   them.
4. Folder source:
   - `game.disk_image` makes a FAT32 card with
     `DCIM/100CANON/IMG_0001.JPG` and `MISC/readme.txt`;
   - the test copies the files out with `game.fat_read` into a folder;
   - `--folder` on that folder copies only the JPEG.
5. A pull that fails (the fake has a file listed by `stat` but missing
   from `files`):
   - exit code 1, and stderr names that file;
   - no `.part` file is left behind;
   - the other files are still copied.
6. `main([])` returns 2. With `--android` and no devices it also returns
   2, with the message "no Android device".
