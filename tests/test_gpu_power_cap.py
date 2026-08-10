"""Model-free contracts for the local NVIDIA power-cap administrator."""

from __future__ import annotations

import stat
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from app.services import gpu_power_cap


QUERY = (
    "0, GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee, NVIDIA RTX Test, "
    "400.00, 600.00, 600.00, 600.00\n"
)


class GpuPowerCapTests(unittest.TestCase):
    def test_query_parser_preserves_exact_identity_and_supported_range(self):
        state = gpu_power_cap.parse_power_query(QUERY)[0]
        self.assertEqual(state.index, 0)
        self.assertEqual(state.uuid, "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertEqual(state.name, "NVIDIA RTX Test")
        self.assertEqual(
            (state.minimum_watts, state.maximum_watts), (400.0, 600.0),
        )
        self.assertEqual(gpu_power_cap.rollback_watts(state), 600)

    def test_selection_requires_exact_uuid_when_multiple_gpus_exist(self):
        states = gpu_power_cap.parse_power_query(
            QUERY
            + "1, GPU-11111111-2222-3333-4444-555555555555, NVIDIA RTX Other, "
            "300.00, 500.00, 450.00, 450.00\n"
        )
        with self.assertRaisesRegex(gpu_power_cap.GpuPowerCapError, "Multiple"):
            gpu_power_cap.select_gpu(states)
        selected = gpu_power_cap.select_gpu(
            states, gpu_uuid="GPU-11111111-2222-3333-4444-555555555555",
        )
        self.assertEqual(selected.index, 1)

    def test_target_must_be_a_whole_watt_inside_live_range(self):
        state = gpu_power_cap.parse_power_query(QUERY)[0]
        self.assertEqual(gpu_power_cap.validate_target_watts(state, 575), 575)
        for invalid in (True, 575.5, 399, 601, float("nan")):
            with self.subTest(invalid=invalid), self.assertRaises(
                gpu_power_cap.GpuPowerCapError,
            ):
                gpu_power_cap.validate_target_watts(state, invalid)

    def test_privileged_executables_must_be_root_owned_and_not_writable(self):
        with mock.patch.object(
            gpu_power_cap, "_trusted_root_executable", return_value=False,
        ), self.assertRaisesRegex(
            gpu_power_cap.GpuPowerCapError, "trusted root-owned executable",
        ):
            gpu_power_cap.discover_gpu_power_states(
                which=lambda _name: "/tmp/untrusted/nvidia-smi",
            )

    def test_trust_requires_nonwritable_root_owned_ancestor_chain(self):
        regular = stat.S_IFREG | 0o755
        directory = stat.S_IFDIR | 0o755
        writable_directory = stat.S_IFDIR | 0o777
        metadata = {
            "/safe/bin/nvidia-smi": SimpleNamespace(st_uid=0, st_mode=regular),
            "/safe/bin": SimpleNamespace(st_uid=0, st_mode=directory),
            "/safe": SimpleNamespace(st_uid=0, st_mode=writable_directory),
            "/": SimpleNamespace(st_uid=0, st_mode=directory),
        }
        with mock.patch.object(
            gpu_power_cap.os, "stat", side_effect=metadata.__getitem__,
        ), mock.patch.object(gpu_power_cap.os, "access", return_value=True):
            self.assertFalse(gpu_power_cap._trusted_root_executable(
                "/safe/bin/nvidia-smi",
            ))

    def test_trust_requires_root_owned_regular_nonwritable_executable_file(self):
        directory = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        cases = (
            ("trusted", 0, stat.S_IFREG | 0o755, True, True),
            ("non_root", 1000, stat.S_IFREG | 0o755, True, False),
            ("writable", 0, stat.S_IFREG | 0o775, True, False),
            ("not_regular", 0, stat.S_IFDIR | 0o755, True, False),
            ("not_executable", 0, stat.S_IFREG | 0o644, False, False),
        )
        for label, uid, mode, executable, expected in cases:
            with self.subTest(label=label):
                file_metadata = SimpleNamespace(st_uid=uid, st_mode=mode)

                def metadata(path):
                    return file_metadata if path == "/safe/bin/nvidia-smi" else directory

                with mock.patch.object(
                    gpu_power_cap.os, "stat", side_effect=metadata,
                ), mock.patch.object(
                    gpu_power_cap.os, "access", return_value=executable,
                ):
                    self.assertEqual(
                        gpu_power_cap._trusted_root_executable(
                            "/safe/bin/nvidia-smi",
                        ),
                        expected,
                    )

    def test_application_uses_pkexec_exact_uuid_and_verifies_result(self):
        calls = []
        query_count = 0

        def runner(command, **kwargs):
            nonlocal query_count
            calls.append((list(command), dict(kwargs)))
            if command[0] == "/usr/bin/pkexec":
                return subprocess.CompletedProcess(command, 0, "", "")
            query_count += 1
            current = "600.00" if query_count == 1 else "575.00"
            return subprocess.CompletedProcess(
                command, 0, QUERY.rsplit("600.00", 1)[0] + current + "\n", "",
            )

        paths = {
            "nvidia-smi": "/usr/bin/nvidia-smi",
            "pkexec": "/usr/bin/pkexec",
        }
        with mock.patch.object(
            gpu_power_cap, "_trusted_root_executable", return_value=True,
        ):
            state = gpu_power_cap.apply_power_limit(
                gpu_uuid="GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                watts=575,
                runner=runner,
                which=paths.get,
            )
        self.assertEqual(state.current_watts, 575.0)
        privileged = [command for command, _kwargs in calls if command[0] == "/usr/bin/pkexec"]
        self.assertEqual(privileged, [[
            "/usr/bin/pkexec", "/usr/bin/nvidia-smi", "-i",
            "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "-pl", "575",
        ]])
        self.assertTrue(all(kwargs.get("capture_output") for _command, kwargs in calls))

    def test_unverified_application_fails(self):
        def runner(command, **_kwargs):
            if command[0] == "/usr/bin/pkexec":
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, QUERY, "")

        with mock.patch.object(
            gpu_power_cap, "_trusted_root_executable", return_value=True,
        ), self.assertRaisesRegex(
            gpu_power_cap.GpuPowerCapError, "did not verify",
        ):
            gpu_power_cap.apply_power_limit(
                gpu_uuid="GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                watts=575,
                runner=runner,
                which={
                    "nvidia-smi": "/usr/bin/nvidia-smi",
                    "pkexec": "/usr/bin/pkexec",
                }.get,
            )


if __name__ == "__main__":
    unittest.main()
