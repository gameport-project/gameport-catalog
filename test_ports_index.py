import hashlib
import json
import os
import tempfile
import unittest

import ports_index


def write_port(root, port_id, **extra):
    folder = os.path.join(root, "ports", port_id)
    os.makedirs(folder)
    raw = json.dumps({"schemaVersion": 1, "id": port_id, **extra}).encode()
    with open(os.path.join(folder, "port.json"), "wb") as file:
        file.write(raw)
    return raw


class PortsIndexTest(unittest.TestCase):
    def test_each_port_gets_the_fingerprint_of_its_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            raw = write_port(root, "quakequest")
            entry = ports_index.build(root)["ports"][0]
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])
            self.assertEqual("ports/quakequest/port.json", entry["path"])

    def test_the_ports_are_listed_by_id_whatever_the_order_they_were_added_in(self):
        with tempfile.TemporaryDirectory() as root:
            write_port(root, "zdoom")
            write_port(root, "arx")
            self.assertEqual(["arx", "zdoom"], [p["id"] for p in ports_index.build(root)["ports"]])

    def test_the_revision_is_the_one_of_the_manifest_and_one_when_it_has_none(self):
        with tempfile.TemporaryDirectory() as root:
            write_port(root, "a", revision=4)
            write_port(root, "b")
            self.assertEqual([4, 1], [p["revision"] for p in ports_index.build(root)["ports"]])

    def test_changing_a_manifest_changes_its_fingerprint_without_touching_a_revision(self):
        with tempfile.TemporaryDirectory() as root:
            write_port(root, "a", revision=1, name="Old")
            before = ports_index.build(root)["ports"][0]
            os.remove(os.path.join(root, "ports", "a", "port.json"))
            os.rmdir(os.path.join(root, "ports", "a"))
            write_port(root, "a", revision=1, name="New")
            after = ports_index.build(root)["ports"][0]
            self.assertNotEqual(before["sha256"], after["sha256"])
            self.assertEqual(before["revision"], after["revision"])

    def test_a_manifest_for_another_id_than_its_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "ports", "a")
            os.makedirs(folder)
            with open(os.path.join(folder, "port.json"), "w") as file:
                json.dump({"id": "b"}, file)
            with self.assertRaises(ValueError):
                ports_index.build(root)

    def test_the_index_in_the_repository_is_what_the_manifests_say(self):
        current = open("v1/vr-ports/index.json", encoding="utf-8").read()
        self.assertEqual(current, ports_index.render(ports_index.build("v1/vr-ports")))


if __name__ == "__main__":
    unittest.main()
