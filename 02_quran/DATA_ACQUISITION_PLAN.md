# Qur'an Data Acquisition Plan

## 1. Purpose

This document defines how verified Qur'an information enters the
Islamic Content Automation system.

No religious content may enter production through an uncontrolled AI
generation process.

---

# 2. Data Components

A Qur'an video contains several independent data components:

1. Qur'an Arabic text
2. Surah metadata
3. Ayah metadata
4. English translation
5. Recitation audio
6. Audio timing
7. Visual metadata

Each component must have an identifiable source.

---

# 3. Arabic Qur'an Text

## Primary source

Quran Foundation Content API

Purpose:

- Surah metadata
- Ayah metadata
- Uthmani Arabic text
- Verse keys
- Verse ranges

Status:

ACCESS REQUEST PENDING

---

## Independent Arabic verification

Tanzil

Purpose:

Independent cross-check of Qur'an Arabic text.

Status:

CANDIDATE — VERIFICATION REQUIRED

---

# 4. Arabic Script

Initial production script:

Uthmani

The system must explicitly identify the Arabic script used.

The system must not silently change between Arabic script variants.

---

# 5. English Translation

Candidate:

Saheeh International

Distribution/source:

QuranEnc

Translation key:

english_saheeh

Current observed source version:

1.1.2

Status:

CANDIDATE — FINAL PRODUCTION LOCK PENDING

The current version must be rechecked immediately before production
integration.

---

# 6. Translation Rules

The translation must be:

- exact
- unmodified
- versioned
- traceable
- properly attributed

The system must not:

- paraphrase
- shorten
- rewrite
- combine translations
- insert AI-generated words
- remove words

---

# 7. Translation Updates

If QuranEnc publishes a newer approved version:

1. Record the new version.
2. Review the source terms.
3. Update the translation configuration.
4. Verify new content.
5. Use the new version for future production.

Existing published videos must not be silently altered.

---

# 8. Recitation

Preferred reciter:

Shaykh Saad Al-Ghamdi

Status:

LICENCE AND SOURCE VERIFICATION REQUIRED

The system must identify:

- exact audio source
- reciter
- recording/version
- ayah coverage
- usage rights
- social-media publishing rights
- redistribution rights
- long-term usage conditions

No random online recording may be used.

---

# 9. Audio Timing

The audio system must ultimately provide ayah-level timing.

Required structure:

{
  "verse_key": "22:13",
  "start_time": 0.000,
  "end_time": 5.000
}

The numbers above are only an example.

Production timings must come from verified audio data.

---

# 10. Visual Assets

Visual assets must be independently tracked.

Every visual asset must have:

- asset ID
- source
- licence/status
- creation method
- intended use
- visual category
- human-face check
- religious-sensitivity check

---

# 11. Visual Generation

AI-generated visuals may be used when appropriate.

Generated visuals must not be presented as literal depictions of:

- Jannah
- Jahannam
- angels
- prophets
- other unseen realities

---

# 12. Source Acquisition Order

The production pipeline should acquire data in this order:

1. Identify Surah
2. Identify ayah range
3. Retrieve Arabic
4. Retrieve translation
5. Retrieve audio
6. Retrieve audio timing
7. Retrieve/select visuals
8. Run verification
9. Create render manifest
10. Render

---

# 13. No Random Verse Selection

The system must never create a production Qur'an video by asking AI to
choose arbitrary verses based only on a topic.

Every video must have an explicit:

- Surah number
- start ayah
- end ayah

---

# 14. Sequential Integrity

If a video specifies:

START = 22:13
END = 22:16

the system must obtain:

22:13
22:14
22:15
22:16

in that exact order.

---

# 15. Cross-Source Verification

Before production:

Arabic source data should be compared against the independent Arabic
verification source.

Translation must be checked against its exact verse key.

Audio must be checked against its exact verse range.

Any mismatch must stop the job.

---

# 16. Source Adapter

Every external source must pass through the project's source adapter.

The video renderer must consume standardized internal records.

The renderer must not directly depend on external API response formats.

---

# 17. Credentials

API credentials must never be stored in GitHub files.

Credentials must be stored in secure cloud secret storage.

---

# 18. Caching

Data may only be cached when the relevant source terms permit it.

Caching must be:

- purposeful
- bounded
- versioned
- cleaned when no longer required

---

# 19. Storage Cleanup

Temporary source downloads must not remain indefinitely.

Temporary files include:

- raw API responses
- temporary audio
- temporary images
- temporary video
- intermediate processing files

Default temporary retention:

24 hours

Permanent verification records must be retained separately.

---

# 20. Source Failure

If an approved source becomes unavailable:

DO NOT GUESS.

DO NOT SILENTLY SWITCH SOURCES.

Record:

SOURCE_FAILURE

Then:

1. retry according to safe limits
2. preserve the manifest
3. preserve verification records
4. clean temporary files
5. stop if the source remains unavailable

---

# 21. Provider Independence

The project must remain capable of replacing a provider without rebuilding
the video engine.

Example:

Quran Foundation
→ Source Adapter
→ Internal Qur'an Record

Alternative approved source
→ Source Adapter
→ Same Internal Qur'an Record

---

# 22. Production Gate

No Qur'an data may enter final video production unless:

Arabic = VERIFIED
Translation = VERIFIED
Ayah sequence = VERIFIED
Audio = VERIFIED
Audio timing = VERIFIED
Source rights = VERIFIED

---

# 23. Final Safety Rule

Unknown source:

STOP

Unknown translation:

STOP

Unknown audio rights:

STOP

Arabic mismatch:

STOP

Ayah mismatch:

STOP

Translation mismatch:

STOP

Audio mismatch:

STOP

When uncertain:

DO NOT PUBLISH.
