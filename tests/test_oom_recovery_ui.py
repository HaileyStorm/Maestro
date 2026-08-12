"""Static contracts for the structured OOM recovery UI."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TYPES = (ROOT / "ui/src/types/index.ts").read_text(encoding="utf-8")
BANNER = (ROOT / "ui/src/components/OomRecoveryBanner.tsx").read_text(
    encoding="utf-8",
)
MAIN = (ROOT / "ui/src/components/MainContent/MainContent.tsx").read_text(
    encoding="utf-8",
)
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
RECOVERY = (ROOT / "ui/src/components/H3DeliveryRecoveryStatus.tsx").read_text(
    encoding="utf-8",
)
RECOVERY_HOOK = (ROOT / "ui/src/lib/useH3DeliveryRecovery.ts").read_text(
    encoding="utf-8",
)
RECOVERY_CONTRACT = (
    ROOT / "ui/src/lib/h3DeliveryRecoveryContract.ts"
).read_text(encoding="utf-8")
STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")


class OomRecoveryUiContracts(unittest.TestCase):
    def run_contract(self, body: str):
        source = """
import {
  recoveryGalleryNavigationVerified,
  selectRecoverySourceIndex,
} from './ui/src/lib/h3DeliveryRecoveryContract.ts'
""" + body
        subprocess.run(
            [
                "node", "--no-warnings", "--experimental-strip-types",
                "--input-type=module", "-e", source,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_delivery_oom_has_structured_recovery_fields(self):
        for field in (
            "stage?: 'h3_delivery'",
            "requested_target?: string",
            "native_available?: boolean",
            "retry_count?: number",
            "manual_retry_count?: number",
            "recoverable?: boolean",
            "actions?: string[]",
        ):
            self.assertIn(field, TYPES)

    def test_banner_distinguishes_delivery_and_saved_original_result(self):
        self.assertIn("oom.stage === 'h3_delivery'", BANNER)
        self.assertIn("Output processing ran out of GPU memory", BANNER)
        self.assertIn("Generation finished.", BANNER)
        self.assertIn("Maestro saved the original result privately", BANNER)
        self.assertIn("after one automatic retry.", BANNER)
        self.assertNotIn("V1 scope", BANNER)
        self.assertNotIn("Deferred to a later phase", BANNER)

    def test_machine_setting_and_raw_details_are_local_only(self):
        self.assertIn("accessContext?.machine_controls === true", BANNER)
        self.assertIn(
            "!isDeliveryOom && machineControls && "
            "oom.suggested_coefficient !== null",
            BANNER,
        )
        self.assertIn("{machineControls && (", BANNER)
        self.assertIn(
            "(!job.oomInfo || machineControls) && "
            "(hasLocalEvents || api.isBackendJobId(job.id))",
            MAIN,
        )

    def test_failed_delivery_card_stays_recoverable_without_rerun_copy(self):
        self.assertIn("Output Processing Failed After Generation", MAIN)
        self.assertIn("recovery options are below", MAIN)
        self.assertIn("Generation will not run again", RECOVERY)
        self.assertIn("<H3DeliveryRecoveryStatus", MAIN)

    def test_failed_retry_child_never_queries_itself_for_source_actions(self):
        self.assertIn("const isDeliveryRecoveryChild", MAIN)
        self.assertIn("!isDeliveryRecoveryChild && job.workspace", MAIN)
        self.assertIn("Delivery Retry Failed", MAIN)
        self.assertIn("selectRecoverySourceIndex", BANNER)
        self.assertIn(
            "failedJob.oomInfo.manual_retry_count == null ? failedJob.id : undefined",
            BANNER,
        )

    def test_newest_source_wins_over_older_source_and_retry_child(self):
        self.run_contract("""
const jobs = [
  { createdAt: 30 },
  { createdAt: 20 },
  { createdAt: 40, manualRetryCount: 1 },
]
if (selectRecoverySourceIndex(jobs) !== 0) throw new Error('wrong source')
if (selectRecoverySourceIndex([
  { createdAt: 20 }, { createdAt: 30 }, { createdAt: 40, manualRetryCount: 1 },
]) !== 1) throw new Error('created_at ordering ignored')
""")
        self.assertIn("selectRecoverySourceIndex", BANNER)
        self.assertNotIn("[...jobs].reverse()", BANNER)

    def test_actions_use_only_owner_project_scoped_capabilities(self):
        self.assertIn("/delivery-recovery?${query}", CLIENT)
        self.assertIn("/delivery-recovery/${suffix}", CLIENT)
        self.assertIn("JSON.stringify({ workspace, capability })", CLIENT)
        self.assertIn("{ cache: 'no-store' }", CLIENT)
        self.assertIn("acceptCapability &&", RECOVERY)
        self.assertIn("retryCapability &&", RECOVERY)
        self.assertIn("Use saved result", RECOVERY)
        self.assertIn("Retry delivery only", RECOVERY)
        self.assertNotIn("console.", RECOVERY)
        self.assertNotIn("console.", RECOVERY_HOOK)

    def test_recovery_state_refreshes_and_child_uses_normal_job_polling(self):
        self.assertIn("window.setInterval(() => { void refresh() }, 2500)", RECOVERY_HOOK)
        self.assertIn("announceH3DeliveryRecoveryChange(sourceJobId)", RECOVERY)
        self.assertIn("await reconnectJobs()", RECOVERY)
        self.assertIn("!activeChild) void reconnectJobs()", RECOVERY)
        self.assertIn("active_recovery_job_id", RECOVERY)
        self.assertIn("completed_recovery_job_id", RECOVERY)
        self.assertIn("Delivery retries used: {retryCount} of {retryLimit}", RECOVERY)
        self.assertIn("typeof recovery.restart_supported === 'boolean'", RECOVERY)
        self.assertIn("Generation will not run again", RECOVERY)
        self.assertIn("machine settings will not change", RECOVERY)
        self.assertIn("recoveryIdentity === identity ? recovery : null", RECOVERY_HOOK)

    def test_completed_recovery_refreshes_and_opens_gallery(self):
        self.assertIn("void loadOutputs()", RECOVERY)
        self.assertIn(": await switchWorkspace(workspace)", RECOVERY)
        self.assertIn("window.dispatchEvent(new Event(OPEN_GALLERY_EVENT))", RECOVERY)
        self.assertIn("window.addEventListener(OPEN_GALLERY_EVENT, openGallery)", MAIN)

    def test_gallery_navigation_rejects_absorbed_switch_and_load_failures(self):
        self.run_contract("""
const base = {
  expectedWorkspace: 'project-a', activeWorkspace: 'project-a',
  browsingUploads: false, switchSucceeded: true, outputsLoaded: true,
}
if (!recoveryGalleryNavigationVerified(base)) throw new Error('valid state rejected')
if (recoveryGalleryNavigationVerified({ ...base, switchSucceeded: false })) {
  throw new Error('absorbed switch failure accepted')
}
if (recoveryGalleryNavigationVerified({ ...base, activeWorkspace: 'project-b' })) {
  throw new Error('wrong project accepted')
}
if (recoveryGalleryNavigationVerified({ ...base, outputsLoaded: false })) {
  throw new Error('absorbed output failure accepted')
}
""")
        self.assertIn("switchWorkspace: (name: string) => Promise<boolean>", STORE)
        self.assertIn("loadOutputs: () => Promise<boolean>", STORE)
        self.assertIn("recoveryGalleryNavigationVerified", RECOVERY)
        self.assertIn("switchSucceeded,", RECOVERY_CONTRACT)
        self.assertIn("outputsLoaded,", RECOVERY_CONTRACT)

    def test_dismissal_comment_matches_restored_failed_jobs(self):
        self.assertIn("may be restored by the normal job reconnection flow", BANNER)
        self.assertNotIn("failed jobs also don't survive", BANNER)

    def test_completed_parent_copy_is_historical_not_present_tense(self):
        self.assertIn("saved the original result privately when", BANNER)
        self.assertIn("Maestro saved the original result privately", MAIN)
        self.assertNotIn("still ran out of GPU memory", BANNER)
        self.assertNotIn("still ran out of GPU memory", MAIN)


if __name__ == "__main__":
    unittest.main()
