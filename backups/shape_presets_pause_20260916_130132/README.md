# Drip Review — Side Trimmer

A working, local Windows prototype for reviewing coolant events in ibaCapture-exported MP4 videos. Built for the supplied 1920 × 1080, approximately 30 fps recordings on an Intel Core i5-14500T with 16 GB RAM. Uses CPU processing; no NVIDIA GPU, cloud account, or video upload is required.

## Start on this computer

### Automatic strip-relative contours (v3)

The interface now supports **Auto-follow strip edges / 自動跟隨**. Restart an older open app window after updating.

1. Select a video and enable **Auto-follow strip edges / 自動跟隨**.
2. Click **Re-locate areas / 重新定位區域**. The supplied Left, Center, and Right contours show the areas that will be analyzed.
3. Play or step through the video to check that the outlines stay on the aluminum strip as its width or position changes.
4. Click the always-visible top **Scan selected / 重新掃描** button, or the left-side **Scan / re-analyze selected video** button. Regions are located independently for every video and tracked through width changes and lateral movement. Exports and saved-result playback use the actual recorded geometry at each frame.
5. To change a shape, choose its name and click **Draw irregular contour / 畫輪廓**. Click around the wanted area, then double-click or press Enter. Every point must be inside the detected strip.
6. To modify the current shape, choose **Edit existing contour / 編輯節點**. Drag a node, right-click an edge to add a notch around a reflection, and Shift+right-click a node to remove it. Press Enter to save.
7. **Add another area / 新增區域** creates any additional named contour. Areas are not limited to Left or Right.
8. For a fixed manual rectangle, use **Manual rectangle**, which switches to manual mode.

Status meanings:

- **Tracking / 邊緣已定位:** current edges passed the geometric, color, and texture checks.
- **Held / 暫用上次位置:** last location is displayed for up to 0.5 seconds. Counting pauses during this period.
- **Lost / 無法定位:** no reliable current region; counting pauses and no fallback manual count is silently produced.
- **Recalibrating:** after a large geometry change or tracking interruption, eight clear frames are used to rebuild the background. These frames are excluded from detection and are reported.

Automatic contours are stored in strip coordinates: each point records its percentage from the current left edge to the current right edge, plus its image height. This lets irregular shapes stretch, shrink, and move with coil width, lateral coil movement, and small camera shifts. The locator is tuned to the current side-trimmer camera's perspective and blue/gray strip appearance. It is not a general-purpose locator for arbitrary cameras, lighting, strip finishes, or large camera repositioning. Verify the preview, especially with smoke, glare, red annotations, or changed camera position. A rejected location is not evidence that no oil drip occurred.

Each moving contour is cropped into a constant-sized image and its polygon mask is applied before background comparison. Pixels inside the surrounding rectangle but outside the contour are ignored. Large position changes cause background recalibration rather than being counted as defects. This alignment replaces the manual-mode camera-vibration option while automatic mode is selected. It is still a rules-based candidate detector, not a trained oil/smoke classifier.

Each automatic result includes `region_timeline.json` and a `tracking` summary in `results.json`, recording usable, held, lost, and recalibrating frames. Older results remain readable and retain their original manual regions. Automatic mode adds processing cost; this version is intended for offline video analysis and does not guarantee 30 fps throughput.

Oil-event detection still runs on every decoded frame so a fast droplet is not skipped. Area geometry uses an adaptive schedule: it starts with a full edge check every five frames, and after four consistent checks it drops to a once-per-second safety check for the remainder of startup. It then checks small camera motion once per second and performs the selected full recheck every 1, 5, or 10 minutes. A detected camera movement immediately forces a new full edge check. Failed or unsafe geometry checks enter Held/Lost and pause counting as before. The default stable interval is 10 minutes.

The supplied original videos are approximately 30 fps (the marked `_C` copies are approximately 30.3 fps). The detector overlaps decoding of the next frame with processing of the current frame; it does not skip frames. On the i5-14500T, the optimized Fast mode measured about 39–54 detection fps on representative 1920×1080 samples, or roughly 1.3–1.8× real-time. The result screen records both detection fps and its real-time ratio. Clip creation is a separate export phase; many event clips can make total wall-clock time longer than the detection pass.

**CPU usage / CPU 使用量** controls OpenCV worker threads. On the supplied i5-14500T Windows reports 20 logical processors: Balanced uses 7 threads, Fast uses 12, and Maximum uses 18. Fast is the default. These are processing-thread limits, not guaranteed Task Manager percentages; decoding, disk speed, operation size, and Windows scheduling affect actual utilization. Maximum can make the computer less responsive and is not always faster for short videos.

Double-click **Start_Drip_Review.cmd** in this folder. The required OpenCV and NumPy packages have already been installed in `.deps`; the launcher uses the available Codex Python runtime. The six original example videos appear automatically while their original paths remain available. `_C` copies contain red circles over the same recordings and are excluded from “Analyze all originals.”

1. Select a video. Its latest saved analysis loads automatically.
2. Select an event in the results table. The player jumps to its peak and zooms into the relevant area.
3. Use **Play**, **0.25x**, and **Frame ◀/▶** to inspect the event. Space toggles playback; left/right arrows step one frame.
4. Choose **Accept event**, **Reject**, or **Unreviewed**. If individual droplets can be resolved, enter the actual number in **Droplets (optional)**. Leave it blank if unknown. A splash with many fragments is not automatically counted as many droplets.
5. Scanning does not create MP4 files. Select an event and choose **Export event video / 匯出影片** only when a full and close-up clip is needed. After export, **Play event clip** opens the close-up MP4.

To process a new exported video, use **Add MP4 videos**, check the green detection outlines, and select **Scan selected / 重新掃描**. **Analyze all originals** processes the current library sequentially, excluding filenames ending in `_C`.

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

The initial regions cover the left, center, and right lower portions of the strip. The program follows the detected strip edges and suppresses textured background edges.

- **Draw irregular contour / 畫輪廓:** pick any named area, click at least three points, then double-click or press Enter. In automatic mode this shape follows the strip; Esc cancels.
- **Edit existing contour / 編輯節點:** drag existing nodes. Right-click a boundary to insert a node and make a notch around stable trimmer/chopper reflections; Shift+right-click removes a node. Fixed textured background is also suppressed automatically, but changing reflections still require review.
- **Add another area / 新增區域:** create a named center or other custom contour. **Delete selected area** removes it.
- **Restore auto Left / Center / Right:** restore three adjoining strip-relative shapes. They share boundaries and cover from the detected left coil edge to the detected right coil edge without intentional gaps. Edge reflections may therefore produce more candidates and require review.
- **Manual rectangle:** turn off automatic following and draw a fixed image rectangle.
- **Contrast threshold:** lower values find fainter changes but produce more false positives; higher values can miss small or blurred droplets. Default: 14.
- **CPU usage:** Balanced leaves more CPU for other work; Fast is the recommended default; Maximum is for dedicated analysis runs.
- **Stable area full recheck:** 1, 5, or 10 minutes. Camera motion is still checked every second, and droplets are still checked every frame.
- **Before/After:** clip context in seconds, default 0.75 seconds each. Clips stop at the source video's boundaries.
- **Compensate for camera vibration:** enabled by default. Failed alignments are skipped and reported; no candidates does not prove no dripping.
- **Add missed event:** after analysis, seek the source video to a missed event and click this button. It creates an accepted manual event without a video; export it only if needed.

Settings are saved when analysis begins. Existing analyses retain their settings. Selecting an event restores the detection regions used by that analysis for correct review.

## Output files

Each analysis creates a new folder under `output/`, with the source stem, run time, and a unique suffix. Source videos are only read.

| File | Contents |
|---|---|
| `results.json` | Source path, settings, timing, events, review status, warnings, performance |
| `events.csv` | Same event list with review decisions, readable in Excel |
| `clips/E…mp4` | On-request full-frame event clip with region and time annotation |
| `clips/E…_closeup.mp4` | On-request 2× enlarged crop with context around the monitored region |
| `clips/E…jpg` | On-request peak-frame evidence, highlighting the strongest detected component |

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
