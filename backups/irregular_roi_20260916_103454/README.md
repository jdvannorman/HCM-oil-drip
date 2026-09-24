# Drip Review — Side Trimmer

A working, local Windows prototype for reviewing coolant events in ibaCapture-exported MP4 videos. Built for the supplied 1920 × 1080, approximately 30 fps recordings on an Intel Core i5-14500T with 16 GB RAM. Uses CPU processing; no NVIDIA GPU, cloud account, or video upload is required.

## Start on this computer

### Automatic strip-edge areas (v2)

The interface now supports **Auto-follow strip edges / 自動跟隨**. Restart an older open app window after updating.

1. Select a video and enable **Auto-follow strip edges / 自動跟隨**.
2. Click **Preview areas / 預覽新區域**. Green quadrilaterals show the areas that will be analyzed. This also leaves saved-result review mode, so old manually positioned areas do not obscure the new preview.
3. Play or step through the video to check that the outlines stay inside the strip beside the trimmers.
4. Click **Analyze selected video**. Regions are located independently for every video and tracked through width changes and lateral movement. Exports and saved-result playback use the actual recorded geometry at each frame.
5. For manual operation, uncheck the option or use **Draw detection area**, which switches to manual mode.

Status meanings:

- **Tracking / 邊緣已定位:** current edges passed the geometric, color, and texture checks.
- **Held / 暫用上次位置:** last location is displayed for up to 0.5 seconds. Counting pauses during this period.
- **Lost / 無法定位:** no reliable current region; counting pauses and no fallback manual count is silently produced.
- **Recalibrating:** after a large geometry change or tracking interruption, eight clear frames are used to rebuild the background. These frames are excluded from detection and are reported.

Automatic mode uses the existing region's vertical extent. Horizontal placement follows each edge with an inward margin; detection bands are reduced on narrow strips to avoid overlap. It is tuned to the current side-trimmer camera's perspective and blue/gray strip appearance. It is not a general-purpose locator for arbitrary cameras, lighting, strip finishes, or widths. Verify the preview, especially with smoke, glare, red annotations, or changed camera position. A rejected location is not evidence that no oil drip occurred.

The moving quadrilateral is rectified into a constant-sized image before background comparison. Large position changes cause background recalibration rather than being counted as defects. This alignment replaces the manual-mode camera-vibration option while automatic mode is selected. It is still a rules-based candidate detector, not a trained oil/smoke classifier.

Each automatic result includes `region_timeline.json` and a `tracking` summary in `results.json`, recording usable, held, lost, and recalibrating frames. Older results remain readable and retain their original manual regions. Automatic mode adds processing cost; this version is intended for offline video analysis and does not guarantee 30 fps throughput.

Double-click **Start_Drip_Review.cmd** in this folder. The required OpenCV and NumPy packages have already been installed in `.deps`; the launcher uses the available Codex Python runtime. The six original example videos appear automatically while their original paths remain available. `_C` copies contain red circles over the same recordings and are excluded from “Analyze all originals.”

1. Select a video. Its latest saved analysis loads automatically.
2. Select an event in the results table. The player jumps to its peak and zooms into the relevant area.
3. Use **Play**, **0.25x**, and **Frame ◀/▶** to inspect the event. Space toggles playback; left/right arrows step one frame.
4. Choose **Accept event**, **Reject**, or **Unreviewed**. If individual droplets can be resolved, enter the actual number in **Droplets (optional)**. Leave it blank if unknown. A splash with many fragments is not automatically counted as many droplets.
5. **Play event clip** opens the saved close-up MP4 in your default video player. **Open results folder** shows all clips and CSV/JSON files.

To process a new exported video, use **Add MP4 videos**, check the green detection outlines, and select **Analyze selected video**. **Analyze all originals** processes the current library sequentially, excluding filenames ending in `_C`.

## What the results mean

- **Candidate event:** a short burst of new, localized bright spots in one monitored area. This is an automatic finding that needs review.
- **Continuous activity:** a burst lasting at least 0.75 seconds. This may be sustained fluid, repeated droplets, or a reflection; it must not be interpreted as one drip.
- **Accepted event:** a reviewer has accepted the event. It does not establish how many physical droplets caused the splash.
- **Confirmed droplets:** the optional count entered by a reviewer for accepted events. The app leaves the total unestablished until counts are entered.
- **Unreviewed/rejected:** not included in the accepted-event total.

The program groups detections separated by up to approximately 0.13 seconds in the same area. Nearby successive droplets and simultaneous droplets can merge. It does **not** yet provide a validated automatic count of individual airborne droplets. It does not identify coolant chemistry; classification is based on visible appearance in the supplied process context.

## Findings from the supplied videos

Clear visual examples include the left-side spot around **0.200 s** and larger splash around **5.267 s** in `2026_09_04__10_26_42.mp4`, and the right-side splash around **1.467 s** in `2026_09_04__10_26_56.mp4`. These times are relative to the start of each video.

The September 15 recording contains more frequent activity and stronger interference near the machinery. It needs closer review; its event total is not a reliable physical droplet count. Lower-contrast August recordings may miss small spots. No independent labeled validation set has been supplied, so recall, precision, and exact ground-truth drip totals are not established.

See `SAMPLE_RESULTS.md` for the final run's per-video candidate counts and measured performance. Earlier development runs remain in `inspection` or older `output` folders; use the runs linked in `SAMPLE_RESULTS.md` or `output/sample_index.json`.

## Adjust detection

The initial regions cover the strip near the left and right lower trimmer assemblies. Trapezoid boundaries help avoid reflections at the strip edges. The program estimates camera movement from surrounding machinery and suppresses textured background edges.

- **Draw detection area:** pick Left or Right, then drag a rectangle on the full-view image. Drawing a new rectangle replaces that area's trapezoid mask. Keep the rectangle on the strip and away from moving machine edges.
- **Restore sample areas:** restore the supplied geometry. Different camera positions or strip widths require adjustment.
- **Contrast threshold:** lower values find fainter changes but produce more false positives; higher values can miss small or blurred droplets. Default: 14.
- **Before/After:** clip context in seconds, default 0.75 seconds each. Clips stop at the source video's boundaries.
- **Compensate for camera vibration:** enabled by default. Failed alignments are skipped and reported; no candidates does not prove no dripping.
- **Add missed event:** after analysis, seek the source video to a missed event and click this button. It creates an accepted manual event and its clips; add the droplet count only if known.

Settings are saved when analysis begins. Existing analyses retain their settings. Selecting an event restores the detection regions used by that analysis for correct review.

## Output files

Each analysis creates a new folder under `output/`, with the source stem, run time, and a unique suffix. Source videos are only read.

| File | Contents |
|---|---|
| `results.json` | Source path, settings, timing, events, review status, warnings, performance |
| `events.csv` | Same event list with review decisions, readable in Excel |
| `clips/E…mp4` | Full-frame event clip with region and time annotation |
| `clips/E…_closeup.mp4` | 2× enlarged crop with context around the monitored region |
| `clips/E…jpg` | Peak-frame evidence, highlighting the strongest detected component |

The JPEG box identifies the strongest component, not every splash fragment. Enlarging a crop helps review but does not recover missing optical detail. Clips use MPEG-4 Part 2 video in MP4 containers, have no audio, and retain approximately the original frame rate. They are re-encoded copies; the source remains available for comparison.

Times use the decoder's presentation timestamps when monotonic, otherwise frame index divided by average FPS. Exports are made by frame index at constant average FPS. Variable-frame-rate files can therefore show small timing differences, which are reported. Absolute plant/iba timestamps are not inferred from filenames or synchronized to process data.

## iba workflow

This version analyzes **local exported video files**. In ibaCapture Manager, select a time interval for a camera and export the interval to MP4, then add that MP4 here. Keep the original resolution and frame rate for detection. When possible, export without drawn circles or other overlays in the monitored area.

Direct reading of ibaCapture's proprietary recording store, `.dat` process data, live RTSP acquisition, automatic folder monitoring, and PLC alarms are not implemented in this prototype. Those are separate integration steps after the detector is validated.

Official references:

- [ibaCapture video formats](https://docs.iba-ag.com/helpsetid%3Dibacapture%26externalid%3DCAP_Introduction%26Language%3Den-us%26publicationversion%3D5.5)
- [ibaCapture MP4 export](https://docs.iba-ag.com/helpsetid%3Dibacapture%26externalid%3DCAP_Export_videos%26Language%3Den-us%26publicationversion%3D5.5)
- [OpenCV video reading and writing](https://docs.opencv.org/4.x/dd/d43/tutorial_py_video_display.html)
- [OpenCV optical flow and camera alignment primitives](https://docs.opencv.org/4.x/dc/d6b/group__video__track.html)

## Install on another Windows computer

Install Python 3.12 64-bit with its launcher and Tcl/Tk support, then double-click **Setup_Drip_Review.cmd**. This creates `.venv` and installs `requirements.txt`. Once installed, double-click **Start_Drip_Review.cmd**. Package installation needs Internet access; video analysis runs offline.

This is a Python desktop app with a Windows launcher, not a packaged standalone `.exe` or Windows service. If the Codex runtime path changes, the `.venv` setup provides an independent runtime. For a clean separate installation, copy the source files without `.deps`, `.venv`, `inspection`, and `output`, then run setup.

## Verification and limits

`test_system.py` checks that successive frames from one burst are not counted as multiple events, rejects invalid detection areas, detects two synthetic transients against a static bright reflection, verifies source preservation, exercises start/end clipping, fully decodes exported clips, and checks cancellation state.

`test_ui.py` checks video loading, frame stepping, event seeking, zoom, accept/reject handlers, and JSON/CSV persistence using temporary review data. Native screen inspection was attempted but its permission request timed out; the visual layout and native mouse interactions were not verified by screen capture. `verify_outputs.py` decodes every frame of the final exported sample clips and checks their lengths and frame rates.

Performance measurements include background calibration and detection; total elapsed time also includes encoding/export. Memory measurements are process peak working set across the batch, not a guarantee for arbitrary resolutions or area sizes. Benchmark results on these short examples do not establish sustained multi-camera capacity.

Before unattended use, label representative positive and negative footage, then measure missed events and false positives on separate held-out clips. Include strip motion, changing illumination, steam, vibration, and different strip widths. At 30 fps, some fast droplets exist in only one or two frames, and exact airborne droplet counting may require better lighting, shutter settings, or a closer/faster camera.
