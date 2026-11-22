---
name: veyra-local-release
description: Build, package and install a local Veyra version on this machine without waiting for upstream PRs or releases - stage the verified 2.2.0 donor plus locally built overlays (FFmpeg DLLs, app EXE), compile the Inno Setup installer and install it. Use when the user asks to generate/install a local version, add a build to the installer, roll back, prune old versions, or continue the full-source app build bootstrap.
---

# Veyra local release — orchestrator

You are the ORCHESTRATOR (main session). Talk to the user in pt-BR; code/scripts/commits in English. Keep user gates, git and decisions here; delegate only long or noisy work, choosing the cheapest agent that can do it.

## Tooling (already set up — verify with `status`, do not recreate)

- CLI: `scripts/local/veyra-local.ps1 <command>` (wraps `python -X utf8 -B scripts/local/veyra_local.py`). Docs: `docs/LOCAL_RELEASE.md`.
- Config/state (outside the repo): `E:\projects\pessoal\veyra-local\config.json`, `state.json`.
- Donor: `E:\projects\pessoal\deps\veyra-donor\2.2.0-AMD` — official 2.2.0 AMD payload, every file SHA-verified against its manifest. Never edit it; rebuild only via `donor`.
- Inno Setup 7.1.1 portable: `E:\projects\pessoal\deps\inno-setup-7.1.1\ISCC.exe` (same version as the official release).
- Outputs: `E:\projects\pessoal\{build,releases,logs,tmp}\local-release\`.

## Commands

| Command | What | Time |
|---|---|---|
| `status` | config, installed current/previous, last local build | instant |
| `release` | new stage (next 2.2.10NN) + installer | ~40 s |
| `release --install` | + silent install over the per-user installation | ~1–2 min |
| `install [--version V] [--interactive]` | install an existing setup | ~1 min |
| `prune [--yes]` | delete version folders other than current/previous in the install dir | — |
| `donor --candidates <dir>` | rebuild donor (only when the base release changes) | ~10 s |

Rollback: the installer writes `current-version.txt` = new + previous, so the launcher falls back to the previous version folder; reinstalling an older local setup is refused (downgrade), install a newer one instead.

## Who does what (account-limit aware)

| Work | Agent | Model | Note |
|---|---|---|---|
| `status`, `release`, quick checks | orchestrator directly | — | a subagent costs more context than these commands |
| Repeated installs, hash/log counting, `veyra-qml.log` scans | `veyra-ops` | haiku | background when > 2 min |
| FFmpeg variant builds, app CMake build, dependency bootstrap | `veyra-builder` | sonnet | background, ≤ 900 s per step, max 2 parallel |
| Scoped C++/QML fix with known files | `veyra-coder` | sonnet | |
| Review of risky patches before installing | `veyra-reviewer` | opus | only for D3D12/FFmpeg lifetime, teardown, threading, installer/update logic |

Never spawn an agent for a single command or a file read. Give each agent a self-contained brief: goal, exact paths, the limits above, and "return: commands, exit codes, paths + SHA256, what is unverified". Check their claims (files, hashes) before reporting.

## Adding a component to the installer

1. Build it (`veyra-builder`), output in its own dir under `E:\projects\pessoal\build\<task>\`.
2. Add an overlay entry to `config.json` (`from`, `to`, explicit `files`; `allowNew: true` only for files the donor lacks; `to: "@root/"` for `Veyra.exe`/`portable-update-trust.json`).
3. `release`, then ask the user before `install` (it replaces the active version of their daily app).

## Full app build from source (phase 2 — done 2026-11-22)

All deps are staged under `E:\projects\pessoal\deps\` with receipts `veyra-app-deps-{A,B,C}.json` (Qt 6.8.3, SDKs incl. NVOF 5.0.7 downloaded manually by the user, vcpkg 30ef65ca static prefixes, Chiaki/Moonlight stages) and `E:\projects\pessoal\build\veyra-ffmpeg\RECEIPT.json` (official-config FFmpeg + dav1d/zlib + both repo patches + deinterlace module).

Rebuild after code changes (delegate to `veyra-builder`):
1. `E:\projects\pessoal\tmp\veyra-app\configure.ps1` (only if CMake options change), then build targets `veyra_qml_ui veyra_update_worker veyra_portable_launcher veyra_virtualcam_components` in `E:\projects\pessoal\build\veyra-app\x64-release` (child env from `tmp\veyra-app\env.ps1`). Incremental builds are seconds–minutes.
2. Refresh `E:\projects\pessoal\build\veyra-app\overlay\{version,root}` from the build (exe, worker, launcher, *.dxil shaders, virtualcam, changed QML vs donor, FFmpeg/dav1d, dolby_vision.dll) and `OVERLAY.json`.
3. Optional smoke test: `tmp\veyra-app\smoke.ps1` (private copy `E:\projects\pessoal\verify\app-local`).
4. `release --install` (config overlays `app-build` + `app-launcher`; old `ffmpeg-d3d12va-refonly` disabled).

Not compiled (SDK unavailable): Magewell, DeckLink, CUDA VFG, RDNA3 network worker, VB-Cable offline; donor copies of their runtime files stay. FidelityFX 1.1.4 libs must be /MT (`CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded`) or the app link fails with LNK2038.

## Known caveats

- The old `ffmpeg-d3d12va-refonly` overlay (no dav1d/zlib) is disabled; the app overlay carries the full FFmpeg.
- Before each app rebuild, set the display version to the NEXT local version so About shows it: `cmake -S . -B <build> -DVEYRA_DISPLAY_VERSION=2.2.0-local.<N>` (N = next counter, e.g. 1004 for 2.2.1004; must start with 2.2.0). The update checker strips the suffix, so official updates are still offered. Without this the user sees "2.2.0" and thinks the build was not installed. Run cmake via `cmd /c "... > log 2>&1"` - PowerShell `*>` aborts on cmake's stderr warnings.
- Setup refuses while Veyra is open; never kill the user's app - ask them to close it.
- Running the app writes its QML cache to `%LOCALAPPDATA%\Veyra\Veyra\cache\qmlcache-<hash>` even with `--data-dir`.
- Local manifests are `localOnly: true`; never publish these installers.
