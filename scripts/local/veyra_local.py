"""Local Veyra release pipeline: verified donor -> versioned stage -> Setup EXE -> install.

For the developer's own machine only. It never publishes, signs or uploads.

  donor      Snapshot an official per-GPU payload, every file verified against its
             package-manifest.json (wrong copies may be replaced from --candidates).
  stage      Hard-link the donor into a new local version and apply overlays
             (locally built DLLs/EXEs/shaders), rewriting the manifest.
  installer  Compile the stage with scripts/installer/Veyra.iss (Inno Setup 7).
  install    Run the generated Setup over the existing per-user installation.
  release    stage + installer (+ install with --install).
  prune      Remove old version folders from the installation (keeps current/previous).
  status     Show config, donor, last local version and installed version.

Local versions are X.Y.((Z+1)*1000+N) on top of the donor X.Y.Z, e.g. 2.2.1001,
so they always upgrade an official X.Y.Z and stay below the next official X.(Y+1).0.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(os.environ.get("VEYRA_LOCAL_CONFIG", ROOT.parent / "veyra-local" / "config.json"))
MANIFEST = "package-manifest.json"
WORKER = "veyra_update_worker.exe"
PLAYER = "veyra_qml_ui.exe"
ROOT_FILES = ("Veyra.exe", "portable-update-trust.json")
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def say(message):
    print(message, flush=True)


def is_plain(path):
    """No symlink/junction anywhere in the absolute path."""
    path = Path(path).resolve(strict=False)
    for part in [path, *path.parents]:
        try:
            if part.lstat().st_file_attributes & REPARSE:
                return False
        except FileNotFoundError:
            continue
    return True


RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_path(name):
    """Same rule as package-portable-update.safe_path, without PureWindowsPath.is_reserved
    (removed in Python 3.15)."""
    return (0 < len(name) < 500 and not any(c in name for c in '\\:"*?<>|')
            and not any(ord(c) < 32 for c in name)
            and all(part not in {"", ".", ".."} and not part.endswith((" ", "."))
                    and part.split(".")[0].upper() not in RESERVED for part in name.split("/")))


def require_inside(path, parent, what):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if not path.is_relative_to(parent) or not is_plain(path):
        raise SystemExit(f"{what} must be a plain path inside {parent}: {path}")
    return path


# --------------------------------------------------------------------------- config / state

class Config:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise SystemExit(f"Config not found: {self.path}\nCopy scripts/local/config.example.json there and edit it.")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = data
        self.artifacts = Path(data["artifactsRoot"]).resolve()
        self.flavor = data["flavor"]
        if self.flavor not in ("AMD", "NVIDIA"):
            raise SystemExit("flavor must be AMD or NVIDIA")
        self.install_dir = Path(os.path.expandvars(data["installDir"])).resolve()
        self.iscc = Path(data["iscc"]).resolve()
        self.donor = Path(data["donor"]).resolve()
        self.compression = data.get("compression", "zip/1")
        self.overlays = data.get("overlays", [])
        self.state_path = self.path.with_name("state.json")

    def dir(self, kind, *parts):
        return self.artifacts.joinpath(kind, "local-release", *parts)

    def state(self):
        if self.state_path.is_file():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"counters": {}, "history": []}

    def save_state(self, state):
        tmp = self.state_path.with_suffix(".json.new")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)


def local_version(base, counter):
    major, minor, patch = (int(p) for p in base.split("."))
    value = (patch + 1) * 1000 + counter
    if counter < 1 or counter > 999 or value > 65535:
        raise SystemExit(f"Local version out of range for base {base} (counter {counter})")
    return f"{major}.{minor}.{value}"


def git_info():
    def run(*args):
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else ""
    return {"commit": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


# --------------------------------------------------------------------------- donor

def cmd_donor(cfg, args):
    install = Path(args.from_install or cfg.install_dir).resolve()
    current = (install / "current-version.txt").read_text(encoding="ascii").splitlines()[0]
    source = install / "versions" / current
    manifest = json.loads((source / MANIFEST).read_bytes())
    if manifest["gpuPackage"] != cfg.flavor:
        raise SystemExit(f"Installed flavor {manifest['gpuPackage']} != config flavor {cfg.flavor}")
    version = manifest["version"]
    destination = cfg.donor
    require_inside(destination, cfg.artifacts, "donor")
    if destination.exists():
        raise SystemExit(f"Donor already exists (delete it first to rebuild): {destination}")
    candidates = [source] + [Path(c).resolve() for c in args.candidates or []]

    work = destination.with_name(destination.name + ".partial")
    if work.exists():
        shutil.rmtree(work)
    payload_dir = work / "version"
    replaced = {}
    for row in manifest["files"]:
        name, want = row["path"], row["sha256"].lower()
        for candidate in candidates:
            for path in (candidate / name, candidate / Path(name).name):
                if path.is_file() and path.stat().st_size == row["size"] and digest(path) == want:
                    target = payload_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    if candidate != source:
                        replaced[name] = str(path)
                    break
            else:
                continue
            break
        else:
            shutil.rmtree(work)
            raise SystemExit(f"No copy of {name} matches the official manifest; pass --candidates <dir with originals>")
    shutil.copy2(source / MANIFEST, payload_dir / MANIFEST)
    shutil.copy2(source / WORKER, payload_dir / WORKER)
    (work / "root").mkdir()
    for name in ROOT_FILES:
        shutil.copy2(install / name, work / "root" / name)
    info = {"version": version, "flavor": cfg.flavor, "source": str(source),
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "manifestFiles": len(manifest["files"]), "replacedFromCandidates": replaced,
            "unverified": {f"version/{WORKER}": digest(payload_dir / WORKER),
                           **{f"root/{n}": digest(work / "root" / n) for n in ROOT_FILES}}}
    (work / "donor.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    os.replace(work, destination)
    say(json.dumps(info, indent=2))


def read_donor(cfg):
    info = json.loads((cfg.donor / "donor.json").read_text(encoding="utf-8"))
    manifest = json.loads((cfg.donor / "version" / MANIFEST).read_bytes())
    return info, manifest


# --------------------------------------------------------------------------- stage

def link_or_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def place_new_file(source, target):
    """Never write through a hard link: copy to a new file, then atomically replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".overlay-new")
    shutil.copy2(source, tmp)
    os.replace(tmp, target)


def overlay_items(cfg):
    for overlay in cfg.overlays:
        if not overlay.get("enabled", True):
            continue
        base = Path(overlay["from"]).resolve()
        files = overlay.get("files")
        if files is None:
            files = [p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()]
        for name in files:
            source = base / name
            if not source.is_file():
                raise SystemExit(f"Overlay {overlay['name']}: missing {source}")
            dest = (overlay.get("to", "").strip("/") + "/" + name).lstrip("/")
            yield overlay, source, dest


def cmd_stage(cfg, args):
    qml = load_module("qml_package", "package-qml-release.py")
    portable = load_module("portable_package", "package-portable-update.py")
    info, manifest = read_donor(cfg)
    base = info["version"]
    state = cfg.state()
    counter = state["counters"].get(base, 0) + 1
    version = args.version or local_version(base, counter)
    portable.canonical_version(version)
    if tuple(map(int, version.split("."))) <= tuple(map(int, base.split("."))):
        raise SystemExit(f"Version {version} is older than the donor {base}")
    stage = cfg.dir("build", version, "stage")
    require_inside(stage, cfg.artifacts, "stage")
    if stage.exists():
        raise SystemExit(f"Stage already exists: {stage}")

    work = stage.with_name("stage.partial")
    if work.exists():
        shutil.rmtree(work)
    rows = {row["path"]: dict(row) for row in manifest["files"]}
    for name in rows:
        link_or_copy(cfg.donor / "version" / name, work / "version" / name)
    link_or_copy(cfg.donor / "version" / WORKER, work / "version" / WORKER)
    for name in ROOT_FILES:
        link_or_copy(cfg.donor / "root" / name, work / "root" / name)

    applied = []
    for overlay, source, dest in overlay_items(cfg):
        if dest.startswith("@root/"):
            name = dest[len("@root/"):]
            if name not in ROOT_FILES:
                raise SystemExit(f"Overlay {overlay['name']}: unknown root file {name}")
            place_new_file(source, work / "root" / name)
        else:
            if not safe_path(dest):
                raise SystemExit(f"Overlay {overlay['name']}: unsafe path {dest}")
            if dest != WORKER:
                qml.validate_payload(dest)
                if dest not in rows and not overlay.get("allowNew", False):
                    raise SystemExit(f"Overlay {overlay['name']}: {dest} is not in the donor (set allowNew)")
            place_new_file(source, work / "version" / dest)
            if dest != WORKER:
                rows[dest] = {"path": dest, "size": source.stat().st_size, "sha256": digest(source)}
        applied.append({"overlay": overlay["name"], "path": dest, "source": str(source), "sha256": digest(source)})

    qml.check_models(work / "version")
    missing = qml.REQUIRED - rows.keys()
    if missing:
        raise SystemExit("Required files missing: " + ", ".join(sorted(missing)))

    local_manifest = {k: v for k, v in manifest.items() if k not in ("files", "releaseUrl", "approvedDonorSha256", "exeSha256")}
    local_manifest.update(version=version, localOnly=True, releaseReady=False, releaseAuthorized=False,
                          exeSha256=digest(work / "version" / PLAYER),
                          localBuild={"baseVersion": base, "counter": counter,
                                      "created": datetime.datetime.now().isoformat(timespec="seconds"),
                                      "git": git_info(), "overlays": applied},
                          files=sorted(rows.values(), key=lambda r: r["path"]))
    (work / "version" / MANIFEST).write_text(json.dumps(local_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(work, stage)
    if not args.version:
        state["counters"][base] = counter
    state["history"].append({"version": version, "stage": str(stage), "overlays": len(applied),
                             "git": local_manifest["localBuild"]["git"]})
    cfg.save_state(state)
    say(f"stage {version}: {len(rows)} files, {len(applied)} overlaid -> {stage}")
    for item in applied:
        say(f"  overlay {item['overlay']}: {item['path']}  {item['sha256'][:16]}")
    return version, stage


# --------------------------------------------------------------------------- installer

def cmd_installer(cfg, args, version=None):
    installer = load_module("windows_installer", "package-windows-installer.py")
    version = version or args.version or cfg.state()["history"][-1]["version"]
    stage = cfg.dir("build", version, "stage")
    manifest_path = stage / "version" / MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    if manifest["version"] != version or manifest["gpuPackage"] != cfg.flavor:
        raise SystemExit("Stage manifest does not match the requested version/flavor")
    if not cfg.iscc.is_file():
        raise SystemExit(f"ISCC.exe not found: {cfg.iscc}")
    out_dir = cfg.dir("releases")
    out_dir.mkdir(parents=True, exist_ok=True)
    output_name = f"Veyra-{version}-{cfg.flavor}-win64-setup.exe"
    destination = out_dir / output_name
    if destination.exists():
        raise SystemExit(f"Installer already exists: {destination}")
    log = cfg.dir("logs", f"{version}-iscc.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    tmp_root = cfg.dir("tmp")
    tmp_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="iscc-", dir=tmp_root) as directory_string:
        directory = Path(directory_string)
        temp_output = directory / "output"
        temp_output.mkdir()
        panel, icon = installer.compose_branding(directory)
        (directory / "portable-package.txt").write_bytes(f"VEYRA_PORTABLE_V1\n{cfg.flavor}\n".encode("ascii"))
        (directory / "veyra-installation.txt").write_bytes(f"Veyra.Win64.{cfg.flavor}".encode("ascii"))
        version_dir = rf"\versions\{version}"
        rows = [
            installer.installer_file(stage / "root" / "Veyra.exe", ""),
            installer.installer_file(stage / "root" / "portable-update-trust.json", ""),
            installer.installer_file(directory / "portable-package.txt", ""),
            installer.installer_file(directory / "veyra-installation.txt", ""),
            installer.installer_file(stage / "version" / WORKER, version_dir),
            installer.installer_file(manifest_path, version_dir),
            installer.installer_file(cfg.iscc.parent / "license.txt", version_dir + r"\licenses", "INNO-SETUP.txt"),
        ]
        for row in manifest["files"]:
            name = row["path"]
            parent = name.rpartition("/")[0]
            folder = version_dir + ("\\" + parent.replace("/", "\\") if parent else "")
            rows.append(installer.installer_file(stage / "version" / name, folder))
        file_list = directory / "files.iss"
        file_list.write_text("\n".join(rows) + "\n", encoding="utf-8-sig")
        defines = dict(Flavor=cfg.flavor, AppVersion=version, OutputDir=temp_output, PayloadList=file_list,
                       AppIcon=ROOT / "assets/veyra.ico", BrandBackground=ROOT / "qml/Veyra/backdrop-dusk.png",
                       BrandPanel=panel, BrandIcon=icon, SetupCompression=cfg.compression)
        command = [str(cfg.iscc), "/Qp", *(f"/D{k}={os.fspath(v) if isinstance(v, Path) else v}"
                                           for k, v in defines.items()),
                   str(ROOT / "scripts/installer/Veyra.iss")]
        env = {**os.environ, "TEMP": str(directory), "TMP": str(directory)}
        say(f"compiling {output_name} ({len(rows)} entries, compression {cfg.compression}) ...")
        started = datetime.datetime.now()
        with log.open("wb") as handle:
            result = subprocess.run(command, cwd=directory, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                    timeout=args.timeout)
        if result.returncode != 0:
            raise SystemExit(f"ISCC failed ({result.returncode}); see {log}")
        intermediate = temp_output / output_name
        with intermediate.open("rb") as archive:
            if archive.read(2) != b"MZ":
                raise SystemExit("ISCC did not produce a Windows executable")
        shutil.move(intermediate, destination)
    seconds = (datetime.datetime.now() - started).total_seconds()
    say(json.dumps({"installer": str(destination), "version": version, "flavor": cfg.flavor,
                    "size": destination.stat().st_size, "sha256": digest(destination),
                    "seconds": round(seconds, 1), "log": str(log)}, indent=2))
    return destination


# --------------------------------------------------------------------------- install / prune

def installed_versions(cfg):
    state = cfg.install_dir / "current-version.txt"
    if not state.is_file():
        return None, None
    lines = state.read_text(encoding="ascii").split("\n")
    return lines[0] or None, (lines[1] if len(lines) > 1 and lines[1] else None)


def cmd_install(cfg, args, setup=None):
    version = args.version or cfg.state()["history"][-1]["version"]
    setup = setup or cfg.dir("releases", f"Veyra-{version}-{cfg.flavor}-win64-setup.exe")
    if not Path(setup).is_file():
        raise SystemExit(f"Installer not found: {setup}")
    if (cfg.install_dir / ".veyra-running.lock").exists():
        say("warning: Veyra may be running; close it first if Setup refuses.")
    log = cfg.dir("logs", f"{version}-install.log")
    command = [str(setup), f"/DIR={cfg.install_dir}", f"/LOG={log}", "/NORESTART"]
    if not args.interactive:
        command += ["/SILENT", "/SUPPRESSMSGBOXES"]
    say("running " + " ".join(command))
    result = subprocess.run(command)
    current, previous = installed_versions(cfg)
    say(f"Setup exit={result.returncode}; installed current={current} previous={previous}; log {log}")
    if result.returncode != 0 or current != version:
        raise SystemExit(1)


def cmd_prune(cfg, args):
    current, previous = installed_versions(cfg)
    versions = cfg.install_dir / "versions"
    keep = {v for v in (current, previous) if v}
    old = [p for p in versions.iterdir() if p.is_dir() and p.name not in keep]
    if not old:
        say(f"nothing to prune (keeping {sorted(keep)})")
        return
    for path in old:
        say(("removing " if args.yes else "would remove ") + str(path))
        if args.yes:
            if not is_plain(path) or not path.resolve().is_relative_to(versions.resolve()):
                raise SystemExit(f"refusing to delete non-plain path {path}")
            shutil.rmtree(path)
    if not args.yes:
        say("re-run with --yes to delete")


def cmd_status(cfg, args):
    current, previous = installed_versions(cfg)
    state = cfg.state()
    donor = cfg.donor / "donor.json"
    say(json.dumps({"config": str(cfg.path), "flavor": cfg.flavor, "installDir": str(cfg.install_dir),
                    "installed": {"current": current, "previous": previous},
                    "donor": json.loads(donor.read_text()) ["version"] if donor.is_file() else None,
                    "iscc": cfg.iscc.is_file(), "lastLocal": state["history"][-1] if state["history"] else None,
                    "overlays": [{"name": o["name"], "enabled": o.get("enabled", True)} for o in cfg.overlays]},
                   indent=2))


def cmd_release(cfg, args):
    version, _ = cmd_stage(cfg, args)
    setup = cmd_installer(cfg, args, version)
    if args.install:
        args.version = version
        cmd_install(cfg, args, setup)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("donor")
    p.add_argument("--from-install", help="installation root (default: config installDir)")
    p.add_argument("--candidates", nargs="*", help="dirs holding official copies of files modified in the install")
    for name in ("stage", "installer", "install", "release"):
        p = sub.add_parser(name)
        p.add_argument("--version", help="explicit X.Y.Z (default: next local version / last staged)")
        p.add_argument("--timeout", type=int, default=880)
        p.add_argument("--interactive", action="store_true", help="show the Setup wizard")
        p.add_argument("--install", action="store_true", help="release: also install")
    p = sub.add_parser("prune")
    p.add_argument("--yes", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()
    cfg = Config(args.config)
    {"donor": cmd_donor, "stage": cmd_stage, "installer": cmd_installer, "install": cmd_install,
     "release": cmd_release, "prune": cmd_prune, "status": cmd_status}[args.command](cfg, args)


if __name__ == "__main__":
    main()
