# Qur'an Source Adapter Specification

## Purpose

The Qur'an production system must not be permanently dependent on a single
external data provider.

External sources must be converted into one standardized internal format.

---

# 1. Architecture

External Source
      ↓
Source Adapter
      ↓
Standard Qur'an Record
      ↓
Verification
      ↓
Content Manifest
      ↓
Video Production

---

# 2. Approved Source Principle

A source may only be used for production after:

1. Source identity is known.
2. Source content is appropriate for the intended field.
3. Licensing/usage requirements are understood.
4. Verification requirements are satisfied.

---

# 3. Standard Ayah Record

Every source adapter must be capable of producing:

{
  "verse_key": "",
  "surah_number": 0,
  "surah_name": "",
  "ayah_number": 0,
  "arabic_text": "",
  "arabic_script": "",
  "translation_text": "",
  "translation_source": "",
  "translation_version": "",
  "audio_source": "",
  "audio_reciter": "",
  "audio_reference": ""
}

---

# 4. Required Qur'an Fields

The internal record must identify:

- Surah number
- Surah name
- Ayah number
- Verse key
- Arabic text
- Arabic script
- Translation source
- Translation version

Audio fields are required when audio is being used.

---

# 5. Source Independence

The video engine must NOT directly depend on:

- Quran Foundation API
- Tanzil
- Any single translation provider
- Any single audio provider

Instead, the video engine receives already-standardized verified records.

---

# 6. Source Replacement

If a source becomes:

- unavailable
- rate limited
- discontinued
- inaccessible
- incompatible
- legally unsuitable
- technically unsuitable

the source adapter may be replaced without rebuilding the video engine.

---

# 7. No Automatic Source Switching

The system must NOT silently switch to another source.

If the primary source fails:

STATUS = SOURCE_FAILURE

The system must either:

1. use another explicitly approved source, or
2. stop the production job.

---

# 8. Source Priority

Each field may have a designated source.

Example:

Arabic:
Primary source → Quran Foundation
Cross-check → Tanzil

Translation:
Approved translation source → [to be selected]

Audio:
Approved licensed source → [to be selected]

The exact production sources must be recorded in the Source Register.

---

# 9. Cross-Source Comparison

Where independent verification is required, the system must compare the
relevant fields before approval.

Examples:

Arabic text
Verse key
Surah number
Ayah number

A mismatch must produce:

VERIFICATION_FAILURE

---

# 10. Source Versioning

Every imported record should retain:

- source name
- source version where available
- retrieval date
- source identifier
- adapter version

This makes production reproducible.

---

# 11. Credentials

API keys, client secrets, access tokens and passwords must never be stored
inside GitHub source files.

Credentials must be stored using secure cloud secret storage.

---

# 12. Caching

Approved source data may be cached when legally permitted.

Caching must not become uncontrolled permanent storage.

Temporary source data must have an explicit retention policy.

---

# 13. Failure Handling

If the source cannot provide required data:

DO NOT GUESS.

Return:

SOURCE_DATA_INCOMPLETE

The production job must stop.

---

# 14. Core Principle

The project depends on VERIFIED DATA,
not on a particular provider.

Provider failure must not require rebuilding the project.
