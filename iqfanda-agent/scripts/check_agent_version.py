#!/usr/bin/env python3
"""Validate consistent add-on metadata and a minimum supported semantic version."""
import argparse
import re
from pathlib import Path


def semver(value):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError("Version must be X.Y.Z")
    return tuple(int(part) for part in value.split("."))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum", required=True)
    args = parser.parse_args()
    addon = Path(__file__).resolve().parents[1]
    version = (addon / "rootfs/app/VERSION").read_text().strip()
    if semver(version) < semver(args.minimum):
        raise SystemExit("VERSION is below the protected feature baseline")
    config = (addon / "config.yaml").read_text(encoding="utf-8-sig")
    for prefix in ("version", "  software_version"):
        if not re.search(r"^" + prefix + r':\s*"' + re.escape(version) + r'"\s*$', config, re.M):
            raise SystemExit("VERSION/config.yaml/software_version differ")
    print("AGENT_VERSION_CONTRACT=PASS version=" + version + " minimum=" + args.minimum)


if __name__ == "__main__":
    main()
