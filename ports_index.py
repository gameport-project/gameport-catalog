"""Writes the index of the VR ports: the list of the ports and the fingerprint of each manifest.

The app checks the fingerprint of a manifest against the index and leaves a port out when they differ, so the index has to follow the manifests. Nobody writes it by hand
and nobody counts versions: add or change `v1/vr-ports/ports/<id>/port.json`, and this fills the index (the workflow runs it on every change of a manifest).
`revision` is the one of the manifest, copied, and the app does not compare it: it is not something to raise when a port changes.

    python3 ports_index.py           writes v1/vr-ports/index.json
    python3 ports_index.py --check   writes nothing, and fails when the index is not what the manifests say
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = "v1/vr-ports"
SCHEMA_VERSION = 1


def build(root=ROOT):
    """The index of the manifests under root/ports, as a dict: the ports sorted by id."""
    entries = []
    ports = os.path.join(root, "ports")
    for port_id in sorted(os.listdir(ports)):
        path = os.path.join(ports, port_id, "port.json")
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as file:
            raw = file.read()
        manifest = json.loads(raw)
        if manifest.get("id") != port_id:
            raise ValueError(f"{path} is for '{manifest.get('id')}', not '{port_id}'")
        entries.append({
            "id": port_id,
            "path": f"ports/{port_id}/port.json",
            "revision": manifest.get("revision", 1),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return {"schemaVersion": SCHEMA_VERSION, "ports": entries}


def render(index):
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()
    text = render(build(args.root))
    path = os.path.join(args.root, "index.json")
    current = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if args.check:
        if current != text:
            sys.exit("the index of the ports is not what the manifests say: run python3 ports_index.py")
        print("the index of the ports is up to date")
    elif current != text:
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        print("index written")
    else:
        print("index already up to date")


if __name__ == "__main__":
    main()
