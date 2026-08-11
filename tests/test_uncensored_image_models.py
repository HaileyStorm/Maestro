"""Model-free registry regressions for vetted uncensored image recipes."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULTS = _ROOT / "app" / "defaults"
_FLUX_MAIN = _ROOT / "app" / "models" / "flux" / "flux_main.py"
_FLUX_HANDLER = _ROOT / "app" / "models" / "flux" / "flux_handler.py"
_QWEN3_ENCODER = (
    _ROOT / "app" / "models" / "flux" / "modules" / "text_encoder_qwen3.py"
)
_WGP = _ROOT / "app" / "wgp.py"
_STORE = _ROOT / "ui" / "src" / "stores" / "useStore.ts"
_LAUNCH = _ROOT / "app" / "launch.py"

_PORNMASTER_PONPOKE_RECIPE = (
    "flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke"
)
_KREA2_MOODY_MIX_RECIPE = "krea2_moody_mix_v7_fp8"
_KREA2_MOODY_CUTIE_RECIPE = "krea2_moody_cutie_v4_fp8"
_MANUAL_CHECKPOINT_INTEGRITY_FUNCTIONS = {
    "_checkpoint_stat_identity",
    "_delete_manual_checkpoint_receipt",
    "_manual_checkpoint_cache_key",
    "_manual_checkpoint_integrity_spec",
    "_manual_checkpoint_path_digest",
    "_manual_checkpoint_receipt_path",
    "_manual_checkpoint_resolved_path",
    "_recover_manual_checkpoint_receipt",
    "_require_manual_checkpoint_integrity",
    "_store_manual_checkpoint_receipt",
    "_verified_local_checkpoint_record",
    "_verify_local_checkpoint_integrity",
    "manual_checkpoint_integrity_ready",
    "manual_checkpoint_integrity_required",
    "verify_manual_checkpoint_integrity",
}


def _load_default(model_id: str) -> dict:
    return json.loads((_DEFAULTS / f"{model_id}.json").read_text(encoding="utf-8"))


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing assignment {name} in {path}")


def _load_functions(path: Path, names: set[str]):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if {node.name for node in functions} != names:
        raise AssertionError(f"Missing helper functions in {path}")
    namespace: dict[str, object] = {"os": __import__("os")}
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


def _configure_manual_checkpoint_loader(
    loader,
    contracts,
    state_dir,
    *,
    cache=None,
):
    loader.update({
        "hashlib": __import__("hashlib"),
        "json": __import__("json"),
        "stat": __import__("stat"),
        "tempfile": __import__("tempfile"),
        "PORNMASTER_V4_PONPOKE_RECIPE": _PORNMASTER_PONPOKE_RECIPE,
        "_MANUAL_CHECKPOINT_INTEGRITY_CONTRACTS": contracts,
        "_MANUAL_CHECKPOINT_RECEIPT_MAX_BYTES": 16 * 1024,
        "_MANUAL_CHECKPOINT_RECEIPT_SCHEMA_VERSION": 1,
        "_MANUAL_CHECKPOINT_VERIFICATION_CACHE": (
            {} if cache is None else cache
        ),
        "_MANUAL_CHECKPOINT_VERIFICATION_LOCK": __import__(
            "threading"
        ).RLock(),
        "_MANUAL_CHECKPOINT_VERIFICATION_STATE_DIR": str(state_dir),
    })
    return loader


def _load_encoder_asset_resolvers():
    source = _FLUX_MAIN.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_FLUX_MAIN))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {
            "_text_encoder_tokenizer_folder",
            "_qwen3_encoder_asset_paths",
        }
    ]
    namespace: dict[str, object] = {"os": __import__("os")}
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(module, str(_FLUX_MAIN), "exec"), namespace)
    return namespace


def _load_flux_auxiliary_query():
    tree = ast.parse(_FLUX_HANDLER.read_text(encoding="utf-8"))
    handler = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "family_handler"
    )
    function = copy.deepcopy(next(
        node for node in handler.body
        if isinstance(node, ast.FunctionDef) and node.name == "query_model_files"
    ))
    function.decorator_list = []
    namespace = {"test_flux2": lambda model_type: model_type.startswith("flux2")}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(_FLUX_HANDLER), "exec"), namespace)
    return namespace["query_model_files"]


class TestUncensoredKleinRegistry(unittest.TestCase):
    CASES = {
        "flux2_klein_4b_uncensored": {
            "base": "flux2_klein_4b",
            "family": "FLUX.2 Klein 4B",
            "revision": "633217e588e4c0bc76619052e05d3ce0e057cd83",
            "repo": "ponpoke/flux2-klein-4b-uncensored-text-encoder",
            "path": "flux2-klein-4b-uncensored-text-encoder/model.safetensors",
            "size": 8044981680,
            "weight_folder": "flux2_klein_4b_uncensored_text_encoder",
            "tokenizer_folder": "Qwen3",
            "term": "ponpoke_flux2_klein_4b_self_review",
            "schedule": {
                "resolution": "1024x1024",
                "batch_size": 1,
                "embedded_guidance_scale": 1,
                "num_inference_steps": 4,
            },
        },
        "flux2_klein_9b_uncensored": {
            "base": "flux2_klein_9b",
            "family": "FLUX.2 Klein 9B",
            "revision": "fba36e796aac081246708dd30392a401ba44922e",
            "repo": "ponpoke/flux2-klein-9b-uncensored-text-encoder",
            "path": "model.safetensors",
            "size": 16381516808,
            "weight_folder": "flux2_klein_9b_uncensored_text_encoder",
            "tokenizer_folder": "qwen3_8b",
            "term": "ponpoke_flux2_klein_9b_self_review",
            "schedule": {
                "resolution": "1024x1024",
                "num_inference_steps": 4,
                "guidance_scale": 1,
            },
        },
    }

    def test_exact_family_base_checkpoint_and_schedule_are_preserved(self):
        for model_id, expected in self.CASES.items():
            with self.subTest(model_id=model_id):
                payload = _load_default(model_id)
                base = _load_default(expected["base"])
                model = payload["model"]
                self.assertEqual(model["architecture"], expected["base"])
                self.assertEqual(model["URLs"], expected["base"])
                self.assertEqual(
                    _load_default(model["URLs"])["model"]["URLs"],
                    base["model"]["URLs"],
                )
                for key, value in expected["schedule"].items():
                    self.assertEqual(payload[key], value)
                    self.assertEqual(payload[key], base[key])

    def test_encoder_artifacts_are_revision_pinned_and_not_hash_claimed(self):
        for model_id, expected in self.CASES.items():
            with self.subTest(model_id=model_id):
                model = _load_default(model_id)["model"]
                provenance = model["artifact_provenance"]
                expected_url = (
                    f"https://huggingface.co/{expected['repo']}/resolve/"
                    f"{expected['revision']}/{expected['path']}"
                )
                self.assertEqual(model["text_encoder_URLs"], [expected_url])
                self.assertTrue(expected_url.endswith(".safetensors"))
                self.assertEqual(model["text_encoder_quantization"], "bf16")
                self.assertEqual(model["text_encoder_folder"], expected["weight_folder"])
                self.assertEqual(
                    model["text_encoder_tokenizer_folder"],
                    expected["tokenizer_folder"],
                )
                self.assertNotEqual(
                    model["text_encoder_folder"],
                    model["text_encoder_tokenizer_folder"],
                )
                self.assertEqual(model["source_repo_id"], expected["repo"])
                self.assertEqual(model["source_revision"], expected["revision"])
                self.assertEqual(model["source_path"], expected["path"])
                self.assertTrue(model["source_gated"])
                self.assertTrue(model["manual_review_required"])
                self.assertEqual(model["license_id"], "flux-non-commercial-v2.1")
                self.assertEqual(model["required_host_terms"], [expected["term"]])
                self.assertEqual(
                    model["license_url"],
                    f"https://huggingface.co/{expected['repo']}/blob/"
                    f"{expected['revision']}/README.md",
                )
                self.assertEqual(provenance["repo_id"], expected["repo"])
                self.assertEqual(provenance["revision"], expected["revision"])
                self.assertEqual(provenance["path"], expected["path"])
                self.assertEqual(provenance["size_bytes"], expected["size"])
                self.assertEqual(provenance["access"], "gated-auto")
                self.assertEqual(
                    provenance["license"],
                    "FLUX non-commercial v2.1 plus repository access conditions",
                )
                self.assertIsNone(provenance["content_sha256"])
                self.assertEqual(
                    provenance["content_sha256_status"],
                    "unavailable_until_authorized_download",
                )

    def test_recipe_and_selector_metadata_are_truthful_and_specific(self):
        for model_id, expected in self.CASES.items():
            with self.subTest(model_id=model_id):
                model = _load_default(model_id)["model"]
                self.assertIn(expected["family"], model["name"])
                self.assertIn("Uncensored Text Encoder (Experimental)", model["name"])
                description = model["description"].lower()
                for phrase in (
                    "experimental exact-family",
                    "swaps only",
                    "gated",
                    "non-commercial",
                    "sha-256 is unavailable",
                ):
                    self.assertIn(phrase, description)
                recipe = model["capability_recipe"]
                self.assertEqual(recipe["kind"], "conditioning_encoder_swap")
                self.assertEqual(recipe["base_model"], expected["base"])
                self.assertEqual(recipe["changed_components"], ["text_encoder"])
                self.assertEqual(
                    recipe["preserved_components"],
                    ["transformer", "vae", "tokenizer"],
                )
                self.assertEqual(
                    recipe["quality_status"],
                    "experimental_requires_benchmark",
                )
                self.assertEqual(
                    model["content_capability"],
                    "uncensored_conditioning",
                )
                self.assertNotIn("nsfw_only", model)

    def test_tokenizer_and_config_assets_remain_shared_while_weights_are_isolated(self):
        resolvers = _load_encoder_asset_resolvers()
        resolve = resolvers["_text_encoder_tokenizer_folder"]
        resolve_assets = resolvers["_qwen3_encoder_asset_paths"]
        self.assertEqual(
            resolve({
                "text_encoder_folder": "variant_weights",
                "text_encoder_tokenizer_folder": "canonical_tokenizer",
            }),
            "canonical_tokenizer",
        )
        self.assertEqual(
            resolve({"text_encoder_folder": "canonical_base"}),
            "canonical_base",
        )
        locate = lambda path: f"/models/{path}"
        self.assertEqual(
            resolve_assets({
                "text_encoder_folder": "variant_weights",
                "text_encoder_tokenizer_folder": "Qwen3",
            }, locate),
            ("/models/Qwen3", "/models/Qwen3/config.json"),
        )
        self.assertEqual(
            resolve_assets({"text_encoder_folder": "Qwen3"}, locate),
            ("/models/Qwen3", None),
        )
        config_resolver = _load_functions(
            _QWEN3_ENCODER,
            {"_resolve_model_config_path"},
        )["_resolve_model_config_path"]
        self.assertEqual(
            config_resolver("/models/Qwen3/qwen3_bf16.safetensors"),
            "/models/Qwen3/config.json",
        )
        self.assertEqual(
            config_resolver(
                "/models/variant_weights/model.safetensors",
                "/models/Qwen3/config.json",
            ),
            "/models/Qwen3/config.json",
        )
        query_auxiliary = _load_flux_auxiliary_query()
        for model_id, expected in self.CASES.items():
            with self.subTest(model_id=model_id, asset="canonical_config"):
                auxiliary = query_auxiliary(
                    lambda filename: [filename],
                    expected["base"],
                    _load_default(model_id)["model"],
                )
                self.assertEqual(
                    auxiliary[0]["sourceFolderList"],
                    [expected["tokenizer_folder"]],
                )
                self.assertIn("config.json", auxiliary[0]["fileList"][0])
                self.assertIn("tokenizer_config.json", auxiliary[0]["fileList"][0])
        source = _FLUX_MAIN.read_text(encoding="utf-8")
        self.assertIn(
            'encoder_kwargs["config_path"] = config_path',
            source,
        )

    def test_unvetted_or_cross_family_adapters_are_not_promoted(self):
        combined = "\n".join(
            json.dumps(_load_default(model_id)).lower()
            for model_id in self.CASES
        )
        for unvetted in (
            "pinocookie",
            "projector-scale",
            "helper-slider",
            "diroverflo",
            ".pt\"",
        ):
            with self.subTest(unvetted=unvetted):
                self.assertNotIn(unvetted, combined)
        for model_id in self.CASES:
            self.assertNotIn("loras", _load_default(model_id)["model"])


class TestLayeredKlein9BRegistry(unittest.TestCase):
    def setUp(self):
        self.payload = _load_default(_PORNMASTER_PONPOKE_RECIPE)
        self.model = self.payload["model"]

    def test_component_graph_keeps_exact_tune_vae_and_encoder_roles(self):
        self.assertEqual(self.model["architecture"], "flux2_klein_9b")
        recipe = self.model["capability_recipe"]
        self.assertEqual(
            recipe["kind"],
            "layered_checkpoint_and_conditioning_encoder",
        )
        self.assertEqual(recipe["base_model"], "flux2_klein_9b")
        self.assertEqual(recipe["operations"], ["generation", "editing"])
        self.assertEqual(
            recipe["changed_components"],
            ["transformer", "text_encoder"],
        )
        self.assertEqual(
            recipe["preserved_components"],
            ["vae", "tokenizer_config"],
        )
        graph = {entry["component"]: entry for entry in recipe["component_graph"]}
        self.assertEqual(set(graph), {
            "transformer", "vae", "text_encoder", "tokenizer_config",
        })
        self.assertEqual(
            graph["transformer"],
            {
                "component": "transformer",
                "role": "exact_family_tune",
                "artifact": "checkpoint",
            },
        )
        self.assertEqual(
            graph["vae"],
            {
                "component": "vae",
                "role": "preserved_external_base",
                "source_model": "flux2_klein_9b",
                "filename": "flux2_vae.safetensors",
            },
        )
        self.assertEqual(graph["text_encoder"]["family"], "qwen3_8b")
        self.assertEqual(graph["text_encoder"]["precision"], "bf16")
        self.assertEqual(graph["tokenizer_config"]["folder"], "qwen3_8b")

    def test_checkpoint_provenance_is_exact_and_loader_fails_closed(self):
        provenance = self.model["artifact_provenance"]
        self.assertEqual(provenance["scope"], "changed_artifacts_only")
        self.assertEqual(
            provenance["preserved_asset_sources"],
            "capability_recipe.component_graph",
        )
        checkpoint = provenance["checkpoint"]
        filename = "pornmasterFlux2Klein_v4TurboFp8.safetensors"
        self.assertEqual(self.model["URLs"], [filename])
        self.assertFalse(self.model["URLs"][0].startswith(("http://", "https://")))
        self.assertEqual(checkpoint, {
            "provider": "civitai",
            "artifact_kind": "exact_family_tune",
            "creator": "iamddtla",
            "model_id": 2382648,
            "version_id": 2973304,
            "filename": filename,
            "precision": "fp8",
            "size_kb": 9212016.476,
            "size_bytes": 9433104872,
            "sha256": "E90EEB50140A10806341B7521C340214C6F76CEC2F8F8DAE7A443C5806072DF7",
            "source_url": (
                "https://civitai.com/models/2382648?modelVersionId=2973304"
            ),
            "download_url": "https://civitai.com/api/download/models/2973304",
            "download_policy": "manual_hash_verified_only",
            "loader_auto_download": False,
            "exact_family": "FLUX.2 Klein 9B",
            "operations": ["generation", "editing"],
            "creator_terms": {
                "allowNoCredit": False,
                "allowDerivatives": True,
                "allowCommercialUse": ["RentCivit"],
                "underlying_base_license": "FLUX non-commercial",
            },
        })
        self.assertEqual(
            self.model["required_host_terms"],
            [
                "civitai_2382648_2973304_creator_terms",
                "bfl_flux2_self_review",
                "ponpoke_flux2_klein_9b_self_review",
            ],
        )
        for phrase in ("iamddtla", "credit", "derivatives", "RentCivit"):
            self.assertIn(phrase, self.model["selector_help"])

        loader = _load_functions(_WGP, {"download_models"})
        network_calls = []

        class MissingCheckpointLocator:
            @staticmethod
            def get_smart_download_location(name, force_path=None):
                return f"/missing/{name}"

        loader.update({
            "server_config": {},
            "RIFE_V3_FILENAME": "flownet.pkl",
            "RIFE_V4_FILENAME": "rife4.26.pkl",
            "process_files_def": lambda **_kwargs: None,
            "query_matanyone_download_def": lambda _config: {},
            "download_mmaudio": lambda: None,
            "download_shared_done": False,
            "get_base_model_type": lambda _model_id: "flux2_klein_9b",
            "get_model_def": lambda _model_id: self.model,
            "model_types_handlers": {"flux2_klein_9b": object()},
            "get_local_model_filename": lambda *_args, **_kwargs: None,
            "fl": MissingCheckpointLocator,
            "download_file": lambda *args, **kwargs: network_calls.append(
                (args, kwargs)
            ),
        })
        with self.assertRaisesRegex(
            Exception,
            "was not found locally and no URL was provided",
        ):
            loader["download_models"](
                filename,
                _PORNMASTER_PONPOKE_RECIPE,
                0,
                1,
            )
        self.assertEqual(network_calls, [])

    def test_manual_checkpoint_preflight_verifies_bytes_before_load_work(self):
        loader = _load_functions(
            _WGP,
            _MANUAL_CHECKPOINT_INTEGRITY_FUNCTIONS,
        )
        contracts = {
            _PORNMASTER_PONPOKE_RECIPE: {
                "filename": "pornmasterFlux2Klein_v4TurboFp8.safetensors",
                "size_bytes": 9433104872,
                "sha256": (
                    "e90eeb50140a10806341b7521c340214c6f76cec2f8f8dae7a443c5806072df7"
                ),
            },
        }
        _configure_manual_checkpoint_loader(loader, contracts, "unused")
        spec = loader["_manual_checkpoint_integrity_spec"](
            _PORNMASTER_PONPOKE_RECIPE,
            self.model,
        )
        self.assertEqual(spec, {
            "filename": "pornmasterFlux2Klein_v4TurboFp8.safetensors",
            "size_bytes": 9433104872,
            "sha256": (
                "e90eeb50140a10806341b7521c340214c6f76cec2f8f8dae7a443c5806072df7"
            ),
            "recipe_id": _PORNMASTER_PONPOKE_RECIPE,
        })

        with tempfile.TemporaryDirectory() as tmp:
            loader["_MANUAL_CHECKPOINT_VERIFICATION_STATE_DIR"] = str(
                Path(tmp, "receipts")
            )
            artifact = Path(tmp, spec["filename"])
            artifact.write_bytes(b"wrong-size")
            loader["get_local_model_filename"] = lambda _filename: str(artifact)
            with self.assertRaisesRegex(RuntimeError, "integrity check failed"):
                loader["_require_manual_checkpoint_integrity"](
                    _PORNMASTER_PONPOKE_RECIPE,
                    self.model,
                )

            payload = b"synthetic-checkpoint"
            artifact.write_bytes(payload)
            self.assertEqual(
                loader["_verify_local_checkpoint_integrity"](
                    str(artifact),
                    model_type=_PORNMASTER_PONPOKE_RECIPE,
                    expected_size=len(payload),
                    expected_sha256=__import__("hashlib").sha256(
                        payload,
                    ).hexdigest(),
                ),
                str(artifact),
            )
            artifact.write_bytes(b"synthetic-checkpoinu")
            with self.assertRaisesRegex(RuntimeError, "integrity check failed"):
                loader["_verify_local_checkpoint_integrity"](
                    str(artifact),
                    model_type=_PORNMASTER_PONPOKE_RECIPE,
                    expected_size=len(payload),
                    expected_sha256=__import__("hashlib").sha256(
                        payload,
                    ).hexdigest(),
                )

        for field, replacement in (
            ("download_policy", None),
            ("size_bytes", 1),
            ("sha256", "0" * 64),
        ):
            with self.subTest(contract_field=field):
                drifted = copy.deepcopy(self.model)
                drifted["artifact_provenance"]["checkpoint"][field] = replacement
                with self.assertRaisesRegex(RuntimeError, "contract is invalid"):
                    loader["_manual_checkpoint_integrity_spec"](
                        _PORNMASTER_PONPOKE_RECIPE,
                        drifted,
                    )
        self.assertIsNone(loader["_manual_checkpoint_integrity_spec"](
            "flux2_klein_9b",
            _load_default("flux2_klein_9b")["model"],
        ))
        alias_id = "pornmaster_manual_alias"
        alias = {
            "architecture": "flux2_klein_9b",
            "URLs": _PORNMASTER_PONPOKE_RECIPE,
        }
        alias_definitions = {
            alias_id: alias,
            _PORNMASTER_PONPOKE_RECIPE: self.model,
        }
        self.assertEqual(
            loader["_manual_checkpoint_integrity_spec"](
                alias_id,
                alias,
                alias_definitions,
            ),
            spec,
        )
        with self.assertRaisesRegex(RuntimeError, "contract is invalid"):
            loader["_manual_checkpoint_integrity_spec"](
                alias_id,
                alias,
                {alias_id: alias},
            )
        ordinary_missing_alias = {"URLs": "arbitrary_local_import"}
        self.assertIsNone(loader["_manual_checkpoint_integrity_spec"](
            "ordinary_missing_alias",
            ordinary_missing_alias,
            {"ordinary_missing_alias": ordinary_missing_alias},
        ))

        tree = ast.parse(_WGP.read_text(encoding="utf-8"), filename=str(_WGP))
        load_models = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "load_models"
        )
        call_lines = {
            name: sorted(
                node.lineno
                for node in ast.walk(load_models)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
            )
            for name in {
                "_require_manual_checkpoint_integrity",
                "_invalidate_loaded_model_state",
                "download_models",
            }
        }
        self.assertLess(
            min(call_lines["_require_manual_checkpoint_integrity"]),
            min(call_lines["_invalidate_loaded_model_state"]),
        )
        self.assertLess(
            min(call_lines["_require_manual_checkpoint_integrity"]),
            min(call_lines["download_models"]),
        )

        load_namespace = _load_functions(_WGP, {"load_models"})
        loader["get_local_model_filename"] = lambda _filename: None
        load_namespace.update({
            "server_config": {"services": {}},
            "require_model_terms": lambda *_args: None,
            "_require_manual_checkpoint_integrity": loader[
                "_require_manual_checkpoint_integrity"
            ],
        })
        drifted_policy = copy.deepcopy(self.model)
        drifted_policy["artifact_provenance"]["checkpoint"].pop(
            "download_policy",
        )
        drifted_hash = copy.deepcopy(self.model)
        drifted_hash["artifact_provenance"]["checkpoint"]["sha256"] = "0" * 64
        for label, candidate, error in (
            ("missing", self.model, "missing or unavailable"),
            ("malformed", drifted_policy, "contract is invalid"),
            ("mismatched", drifted_hash, "contract is invalid"),
        ):
            with self.subTest(load_boundary=label):
                side_effects = []
                load_namespace.update({
                    "models_def": {
                        _PORNMASTER_PONPOKE_RECIPE: candidate,
                    },
                    "get_model_def": lambda _model_id, value=candidate: value,
                    "get_base_model_type": lambda _model_id: side_effects.append(
                        "base-model"
                    ),
                    "_invalidate_loaded_model_state": lambda: side_effects.append(
                        "invalidate"
                    ),
                    "download_models": lambda *_args, **_kwargs: side_effects.append(
                        "download"
                    ),
                })
                with self.assertRaisesRegex(RuntimeError, error):
                    load_namespace["load_models"](_PORNMASTER_PONPOKE_RECIPE)
                self.assertEqual(side_effects, [])

        side_effects = []
        load_namespace.update({
            "models_def": alias_definitions,
            "get_model_def": lambda model_id: alias_definitions[model_id],
            "get_base_model_type": lambda _model_id: side_effects.append(
                "base-model"
            ),
            "_invalidate_loaded_model_state": lambda: side_effects.append(
                "invalidate"
            ),
            "download_models": lambda *_args, **_kwargs: side_effects.append(
                "download"
            ),
        })
        with self.assertRaisesRegex(RuntimeError, "missing or unavailable"):
            load_namespace["load_models"](alias_id)
        self.assertEqual(side_effects, [])

        orphan_definitions = {alias_id: alias}
        side_effects = []
        loader["get_local_model_filename"] = lambda _filename: side_effects.append(
            "local-lookup"
        )
        load_namespace.update({
            "models_def": orphan_definitions,
            "get_model_def": lambda model_id: orphan_definitions.get(model_id),
            "get_base_model_type": lambda _model_id: side_effects.append(
                "base-model"
            ),
            "_invalidate_loaded_model_state": lambda: side_effects.append(
                "invalidate"
            ),
            "download_models": lambda *_args, **_kwargs: side_effects.append(
                "download"
            ),
        })
        with self.assertRaisesRegex(RuntimeError, "contract is invalid"):
            load_namespace["load_models"](alias_id)
        self.assertEqual(side_effects, [])

    def test_manual_checkpoint_status_requires_stable_cached_preflight(self):
        loader = _load_functions(
            _WGP,
            _MANUAL_CHECKPOINT_INTEGRITY_FUNCTIONS,
        )
        payload = b"synthetic-checkpoint"
        digest = __import__("hashlib").sha256(payload).hexdigest()
        contract = {
            "filename": "pornmasterFlux2Klein_v4TurboFp8.safetensors",
            "size_bytes": len(payload),
            "sha256": digest,
        }
        synthetic = copy.deepcopy(self.model)
        checkpoint = synthetic["artifact_provenance"]["checkpoint"]
        checkpoint.update({
            "size_bytes": len(payload),
            "sha256": digest.upper(),
        })
        cache = {}
        contracts = {
            _PORNMASTER_PONPOKE_RECIPE: contract,
        }
        alias_id = "pornmaster_manual_alias"
        alias = {
            "architecture": "flux2_klein_9b",
            "URLs": _PORNMASTER_PONPOKE_RECIPE,
        }
        definitions = {
            alias_id: alias,
            _PORNMASTER_PONPOKE_RECIPE: synthetic,
        }
        _configure_manual_checkpoint_loader(
            loader,
            contracts,
            "unused",
            cache=cache,
        )
        required = loader["manual_checkpoint_integrity_required"]
        self.assertTrue(required(
            _PORNMASTER_PONPOKE_RECIPE,
            synthetic,
            definitions,
        ))
        self.assertTrue(required(alias_id, alias, definitions))
        self.assertTrue(required(
            alias_id,
            alias,
            {alias_id: alias},
        ))
        ordinary = _load_default("flux2_klein_9b")["model"]
        self.assertFalse(required("flux2_klein_9b", ordinary, {
            "flux2_klein_9b": ordinary,
        }))
        unregistered_manual = {
            "URLs": ["manual.safetensors"],
            "artifact_provenance": {
                "checkpoint": {
                    "download_policy": "manual_hash_verified_only",
                },
            },
        }
        self.assertTrue(required(
            "unregistered_manual",
            unregistered_manual,
            {"unregistered_manual": unregistered_manual},
        ))

        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = Path(tmp, "receipts")
            _configure_manual_checkpoint_loader(
                loader,
                contracts,
                receipt_dir,
                cache=cache,
            )
            artifact = Path(tmp, contract["filename"])
            artifact.write_bytes(payload)
            loader["get_local_model_filename"] = lambda _filename: str(artifact)
            ready = loader["manual_checkpoint_integrity_ready"]
            verify = loader["verify_manual_checkpoint_integrity"]
            self.assertFalse(ready(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))
            self.assertTrue(verify(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))
            self.assertTrue(ready(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))
            self.assertTrue(ready(alias_id, alias, definitions))

            receipt = receipt_dir / f"{_PORNMASTER_PONPOKE_RECIPE}.json"
            self.assertTrue(receipt.is_file())
            receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["schema_version"], 1)
            self.assertEqual(
                receipt_data["recipe_id"],
                _PORNMASTER_PONPOKE_RECIPE,
            )
            self.assertEqual(receipt_data["expected_size"], len(payload))
            self.assertEqual(receipt_data["expected_sha256"], digest)
            self.assertEqual(len(receipt_data["identity"]), 4)
            self.assertNotIn(str(artifact), receipt.read_text(encoding="utf-8"))
            self.assertNotIn(payload.decode("ascii"), receipt.read_text(
                encoding="utf-8",
            ))
            if __import__("os").name == "posix":
                self.assertEqual(receipt_dir.stat().st_mode & 0o777, 0o700)
                self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)

            cache.clear()
            real_hashlib = __import__("hashlib")

            class MetadataOnlyHashlib:
                @staticmethod
                def sha256(data=b""):
                    if not data:
                        raise AssertionError(
                            "status helper must not start a checkpoint hash"
                        )
                    return real_hashlib.sha256(data)

            loader["hashlib"] = MetadataOnlyHashlib
            self.assertTrue(ready(alias_id, alias, definitions))
            self.assertTrue(ready(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))

            class NoHashingAllowed:
                @staticmethod
                def sha256(*_args, **_kwargs):
                    raise AssertionError("status helper must not hash")

            loader["hashlib"] = NoHashingAllowed
            self.assertTrue(ready(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))

            real_os = loader["os"]

            class EvictingStatusOS:
                @staticmethod
                def stat(path):
                    cache.clear()
                    return real_os.stat(path)

            loader["os"] = EvictingStatusOS
            self.assertFalse(ready(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))
            self.assertEqual(cache, {})

            loader["os"] = real_os
            loader["hashlib"] = __import__("hashlib")
            moved_dir = Path(tmp, "moved")
            moved_dir.mkdir()
            moved_artifact = moved_dir / artifact.name
            artifact.replace(moved_artifact)
            loader["get_local_model_filename"] = (
                lambda _filename: str(moved_artifact)
            )
            self.assertFalse(ready(alias_id, alias, definitions))
            self.assertFalse(receipt.exists())
            moved_artifact.replace(artifact)
            loader["get_local_model_filename"] = lambda _filename: str(artifact)

            loader["_require_manual_checkpoint_integrity"](
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            )
            artifact.write_bytes(b"synthetic-checkpoinu")
            cache.clear()
            self.assertFalse(ready(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))
            self.assertEqual(cache, {})
            self.assertFalse(receipt.exists())

            artifact.write_bytes(payload)
            loader["_require_manual_checkpoint_integrity"](
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            )
            artifact.unlink()
            self.assertFalse(ready(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))
            self.assertEqual(cache, {})
            self.assertFalse(receipt.exists())

            artifact.write_bytes(payload)
            self.assertTrue(verify(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))
            cache.clear()
            stale_receipt = json.loads(receipt.read_text(encoding="utf-8"))
            stale_receipt["contract"]["sha256"] = "0" * 64
            receipt.write_text(json.dumps(stale_receipt), encoding="utf-8")
            receipt.chmod(0o600)
            self.assertFalse(ready(alias_id, alias, definitions))
            self.assertFalse(receipt.exists())

            self.assertTrue(verify(
                _PORNMASTER_PONPOKE_RECIPE,
                synthetic,
                definitions,
            ))
            cache.clear()
            if __import__("os").name == "posix":
                receipt.chmod(0o644)
                self.assertFalse(ready(alias_id, alias, definitions))
                self.assertFalse(receipt.exists())
                self.assertTrue(verify(
                    _PORNMASTER_PONPOKE_RECIPE,
                    synthetic,
                    definitions,
                ))
                cache.clear()

            receipt.write_text("{malformed", encoding="utf-8")
            receipt.chmod(0o600)
            self.assertFalse(ready(alias_id, alias, definitions))
            self.assertFalse(receipt.exists())

        class NoLocalLookup:
            def __call__(self, _filename):
                raise AssertionError("unregistered recipes must not inspect local files")

        loader["get_local_model_filename"] = NoLocalLookup()
        with self.assertRaisesRegex(RuntimeError, "not registered"):
            loader["verify_manual_checkpoint_integrity"](
                "flux2_klein_9b",
                ordinary,
                {"flux2_klein_9b": ordinary},
            )
        with self.assertRaisesRegex(RuntimeError, "not registered"):
            loader["verify_manual_checkpoint_integrity"](
                "unregistered_manual",
                unregistered_manual,
                {"unregistered_manual": unregistered_manual},
            )
        lookalike = copy.deepcopy(ordinary)
        lookalike.update({
            "architecture": synthetic["architecture"],
            "URLs": [contract["filename"]],
            "artifact_provenance": copy.deepcopy(
                synthetic["artifact_provenance"]
            ),
        })
        lookalike["artifact_provenance"]["checkpoint"].pop(
            "download_policy"
        )
        self.assertFalse(required(
            "arbitrary_local_civitai_import",
            lookalike,
            {"arbitrary_local_civitai_import": lookalike},
        ))
        with self.assertRaisesRegex(RuntimeError, "not registered"):
            loader["verify_manual_checkpoint_integrity"](
                "arbitrary_local_civitai_import",
                lookalike,
                {"arbitrary_local_civitai_import": lookalike},
            )

        drifted = copy.deepcopy(synthetic)
        drifted["artifact_provenance"]["checkpoint"]["sha256"] = "0" * 64
        drifted["artifact_provenance"]["checkpoint"].pop("download_policy")
        self.assertTrue(required(
            _PORNMASTER_PONPOKE_RECIPE,
            drifted,
            {_PORNMASTER_PONPOKE_RECIPE: drifted},
        ))
        self.assertFalse(loader["manual_checkpoint_integrity_ready"](
            _PORNMASTER_PONPOKE_RECIPE,
            drifted,
            {_PORNMASTER_PONPOKE_RECIPE: drifted},
        ))

    def test_registered_krea2_manual_roots_are_exact_and_fail_closed(self):
        tree = ast.parse(_WGP.read_text(encoding="utf-8"), filename=str(_WGP))
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_MANUAL_CHECKPOINT_INTEGRITY_CONTRACTS"
                for target in node.targets
            )
        )
        expression = ast.fix_missing_locations(ast.Expression(assignment.value))
        contracts = eval(
            compile(expression, str(_WGP), "eval"),
            {"PORNMASTER_V4_PONPOKE_RECIPE": _PORNMASTER_PONPOKE_RECIPE},
            {},
        )
        expected = {
            _KREA2_MOODY_MIX_RECIPE: {
                "architecture": "krea2_raw",
                "provider": "civitai",
                "model_id": 2731187,
                "version_id": 3209007,
                "file_id": 3090691,
                "filename": "moodyKrea2Mix_v70.safetensors",
                "download_url": (
                    "https://civitai.com/api/download/models/3209007"
                    "?type=Diffusion%20Model&format=SafeTensor&fp=fp8"
                ),
                "size_bytes": 14125457032,
                "sha256": (
                    "405db6a1d060075d176c3578063b6fa2feb07b58bb61ddb403ddba0669a35a6d"
                ),
            },
            _KREA2_MOODY_CUTIE_RECIPE: {
                "architecture": "krea2_raw",
                "provider": "civitai",
                "model_id": 2764429,
                "version_id": 3211049,
                "file_id": 3092831,
                "filename": "moodyCutieMixKrea2_v40.safetensors",
                "download_url": (
                    "https://civitai.com/api/download/models/3211049"
                    "?type=Diffusion%20Model&format=SafeTensor&fp=fp8"
                ),
                "size_bytes": 14125457032,
                "sha256": (
                    "6c54d783aaaab1a6924fafcfa3afa9f36abe72a59723d424e932484a8c98316a"
                ),
            },
        }
        for root_id, contract in expected.items():
            self.assertEqual(contracts[root_id], contract)

        loader = _load_functions(
            _WGP,
            _MANUAL_CHECKPOINT_INTEGRITY_FUNCTIONS,
        )
        _configure_manual_checkpoint_loader(loader, contracts, "unused")

        def model_for(contract):
            return {
                "architecture": contract["architecture"],
                "URLs": [contract["filename"]],
                "artifact_provenance": {
                    "checkpoint": {
                        field: contract[field]
                        for field in (
                            "provider",
                            "model_id",
                            "version_id",
                            "file_id",
                            "filename",
                            "download_url",
                            "size_bytes",
                        )
                    } | {
                        "sha256": contract["sha256"].upper(),
                        "download_policy": "manual_hash_verified_only",
                        "loader_auto_download": False,
                    },
                },
            }

        required = loader["manual_checkpoint_integrity_required"]
        for root_id, contract in expected.items():
            with self.subTest(registered_root=root_id):
                model = model_for(contract)
                definitions = {root_id: model}
                self.assertTrue(required(root_id, model, definitions))
                self.assertEqual(
                    loader["_manual_checkpoint_integrity_spec"](
                        root_id,
                        model,
                        definitions,
                    ),
                    contract | {"recipe_id": root_id},
                )
                alias_id = f"{root_id}_alias"
                alias = {"URLs": root_id}
                alias_definitions = {alias_id: alias, root_id: model}
                self.assertEqual(
                    loader["_manual_checkpoint_integrity_spec"](
                        alias_id,
                        alias,
                        alias_definitions,
                    ),
                    contract | {"recipe_id": root_id},
                )
                with self.assertRaisesRegex(RuntimeError, "contract is invalid"):
                    loader["_manual_checkpoint_integrity_spec"](
                        alias_id,
                        alias,
                        {alias_id: alias},
                    )

                drift_cases = {
                    "architecture": "flux2_klein_9b",
                    "provider": "huggingface",
                    "model_id": contract["model_id"] + 1,
                    "version_id": contract["version_id"] + 1,
                    "file_id": contract["file_id"] + 1,
                    "filename": f"wrong-{contract['filename']}",
                    "download_url": (
                        f"https://civitai.com/api/download/models/{contract['version_id']}"
                    ),
                    "size_bytes": contract["size_bytes"] - 1,
                    "sha256": "0" * 64,
                    "download_policy": "auto",
                    "loader_auto_download": True,
                }
                for field, replacement in drift_cases.items():
                    drifted = copy.deepcopy(model)
                    target = (
                        drifted
                        if field == "architecture"
                        else drifted["artifact_provenance"]["checkpoint"]
                    )
                    target[field] = replacement
                    with self.subTest(root=root_id, drift=field):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "contract is invalid",
                        ):
                            loader["_manual_checkpoint_integrity_spec"](
                                root_id,
                                drifted,
                                {root_id: drifted},
                            )
                typed_drift = copy.deepcopy(model)
                typed_drift["artifact_provenance"]["checkpoint"]["model_id"] = float(
                    contract["model_id"]
                )
                with self.assertRaisesRegex(RuntimeError, "contract is invalid"):
                    loader["_manual_checkpoint_integrity_spec"](
                        root_id,
                        typed_drift,
                        {root_id: typed_drift},
                    )

        lookalike = model_for(expected[_KREA2_MOODY_MIX_RECIPE])
        lookalike["artifact_provenance"]["checkpoint"].pop("download_policy")
        for bypass_id in (
            "2731187",
            "3209007",
            "3090691",
            "krea2_moody_mix_v7_fp8_copy",
            "huggingface_local_krea2_import",
        ):
            with self.subTest(unregistered_lookalike=bypass_id):
                definitions = {bypass_id: lookalike}
                self.assertFalse(required(bypass_id, lookalike, definitions))
                self.assertIsNone(
                    loader["_manual_checkpoint_integrity_spec"](
                        bypass_id,
                        lookalike,
                        definitions,
                    )
                )

        load_namespace = _load_functions(_WGP, {"load_models"})
        load_namespace.update({
            "server_config": {"services": {}},
            "require_model_terms": lambda *_args: None,
            "_require_manual_checkpoint_integrity": loader[
                "_require_manual_checkpoint_integrity"
            ],
        })
        with tempfile.TemporaryDirectory() as tmp:
            loader["_MANUAL_CHECKPOINT_VERIFICATION_STATE_DIR"] = str(
                Path(tmp, "receipts")
            )
            for root_id, exact_contract in expected.items():
                payload = root_id.encode("ascii")
                tiny_contract = dict(exact_contract)
                tiny_contract.update({
                    "size_bytes": len(payload),
                    "sha256": __import__("hashlib").sha256(payload).hexdigest(),
                })
                model = model_for(tiny_contract)
                artifact = Path(tmp, tiny_contract["filename"])
                artifact.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
                side_effects = []
                loader["_MANUAL_CHECKPOINT_INTEGRITY_CONTRACTS"] = {
                    root_id: tiny_contract,
                }
                loader["get_local_model_filename"] = lambda _filename, path=artifact: str(
                    path
                )
                load_namespace.update({
                    "models_def": {root_id: model},
                    "get_model_def": lambda _model_id, value=model: value,
                    "get_base_model_type": lambda _model_id: side_effects.append(
                        "base-model"
                    ),
                    "_invalidate_loaded_model_state": lambda: side_effects.append(
                        "invalidate"
                    ),
                    "download_models": lambda *_args, **_kwargs: side_effects.append(
                        "download"
                    ),
                })
                with self.subTest(load_hash_boundary=root_id):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "integrity check failed",
                    ):
                        load_namespace["load_models"](root_id)
                    self.assertEqual(side_effects, [])

    def test_pinned_ponpoke_encoder_uses_canonical_qwen3_8b_assets(self):
        revision = "fba36e796aac081246708dd30392a401ba44922e"
        repo = "ponpoke/flux2-klein-9b-uncensored-text-encoder"
        self.assertEqual(self.model["text_encoder_URLs"], [
            f"https://huggingface.co/{repo}/resolve/{revision}/model.safetensors",
        ])
        self.assertEqual(
            self.model["text_encoder_folder"],
            "flux2_klein_9b_uncensored_text_encoder",
        )
        self.assertEqual(self.model["text_encoder_tokenizer_folder"], "qwen3_8b")
        self.assertEqual(self.model["text_encoder_quantization"], "bf16")
        encoder = self.model["artifact_provenance"]["text_encoder"]
        self.assertEqual(encoder["provider"], "huggingface")
        self.assertEqual(encoder["artifact_kind"], "conditioning_encoder")
        self.assertEqual(encoder["repo_id"], repo)
        self.assertEqual(encoder["revision"], revision)
        self.assertEqual(encoder["path"], "model.safetensors")
        self.assertEqual(encoder["size_bytes"], 16381516808)
        self.assertEqual(encoder["access"], "gated-auto")
        self.assertEqual(
            encoder["license"],
            "FLUX non-commercial v2.1 plus repository access conditions",
        )
        self.assertIsNone(encoder["content_sha256"])
        self.assertEqual(
            encoder["content_sha256_status"],
            "unavailable_until_authorized_download",
        )

        query_auxiliary = _load_flux_auxiliary_query()
        auxiliary = query_auxiliary(
            lambda filename: [filename],
            self.model["architecture"],
            self.model,
        )
        self.assertEqual(auxiliary[0]["repoId"], "DeepBeepMeep/Flux2")
        self.assertEqual(auxiliary[0]["sourceFolderList"], ["qwen3_8b"])
        self.assertIn("config.json", auxiliary[0]["fileList"][0])
        self.assertIn("tokenizer_config.json", auxiliary[0]["fileList"][0])
        self.assertEqual(auxiliary[1], {
            "repoId": "DeepBeepMeep/Flux2",
            "sourceFolderList": [""],
            "fileList": [["flux2_vae.safetensors"]],
        })
        provenance = self.model["artifact_provenance"]
        self.assertEqual(provenance["vae"], {
            "provider": "huggingface",
            "artifact_kind": "preserved_external_base_vae",
            "source_model": "flux2_klein_9b",
            "repo_id": "DeepBeepMeep/Flux2",
            "revision": None,
            "path": "flux2_vae.safetensors",
            "content_sha256": None,
            "content_sha256_status": (
                "inherited_base_asset_not_pinned_in_current_manifest"
            ),
        })
        tokenizer_config = provenance["tokenizer_config"]
        self.assertEqual(tokenizer_config["provider"], "huggingface")
        self.assertEqual(
            tokenizer_config["artifact_kind"],
            "canonical_tokenizer_and_config",
        )
        self.assertEqual(tokenizer_config["source_model"], "flux2_klein_9b")
        self.assertEqual(tokenizer_config["repo_id"], "DeepBeepMeep/Flux2")
        self.assertIsNone(tokenizer_config["revision"])
        self.assertEqual(tokenizer_config["folder"], "qwen3_8b")
        self.assertEqual(tokenizer_config["paths"], auxiliary[0]["fileList"][0])
        self.assertIsNone(tokenizer_config["content_sha256"])
        self.assertEqual(
            tokenizer_config["content_sha256_status"],
            "inherited_base_assets_not_pinned_in_current_manifest",
        )

    def test_schedule_and_cross_family_guards_are_exact(self):
        self.assertEqual(self.payload["resolution"], "1024x1024")
        self.assertEqual(self.payload["num_inference_steps"], 4)
        self.assertEqual(self.payload["guidance_scale"], 1)
        encoded = json.dumps(self.model).lower()
        self.assertNotIn("flux2-klein-4b-uncensored-text-encoder", encoded)
        self.assertNotIn('"family": "qwen3"', encoded)
        self.assertNotEqual(self.model["text_encoder_tokenizer_folder"], "Qwen3")
        self.assertNotIn("loras", self.model)

    def test_recipe_is_experimental_manual_only_and_never_an_auto_default(self):
        self.assertTrue(self.model["visible"])
        self.assertEqual(
            self.model["availability_status"],
            "experimental_manual_installation",
        )
        self.assertTrue(self.model["manual_installation_ready"])
        self.assertFalse(self.model["downloadable"])
        self.assertEqual(self.model["selection_policy"], "manual_only")
        self.assertFalse(self.model["automatic_routing"])
        self.assertFalse(self.model["verified"])
        self.assertEqual(self.model["default_for_operations"], [])
        self.assertEqual(
            self.model["capability_recipe"]["quality_status"],
            "experimental_requires_benchmark",
        )
        self.assertIn("(Experimental)", self.model["name"])
        self.assertIn("manual-only", self.model["description"].lower())
        launch = _LAUNCH.read_text(encoding="utf-8")
        self.assertNotIn(
            f'_PROJECT_REFERENCE_DEFAULT_CREATE_MODEL = "{_PORNMASTER_PONPOKE_RECIPE}"',
            launch,
        )
        self.assertNotIn(
            f'_PROJECT_REFERENCE_DEFAULT_EDITOR_MODEL = "{_PORNMASTER_PONPOKE_RECIPE}"',
            launch,
        )
        verified_routes = _literal_assignment(
            _LAUNCH,
            "_PROJECT_REFERENCE_VERIFIED_OPERATION_RECIPES",
        )
        self.assertNotIn(
            _PORNMASTER_PONPOKE_RECIPE,
            json.dumps(verified_routes, sort_keys=True),
        )


class TestCuratedImageVisibility(unittest.TestCase):
    FRESH_IMAGE_MODELS = {
        "flux2_dev",
        "flux2_klein_4b_uncensored",
        "flux2_klein_9b_uncensored",
        "flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke",
        "flux_krea",
        "flux_dev_kontext",
        "krea2_raw",
        "krea2_turbo",
        "krea2_raw_edit",
        "krea2_turbo_edit",
        "qwen_image_edit_2511_20B_fp8_lightning_8step",
        "qwen_image_edit_2511_nsfw",
    }

    @classmethod
    def setUpClass(cls):
        cls.store = _STORE.read_text(encoding="utf-8")
        cls.default_block = cls.store.split(
            "const DEFAULT_ENABLED_MODELS = new Set([", 1,
        )[1].split("])\n", 1)[0]

    def test_fresh_installs_see_curated_generation_and_edit_choices(self):
        for model_id in self.FRESH_IMAGE_MODELS:
            with self.subTest(model_id=model_id):
                self.assertIn(f"'{model_id}'", self.default_block)

    def test_v9_visibility_migration_is_bounded_idempotent_and_preserves_hides(self):
        version = int(re.search(
            r"const DEFAULTS_VERSION = (\d+)", self.store,
        ).group(1))
        self.assertEqual(version, 9)
        match = re.search(r"\n\s*9:\s*(\[[^\]]*\])", self.store)
        self.assertIsNotNone(match)
        additions = ast.literal_eval(match.group(1))
        self.assertEqual(additions, [
            "flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke",
        ])
        self.assertIn(
            "pre-v8 hide of an existing id cannot be distinguished",
            self.store,
        )
        self.assertIn(
            "for (let v = storedVer + 1; v <= DEFAULTS_VERSION; v++)",
            self.store,
        )
        self.assertIn("if (storedVer < DEFAULTS_VERSION)", self.store)

        # This mirrors the version gate: the v9 addition runs for v8 exactly
        # once, while a user hide persisted at v9 is not resurrected later.
        def migrate(stored_version: int, enabled: set[str]) -> set[str]:
            result = set(enabled)
            if stored_version < version:
                result.update(additions)
            return result

        recipe = "flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke"
        after_v9_hide = set(additions) - {recipe}
        self.assertNotIn(recipe, migrate(9, after_v9_hide))
        self.assertIn(recipe, migrate(8, set()))
        self.assertEqual(migrate(9, migrate(8, set())), {recipe})


class TestExistingQwenMatureRecipe(unittest.TestCase):
    def test_qwen_2511_mature_recipe_remains_exact_and_pinned(self):
        payload = _load_default("qwen_image_edit_2511_nsfw")
        model = payload["model"]
        self.assertEqual(model["architecture"], "qwen_image_edit_plus2_20B")
        self.assertEqual(payload["num_inference_steps"], 8)
        self.assertEqual(payload["guidance_scale"], 1)
        self.assertTrue(model["nsfw_only"])
        self.assertIn("OpenRAIL++", model["description"])
        self.assertEqual(model["loras_multipliers"], [1.0])
        self.assertEqual(model["loras"], [
            "https://huggingface.co/ScottzillaSystems/"
            "qwen-image-edit-plus-nsfw-lora/resolve/"
            "66e89e998dd4ea1a359c4bf0dd5e17d2f0b06ef0/"
            "qwen-image-edit-plus-nsfw-lora.safetensors",
        ])
        self.assertIn("lightning_8steps", model["URLs"][0])


class TestCuratedSiblingIntegrity(unittest.TestCase):
    def test_visible_sibling_families_keep_their_exact_architectures_and_schedules(self):
        cases = {
            "flux2_dev": ("flux2_dev", {"sampling_steps": 30, "embedded_guidance_scale": 4}),
            "flux_krea": ("flux", {}),
            "flux_dev_kontext": ("flux_dev_kontext", {}),
            "krea2_raw": ("krea2_raw", {"num_inference_steps": 52, "guidance_scale": 3.5}),
            "krea2_turbo": ("krea2_turbo", {"num_inference_steps": 8, "guidance_scale": 0}),
            "krea2_raw_edit": ("krea2_raw_edit", {"num_inference_steps": 20, "guidance_scale": 2}),
            "krea2_turbo_edit": ("krea2_turbo_edit", {"num_inference_steps": 8, "guidance_scale": 0}),
            "qwen_image_edit_2511_20B_fp8_lightning_8step": (
                "qwen_image_edit_plus2_20B",
                {"num_inference_steps": 8, "guidance_scale": 1},
            ),
            "qwen_image_edit_2511_nsfw": (
                "qwen_image_edit_plus2_20B",
                {"num_inference_steps": 8, "guidance_scale": 1},
            ),
        }
        for model_id, (architecture, schedule) in cases.items():
            with self.subTest(model_id=model_id):
                payload = _load_default(model_id)
                self.assertEqual(payload["model"]["architecture"], architecture)
                for key, expected in schedule.items():
                    self.assertEqual(payload[key], expected)


if __name__ == "__main__":
    unittest.main()
