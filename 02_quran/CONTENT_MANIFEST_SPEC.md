# Qur'an Video Content Manifest Specification

## 1. Purpose

The Content Manifest is the authoritative instruction set for one Qur'an
video.

The renderer must never independently choose Qur'an content.

The manifest defines exactly what may be rendered.

---

# 2. Manifest Identity

Every manifest must contain:

- manifest_id
- content_type
- created_at
- manifest_version
- status

Example:

manifest_id:
quran-22-13-16-001

content_type:
quran

manifest_version:
1

status:
DRAFT

---

# 3. Qur'an Range

Every Qur'an video must explicitly specify:

- surah_number
- surah_name
- start_ayah
- end_ayah

Example:

surah_number:
22

surah_name:
Al-Hajj

start_ayah:
13

end_ayah:
16

---

# 4. Sequence Rule

The requested range is inclusive.

Example:

start_ayah = 13
end_ayah = 16

Required sequence:

22:13
22:14
22:15
22:16

The system must verify the complete sequence before rendering.

---

# 5. No Random Selection

The renderer must never:

- randomly select ayahs
- skip ayahs
- reorder ayahs
- duplicate ayahs
- combine unrelated ayahs
- select verses based only on an AI-generated topic

---

# 6. Ayah Records

The manifest must contain an ordered ayah list.

Each record must contain:

- verse_key
- surah_number
- ayah_number
- sequence_index

Example:

{
  "verse_key": "22:13",
  "surah_number": 22,
  "ayah_number": 13,
  "sequence_index": 1
}

---

# 7. Sequence Verification

Before rendering:

EXPECTED_SEQUENCE

must equal:

MANIFEST_SEQUENCE

If they differ:

VERIFICATION_FAILURE

The render job must stop.

---

# 8. Arabic Source

The manifest must record:

- source
- source identifier
- script
- source version where available
- retrieval timestamp

Example:

arabic_source:
Quran Foundation

arabic_script:
Uthmani

---

# 9. Translation Source

The manifest must record:

- translator
- source
- translation key
- version

Example:

translator:
Saheeh International

source:
QuranEnc

translation_key:
english_saheeh

translation_version:
[APPROVED VERSION]

---

# 10. Translation Integrity

The translation text must be obtained from the approved source.

The manifest must not contain AI-rewritten translation text.

The production system must reject a translation if its verse key does not
match the Arabic verse key.

---

# 11. Audio

The manifest must record:

- reciter
- audio source
- recording/version
- rights status
- timing source

Preferred reciter:

Saad Al-Ghamdi

However:

audio_rights_status must equal:

VERIFIED

before production.

---

# 12. Audio Timing

Each ayah must have verified timing.

Required fields:

- verse_key
- start_time
- end_time

Timing must be associated with the exact audio recording/version.

---

# 13. Visual Requirements

The manifest must contain visual guidance.

Examples:

visual_theme:
mercy

visual_categories:
- rain
- dawn
- mosque architecture
- flowing water

human_presence:
none

faces:
prohibited

religious_figure_depiction:
prohibited

---

# 14. Visual Selection

Visuals must support the meaning and emotional tone of the content.

Visuals must not claim to literally depict unseen religious realities.

---

# 15. Video Configuration

Required fields:

aspect_ratio:
9:16

width:
1080

height:
1920

frame_rate:
30

video_codec:
H.264

audio_codec:
AAC

---

# 16. Text Configuration

Required:

show_surah_reference:
true

show_ayah_number:
true

show_arabic:
true

show_translation:
true

same_frame:
true

---

# 17. Text Reference

The reference displayed for each ayah must correspond to the actual
ayah being displayed.

Example:

Actual:
22:13

Displayed:
SURAH 22 • AL-HAJJ • AYAH 13

Mismatch:

VERIFICATION_FAILURE

---

# 18. Verification Status

A manifest may have:

DRAFT

VALIDATING

VERIFIED

RENDERING

QC_PENDING

APPROVED

PUBLISHED

FAILED

REJECTED

---

# 19. Production Gate

Rendering is allowed only when:

status = VERIFIED

and:

arabic_verified = true

translation_verified = true

sequence_verified = true

audio_verified = true

audio_rights_verified = true

visuals_verified = true

---

# 20. Human Approval

Before automatic publication:

human_approval = true

must be present.

Until the system has passed extensive production testing,
automatic publication remains disabled.

---

# 21. Immutability

After verification, the manifest must not be silently modified.

If any production-critical field changes:

The manifest must return to:

DRAFT

and undergo verification again.

---

# 22. Reproducibility

A published video must be traceable to:

- manifest ID
- manifest version
- source versions
- translation version
- audio version
- render version
- visual asset IDs

---

# 23. Failure Principle

If required information is unknown:

STOP.

If a source is unavailable:

STOP.

If sequence is wrong:

STOP.

If translation does not match:

STOP.

If audio rights are unknown:

STOP.

If Arabic verification fails:

STOP.

Never guess.

---

# 24. Core Principle

The manifest controls the video.

The AI does not control the Qur'an content.

The renderer executes verified instructions.
