# Islamic Content Automation — Cloud Architecture

## 1. Core Principle

The entire production system must operate in the cloud.

The user's personal computer must NOT be required for:

- video rendering
- scheduled automation
- data processing
- file storage
- publishing
- quality-control processing
- server hosting

The user's device is only an administration/approval interface.

---

# 2. Architecture

The initial architecture consists of:

GitHub
+
Cloudflare
+
Approved external APIs
+
Approved media/rendering infrastructure

---

# 3. GitHub

GitHub is the source-control and project-documentation layer.

GitHub stores:

- source code
- configuration templates
- documentation
- rulebooks
- specifications
- test definitions
- version history

GitHub must NOT store:

- API secrets
- passwords
- access tokens
- private credentials
- large production video archives
- copyrighted media unless explicitly permitted

---

# 4. Cloudflare

Cloudflare may provide:

- serverless API endpoints
- lightweight automation
- scheduling
- workflow coordination
- metadata storage
- object storage
- queues
- cleanup automation

Cloudflare Workers must NOT be used for heavy video rendering.

---

# 5. Object Storage

Cloud object storage is used for:

- approved source assets
- temporary processing files
- final masters
- thumbnails
- generated visual assets
- publication records where appropriate

Storage must have explicit lifecycle rules.

---

# 6. Temporary Storage

Temporary files include:

- intermediate renders
- extracted frames
- temporary audio files
- preview videos
- failed render outputs
- processing caches
- temporary image assets

Temporary files must have automatic expiration.

Default planned retention:

24 hours

The exact retention period may be adjusted after production testing.

---

# 7. Permanent Storage

Permanent records include:

- verified content manifests
- source records
- verification records
- human approval records
- publication records
- final approved master assets where required
- system configuration
- project documentation

Permanent records must not be deleted by temporary cleanup jobs.

---

# 8. Video Rendering

Heavy video rendering requires a separate compute layer.

The rendering layer must:

- be cloud-based
- support FFmpeg or equivalent
- support Arabic text rendering
- support high-quality video output
- support audio synchronization
- support automatic cleanup

No local rendering is allowed.

---

# 9. Rendering Strategy

Rendering should be job-based.

Example:

CREATE JOB
↓
VALIDATE MANIFEST
↓
FETCH REQUIRED ASSETS
↓
RENDER
↓
RUN QUALITY CHECKS
↓
UPLOAD RESULT
↓
VERIFY RESULT
↓
DELETE TEMPORARY FILES
↓
MARK JOB COMPLETE

---

# 10. Storage Protection

The system must prevent storage exhaustion.

Required protections:

- automatic cleanup
- temporary file expiration
- failed-job cleanup
- duplicate detection
- file-size monitoring
- storage usage monitoring
- retention policies
- cleanup logs

---

# 11. Cost Protection

The system must not intentionally exceed free-tier limits.

Required protections:

- usage monitoring
- job limits
- render limits
- storage limits
- API request limits
- automatic pause on abnormal usage
- no paid upgrade without explicit user approval

If a free-tier limit is reached:

STOP NEW JOBS

Do not silently create charges.

---

# 12. Secrets

Secrets must be stored using secure cloud secret/environment-variable
facilities.

Never commit secrets to GitHub.

Examples:

- Quran API credentials
- social-media API credentials
- storage credentials
- publishing tokens

---

# 13. Separation of Responsibilities

GitHub:

SOURCE CONTROL
DOCUMENTATION
VERSIONING

Cloudflare:

AUTOMATION
CONTROL
QUEUES
METADATA
LIGHTWEIGHT SERVICES

Object Storage:

MEDIA
ASSETS
TEMPORARY FILES
ARCHIVES

Rendering Compute:

VIDEO GENERATION
AUDIO PROCESSING
FRAME PROCESSING

External APIs:

QUR'AN DATA
SOCIAL PUBLISHING
OTHER APPROVED SERVICES

---

# 14. Provider Independence

No single cloud provider should be treated as the permanent owner of the
entire architecture.

Components should be replaceable where practical.

Example:

Rendering Provider A
→ Rendering Provider B

without redesigning:

- Qur'an verification
- content manifests
- video templates
- quality control
- publishing records

---

# 15. Failure Handling

If any external service fails:

1. Record the failure.
2. Preserve the manifest.
3. Preserve verification records.
4. Clean temporary files.
5. Retry only according to safe retry limits.
6. Do not duplicate-publish.
7. Do not silently switch providers.

---

# 16. Publishing Safety

Publishing must only happen after:

MANIFEST VERIFIED
+
VIDEO VERIFIED
+
QUALITY CONTROL PASSED
+
HUMAN APPROVAL

Until the system has been extensively tested, automatic publishing remains
disabled.

---

# 17. Free-First Principle

Use free/open-source resources whenever they meet the requirements.

Before using a paid resource:

1. Check for a legitimate free alternative.
2. Check current free-tier limits.
3. Check licence terms.
4. Check whether the free tier is suitable for long-term operation.

Never assume a service will remain free forever.

---

# 18. Cost Ceiling

The system must be designed so that exceeding a free-tier allowance does
not automatically create a financial charge.

Where a provider supports spending limits or usage controls, enable them.

---

# 19. Long-Term Principle

The architecture should survive:

- provider changes
- API changes
- rendering provider changes
- source changes
- template updates
- social-platform changes

without losing the project's religious verification records or published
content history.
