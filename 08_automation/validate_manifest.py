#!/usr/bin/env python3

"""
Qur'an Content Manifest Validator

This validator checks the structural integrity of a content manifest.

IMPORTANT:
This program validates metadata and production gates.
It does NOT determine whether Qur'anic text itself is religiously correct.

Religious/source verification must occur upstream through approved sources.
"""

import json
import sys
from pathlib import Path


REQUIRED_TOP_LEVEL = [
    "manifest_id",
    "content_type",
    "manifest_version",
    "status",
    "test_data",
    "quran_range",
    "ayahs",
    "audio",
    "visuals",
    "video",
    "text",
    "verification",
]


REQUIRED_QURAN_RANGE = [
    "surah_number",
    "surah_name",
    "start_ayah",
    "end_ayah",
]


REQUIRED_AYAH_FIELDS = [
    "verse_key",
    "surah_number",
    "ayah_number",
    "sequence_index",
]


REQUIRED_VERIFICATION_FIELDS = [
    "arabic_verified",
    "translation_verified",
    "sequence_verified",
    "audio_verified",
    "audio_rights_verified",
    "visuals_verified",
    "human_approval",
]


def fail(errors):
    print("MANIFEST VALIDATION: FAILED")
    print()

    for error in errors:
        print(f"- {error}")

    print()
    print("RESULT: DO NOT RENDER")
    return 1


def main():
    if len(sys.argv) != 2:
        print("Usage: python validate_manifest.py <manifest.json>")
        return 2

    manifest_path = Path(sys.argv[1])

    if not manifest_path.exists():
        return fail([f"Manifest not found: {manifest_path}"])

    try:
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)
    except json.JSONDecodeError as exc:
        return fail([f"Invalid JSON: {exc}"])

    errors = []

    # ---------------------------------------------------------
    # Top-level structure
    # ---------------------------------------------------------

    for field in REQUIRED_TOP_LEVEL:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")

    if errors:
        return fail(errors)

    # ---------------------------------------------------------
    # Basic identity
    # ---------------------------------------------------------

    if manifest["content_type"] != "quran":
        errors.append("content_type must equal 'quran'.")

    if not isinstance(manifest["manifest_version"], int):
        errors.append("manifest_version must be an integer.")

    # ---------------------------------------------------------
    # Qur'an range
    # ---------------------------------------------------------

    quran_range = manifest["quran_range"]

    for field in REQUIRED_QURAN_RANGE:
        if field not in quran_range:
            errors.append(f"Missing quran_range field: {field}")

    if not errors:
        surah_number = quran_range["surah_number"]
        start_ayah = quran_range["start_ayah"]
        end_ayah = quran_range["end_ayah"]

        if not isinstance(surah_number, int):
            errors.append("surah_number must be an integer.")

        if not isinstance(start_ayah, int):
            errors.append("start_ayah must be an integer.")

        if not isinstance(end_ayah, int):
            errors.append("end_ayah must be an integer.")

        if isinstance(start_ayah, int) and isinstance(end_ayah, int):
            if start_ayah < 1:
                errors.append("start_ayah must be >= 1.")

            if end_ayah < start_ayah:
                errors.append("end_ayah must be >= start_ayah.")

    # ---------------------------------------------------------
    # Ayah sequence
    # ---------------------------------------------------------

    ayahs = manifest["ayahs"]

    if not isinstance(ayahs, list):
        errors.append("ayahs must be a list.")
        ayahs = []

    expected_count = 0

    if not errors and isinstance(start_ayah, int) and isinstance(end_ayah, int):
        expected_count = end_ayah - start_ayah + 1

        if len(ayahs) != expected_count:
            errors.append(
                f"Ayah count mismatch: expected {expected_count}, "
                f"received {len(ayahs)}."
            )

    expected_sequence = []

    for index, ayah in enumerate(ayahs, start=1):

        if not isinstance(ayah, dict):
            errors.append(f"Ayah record {index} is not an object.")
            continue

        for field in REQUIRED_AYAH_FIELDS:
            if field not in ayah:
                errors.append(
                    f"Ayah record {index} missing field: {field}"
                )

        if not all(field in ayah for field in REQUIRED_AYAH_FIELDS):
            continue

        if ayah["surah_number"] != quran_range["surah_number"]:
            errors.append(
                f"Ayah record {index} has incorrect Surah number."
            )

        if ayah["sequence_index"] != index:
            errors.append(
                f"Ayah record {index} has incorrect sequence_index."
            )

        expected_key = (
            f"{ayah['surah_number']}:{ayah['ayah_number']}"
        )

        if ayah["verse_key"] != expected_key:
            errors.append(
                f"Ayah record {index} verse_key mismatch: "
                f"expected {expected_key}, "
                f"received {ayah['verse_key']}."
            )

        expected_sequence.append(
            (ayah["surah_number"], ayah["ayah_number"])
        )

    # ---------------------------------------------------------
    # Exact range sequence
    # ---------------------------------------------------------

    if not errors and expected_count:
        expected = [
            (quran_range["surah_number"], ayah_number)
            for ayah_number in range(
                quran_range["start_ayah"],
                quran_range["end_ayah"] + 1,
            )
        ]

        if expected_sequence != expected:
            errors.append(
                "Ayah sequence is incorrect. "
                "Required sequence is the complete inclusive range."
            )

    # ---------------------------------------------------------
    # Duplicate detection
    # ---------------------------------------------------------

    verse_keys = [
        ayah.get("verse_key")
        for ayah in ayahs
        if isinstance(ayah, dict)
    ]

    duplicates = sorted(
        {
            key
            for key in verse_keys
            if key is not None and verse_keys.count(key) > 1
        }
    )

    if duplicates:
        errors.append(
            "Duplicate verse keys detected: "
            + ", ".join(duplicates)
        )

    # ---------------------------------------------------------
    # Verification structure
    # ---------------------------------------------------------

    verification = manifest["verification"]

    if not isinstance(verification, dict):
        errors.append("verification must be an object.")
        verification = {}

    for field in REQUIRED_VERIFICATION_FIELDS:
        if field not in verification:
            errors.append(
                f"Missing verification field: {field}"
            )

    # ---------------------------------------------------------
    # Test-data protection
    # ---------------------------------------------------------

    if manifest["test_data"] is True:
        if errors:
            return fail(errors)

        print("MANIFEST VALIDATION: STRUCTURALLY VALID")
        print()
        print("STATUS: TEST DATA")
        print("Production rendering is blocked.")
        print()
        print("RESULT: TEST MANIFEST ACCEPTED FOR TESTING ONLY")
        return 0

    # ---------------------------------------------------------
    # Production safety gate
    # ---------------------------------------------------------

    required_approvals = [
        "arabic_verified",
        "translation_verified",
        "sequence_verified",
        "audio_verified",
        "audio_rights_verified",
        "visuals_verified",
        "human_approval",
    ]

    for field in required_approvals:
        if verification.get(field) is not True:
            errors.append(
                f"Production gate not satisfied: {field} must be true."
            )

    if manifest.get("status") != "VERIFIED":
        errors.append(
            "Production manifest status must be 'VERIFIED'."
        )

    # ---------------------------------------------------------
    # Final result
    # ---------------------------------------------------------

    if errors:
        return fail(errors)

    print("MANIFEST VALIDATION: PASSED")
    print()
    print(f"Manifest: {manifest['manifest_id']}")
    print(
        f"Range: "
        f"{quran_range['surah_number']}:"
        f"{quran_range['start_ayah']}-"
        f"{quran_range['end_ayah']}"
    )
    print(f"Ayahs: {len(ayahs)}")
    print()
    print("RESULT: SAFE TO PROCEED TO NEXT VERIFICATION STAGE")

    return 0


if __name__ == "__main__":
    sys.exit(main())
