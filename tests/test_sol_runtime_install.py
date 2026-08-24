from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = Path(_HERE).resolve().parents[0]
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from scripts.install_optional_cuda_acceleration import (  # noqa: E402
    FLASHATTENTION_WHEEL,
    SAGEATTENTION_WHEEL,
    install_optional_wheel,
    main as optional_install_main,
)
from scripts.verify_sol_runtime import (  # noqa: E402
    format_required_runtime_failure,
    normalize_capability,
    validate_required_runtime,
)


class TestOptionalLinuxAccelerationInstall(unittest.TestCase):
    def test_uses_prebuilt_cuda13_wheels(self):
        self.assertIn("sageattention-2.2.0-cp311-cp311-linux_x86_64.whl", SAGEATTENTION_WHEEL)
        self.assertIn("sha256=2ce936012a361e80", SAGEATTENTION_WHEEL)
        self.assertIn("cu130torch2.10-cp311-cp311-linux_x86_64.whl", FLASHATTENTION_WHEEL)

    def test_optional_wheel_failure_never_raises_or_blocks_runtime(self):
        calls = []

        def failed_runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=1, stdout="compiler output")

        with patch("scripts.install_optional_cuda_acceleration.shutil.which", return_value="uv"):
            self.assertFalse(
                install_optional_wheel(
                    "Optional test wheel",
                    "https://example.invalid/test.whl",
                    runner=failed_runner,
                )
            )

        self.assertEqual(calls[0][0][:3], ["uv", "pip", "install"])
        self.assertFalse(calls[0][1]["check"])

    def test_installer_oserror_and_missing_returncode_fail_open(self):
        def exploding_runner(command, **kwargs):
            raise subprocess.TimeoutExpired(cmd=command, timeout=1)

        with patch("scripts.install_optional_cuda_acceleration.shutil.which", return_value="uv"):
            self.assertFalse(
                install_optional_wheel(
                    "Optional timeout wheel",
                    "https://example.invalid/test.whl",
                    runner=exploding_runner,
                )
            )
            self.assertFalse(
                install_optional_wheel(
                    "Optional incomplete result",
                    "https://example.invalid/test.whl",
                    runner=lambda *args, **kwargs: SimpleNamespace(),
                )
            )

    def test_optional_main_never_fails_the_required_runtime(self):
        buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "env-sol" / ".maestro_flash.installed"
            marker.parent.mkdir(parents=True)
            marker.write_text("stale", encoding="utf-8")
            with patch(
                "scripts.install_optional_cuda_acceleration.install_optional_wheel",
                return_value=False,
            ) as mocked:
                with redirect_stdout(buffer):
                    self.assertEqual(
                        optional_install_main(
                            ["--flash-only", "--marker", "env-sol/.maestro_flash.installed"],
                            app_root=Path(temporary),
                        ),
                        0,
                    )
            self.assertFalse(marker.exists())
        mocked.assert_called_once()
        self.assertNotIn("Required CUDA 13 runtime verified", buffer.getvalue())
        self.assertNotIn("is ready", buffer.getvalue())

    def test_optional_main_publishes_marker_only_after_all_requested_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "env-sol" / ".maestro_flash.installed"
            with patch(
                "scripts.install_optional_cuda_acceleration.install_optional_wheel",
                side_effect=[True, True],
            ) as mocked:
                self.assertEqual(
                    optional_install_main(
                        ["--marker", "env-sol/.maestro_flash.installed"],
                        app_root=Path(temporary),
                    ),
                    0,
                )
            self.assertEqual(mocked.call_count, 2)
            self.assertTrue(marker.is_file())

    def test_sage_failure_keeps_shared_marker_missing_until_full_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "env-sol" / ".maestro_flash.installed"
            args = ["--marker", "env-sol/.maestro_flash.installed"]
            with patch(
                "scripts.install_optional_cuda_acceleration.install_optional_wheel",
                side_effect=[False, True],
            ) as first_attempt:
                self.assertEqual(
                    optional_install_main(args, app_root=Path(temporary)),
                    0,
                )
            self.assertEqual(first_attempt.call_count, 2)
            self.assertFalse(marker.exists())

            with patch(
                "scripts.install_optional_cuda_acceleration.install_optional_wheel",
                side_effect=[True, True],
            ) as retry:
                self.assertEqual(
                    optional_install_main(args, app_root=Path(temporary)),
                    0,
                )
            self.assertEqual(retry.call_count, 2)
            self.assertTrue(marker.is_file())


class TestRequiredSolRuntimeVerification(unittest.TestCase):
    def test_supported_cuda13_runtime_passes(self):
        self.assertEqual(
            validate_required_runtime(
                python_version="3.11.14",
                torch_version="2.10.0+cu130",
                cuda_version="13.0",
                triton_version="3.6.0",
                cuda_available=True,
                capability=(8, 9),
            ),
            [],
        )

    def test_cuda12_or_unsupported_gpu_does_not_publish_runtime_marker(self):
        problems = validate_required_runtime(
            python_version="3.11.14",
            torch_version="2.10.0+cu130",
            cuda_version="12.8",
            triton_version="3.6.0",
            cuda_available=True,
            capability=(8, 6),
        )
        self.assertTrue(any("CUDA 13" in problem for problem in problems))
        self.assertTrue(any("Sol-compatible" in problem for problem in problems))
        failure = format_required_runtime_failure(problems)
        self.assertIn("[Sol Runtime] Error:", failure)
        self.assertNotIn("verified", failure)

    def test_incomplete_capability_never_publishes_verified(self):
        self.assertIsNone(normalize_capability(None))
        self.assertIsNone(normalize_capability((12,)))
        self.assertIsNone(normalize_capability(("sm", "120")))
        problems = validate_required_runtime(
            python_version="3.11.14",
            torch_version="2.10.0+cu130",
            cuda_version="13.0",
            triton_version="3.6.0",
            cuda_available=True,
            capability=(12,),
        )
        self.assertTrue(any("Sol-compatible" in problem for problem in problems))
        self.assertNotIn("verified", format_required_runtime_failure(problems))

    def test_failed_main_does_not_claim_verified(self):
        from scripts import verify_sol_runtime

        fake_torch = SimpleNamespace(
            __version__="2.10.0+cu128",
            version=SimpleNamespace(cuda="12.8"),
            cuda=SimpleNamespace(
                is_available=lambda: True,
                get_device_capability=lambda device: (8, 6),
            ),
        )
        fake_triton = SimpleNamespace(__version__="3.6.0")
        buffer = io.StringIO()
        with patch.dict(sys.modules, {"torch": fake_torch, "triton": fake_triton}):
            with redirect_stdout(buffer):
                self.assertEqual(verify_sol_runtime.main(), 1)
        output = buffer.getvalue()
        self.assertIn("[Sol Runtime] Error:", output)
        self.assertIn("CUDA 13", output)
        self.assertNotIn("verified", output)

    def test_normal_start_selects_ready_sol_runtime_with_legacy_fallback(self):
        start = (_ROOT / "start.js").read_text(encoding="utf-8")
        start_sol = (_ROOT / "start_sol.js").read_text(encoding="utf-8")
        sol_install = (_ROOT / "sol_install.js").read_text(encoding="utf-8")
        self.assertIn('require("./launcher_profile")', start)
        self.assertIn("runtimeProfile(kernel)", start)
        self.assertIn("legacyRuntimeProfile(kernel)", start)
        self.assertIn("exists('${runtime.marker}') ? '${runtime.env}'", start)
        self.assertIn("venv: selectedEnv", start)
        self.assertIn("venv_python: selectedPython", start)
        self.assertIn("python launch.py", start)
        self.assertIn('"event": "/(http:\\/\\/[0-9.:]+)/"', start)
        self.assertIn('url: "{{input.event[1]}}"', start)
        self.assertNotIn("require(\"./start_sol\")", start)
        self.assertNotIn("MAESTRO_SOL_RUNTIME", start)
        self.assertIn('module.exports = require("./start")', start_sol)
        self.assertIn('module.exports = require("./update")', sol_install)
        self.assertNotIn("MAESTRO_SOL_RUNTIME", start_sol)

    def test_standard_install_and_update_own_profile_markers(self):
        install = (_ROOT / "install.js").read_text(encoding="utf-8")
        update = (_ROOT / "update.js").read_text(encoding="utf-8")
        torch_script = (_ROOT / "torch.js").read_text(encoding="utf-8")

        self.assertIn("runtimeProfile(kernel)", install)
        self.assertIn("venv: runtime.env", install)
        self.assertIn("venv_python: runtime.python", install)
        self.assertIn("needsCuda13DriverUpdate(kernel)", install)
        self.assertIn("runtimeProfile(kernel)", update)
        self.assertIn("exists('${runtime.marker}')", update)
        self.assertIn("exists('${runtime.flashMarker}')", update)
        self.assertIn("flash_only: true", update)
        self.assertIn("runtimeProfile(kernel)", torch_script)
        self.assertIn("path: runtime.marker", torch_script)
        self.assertIn("path: runtime.flashMarker", torch_script)
        self.assertIn("--marker ${appRelativeFlashMarker}", torch_script)
        self.assertIn("...(optionalMessage ? [] : [{", torch_script)
        self.assertNotIn(
            "install_optional_cuda_acceleration.py --flash-only",
            torch_script,
        )


if __name__ == "__main__":
    unittest.main()
