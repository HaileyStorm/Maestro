"""Model-free regressions for image recipe license/self-review gates."""

from __future__ import annotations

import ast
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.host_terms import (  # noqa: E402
    BFL_FLUX1_REVIEW_TERM,
    BFL_FLUX2_REVIEW_TERM,
    CIVITAI_PORNMASTER_V4_CREATOR_TERM,
    CURRENT_HOST_TERM_BINDINGS,
    CURRENT_HOST_TERM_VERSIONS,
    KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
    KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH,
    KREA2_MOODY_CUTIE_V4_RECIPE_ID,
    KREA2_MOODY_MIX_V7_CREATOR_TERM,
    KREA2_MOODY_MIX_V7_RECIPE_GRAPH,
    KREA2_MOODY_MIX_V7_RECIPE_ID,
    KREA2_REVIEW_TERM,
    PONPOKE_FLUX2_KLEIN4B_TERM,
    PONPOKE_FLUX2_KLEIN9B_TERM,
    PORNMASTER_V4_RECIPE_GRAPH,
    accept_host_term,
)
from services.model_terms import (  # noqa: E402
    MODEL_TERM_DOCUMENTS,
    ModelTermsContractError,
    ModelTermsRequiredError,
    PORNMASTER_V4_PONPOKE_RECIPE,
    PORNMASTER_V4_REQUIRED_TERMS,
    model_availability_policy,
    model_terms_manifest_valid,
    model_terms_status,
    model_terms_statuses,
    required_model_term,
    required_model_terms,
    require_model_terms,
)


def _definitions(*names: str) -> dict[str, dict]:
    definitions = {}
    for name in names:
        with (APP_ROOT / "defaults" / f"{name}.json").open(
            "r", encoding="utf-8",
        ) as handle:
            definitions[name] = json.load(handle)["model"]
    return definitions


class ImageRecipeTermTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = _definitions(
            "flux2_dev",
            "flux2_dev_nvfp4",
            "pi_flux2",
            "flux2_klein_4b",
            "flux2_klein_4b_uncensored",
            "flux2_klein_9b",
            "flux2_klein_9b_uncensored",
            PORNMASTER_V4_PONPOKE_RECIPE,
            "flux_dev_kontext",
            "flux_dev_kontext_dreamomni2",
            "flux_krea",
            "krea2_raw",
            "krea2_raw_edit",
            "krea2_turbo",
            "krea2_turbo_edit",
            KREA2_MOODY_MIX_V7_RECIPE_ID,
            KREA2_MOODY_CUTIE_V4_RECIPE_ID,
            "qwen_image_edit_2511_nsfw",
        )

    def test_flux2_dev_and_klein9b_derivatives_inherit_bfl_gate(self):
        for model_type in (
            "flux2_dev",
            "flux2_dev_nvfp4",
            "pi_flux2",
            "flux2_klein_9b",
        ):
            with self.subTest(model_type=model_type):
                self.assertEqual(
                    required_model_term(model_type, self.definitions),
                    BFL_FLUX2_REVIEW_TERM,
                )

        self.assertEqual(
            required_model_term("flux2_klein_base_9b", self.definitions),
            BFL_FLUX2_REVIEW_TERM,
        )

    def test_ponpoke_encoder_terms_are_exact_and_base_order_is_stable(self):
        self.assertEqual(
            required_model_terms("flux2_klein_4b_uncensored", self.definitions),
            (PONPOKE_FLUX2_KLEIN4B_TERM,),
        )
        self.assertEqual(
            required_model_terms("flux2_klein_9b_uncensored", self.definitions),
            (BFL_FLUX2_REVIEW_TERM, PONPOKE_FLUX2_KLEIN9B_TERM),
        )
        derived = dict(self.definitions)
        derived["future_4b_encoder_alias"] = {
            "URLs": "flux2_klein_4b_uncensored",
        }
        derived["future_9b_encoder_alias"] = {
            "capability_recipe": {
                "base_model": "flux2_klein_9b_uncensored",
            },
        }
        self.assertEqual(
            required_model_terms("future_4b_encoder_alias", derived),
            (PONPOKE_FLUX2_KLEIN4B_TERM,),
        )
        self.assertEqual(
            required_model_terms("future_9b_encoder_alias", derived),
            (BFL_FLUX2_REVIEW_TERM, PONPOKE_FLUX2_KLEIN9B_TERM),
        )

    def test_pornmaster_creator_base_and_encoder_terms_are_cumulative(self):
        self.assertEqual(
            required_model_terms(
                PORNMASTER_V4_PONPOKE_RECIPE, self.definitions,
            ),
            PORNMASTER_V4_REQUIRED_TERMS,
        )
        self.assertEqual(PORNMASTER_V4_REQUIRED_TERMS, (
            CIVITAI_PORNMASTER_V4_CREATOR_TERM,
            BFL_FLUX2_REVIEW_TERM,
            PONPOKE_FLUX2_KLEIN9B_TERM,
        ))
        self.assertTrue(model_terms_manifest_valid(
            PORNMASTER_V4_PONPOKE_RECIPE,
            self.definitions,
        ))
        self.assertEqual(
            [item["term"] for item in model_terms_statuses(
                {}, PORNMASTER_V4_PONPOKE_RECIPE, self.definitions,
            )],
            list(PORNMASTER_V4_REQUIRED_TERMS),
        )

    def test_manual_availability_inherits_only_through_declared_relations(self):
        definitions = copy.deepcopy(self.definitions)
        definitions["manual_alias"] = {
            "URLs": PORNMASTER_V4_PONPOKE_RECIPE,
        }
        definitions["manual_composite"] = {
            "modules": ["manual_alias"],
        }
        expected = {
            "downloadable": False,
            "manual_installation_ready": True,
            "availability_status": "experimental_manual_installation",
        }
        self.assertEqual(
            model_availability_policy("manual_alias", definitions),
            expected,
        )
        self.assertEqual(
            model_availability_policy("manual_composite", definitions),
            expected,
        )

        # Owner-imported artifacts remain generic unless their definition
        # explicitly declares a server recipe relation or installation policy.
        definitions["owner_civitai_import"] = {
            "architecture": "flux2_klein_9b",
            "URLs": ["pornmasterFlux2Klein_v4TurboFp8.safetensors"],
            "civitai": {"modelId": 2382648, "versionId": 2973304},
            "tags": ["manual_hash_verified_only", "pornmaster"],
        }
        definitions["owner_hf_import"] = {
            "architecture": "flux2_klein_9b",
            "URLs": ["https://huggingface.co/owner/repo/model.safetensors"],
        }
        for model_type in ("owner_civitai_import", "owner_hf_import"):
            with self.subTest(model_type=model_type):
                self.assertEqual(model_availability_policy(
                    model_type, definitions,
                ), {
                    "downloadable": True,
                    "manual_installation_ready": False,
                    "availability_status": "available",
                })

    def test_pornmaster_manifest_mismatch_fails_closed_even_when_accepted(self):
        services = {}
        for term in PORNMASTER_V4_REQUIRED_TERMS:
            accept_host_term(
                services, term, 1,
                accepted_at="2026-08-10T00:00:00Z",
            )
        require_model_terms(
            services, PORNMASTER_V4_PONPOKE_RECIPE, self.definitions,
        )

        stale = dict(self.definitions)
        stale_recipe = json.loads(json.dumps(
            stale[PORNMASTER_V4_PONPOKE_RECIPE],
        ))
        stale_recipe["artifact_provenance"]["checkpoint"]["version_id"] = 0
        stale[PORNMASTER_V4_PONPOKE_RECIPE] = stale_recipe
        self.assertFalse(model_terms_manifest_valid(
            PORNMASTER_V4_PONPOKE_RECIPE, stale,
        ))
        stale["pornmaster_alias"] = {
            "capability_recipe": {
                "base_model": PORNMASTER_V4_PONPOKE_RECIPE,
            },
        }
        self.assertFalse(model_terms_manifest_valid(
            "pornmaster_alias", stale,
        ))
        with self.assertRaises(ModelTermsContractError):
            require_model_terms(
                services, "pornmaster_alias", stale,
            )

    def test_pornmaster_executable_graph_is_part_of_creator_binding(self):
        mutations = {
            "architecture": lambda recipe: recipe.__setitem__(
                "architecture", "other",
            ),
            "checkpoint URL": lambda recipe: recipe.__setitem__(
                "URLs", ["other.safetensors"],
            ),
            "encoder URL": lambda recipe: recipe.__setitem__(
                "text_encoder_URLs", ["other.safetensors"],
            ),
            "base relation": lambda recipe: recipe[
                "capability_recipe"
            ].__setitem__("base_model", "flux2_klein_4b"),
            "byte size": lambda recipe: recipe[
                "artifact_provenance"
            ]["checkpoint"].__setitem__("size_bytes", 0),
            "auto route": lambda recipe: recipe.__setitem__(
                "automatic_routing", True,
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                definitions = json.loads(json.dumps(self.definitions))
                mutate(definitions[PORNMASTER_V4_PONPOKE_RECIPE])
                self.assertFalse(model_terms_manifest_valid(
                    PORNMASTER_V4_PONPOKE_RECIPE, definitions,
                ))

    def test_pornmaster_term_bindings_cross_check_the_full_recipe_graph(self):
        mutations = {
            "creator byte size": (
                CIVITAI_PORNMASTER_V4_CREATOR_TERM,
                lambda binding: binding.__setitem__("file_size_bytes", 1),
            ),
            "creator revision": (
                CIVITAI_PORNMASTER_V4_CREATOR_TERM,
                lambda binding: binding.__setitem__("revision", "stale"),
            ),
            "creator graph": (
                CIVITAI_PORNMASTER_V4_CREATOR_TERM,
                lambda binding: binding["recipe_graph"]["checkpoint"].__setitem__(
                    "size_bytes", 1,
                ),
            ),
            "base revision": (
                BFL_FLUX2_REVIEW_TERM,
                lambda binding: binding["covered_repositories"][1].__setitem__(
                    "revision", "stale",
                ),
            ),
            "base license revision": (
                BFL_FLUX2_REVIEW_TERM,
                lambda binding: binding.__setitem__("revision", "stale"),
            ),
            "encoder revision": (
                PONPOKE_FLUX2_KLEIN9B_TERM,
                lambda binding: binding.__setitem__("revision", "stale"),
            ),
            "encoder license": (
                PONPOKE_FLUX2_KLEIN9B_TERM,
                lambda binding: binding.__setitem__("license_id", "stale"),
            ),
        }
        for label, (term, mutate) in mutations.items():
            with self.subTest(label=label):
                replacement = copy.deepcopy(CURRENT_HOST_TERM_BINDINGS[term])
                mutate(replacement)
                with patch.dict(
                    CURRENT_HOST_TERM_BINDINGS,
                    {term: replacement},
                    clear=False,
                ):
                    self.assertFalse(model_terms_manifest_valid(
                        PORNMASTER_V4_PONPOKE_RECIPE,
                        self.definitions,
                    ))

        creator_graph = CURRENT_HOST_TERM_BINDINGS[
            CIVITAI_PORNMASTER_V4_CREATOR_TERM
        ]["recipe_graph"]
        self.assertEqual(creator_graph, PORNMASTER_V4_RECIPE_GRAPH)
        with patch.dict(
            CURRENT_HOST_TERM_VERSIONS,
            {CIVITAI_PORNMASTER_V4_CREATOR_TERM: 2},
            clear=False,
        ):
            self.assertFalse(model_terms_manifest_valid(
                PORNMASTER_V4_PONPOKE_RECIPE,
                self.definitions,
            ))

    def test_pornmaster_requires_each_acceptance_before_use(self):
        services = {}
        for expected in PORNMASTER_V4_REQUIRED_TERMS:
            with self.assertRaises(ModelTermsRequiredError) as raised:
                require_model_terms(
                    services, PORNMASTER_V4_PONPOKE_RECIPE,
                    self.definitions,
                )
            self.assertEqual(raised.exception.term, expected)
            accept_host_term(
                services, expected, 1,
                accepted_at="2026-08-10T00:00:00Z",
            )
        require_model_terms(
            services, PORNMASTER_V4_PONPOKE_RECIPE, self.definitions,
        )

    def test_text_encoder_aliases_and_composites_inherit_all_terms(self):
        definitions = dict(self.definitions)
        definitions["encoder_alias"] = {
            "text_encoder_URLs": "flux2_klein_4b_uncensored",
        }
        definitions["encoder_composite"] = {
            "text_encoder_URLs": [
                "encoder_alias",
                "flux2_klein_9b_uncensored",
            ],
        }
        self.assertEqual(
            required_model_terms("encoder_alias", definitions),
            (PONPOKE_FLUX2_KLEIN4B_TERM,),
        )
        self.assertEqual(
            required_model_terms("encoder_composite", definitions),
            (
                PONPOKE_FLUX2_KLEIN4B_TERM,
                BFL_FLUX2_REVIEW_TERM,
                PONPOKE_FLUX2_KLEIN9B_TERM,
            ),
        )

    def test_flux1_krea_kontext_and_declared_derivatives_inherit(self):
        derived = dict(self.definitions)
        derived["custom_krea_recipe"] = {
            "architecture": "flux",
            "URLs": "flux_krea",
        }
        for model_type in (
            "flux_krea",
            "flux_dev_kontext",
            "flux_dev_kontext_dreamomni2",
            "custom_krea_recipe",
        ):
            with self.subTest(model_type=model_type):
                self.assertEqual(
                    required_model_term(model_type, derived),
                    BFL_FLUX1_REVIEW_TERM,
                )

    def test_krea2_edit_aliases_inherit_one_community_license_gate(self):
        for model_type in (
            "krea2_raw",
            "krea2_raw_edit",
            "krea2_turbo",
            "krea2_turbo_edit",
        ):
            with self.subTest(model_type=model_type):
                self.assertEqual(
                    required_model_term(model_type, self.definitions),
                    KREA2_REVIEW_TERM,
                )

    def test_moody_krea_creator_and_base_terms_are_exact_and_cumulative(self):
        expected = {
            KREA2_MOODY_MIX_V7_RECIPE_ID: (
                KREA2_MOODY_MIX_V7_CREATOR_TERM,
                KREA2_MOODY_MIX_V7_RECIPE_GRAPH,
            ),
            KREA2_MOODY_CUTIE_V4_RECIPE_ID: (
                KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
                KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH,
            ),
        }
        for recipe_id, (creator_term, graph) in expected.items():
            with self.subTest(recipe_id=recipe_id):
                model = self.definitions[recipe_id]
                required = (creator_term, KREA2_REVIEW_TERM)
                self.assertEqual(required_model_terms(
                    recipe_id, self.definitions,
                ), required)
                self.assertTrue(model_terms_manifest_valid(
                    recipe_id, self.definitions,
                ))
                self.assertEqual(
                    [item["term"] for item in model_terms_statuses(
                        {}, recipe_id, self.definitions,
                    )],
                    list(required),
                )
                checkpoint = model["artifact_provenance"]["checkpoint"]
                self.assertEqual(checkpoint["title"], graph["display_name"])
                self.assertEqual(
                    checkpoint["download_url"],
                    graph["checkpoint"]["download_url"],
                )
                self.assertTrue(checkpoint["download_url"].endswith(
                    "?type=Diffusion%20Model&format=SafeTensor&fp=fp8"
                ))
                self.assertEqual(model["capability_recipe"]["operations"], [
                    "generation",
                ])
                self.assertFalse(model["revenue_eligible"])
                self.assertFalse(model["fine_tuning_eligible"])
                self.assertFalse(model["derivative_tooling"])
                self.assertFalse(model["automatic_routing"])
                self.assertEqual(model["default_for_operations"], [])
                self.assertTrue(model["visible"])
                self.assertIn("Broad-capability", model["name"])
                self.assertIn(
                    "creator-described as uncensored; effectiveness not yet benchmarked",
                    model["selector_help"],
                )

    def test_moody_manifest_drift_and_missing_alias_target_fail_closed(self):
        for recipe_id, creator_term in (
            (
                KREA2_MOODY_MIX_V7_RECIPE_ID,
                KREA2_MOODY_MIX_V7_CREATOR_TERM,
            ),
            (
                KREA2_MOODY_CUTIE_V4_RECIPE_ID,
                KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
            ),
        ):
            with self.subTest(recipe_id=recipe_id):
                alias_id = f"{recipe_id}_alias"
                valid_alias = dict(self.definitions)
                valid_alias[alias_id] = {"URLs": recipe_id}
                self.assertEqual(
                    required_model_terms(alias_id, valid_alias),
                    (creator_term, KREA2_REVIEW_TERM),
                )
                self.assertTrue(model_terms_manifest_valid(
                    alias_id, valid_alias,
                ))
                self.assertEqual(
                    model_availability_policy(alias_id, valid_alias),
                    {
                        "downloadable": False,
                        "manual_installation_ready": True,
                        "availability_status": (
                            "experimental_manual_installation"
                        ),
                    },
                )

                missing = {alias_id: {"URLs": recipe_id}}
                self.assertEqual(
                    required_model_terms(alias_id, missing),
                    (creator_term, KREA2_REVIEW_TERM),
                )
                self.assertFalse(model_terms_manifest_valid(alias_id, missing))
                self.assertEqual(
                    model_availability_policy(alias_id, missing),
                    {
                        "downloadable": False,
                        "manual_installation_ready": False,
                        "availability_status": (
                            "manual_installation_contract_unavailable"
                        ),
                    },
                )
                services = {}
                accept_host_term(
                    services, creator_term, 1,
                    accepted_at="2026-08-10T00:00:00Z",
                )
                accept_host_term(
                    services, KREA2_REVIEW_TERM, 2,
                    accepted_at="2026-08-10T00:00:00Z",
                )
                with self.assertRaises(ModelTermsContractError):
                    require_model_terms(services, alias_id, missing)

                for path, replacement in (
                    (("artifact_provenance", "checkpoint", "model_id"), 0),
                    (("artifact_provenance", "checkpoint", "version_id"), 0),
                    (("artifact_provenance", "checkpoint", "file_id"), 0),
                    (("artifact_provenance", "checkpoint", "download_url"), "stale"),
                    (("artifact_provenance", "checkpoint", "sha256"), "0" * 64),
                    (("capability_recipe", "operations"), ["generation", "editing"]),
                    (("revenue_eligible",), True),
                    (("automatic_routing",), True),
                ):
                    drifted = copy.deepcopy(self.definitions)
                    target = drifted[recipe_id]
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = replacement
                    self.assertFalse(model_terms_manifest_valid(
                        recipe_id, drifted,
                    ))

    def test_moody_and_bypass_lookalike_imports_remain_generic(self):
        for import_id in (
            "2728234", "3067151", "2746817", "3089754",
            "owner_moody_civitai_import", "owner_moody_hf_import",
        ):
            with self.subTest(import_id=import_id):
                model = {
                    "architecture": "krea2_raw",
                    "URLs": [
                        "https://huggingface.co/owner/repo/model.safetensors"
                    ],
                    "civitai": {
                        "modelId": 2728234,
                        "versionId": 3067151,
                    },
                    "tags": ["moody", "uncensored", "fp8"],
                }
                definitions = {import_id: model}
                self.assertEqual(required_model_terms(
                    import_id, definitions,
                ), ())
                self.assertTrue(model_terms_manifest_valid(
                    import_id, definitions,
                ))
                self.assertEqual(
                    model_availability_policy(import_id, definitions),
                    {
                        "downloadable": True,
                        "manual_installation_ready": False,
                        "availability_status": "available",
                    },
                )

    def test_moody_candidates_are_not_default_enabled_or_auto_routed(self):
        store = (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(
            encoding="utf-8",
        )
        default_enabled = store.split(
            "const DEFAULT_ENABLED_MODELS = new Set([", 1,
        )[1].split("])\n", 1)[0]
        migrations = store.split(
            "const DEFAULTS_ADDED_IN:", 1,
        )[1].split("\n}\n", 1)[0]
        mode_defaults = store.split(
            "const modeDefaultModel:", 1,
        )[1].split("export function getFamilyMode", 1)[0]
        routing_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                APP_ROOT / "services" / "director_model_compat.py",
                APP_ROOT / "services" / "reference_sheets.py",
            )
        )
        for recipe_id in (
            KREA2_MOODY_MIX_V7_RECIPE_ID,
            KREA2_MOODY_CUTIE_V4_RECIPE_ID,
        ):
            self.assertNotIn(recipe_id, default_enabled)
            self.assertNotIn(recipe_id, migrations)
            self.assertNotIn(recipe_id, mode_defaults)
            self.assertNotIn(recipe_id, routing_sources)
        for bypass_id in ("2728234", "3067151", "2746817", "3089754"):
            self.assertNotIn(bypass_id, default_enabled)
            self.assertNotIn(bypass_id, migrations)
            self.assertNotIn(bypass_id, mode_defaults)
            self.assertNotIn(bypass_id, routing_sources)

    def test_apache_klein4b_and_openrail_qwen_have_no_extra_gate(self):
        for model_type in (
            "flux2_klein_4b",
            "qwen_image_edit_2511_nsfw",
        ):
            with self.subTest(model_type=model_type):
                self.assertIsNone(
                    required_model_term(model_type, self.definitions),
                )

    def test_unknown_corrupt_and_cyclic_recipes_do_not_invent_terms(self):
        definitions = {
            "a": {"URLs": "b"},
            "b": {"URLs": "a"},
            "broken": {"URLs": [None, 3, {"base": "flux2_dev"}]},
        }
        self.assertIsNone(required_model_term("missing", definitions))
        self.assertIsNone(required_model_term("a", definitions))
        self.assertIsNone(required_model_term("broken", definitions))

    def test_composite_recipe_accumulates_each_declared_branch_term(self):
        definitions = dict(self.definitions)
        definitions["composite"] = {
            "architecture": "custom",
            "URLs": "flux2_dev",
            "modules": ["krea2_raw"],
        }
        self.assertEqual(
            required_model_terms("composite", definitions),
            (BFL_FLUX2_REVIEW_TERM, KREA2_REVIEW_TERM),
        )

    def test_future_derivatives_can_declare_exact_server_owned_terms(self):
        definitions = dict(self.definitions)
        definitions["future_klein9_derivative"] = {
            "architecture": "future_transformer",
            "required_host_terms": [BFL_FLUX2_REVIEW_TERM],
        }
        definitions["future_krea_checkpoint"] = {
            "architecture": "future_transformer",
            "required_host_terms": KREA2_REVIEW_TERM,
        }
        definitions["future_composite"] = {
            "URLs": "future_klein9_derivative",
            "modules": ["future_krea_checkpoint"],
        }
        self.assertEqual(
            required_model_terms("future_composite", definitions),
            (BFL_FLUX2_REVIEW_TERM, KREA2_REVIEW_TERM),
        )

        definitions["unverified_candidate"] = {
            "architecture": "flux2_klein_9b",
            "URLs": ["https://example.invalid/model.safetensors"],
        }
        self.assertEqual(required_model_terms("unverified_candidate", definitions), ())

    def test_public_documents_use_exact_review_sources(self):
        self.assertEqual(
            MODEL_TERM_DOCUMENTS[KREA2_REVIEW_TERM]["license_url"],
            (
                "https://huggingface.co/krea/Krea-2-Turbo/"
                "blob/98e0fe1/README.md"
            ),
        )
        self.assertIn(
            "/FLUX.1-dev/blob/3de623f/LICENSE.md",
            MODEL_TERM_DOCUMENTS[BFL_FLUX1_REVIEW_TERM]["license_url"],
        )
        self.assertIn(
            "/FLUX.2-dev/blob/0cb56aa/LICENSE.md",
            MODEL_TERM_DOCUMENTS[BFL_FLUX2_REVIEW_TERM]["license_url"],
        )
        self.assertIn(
            "633217e588e4c0bc76619052e05d3ce0e057cd83/README.md",
            MODEL_TERM_DOCUMENTS[PONPOKE_FLUX2_KLEIN4B_TERM]["license_url"],
        )
        self.assertIn(
            "fba36e796aac081246708dd30392a401ba44922e/README.md",
            MODEL_TERM_DOCUMENTS[PONPOKE_FLUX2_KLEIN9B_TERM]["license_url"],
        )
        notices = " ".join(
            document["notice"] for document in MODEL_TERM_DOCUMENTS.values()
        )
        self.assertIn("local fidelity QA", notices)
        self.assertIn("it is not moderation", notices)
        self.assertIn("does not decide permissibility", notices)
        krea_notice = MODEL_TERM_DOCUMENTS[KREA2_REVIEW_TERM]["notice"]
        self.assertIn(
            "broad-capability research, evaluation, and fine-tune development",
            krea_notice,
        )
        self.assertIn("not automatically circumvention", krea_notice)
        self.assertIn(
            "explicitly designed to defeat safety filters", krea_notice,
        )
        self.assertIn("excluded from Maestro's curated routing", krea_notice)
        self.assertIn("Acceptable Use Policy", krea_notice)
        self.assertIn("required human review", krea_notice)
        for creator_term, source_url in (
            (
                KREA2_MOODY_MIX_V7_CREATOR_TERM,
                "https://civitai.com/models/2731187?modelVersionId=3209007",
            ),
            (
                KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
                "https://civitai.com/models/2764429?modelVersionId=3211049",
            ),
        ):
            creator_notice = MODEL_TERM_DOCUMENTS[creator_term]
            self.assertEqual(creator_notice["license_url"], source_url)
            self.assertIn("credit is required", creator_notice["notice"])
            self.assertIn("derivatives are forbidden", creator_notice["notice"])
            self.assertIn("limited to RentCivit", creator_notice["notice"])
            self.assertIn(
                "does not permit Moody derivatives or derivative tooling",
                creator_notice["notice"],
            )

    def test_gate_is_fail_closed_and_acceptance_is_host_wide(self):
        services = {}
        with self.assertRaises(ModelTermsRequiredError) as raised:
            require_model_terms(services, "krea2_turbo_edit", self.definitions)
        self.assertEqual(raised.exception.term, KREA2_REVIEW_TERM)
        self.assertNotIn("prompt", str(raised.exception).lower())
        accept_host_term(
            services,
            KREA2_REVIEW_TERM,
            2,
            accepted_at="2026-08-10T00:00:00Z",
        )
        require_model_terms(services, "krea2_turbo_edit", self.definitions)
        status = model_terms_status(
            services, "krea2_raw", self.definitions,
        )
        self.assertIsNotNone(status)
        self.assertTrue(status["accepted"])
        self.assertEqual(status["review_mode"], "manual_self_review")


class ImageRecipeGateBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch_source = (APP_ROOT / "launch.py").read_text(encoding="utf-8")
        cls.launch_module = ast.parse(cls.launch_source)
        cls.wgp_source = (APP_ROOT / "wgp.py").read_text(encoding="utf-8")
        cls.wgp_module = ast.parse(cls.wgp_source)

    @staticmethod
    def _function_source(module, source: str, name: str) -> str:
        node = next(
            item for item in module.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        return ast.get_source_segment(source, node) or ""

    def test_shared_loader_rechecks_before_state_change_or_download_resolution(self):
        source = self._function_source(self.wgp_module, self.wgp_source, "load_models")
        self.assertLess(source.index("require_model_terms("), source.index("_invalidate_loaded_model_state()"))
        self.assertLess(source.index("require_model_terms("), source.index("get_model_filename("))

    def test_manual_readiness_requires_cached_integrity_before_filename_checks(self):
        source = self._function_source(
            self.launch_module, self.launch_source, "_check_model_downloaded",
        )
        self.assertIn('"manual_checkpoint_integrity_required"', source)
        self.assertIn('"manual_checkpoint_integrity_ready"', source)
        self.assertLess(
            source.index("manual_required("),
            source.index("_model_weight_groups("),
        )
        self.assertLess(
            source.index("manual_ready("),
            source.index("_model_weight_groups("),
        )

        node = copy.deepcopy(next(
            item for item in self.launch_module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_check_model_downloaded"
        ))
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        calls = []
        definitions = {"manual-alias": {"URLs": PORNMASTER_V4_PONPOKE_RECIPE}}
        namespace = {
            "wgp": type("FakeWgp", (), {
                "models_def": definitions,
                "get_model_def": staticmethod(definitions.get),
                "manual_checkpoint_integrity_required": staticmethod(
                    lambda *_args: calls.append("required") or True
                ),
                "manual_checkpoint_integrity_ready": staticmethod(
                    lambda *_args: calls.append("ready") or False
                ),
            })(),
            "_model_weight_groups": lambda *_args: calls.append("basename"),
        }
        exec(compile(module, "manual-readiness", "exec"), namespace)
        self.assertFalse(namespace["_check_model_downloaded"]("manual-alias"))
        self.assertEqual(calls, ["required", "ready"])

    def test_wgp_krea_contracts_match_defaults_and_preserve_generic_imports(self):
        assignment = next(
            node for node in self.wgp_module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_MANUAL_CHECKPOINT_INTEGRITY_CONTRACTS"
                for target in node.targets
            )
        )
        contracts = eval(  # noqa: S307 - fixed repository-owned AST literal
            compile(
                ast.fix_missing_locations(ast.Expression(assignment.value)),
                "manual-contracts",
                "eval",
            ),
            {"PORNMASTER_V4_PONPOKE_RECIPE": PORNMASTER_V4_PONPOKE_RECIPE},
            {},
        )
        krea_contracts = {
            key: value for key, value in contracts.items()
            if key.startswith("krea2_moody_")
        }
        self.assertEqual(set(krea_contracts), {
            KREA2_MOODY_MIX_V7_RECIPE_ID,
            KREA2_MOODY_CUTIE_V4_RECIPE_ID,
        })
        for recipe_id, contract in krea_contracts.items():
            checkpoint = _definitions(recipe_id)[recipe_id][
                "artifact_provenance"
            ]["checkpoint"]
            for field in (
                "provider", "model_id", "version_id", "file_id", "filename",
                "size_bytes", "download_url",
            ):
                self.assertEqual(checkpoint[field], contract[field])
            self.assertEqual(checkpoint["sha256"].lower(), contract["sha256"])

        wanted = {
            "_manual_checkpoint_integrity_spec",
            "manual_checkpoint_integrity_required",
        }
        nodes = [
            copy.deepcopy(node)
            for node in self.wgp_module.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            "_MANUAL_CHECKPOINT_INTEGRITY_CONTRACTS": contracts,
        }
        exec(compile(module, "manual-krea-contracts", "exec"), namespace)
        required = namespace["manual_checkpoint_integrity_required"]
        spec = namespace["_manual_checkpoint_integrity_spec"]
        definitions = _definitions(
            KREA2_MOODY_MIX_V7_RECIPE_ID,
            KREA2_MOODY_CUTIE_V4_RECIPE_ID,
        )
        for recipe_id in krea_contracts:
            model = definitions[recipe_id]
            self.assertTrue(required(recipe_id, model, definitions))
            resolved = spec(recipe_id, model, definitions)
            self.assertEqual(resolved.pop("recipe_id"), recipe_id)
            self.assertEqual(resolved, contracts[recipe_id])
            alias_id = f"{recipe_id}_alias"
            alias = {"URLs": recipe_id}
            aliased = {**definitions, alias_id: alias}
            self.assertTrue(required(alias_id, alias, aliased))
            resolved_alias = spec(alias_id, alias, aliased)
            self.assertEqual(resolved_alias.pop("recipe_id"), recipe_id)
            self.assertEqual(resolved_alias, contracts[recipe_id])
            self.assertTrue(required(alias_id, alias, {alias_id: alias}))
            with self.assertRaisesRegex(RuntimeError, "contract is invalid"):
                spec(alias_id, alias, {alias_id: alias})

        for import_id in (
            "2728234", "3067151", "2746817", "3089754",
            "owner_moody_civitai_import", "owner_moody_hf_import",
        ):
            ordinary = {
                "architecture": "krea2_raw",
                "URLs": [
                    "https://huggingface.co/owner/moody/model.safetensors"
                ],
                "civitai": {"modelId": 2728234, "versionId": 3067151},
            }
            ordinary_defs = {import_id: ordinary}
            self.assertFalse(required(import_id, ordinary, ordinary_defs))
            self.assertIsNone(spec(import_id, ordinary, ordinary_defs))

    def test_catalog_hides_invalid_manifest_before_update_and_exports_terms(self):
        source = self._function_source(
            self.launch_module, self.launch_source, "list_models",
        )
        self.assertLess(
            source.index("model_terms_manifest_valid(mt, wgp.models_def)"),
            source.index("_versioned_model_updater.apply_recorded(mt, md)"),
        )
        self.assertIn('"required_host_terms"', source)
        self.assertIn("model_terms_statuses({}, mt, wgp.models_def)", source)
        self.assertIn("**model_availability_policy(mt, wgp.models_def)", source)
        self.assertIn('"manual_checkpoint_verification_required"', source)
        self.assertIn('"manual_checkpoint_verified"', source)
        self.assertIn('"supported_operations"', source)
        self.assertIn('"automatic_routing"', source)
        self.assertIn('"default_for_operations"', source)
        self.assertIn('"revenue_eligible"', source)
        self.assertIn('"fine_tuning_eligible"', source)
        self.assertIn('"derivative_tooling"', source)

    def test_manual_only_download_policy_is_executable_before_side_effects(self):
        names = {
            "ModelDownloadUnavailableError",
            "_public_model_availability",
            "_require_model_download_available",
            "_download_model_files",
            "download_model",
        }
        nodes = [
            copy.deepcopy(node)
            for node in self.launch_module.body
            if (
                isinstance(node, ast.ClassDef)
                and node.name in names
            ) or (
                isinstance(node, ast.FunctionDef)
                and node.name in names
            )
        ]
        for node in nodes:
            if isinstance(node, ast.FunctionDef):
                node.decorator_list = []
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        side_effects = []
        model_def = {
            "downloadable": False,
            "manual_installation_ready": True,
            "availability_status": "experimental_manual_installation",
        }
        definitions = {PORNMASTER_V4_PONPOKE_RECIPE: model_def}
        namespace = {
            "Request": object,
            "wgp": type("FakeWgp", (), {
                "get_model_def": staticmethod(lambda _model_type: model_def),
                "models_def": definitions,
            })(),
            "_require_h3_legal_execution": lambda model_types: (
                side_effects.append(("legal", tuple(model_types)))
            ),
            "_require_model_recipe_terms": lambda model_types: side_effects.append(
                ("terms", tuple(model_types)),
            ),
            "_ensure_versioned_model_current": lambda *_args, **_kwargs: (
                side_effects.append(("updater",))
            ),
        }
        exec(compile(module, "manual-download-policy", "exec"), namespace)

        self.assertEqual(namespace["_public_model_availability"](
            PORNMASTER_V4_PONPOKE_RECIPE,
            model_def,
            definitions,
        ), {
            "downloadable": False,
            "manual_installation_ready": True,
            "availability_status": "experimental_manual_installation",
        })
        with self.assertRaises(namespace["ModelDownloadUnavailableError"]):
            namespace["_download_model_files"](
                PORNMASTER_V4_PONPOKE_RECIPE,
            )
        self.assertEqual(side_effects, [
            ("legal", (PORNMASTER_V4_PONPOKE_RECIPE,)),
            ("terms", (PORNMASTER_V4_PONPOKE_RECIPE,)),
        ])

        alias_id = "manual_alias"
        alias_definitions = {
            alias_id: {"URLs": PORNMASTER_V4_PONPOKE_RECIPE},
            PORNMASTER_V4_PONPOKE_RECIPE: model_def,
        }
        namespace["wgp"].models_def = alias_definitions
        namespace["wgp"].get_model_def = alias_definitions.get
        side_effects.clear()
        with self.assertRaises(namespace["ModelDownloadUnavailableError"]):
            namespace["_download_model_files"](alias_id)
        self.assertEqual(side_effects, [
            ("legal", (alias_id,)),
            ("terms", (alias_id,)),
        ])

        # Removing the registered target cannot turn its exact alias into a
        # generic downloadable model or reach updater/network resolution.
        missing_target_definitions = {
            alias_id: {"URLs": PORNMASTER_V4_PONPOKE_RECIPE},
        }
        namespace["wgp"].models_def = missing_target_definitions
        namespace["wgp"].get_model_def = missing_target_definitions.get
        side_effects.clear()
        with self.assertRaises(namespace["ModelDownloadUnavailableError"]):
            namespace["_download_model_files"](alias_id)
        self.assertEqual(side_effects, [
            ("legal", (alias_id,)),
            ("terms", (alias_id,)),
        ])

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        namespace["wgp"].models_def = definitions
        namespace["wgp"].get_model_def = definitions.get
        thread_calls = []
        namespace.update({
            "HTTPException": FakeHTTPException,
            "threading": type("FakeThreading", (), {
                "Thread": staticmethod(
                    lambda *_args, **_kwargs: thread_calls.append("thread")
                ),
            })(),
            "_workspace_lifecycle_lock": __import__("threading").RLock(),
            "_model_downloads_lock": __import__("threading").Lock(),
            "_model_downloads": {},
            "_request_project_workspace": lambda *_args: "default",
            "_require_host_terms_project_access": lambda *_args: None,
            "_require_remote_visible_models": lambda *_args: None,
        })
        request = type("FakeRequest", (), {})()
        with self.assertRaises(FakeHTTPException) as raised:
            namespace["download_model"](
                PORNMASTER_V4_PONPOKE_RECIPE,
                request,
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(namespace["_model_downloads"], {})
        self.assertEqual(thread_calls, [])

    def test_manual_checkpoint_verification_is_explicit_local_and_side_effect_free(self):
        wanted = {"_public_model_availability", "verify_manual_checkpoint"}
        nodes = [
            copy.deepcopy(node)
            for node in self.launch_module.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        for node in nodes:
            node.decorator_list = []
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        root = {
            "downloadable": False,
            "manual_installation_ready": True,
            "availability_status": "experimental_manual_installation",
        }
        alias = {"URLs": PORNMASTER_V4_PONPOKE_RECIPE}
        definitions = {
            PORNMASTER_V4_PONPOKE_RECIPE: root,
            "manual_alias": alias,
        }
        calls = []
        wgp = type("FakeWgp", (), {
            "models_def": definitions,
            "get_model_def": staticmethod(definitions.get),
            "manual_checkpoint_integrity_required": staticmethod(
                lambda *_args: calls.append("required") or True
            ),
            "verify_manual_checkpoint_integrity": staticmethod(
                lambda *_args: calls.append("verify") or True
            ),
        })()
        namespace = {
            "Request": object,
            "HTTPException": FakeHTTPException,
            "wgp": wgp,
            "_request_is_cloudflare_remote": lambda request: request.remote,
            "_runtime_share_registration_is_local": lambda request: request.local,
            "_require_h3_legal_execution": lambda values: calls.append(
                ("legal", tuple(values)),
            ),
            "_require_model_recipe_terms": lambda values: calls.append(
                ("terms", tuple(values)),
            ),
            "_check_model_downloaded": lambda model_type: calls.append(
                ("ready", model_type),
            ) or False,
        }
        exec(compile(module, "manual-verification-route", "exec"), namespace)
        request = type("Request", (), {"remote": False, "local": True})()
        result = namespace["verify_manual_checkpoint"]("manual_alias", request)
        self.assertEqual(result, {
            "status": "verified",
            "model_type": "manual_alias",
            "manual_checkpoint_verified": True,
            "is_downloaded": False,
        })
        self.assertEqual(calls, [
            ("legal", ("manual_alias",)),
            ("terms", ("manual_alias",)),
            "required",
            "verify",
            ("ready", "manual_alias"),
        ])

        calls.clear()
        remote = type("Request", (), {"remote": True, "local": False})()
        with self.assertRaises(FakeHTTPException) as raised:
            namespace["verify_manual_checkpoint"]("manual_alias", remote)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(calls, [])

        catalog = self._function_source(
            self.launch_module, self.launch_source, "list_models",
        )
        self.assertNotIn("verify_manual_checkpoint_integrity(", catalog)

    def test_missing_registered_manual_alias_target_fails_closed(self):
        alias_id = "missing-manual-root-alias"
        definitions = {
            alias_id: {"URLs": PORNMASTER_V4_PONPOKE_RECIPE},
        }
        self.assertEqual(
            required_model_terms(alias_id, definitions),
            PORNMASTER_V4_REQUIRED_TERMS,
        )
        self.assertFalse(model_terms_manifest_valid(alias_id, definitions))
        self.assertEqual(
            model_availability_policy(alias_id, definitions),
            {
                "downloadable": False,
                "manual_installation_ready": False,
                "availability_status": (
                    "manual_installation_contract_unavailable"
                ),
            },
        )

        services = {
            "host_terms": {
                "acceptances": {
                    term: {
                        "accepted": True,
                        "version": CURRENT_HOST_TERM_VERSIONS[term],
                    }
                    for term in PORNMASTER_V4_REQUIRED_TERMS
                },
            },
        }
        with self.assertRaises(ModelTermsContractError):
            require_model_terms(services, alias_id, definitions)

    def test_recovery_scanner_includes_all_sealed_reference_operation_models(self):
        node = next(
            item for item in self.launch_module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_job_model_term_ids"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {}
        exec(compile(module, "model-term-job-scan", "exec"), namespace)
        scan = namespace["_job_model_term_ids"]
        self.assertEqual(
            scan({
                "params": {
                    "model_type": "requested-generation",
                    "reference_pack": {
                        "generation_model": "requested-generation",
                        "editor_model": "requested-editor",
                        "operation_routing": {
                            "operations": {
                                "generation": {
                                    "resolved_model": "resolved-generation",
                                },
                                "edit": {"resolved_model": "resolved-edit"},
                                "repair": {"resolved_model": "resolved-repair"},
                                "callout": {"resolved_model": "resolved-callout"},
                            },
                        },
                    },
                },
            }),
            [
                "requested-generation",
                "requested-editor",
                "resolved-generation",
                "resolved-edit",
                "resolved-repair",
                "resolved-callout",
            ],
        )
        self.assertEqual(
            scan({
                "params": {
                    "reference_pack": {
                        "generation_model": "requested-generation",
                        "operation_routing": [
                            {"resolved_model": "resolved-generation"},
                            {"resolved_model": "resolved-edit"},
                            {"resolved_model": "resolved-generation"},
                        ],
                    },
                },
            }),
            [
                "requested-generation",
                "resolved-generation",
                "resolved-edit",
            ],
        )

    def test_download_authority_and_terms_precede_mutation_thread_and_network(self):
        worker = self._function_source(
            self.launch_module, self.launch_source, "_download_model_files",
        )
        self.assertLess(worker.index("_require_model_recipe_terms("), worker.index("_ensure_versioned_model_current("))
        self.assertLess(worker.index("_require_model_recipe_terms("), worker.index("wgp.download_models("))
        self.assertLess(worker.index("_require_model_download_available("), worker.index("_ensure_versioned_model_current("))
        self.assertLess(worker.index("_require_model_download_available("), worker.index("wgp.download_models("))

        endpoint = self._function_source(
            self.launch_module, self.launch_source, "download_model",
        )
        admission = endpoint[endpoint.index("selected = _request_project_workspace"):]
        self.assertLess(admission.index("_require_host_terms_project_access("), admission.index("_require_remote_visible_models("))
        self.assertLess(admission.index("_require_remote_visible_models("), admission.index("_require_model_recipe_terms("))
        self.assertLess(admission.index("_require_model_recipe_terms("), admission.index("_model_downloads[model_type] ="))
        self.assertLess(admission.index("_require_model_recipe_terms("), admission.index("threading.Thread("))
        self.assertLess(admission.index("_require_model_download_available("), admission.index("_model_downloads[model_type] ="))
        self.assertLess(admission.index("_require_model_download_available("), admission.index("threading.Thread("))

    def test_queue_routes_and_worker_are_fail_closed_before_side_effects(self):
        registrar = self._function_source(
            self.launch_module, self.launch_source,
            "_queue_recovery_register_and_publish",
        )
        self.assertLess(registrar.index("_require_job_model_recipe_terms("), registrar.index("_jobs.prepare("))
        self.assertLess(registrar.index("_require_job_model_recipe_terms("), registrar.index("threading.Thread("))

        generic = self._function_source(
            self.launch_module, self.launch_source, "generate",
        )
        self.assertLess(generic.index("_require_project_access("), generic.index("_require_model_recipe_terms("))
        self.assertLess(generic.index("_require_model_recipe_terms("), generic.index("_authorize_generation_media_inputs("))

        reference = self._function_source(
            self.launch_module, self.launch_source,
            "generate_project_asset_references",
        )
        self.assertLess(reference.index("_asset_scope("), reference.index("_require_model_recipe_terms("))
        self.assertLess(reference.index("_require_model_recipe_terms("), reference.index("_begin_workspace_operation("))

        runtime = self._function_source(
            self.launch_module, self.launch_source, "_run_generation",
        )
        self.assertIn("_queue_recovery_delivery_pending(job) is None", runtime)
        self.assertLess(runtime.index("try_start("), runtime.index("_require_job_runtime_model_admission("))
        self.assertLess(runtime.index("_require_job_runtime_model_admission("), runtime.index("_apply_per_job_coefficient("))

    def test_recovery_admission_rechecks_before_queue_or_thread_mutation(self):
        startup = self._function_source(
            self.launch_module, self.launch_source,
            "_restore_queue_recovery_on_startup",
        )
        resumable_loop = startup[startup.index("for job in resumable:"):]
        self.assertIn(
            "if _queue_recovery_delivery_pending(job) is None:",
            resumable_loop,
        )
        self.assertLess(
            resumable_loop.index("_require_job_runtime_model_admission("),
            resumable_loop.index("threading.Thread("),
        )

        resume = self._function_source(
            self.launch_module, self.launch_source, "_resume_recovered_job",
        )
        self.assertIn(
            "if _queue_recovery_delivery_pending(job) is None:",
            resume,
        )
        self.assertLess(
            resume.index("_require_job_runtime_model_admission("),
            resume.index("next_recovery_attempt("),
        )
        self.assertLess(
            resume.index("_require_job_runtime_model_admission("),
            resume.index("threading.Thread("),
        )

        local = self._function_source(
            self.launch_module, self.launch_source,
            "start_local_h3_generation_recovery",
        )
        self.assertIn(
            "if _queue_recovery_delivery_pending(job) is None:",
            local,
        )
        self.assertLess(
            local.index("_require_job_runtime_model_admission("),
            local.index("next_recovery_attempt("),
        )
        self.assertLess(
            local.index("_require_job_runtime_model_admission("),
            local.index("threading.Thread("),
        )

        director_child = self._function_source(
            self.launch_module, self.launch_source,
            "_director_recovery_submit_child",
        )
        blocked_branch = director_child[
            director_child.index(
                'if str(existing.get("recovery_state") or "") in {'
            ):
        ]
        self.assertIn(
            "if _queue_recovery_delivery_pending(existing) is None:",
            blocked_branch,
        )
        self.assertLess(
            blocked_branch.index("_require_job_model_recipe_terms("),
            blocked_branch.index('existing["session_id"] ='),
        )
        self.assertLess(
            blocked_branch.index("_require_job_model_recipe_terms("),
            blocked_branch.index("_queue_recovery_checkpoint("),
        )
        self.assertLess(
            blocked_branch.index("_require_job_model_recipe_terms("),
            blocked_branch.index("threading.Thread("),
        )


if __name__ == "__main__":
    unittest.main()
