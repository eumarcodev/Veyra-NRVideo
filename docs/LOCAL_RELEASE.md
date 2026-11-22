# Local release pipeline (developer machine)

Build and install your own Veyra versions without waiting for upstream PRs or releases, and without hand-copying DLLs into the installed app. Local only: these installers are never published.

## How it works

```
official 2.2.0 payload ──verify SHA vs package-manifest.json──▶ donor (read-only snapshot)
donor ──hard links──▶ stage/<version> ◀── overlays (locally built DLL/EXE, new files + atomic replace)
stage ──scripts/installer/Veyra.iss (Inno Setup 7.1.1)──▶ Veyra-<version>-<GPU>-win64-setup.exe ──▶ per-user install
```

- Local versions are `X.Y.((Z+1)*1000+N)` on top of donor `X.Y.Z`: 2.2.0 → 2.2.1001, 2.2.1002, … They upgrade the official 2.2.0 and the next official minor (2.3.0) still installs over them. An official 2.2.1 would be refused as a downgrade; uninstall first or rebuild the donor on 2.2.1.
- Each install adds `versions\<version>` and keeps the previous one for the launcher's fallback. `prune` removes the rest.
- The stage manifest records the base version, git commit/branch/dirty and every overlaid file with its SHA256.

## Setup (once)

1. Inno Setup 7.1.1 extracted portable (`innosetup-7.1.1-x64.exe /PORTABLE=1 /CURRENTUSER /DIR=...`), Python 3.11+ with Pillow.
2. Copy `scripts/local/config.example.json` to `<repo parent>\veyra-local\config.json` (or set `VEYRA_LOCAL_CONFIG`) and adjust paths/flavor.
3. Build the donor from the installed official version. If some files of the installation were modified by hand, pass a folder with the originals; every file must match the official manifest:

```powershell
.\scripts\local\veyra-local.ps1 donor --candidates E:\projects\pessoal\verify\d3d12va-refonly\backup-original
```

## Daily use

```powershell
.\scripts\local\veyra-local.ps1 release --install   # new version + installer + silent install
.\scripts\local\veyra-local.ps1 status
.\scripts\local\veyra-local.ps1 install --version 2.2.1003 --interactive
.\scripts\local\veyra-local.ps1 prune               # dry run; add --yes to delete
```

Close Veyra before installing. Logs: `<artifacts>\logs\local-release\<version>-{iscc,install}.log`.

## Overlays

Each entry in `config.json` maps built files into the payload:

```json
{"name": "ffmpeg-d3d12va-refonly", "enabled": true,
 "from": "E:/projects/pessoal/build/d3d12va-refonly/patched-install/bin", "to": "",
 "files": ["avcodec-63.dll", "avformat-63.dll", "avutil-61.dll"]}
```

- Only files already in the donor may be replaced unless `"allowNew": true`. The release payload rules (`package-qml-release.validate_payload`) and model identity checks still apply.
- `"to": "@root/"` replaces `Veyra.exe` or `portable-update-trust.json` in the install root; `veyra_update_worker.exe` is accepted as a payload overlay.
- Overlays never write through the donor's hard links.

## Limits

- Only components built on this machine can be overlaid. Building the app itself (`veyra_qml_ui.exe`, QML, shaders) needs Qt 6.8.3 and the remaining dependencies in `docs/BUILD_2.2.0.md`.
- The app EXE keeps reporting its compiled version (2.2.0) until it is rebuilt with `VEYRA_DISPLAY_VERSION`.
- `compression` defaults to `zip/1` (≈35 s, ≈780 MB for AMD); the official build uses `lzma2/fast` (smaller, much slower).
