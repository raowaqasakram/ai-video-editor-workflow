# Backdrop photos

Drop a photo of a real office or studio in here, then point a question's
`config.json` at it:

```json
"style": "studio_real",
"backdrop_photo": "office.jpg"
```

A real photograph beats the rendered set (`pipeline/backdrop.py`) every time, so
prefer this path whenever a usable photo exists.

## What makes a good plate

The photo is thrown far out of focus, underexposed and desaturated before use, so
sharpness and clutter do not matter much. What does matter:

* **Shot at eye height, roughly head-on.** A photo taken from a ladder or from the
  floor puts the room's perspective at odds with the camera's, which reads as wrong
  even after blurring.
* **Depth.** A wall two feet behind the camera position gives nothing to defocus.
  A room with something four or five metres deep — a corridor, shelving, a lit far
  wall — is what produces real bokeh.
* **Its own light.** Lamps, a monitor, a window. These become the highlights that
  sell it as a lit space.
* **Dark to mid tones, nothing brighter than the speaker's face.** A bright window
  behind him will pull the eye off him.
* **Portrait or large enough to cover-crop to 9:16** (1080x1920). Landscape photos
  work, but the sides get cropped away, so keep the interest near the centre.

Anything at least ~1500px on the short edge is plenty, since it ends up blurred.

## Licensing

Use photos you shot or that are licensed for commercial use — these end up in
published videos.
