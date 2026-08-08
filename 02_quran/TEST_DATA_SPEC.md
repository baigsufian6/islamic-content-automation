# Qur'an Automation — Test Data Specification

## Purpose

This test dataset exists only to test software and video-processing logic.

It is NOT Qur'an text.

It must NEVER be published as Islamic religious content.

---

# 1. Test Identity

Every test record must clearly identify itself as:

TEST DATA

The production system must reject test records for publishing.

---

# 2. Test Surah

Test Surah Number:

999

Test Surah Name:

TEST SURAH

This is fictional and does not represent a Qur'anic Surah.

---

# 3. Test Ayahs

The initial test sequence contains four fictional records:

999:1
999:2
999:3
999:4

---

# 4. Test Arabic Field

Use the following clearly fictional placeholder:

[TEST ARABIC 999:1]

[TEST ARABIC 999:2]

[TEST ARABIC 999:3]

[TEST ARABIC 999:4]

These strings are placeholders only.

They must never be presented as Qur'an.

---

# 5. Test English Field

Use:

[TEST TRANSLATION 999:1]

[TEST TRANSLATION 999:2]

[TEST TRANSLATION 999:3]

[TEST TRANSLATION 999:4]

These strings are placeholders only.

---

# 6. Test Audio

No real Qur'an recitation is required.

The audio-testing system may use:

- generated test tones
- silence
- synthetic speech clearly identified as test material
- other non-religious test audio

Test audio must never be presented as Qur'an recitation.

---

# 7. Test Timing

Use fictional timing:

999:1
START = 0.0 seconds
END = 4.0 seconds

999:2
START = 4.0 seconds
END = 8.0 seconds

999:3
START = 8.0 seconds
END = 12.0 seconds

999:4
START = 12.0 seconds
END = 16.0 seconds

---

# 8. Test Display

At 999:1:

SURAH 999 • TEST SURAH • AYAH 1

[TEST ARABIC 999:1]

[TEST TRANSLATION 999:1]

At 999:2:

SURAH 999 • TEST SURAH • AYAH 2

[TEST ARABIC 999:2]

[TEST TRANSLATION 999:2]

At 999:3:

SURAH 999 • TEST SURAH • AYAH 3

[TEST ARABIC 999:3]

[TEST TRANSLATION 999:3]

At 999:4:

SURAH 999 • TEST SURAH • AYAH 4

[TEST ARABIC 999:4]

[TEST TRANSLATION 999:4]

---

# 9. Tests Required

The system must eventually prove that:

1. 999:1 appears first.
2. 999:2 appears second.
3. 999:3 appears third.
4. 999:4 appears fourth.
5. The verse indicator changes at the correct boundary.
6. Arabic and English change together.
7. No previous ayah remains visible after the transition.
8. No ayah is skipped.
9. No ayah is duplicated.
10. The video ends after the final test ayah.
11. Test content cannot be published.

---

# 10. Deliberate Failure Tests

The quality-control system must also be tested against invalid data.

Examples:

WRONG ORDER:

999:1
999:3
999:2
999:4

Expected result:

FAIL

---

MISSING AYAH:

999:1
999:2
999:4

Expected result:

FAIL

---

DUPLICATE AYAH:

999:1
999:2
999:2
999:3

Expected result:

FAIL

---

WRONG TRANSLATION KEY:

Arabic = 999:2
Translation = 999:3

Expected result:

FAIL

---

WRONG VERSE INDICATOR:

Actual content = 999:2
Displayed indicator = 999:3

Expected result:

FAIL

---

# 11. Production Safety

The production system must reject any record containing:

test_data = true

or:

surah_number = 999

or:

status = TEST

or another equivalent test marker.

---

# 12. Principle

Test everything with fictional data first.

Only verified religious data may enter production.
