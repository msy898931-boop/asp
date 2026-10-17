# Field test — the Android `.apk` on real hardware

**Date:** 2026-10-17
**Device:** Samsung Galaxy (2016 model)
**Artifact:** `astra.apk` (25.3 KB, `minSdkVersion 21`)
**Tester:** @msy898931-boop, on a physical device
**Reported by:** OpenHands agent, on behalf of @msy898931-boop

This is the first evidence that the shipping artifact runs end-to-end on real
hardware. Every earlier read of this repository assessed the `.apk` by opening
its contents (a 25 KB WebView shell); none had run it. This one did.

## What was exercised

| # | Step | Result |
| --- | --- | --- |
| 1 | Install and launch the `.apk` on the device | **opens** |
| 2 | The access gate is reached | **key prompt shown** |
| 3 | Enter the access key | **accepted; the app opens** |
| 4 | Data panels populate | **all readings load normally** |
| 5 | Close the app, then reopen it | **opens normally; the key is not re-asked** |

Step 5 is the one that matters most: it shows the `HttpOnly` access cookie
survives a full app restart, so the key is typed once, not on every launch.

## Why the device matters

A 2016 handset is weak hardware by any current measure. The artifact declares
`--min-sdk-version 21` (Android 5.0, 2014) in `android/build.sh`, so the app
was *designed* to reach devices this old. This test shows that design holds in
practice, not only on paper: the shell, the gate, the cookie, and the data
panels all work on hardware roughly a decade old.

## What this does not prove

- **One device, one Android version.** Behaviour on other screen sizes and
  Android releases is still unmeasured.
- **The desktop `.exe` is still untested by a real user.** Half of the delivery
  dimension remains evidence-free.
- **The shell loads a remote URL.** With the host asleep the app has no local
  fallback; the offline shell covers the PWA, not the WebView shell.
- **No screenshot was captured.** The result is a tester's report, not a
  machine-checked record.

## Effect on the assessment

`docs/ASSESSMENT_2026-10-16.md` scored Delivery & portability **3.5/5** on the
grounds that the artifacts "exist but are untested by real users". That is now
half-resolved for the phone side. The delivery dimension moves toward
**4.25/5** and the mobile-experience dimension toward **6.0/8**, both held back
by the single-device scope and the untested `.exe`.

This file adds evidence only. It changes no code.
