from pathlib import Path
import subprocess

from .config import *


_MODEL = None


def transcribe(path):
    global _MODEL

    if _MODEL is None:
        from faster_whisper import WhisperModel

        _MODEL = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE
        )

    segs, info = _MODEL.transcribe(
        path,
        word_timestamps=True,
        vad_filter=True
    )

    out = []

    for s in segs:
        out.append({
            'start': s.start,
            'end': s.end,
            'text': s.text.strip(),
            'words': [
                {
                    'word': w.word,
                    'start': w.start,
                    'end': w.end
                }
                for w in (s.words or [])
            ]
        })

    return (
        out,
        getattr(info, 'duration', 0)
        or (out[-1]['end'] if out else 0)
    )


def choose_clips(segs, duration):
    """
    Select up to MAX_CLIPS_PER_SOURCE interesting transcript windows.

    Prefer clips around 30–60 seconds, while allowing shorter clips
    when there isn't enough continuous speech.
    """

    hooks = [
        'but',
        'why',
        'how',
        'actually',
        'secret',
        'mistake',
        'never',
        'always',
        'surprising',
        'because',
        'problem',
        'truth',
        'imagine',
        'important',
        'reason'
    ]

    if not segs or duration <= 0:
        return []

    min_len = MIN_CLIP_SECONDS
    max_len = MAX_CLIP_SECONDS
    preferred_len = 40.0

    candidates = []

    for i, start_seg in enumerate(segs):
        start = max(
            0.0,
            float(start_seg['start']) - 1.5
        )

        end = start
        last_j = i

        # Build a longer candidate, up to MAX_CLIP_SECONDS.
        for j in range(i, len(segs)):
            seg_end = float(segs[j]['end'])

            if seg_end <= start:
                continue

            if seg_end - start > max_len:
                break

            end = seg_end
            last_j = j

        clip_len = end - start

        if clip_len < min_len:
            continue

        text = ' '.join(
            str(segs[k].get('text', ''))
            for k in range(i, last_j + 1)
        ).strip()

        lower = text.lower()

        # Hook/interesting-word score.
        hook_score = sum(
            1 for h in hooks
            if h in lower
        )

        # Question score.
        question_score = min(
            2,
            lower.count('?')
        )

        # Reward enough spoken content.
        word_count = len(lower.split())

        speech_score = min(
            2.0,
            word_count / 30.0
        )

        # Prefer clips around 40 seconds.
        length_score = max(
            0.0,
            2.0 - abs(clip_len - preferred_len) / 20.0
        )

        score = (
            hook_score
            + question_score
            + speech_score
            + length_score
        )

        candidates.append({
            'score': score,
            'start': start,
            'end': end,
            'index': i
        })

    # Highest-scoring candidates first.
    candidates.sort(
        key=lambda x: x['score'],
        reverse=True
    )

    chosen = []

    for candidate in candidates:
        a = candidate['start']
        b = candidate['end']

        # Prevent overlapping clips.
        overlaps = any(
            not (
                b <= existing[0]
                or a >= existing[1]
            )
            for existing in chosen
        )

        if overlaps:
            continue

        chosen.append(
            (
                a,
                b,
                candidate['index'],
                candidate['score']
            )
        )

        if len(chosen) >= MAX_CLIPS_PER_SOURCE:
            break

    return sorted(chosen)


def ast(t):
    t = max(0, float(t))

    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)

    cs = int(
        round(
            (t - int(t)) * 100
        )
    )

    return (
        f'{h}:'
        f'{m:02d}:'
        f'{s:02d}.'
        f'{cs:02d}'
    )


def ass(path, segs, a, b):
    events = []

    words = [
        w
        for s in segs
        for w in s['words']
        if w['end'] >= a and w['start'] <= b
    ]

    # Group words into short caption phrases.
    for i in range(0, len(words), 4):
        q = words[i:i + 4]

        if not q:
            continue

        st = max(
            0,
            q[0]['start'] - a
        )

        en = min(
            b - a,
            q[-1]['end'] - a
        )

        if en > st:
            text = ' '.join(
                w['word'].strip()
                for w in q
            )

            # Prevent ASS formatting characters
            # from breaking the subtitle file.
            text = (
                text
                .replace('{', '')
                .replace('}', '')
            )

            events.append(
                (
                    st,
                    en,
                    text
                )
            )

    lines = [
        '[Script Info]',
        'ScriptType: v4.00+',
        'PlayResX:1080',
        'PlayResY:1920',

        '[V4+ Styles]',

        (
            'Format: Name,Fontname,Fontsize,'
            'PrimaryColour,SecondaryColour,'
            'OutlineColour,BackColour,Bold,Italic,'
            'Underline,StrikeOut,ScaleX,ScaleY,'
            'Spacing,Angle,BorderStyle,Outline,'
            'Shadow,Alignment,MarginL,MarginR,'
            'MarginV,Encoding'
        ),

        (
            'Style: Default,Arial,64,'
            '&H00FFFFFF,&H00FFFFFF,'
            '&H00000000,&H88000000,'
            '1,0,0,0,100,100,0,0,1,4,2,2,'
            '80,80,420,1'
        ),

        '[Events]',

        (
            'Format: Layer,Start,End,Style,Name,'
            'MarginL,MarginR,MarginV,Effect,Text'
        )
    ]

    for st, en, txt in events:
        lines.append(
            f'Dialogue: 0,'
            f'{ast(st)},'
            f'{ast(en)},'
            f'Default,,0,0,420,,'
            f'{txt}'
        )

    Path(path).write_text(
        '\n'.join(lines),
        encoding='utf-8'
    )


def render(source, a, b, segs, outdir, n):
    outdir = Path(outdir)

    outdir.mkdir(
        parents=True,
        exist_ok=True
    )

    af = outdir / f'clip_{n:02d}.ass'
    mp4 = outdir / f'clip_{n:02d}.mp4'

    # Create subtitle file.
    ass(
        af,
        segs,
        a,
        b
    )

    # Prepare subtitle path for FFmpeg's subtitles filter.
    #
    # FFmpeg filter syntax treats several characters specially,
    # so escape them before inserting the path.
    subtitle_path = str(af).replace(
        '\\',
        '/'
    )

    subtitle_path = subtitle_path.replace(
        ':',
        '\\:'
    )

    subtitle_path = subtitle_path.replace(
        '[',
        '\\['
    ).replace(
        ']',
        '\\]'
    )

    subtitle_path = subtitle_path.replace(
        ',',
        '\\,'
    )

    subtitle_path = subtitle_path.replace(
        "'",
        "\\'"
    )

    vf = (
        'scale=1080:1920:'
        'force_original_aspect_ratio=increase,'
        'crop=1080:1920,'
        f"subtitles='{subtitle_path}'"
    )

    subprocess.run(
        [
            FFMPEG_BIN,
            '-y',

            '-ss',
            str(a),

            '-i',
            source,

            '-t',
            str(b - a),

            '-vf',
            vf,

            '-c:v',
            'libx264',

            '-preset',
            'veryfast',

            '-crf',
            '18',

            '-c:a',
            'aac',

            '-b:a',
            '128k',

            str(mp4)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )

    return str(mp4)