# ARCHITECTURE.md

# AI Content Studio

## Technical Architecture Specification

Version: 1.0

> **Scope note.** This document is the target architecture for the config-driven
> `modules/` build-out. The pipeline that actually ships videos today lives in
> `pipeline/`, and **[RUNBOOK.md](./RUNBOOK.md) is the source of truth** for it —
> including the encode path (picture encoded twice, audio once), the shared encode
> profile in `pipeline/encode.py`, measured colour correction, audio mastering at
> -14 LUFS, and the automated QC pass. Where the two disagree, the RUNBOOK wins.

---

# 1. System Overview

AI Content Studio is an automated video processing platform that converts long-form StreamYard livestream recordings into professional short-form developer content.

The system uses:

* Computer Vision
* OCR
* Speech AI
* Video Processing
* Generative AI
* Automated Content Optimization

The architecture follows a modular pipeline design.

---

# 2. High-Level Architecture

```
                    INPUT VIDEO

                         |
                         |

                Video Analyzer

                         |
                         |

        +----------------+----------------+

        |                                 |

 OCR Question Detection          Audio Extraction

        |                                 |

        |                                 |

 Question Timeline              Whisper Transcription


        +----------------+----------------+

                         |

                 Clip Generator

                         |

        +----------------+----------------+

        |                                 |

 Video Enhancement              AI Content Analysis


        |                                 |

        |                                 |

 Caption Generator             Hook Detection


        |                                 |

        +----------------+----------------+

                         |

                Social Media Renderer

                         |

                         |

              Review Dashboard

                         |

                         |

              Approved Content
```

---

# 3. Recommended Project Structure

```
AI-Content-Studio/

│

├── CLAUDE.md

├── ARCHITECTURE.md

│

├── app/

│   ├── main.py

│   ├── config.py

│   └── pipeline.py

│

├── modules/

│

│   ├── video/

│   │   ├── analyzer.py

│   │   ├── clipper.py

│   │   └── enhancer.py

│

│   ├── ocr/

│   │   ├── detector.py

│   │   └── extractor.py

│

│   ├── speech/

│   │   ├── whisper.py

│   │   └── subtitles.py

│

│   ├── ai/

│   │   ├── hooks.py

│   │   ├── scoring.py

│   │   └── metadata.py

│

│   ├── captions/

│   │   ├── renderer.py

│   │   └── styles.py

│

│   ├── social/

│   │   ├── vertical.py

│   │   └── export.py

│

│   └── dashboard/

│       └── generator.py


├── config/

│   ├── settings.yaml

│   └── branding.yaml


├── INPUT/

├── OUTPUT/

├── READY_TO_UPLOAD/

├── TEMP/

└── logs/

```

---

# 4. Processing Pipeline

Main execution:

```
python main.py process
```

Execution:

```
Load Configuration

        ↓

Validate Input Video

        ↓

Analyze Video Metadata

        ↓

Run OCR Detection

        ↓

Generate Question Timeline

        ↓

Extract Clips

        ↓

Generate Transcript

        ↓

Analyze Hook

        ↓

Generate Captions

        ↓

Enhance Video

        ↓

Create Vertical Version

        ↓

Generate Metadata

        ↓

Generate Dashboard

        ↓

Complete
```

---

# 5. Video Analyzer Module

Location:

```
modules/video/analyzer.py
```

Responsibilities:

Extract:

* Resolution
* FPS
* Duration
* Audio availability
* Codec information

Output:

```json
{
 "duration":5400,
 "width":1920,
 "height":1080,
 "fps":30
}
```

---

# 6. OCR Detection Engine

Location:

```
modules/ocr/detector.py
```

Purpose:

Detect StreamYard question overlay.

Input:

```
livestream.mp4
```

Output:

```
questions.json
```

Example:

```json
[
 {
  "question":
  "Should I learn Java in 2026?",

  "start":
  "00:12:15",

  "end":
  "00:15:40",

  "confidence":
  0.94
 }
]
```

---

# 7. OCR Optimization Strategy

Do not process every frame.

Optimization:

1. Extract frames every 0.5-1 second.
2. Run OCR.
3. Detect overlay changes.
4. Increase sampling when overlay appears.

This reduces processing time.

---

# 8. Clip Generator

Location:

```
modules/video/clipper.py
```

Input:

```
questions.json
```

Output:

```
Question_001/clip.mp4
```

Uses:

FFmpeg.

Requirements:

* Preserve quality
* Preserve audio sync
* Fast processing

---

# 9. Speech Processing

Location:

```
modules/speech/
```

Engine:

Whisper Large v3

Output:

```
transcript.txt

subtitles.srt
```

Additional processing:

* Remove filler words
* Correct punctuation
* Preserve technical terms

---

# 10. Hook Analyzer

Location:

```
modules/ai/hooks.py
```

Purpose:

Find the strongest opening moment.

Input:

* Transcript
* Question
* Timeline

Output:

```json
{
"recommended_start":
"00:13:02",

"hook":
"Most developers learn AI incorrectly",

"score":9
}
```

---

# 11. Caption Engine

Location:

```
modules/captions/
```

Responsibilities:

Generate professional subtitles.

Rules:

* Two lines maximum
* Developer-focused design
* Keyword highlighting

Example:

```
Most developers ignore

SYSTEM DESIGN
when learning coding.
```

---

# 12. Video Renderer

Location:

```
modules/social/vertical.py
```

Creates:

```
1080x1920
```

Logic:

* Face tracking
* Smart crop
* Caption placement

---

# 13. Branding Configuration

File:

```
config/branding.yaml
```

Example:

```yaml
creator:
  name: "Rao Waqas Akram"

title:
  Software Architect

caption:
  font: Inter
  style: premium-tech

colors:
  primary: white
  accent: blue
```

---

# 14. Metadata Generator

Location:

```
modules/ai/metadata.py
```

Creates:

* Titles
* Descriptions
* Hashtags
* Thumbnail text

Output:

```
metadata.json
```

---

# 15. Quality Scoring Engine

Location:

```
modules/ai/scoring.py
```

Scores:

```
Hook

Education

Technical Value

Audience Fit

Shareability
```

Example:

```json
{
"score":92,
"recommendation":
"Publish first"
}
```

---

# 16. Review Dashboard

Location:

```
modules/dashboard/
```

Technology:

Simple:

* HTML
* JavaScript

Display:

* Video
* Question
* Scores
* Metadata
* Approve/Reject

---

# 17. Storage Strategy

Initial version:

Filesystem based.

No database required.

Future:

SQLite/PostgreSQL.

---

# 18. Configuration Driven Design

Never hardcode:

* Paths
* Fonts
* Colors
* Video resolution
* Caption settings

Everything belongs in:

```
config/
```

---

# 19. Error Handling

Every module must:

* Catch exceptions
* Log failures
* Continue processing other clips

Example:

If Question_003 fails:

Continue:

Question_004

Question_005

---

# 20. Performance Goals

Target:

90-minute livestream:

Processing time:

< 20 minutes on modern hardware

Support:

100+ clips per session.

---

# 21. Future AI Features

Architecture should allow:

* Automatic posting
* Audience analytics
* Topic recommendations
* Content calendar
* Developer newsletter generation
* LinkedIn carousel creation
* Course material generation

---

# 22. Development Roadmap

## Phase 1

Basic pipeline:

✓ OCR detection
✓ Clip extraction
✓ Whisper subtitles

## Phase 2

Professional editing:

✓ Captions
✓ Vertical crop
✓ Branding

## Phase 3

AI intelligence:

✓ Hooks
✓ Scoring
✓ Metadata

## Phase 4

Automation:

✓ Dashboard
✓ Publishing workflow
✓ Analytics

---

# Final Architecture Principle

The system should behave like:

"An AI-powered video editor + content strategist + social media manager for a software engineering creator."

Every component must be replaceable without rewriting the entire system.
