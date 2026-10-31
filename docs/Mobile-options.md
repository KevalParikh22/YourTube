# YourTube on a phone: options

Written 2026-10-31 for the decision "should YourTube be usable on a phone, and installed as an app?" Nothing here is built yet.

**Short answer:** make the phone a **remote for the computer at home** (option a). It needs almost no new code, a real home-screen install works, and the AI keeps running on the PC's graphics card. A phone-only app with its own AI model (option b) is possible on Android for sorting and short ratings, but it is a new app and it can't do the heavy parts well. Hosting (option c) runs into YouTube blocking server addresses.

How sure the facts are: model speeds below come mostly from Google's own blog and from developer posts, measured on a flagship phone. Mid-range phones are slower, and no source measured exactly the user's phone. **Measure on the real phone before building anything on these numbers.**

## 1. What a 6 GB phone can run today

### Language model (sorting, rating, summaries, Ask)
- **Gemma 4 E2B** (the small "effective 2B" model) is the realistic one. At 4-bit it needs about 2 to 5 GB depending on the source; a 6 GB phone is a tight but plausible fit. **E4B** (the larger one) needs about 8 GB, so it is "not on a 6 GB phone" in practice. [Unsloth, Gemma 4 guide, 2026](https://unsloth.ai/docs/models/gemma-4); [gemma4all, 2026](https://gemma4all.com/blog/gemma-4-on-phone); [cloudinsight, 2026](https://cloudinsight.pages.dev/en/blog/gemma-4-hardware-requirements/)
- **Speed, flagship (Samsung S26 Ultra):** about 52 tokens a second for E2B on the phone's graphics chip, 47 on its processor; E4B about 22. [Google Developers Blog, LiteRT-LM, 2026](https://developers.googleblog.com/blazing-fast-on-device-genai-with-litert-lm/); [DEV Community guide, 2026](https://dev.to/rahul_patwa_f99f19cd1519b/gemma-4-on-android-complete-developer-guide-to-on-device-ai-f5b)
- **Speed, mid-range:** developer reports range from about 2 to 12 tokens a second, with no named device in most. [DEV Community, 2026](https://dev.to/samdude/gemma-4-on-android-tricks-for-faster-on-device-inference-3kj5); [GitHub gist, 2026](https://gist.github.com/ashokvarmamatta/2305e6d9a2c7b5ac7da8fc5c1c95e489)
- **The route Google recommends** for apps is Android AICore (the system's built-in model, Gemini Nano) on supported phones, or the LiteRT-LM library to ship Gemma inside the app. [Google Developers Blog, 2026](https://developers.googleblog.com/blazing-fast-on-device-genai-with-litert-lm/)

What that means for YourTube's tasks (my estimate from the numbers above, not measured):

| Task | Today on the PC | On a mid-range 6 GB phone with E2B |
|---|---|---|
| Sort a video into a category | short prompt, short answer | **works**, a few seconds each |
| Rate a video (Watch/Skim/Skip) | reads up to a long transcript sample | **slow**: the phone's context and memory force a much smaller transcript sample, so ratings are weaker; tens of seconds each |
| Summarise a video | long input, long output | **poor**: minutes per video, phone gets hot |
| Ask your library | needs the meaning index of every video | **possible** only if the index is built elsewhere; answers are slower and shallower than on the PC |
| Hundreds of videos in one go | runs in the background on the PC | **not practical**: Android limits long background work and the battery drains |

### Speech to text (videos without captions)
- whisper.cpp runs on Android. One owner of an S24 Ultra reports about twice real time with a distilled small English model; a CPU-only phone was far slower when streaming. No clean benchmark exists for mid-range phones. [whisper.cpp discussion #1934](https://github.com/ggerganov/whisper.cpp/discussions/1934); [whisper.cpp issue #1635](https://github.com/ggerganov/whisper.cpp/issues/1635); [VoicePing engine benchmark, 2026](https://voiceping-landingpage-prod.pages.dev/en/blog/research-offline-speech-transcription-benchmark/)
- So a 20-minute video would take roughly 10 minutes or more of the phone working hard. Fine for one video, not for a backlog.

### Slides and whiteboards
- This needs a vision model reading up to 150 frames per video. **Not practical on a 6 GB phone** (memory and time); no source suggests otherwise.

## 2. What YourTube needs that a phone may not allow

- **Reading YouTube.** YourTube reads playlists with yt-dlp, a Python program. The YouTube Data API can't read Watch Later (see `docs/Phase-0-Research-and-Improvements.md`), so yt-dlp is the only route.
  - **Android: possible.** Apps such as Seal and YTDLnis already ship yt-dlp inside an Android app through the youtubedl-android library. [Seal on GitHub](https://github.com/JunkFood02/Seal); [It's FOSS on Seal](https://itsfoss.com/seal/)
  - **iPhone: not possible in a normal App Store app** as far as I found: those apps are Android-only, and I found no iOS app that bundles yt-dlp. [Clubic on Seal](https://www.clubic.com/telecharger-fiche445990-seal.html)
  - yt-dlp breaks when YouTube changes, and updates often. An app has to update it often too.
- **Cookies for private playlists** (Watch Later). On the PC they come from the browser. A phone app would have to sign the user in to YouTube inside the app and keep those cookies, which is fragile and which YouTube may treat as suspicious.
- **Background work.** Android stops long background tasks unless the app shows a notification and asks for exceptions; iPhone is stricter still. Syncs and rating hundreds of videos assume a computer that keeps running.
- **Storage.** The model (2 to 5 GB) plus transcripts and the index fit on most phones, but together with a whisper model it is a large app.

## 3. Three ways to do it

### (a) The phone as a remote for the home computer (recommended)
- **What it is:** the same dashboard, opened on the phone. Everything (yt-dlp, the local AI, transcription) still runs on the PC.
- **How it is reached:** Tailscale (a free private network between your own devices) gives the PC an `https://` address that only your devices can open (`tailscale serve`). Both phone and PC run the Tailscale app. [Open WebUI docs, Tailscale](https://docs.openwebui.com/ecosystem/computer/phone-and-remote/tailscale); [Open WebUI, HTTPS with Tailscale](https://docs.openwebui.com/reference/https/tailscale.md)
- **Install as an app:** because the address is HTTPS, Chrome on Android (and Samsung Internet) can install it as a real home-screen app. Over plain `http://192.168…` they can't. [MDN, Making PWAs installable](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_Installable)
- **Work:** small.
  1. The phone pass (T8 in the current plan), so every page works at phone width.
  2. Accept the Tailscale host name in the dashboard's Host check (today it allows only localhost, as protection against DNS rebinding). Off by default, switched on in Settings.
  3. A manifest and icons so the install works (the dropped section G of the plan).
  4. A setup page in the guide.
- **Risks:** anyone with access to your Tailscale network and the access link can change the library, so the access token stays required. Don't use `tailscale funnel`, which makes the address public. The PC has to be on.
- **Cost:** free (Tailscale's personal plan).

### (b) A real phone app with an on-device model
- **What it is:** a new Android app (Kotlin, or Flutter) with yt-dlp inside (like Seal), Gemma 4 E2B through LiteRT-LM, and a phone database.
- **What carries over:** the ideas, prompts, the database layout, and the rules (never change the source playlist; AI only suggests). **No code carries over**: YourTube is Python and plain JavaScript.
- **What gets worse:** ratings and summaries (smaller model, smaller transcript sample), no slide reading, slow transcription, no big batches.
- **iPhone:** not possible with the same features (no yt-dlp).
- **Work:** large, a second product to maintain, plus keeping yt-dlp up to date inside it.
- **Cost:** no running cost; Play Store fee if published.

### (c) Hosted on a server or in the cloud
- **The blocker:** YouTube now refuses many requests from data-centre addresses ("Sign in to confirm you're not a bot"), even with valid cookies. People get around it with residential proxies, which cost money and sit in a grey area. [DEV Community, 2026](https://dev.to/osovsky/i-was-building-a-cloud-video-service-youtube-turned-me-into-an-ip-trafficker-1l9o); [V2EX thread, April 2026](https://www.v2ex.com/t/1209318); [yt-dlp issue #10128](https://github.com/yt-dlp/yt-dlp/issues/10128)
- **Also:** your YouTube cookies and API keys would live on a server; the local AI would become paid cloud AI.
- **Verdict:** not worth it for one person.

## 4. Where installing as an app fits

- It is only worth doing together with **(a)**: an HTTPS address the phone can reach. Then Chrome on Android installs it as a home-screen app with its own window.
- On the PC alone it adds little (the user's point, 2026-10-31), so it stays dropped there.
- It does **not** make the phone do any of the work. The PC still does everything.

## 5. Recommendation and first step

1. **Finish the phone pass (T8)** in the current plan. That is needed for every option.
2. **Then try (a) by hand, no code:** install Tailscale on the PC and the phone, run `tailscale serve` pointing at port 8765, and open the address on the phone. It will be refused by the Host check, which shows exactly what to change.
3. **If that is useful day to day,** plan the small build: the "Open on my phone" switch in Settings, the manifest and icons, the setup guide.
4. **Revisit (b) only** if you often want YourTube with the PC switched off. Before planning it, measure Gemma 4 E2B on your actual phone with Google's AI Edge Gallery app or a LiteRT-LM sample: tokens per second on a 2,000-word prompt.

Not checked: Chrome's current install rules on web.dev itself (MDN was used), iPhone install behaviour over Tailscale, and any speed on the user's own phone.
