# Islamic Content Automation — Master Rulebook

## 1. Purpose

This project creates professional Islamic educational and reminder content for
YouTube, Instagram, Facebook, and X.

The primary objectives are:

1. Accuracy
2. Religious integrity
3. Respectful presentation
4. Professional production quality
5. Long-term reliability
6. Cloud-only operation
7. Zero-cost operation where realistically possible

Accuracy always takes priority over speed or automation.

---

# 2. Qur'an Rules

## 2.1 Authoritative source

Qur'an Arabic text must come from a verified structured Qur'an source.

The system must NEVER generate Qur'an Arabic text using an AI language model.

## 2.2 Verse identity

Every Qur'an ayah must have:

- Surah number
- Surah name
- Ayah number
- Exact Arabic text
- Approved English translation
- Verified source reference

## 2.3 Verse order

When multiple ayat are used, they must remain in exact Qur'anic order.

Example:

22:13
22:14
22:15
22:16

The system must never rearrange, skip, or randomly combine verses.

## 2.4 No random verse selection

The AI must not independently select random Qur'anic verses merely because
they appear relevant to a topic.

A Qur'an video must have an explicitly defined Surah and ayah range.

## 2.5 Complete verse integrity

An ayah must not be altered, paraphrased, truncated, rearranged, or
combined with another ayah in a way that changes its meaning.

## 2.6 On-screen Qur'an presentation

Arabic Qur'an text and its English translation must appear on the SAME
video frame while that ayah is being recited.

They must NOT be separated into different slides/pages.

## 2.7 Verse indicator

Every ayah displayed in the video must show:

SURAH [number] • [Surah name] • AYAH [number]

Example:

SURAH 22 • AL-HAJJ • AYAH 13

When the next ayah begins, the indicator must change to the exact next ayah.

## 2.8 Synchronization

The Arabic text, English translation, verse indicator, and recitation must
refer to the same ayah.

The system must not rely on arbitrary fixed timers when verified ayah timing
is available.

---

# 3. Translation Rules

## 3.1 Verified translation only

English Qur'an translations must come from an identified and approved source.

## 3.2 No AI-generated translation for final publication

AI may assist with explanation or comparison during preparation, but an
AI-generated translation must not automatically become the published
Qur'an translation.

## 3.3 Translation traceability

Every published translation must be traceable to its source and version.

---

# 4. Recitation Rules

## 4.1 Reciter

The planned primary reciter is:

Shaykh Saad Al-Ghamdi

## 4.2 Licensing

Only audio that is legally permitted for the intended publishing use may be
used.

The system must not download or reuse copyrighted audio from arbitrary
websites or social-media videos simply because it is available online.

## 4.3 Synchronization

The recitation used for a video must correspond to the exact ayat specified
in the content manifest.

---

# 5. Visual Rules

## 5.1 No faces

Published videos must not contain visible human faces.

## 5.2 Default visual policy

The preferred visual approach is no people at all.

Possible visuals include:

- Qur'an manuscripts
- Qur'an calligraphy
- Islamic architecture
- Mosques
- Masjid interiors
- Islamic geometric patterns
- Historical architecture
- Courtyards
- Doors and arches
- Lanterns
- Mountains
- Deserts
- Oceans
- Rain
- Clouds
- Stars
- Sky
- Natural landscapes
- Abstract cinematic environments
- Atmospheric light
- Shadows
- Fire-like abstract environments
- Storms
- Water
- Other respectful cinematic environments

## 5.3 Religious figures

Do not visually depict prophets, companions, angels, or other unseen
religious figures.

## 5.4 Meaning-sensitive visuals

Visuals should support the emotional and thematic meaning of the selected
ayahs.

The system must not simply select random footage because it contains a
keyword.

## 5.5 No fabricated religious claims

Visuals must not imply that an AI-generated scene is a literal depiction of
Jannah, Jahannam, angels, prophets, or other unseen realities.

---

# 6. Video Design Rules

## 6.1 Professional appearance

The final video must look like professional Islamic media content, not a
generic AI slideshow.

## 6.2 Arabic and English together

Arabic Qur'an text and English translation must appear simultaneously.

## 6.3 Verse-by-verse presentation

The displayed content must change when the recitation changes from one ayah
to the next.

## 6.4 Typography

Arabic typography must be highly readable and respectful.

English translation must remain readable on mobile devices.

## 6.5 No excessive effects

Avoid:

- excessive transitions
- distracting animations
- unnecessary zoom effects
- flashing effects
- cheesy AI effects
- excessive text movement

## 6.6 Quality

The final output should be high-quality and suitable for professional
social-media publishing.

---

# 7. Hadith Rules

## 7.1 Separate Qur'an and Hadith pipelines

Hadith content must use a separate verification pipeline.

## 7.2 Source

Every published Hadith must identify its source.

## 7.3 Authenticity

Hadith authenticity must not be invented or guessed by AI.

## 7.4 Explanation

The system must clearly distinguish:

- Hadith text
- Translation
- Source
- Authenticity/grade information
- Scholarly explanation
- Editorial reflection

## 7.5 Sunday carousel

The planned weekly Hadith content is a detailed Sunday carousel.

---

# 8. Publishing Rules

The planned weekly schedule is:

Monday    — Qur'an
Tuesday   — Qur'an
Wednesday — Qur'an
Thursday  — Qur'an
Friday    — Qur'an
Saturday  — Preparation / quality control
Sunday    — Hadith carousel

## 8.1 Multi-platform

Approved content may be published to:

- YouTube
- Instagram
- Facebook
- X

## 8.2 One verified master

The system should create one verified master asset and generate platform-specific
versions from it where necessary.

## 8.3 No accidental duplicate publishing

The system must track published content and platform publication IDs.

A successful publication must be recorded.

---

# 9. Quality Control

No content should automatically publish unless required validation checks
have passed.

Checks should include:

- Qur'an source
- Arabic text
- Surah number
- Surah name
- Ayah number
- Ayah order
- Translation
- Audio source
- Audio/ayah synchronization
- Visual compliance
- No faces
- Video resolution
- Video integrity
- Audio integrity
- Correct caption/metadata
- Platform compatibility

Religious accuracy takes priority over automation.

---

# 10. Human Approval

The production system should support:

DRAFT → QUALITY CONTROL → HUMAN APPROVAL → PUBLISH

Automatic publication should only be enabled after the verification system
has been thoroughly tested.

---

# 11. Cloud-Only Rule

No production process should require the user's local computer.

The system should run using cloud services.

The user's computer should not be required to:

- render video
- store production archives
- run FFmpeg
- run Python
- host servers
- run scheduled jobs

---

# 12. Cost Rule

The project should use free/open-source software and free cloud tiers
where realistically available.

No paid service should be introduced without first checking whether a
legitimate free alternative exists.

The system must never intentionally bypass a service's limits or terms.

---

# 13. Storage Management

Storage is a controlled resource.

Temporary files must NOT accumulate indefinitely.

The system must distinguish between:

### PERMANENT

- Source manifests
- Verification records
- Published metadata
- Final approved masters where appropriate
- Publication records
- Configuration
- Documentation

### TEMPORARY

- Intermediate renders
- Extracted frames
- Preview videos
- Failed renders
- Temporary audio conversions
- Temporary image files
- Processing caches
- Duplicate exports

Temporary files must have an automatic cleanup policy.

Failed jobs must also be cleaned up.

The system must not delete permanent source records during cleanup.

Cleanup must be logged.

---

# 14. Versioning

Important production components must be versioned.

Examples:

- Qur'an source/version
- Translation source/version
- Hadith source/version
- Audio source
- Video template version
- Font version
- Rendering configuration
- Visual rules
- Quality-control rules

Previously published content must not silently change when the system is
updated.

---

# 15. AI Rules

AI may assist with:

- planning
- metadata
- visual concepts
- workflow automation
- quality-control assistance
- organization
- captions
- technical operations

AI must NOT be treated as the final authority for:

- Qur'an Arabic
- Qur'an verse selection
- Qur'an translation
- Hadith authenticity
- religious rulings

AI-generated religious text must never automatically become published
religious source material.

---

# 16. Failure Policy

When the system is uncertain, it must STOP.

It is better to produce no video than to publish incorrect Islamic content.

Examples:

UNKNOWN SOURCE → STOP
MISSING AYAH → STOP
WRONG ORDER → STOP
AUDIO MISMATCH → STOP
TRANSLATION MISMATCH → STOP
QUALITY CHECK FAILURE → STOP
UNCERTAIN HADITH AUTHENTICITY → STOP

---

# 17. Core Principle

ACCURACY > RELIGIOUS INTEGRITY > QUALITY > RELIABILITY > AUTOMATION > SPEED

When in doubt, stop and verify.
