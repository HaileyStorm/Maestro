import ast
import asyncio
import json
import os
import stat
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
LAUNCH_PATH = APP_ROOT / "launch.py"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services import audio_upload_validation as validation  # noqa: E402
from services.output_access import write_upload_access_sidecar  # noqa: E402


def _chunk(kind: bytes, payload: bytes) -> bytes:
    padding = b"\0" if len(payload) & 1 else b""
    return kind + struct.pack("<I", len(payload)) + payload + padding


def _wav(
    *,
    format_tag: int = 1,
    channels: int = 1,
    sample_rate: int = 16_000,
    bits: int = 16,
    frames: int = 1_600,
    extensible_subformat: int | None = None,
    classic_extension: bytes | None = None,
    extra_chunks: tuple[bytes, ...] = (),
) -> bytes:
    block_align = channels * ((bits + 7) // 8)
    if extensible_subformat is None:
        fmt = struct.pack(
            "<HHIIHH",
            format_tag,
            channels,
            sample_rate,
            sample_rate * block_align,
            block_align,
            bits,
        )
        if classic_extension is not None:
            fmt += struct.pack("<H", len(classic_extension)) + classic_extension
    else:
        guid = struct.pack("<I", extensible_subformat) + bytes.fromhex(
            "00001000800000aa00389b71"
        )
        fmt = struct.pack(
            "<HHIIHHHHI",
            0xFFFE,
            channels,
            sample_rate,
            sample_rate * block_align,
            block_align,
            bits,
            22,
            bits,
            0,
        ) + guid
    payload = (
        _chunk(b"fmt ", fmt)
        + b"".join(extra_chunks)
        + _chunk(b"data", bytes(frames * block_align))
    )
    return b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WAVE" + payload


class WavContainerValidationTests(unittest.TestCase):
    def test_accepts_supported_pcm_float_extensible_and_odd_metadata(self):
        cases = (
            (_wav(bits=8), "pcm", 8, 1),
            (_wav(bits=16), "pcm", 16, 1),
            (_wav(channels=2), "pcm", 16, 2),
            (_wav(bits=24), "pcm", 24, 1),
            (_wav(bits=32), "pcm", 32, 1),
            (_wav(classic_extension=b""), "pcm", 16, 1),
            (_wav(format_tag=3, bits=32), "ieee_float", 32, 1),
            (_wav(format_tag=3, bits=64), "ieee_float", 64, 1),
            (_wav(bits=24, extensible_subformat=1), "pcm", 24, 1),
            (
                _wav(extra_chunks=(_chunk(b"JUNK", b"x"),)),
                "pcm",
                16,
                1,
            ),
        )
        for content, codec, bits, channels in cases:
            with self.subTest(codec=codec, bits=bits):
                result = validation.validate_wav_upload(
                    content,
                    max_bytes=len(content),
                )
                self.assertEqual(result.codec, codec)
                self.assertEqual(result.sample_width_bits, bits)
                self.assertEqual(result.channels, channels)
                self.assertEqual(result.sample_rate, 16_000)
                self.assertEqual(result.duration_seconds, 0.1)

    def test_rejects_unsupported_audio_contracts(self):
        cases = (
            _wav(format_tag=6),
            _wav(channels=3),
            _wav(sample_rate=7_999),
            _wav(sample_rate=192_001),
            _wav(bits=12),
            _wav(format_tag=3, bits=16),
            _wav(bits=16, extensible_subformat=6),
        )
        for content in cases:
            with self.subTest(size=len(content)):
                with self.assertRaises(validation.WavUploadValidationError) as raised:
                    validation.validate_wav_upload(
                        content,
                        max_bytes=len(content),
                    )
                self.assertEqual(
                    raised.exception.public_message,
                    (
                        "WAV audio must use PCM or IEEE float encoding with "
                        "1-2 channels at 8-192 kHz."
                    ),
                )

    def test_rejects_wrong_magic_corruption_truncation_and_empty_audio(self):
        valid = _wav()
        malformed_byte_rate = bytearray(valid)
        struct.pack_into("<I", malformed_byte_rate, 28, 1)
        duplicate_fmt_payload = valid[12:36] + valid[12:]
        duplicate_fmt = (
            b"RIFF"
            + struct.pack("<I", len(duplicate_fmt_payload) + 4)
            + b"WAVE"
            + duplicate_fmt_payload
        )
        fmt_chunk = valid[12:36]
        data_chunk = valid[36:]
        reordered_payload = data_chunk + fmt_chunk
        data_before_format = (
            b"RIFF"
            + struct.pack("<I", len(reordered_payload) + 4)
            + b"WAVE"
            + reordered_payload
        )
        cases = (
            b"not a wav",
            b"NOPE" + valid[4:],
            valid[:8] + b"NOPE" + valid[12:],
            valid[:-1],
            valid + b"trailing payload",
            bytes(malformed_byte_rate),
            duplicate_fmt,
            data_before_format,
            _wav(extra_chunks=(_chunk(b"data", b"\0\0"),)),
            _wav(frames=0),
        )
        for content in cases:
            with self.subTest(size=len(content)):
                with self.assertRaises(validation.WavUploadValidationError):
                    validation.validate_wav_upload(
                        content,
                        max_bytes=max(1, len(content)),
                    )

    def test_rejects_data_not_aligned_to_frames_and_declared_size_overrun(self):
        valid = _wav(channels=2, bits=16)
        misaligned = bytearray(valid)
        data_size_offset = valid.index(b"data") + 4
        data_size = struct.unpack_from("<I", valid, data_size_offset)[0]
        struct.pack_into("<I", misaligned, data_size_offset, data_size - 1)
        struct.pack_into("<I", misaligned, 4, len(misaligned) - 9)
        with self.assertRaises(validation.WavUploadValidationError):
            validation.validate_wav_upload(
                bytes(misaligned),
                max_bytes=len(misaligned),
            )

        declared_overrun = bytearray(valid)
        struct.pack_into("<I", declared_overrun, 4, len(valid) + 100)
        with self.assertRaises(validation.WavUploadValidationError):
            validation.validate_wav_upload(
                bytes(declared_overrun),
                max_bytes=len(declared_overrun),
            )

    def test_enforces_byte_and_duration_bounds(self):
        content = _wav(frames=32_000)
        with self.assertRaises(validation.WavUploadValidationError):
            validation.validate_wav_upload(
                content,
                max_bytes=len(content) - 1,
            )
        with mock.patch.object(validation, "MAX_WAV_DURATION_SECONDS", 1):
            with self.assertRaises(validation.WavUploadValidationError) as raised:
                validation.validate_wav_upload(
                    content,
                    max_bytes=len(content),
                )
        self.assertEqual(
            raised.exception.public_message,
            "WAV audio is too long (maximum 6 hours).",
        )


class RegularUploadStorageTests(unittest.TestCase):
    def test_creates_private_regular_file_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = os.path.join(directory, "audio")
            path = validation.write_regular_upload(root, "voice.wav", b"one")
            self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))
            self.assertEqual(Path(path).read_bytes(), b"one")
            with self.assertRaises(validation.AudioUploadStorageError):
                validation.write_regular_upload(root, "voice.wav", b"two")
            self.assertEqual(Path(path).read_bytes(), b"one")

    def test_rejects_traversal_and_symlink_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = os.path.join(directory, "audio")
            with self.assertRaises(validation.AudioUploadStorageError):
                validation.write_regular_upload(root, "../voice.wav", b"audio")
            self.assertFalse(Path(directory, "voice.wav").exists())

            if hasattr(os, "symlink"):
                target = os.path.join(directory, "target")
                os.makedirs(target)
                alias = os.path.join(directory, "alias")
                try:
                    os.symlink(target, alias, target_is_directory=True)
                except OSError:
                    return
                with self.assertRaises(validation.AudioUploadStorageError):
                    validation.write_regular_upload(alias, "voice.wav", b"audio")
                self.assertFalse(Path(target, "voice.wav").exists())


class _HTTPException(Exception):
    def __init__(self, status_code: int, detail: str = "") -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Upload:
    def __init__(self, filename: str, content: bytes, content_type: str = "") -> None:
        self.filename = filename
        self.content = content
        self.content_type = content_type
        self.read_limits = []

    async def read(self, limit: int) -> bytes:
        self.read_limits.append(limit)
        return self.content[:limit]


class _UuidSequence:
    def __init__(self, *tokens: str) -> None:
        self._tokens = iter(tokens)

    def uuid4(self):
        return types.SimpleNamespace(hex=next(self._tokens))


class _FakeFfmpegError(Exception):
    pass


class _FakeFfmpegPipeline:
    def __init__(self, source: str) -> None:
        self.source = source
        self.target = None

    def output(self, target: str, **_kwargs):
        self.target = target
        return self

    def overwrite_output(self):
        return self

    def run(self, quiet: bool = False):
        del quiet
        Path(self.target).write_bytes(b"converted wav")


class _FakeFfmpegModule:
    Error = _FakeFfmpegError

    @staticmethod
    def input(source: str):
        return _FakeFfmpegPipeline(source)


def _load_upload_route(sidecar_calls: list[tuple]):
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item for item in ast.walk(tree)
        if isinstance(item, ast.AsyncFunctionDef) and item.name == "upload_audio"
    )
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Request": object,
        "UploadFile": object,
        "File": lambda *args, **kwargs: None,
        "HTTPException": _HTTPException,
        "MAX_AUDIO_UPLOAD_BYTES": 500 * 1024 * 1024,
        "os": os,
        "uuid": __import__("uuid"),
        "_probe_audio_duration": lambda _path: None,
        "write_upload_access_sidecar": (
            lambda *args, **kwargs: sidecar_calls.append((args, kwargs)) or {}
        ),
        "public_output_policy": lambda _access: {"private": True},
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    return namespace["upload_audio"]


class AudioUploadRouteTests(unittest.TestCase):
    @staticmethod
    def _request():
        return types.SimpleNamespace(
            headers={},
            state=types.SimpleNamespace(
                maestro_remote=False,
                maestro_session_id="a" * 32,
            ),
        )

    def test_mime_empty_valid_wav_is_admitted_and_sidecar_stamps_final_file(self):
        sidecar_calls = []
        route = _load_upload_route(sidecar_calls)
        upload = _Upload("voice.wav", _wav(), content_type="")
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                result = asyncio.run(route(self._request(), upload))
                stored_bytes = Path(result["path"]).read_bytes()
            finally:
                os.chdir(previous)
        self.assertEqual(upload.read_limits, [500 * 1024 * 1024 + 1])
        self.assertEqual(stored_bytes, upload.content)
        self.assertEqual(result["duration_seconds"], 0.1)
        self.assertEqual(len(sidecar_calls), 1)
        stamped_path = sidecar_calls[0][0][0]
        self.assertEqual(stamped_path, result["path"])
        self.assertTrue(os.path.basename(stamped_path).endswith(".wav"))

    def test_invalid_wav_fails_before_any_filesystem_or_sidecar_mutation(self):
        sidecar_calls = []
        route = _load_upload_route(sidecar_calls)
        upload = _Upload("private-secret.wav", b"private sample bytes")
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with self.assertRaises(_HTTPException) as raised:
                    asyncio.run(route(self._request(), upload))
                self.assertFalse(Path("uploads").exists())
            finally:
                os.chdir(previous)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail,
            "Audio upload is not a valid WAV file.",
        )
        self.assertNotIn("private", raised.exception.detail)
        self.assertEqual(sidecar_calls, [])

    def test_sidecar_failure_removes_validated_private_upload(self):
        route = _load_upload_route([])

        def fail_sidecar(*_args, **_kwargs):
            raise OSError("synthetic sidecar failure")

        route.__globals__["write_upload_access_sidecar"] = fail_sidecar
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with self.assertRaises(_HTTPException) as raised:
                    asyncio.run(route(self._request(), _Upload("voice.wav", _wav())))
                audio_root = Path("uploads", "audio")
                self.assertEqual(list(audio_root.iterdir()), [])
            finally:
                os.chdir(previous)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(
            raised.exception.detail,
            "Audio upload could not be published.",
        )

    def test_policy_failure_removes_published_sidecar_and_private_upload(self):
        route = _load_upload_route([])
        route.__globals__["write_upload_access_sidecar"] = (
            write_upload_access_sidecar
        )

        def fail_policy(_access):
            raise RuntimeError("synthetic policy failure")

        route.__globals__["public_output_policy"] = fail_policy
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with self.assertRaises(_HTTPException):
                    asyncio.run(route(self._request(), _Upload("voice.wav", _wav())))
                self.assertEqual(list(Path("uploads", "audio").iterdir()), [])
            finally:
                os.chdir(previous)

    def test_transcode_retirement_failure_cleans_source_and_reserved_output(self):
        sidecar_calls = []
        route = _load_upload_route(sidecar_calls)
        route.__globals__["uuid"] = _UuidSequence("cafebabe", "deadbeef")
        import services.win_safe_files as win_safe_files

        blocked = set()

        def fail_first_source_delete(path, *, retries=3, retry_delay=0.2):
            del retries, retry_delay
            if path.endswith(".mp3") and path not in blocked:
                blocked.add(path)
                return {"deleted": False, "reason": "locked"}
            try:
                os.remove(path)
                return {"deleted": True}
            except FileNotFoundError:
                return {"deleted": False, "reason": "not_found"}

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            sys.modules,
            {"ffmpeg": _FakeFfmpegModule()},
        ), mock.patch.object(
            win_safe_files,
            "safe_delete",
            side_effect=fail_first_source_delete,
        ):
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with self.assertRaises(_HTTPException) as raised:
                    asyncio.run(
                        route(self._request(), _Upload("track.mp3", b"encoded"))
                    )
                self.assertEqual(list(Path("uploads", "audio").iterdir()), [])
            finally:
                os.chdir(previous)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(sidecar_calls, [])

    def test_persistent_lock_is_private_durably_accounted_and_retried(self):
        route = _load_upload_route([])
        route.__globals__["uuid"] = _UuidSequence("cafebabe", "deadbeef")
        route.__globals__["write_upload_access_sidecar"] = (
            write_upload_access_sidecar
        )
        import services.win_safe_files as win_safe_files

        def persistently_locked(path, *, retries=3, retry_delay=0.2):
            del retries, retry_delay
            if os.path.isfile(path):
                return {"deleted": False, "reason": "locked"}
            return {"deleted": False, "reason": "not_found"}

        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with mock.patch.dict(
                    sys.modules,
                    {"ffmpeg": _FakeFfmpegModule()},
                ), mock.patch.object(
                    win_safe_files,
                    "safe_delete",
                    side_effect=persistently_locked,
                ):
                    with self.assertRaises(_HTTPException) as raised:
                        asyncio.run(
                            route(
                                self._request(),
                                _Upload("private-track.mp3", b"encoded"),
                            )
                        )

                audio_root = Path("uploads", "audio")
                markers = list(
                    audio_root.glob(".maestro-audio-cleanup-*.json")
                )
                self.assertEqual(len(markers), 1)
                marker = json.loads(markers[0].read_text(encoding="ascii"))
                self.assertEqual(marker["version"], 1)
                self.assertEqual(
                    marker["targets"],
                    [
                        "cafebabe.mp3",
                        "cafebabe.mp3.access.json",
                        "deadbeef.wav",
                        "deadbeef.wav.access.json",
                    ],
                )
                self.assertNotIn("private-track", markers[0].read_text())
                self.assertTrue(
                    all("/" not in name and "\\" not in name for name in marker["targets"])
                )
                self.assertEqual(
                    validation.retry_pending_audio_cleanup(
                        str(audio_root.resolve()),
                        win_safe_files.safe_delete,
                    ),
                    1,
                )
                self.assertEqual(list(audio_root.iterdir()), [])
            finally:
                os.chdir(previous)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertNotIn("cafebabe", raised.exception.detail)
        self.assertNotIn("deadbeef", raised.exception.detail)

    def test_locked_postpublication_sidecar_remains_private(self):
        route = _load_upload_route([])
        route.__globals__["uuid"] = _UuidSequence("cafebabe")
        route.__globals__["write_upload_access_sidecar"] = (
            write_upload_access_sidecar
        )

        def fail_policy(_access):
            raise RuntimeError("synthetic policy failure")

        route.__globals__["public_output_policy"] = fail_policy
        import services.win_safe_files as win_safe_files

        def persistently_locked(path, *, retries=3, retry_delay=0.2):
            del retries, retry_delay
            if os.path.isfile(path):
                return {"deleted": False, "reason": "locked"}
            return {"deleted": False, "reason": "not_found"}

        real_replace = os.replace
        sidecar_replaces = 0

        def lock_sidecar_replacement(source, target):
            nonlocal sidecar_replaces
            if str(target).endswith(".access.json"):
                sidecar_replaces += 1
                if sidecar_replaces > 1:
                    raise PermissionError("synthetic locked sidecar")
            return real_replace(source, target)

        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with mock.patch.object(
                    win_safe_files,
                    "safe_delete",
                    side_effect=persistently_locked,
                ), mock.patch.object(
                    os,
                    "replace",
                    side_effect=lock_sidecar_replacement,
                ):
                    with self.assertRaises(_HTTPException) as raised:
                        asyncio.run(
                            route(
                                self._request(),
                                _Upload("voice.wav", _wav()),
                                private=False,
                            )
                        )
                audio_root = Path("uploads", "audio")
                sidecar = json.loads(
                    (audio_root / "cafebabe.wav.access.json").read_text()
                )
                self.assertIs(sidecar["private"], True)
                self.assertEqual(
                    len(list(audio_root.glob(".maestro-audio-cleanup-*.json"))),
                    1,
                )
                self.assertNotIn("cafebabe", raised.exception.detail)
                self.assertEqual(
                    validation.retry_pending_audio_cleanup(
                        str(audio_root.resolve()),
                        win_safe_files.safe_delete,
                    ),
                    1,
                )
                self.assertEqual(list(audio_root.iterdir()), [])
            finally:
                os.chdir(previous)

    def test_reserved_transcode_target_collision_never_overwrites_peer(self):
        sidecar_calls = []
        route = _load_upload_route(sidecar_calls)
        route.__globals__["uuid"] = _UuidSequence("cafebabe", "deadbeef")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            sys.modules,
            {"ffmpeg": _FakeFfmpegModule()},
        ):
            previous = os.getcwd()
            os.chdir(directory)
            try:
                audio_root = Path("uploads", "audio")
                audio_root.mkdir(parents=True)
                peer = audio_root / "deadbeef.wav"
                peer.write_bytes(b"peer audio")
                with self.assertRaises(_HTTPException) as raised:
                    asyncio.run(
                        route(self._request(), _Upload("track.mp3", b"encoded"))
                    )
                self.assertEqual(peer.read_bytes(), b"peer audio")
                self.assertEqual(
                    sorted(path.name for path in audio_root.iterdir()),
                    ["deadbeef.wav"],
                )
            finally:
                os.chdir(previous)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(sidecar_calls, [])

    def test_route_source_orders_wav_validation_before_all_mutation(self):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in ast.walk(tree)
            if isinstance(item, ast.AsyncFunctionDef) and item.name == "upload_audio"
        )
        route_source = ast.get_source_segment(source, node) or ""
        validation_index = route_source.index("validate_wav_upload(")
        for mutation in (
            "retry_pending_audio_cleanup(",
            'os.path.join(os.getcwd(), "uploads", "audio")',
            "uuid.uuid4()",
            "write_regular_upload(",
            "access = write_upload_access_sidecar(",
        ):
            with self.subTest(mutation=mutation):
                self.assertLess(validation_index, route_source.index(mutation))
        self.assertNotIn("file.content_type", route_source)
        self.assertNotIn("file.filename!r", route_source)
        self.assertNotIn("stderr", route_source)
        self.assertIn(
            "Uploaded media could not be decoded as audio.",
            route_source,
        )


if __name__ == "__main__":
    unittest.main()
