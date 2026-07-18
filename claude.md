# CLAUDE.md

# AI Content Studio

## StreamYard YouTube Live → Short Form Content Automation Pipeline

---

# Project Vision

Build an AI-powered content production system that converts weekly YouTube Live sessions into professional short-form videos.

The creator primarily creates content around:

* Software Engineering
* Java
* Spring Boot
* Docker
* Cloud Computing
* System Design
* AI Engineering
* Developer Career Guidance
* Mentoring Software Engineers

The final output should be suitable for:

* YouTube Shorts
* TikTok
* Facebook Reels
* Instagram Reels
* LinkedIn video posts

The goal is to automate the workflow of a professional video editing team.

---

# Core User Workflow

The user will:

1. Complete a weekly StreamYard livestream.
2. Download the recorded video.
3. Place it inside:

```
INPUT/
    livestream.mp4
```

4. Run one command.
5. Review generated clips.
6. Approve selected clips.
7. Publish.

---

# Main Processing Pipeline

```
Livestream Video

        ↓

StreamYard Question Detection

        ↓

OCR Processing

        ↓

Question Based Clip Extraction

        ↓

Speech Analysis

        ↓

Hook Optimization

        ↓

Professional Caption Generation

        ↓

Video Enhancement

        ↓

Vertical Reel Creation

        ↓

AI Metadata Generation

        ↓

Quality Scoring

        ↓

Review Dashboard

        ↓

Approved Content
```

---

# Technology Stack

Primary Language:

* Python

Video:

* FFmpeg
* OpenCV

OCR:

Preferred:

1. PaddleOCR
2. EasyOCR
3. Tesseract fallback

Speech:

* Whisper Large v3

AI Processing:

* Claude Code

Computer Vision:

* OpenCV
* MediaPipe face tracking

---

# StreamYard Question Detection

## Objective

Automatically detect when the audience question appears on screen.

The StreamYard workflow:

User clicks:

```
SHOW QUESTION
```

Question overlay appears.

User clicks:

```
HIDE QUESTION
```

Question disappears.

These timestamps define the answer clip.

---

# OCR Rules

Process video frames.

Detect:

* Question overlay appearance
* Question overlay disappearance
* Question text

Generate:

```
Question Start Timestamp

Question End Timestamp

Question Text

OCR Confidence
```

Example:

```
Question:

Should I learn Java in 2026?

Start:

00:12:15

End:

00:15:42
```

---

# OCR Intelligence

OCR errors are expected.

Example:

OCR:

```
Hw cn I lern AI
```

Correct:

```
How can I learn AI?
```

Rules:

* Preserve original meaning.
* Do not invent questions.
* Flag low confidence results.

---

# Clip Generation

Use FFmpeg.

Each detected question creates:

```
Question_001.mp4

Question_002.mp4

Question_003.mp4
```

Do not unnecessarily re-encode.

Maintain original quality.

---

# AI Hook Detection

Do not always start the clip exactly when the question appears.

Analyze the answer.

Find the strongest opening moment.

Prioritize:

* Strong opinions
* Contrarian statements
* Mistakes
* Career advice
* Practical examples
* Surprising insights

Example:

Remove:

"Good question..."

Prefer:

"Most developers are learning AI the wrong way."

The final clip should start with the strongest hook.

---

# Caption System

Captions are a major part of the brand.

The style should be:

Premium technology creator style.

Reference feeling:

* Apple product videos
* Modern developer conferences
* High-quality engineering channels

Avoid:

* Flashy TikTok effects
* Cartoon animations
* Distracting colors

---

# Caption Design Rules

Font:

* Inter
* Manrope
* Modern Sans Serif

Rules:

* Maximum 2 lines
* Large readable text
* Smooth animations
* Professional spacing

Primary:

White text

Highlight:

Brand accent color

---

# Keyword Highlighting

Automatically highlight:

Technical keywords:

* Java
* Spring Boot
* Docker
* Kubernetes
* AI
* Machine Learning
* Cloud
* AWS
* Azure
* System Design
* Microservices
* Architecture
* CI/CD

Example:

Normal:

```
Learn Docker before Kubernetes.
```

Better:

```
Learn DOCKER
before KUBERNETES.
```

---

# Question Overlay Design

At the beginning of every clip:

Show:

```
Viewer Question

"Should I learn Java in 2026?"
```

Style:

* Clean card
* Professional animation
* Fade transition

---

# Subtitle Rules

Generate subtitles using Whisper.

Requirements:

* Accurate timing
* Correct punctuation
* Preserve technical words
* Remove unnecessary filler words

Do not modify meaning.

---

# Video Format

Generate:

## Original Version

Keep original aspect ratio.

## Social Version

Create:

```
1080 x 1920

9:16
```

for:

* TikTok
* Reels
* Shorts

---

# Smart Cropping

Use face detection.

Requirements:

* Keep speaker centered.
* Avoid covering face.
* Avoid covering important content.

If screen sharing:

Move captions away from code.

---

# Video Enhancement

Apply:

* Audio normalization
* Noise reduction
* Brightness correction
* Contrast improvement
* Sharpening

Do not create artificial effects.

Maintain professional look.

---

# Developer Branding

Add consistent identity.

Example:

```
Rao Waqas Akram

Software Architect
AI & Engineering Mentor
```

Use as lower third.

---

# Content Intelligence

For every generated clip create:

## Scores

```
Hook Score: /10

Educational Value: /10

Developer Relevance: /10

Shareability: /10

Virality Potential: /10
```

---

# AI Generated Metadata

Generate:

```
title.txt

description.txt

hashtags.txt

thumbnail_title.txt

metadata.json
```

---

# Title Rules

Titles should be:

* Clear
* Professional
* Search friendly
* Under 70 characters

Avoid:

* Fake curiosity
* Misleading clickbait

---

# Description Rules

Include:

* Original question
* Summary
* Key learning points

---

# Hashtags

Generate 10-15 relevant hashtags.

Example:

```
#SoftwareEngineering
#Java
#AI
#Programming
#DeveloperCareer
#SystemDesign
```

---

# Folder Structure

```
AI-Content-Studio

├── CLAUDE.md

├── INPUT

│   └── livestream.mp4

├── OUTPUT

│   ├── Question_001

│   │   ├── clip.mp4
│   │   ├── vertical.mp4
│   │   ├── subtitles.srt
│   │   ├── question.txt
│   │   ├── title.txt
│   │   ├── description.txt
│   │   ├── hashtags.txt
│   │   └── metadata.json

├── READY_TO_UPLOAD

├── scripts

├── config

└── logs
```

---

# Review Dashboard

Generate:

```
review.html
```

The dashboard should show:

* Video preview
* Question
* Title
* Description
* Scores
* Approve button
* Reject button

Only approved videos move to:

```
READY_TO_UPLOAD
```

---

# Logging

Maintain:

```
processing.log
```

Include:

* OCR results
* timestamps
* errors
* processing time
* failed clips

---

# Future Expansion

The system should be designed for:

* LinkedIn posts
* Twitter/X threads
* Blog generation
* Newsletter creation
* Content analytics
* Topic tracking
* Duplicate answer detection
* Audience question database

---

# Engineering Principles

Always:

* Use modular architecture.
* Keep configuration external.
* Write clean maintainable code.
* Add error handling.
* Add logging.
* Prefer open-source tools.
* Avoid unnecessary complexity.

---

# Final Success Criteria

A single command should transform:

```
90-minute livestream
```

into:

```
20-30 professional short videos

with:

✓ Correct clips
✓ Professional captions
✓ Strong hooks
✓ Vertical formatting
✓ SEO metadata
✓ Social-ready output
✓ Quality scoring
```

The system should behave like an AI-powered video production team.
