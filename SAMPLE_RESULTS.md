# Sample results — final prototype run

Six original 1080p recordings were analyzed. The two `_C` versions are marked copies and were not added to the totals.

**32 candidate drip events and 2 continuous-activity records were flagged. These are not 32 confirmed physical droplets.** All records remain unreviewed. The detector can miss small/dark/blurred droplets and flag reflections; no precision or recall claim is supported yet.

| Original video | Candidate events | Continuous activity | Detection time | Detection speed | Total including export |
|---|---:|---:|---:|---:|---:|
| 2026_08_31 6_33_07.mp4 | 1 | 0 | 2.75 s | 35.67 fps | 4.55 s |
| 2026_08_31__06_33_24.mp4 | 1 | 0 | 2.64 s | 37.07 fps | 4.50 s |
| 2026_08_31__06_34_13.mp4 | 3 | 0 | 6.23 s | 42.24 fps | 11.10 s |
| 2026_09_04__10_26_42.mp4 | 4 | 0 | 2.54 s | 81.37 fps | 5.70 s |
| 2026_09_04__10_26_56.mp4 | 2 | 0 | 1.39 s | 84.42 fps | 2.97 s |
| 2026_09_15__09_47_49.mp4 | 21 | 2 | 4.57 s | 98.09 fps | 36.08 s |

Test CPU was read from the computer: Intel Core i5-14500T, 14 cores / 20 logical processors. Detector process peak working set across the final batch: **232.9 MB**. These measurements cover the configured regions in the supplied short videos; they do not establish sustained multi-camera performance. CPU load and encoding change elapsed time.

## Visual examples to review first

- September 4, `10_26_42`: clear localized left-side spots/splash at approximately **0.200**, **0.800**, and **5.267 seconds**. The additional 6.700-second edge candidate is uncertain.
- September 4, `10_26_56`: left-side spot at approximately **0.500 seconds**, and a clear right-side splash at **1.467 seconds**.
- September 15: many transient spots are visible. Machine-edge reflections remain in some detections. Activity spanning approximately **6.47–7.50 seconds** and **7.83–9.63 seconds** was grouped into two continuous records, and cannot be treated as two individual drips. A right-side streak/splash is visible around **9.100 seconds**.

All times are relative to the start of each video. A visible spot or splash does not establish the original number of airborne droplets.

## Open final results

The app automatically chooses the latest completed result for each source. Final run folders:

- [August 31, 06:33:07 results](<output/2026_08_31 6_33_07_20260915_110128_9010e/results.json>)
- [August 31, 06:33:24 results](output/2026_08_31__06_33_24_20260915_110132_65e2c/results.json)
- [August 31, 06:34:13 results](output/2026_08_31__06_34_13_20260915_110137_90617/results.json)
- [September 4, 10:26:42 results](output/2026_09_04__10_26_42_20260915_110148_1d991/results.json)
- [September 4, 10:26:56 results](output/2026_09_04__10_26_56_20260915_110154_5be05/results.json)
- [September 15, 09:47:49 results](output/2026_09_15__09_47_49_20260915_110157_aa1aa/results.json)

[Example: right-side splash clip at 1.467 seconds](output/2026_09_04__10_26_56_20260915_110154_5be05/clips/E002_right_1.467s_closeup.mp4)

## Checks completed

- 3 automated detector/export tests passed, including synthetic burst counting, source preservation, invalid areas, video boundaries, and cancellation.
- Application handler checks passed for loading video, single-frame stepping, event seeking, zoom, accept/reject decisions, and persistent JSON/CSV output.
- All **68 exported clips**, containing **3,406 frames** in total, decoded completely and matched their expected frame counts and frame rates. See [verification.json](output/verification.json).
- No source frames were skipped due to camera-alignment failure during the final sample run.
- Native Windows screen inspection was blocked by an expired permission request. Visual layout/mouse operation has not been verified through screen capture. This did not prevent detector, export, or application-handler checks.

Exact drip counts, false-positive rate, and missed-event rate still require frame-by-frame ground-truth labeling and a separate validation set.
