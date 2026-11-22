---
name: d3d12va-refonly-fix
description: Orchestrates the end-to-end fix for the FFmpeg D3D12VA reference-only allocation pool leak (RX 9070 XT, "avcodec_send_packet failed code=-22" / error 2004 teardown in PS5 Remote Play). Use when the user asks to continue, resume, or run the d3d12va reference-only fix, the FFmpeg bcd2c69e08 backport, or this branch's work.
---

# D3D12VA reference-only pool fix — orchestrator

You are the ORCHESTRATOR. Keep decisions, user gates, git and the plan in the main session; delegate heavy or noisy work to subagents with the model listed per phase. Talk to the user in pt-BR; code, patches, commits and PR text in English.

## Ground truth (verify, do not trust blindly)

- Branch `fix/d3d12va-reference-only-leak`. Read `AGENTS.md` (written for the maintainer's machine: map `E:/项目/Veyra/...` to `E:\projects\pessoal\...`), `docs/DEVELOPMENT.md`, `scripts/ffmpeg/README.md`, and the ai-memory handoff/session pages for this fix.
- Evidence: RX 9070 XT reports `configFlags=0x4` (`D3D12_VIDEO_DECODE_CONFIGURATION_FLAG_REFERENCE_ONLY_ALLOCATIONS_REQUIRED`) for HEVC Main/Main10 and H.264. User log `C:\Users\hanre\AppData\Local\Programs\Veyra (AMD)\logs\veyra-qml.log`: 32x `avcodec_send_packet failed code=-22`, 14x `error=2004`, `packetLost=0`, always at `videoCallbacks=71`.
- Fix = backport FFmpeg commit `bcd2c69e08` (`libavcodec/d3d12va_decode.c`, ~+19 -7: reuse the slot already mapped to `output_resource`, iterate `max_num_ref + 1`).
- Decided with the user: NO Vulkan switch, NO palliative workaround, decode stays fixed to Hardware.

## Hard rules

- Never modify the installed app `C:\Users\hanre\AppData\Local\Programs\Veyra (AMD)`. Test only in a private copy under `E:\projects\pessoal\verify\d3d12va-refonly\`.
- Downloads/installs (FFmpeg n9.0.1 source, vcpkg 9.0.1#1 port patches, MSYS2, nasm) require an explicit user "yes" in chat for this session. Put them under `E:\projects\pessoal\deps\` (each download in its own new empty dir). Never change the global PATH; set env only for child processes. The system PATH has an entry with a stray quote (`...Windows Performance Toolkit"`) that breaks `vcvars64.bat`: strip quoted entries in the child env.
- Build: max 2 concurrent, each ≤900 s, run in background. Each test ≤300 s. Artifacts in `E:\projects\pessoal\{build,tests,logs,tmp}\d3d12va-refonly\`.
- No push, PR, release or messages unless the user asks now. Minimal change.
- Subagents return evidence (commands, exit codes, counts, paths), not just "it worked". Check their claims (diffs, files) before reporting to the user.

## Phases and model per subagent

| # | Phase | Who | Model | Why |
|---|-------|-----|-------|-----|
| 0 | Preflight: git status, branch, read rules, ask download authorization (AskUserQuestion) | orchestrator | — | user gates stay here |
| 1 | Fetch sources: FFmpeg n9.0.1 tarball + vcpkg ports `ffmpeg` 9.0.1#1 patches; record URLs + SHA256 | `general-purpose` (background) | `haiku` | mechanical I/O |
| 2 | Toolchain: portable MSYS2 + nasm under `deps\`, verify versions, child-only env script | `general-purpose` | `sonnet` | env pitfalls |
| 3 | Author `scripts/ffmpeg/d3d12va-reference-only-pool.patch`: read upstream `bcd2c69e08` (and its follow-ups touching the same function), adapt to 9.0.1, `git apply --check` on the extracted tree, explain each hunk; update `scripts/ffmpeg/README.md` next to `ps5-h264-slices.patch` (source commit, license LGPL, rationale) | `general-purpose` | `opus` | correctness-critical D3D12 resource lifetime |
| 4 | Build two FFmpeg variants (baseline vs patched) with `scripts/ffmpeg/prepare-slice-build.py`, `--reference-bin` = installed Veyra 2.2.0 DLL dir (read-only, to copy the LGPL configure); keep all repo patches applied in both, only the new patch differs | `general-purpose` (background, 2 parallel max) | `sonnet` | long build, log triage |
| 5 | A/B: `ffmpeg.exe -hwaccel d3d12va -i <HEVC 1080p, long GOP> -f null -` on this RX 9070 XT with `-v verbose`; count `No space for new Reference frame` / decode errors per variant; also H.264 | `general-purpose` | `haiku` | run + count |
| 6 | Independent adversarial review of the patch + A/B evidence (leaks, off-by-one on `max_num_ref+1`, non-reference-only GPUs unaffected) | `general-purpose` | `opus` | second opinion |
| 7 | Code: install `av_log_set_callback` capture for the remoteplay decoder path (pattern in `src/engine/ExportStreams.cpp:47`), bounded and rate-limited, into `veyra-qml.log` | `general-purpose` | `sonnet` | scoped C++ change |
| 8 | Real PS5 validation: copy installed Veyra to the private dir, swap `avcodec-63.dll` (+ matching avutil if ABI requires), Hardware mode, 1080p60 H.265, host 10.1.4.126. USER drives the console; orchestrator guides | orchestrator + user | — | needs the human |
| 9 | Parse the PS5 run log: teardown count, `code=-22`, `2004`, av_log lines | `Explore` or `general-purpose` | `haiku` | log scan |
| 10 | Separate bug, separate commit: reconnect backoff after decode teardown (`src/source/RemotePlaySessionSource.cpp:238` waits only 1 s → quitReason 4 / 2004 burns the 3-attempt budget) | `general-purpose` | `sonnet` | scoped fix |
| 11 | Commits (English, one per concern), then ASK before push to `origin` (fork) / PR to upstream `Likely7/Veyra-NRVideo` | orchestrator | — | user gate |

Dependencies: 1 ∥ 2 → 3 → 4 → 5 → 6. Phase 7 can run in parallel with 4–5 (different files). 8 needs 5 green + 7. 10 after 8. Out of scope: View Only / DualSense drop (`src/remoteplay/ChiakiBackend.cpp:201`) — separate issue.

## Subagent brief template

Each Agent call: `subagent_type`, `model` from the table, and a self-contained prompt with: goal, exact paths, the hard rules above that apply, time limits, and "return: commands run, exit codes, key log lines, output paths, anything not verified". Never tell a subagent to push, download without the user's yes, or touch the installed app.

## Stop conditions

- Patch does not apply / A/B shows no difference → stop, report evidence, ask the user (do not invent a workaround).
- Usage running low → finish the current phase, then save an ai-memory handoff (skill `ai-memory-handoff`) with the phase reached and paths.

## Finish

Per the user's global rule: when the fix is merged/tested, record in ai-memory the measured numbers, how it was solved, new gotchas, changed procedures/scripts and the final state, updating existing pages instead of duplicating.
