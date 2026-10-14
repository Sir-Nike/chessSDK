#!/usr/bin/env python3
"""Freeze target Linux wheels and uv binaries before offline Docker capture."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {
    "manylinux2014_x86_64": "x86_64-manylinux2014",
    "manylinux2014_aarch64": "aarch64-manylinux2014",
    "manylinux_2_17_x86_64": "x86_64-manylinux2014",
    "manylinux_2_17_aarch64": "aarch64-manylinux2014",
}


def fetch(artifact, directory, allowed_hashes):
    expected = artifact["hashes"]["sha256"]
    if expected not in allowed_hashes:
        raise ValueError("Artifact differs from the committed uv.lock")
    url = urllib.parse.urlparse(artifact["url"])
    if url.scheme != "https":
        raise ValueError("Artifact download requires HTTPS")
    path = directory / Path(url.path).name
    with urllib.request.urlopen(artifact["url"], timeout=60) as response, path.open("wb") as target:
        shutil.copyfileobj(response, target)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Artifact SHA-256 mismatch")
    return path


def prepare(args):
    platform = PLATFORMS.get(args.platform, args.platform)
    python_version = args.python
    if "." not in python_version:
        python_version = python_version[0] + "." + python_version[1:]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages = {(p["name"], p["version"]): p for p in lock["package"] if "version" in p}
    destination = ROOT / "vendor" / ("check-wheels" if args.check else "wheels")
    with tempfile.TemporaryDirectory(prefix="wheel-prepare-") as folder:
        temporary = Path(folder)
        exported = temporary / "requirements.txt"
        command = ["uv", "export", "--project", str(ROOT), "--locked", "--no-emit-project",
                   "--no-dev", "--no-hashes", "--no-header", "--no-annotate", "--quiet",
                   "--output-file", str(exported)]
        if args.check:
            command += ["--only-group", "check"]
        subprocess.run(command, check=True)
        pylock = temporary / "pylock.target.toml"
        subprocess.run(["uv", "pip", "compile", str(exported), "--python-platform", platform,
                        "--python-version", python_version, "--format", "pylock.toml",
                        "--output-file", str(pylock), "--quiet"], check=True)
        resolved = tomllib.loads(pylock.read_text())
        for package in resolved["packages"]:
            frozen = packages[(package["name"], package["version"])]
            allowed = {a["hash"].removeprefix("sha256:") for a in frozen.get("wheels", [])}
            if frozen.get("sdist"):
                allowed.add(frozen["sdist"]["hash"].removeprefix("sha256:"))
            if package.get("wheels"):
                if len(package["wheels"]) != 1:
                    raise ValueError("Target resolution must select one wheel per package")
                fetch(package["wheels"][0], temporary, allowed)
            else:
                # Pure rules libraries such as chess publish source only. Freeze its
                # source hash and build locally with uv before the offline capture.
                source = fetch(package["sdist"], temporary, allowed)
                constraints = temporary / "build-constraints.txt"
                constraints.write_text("setuptools==81.0.0\nwheel==0.45.1\n")
                subprocess.run(["uv", "build", "--no-build-logs", "--wheel", str(source), "--out-dir", folder,
                                "--build-constraints", str(constraints)], check=True)
                built = list(temporary.glob(package["name"].replace("-", "_") + "-*.whl"))
                if not built or any(not p.name.endswith("-none-any.whl") for p in built):
                    raise ValueError("Source-only dependencies must produce pure Python wheels")
        wheels = sorted(temporary.glob("*.whl"))
        if not wheels or (args.check and any(not p.name.endswith("-none-any.whl") for p in wheels)):
            raise ValueError("No compatible pure checker wheels prepared")
        destination.mkdir(parents=True, exist_ok=True)
        for old in destination.glob("*.whl"):
            old.unlink()
        manifest = {}
        for wheel in wheels:
            shutil.copy2(wheel, destination / wheel.name)
            manifest[wheel.name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        (destination / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2) + "\n")
        shutil.copy2(exported, ROOT / ("requirements-check.txt" if args.check else "requirements.txt"))

        # Two pinned static binaries allow offline builds on Mac ARM and Linux x86.
        uv_package = next(p for p in lock["package"] if p["name"] == "uv")
        binaries = ROOT / "vendor" / "uv"
        binaries.mkdir(parents=True, exist_ok=True)
        binary_manifest = {"version": uv_package["version"], "binaries": {}}
        for arch, suffix in (("amd64", "manylinux_2_17_x86_64.manylinux2014_x86_64.whl"),
                             ("arm64", "manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64.whl")):
            artifact = next(w for w in uv_package["wheels"] if w["url"].endswith(suffix))
            wheel = fetch({"url": artifact["url"], "hashes": {"sha256": artifact["hash"].removeprefix("sha256:")}},
                          temporary, {artifact["hash"].removeprefix("sha256:")})
            frozen_wheel = binaries / ("uv-" + arch + ".whl")
            shutil.copy2(wheel, frozen_wheel)
            binary_manifest["binaries"][frozen_wheel.name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        (binaries / "SHA256SUMS.json").write_text(json.dumps(binary_manifest, indent=2) + "\n")
    print(f"Prepared {len(wheels)} frozen wheels and pinned uv binaries in {ROOT / 'vendor'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", default="manylinux2014_x86_64")
    parser.add_argument("--python", default="312")
    parser.add_argument("--check", action="store_true")
    prepare(parser.parse_args())
