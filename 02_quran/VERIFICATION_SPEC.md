# Qur'an Verification Specification

## Purpose

This specification defines the minimum verification required before Qur'an
content can be used in a production video.

The system must prefer stopping over publishing uncertain or mismatched
religious content.

---

# 1. Verification Model

Every Qur'an video must pass:

SOURCE
↓
STRUCTURE CHECK
↓
ARABIC CHECK
↓
TRANSLATION CHECK
↓
SEQUENCE CHECK
↓
AUDIO CHECK
↓
MANIFEST CHECK
↓
HUMAN REVIEW

Only verified content may proceed to video rendering.

---

# 2. Source Verification

The Qur'an content must originate from an approved source.

Primary structured source:

Quran Foundation Content API

Independent Arabic cross-check:

Tanzil

The source used for each field must be recorded.

---

# 3. Surah Verification

Verify:

- Surah number
- Surah name
- Surah identity

Example:

22 = Al-Hajj

The system must reject the content if the Surah number and Surah name do
not correspond.

---

# 4. Ayah Verification

Every ayah must contain:

- Surah number
- Ayah number
- Verse key
- Arabic text
- Translation reference

Example:

22:13

The verse key must match the Surah number and Ayah number.

---

# 5. Arabic Text Verification

The production Arabic text must be obtained from the approved Qur'an source.

The system must not generate Arabic Qur'an text using an AI model.

The Arabic text must be compared against the independent verification source.

The system must reject content if:

- characters differ unexpectedly
- ayah boundaries differ
- words are missing
- words are added
- words are rearranged
- the wrong ayah is present

---

# 6. Script Policy

The project must choose one approved Arabic script for final video rendering.

Initial preferred script:

Uthmani

The exact source representation must be recorded.

The system must not silently switch between:

- Uthmani
- Imla'i
- IndoPak
- Tajweed markup
- simplified Arabic

without explicit configuration.

---

# 7. Translation Verification

Every English translation must have:

- translator
- source
- version
- source identifier
- corresponding verse key

The translation must correspond to the exact ayah.

The system must reject content if the translation belongs to another ayah.

AI-generated translations must not automatically become final published
translations.

---

# 8. Verse Range Verification

When a video specifies:

START = 22:13
END = 22:16

the expected sequence is:

22:13
22:14
22:15
22:16

The system must verify:

- first ayah is correct
- last ayah is correct
- every intermediate ayah exists
- no ayah is duplicated
- no ayah is skipped
- order is ascending

---

# 9. Cross-Surah Rule

A single video must not cross from one Surah into another unless the
manifest explicitly allows a multi-Surah video.

Default:

MULTI-SURAH VIDEO = NOT ALLOWED

If enabled later, every Surah boundary must be explicitly recorded.

---

# 10. Audio Verification

The recitation must correspond to the exact selected ayah range.

For each ayah:

- reciter identity must be known
- audio source must be known
- audio rights must be verified
- audio must correspond to the ayah
- ayah boundaries must be known or reliably detected

If timing cannot be verified:

AUDIO SYNC = FAIL

The system must not guess the ayah timing.

---

# 11. Display Synchronization

While ayah 22:13 is being recited, the screen must display:

SURAH 22 • AL-HAJJ • AYAH 13

Arabic text for 22:13

English translation for 22:13

When 22:14 begins, the screen must switch to:

SURAH 22 • AL-HAJJ • AYAH 14

Arabic text for 22:14

English translation for 22:14

The Arabic, translation and indicator must always refer to the same ayah.

---

# 12. Visual Verification

Visual content must satisfy:

- no visible human faces
- no prohibited religious depictions
- no prophets depicted
- no companions depicted
- no angels depicted as human figures
- no misleading literal depiction of unseen realities
- visual is appropriate to the meaning/theme
- visual source or generation method is recorded
- publishing rights are verified

---

# 13. Manifest Verification

The actual video manifest must agree with the verified Qur'an data.

Check:

- Surah number
- Surah name
- start ayah
- end ayah
- ayah list
- translation source
- reciter
- audio source
- presentation settings

Any mismatch = FAIL.

---

# 14. Video Verification

Before approval:

- correct aspect ratio
- correct resolution
- Arabic visible
- English translation visible
- verse indicator visible
- correct ayah number
- correct Surah number
- no text clipping
- Arabic characters render correctly
- English text is readable
- audio exists
- audio is synchronized
- no corrupted frames
- no unexpected black frames
- no unintended watermark
- no visible faces

---

# 15. Failure Policy

Any failed critical check prevents publishing.

Critical failures include:

WRONG ARABIC
WRONG AYAH
WRONG SURAH
WRONG ORDER
WRONG TRANSLATION
UNVERIFIED AUDIO
AUDIO MISMATCH
BAD SYNCHRONIZATION
FACE DETECTED
TEXT CORRUPTION
MISSING VERSE INDICATOR

Result:

DO NOT PUBLISH

---

# 16. Verification Status

Possible states:

UNVERIFIED

CHECKING

VERIFIED

FAILED

HUMAN_REVIEW

APPROVED

REJECTED

PUBLISHED

---

# 17. No Silent Corrections

The system must not silently modify verified Qur'an content.

If a correction is required:

1. Record the failure.
2. Record the reason.
3. Correct the source/configuration.
4. Re-run verification.
5. Create a new verification result.

---

# 18. Verification Record

Every production video must eventually have a verification record containing:

- content ID
- verification date
- source versions
- Qur'an source
- Arabic verification result
- translation verification result
- sequence verification result
- audio verification result
- visual verification result
- video verification result
- human approval status

---

# 19. Golden Rule

IF THERE IS DOUBT:

STOP.

DO NOT GUESS.

DO NOT AUTO-PUBLISH.

Accuracy is more important than completing the scheduled post.
