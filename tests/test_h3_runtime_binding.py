"""CPU temporary-file binding and real loader orchestration; no model weights."""

from __future__ import annotations

import ast
import gc
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from models.minimax_h3 import conditioner
from models.minimax_h3 import minimax_h3_main as main
from services import h3_runtime_binding as binding
from services.h3_cumulative_recovery import H3CumulativeIdentity, H3CumulativeRecovery
from test_minimax_h3_cumulative import fake_model


class Component:
    def named_parameters(self):
        return ()

    def named_buffers(self):
        return ()

    def named_modules(self):
        return (("", self),)


class RuntimeBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {}
        for role in (
            "transformer",
            "video_vae",
            "audio_vae",
            "text_config",
            "conditioner_0",
        ):
            path = self.root / role
            path.write_bytes((role + " fixture").encode())
            self.files[role] = path
        self.processor = self.root / "processor"
        self.processor.mkdir()
        for name in binding.PROCESSOR_FILES:
            (self.processor / name).write_bytes((name + " fixture").encode())
        self.contract = {"dtype": "torch.bfloat16", "implementation": "fixture"}
        self.loaded = {"video_shift": 12.0, "audio_shift": 3.0, "qkv": "contiguous"}
        self.components = tuple(Component() for _ in range(6))

    def snapshot(self, **kwargs):
        return binding.snapshot_h3_runtime_files(
            self.files, self.contract, processor_dir=self.processor, **kwargs
        )

    def test_digest_relocates_but_changes_with_content_or_contract(self):
        snapshot = self.snapshot()
        proof = snapshot.bind(self.components, self.loaded)
        self.assertEqual(
            proof.verified_digest(self.components, self.loaded), proof.sha256
        )
        self.assertRegex(proof.sha256, r"^[0-9a-f]{64}$")
        other = self.root / "relocated"
        other.mkdir()
        other_files = {}
        for role, path in self.files.items():
            destination = other / role
            shutil.copyfile(path, destination)
            other_files[role] = destination
        shutil.copytree(self.processor, other / "processor")
        moved = binding.snapshot_h3_runtime_files(
            other_files, self.contract, processor_dir=other / "processor"
        )
        self.assertEqual(moved.bind(self.components, self.loaded).sha256, proof.sha256)
        other_files["conditioner_0"].write_bytes(b"changed")
        changed = binding.snapshot_h3_runtime_files(
            other_files, self.contract, processor_dir=other / "processor"
        )
        self.assertNotEqual(
            changed.bind(self.components, self.loaded).sha256, proof.sha256
        )
        self.assertNotEqual(
            snapshot.bind(
                self.components, {**self.loaded, "qkv": "interleaved"}
            ).sha256,
            proof.sha256,
        )
        self.assertNotIn(str(self.root).encode(), snapshot.contract_json)

    def test_same_size_change_with_reset_mtime_and_file_replacement_rejected(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                snapshot = self.snapshot()
                path = self.files["transformer"]
                info = path.stat()
                if replacement:
                    other = self.root / "replacement"
                    other.write_bytes(path.read_bytes())
                    os.replace(other, path)
                else:
                    path.write_bytes(b"x" * info.st_size)
                    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                with self.assertRaisesRegex(binding.H3RuntimeBindingError, "changed"):
                    snapshot.verify()

    def test_linked_root_resolution_must_remain_exact(self):
        actual = self.files["transformer"]
        link = self.root / "linked-transformer"
        link.symlink_to(actual)
        self.files["transformer"] = link
        snapshot = self.snapshot()
        other = self.root / "other-transformer"
        other.write_bytes(actual.read_bytes())
        link.unlink()
        link.symlink_to(other)
        with self.assertRaisesRegex(
            binding.H3RuntimeBindingError, "resolution changed"
        ):
            snapshot.verify()

    def test_optional_processor_files_and_census_are_bound(self):
        original = self.snapshot().bind(self.components, self.loaded).sha256
        optional = self.processor / "added_tokens.json"
        optional.write_bytes(b'{"extra":1}')
        snapshot = self.snapshot()
        self.assertNotEqual(
            snapshot.bind(self.components, self.loaded).sha256, original
        )
        optional.unlink()
        with self.assertRaisesRegex(binding.H3RuntimeBindingError, "inventory changed"):
            snapshot.verify()
        snapshot = self.snapshot()
        optional.write_bytes(b"extra")
        with self.assertRaisesRegex(binding.H3RuntimeBindingError, "inventory changed"):
            snapshot.verify()

    def test_bad_inventory_nonregular_empty_and_cancel_fail_without_blocking(self):
        del self.files["conditioner_0"]
        with self.assertRaises(binding.H3RuntimeBindingError):
            self.snapshot()
        self.files["conditioner_0"] = self.root / "conditioner_0"
        self.files["transformer"].write_bytes(b"")
        with self.assertRaisesRegex(
            binding.H3RuntimeBindingError, "unsafe type or size"
        ):
            self.snapshot()
        self.files["transformer"].unlink()
        os.mkfifo(self.files["transformer"])
        with self.assertRaisesRegex(
            binding.H3RuntimeBindingError, "unsafe type or size"
        ):
            self.snapshot()
        self.files["transformer"].unlink()
        self.files["transformer"].write_bytes(b"good")
        with self.assertRaises(InterruptedError):
            self.snapshot(abort_check=lambda: True)
        (self.processor / "chat_templates").mkdir()
        with self.assertRaisesRegex(
            binding.H3RuntimeBindingError, "unsupported asset type"
        ):
            self.snapshot()

    def test_changed_loaded_contract_component_or_dead_reference_rejected(self):
        proof = self.snapshot().bind(self.components, self.loaded)
        with self.assertRaisesRegex(binding.H3RuntimeBindingError, "contract changed"):
            proof.verified_digest(self.components, {**self.loaded, "audio_shift": 4.0})
        with self.assertRaisesRegex(
            binding.H3RuntimeBindingError, "components changed"
        ):
            proof.verified_digest((Component(), *self.components[1:]), self.loaded)
        reference = proof.components[0]
        self.components = self.components[1:]
        gc.collect()
        self.assertIsNone(reference())
        for value in (proof, proof.snapshot):
            with self.assertRaises(TypeError):
                pickle.dumps(value)

    def test_loaded_code_defaults_constants_and_wrapped_code_are_bound(self):
        module = types.ModuleType("fixture_implementation")
        exec(  # noqa: S102 - fixed fixture source exercises loaded bytecode.
            "VALUE = 12\ndef compute(value=3):\n return value + VALUE\nclass Config:\n shift = 12\n",
            module.__dict__,
        )
        original = binding.implementation_sha256([module])
        self.assertEqual(binding.implementation_sha256([module]), original)
        module.compute.__defaults__ = (4,)
        self.assertNotEqual(binding.implementation_sha256([module]), original)
        module.compute.__defaults__ = (3,)
        module.Config.shift = 13
        self.assertNotEqual(binding.implementation_sha256([module]), original)
        module.Config.shift = 12
        exec("def compute(value=3):\n return value - VALUE\n", module.__dict__)  # noqa: S102
        self.assertNotEqual(binding.implementation_sha256([module]), original)
        module.compute.__wrapped__ = module.compute
        # A direct wrapper cycle is ignored; an indirect cycle fails closed.
        other = types.FunctionType(module.compute.__code__, module.__dict__)
        module.compute.__wrapped__ = other
        other.__wrapped__ = module.compute
        with self.assertRaisesRegex(binding.H3RuntimeBindingError, "cycle"):
            binding.implementation_sha256([module])

    def test_real_quant_router_lazy_caches_do_not_change_code_identity(self):
        from mmgp import quant_router
        from optimum.quanto import qint8

        with (
            patch.object(quant_router, "_QTYPE_QMODULE_CACHE", None),
            patch.object(quant_router, "_QMODULE_BASE_ATTRS", None),
        ):
            original = binding.implementation_sha256([quant_router])
            # Real MMGP discovery, no checkpoint/model-weight load or CUDA.
            quant_router._get_qmodule_base_attrs()
            quant_router._get_qmodule_for_qtype(qint8)
            self.assertIsInstance(quant_router._QTYPE_QMODULE_CACHE, dict)
            self.assertEqual(binding.implementation_sha256([quant_router]), original)
            with patch.dict(quant_router._DEFAULT_KIND_PRIORITIES, {"int8": 99}):
                self.assertNotEqual(
                    binding.implementation_sha256([quant_router]), original
                )

        # The same spelling in an unrelated module remains a bound constant.
        module = types.ModuleType("fixture_cache_names")
        module._QTYPE_QMODULE_CACHE = None
        original = binding.implementation_sha256([module])
        module._QTYPE_QMODULE_CACHE = "changed"
        self.assertNotEqual(binding.implementation_sha256([module]), original)

    def test_loaded_set_constants_bind_across_fresh_hash_seed_processes(self):
        code = """
import types
from services.h3_runtime_binding import implementation_sha256
module = types.ModuleType('fixture_set_constants')
exec("def compute(value):\\n return value in {'alpha', 'beta', 'gamma', 'delta'}\\n", module.__dict__)
print(implementation_sha256([module]))
"""
        digests = []
        for seed in ("1", "2"):
            env = os.environ.copy()
            env.update(
                PYTHONPATH=str(Path(__file__).resolve().parents[1] / "app"),
                PYTHONHASHSEED=seed,
                CUDA_VISIBLE_DEVICES="",
            )
            result = subprocess.run(
                [sys.executable, "-c", code], env=env, capture_output=True,
                text=True, timeout=30, check=True,
            )
            digests.append(result.stdout.strip())
        self.assertRegex(digests[0], r"^[0-9a-f]{64}$")
        self.assertEqual(digests[0], digests[1])

        module = types.ModuleType("fixture_constant_types")
        exec("def compute():\n return ('alpha', 'beta')\n", module.__dict__)
        original = binding.implementation_sha256([module])
        module.compute.__code__ = module.compute.__code__.replace(
            co_consts=(None, frozenset(("alpha", "beta")))
        )
        self.assertNotEqual(binding.implementation_sha256([module]), original)
        changed = binding.implementation_sha256([module])
        module.compute.__code__ = module.compute.__code__.replace(
            co_consts=(None, frozenset(("alpha", "gamma")))
        )
        self.assertNotEqual(binding.implementation_sha256([module]), changed)

    def fake_loaded(self):
        model = fake_model()
        model._h3_runtime_profile = {"fixture": True}
        model.conditioner.qwen = SimpleNamespace(
            config=SimpleNamespace(to_dict=lambda: {"fixture": True})
        )
        model.conditioner.max_text_tokens = 512
        for component in model._h3_runtime_components()[:4]:
            component._model_dtype = torch.float32
            component.named_parameters = lambda: ()
            component.named_buffers = lambda: ()
            component.named_modules = lambda component=component: (("", component),)
        model.transformer.h3_checkpoint_info = {
            "architecture": "fixture",
            "curve_dim": 8,
        }
        model.transformer.h3_qkv_layout = "contiguous"
        model.vae.config = {
            "latents_mean": main.VIDEO_LATENTS_MEAN,
            "latents_std": main.VIDEO_LATENTS_STD,
        }
        model.audio_vae.config = {
            "latents_mean": main.AUDIO_LATENTS_MEAN,
            "latents_std": main.AUDIO_LATENTS_STD,
        }
        return model

    def test_diffusers_default_fields_bind_across_fresh_hash_seed_processes(self):
        code = """
import json
from diffusers.configuration_utils import ConfigMixin, register_to_config
from services.h3_runtime_binding import diffusers_config_contract
class Config(ConfigMixin):
    config_name = "config.json"
    @register_to_config
    def __init__(self, channels=24, rate=32000, layers=(1, 2), alpha=1, beta=2, gamma=3):
        pass
print(json.dumps(diffusers_config_contract(Config().config), sort_keys=True))
"""
        contracts = []
        for seed in ("1", "2", "3"):
            environment = dict(
                os.environ,
                CUDA_VISIBLE_DEVICES="",
                PYTHONHASHSEED=seed,
                PYTHONPATH=str(Path(__file__).resolve().parents[1] / "app"),
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            contracts.append(json.loads(result.stdout))
        self.assertEqual(contracts[0], contracts[1])
        self.assertEqual(contracts[0], contracts[2])
        self.assertEqual(contracts[0]["layers"], [1, 2])

    def test_default_metadata_order_is_stable_but_values_and_sequences_are_bound(self):
        model = self.fake_loaded()
        model.vae.config.update(
            _use_default_values=["channels", "layers"], channels=24, layers=[1, 2]
        )
        model.audio_vae.config.update(
            _use_default_values=["rate", "latents"], rate=32000, latents=32
        )
        snapshot = binding.snapshot_h3_runtime_files(
            self.files, model._h3_runtime_code_contract(), processor_dir=self.processor
        )
        model._h3_runtime_binding = snapshot.bind(
            model._h3_runtime_components(), model._h3_loaded_runtime_contract()
        )
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}):
            digest = model.verified_h3_runtime_sha256()
            model.vae.config["_use_default_values"].reverse()
            model.audio_vae.config["_use_default_values"].reverse()
            self.assertEqual(digest, model.verified_h3_runtime_sha256())
            # Contract preparation does not reorder the producer's config.
            self.assertEqual(
                model.vae.config["_use_default_values"], ["layers", "channels"]
            )
            for key, changed in (
                ("channels", 25),
                ("layers", [2, 1]),
                ("_use_default_values", ["channels"]),
            ):
                with self.subTest(key=key):
                    original = model.vae.config[key]
                    model.vae.config[key] = changed
                    with self.assertRaisesRegex(
                        binding.H3RuntimeBindingError, "contract changed"
                    ):
                        model.verified_h3_runtime_sha256()
                    model.vae.config[key] = original
        for malformed in ("channels", [1], [""], ["channels", "channels"]):
            with (
                self.subTest(malformed=malformed),
                self.assertRaisesRegex(
                    binding.H3RuntimeBindingError, "metadata is invalid"
                ),
            ):
                binding.diffusers_config_contract({"_use_default_values": malformed})

    def test_real_getter_rejects_replacement_release_ordinary_and_scheduler_change(
        self,
    ):
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}):
            model = self.fake_loaded()
            snapshot = binding.snapshot_h3_runtime_files(
                self.files,
                model._h3_runtime_code_contract(),
                processor_dir=self.processor,
            )
            model._h3_runtime_binding = snapshot.bind(
                model._h3_runtime_components(), model._h3_loaded_runtime_contract()
            )
            digest = model.verified_h3_runtime_sha256()
            self.assertEqual(digest, model.verified_h3_runtime_sha256())
            model.audio_scheduler.set_shift(4)
            with self.assertRaisesRegex(
                binding.H3RuntimeBindingError, "contract changed"
            ):
                model.verified_h3_runtime_sha256()
            model.audio_scheduler.set_shift(3)
            model.generate(
                "ordinary scene",
                frame_num=141,
                height=64,
                width=64,
                sampling_steps=2,
                seed=1,
                custom_settings={"h3_attention_engine": "sdpa"},
            )
            with self.assertRaisesRegex(binding.H3RuntimeBindingError, "unavailable"):
                model.verified_h3_runtime_sha256()
            model._h3_runtime_binding = snapshot.bind(
                model._h3_runtime_components(), model._h3_loaded_runtime_contract()
            )
            model.release()
            with self.assertRaisesRegex(binding.H3RuntimeBindingError, "unavailable"):
                model.verified_h3_runtime_sha256()

    def model_def(self):
        return {
            "required_runtime_assets": {
                "video_vae": "video-relative",
                "audio_vae": "audio-relative",
                "text_encoder_config": "text-relative",
                "processor": [
                    "processor/" + name for name in sorted(binding.PROCESSOR_FILES)
                ],
            }
        }

    def test_constructor_private_hashes_then_loads_exact_resolved_paths_only(self):
        components = self.fake_loaded()._h3_runtime_components()
        captured = []
        real_snapshot = binding.snapshot_h3_runtime_files

        def snapshot(*args, **kwargs):
            value = real_snapshot(*args, **kwargs)
            captured.append(value)
            return value

        lookup = {
            "video-relative": self.files["video_vae"],
            "audio-relative": self.files["audio_vae"],
            "text-relative": self.files["text_config"],
        }
        with (
            patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}),
            patch.object(
                main.fl, "locate_file", side_effect=lambda path: str(lookup[path])
            ),
            patch.object(main.fl, "locate_folder", return_value=str(self.processor)),
            patch.object(
                main, "_load_transformer", return_value=components[0]
            ) as transformer_load,
            patch.object(
                main, "_load_conditioner", return_value=components[1]
            ) as conditioner_load,
            patch.object(
                main, "_load_video_vae", return_value=components[2]
            ) as video_load,
            patch.object(
                main, "_load_audio_vae", return_value=components[3]
            ) as audio_load,
            patch.object(binding, "snapshot_h3_runtime_files", side_effect=snapshot),
        ):
            model = main.MiniMaxH3Model(
                str(self.files["transformer"]),
                self.model_def(),
                [str(self.files["conditioner_0"])],
                dtype=torch.float32,
                selected_model_type="minimax_h3",
            )
            self.assertEqual(len(captured), 1)
            with self.assertRaisesRegex(binding.H3RuntimeBindingError, "unavailable"):
                model.verified_h3_runtime_sha256()
            model.finalize_h3_runtime_binding(
                compile=False,
                quantize_transformer=False,
                convert_weights_float_to=torch.float32,
            )
            self.assertRegex(model.verified_h3_runtime_sha256(), r"^[0-9a-f]{64}$")
            self.assertEqual(
                transformer_load.call_args.args[0],
                str(self.files["transformer"].resolve()),
            )
            self.assertEqual(
                conditioner_load.call_args.args[0],
                [str(self.files["conditioner_0"].resolve())],
            )
            self.assertEqual(
                conditioner_load.call_args.kwargs["resolved_assets"],
                (
                    str(self.files["text_config"].resolve()),
                    str(self.processor.resolve()),
                ),
            )
            self.assertEqual(
                video_load.call_args.args[0], str(self.files["video_vae"].resolve())
            )
            self.assertEqual(
                audio_load.call_args.args[0], str(self.files["audio_vae"].resolve())
            )

    def test_constructor_normal_gate_does_not_hash_or_resolve_extra_assets(self):
        for gate, reference in (("0", False), ("1", True)):
            with (
                self.subTest(gate=gate, reference=reference),
                patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": gate}),
                patch.object(
                    main.fl, "locate_file", return_value="ordinary-path"
                ) as locate,
                patch.object(main, "_load_transformer", return_value=Component()),
                patch.object(
                    main, "_load_conditioner", return_value=Component()
                ) as load,
                patch.object(main, "_load_video_vae", return_value=Component()),
                patch.object(main, "_load_audio_vae", return_value=Component()),
                patch.object(binding, "snapshot_h3_runtime_files") as snapshot,
            ):
                definition = {
                    **self.model_def(),
                    "minimax_h3_reference_mode": reference,
                }
                model = main.MiniMaxH3Model(
                    "transformer-path",
                    definition,
                    "conditioner-path",
                    selected_model_type="minimax_h3",
                )
                snapshot.assert_not_called()
                self.assertEqual(locate.call_count, 2)
                self.assertNotIn("resolved_assets", load.call_args.kwargs)
                self.assertIsNone(model._h3_runtime_binding)

    def test_private_hash_observes_wgp_cancellation_before_any_component_load(self):
        class Reporter:
            def __init__(self):
                self.checks = 0

            def transition(self, _label):
                pass

            def check_cancelled(self):
                self.checks += 1
                raise InterruptedError("fake WGP load cancellation")

        reporter = Reporter()
        lookup = {
            "video-relative": self.files["video_vae"],
            "audio-relative": self.files["audio_vae"],
            "text-relative": self.files["text_config"],
        }
        with (
            patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}),
            patch.object(
                main.fl, "locate_file", side_effect=lambda path: str(lookup[path])
            ),
            patch.object(main.fl, "locate_folder", return_value=str(self.processor)),
            patch.object(main.offload, "flush_torch_caches"),
            patch.object(main, "_load_transformer") as load,
            self.assertRaisesRegex(InterruptedError, "load cancellation"),
        ):
            main.MiniMaxH3Model(
                str(self.files["transformer"]),
                self.model_def(),
                str(self.files["conditioner_0"]),
                load_status_callback=reporter.transition,
            )
        load.assert_not_called()
        self.assertEqual(reporter.checks, 1)

    def test_load_failure_or_changed_asset_discards_partial_binding_and_components(
        self,
    ):
        release = main.MiniMaxH3Model.release
        for fail in (False, True):
            with self.subTest(fail=fail):
                components = self.fake_loaded()._h3_runtime_components()
                observed = []

                def transformer_load(*args, components=components, **kwargs):
                    return components[0]

                def audio_load(*args, components=components, fail=fail):
                    self.files["transformer"].write_bytes(b"changed during fake load")
                    if fail:
                        raise RuntimeError("fake load failure")
                    return components[3]

                def released(model, observed=observed):
                    release(model)
                    observed.append(model)

                lookup = {
                    "video-relative": self.files["video_vae"],
                    "audio-relative": self.files["audio_vae"],
                    "text-relative": self.files["text_config"],
                }
                with (
                    patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}),
                    patch.object(
                        main.fl,
                        "locate_file",
                        side_effect=lambda path, lookup=lookup: str(lookup[path]),
                    ),
                    patch.object(
                        main.fl, "locate_folder", return_value=str(self.processor)
                    ),
                    patch.object(
                        main, "_load_transformer", side_effect=transformer_load
                    ),
                    patch.object(main, "_load_conditioner", return_value=components[1]),
                    patch.object(main, "_load_video_vae", return_value=components[2]),
                    patch.object(main, "_load_audio_vae", side_effect=audio_load),
                    patch.object(main.offload, "flush_torch_caches"),
                    patch.object(
                        main.MiniMaxH3Model,
                        "release",
                        side_effect=released,
                        autospec=True,
                    ),
                    self.assertRaises((ValueError, RuntimeError)),
                ):
                    main.MiniMaxH3Model(
                        str(self.files["transformer"]),
                        self.model_def(),
                        str(self.files["conditioner_0"]),
                        dtype=torch.float32,
                    )
                self.assertEqual(len(observed), 1)
                self.assertIsNone(observed[0]._h3_runtime_binding)
                self.assertEqual(observed[0]._h3_runtime_components(), (None,) * 6)

    def test_processor_offline_flag_forbids_remote_fallback_and_preserves_normal_call(
        self,
    ):
        for private in (False, True):
            with (
                patch.object(
                    conditioner.AutoTokenizer,
                    "from_pretrained",
                    return_value=Component(),
                ) as tokenizer,
                patch.object(conditioner, "_ensure_h3_marker_tokens"),
                patch.object(
                    conditioner.Qwen2VLImageProcessorFast,
                    "from_pretrained",
                    return_value=Component(),
                ) as image,
                patch.object(conditioner, "Krea2Qwen3VLProcessor"),
            ):
                conditioner.build_h3_processor(
                    "fixture-folder", local_files_only=private
                )
                self.assertEqual(
                    tokenizer.call_args.kwargs,
                    {
                        "trust_remote_code": False,
                        **({"local_files_only": True} if private else {}),
                    },
                )
                self.assertEqual(
                    image.call_args.kwargs,
                    {"local_files_only": True} if private else {},
                )

    def test_real_wgp_setup_boundary_finalizes_only_after_success_and_cleans_failures(
        self,
    ):
        source = (Path(__file__).resolve().parents[1] / "app/wgp.py").read_text()
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "load_models"
        )
        protected = next(
            node
            for node in function.body
            if isinstance(node, ast.Try)
            and any(
                isinstance(item, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "offloadobj"
                    for target in item.targets
                )
                for item in node.body
            )
        )
        start = next(
            i
            for i, node in enumerate(protected.body)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "offloadobj"
                for target in node.targets
            )
        )
        boundary = ast.Try(
            body=protected.body[start:],
            handlers=protected.handlers,
            orelse=[],
            finalbody=protected.finalbody,
        )
        code = compile(
            ast.fix_missing_locations(ast.Module(body=[boundary], type_ignores=[])),
            "reviewed-wgp-setup-boundary",
            "exec",
        )
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}):
            for failure in (None, "setup", "compile", "quantize", "lora", "cancel"):
                with self.subTest(failure=failure):
                    model = self.fake_loaded()
                    model._h3_runtime_binding = None
                    model._h3_runtime_snapshot = binding.snapshot_h3_runtime_files(
                        self.files,
                        model._h3_runtime_code_contract(),
                        processor_dir=self.processor,
                    )
                    owner = SimpleNamespace(release=Mock())

                    def setup(model=model, owner=owner, failure=failure, **_kwargs):
                        with self.assertRaisesRegex(
                            binding.H3RuntimeBindingError, "unavailable"
                        ):
                            model.verified_h3_runtime_sha256()
                        if failure == "setup":
                            raise RuntimeError("fake setup failure")
                        if failure == "lora":
                            model.transformer._loras_model_data = {"adapter": object()}
                        return owner

                    namespace = {
                        "wan_model": model,
                        "pipe": {},
                        "kwargs": {},
                        "loras_transformer": [],
                        "handler_model_kwargs": {},
                        "offloadobj": None,
                        "offload_setup_started": True,
                        "offload": SimpleNamespace(
                            last_offload_obj=owner, flush_torch_caches=Mock()
                        ),
                        "gc": SimpleNamespace(collect=Mock()),
                        "clear_gen_cache": Mock(),
                        "torch": SimpleNamespace(set_default_device=Mock()),
                        "previous_default_device": "cpu",
                        "_run_model_offload_with_residency": lambda _pipe, **kwargs: (
                            setup(**kwargs)
                        ),
                        "mmgp_profile": 1,
                        "residency_key": {},
                        "residency_store": None,
                        "force_residency_reprofile": False,
                        "is_h3_load": True,
                        "offload_kwargs": {
                            "compile": failure == "compile",
                            "quantizeTransformer": failure == "quantize",
                            "convertWeightsFloatTo": torch.float32,
                        },
                        "model_load_succeeded": False,
                        "status_reporter": SimpleNamespace(
                            check_cancelled=Mock(
                                side_effect=[
                                    None,
                                    InterruptedError("fake finalize cancellation"),
                                ]
                                if failure == "cancel"
                                else None
                            ),
                            transition=Mock(),
                            close=Mock(),
                        ),
                    }
                    if failure is None:
                        exec(code, namespace)  # noqa: S102 - reviewed repo AST, fake setup only.
                        self.assertTrue(namespace["model_load_succeeded"])
                        self.assertRegex(
                            model.verified_h3_runtime_sha256(), r"^[0-9a-f]{64}$"
                        )
                        owner.release.assert_not_called()
                    else:
                        with self.assertRaises(
                            (
                                RuntimeError,
                                InterruptedError,
                                binding.H3RuntimeBindingError,
                            )
                        ):
                            exec(code, namespace)  # noqa: S102
                        self.assertFalse(namespace["model_load_succeeded"])
                        self.assertIsNone(model._h3_runtime_binding)
                        self.assertEqual(model._h3_runtime_components(), (None,) * 6)
                        owner.release.assert_called_once()

    def test_actual_tempfile_identity_restores_a_fresh_cpu_instance(self):
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}):
            original = self.fake_loaded()
            generated = original.generate(
                "scene",
                frame_num=141,
                height=64,
                width=64,
                sampling_steps=2,
                seed=1,
                custom_settings={"h3_attention_engine": "sdpa"},
                _h3_cumulative_capture=True,
            )
            original.release()
            fresh = self.fake_loaded()
            fresh._h3_runtime_snapshot = binding.snapshot_h3_runtime_files(
                self.files,
                fresh._h3_runtime_code_contract(),
                processor_dir=self.processor,
            )
            fresh.finalize_h3_runtime_binding(
                compile=False,
                quantize_transformer=False,
                convert_weights_float_to=torch.float32,
            )
            identity = H3CumulativeIdentity(
                "owner",
                "project",
                "chain",
                "job",
                fresh.verified_h3_runtime_sha256(),
                64,
                64,
            )
            recovered = H3CumulativeRecovery(
                generated["_h3_cumulative_handoff"]["state"],
                identity,
                "unit:v1:" + "b" * 64,
            )
            handoff = fresh.restore_h3_cumulative_handoff(
                recovered, expected_identity=identity
            )
            self.assertIs(handoff["state"], recovered.state)
            self.assertIs(handoff["model_token"], fresh._h3_cumulative_token)

    def test_real_empty_mmgp_lora_hooks_are_allowed_but_adapters_are_rejected(self):
        from mmgp import offload

        model = torch.nn.Sequential(torch.nn.Linear(2, 2))
        inputs = torch.ones(1, 2)
        expected = model(inputs)
        original = binding.tensor_layout_sha256([model])
        model._loras_model_data = {}
        model[0].forward = offload.offload.hook_lora(
            None, model[0], model, "transformer", model._loras_model_data, {}, "0"
        )
        self.assertEqual(binding.tensor_layout_sha256([model]), original)
        self.assertTrue(torch.equal(model(inputs), expected))
        model[0]._mm_lora_data["adapter"] = {"weight": torch.ones(2, 2)}
        with self.assertRaisesRegex(binding.H3RuntimeBindingError, "LoRA"):
            binding.tensor_layout_sha256([model])
        # A stale/missing root registry cannot hide module-level adapter data.
        del model._loras_model_data
        with self.assertRaisesRegex(binding.H3RuntimeBindingError, "LoRA"):
            binding.tensor_layout_sha256([model])
        model[0]._mm_lora_data = {}
        for malformed in ({"foreign": {}}, {model[0]: None}, False):
            model._loras_model_data = malformed
            with self.assertRaisesRegex(binding.H3RuntimeBindingError, "LoRA"):
                binding.tensor_layout_sha256([model])

    def test_effective_tensor_dtype_and_loaded_loras_invalidate_contract(self):
        parameter = torch.nn.Parameter(torch.zeros(2, dtype=torch.float32))
        components = tuple(torch.nn.Linear(2, 2) for _ in range(4))
        components[0].weight = parameter
        before = binding.tensor_layout_sha256(components)
        components[0].weight = torch.nn.Parameter(parameter.to(torch.float16))
        self.assertNotEqual(binding.tensor_layout_sha256(components), before)
        components[0]._loras_model_data = {"fixture-adapter": object()}
        with self.assertRaisesRegex(binding.H3RuntimeBindingError, "LoRA"):
            binding.tensor_layout_sha256(components)
        components[0]._loras_model_data = {}
        components[0]._h3_turbo_active = True
        with self.assertRaisesRegex(binding.H3RuntimeBindingError, "Turbo"):
            binding.tensor_layout_sha256(components)

    def test_restore_rejects_absent_or_mismatched_actual_identity_before_minting_token(
        self,
    ):
        with patch.dict(os.environ, {"MAESTRO_H3_CUMULATIVE_EXPERIMENTAL": "1"}):
            model = self.fake_loaded()
            identity = H3CumulativeIdentity(
                "owner", "project", "chain", "job", "a" * 64, 64, 64
            )
            recovered = H3CumulativeRecovery(None, identity, {})
            with self.assertRaisesRegex(binding.H3RuntimeBindingError, "unavailable"):
                model.restore_h3_cumulative_handoff(
                    recovered, expected_identity=identity
                )
            snapshot = binding.snapshot_h3_runtime_files(
                self.files,
                model._h3_runtime_code_contract(),
                processor_dir=self.processor,
            )
            model._h3_runtime_binding = snapshot.bind(
                model._h3_runtime_components(), model._h3_loaded_runtime_contract()
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                model.restore_h3_cumulative_handoff(
                    recovered, expected_identity=identity
                )
            self.assertIsNone(model._h3_cumulative_token)


if __name__ == "__main__":
    unittest.main()
