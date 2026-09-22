# ClipForge Local V2 — 3 Posts/Day

Local-first short-form clipping and scheduling dashboard.

## Included
- YouTube URL ingestion and local inbox support
- yt-dlp downloading
- faster-whisper local CPU transcription with word timestamps
- heuristic highlight selection with natural-ish boundaries
- FFmpeg 9:16 rendering
- phrase/word-style ASS captions
- SQLite jobs, clips and schedules
- Calendar with drag/drop date/time rescheduling
- Manual / Review / Full Automation modes
- Auto scheduler with exactly 3 default content slots/day: 10:00, 14:00, 19:30
- YouTube OAuth upload implementation
- Instagram/Facebook provider architecture requiring your own Meta credentials
- TikTok excluded

Only process videos you own or are authorized to use. Platform terms and copyright rules still apply.

## Windows
1. Install Python 3.11+ and FFmpeg.
2. Extract this ZIP.
3. Run setup.bat.
4. Run start.bat.
5. Open http://127.0.0.1:8000

The three daily slots are content slots. One scheduled clip can cross-post to multiple enabled platforms and still counts as one slot.

YouTube upload requires your own Google OAuth client_secret.json. Instagram/Facebook publishing requires your own Meta app credentials and current permissions.


## V2.1 quality/clip fixes
- Downloads the highest available source up to 1440p before rendering.
- Uses a higher-quality H.264 render setting (CRF 18).
- Short sources can produce clips down to 8 seconds when needed; default minimum is 15 seconds.
- If a YouTube source is shorter than the normal minimum, the clip selector adapts to the source duration instead of returning zero clips.
