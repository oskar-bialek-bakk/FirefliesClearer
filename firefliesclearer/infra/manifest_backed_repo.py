"""Read-only MeetingRepository backed by the local Manifest cache.

Implements only ``list_meetings``. Mutation methods raise NotImplementedError —
ScanService is the only caller that should reach this adapter, and it only
needs reads. ArchiveService/PurgeService continue to use the live
FirefliesClient for fetch_artifacts and delete_meeting.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from firefliesclearer.core.manifest import Manifest
from firefliesclearer.core.models import ArtifactBundle, Meeting
from firefliesclearer.ports.meeting_repository import MeetingFilter


class ManifestBackedRepository:
    def __init__(self, manifest: Manifest) -> None:
        self._manifest = manifest

    async def list_meetings(self, filter: MeetingFilter) -> AsyncIterator[Meeting]:
        for m in self._manifest.list_known(older_than=filter.older_than):
            yield m

    async def fetch_artifacts(self, meeting_id: str) -> ArtifactBundle:
        raise NotImplementedError(
            "ManifestBackedRepository is a read-only cache adapter; "
            "fetch_artifacts must go to the live FirefliesClient."
        )

    async def delete_meeting(self, meeting_id: str) -> None:
        raise NotImplementedError(
            "ManifestBackedRepository is a read-only cache adapter; "
            "delete_meeting must go to the live FirefliesClient."
        )

    async def ping_user(self) -> str:
        raise NotImplementedError(
            "ManifestBackedRepository is a read-only cache adapter; "
            "ping_user must go to the live FirefliesClient."
        )
