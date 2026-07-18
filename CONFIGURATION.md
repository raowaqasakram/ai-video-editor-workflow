# CONFIGURATION.md

# AI Content Studio Configuration Specification

Version: 1.0

---

# Purpose

This document defines all configurable settings for the AI Content Studio.

The application must never hardcode:

* Branding
* Caption styles
* Video formats
* Fonts
* Colors
* Export settings
* AI thresholds

All values must come from configuration files.

---

# Configuration Files

## branding.yaml

Controls visual identity.

Contains:

* Creator information
* Fonts
* Colors
* Caption style
* Lower thirds
* Animations

---

## settings.yaml

Controls system behavior.

Contains:

* Processing options
* AI models
* Export settings
* Performance settings

---

# Branding Philosophy

The creator brand should represent:

* Professional software engineering
* Technical authority
* Modern AI ecosystem
* Premium education

Visual inspiration:

* Apple technical videos
* Google I/O presentations
* Modern developer conferences

Avoid:

* Gaming style graphics
* Excessive animations
* Loud colors
* Meme-style editing

---

# Caption System

Captions are one of the most important brand elements.

Requirements:

* Premium typography
* High readability
* Minimal distraction
* Developer audience friendly

---

# Caption Rules

Maximum:

2 lines

Characters per line:

35-45

Position:

Lower third

Exception:

Move captions when:

* Screen sharing detected
* Code visible
* Face obstruction detected

---

# Caption Animation

Default:

Smooth fade in/out

Optional:

Word emphasis animation

Avoid:

* bouncing text
* flashing
* excessive movement

---

# Keyword Highlighting

Automatically highlight:

Technical terms:

* Java
* Spring Boot
* Docker
* Kubernetes
* AI
* Cloud
* AWS
* Azure
* Microservices
* System Design
* Architecture
* DevOps

---

# Question Card

Every clip should begin with:

Viewer Question

Example:

"Should I learn Java in 2026?"

Duration:

3-5 seconds

Animation:

Fade + slide

---

# Lower Third

Display:

Creator name:

Rao Waqas Akram

Title:

Software Architect | AI Mentor

Duration:

5 seconds

Position:

Bottom left

---

# Video Export Profiles

## TikTok

Resolution:

1080x1920

FPS:

30

Format:

MP4

Codec:

H.264

---

## YouTube Shorts

Resolution:

1080x1920

FPS:

30

---

## Facebook Reels

Resolution:

1080x1920

FPS:

30

---

# Quality Settings

Video:

High quality

Audio:

Normalized

Bitrate:

Adaptive

---

# AI Settings

## Hook Detection

Enabled:

true

Minimum score:

7/10

---

## Virality Scoring

Enabled:

true

Factors:

* Hook
* Education
* Technical value
* Emotional impact
* Shareability

---

# OCR Settings

Detection interval:

1 second

Confidence threshold:

80%

Low confidence:

Flag for review

---

# Whisper Settings

Model:

large-v3

Language:

auto

Preserve:

Technical terminology

---

# Processing Modes

## Fast Mode

For quick previews.

Lower quality.

---

## Production Mode

Full processing.

Used before publishing.

---

# Logging

Required:

processing.log

Include:

* timestamps
* module name
* errors
* warnings
* performance metrics

---

# Future Configuration Support

Should support:

* Multiple creators
* Multiple brands
* Different caption themes
* Different platforms
* Different languages

---

# Rule

Any visual or processing change should be possible by editing YAML files only.
