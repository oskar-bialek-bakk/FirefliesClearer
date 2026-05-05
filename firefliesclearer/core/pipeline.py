"""Per-meeting transactional pipeline: list -> archive -> verify -> delete."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from firefliesclearer.core.archiver import ArchiveDriftError, Archiver
from firefliesclearer.core.manifest import Manifest, MeetingRecord
from firefliesclearer.core.models import Meeting, MeetingState
from firefliesclearer.ports.clock import Clock
from firefliesclearer.ports.meeting_repository import MeetingRepository
from firefliesclearer.ports.summary_renderer import SummaryRenderer


class PipelineMode(StrEnum):
    DRY_RUN = "dry_run"
    APPLY = "apply"  # archive + delete
    ARCHIVE_ONLY = "archive"  # archive, no delete (curated step 2)
    PURGE_ONLY = "purge"  # delete archived, no new archive (curated step 3)


@dataclass(slots=True)
class RunReport:
    archived: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        *,
        repository: MeetingRepository,
        manifest: Manifest,
        archiver: Archiver,
        renderer: SummaryRenderer,
        clock: Clock,
        user_email: str | None = None,
    ) -> None:
        self._repo = repository
        self._manifest = manifest
        self._archiver = archiver
        self._renderer = renderer
        self._clock = clock
        # Lower-cased copy used for case-insensitive comparison in
        # ``_archive``. None means "skip the host check" — both for backward
        # compatibility with existing call sites that don't know the user's
        # email and as the safe default when the value can't be resolved.
        self._user_email_lc = user_email.lower() if user_email else None

    async def run(self, meetings: Sequence[Meeting], *, mode: PipelineMode) -> RunReport:
        report = RunReport()
        for m in meetings:
            await self._process_one(m, mode=mode, report=report)
        return report

    async def _process_one(
        self, meeting: Meeting, *, mode: PipelineMode, report: RunReport
    ) -> None:
        if mode is PipelineMode.DRY_RUN:
            return
        existing = self._manifest.get(meeting.meeting_id)
        if existing and existing.state is MeetingState.DELETED:
            report.skipped += 1
            return
        if mode is PipelineMode.PURGE_ONLY:
            await self._purge_only(meeting, existing, report)
            return
        if existing is None or existing.state is MeetingState.PENDING:
            self._manifest.register(meeting, at=self._clock.now())
            ok = await self._archive(meeting, report)
            if not ok:
                return
        elif existing.state is MeetingState.KNOWN or existing.state.value.startswith("failed_"):
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.PENDING,
                at=self._clock.now(),
            )
            ok = await self._archive(meeting, report)
            if not ok:
                return
        if mode is PipelineMode.ARCHIVE_ONLY:
            return
        await self._delete(meeting, report)

    async def _archive(self, meeting: Meeting, report: RunReport) -> bool:
        try:
            bundle = await self._repo.fetch_artifacts(meeting.meeting_id)
        except Exception as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_FETCH,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"fetch: {e}"))
            return False

        try:
            pdf = self._renderer.render(
                bundle.summary_payload or {},
                meeting_title=meeting.title,
                meeting=meeting,
                source_url=bundle.metadata.get("source_url") if bundle.metadata else None,
            )
        except Exception as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_RENDER,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"render: {e}"))
            return False

        try:
            result = self._archiver.archive(
                meeting=meeting,
                bundle=bundle,
                summary_pdf=pdf,
                allow_known=True,
            )
        except ArchiveDriftError as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_VERIFY,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"drift: {e}"))
            return False
        except Exception as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_DOWNLOAD,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"write: {e}"))
            return False

        if not self._archiver.verify(result.archive_dir):
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.FAILED_VERIFY,
                at=self._clock.now(),
                last_error="verify failed",
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, "verify failed"))
            return False

        now = self._clock.now()
        self._manifest.transition(
            meeting.meeting_id,
            to=MeetingState.ARCHIVED,
            at=now,
            archive_path=str(result.archive_dir),
            verified_at=now,
            sha256s=result.sha256s,
        )
        report.archived += 1
        # Auto-mark non-host meetings as DELETED. Fireflies' deleteTranscript
        # mutation rejects calls from non-hosts (the user "does not have
        # admin privileges to delete"), so an API attempt would just burn
        # one of today's 50 GraphQL ops on a guaranteed failure. We have a
        # verified archive on disk, so the local manifest can claim DELETED
        # honestly — the row's ``deleted_at`` makes the provenance clear in
        # state_log: ``reason='non_host_no_api_delete'``.
        if self._user_email_lc and self._is_non_host(meeting):
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.DELETED,
                at=now,
                details={"reason": "non_host_no_api_delete"},
            )
            report.deleted += 1
        return True

    def _is_non_host(self, meeting: Meeting) -> bool:
        """True when the meeting's host_email is set and differs from the
        configured user_email (case-insensitive). Returns False when either
        value is empty so we never falsely promote a row missing host data."""
        if not self._user_email_lc:
            return False
        if not meeting.host_email:
            return False
        return meeting.host_email.lower() != self._user_email_lc

    async def _delete(self, meeting: Meeting, report: RunReport) -> None:
        rec = self._manifest.get(meeting.meeting_id)
        # DELETED_FAILED is a valid retry entry: the archive on disk is intact
        # and verified, only the upstream API call failed last time.
        if rec is None or rec.state not in (MeetingState.ARCHIVED, MeetingState.DELETED_FAILED):
            return
        try:
            await self._repo.delete_meeting(meeting.meeting_id)
        except Exception as e:
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.DELETED_FAILED,
                at=self._clock.now(),
                last_error=str(e),
            )
            report.failed += 1
            report.failures.append((meeting.meeting_id, f"delete: {e}"))
            return
        self._manifest.transition(
            meeting.meeting_id,
            to=MeetingState.DELETED,
            at=self._clock.now(),
        )
        report.deleted += 1

    async def _purge_only(
        self,
        meeting: Meeting,
        existing: MeetingRecord | None,
        report: RunReport,
    ) -> None:
        if existing is None or existing.state is not MeetingState.ARCHIVED:
            report.skipped += 1
            return
        await self._delete(meeting, report)

    # ------------------------------------------------------------------
    # Public per-meeting helpers (used by ArchiveService and the web layer)
    # ------------------------------------------------------------------

    async def archive_one(self, meeting: Meeting) -> MeetingState:
        """Archive a single meeting; return its final state."""
        report = RunReport()
        existing = self._manifest.get(meeting.meeting_id)
        if existing and existing.state is MeetingState.DELETED:
            return MeetingState.DELETED
        if existing is None or existing.state is MeetingState.PENDING:
            self._manifest.register(meeting, at=self._clock.now())
            await self._archive(meeting, report)
        elif existing.state is MeetingState.KNOWN or existing.state.value.startswith("failed_"):
            self._manifest.transition(
                meeting.meeting_id,
                to=MeetingState.PENDING,
                at=self._clock.now(),
            )
            await self._archive(meeting, report)
        rec = self._manifest.get(meeting.meeting_id)
        return rec.state if rec else MeetingState.PENDING

    async def purge_one(self, meeting: Meeting) -> MeetingState:
        """Delete *meeting* from the source repository; return final state.

        Accepts ARCHIVED or DELETED_FAILED as the manifest entry state — the
        archive on disk is the same in both cases, only the prior delete API
        call differs. Any other state (PENDING, FAILED_*, DELETED, KNOWN)
        short-circuits and returns the current state without calling delete.

        Does NOT re-verify the archive on disk before deletion (deferred to
        v2.x — tracked as the verify-before-delete gap).
        """
        report = RunReport()
        existing = self._manifest.get(meeting.meeting_id)
        if existing is None or existing.state not in (
            MeetingState.ARCHIVED,
            MeetingState.DELETED_FAILED,
        ):
            return existing.state if existing else MeetingState.PENDING
        await self._delete(meeting, report)
        rec = self._manifest.get(meeting.meeting_id)
        return rec.state if rec else MeetingState.PENDING
