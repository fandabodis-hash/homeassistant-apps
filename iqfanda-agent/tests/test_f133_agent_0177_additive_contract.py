"""F133: Agent 0.1.177 must equal F99-verified 0.1.176 + Midea 00019 only.

Pure stdlib Git/source checks; no device access, no SSH, no cloud or Modbus I/O.
Fail the PR if an existing 0.1.176 file is modified outside the exact
allowlist, or any 176 behavior is changed inside the shared worker/heartbeat.
"""
from __future__ import annotations

import ast
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE_176 = "6a722bac445cb571e26048a88df75a01f34f8c16"

WORKER = "iqfanda-agent/rootfs/app/command_worker.py"
HEARTBEAT = "iqfanda-agent/rootfs/app/heartbeat.py"
CONFIG = "iqfanda-agent/config.yaml"
VERSION = "iqfanda-agent/rootfs/app/VERSION"

ADDED_ONLY = {
    ".github/workflows/f106-midea-00019-readonly.yml",
    "iqfanda-agent/docs/F114_00019_MIDEA_RTU_HOLDING_STATUS_20261123.md",
    "iqfanda-agent/docs/F133_0177_ADDITIVE_ONLY_CONTRACT_20261123.md",
    "iqfanda-agent/rootfs/app/communication/midea_00019_cloud_probe.py",
    "iqfanda-agent/rootfs/app/communication/midea_00019_ftdi_inventory.py",
    "iqfanda-agent/rootfs/app/communication/midea_00019_readonly.py",
    "iqfanda-agent/tests/test_f106_midea_00019_readonly.py",
    "iqfanda-agent/tests/test_f107_midea_00019_cloud_probe.py",
    "iqfanda-agent/tests/test_f132_midea_00019_ftdi_inventory.py",
    "iqfanda-agent/tests/test_f133_agent_0177_additive_contract.py",
    "iqfanda-agent/CHANGELOG.md",
}
# F137: reviewed release metadata, no changes in protected control algorithms.
MANIFEST_RELEASE = "iqfanda-agent/contracts/manual_battery_release.json"
MANIFEST_RUNTIME = "iqfanda-agent/rootfs/app/protection/manual_battery_manifest.json"
MODIFIED_ONLY = {WORKER, HEARTBEAT, CONFIG, VERSION, MANIFEST_RELEASE, MANIFEST_RUNTIME}


def git(*args: str) -> bytes:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, stderr=subprocess.PIPE, timeout=30
    )


def baseline(path: str) -> bytes:
    return git("show", f"{BASE_176}:{path}")


def local(path: str) -> bytes:
    return (ROOT / path).read_bytes()


def strip_one_insertion(data: bytes, *, start: bytes, end: bytes) -> bytes:
    assert data.count(start) == 1, "F133_EXPECTED_MIDEA_INSERTION_MISSING_OR_DUPLICATE"
    first, tail = data.split(start)
    assert tail.count(end) == 1, "F133_MIDEA_INSERTION_END_MISSING_OR_DUPLICATE"
    return first + end + tail.split(end, 1)[1]


class TestAgent0177OnlyAddsMidea(unittest.TestCase):
    def test_change_list_is_exclusively_approved_midea_scope(self):
        lines = git("diff", "--name-status", "--no-renames", BASE_176, "HEAD").decode().splitlines()
        actual = {}
        for line in lines:
            status, file_path = line.split("\t", 1)
            self.assertIn(status, {"M", "A"}, f"F133_UNEXPECTED_RENAME_DELETE_{file_path}")
            actual[file_path] = status
        self.assertEqual(set(actual), ADDED_ONLY | MODIFIED_ONLY,
                         f"F133_GLOBAL_176_CODE_CHANGED: {actual}")
        for path in ADDED_ONLY:
            self.assertEqual(actual[path], "A", f"F133_EXISTING_PATH_MODIFIED_{path}")
        for path in MODIFIED_ONLY:
            self.assertEqual(actual[path], "M", f"F133_EXPECTED_ONLY_VERSION_OR_MIDEA_HOOK_{path}")

    def test_worker_176_is_byte_identical_after_subtracting_exact_midea_handler(self):
        content = strip_one_insertion(
            local(WORKER),
            start=b"    # F107: strictly one device, read-only Midea RTU. No universal Modbus writes.\n",
            end=b'    if command_type == "photovoltaic_modbus_probe":\n',
        )
        self.assertEqual(content, baseline(WORKER), "F133_176_COMMAND_WORKER_CHANGED")

    def test_heartbeat_176_is_byte_identical_after_subtracting_exact_midea_hook(self):
        content = strip_one_insertion(
            local(HEARTBEAT),
            start=b"    # F132: exclusively for FANDA 00019 and only from real USB inventory.\n",
            end=b"    # Observation only: the local battery loop never waits for the heartbeat.\n",
        )
        self.assertEqual(content, baseline(HEARTBEAT), "F133_176_HEARTBEAT_CHANGED")

    def test_177_can_only_be_manually_installed_not_automatically(self):
        config = local(CONFIG)
        guard = b'breaking_versions:\n  - "0.1.177"\n  - "0.1.178"\n  - "0.1.179"\n'
        self.assertEqual(config.count(guard), 1, "F137_AUTO_UPDATE_NOT_BLOCKED")
        self.assertIn(b'boot: auto\n', config, "F137_EXISTING_BOOT_CHANGED")
        self.assertNotIn(b'boot: manual\n', config)

    def test_version_metadata_only_and_other_settings_unmodified(self):
        self.assertEqual(
            local(CONFIG).replace(
                b'# F137: manual-only for the fleet; auto-update MUST NOT cross 0.1.177.\n'
                b'breaking_versions:\n  - "0.1.177"\n  - "0.1.178"\n  - "0.1.179"\n', b""
            ).replace(b"0.1.179", b"0.1.176"), baseline(CONFIG),
            "F133_UNRELATED_ADDON_CONFIG_CHANGED"
        )
        self.assertEqual(local(VERSION).strip(), b"0.1.179")
        self.assertEqual(baseline(VERSION).strip(), b"0.1.176")

    def test_only_exact_midea_identity_enters_new_command_handler(self):
        tree = ast.parse(local(WORKER).decode("utf-8-sig"))
        matches = [
            node for node in ast.walk(tree) if isinstance(node, ast.If)
            and any(
                isinstance(child, ast.ImportFrom)
                and child.module == "communication.midea_00019_cloud_probe"
                for statement in node.body for child in ast.walk(statement)
            )
        ]
        self.assertEqual(len(matches), 1, "F133_MIDEA_HANDLER_NOT_EXACTLY_ONE")
        node = matches[0]
        self.assertIsInstance(node.test, ast.BoolOp)
        self.assertIsInstance(node.test.op, ast.And)
        condition = ast.unparse(node.test)
        for expected in (
            "midea_00019_modbus_readonly_v1",
            "648d4229-7c17-400f-bbed-ffe5ab04b844",
            "980e5e5e-76b4-4cac-9b6c-1f31fa5fd6e1",
        ):
            self.assertIn(expected, condition, "F133_MIDEA_COMMAND_NOT_UUID_SITE_GATED")

    def test_heartbeat_hook_is_uuid_only_and_does_not_read_modbus(self):
        text = local(HEARTBEAT).decode("utf-8-sig")
        segment = text.split(
            "    # F132: exclusively for FANDA 00019", 1
        )[1].split("    # Observation only:", 1)[0]
        self.assertIn("648d4229-7c17-400f-bbed-ffe5ab04b844", segment)
        self.assertIn("enrich_if_verified", segment)
        for forbidden in ("read_holding_registers", "read_input_registers",
                          "write_register", "write_coil", "execute_readonly_command"):
            self.assertNotIn(forbidden, segment, "F133_UNEXPECTED_HEARTBEAT_RTU_ACTION")


if __name__ == "__main__":
    unittest.main()
