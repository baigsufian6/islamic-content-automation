# Islamic Content Automation — Rendering Requirements

## 1. Purpose

Define the technical requirements for professional cloud-based video
generation.

Rendering must not require the user's local computer.

---

# 2. Rendering Environment

The renderer must run in the cloud.

The renderer must support:

- FFmpeg or equivalent professional video processing
- Arabic text rendering
- Unicode text
- Right-to-left Arabic layout
- English text
- Audio synchronization
- Image/video backgrounds
- Transitions
- Motion effects
- 1080x1920 vertical output
- 1920x1080 horizontal output where required
- High-quality H.264 output
- High-quality AAC audio
- Subtitle/text overlays
- Verse-level timing

---

# 3. Primary Social Format

Primary format:

1080 x 1920

Aspect ratio:

9:16

Purpose:

Instagram Reels
Facebook Reels
YouTube Shorts
X vertical video where appropriate

---

# 4. Secondary Format

Secondary format:

1920 x 1080

Aspect ratio:

16:9

Purpose:

YouTube long-form where appropriate.

---

# 5. Frame Rate

Preferred default:

30 FPS

The system must use a consistent frame rate within a video.

---

# 6. Video Codec

Preferred:

H.264 / AVC

The exact encoder settings must be tested for:

- quality
- file size
- rendering time
- platform compatibility

---

# 7. Audio Codec

Preferred:

AAC

Audio must remain synchronized with the video.

---

# 8. Arabic Text

The renderer must correctly display:

- Arabic characters
- Arabic diacritics
- Qur'anic marks where required
- Right-to-left text
- Connected Arabic glyphs

The renderer must NOT:

- reverse Arabic text
- corrupt Arabic glyphs
- remove required marks
- substitute unsupported characters
- alter Qur'an text

---

# 9. Ayah Display

Each ayah is displayed as a complete synchronized unit.

Each ayah frame contains:

SURAH NUMBER
+
SURAH NAME
+
AYAH NUMBER
+
ARABIC TEXT
+
ENGLISH TRANSLATION

Example:

SURAH 22 • AL-HAJJ • AYAH 13

[Arabic ayah]

[English translation]

---

# 10. Same-Frame Requirement

Arabic and English translation must appear simultaneously.

The system must NOT create:

Slide 1 = Arabic
Slide 2 = English

Instead:

One ayah presentation = Arabic + English together.

---

# 11. Ayah Transition

When the recitation moves from:

22:13

to:

22:14

the displayed content must transition accordingly.

The new frame must show:

SURAH 22 • AL-HAJJ • AYAH 14

along with the exact Arabic and English translation for 22:14.

---

# 12. Timing

Ayah transitions must use verified timing data.

The renderer must not assume every ayah has identical duration.

The timing record must identify:

ayah
start_time
end_time

---

# 13. Backgrounds

Backgrounds may include:

- Qur'an manuscripts
- Qur'an calligraphy
- mosques
- Islamic architecture
- masjid interiors
- courtyards
- arches
- lanterns
- Islamic geometric patterns
- historical architecture
- deserts
- mountains
- oceans
- rain
- clouds
- sky
- stars
- water
- atmospheric environments
- abstract cinematic environments
- slow-motion cinematic footage

Nature is NOT required.

---

# 14. Human Imagery

Default:

NO PEOPLE

Absolutely prohibited:

- visible human faces
- identifiable people
- prophets
- companions
- angels depicted as humans
- religious figures depicted as humans

Faceless human silhouettes may only be used if explicitly approved by the
visual rules and do not create a religious misrepresentation.

Default production remains:

NO PEOPLE.

---

# 15. Unseen Realities

The renderer must not present AI-generated imagery as a literal depiction
of:

- Jannah
- Jahannam
- angels
- prophets
- unseen religious realities

Abstract atmospheric visuals may be used to communicate emotional tone.

---

# 16. Visual Motion

Preferred motion should be subtle and cinematic.

Examples:

- slow camera movement
- slow zoom
- gentle parallax
- atmospheric particles
- natural movement
- subtle light movement
- slow transitions

Avoid:

- flashy effects
- excessive particles
- rapid zooms
- distracting transitions
- generic AI slideshow effects

---

# 17. Typography

Typography must be:

- elegant
- readable
- consistent
- mobile-friendly
- professionally spaced

Arabic typography receives visual priority.

English translation must remain readable without overwhelming the Arabic.

---

# 18. Safe Text Area

Text must remain inside platform-safe areas.

No critical text may be placed near:

- screen edges
- bottom navigation areas
- platform overlays
- top interface areas

---

# 19. Background Readability

The renderer must automatically or manually ensure sufficient contrast
between text and background.

Possible techniques:

- dark overlay
- gradient overlay
- blur
- vignette
- controlled brightness
- controlled contrast

The Qur'an text must always remain readable.

---

# 20. Render Quality

Final video must be checked for:

- resolution
- frame integrity
- audio integrity
- synchronization
- text clipping
- Arabic rendering
- translation visibility
- verse indicator
- correct ayah
- correct Surah
- correct timing
- no unexpected black frames

---

# 21. Temporary Files

Rendering produces temporary files.

Temporary files must be stored separately from permanent assets.

Temporary files must automatically expire.

Default:

24 hours

Failed jobs must also trigger cleanup.

---

# 22. Rendering Job Lifecycle

CREATE JOB

↓

VALIDATE MANIFEST

↓

FETCH VERIFIED DATA

↓

FETCH REQUIRED MEDIA

↓

CREATE TIMELINE

↓

RENDER VIDEO

↓

RUN VIDEO QUALITY CHECKS

↓

UPLOAD MASTER

↓

VERIFY UPLOAD

↓

DELETE TEMPORARY FILES

↓

RECORD RESULT

---

# 23. Failed Jobs

If rendering fails:

1. Record failure.
2. Preserve manifest.
3. Preserve verification records.
4. Delete temporary rendering files.
5. Retry only within configured limits.
6. Prevent duplicate publication.

---

# 24. Cost Protection

Rendering must not silently create paid usage.

The system must track:

- rendering minutes
- storage usage
- artifact usage
- media transfer
- API requests

If a configured free-tier safety threshold is reached:

STOP NEW RENDER JOBS.

No automatic paid upgrade.

---

# 25. Resource Efficiency

The renderer should minimize:

- unnecessary re-encoding
- duplicate downloads
- duplicate renders
- excessive resolution
- unnecessary frame extraction
- unnecessary temporary copies

Reuse approved assets where licensing permits.

---

# 26. Master Output

Each approved video should have:

- final video file
- content ID
- manifest ID
- render version
- verification status
- creation timestamp

---

# 27. No Local Rendering

The user's computer must never be required for final production.

Local software may only be used optionally for administration or inspection.

The production pipeline itself is cloud-only.

---

# 28. Professional Standard

The final output should look like professionally produced Islamic media.

The goal is NOT:

"AI-generated video."

The goal is:

"Beautiful, restrained, accurate Islamic educational media."

---

# 29. Core Rule

Religious accuracy and readability take priority over visual effects.

A beautiful video with incorrect Qur'an information is a failed video.
