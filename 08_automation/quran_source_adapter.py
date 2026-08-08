"""
Qur'an Source Adapter

Purpose:
Convert approved external Qur'an source data into the project's
standard internal format.

IMPORTANT:
This adapter must never modify Qur'anic text.

It may normalize metadata and structure, but source text must remain
exactly as received from the approved source.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class QuranVerse:
    verse_key: str
    surah_number: int
    ayah_number: int
    arabic_text: str
    translation_text: Optional[str]
    translation_source: Optional[str]
    translation_version: Optional[str]


class QuranSourceAdapter:
    """
    Interface for approved Qur'an data providers.

    Provider-specific API code will be implemented separately.
    """

    def get_verse(
        self,
        surah_number: int,
        ayah_number: int,
    ) -> QuranVerse:
        raise NotImplementedError

    def get_range(
        self,
        surah_number: int,
        start_ayah: int,
        end_ayah: int,
    ) -> list[QuranVerse]:
        raise NotImplementedError
