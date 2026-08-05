# Voice reference recording script

Read this once, in one sitting, into one file. It replaces the single 102-second take with
material the tone picker can actually choose between.

**Why this exists.** Chatterbox clones prosody from only ~6 seconds of your reference and
discards the rest. The pipeline now scans your recording and hands the cloner the 12-second
window whose *delivery* matches the video being made. On the old take, `authoritative` found a
genuinely slower passage but `punchy` and `energetic` landed on windows nearly identical to
neutral — because that recording is one uniform performance. The picker can only choose between
deliveries you actually recorded. That is the entire point of the four sections below.

---

## Before you press record

**Room.** Soft and small beats big and treated-badly. A walk-in wardrobe with clothes in it is
genuinely excellent. Otherwise: sit away from bare parallel walls, put a duvet or coat on a chair
behind you and another to one side. Kill the fan, the AC, the fridge hum if you can hear it.

**Mic.** About a hand's width (15–20 cm) from your mouth, angled slightly off to one side so
your plosives blow past it rather than into it. If you have a pop filter, use it. Keep the same
distance the whole time — moving closer and further is the one inconsistency that genuinely
degrades a clone.

**Audacity setup** (do these once):

1. `Edit → Preferences → Devices` → Recording **Channels: 1 (Mono)**. A cloned voice is mono;
   recording stereo just averages two versions of the same signal.
2. `Edit → Preferences → Quality` → Default Sample Rate **44100 Hz**, Default Sample Format
   **32-bit float**. Float gives you headroom so a loud moment can't hard-clip while recording.
3. `Edit → Preferences → Recording` → untick **Software Playthrough** (stops echo/feedback).
4. Windows: `Settings → System → Sound → <your mic> → Properties` → turn **OFF** all audio
   enhancements, noise suppression, and "voice clarity". Windows' processing is a compressor and
   a gate — both destroy exactly the dynamics the tone picker reads.
5. Record 10 seconds of *silence* first, saying nothing. That is your noise profile, and it also
   proves the room is quiet enough. If that silence looks like a visible waveform rather than a
   flat line, fix the room before continuing.

**Levels.** Speak your loudest line and watch the meter. You want peaks around **-12 to -6 dB**.
Never let it touch 0. If it does, lower the input gain — do not "fix it later".

---

## While recording

- Read at your natural pace for each section's tone. Do not perform. The goal is *you*, four ways.
- **Leave about 3 seconds of silence between sections.** The picker uses 12-second windows; the
  gaps keep a window from straddling two different tones and blending them.
- Fluff a line? Stop, leave 2 seconds, and read the whole sentence again. Do not restart the
  section. You will cut the bad take out afterwards.
- Do not smile through the authoritative section or frown through the energetic one out of habit.
  Your face changes your formants more than you would expect.

---

# SECTION 1 — NEUTRAL

> **Delivery:** your default explaining voice. The one you would use walking a colleague through
> something at your desk. Even pace, even volume, no performance. This is the baseline every other
> section is measured against, so resist the urge to make it interesting.

Most of what people believe about hiring at large technology companies comes from other people who
also never sat on the other side of the table. The process is not mysterious, but it is specific,
and the specifics are where candidates lose. A resume is read for about six seconds by someone who
is looking for a reason to move on. An interview loop is not one decision, it is five or six
independent ones that get reconciled in a room you will never see. A compensation band is not a
number, it is a range with a policy attached to it, and the policy is usually more negotiable than
the range.

None of that is secret. It is simply not written down anywhere the candidate can reach, so it gets
replaced by folklore. The folklore is comforting because it is external. If an automated system
rejected you, there was nothing you could have done. If a person rejected you in six seconds, there
was something specific on that page that made them stop reading, and you could have changed it.

That is the difference this channel is about. Not motivation, not encouragement, just the actual
mechanics of how these decisions get made and what you can control inside them.

---

# SECTION 2 — AUTHORITATIVE

> **Delivery:** slow down. Noticeably slower than feels natural — this is the one people rush.
> Land each full stop and let it sit for a beat before the next sentence. Weight on the numbers.
> Think of explaining something consequential to someone who needs to get it right the first time,
> where being misunderstood costs them. Lower in your chest, less bright, unhurried.

Here is what the number actually means.

A level is not a title. It is a budget line, a scope expectation, and a promotion clock, bundled
together and given a label. When a company down-levels you, it is not commenting on your ability.
It is deciding which budget you come out of.

Consider the arithmetic. The gap between two adjacent levels at a large firm is routinely sixty to
ninety thousand dollars a year in total compensation. Over a four year vesting cycle, that single
decision — made in about twenty minutes, by people who met you for an hour — is worth more than a
quarter of a million dollars.

Now consider how that decision gets made. Your interviewers do not assign your level. They write
evidence. A separate committee reads that evidence, cold, without you in the room, and matches it
against a written rubric. If your evidence describes execution, you are levelled as an executor.
If it describes judgment under constraint, ownership of an ambiguous problem, and consequences you
absorbed personally, you are levelled as an owner.

The distinction is not seniority of tone. It is whether the evidence on the page can only have been
produced by someone operating at that level. That is the whole test. Everything else is presentation.

---

# SECTION 3 — PUNCHY

> **Delivery:** this is the argument section. You are pushing back on something you think is wrong,
> and you are slightly impatient with it. Hit the contrast words hard — the *not this, that*
> moments. Short sentences, sharp stops, more range top to bottom. Let the questions actually rise.
> More energy in the front of your mouth. This should feel like you are making a point, not
> reading one.

So let me kill this myth properly.

The robot did not reject you. There is no robot. A human being looked at your resume for six
seconds and moved on, and that is worse news, because it means the reason was visible.

Think about what that person is actually doing. Fifty applications. One requisition. They are not
reading — they are scanning for a reason to stop. Every generic line you wrote is a reason to stop.

"Passionate about machine learning." Stop. "Team player with strong communication skills." Stop.
"Worked on various projects using Python, TensorFlow, and SQL." Stop.

Why? Because none of it is falsifiable. None of it could only be true of you. Swap your name for
anyone else's and every one of those sentences still reads exactly the same. That is the test, and
almost nobody applies it.

Now compare. "Cut model serving latency at the ninety-ninth percentile from four hundred
milliseconds to ninety by replacing the ranking stage." Who else could have written that? Nobody.
It names a system, a number, and a decision. It cannot be copied because it actually happened.

That is the entire difference. Not keywords. Not formatting. Not a template you downloaded.
Evidence, versus adjectives.

Stop optimising for the machine that is not reading it. Start writing for the person who is.

---

# SECTION 4 — ENERGETIC

> **Delivery:** quick and flowing, riding the momentum from clause to clause without dropping
> energy at the commas. Genuine enthusiasm — you find this stuff interesting and it should sound
> like it. Lighter, faster, more forward. Do not over-articulate; let sentences run together the
> way they do when you are telling someone something you are excited about.

Okay, so here is where it gets genuinely interesting, and honestly this is my favourite part of the
whole process, because once you see it you cannot unsee it.

Every single one of these companies has a document. An actual written document, with rows and
columns, that says exactly what separates one level from the next, and interviewers are trained on
it, calibrated against it, and audited on how well their scores match it. It exists. People have
described it publicly. Former employees talk about it constantly. And yet almost every candidate
walks into the room having never once asked what is on it.

And the thing is, you can reverse engineer most of it just from the questions they ask you, because
the questions are not random, they are probes, and each probe maps to a row on that document. When
someone asks you to walk through a project, they are not making conversation, they are looking for
scope. When they interrupt to ask why you chose that approach, they are testing judgment. When they
push back on your answer and watch what you do, that is not hostility, that is the collaboration
signal, and it is worth more than getting the answer right.

So once you know the shape of the rubric, the entire loop stops feeling like a test and starts
feeling like a conversation where you already know what the other person is writing down. Which,
when you think about it, is a completely different game.

---

## After recording

1. **Cut the mistakes.** Select each fluffed take and delete it. Leave the good silences alone.
2. **Trim the ends.** Delete the long silence before the first word and after the last.
3. **Noise reduction, gently.** Select 5 seconds of your room-tone silence →
   `Effect → Noise Removal and Repair → Noise Reduction → Get Noise Profile`. Then
   `Select All` → same effect → **Noise Reduction 6 dB, Sensitivity 6.00, Frequency Smoothing 3**
   → OK. Six is deliberate; heavy reduction makes voices sound underwater and clones learn that.
4. **Do NOT** apply Compressor, Limiter, Normalize, Loudness Normalization, or heavy EQ.

   This one matters more than it looks. The tone picker chooses your delivery by measuring
   **dynamic range** — how much your level moves between emphasised and unemphasised words. A
   compressor's entire job is to flatten that. Compress this file and every section collapses to
   the same measurement, the picker goes blind, and you get the flat delivery you started with.
   The pipeline already loudness-normalises the *final* mix at render, so nothing is gained here
   and the tone control is lost.
5. **Export.** `File → Export → Export as WAV` → Format **WAV (Microsoft)**, Encoding
   **Signed 16-bit PCM**, Channels **Mono**, Sample Rate **44100 Hz**.

Save it as a **new file** — for example `assets/voice_reference_v2.wav`. Do not overwrite the
existing `assets/voice_reference.wav` until you have compared them, and never while a voiceover
run is in progress.

To switch over, point `TTS_REFERENCE_CLIP` at the new file in `.env`. To hear one specific tone on
demand rather than letting the script template choose, set `TTS_TONE=punchy` (or `authoritative` /
`energetic` / `neutral`). The run log line `reference_clip_condensed` reports the tone, the offset
it chose, and the measured pace/dynamics/density of that window — check it to confirm the four
sections really did come out different.
