"""Discovery, bounded analysis, and deterministic research reconciliation."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping
import uuid

from services.research_providers import CodexNousRunner, validate_analysis_result
from services.research_sources import (
    DEFAULT_CANDIDATES_PER_CYCLE,
    DEFAULT_SOURCE_SPECS,
    MAX_CANDIDATES_PER_CYCLE,
    SourceSpec,
    discover_public_candidates,
    sanitize_untrusted,
)
from services.research_store import (
    MAX_CURRENT_FINDINGS,
    ResearchRunLocked,
    ResearchStore,
    iso_utc,
    utc_now,
)


class ResearchPipelineError(RuntimeError):
    pass


def _bounded(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_diagnostic(value: Any, limit: int = 500) -> str:
    sanitized, _flags = sanitize_untrusted(_bounded(value, limit))
    return sanitized[:limit]


def _unique_bounded(values: Iterable[Any], *, count: int, chars: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _bounded(value, chars)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
            if len(result) >= count:
                break
    return result


def _identity(candidate: Mapping[str, Any]) -> tuple[str, str]:
    """Use authenticated source aliases; never merge unrelated title matches."""
    aliases = candidate.get("identity_aliases")
    safe_aliases = sorted(
        _bounded(value, 300).lower()
        for value in aliases if isinstance(value, str)
    ) if isinstance(aliases, list) else []
    basis = safe_aliases[0] if safe_aliases else "source:" + _bounded(candidate.get("source_id"), 200)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest(), basis


def reconcile_findings(
    observations: Iterable[tuple[Mapping[str, Any], Mapping[str, Any]]],
    *,
    existing: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """Deterministically merge duplicate observations and surface conflicts."""
    groups: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any], str]]] = {}
    for candidate, result in observations:
        key, basis = _identity(candidate)
        groups.setdefault(key, []).append((candidate, result, basis))
    old_findings = dict((existing or {}).get("findings") or {})
    merged = dict(old_findings)
    for identity_key in sorted(groups):
        group = sorted(
            groups[identity_key],
            key=lambda pair: (
                str(pair[0].get("updated_at") or ""),
                str(pair[0].get("source_digest") or ""),
            ),
        )
        winner_candidate, winner_result, identity_basis = group[-1]
        winner_analysis = winner_result["analysis"]
        finding_id = f"research-{identity_key[:20]}"
        previous = old_findings.get(finding_id)
        aliases = sorted({
            str(alias)
            for candidate, _, _ in group
            for alias in candidate.get("identity_aliases", [])
            if isinstance(alias, str)
        })
        if previous is None:
            matching = [
                value for value in old_findings.values()
                if isinstance(value, Mapping)
                and set(value.get("identity_aliases") or []).intersection(aliases)
            ]
            if len(matching) == 1:
                previous = matching[0]
                finding_id = str(previous.get("finding_id") or finding_id)
        decisions = sorted({str(result["analysis"]["decision"]) for _, result, _ in group})
        targets = sorted({str(result["analysis"]["target_area"]) for _, result, _ in group})
        conflicts: list[str] = []
        if len(decisions) > 1:
            conflicts.append("observations disagree on disposition: " + ", ".join(decisions))
        if len(targets) > 1:
            conflicts.append("observations disagree on target area: " + ", ".join(targets))
        for _, result, _ in group:
            conflicts.extend(result["analysis"].get("conflict_claims") or [])
        if isinstance(previous, Mapping):
            conflicts.extend(previous.get("conflicts") or [])
            old_decision = previous.get("decision")
            old_target = previous.get("target_area")
            if old_decision and old_decision != winner_analysis["decision"]:
                conflicts.append(f"new analysis changed disposition from {old_decision} to {winner_analysis['decision']}")
            if old_target and old_target != winner_analysis["target_area"]:
                conflicts.append(f"new analysis changed target area from {old_target} to {winner_analysis['target_area']}")
        source_urls = _unique_bounded(
            [candidate.get("canonical_url") for candidate, _, _ in group]
            + (list(previous.get("source_urls") or []) if isinstance(previous, Mapping) else []),
            count=8,
            chars=1_024,
        )
        evidence = _unique_bounded(
            [item for _, result, _ in group for item in result["analysis"].get("evidence", [])]
            + (list(previous.get("evidence") or []) if isinstance(previous, Mapping) else []),
            count=8,
            chars=300,
        )
        risks = _unique_bounded(
            [item for _, result, _ in group for item in result["analysis"].get("risks", [])]
            + (list(previous.get("risks") or []) if isinstance(previous, Mapping) else []),
            count=8,
            chars=300,
        )
        provider_provenance = list(previous.get("provider_provenance") or []) if isinstance(previous, Mapping) else []
        provider_provenance.extend([
            {
                "source_id": candidate["source_id"],
                "source_digest": candidate["source_digest"],
                "selected_provider": result["selected_provider"],
                "fallback_used": result["fallback_used"],
                "deepseek_attempt": dict(result["deepseek_attempt"]),
            }
            for candidate, result, _ in group
        ])
        unique_provenance: dict[str, dict[str, Any]] = {}
        for item in provider_provenance:
            if not isinstance(item, dict):
                continue
            key = f"{item.get('source_digest')}:{item.get('selected_provider')}"
            unique_provenance[key] = item
        provider_provenance = [unique_provenance[key] for key in sorted(unique_provenance)][-24:]
        prior_aliases = previous.get("identity_aliases") or [] if isinstance(previous, Mapping) else []
        aliases = sorted(set(aliases).union(str(value) for value in prior_aliases if isinstance(value, str)))
        resolved = previous.get("conflict_resolution") if isinstance(previous, Mapping) else None
        resolution_history = list(previous.get("conflict_resolution_history") or []) if isinstance(previous, Mapping) else []
        if resolved is not None and not resolution_history:
            resolution_history = [resolved]
        durable_conflicts = _unique_bounded(conflicts, count=10, chars=300)
        if durable_conflicts:
            # The append-only resolution event remains the audit record; the
            # singular index marker describes only the current resolved state.
            resolved = None
        prior_source_ids = previous.get("source_ids") or [] if isinstance(previous, Mapping) else []
        source_ids = _unique_bounded(
            [candidate.get("source_id") for candidate, _, _ in group] + list(prior_source_ids),
            count=24,
            chars=240,
        )
        merged[finding_id] = {
            "finding_id": finding_id,
            "identity_basis": identity_basis,
            "identity_aliases": aliases,
            "status": "ready_for_review",
            "title": _bounded(winner_candidate.get("title"), 180),
            "kind": winner_candidate.get("kind"),
            "decision": winner_analysis["decision"],
            "target_area": _bounded(winner_analysis["target_area"], 160),
            "summary": _bounded(winner_analysis["summary"], 500),
            "value": _bounded(winner_analysis["value"], 500),
            "evidence": evidence,
            "risks": risks,
            "conflicts": durable_conflicts,
            "conflict_resolution": resolved,
            "conflict_resolution_history": resolution_history,
            "source_urls": source_urls,
            "source_ids": sorted(source_ids),
            "provider_provenance": provider_provenance,
            "created_at": previous.get("created_at") if isinstance(previous, Mapping) else iso_utc(now),
            "updated_at": iso_utc(now),
            "observation_count": int(previous.get("observation_count") or 0) + len(group)
            if isinstance(previous, Mapping) else len(group),
        }
    newest = sorted(
        merged.items(),
        key=lambda pair: (str(pair[1].get("updated_at") or ""), pair[0]),
        reverse=True,
    )[:MAX_CURRENT_FINDINGS]
    return {
        "schema_version": 1,
        "updated_at": iso_utc(now),
        "findings": dict(sorted(newest)),
    }


class ResearchPipeline:
    def __init__(
        self,
        store: ResearchStore,
        *,
        fetcher: Callable[[SourceSpec], Any] | None = None,
        analyst: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        specs: Iterable[SourceSpec] = DEFAULT_SOURCE_SPECS,
        clock: Callable[[], datetime] = utc_now,
        max_candidates: int = DEFAULT_CANDIDATES_PER_CYCLE,
    ):
        self.store = store
        self.fetcher = fetcher
        self.analyst = analyst or CodexNousRunner()
        self.specs = tuple(specs)
        self.clock = clock
        if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or not 1 <= max_candidates <= MAX_CANDIDATES_PER_CYCLE:
            raise ValueError(f"max_candidates must be between 1 and {MAX_CANDIDATES_PER_CYCLE}")
        self.max_candidates = max_candidates

    def preview(self, *, force: bool = False) -> dict[str, Any]:
        due, reason = self.store.due(now=self.clock())
        return {
            "dry_run": True,
            "would_run": bool(force or due),
            "force": bool(force),
            "reason": "forced" if force else reason,
            "source_lanes": [spec.lane for spec in self.specs],
            "max_candidates": self.max_candidates,
            "state": self.store.read_model(),
        }

    def run(self, *, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            return self.preview(force=force)
        due, due_reason = self.store.due(now=self.clock())
        if not force and not due:
            return {
                "status": "skipped",
                "reason": due_reason,
                "state": self.store.read_model(),
            }
        run_id = f"research-{uuid.uuid4().hex}"
        with self.store.lock("research-run"):
            due, due_reason = self.store.due(now=self.clock())
            if not force and not due:
                return {
                    "status": "skipped",
                    "reason": due_reason,
                    "state": self.store.read_model(),
                }
            started = self.clock()
            try:
                self.store.mark_research_started(run_id, now=started)
            except ResearchRunLocked:
                return {
                    "status": "skipped",
                    "reason": "implementation_active",
                    "state": self.store.read_model(),
                }
            self.store.append_event("research_started", {
                "run_id": run_id,
                "forced": bool(force),
            }, now=started)
            cycle_summary: dict[str, Any] = {
                "run_id": run_id,
                "started_at": iso_utc(started),
                "status": "failed",
                "discovered": 0,
                "analyzed": 0,
                "provider_failures": 0,
                "source_failures": 0,
                "ready_for_review": 0,
                "batch_size": self.max_candidates,
            }
            try:
                candidates, source_failures = discover_public_candidates(
                    fetcher=self.fetcher,
                    specs=self.specs,
                    max_candidates=self.max_candidates,
                )
                cycle_summary["discovered"] = len(candidates)
                cycle_summary["source_failures"] = len(source_failures)
                self.store.update_research_progress(phase="analysis", queued=len(candidates))
                observations: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
                provider_failures: list[dict[str, str]] = []
                deepseek_disabled_reason: str | None = None
                for position, candidate in enumerate(candidates):
                    try:
                        if deepseek_disabled_reason is not None:
                            fallback = getattr(self.analyst, "luna_fallback", None)
                            if not callable(fallback):
                                raise ResearchPipelineError("DeepSeek circuit is open and analyst has no Luna fallback")
                            raw_result = fallback(candidate, deepseek_disabled_reason)
                        else:
                            raw_result = self.analyst(candidate)
                        result = validate_analysis_result(candidate, raw_result)
                        attempt = result.get("deepseek_attempt") or {}
                        if attempt.get("status") in {"failed", "unavailable"}:
                            failure = str(attempt.get("exact_failure") or "")
                            deepseek_disabled_reason = _bounded(
                                failure or "DeepSeek primary attempt failed its exact gate",
                                500,
                            )
                    except Exception as error:
                        diagnostic = _safe_diagnostic(error)
                        if deepseek_disabled_reason is None:
                            deepseek_disabled_reason = diagnostic or "DeepSeek primary attempt failed"
                        provider_failures.append({
                            "source_id": _bounded(candidate.get("source_id"), 240),
                            "error_type": _safe_diagnostic(type(error).__name__, 80),
                            "message": diagnostic,
                        })
                    else:
                        observations.append((candidate, result))
                    self.store.update_research_progress(
                        phase="analysis",
                        queued=max(0, len(candidates) - position - 1),
                    )
                self.store.update_research_progress(phase="reconciliation", queued=0)
                index = reconcile_findings(
                    observations,
                    existing=self.store.load_index(),
                    now=self.clock(),
                )
                self.store.save_index(index)
                cycle_summary.update({
                    "status": "completed",
                    "analyzed": len(observations),
                    "provider_failures": len(provider_failures),
                    "ready_for_review": sum(
                        1 for value in index["findings"].values()
                        if value.get("status") == "ready_for_review"
                    ),
                    "source_failure_summaries": source_failures[:3],
                    "provider_failure_summaries": provider_failures[:3],
                    "deepseek_disabled_reason": deepseek_disabled_reason,
                })
            except Exception as error:
                cycle_summary["failure"] = {
                    "error_type": _safe_diagnostic(type(error).__name__, 80),
                    "message": _safe_diagnostic(error),
                }
                raise
            finally:
                finished = self.clock()
                cycle_summary["completed_at"] = iso_utc(finished)
                self.store.mark_research_finished(
                    cycle_summary,
                    now=finished,
                    advance_schedule=True,
                )
                self.store.append_event("research_finished", {
                    key: cycle_summary[key]
                    for key in (
                        "run_id", "status", "discovered", "analyzed",
                        "provider_failures", "source_failures", "ready_for_review",
                    )
                }, now=finished)
            return {
                "status": "completed",
                "cycle": cycle_summary,
                "state": self.store.read_model(),
            }

    def implementation_packet(
        self,
        *,
        readiness_threshold: int = 3,
        force: bool = False,
    ) -> dict[str, Any]:
        """Return reconciled review chunks; never execute implementation."""
        return self.store.build_implementation_packet(
            readiness_threshold=readiness_threshold,
            force=force,
            now=self.clock(),
        )
