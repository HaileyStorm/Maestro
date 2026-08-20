"""Focused source contracts for Maestro Continuum branding and provenance."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTINUUM_VERSION = (ROOT / "CONTINUUM_VERSION").read_text(encoding="utf-8").strip()
MAESTRO_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
BRANDING = (ROOT / "ui/src/lib/branding.ts").read_text(encoding="utf-8")
VITE = (ROOT / "ui/vite.config.ts").read_text(encoding="utf-8")
APP = (ROOT / "ui/src/App.tsx").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "ui/src/components/Sidebar/Sidebar.tsx").read_text(encoding="utf-8")
WELCOME = (ROOT / "ui/src/components/WelcomeModal.tsx").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "ui/src/lib/changelog.ts").read_text(encoding="utf-8")
WHATS_NEW = (ROOT / "ui/src/components/WhatsNewDialog.tsx").read_text(encoding="utf-8")
TYPES = (ROOT / "ui/src/types/index.ts").read_text(encoding="utf-8")
INDEX = (ROOT / "ui/index.html").read_text(encoding="utf-8")
PINOKIO = (ROOT / "pinokio.js").read_text(encoding="utf-8")


class ProductBrandingTests(unittest.TestCase):
    def test_product_and_base_versions_have_distinct_sources(self):
        self.assertRegex(CONTINUUM_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertRegex(MAESTRO_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(CONTINUUM_VERSION, "0.3.0")
        self.assertEqual(MAESTRO_VERSION, "1.9.0")
        self.assertIn("readVersion('../CONTINUUM_VERSION')", VITE)
        self.assertIn("readVersion('../VERSION')", VITE)
        self.assertIn("__CONTINUUM_VERSION__", VITE)
        self.assertIn("__MAESTRO_BASE_VERSION__", VITE)
        self.assertIn("Built on Maestro", BRANDING)
        self.assertIn("currentVersion: PRODUCT_VERSION", CHANGELOG)
        self.assertIn("maestroBaseVersion: MAESTRO_BASE_VERSION", CHANGELOG)
        self.assertIn("validateChangelogManifest(CHANGELOG_MANIFEST)", CHANGELOG)

    def test_visual_and_accessible_names_are_both_explicit(self):
        self.assertIn("PRODUCT_NAME = 'Maestro Continuum'", BRANDING)
        self.assertIn("PRODUCT_NAME_VISUAL = 'Maestro // Continuum'", BRANDING)
        for source in (APP, SIDEBAR, WELCOME):
            self.assertIn("PRODUCT_NAME", source)
            self.assertIn("PRODUCT_NAME_VISUAL", source)
            self.assertIn("PRODUCT_PROVENANCE", source)
        self.assertIn("<title>Maestro Continuum</title>", INDEX)

    def test_remote_shell_uses_embedded_provenance_not_system_config(self):
        self.assertNotIn("systemConfig?.app_version", APP)
        self.assertNotIn("systemConfig?.app_version", SIDEBAR)
        self.assertIn("{PRODUCT_PROVENANCE}", APP)
        self.assertIn("{PRODUCT_PROVENANCE}", SIDEBAR)

    def test_launcher_preserves_schema_branding_and_provenance(self):
        self.assertIn('version: "8.0"', PINOKIO)
        self.assertIn('title: "Maestro // Continuum"', PINOKIO)
        self.assertIn("fs.readFileSync(path.join(__dirname, 'CONTINUUM_VERSION')", PINOKIO)
        self.assertIn("fs.readFileSync(path.join(__dirname, 'VERSION')", PINOKIO)
        self.assertIn("Maestro Continuum v${continuumVersion}", PINOKIO)
        self.assertIn("Built on Maestro ${maestroBaseVersion}", PINOKIO)
        self.assertIn('icon: "maestro_simplified_icon_alpha.png"', PINOKIO)

    def test_welcome_remains_scrollable_with_reachable_primary_action(self):
        self.assertIn("h-[100vh]", WELCOME)
        self.assertIn("supports-[height:100dvh]:h-[100dvh]", WELCOME)
        self.assertIn("max-h-full", WELCOME)
        for property_name, edge in (
            ("paddingTop", "top"),
            ("paddingRight", "right"),
            ("paddingBottom", "bottom"),
            ("paddingLeft", "left"),
        ):
            self.assertIn(
                f"{property_name}: 'max(0.75rem, env(safe-area-inset-{edge}))'",
                WELCOME,
            )
        self.assertIn("flex-1 min-h-0 overflow-y-auto", WELCOME)
        self.assertIn(
            "sticky bottom-0 max-h-[55%] overflow-y-auto overscroll-contain shrink-0 border-t border-border",
            WELCOME,
        )
        self.assertIn("pb-[max(1rem,env(safe-area-inset-bottom))]", WELCOME)
        self.assertIn("flex min-h-11 w-full items-center justify-center", WELCOME)
        self.assertIn("Enter the studio", WELCOME)

    def test_whats_new_is_static_accessible_and_provenance_aware(self):
        self.assertIn("{!isMobile && <WhatsNewButton />}", SIDEBAR)
        self.assertIn("<WhatsNewButton compact />", APP)
        self.assertIn('role="dialog"', WHATS_NEW)
        self.assertIn('aria-modal="true"', WHATS_NEW)
        self.assertIn("installModalFocus({", WHATS_NEW)
        self.assertIn("max-h-[calc(100dvh-1.5rem)]", WHATS_NEW)
        self.assertIn("Earlier releases", WHATS_NEW)
        self.assertIn("Earlier Continuum releases", WHATS_NEW)
        self.assertIn("Included Maestro history", WHATS_NEW)
        self.assertIn("Continuum, Maestro, and WanGP each have their own release history.", CHANGELOG)
        self.assertIn("Earlier WanGP changes are documented by the WanGP project", WHATS_NEW)
        for source in (WHATS_NEW, WELCOME):
            self.assertIn("Continuum ${CURRENT_RELEASE.version} release highlights", source)
            self.assertNotIn("Continuum 0.2 release highlights", source)
        self.assertNotIn("systemConfig", CHANGELOG)
        system_config = TYPES[TYPES.index("export interface SystemConfig"):]
        self.assertIn("Maestro-base compatibility version", system_config)
        self.assertNotIn("shown next to the app title", system_config)


if __name__ == "__main__":
    unittest.main()
