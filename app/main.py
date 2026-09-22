import json
import threading
import time
import traceback

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import *
from .db import *
from .downloader import download_url
from .pipeline import transcribe, choose_clips, render
from .providers import status as provider_status, youtube_upload, meta_upload
from .ai_metadata import generate_metadata


app = FastAPI(title="ClipForge Local V2")

init_db()

LOCK = threading.Lock()


class URLReq(BaseModel):
    url: str


class SReq(BaseModel):
    clip_id: int
    run_at: str
    title: str = ""
    description: str = ""
    hashtags: str = ""
    platforms: list[str] = ["youtube"]


class ModeReq(BaseModel):
    mode: str


class AutoReq(BaseModel):
    enabled: bool


def normalize_run_at(value: str) -> str:
    """
    Accept:
        2026-09-21T10:00
        2026-09-21T10:00:00
        2026-09-21T10:00+05:30

    Store with the configured ClipForge timezone.
    """
    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=ZoneInfo(TIMEZONE)
            )

        return dt.isoformat()

    except Exception:
        raise HTTPException(
            400,
            "Invalid date/time."
        )


def schedule_is_past(value: str) -> bool:
    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=ZoneInfo(TIMEZONE)
        )

    return (
        dt.astimezone(timezone.utc)
        <= datetime.now(timezone.utc)
    )


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/api/jobs")
def jobs():
    return [
        dict(x)
        for x in execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT 30",
            fetch=True
        )
    ]


@app.get("/api/clips")
def clips():
    return [
        dict(x)
        for x in execute(
            """
            SELECT c.*, j.title source_title
            FROM clips c
            JOIN jobs j ON j.id = c.job_id
            ORDER BY c.id DESC
            LIMIT 100
            """,
            fetch=True
        )
    ]


@app.get("/api/schedules")
def schedules():
    return [
        dict(x)
        for x in execute(
            "SELECT * FROM schedules ORDER BY run_at",
            fetch=True
        )
    ]


@app.get("/api/settings")
def settings():
    return {
        x["key"]: x["value"]
        for x in execute(
            "SELECT key,value FROM settings",
            fetch=True
        )
    }


@app.get("/api/providers")
def providers():
    return provider_status()


@app.post("/api/jobs")
def newjob(r: URLReq):
    execute(
        """
        INSERT INTO jobs
        (source,status,progress,message,created_at,updated_at)
        VALUES(?,?,?,?,?,?)
        """,
        (
            r.url,
            "queued",
            0,
            "Queued",
            now(),
            now()
        )
    )

    j = one(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT 1"
    )

    threading.Thread(
        target=process,
        args=(j["id"], r.url),
        daemon=True
    ).start()

    return j


def upd(jid, **kw):
    q = ",".join(
        f"{k}=?"
        for k in kw
    )

    execute(
        f"UPDATE jobs SET {q},updated_at=? WHERE id=?",
        tuple(kw.values()) + (now(), jid)
    )


def process(jid, url):
    if not LOCK.acquire(False):
        threading.Timer(
            5,
            process,
            args=(jid, url)
        ).start()
        return

    try:
        upd(
            jid,
            status="downloading",
            progress=10,
            message="Downloading source"
        )

        path, title = download_url(url)

        upd(
            jid,
            status="transcribing",
            progress=30,
            message="Transcribing locally"
        )

        segs, dur = transcribe(path)

        upd(
            jid,
            status="selecting",
            progress=55,
            message="Selecting highlights"
        )

        choices = choose_clips(
            segs,
            dur
        )

        out = OUTPUT / f"{jid}_{Path(path).stem[:70]}"

        for n, (a, b, i, score) in enumerate(
            choices,
            1
        ):
            upd(
                jid,
                status="rendering",
                progress=min(
                    95,
                    55 + n * 8
                ),
                message=f"Rendering clip {n}/{len(choices)}"
            )

            mp4 = render(
                path,
                a,
                b,
                segs,
                out,
                n
            )

            txt = " ".join(
                s["text"]
                for s in segs
                if s["end"] >= a and s["start"] <= b
            ).strip()

            ai = generate_metadata(txt)

            ai_title = ai.get(
                "title",
                f"{title} — Clip {n}"
            ).strip()

            ai_description = ai.get(
                "description",
                ""
            ).strip()

            ai_hashtags = ai.get(
                "hashtags",
                []
            )

            if not isinstance(ai_hashtags, list):
                ai_hashtags = []

            ai_hashtags = [
                str(x).strip()
                for x in ai_hashtags
                if str(x).strip()
            ]

            execute(
                """
                INSERT INTO clips
                (
                    job_id,
                    path,
                    title,
                    duration,
                    transcript,
                    description,
                    hashtags,
                    status,
                    created_at
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    jid,
                    mp4,
                    ai_title,
                    b - a,
                    txt,
                    ai_description,
                    json.dumps(ai_hashtags),
                    "ready",
                    now()
                )
            )

        upd(
            jid,
            status="done",
            progress=100,
            message=f"Created {len(choices)} clips"
        )

    except Exception as e:
        traceback.print_exc()

        upd(
            jid,
            status="failed",
            progress=0,
            message=str(e)[:500]
        )

    finally:
        LOCK.release()


def clip_metadata(clip_id):
    c = one(
        "SELECT * FROM clips WHERE id=?",
        (clip_id,)
    )

    if not c:
        raise HTTPException(
            404,
            "Clip not found."
        )

    title = c["title"] or ""
    description = c.get(
        "description",
        ""
    ) or ""

    hashtags = c.get(
        "hashtags",
        ""
    ) or ""

    try:
        tags = json.loads(hashtags)

        if isinstance(tags, list):
            hashtags = " ".join(
                str(x)
                for x in tags
            )
    except Exception:
        pass

    return title, description, hashtags


@app.post("/api/schedules")
def add(r: SReq):
    run_at = normalize_run_at(
        r.run_at
    )

    if schedule_is_past(run_at):
        raise HTTPException(
            400,
            "Please choose a future date and time."
        )

    title = r.title
    description = r.description
    hashtags = r.hashtags

    if not title or not description or not hashtags:
        clip_title, clip_description, clip_hashtags = (
            clip_metadata(r.clip_id)
        )

        if not title:
            title = clip_title

        if not description:
            description = clip_description

        if not hashtags:
            hashtags = clip_hashtags

    execute(
        """
        INSERT INTO schedules
        (
            clip_id,
            run_at,
            title,
            description,
            hashtags,
            platforms,
            status,
            created_at,
            updated_at,
            platform_results
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            r.clip_id,
            run_at,
            title,
            description,
            hashtags,
            json.dumps(r.platforms),
            "scheduled",
            now(),
            now(),
            "{}"
        )
    )

    return {
        "ok": True
    }


@app.put("/api/schedules/{sid}")
def edit(sid: int, r: SReq):
    old = one(
        "SELECT * FROM schedules WHERE id=?",
        (sid,)
    )

    if not old:
        raise HTTPException(
            404,
            "Schedule not found."
        )

    if old["status"] == "published":
        raise HTTPException(
            409,
            "Published schedules cannot be edited."
        )

    run_at = normalize_run_at(
        r.run_at
    )

    if schedule_is_past(run_at):
        raise HTTPException(
            400,
            "Please choose a future date and time."
        )

    new_platforms = json.dumps(
        r.platforms
    )

    # Preserve successful platform uploads when only
    # date/time/title/description changes.
    # Reset them if the clip or platforms themselves changed.
    reset_results = (
        old["clip_id"] != r.clip_id
        or old["platforms"] != new_platforms
    )

    execute(
        """
        UPDATE schedules
        SET
            clip_id=?,
            run_at=?,
            title=?,
            description=?,
            hashtags=?,
            platforms=?,
            status='scheduled',
            last_error=NULL,
            platform_results=CASE
                WHEN ? = 1 THEN '{}'
                ELSE platform_results
            END,
            updated_at=?
        WHERE id=?
        """,
        (
            r.clip_id,
            run_at,
            r.title,
            r.description,
            r.hashtags,
            new_platforms,
            1 if reset_results else 0,
            now(),
            sid
        )
    )

    return {
        "ok": True
    }


@app.post("/api/schedules/{sid}/unschedule")
def unschedule(sid: int):
    s = one(
        "SELECT * FROM schedules WHERE id=?",
        (sid,)
    )

    if not s:
        raise HTTPException(
            404,
            "Schedule not found."
        )

    if s["status"] == "published":
        raise HTTPException(
            409,
            "Published content cannot be unscheduled."
        )

    execute(
        """
        UPDATE schedules
        SET
            status='cancelled',
            last_error='Cancelled by user',
            updated_at=?
        WHERE id=?
        """,
        (
            now(),
            sid
        )
    )

    return {
        "ok": True
    }


@app.delete("/api/schedules/{sid}")
def delete(sid: int):
    s = one(
        "SELECT * FROM schedules WHERE id=?",
        (sid,)
    )

    if not s:
        raise HTTPException(
            404,
            "Schedule not found."
        )

    if s["status"] == "published":
        raise HTTPException(
            409,
            "Published schedule records cannot be deleted."
        )

    execute(
        "DELETE FROM schedules WHERE id=?",
        (sid,)
    )

    return {
        "ok": True
    }


@app.post("/api/settings/mode")
def mode(r: ModeReq):
    if r.mode not in (
        "manual",
        "review",
        "full"
    ):
        raise HTTPException(
            400,
            "invalid mode"
        )

    execute(
        """
        INSERT OR REPLACE INTO settings
        (key,value)
        VALUES('automation_mode',?)
        """,
        (r.mode,)
    )

    return {
        "mode": r.mode
    }


@app.post("/api/settings/auto")
def auto(r: AutoReq):
    execute(
        """
        INSERT OR REPLACE INTO settings
        (key,value)
        VALUES('auto_schedule_enabled',?)
        """,
        ("1" if r.enabled else "0",)
    )

    return {
        "enabled": r.enabled
    }


@app.post("/api/auto-schedule")
def autoschedule():
    ss = settings()

    slots = json.loads(
        ss.get(
            "daily_slots",
            '["10:00","14:00","19:30"]'
        )
    )

    tz = ZoneInfo(
        ss.get(
            "timezone",
            TIMEZONE
        )
    )

    nowdt = datetime.now(tz)

    ready = execute(
        """
        SELECT
            id,
            title,
            description,
            hashtags
        FROM clips
        WHERE status='ready'
        ORDER BY id
        """,
        fetch=True
    )

    used = {
        x["clip_id"]
        for x in execute(
            """
            SELECT clip_id
            FROM schedules
            WHERE status NOT IN ('failed','cancelled')
            """,
            fetch=True
        )
    }

    used_slots = {
        x["run_at"]
        for x in execute(
            """
            SELECT run_at
            FROM schedules
            WHERE status NOT IN ('failed','cancelled')
            """,
            fetch=True
        )
    }

    made = 0

    for c in ready:

        if c["id"] in used:
            continue

        scheduled = False

        for off in range(60):

            d = (
                nowdt + timedelta(days=off)
            ).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0
            )

            for slot in slots:

                h, m = map(
                    int,
                    slot.split(":")
                )

                dt = d.replace(
                    hour=h,
                    minute=m
                )

                if dt <= nowdt:
                    continue

                run_at = dt.isoformat()

                if run_at in used_slots:
                    continue

                hashtags = c["hashtags"] or ""

                try:
                    tags = json.loads(
                        hashtags
                    )

                    if isinstance(tags, list):
                        hashtags = " ".join(
                            str(x)
                            for x in tags
                        )
                except Exception:
                    pass

                execute(
                    """
                    INSERT INTO schedules
                    (
                        clip_id,
                        run_at,
                        title,
                        description,
                        hashtags,
                        platforms,
                        status,
                        created_at,
                        updated_at,
                        platform_results
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c["id"],
                        run_at,
                        c["title"] or "",
                        c["description"] or "",
                        hashtags,
                        '["youtube","instagram","facebook"]',
                        "scheduled",
                        now(),
                        now(),
                        "{}"
                    )
                )

                used_slots.add(run_at)
                used.add(c["id"])

                made += 1
                scheduled = True

                break

            if scheduled:
                break

    return {
        "scheduled": made,
        "slots_per_day": len(slots)
    }


def publisher():
    while True:
        try:
            ss = settings()

            if ss.get(
                "auto_schedule_enabled"
            ) == "1":
                autoschedule()

            if ss.get(
                "automation_mode"
            ) == "full":

                now_utc = datetime.now(
                    timezone.utc
                )

                schedules = execute(
                    """
                    SELECT *
                    FROM schedules
                    WHERE status IN ('scheduled','failed')
                    ORDER BY run_at
                    """,
                    fetch=True
                )

                for s in schedules:

                    run_at = datetime.fromisoformat(
                        s["run_at"]
                    )

                    if run_at.tzinfo is None:
                        run_at = run_at.replace(
                            tzinfo=ZoneInfo(TIMEZONE)
                        )

                    if (
                        run_at.astimezone(
                            timezone.utc
                        ) > now_utc
                    ):
                        continue

                    # Give failed jobs a short retry delay.
                    if s["status"] == "failed":
                        try:
                            updated = datetime.fromisoformat(
                                s["updated_at"]
                            )

                            if updated.tzinfo is None:
                                updated = updated.replace(
                                    tzinfo=timezone.utc
                                )

                            age = (
                                now_utc
                                - updated.astimezone(
                                    timezone.utc
                                )
                            ).total_seconds()

                            if age < 60:
                                continue

                        except Exception:
                            pass

                    publish(
                        dict(s)
                    )

        except Exception:
            traceback.print_exc()

        time.sleep(
            SCHEDULER_INTERVAL_SECONDS
        )


def publish(s):
    schedule_id = s["id"]

    raw_results = s.get(
        "platform_results"
    ) or "{}"

    try:
        platform_results = json.loads(
            raw_results
        )

        if not isinstance(
            platform_results,
            dict
        ):
            platform_results = {}

    except Exception:
        platform_results = {}

    try:
        execute(
            """
            UPDATE schedules
            SET
                status='uploading',
                attempts=attempts+1,
                updated_at=?
            WHERE id=?
            """,
            (
                now(),
                schedule_id
            )
        )

        c = one(
            "SELECT * FROM clips WHERE id=?",
            (s["clip_id"],)
        )

        if not c:
            raise ValueError(
                f"Clip {s['clip_id']} not found"
            )

        upload_title = (
            s["title"]
            or c["title"]
            or ""
        )

        desc = (
            s["description"]
            or c.get("description", "")
            or ""
        )

        hashtags = (
            s["hashtags"]
            or c.get("hashtags", "")
            or ""
        )

        try:
            tags = json.loads(
                hashtags
            )

            if isinstance(tags, list):
                hashtags = " ".join(
                    str(x)
                    for x in tags
                )

        except Exception:
            pass

        if hashtags and hashtags not in desc:
            desc += "\n\n" + hashtags

        platforms = json.loads(
            s["platforms"]
        )

        for p in platforms:

            if p in platform_results:
                print(
                    f"[Publisher] "
                    f"Skipping {p} - "
                    f"already successful: "
                    f"{platform_results[p]}"
                )
                continue

            print(
                f"[Publisher] "
                f"Uploading to {p}..."
            )

            if p == "youtube":

                upload_result = youtube_upload(
                    c["path"],
                    upload_title,
                    desc
                )

            elif p in (
                "instagram",
                "facebook"
            ):

                upload_result = meta_upload(
                    c["path"],
                    upload_title,
                    desc,
                    p
                )

            else:
                raise ValueError(
                    f"Unsupported platform: {p}"
                )

            platform_results[p] = upload_result

            execute(
                """
                UPDATE schedules
                SET
                    platform_results=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    json.dumps(
                        platform_results
                    ),
                    now(),
                    schedule_id
                )
            )

            print(
                f"[Publisher] "
                f"{p} upload successful: "
                f"{upload_result}"
            )

        missing = [
            p
            for p in platforms
            if p not in platform_results
        ]

        if missing:

            execute(
                """
                UPDATE schedules
                SET
                    status='failed',
                    last_error=?,
                    platform_results=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    "Platforms not completed: "
                    + ", ".join(missing),
                    json.dumps(
                        platform_results
                    ),
                    now(),
                    schedule_id
                )
            )

            return

        execute(
            """
            UPDATE schedules
            SET
                status='published',
                last_error=NULL,
                platform_results=?,
                updated_at=?
            WHERE id=?
            """,
            (
                json.dumps(
                    platform_results
                ),
                now(),
                schedule_id
            )
        )

        print(
            f"[Publisher] "
            f"Schedule {schedule_id} "
            f"published successfully."
        )

    except Exception as e:

        try:
            saved_results = json.dumps(
                platform_results
            )
        except Exception:
            saved_results = "{}"

        execute(
            """
            UPDATE schedules
            SET
                status='failed',
                last_error=?,
                platform_results=?,
                updated_at=?
            WHERE id=?
            """,
            (
                str(e)[:1000],
                saved_results,
                now(),
                schedule_id
            )
        )

        print(
            f"[Publisher] "
            f"Schedule {schedule_id} failed: "
            f"{str(e)[:1000]}"
        )


threading.Thread(
    target=publisher,
    daemon=True
).start()


HTML = """
<!doctype html>
<html>

<head>

<meta charset="utf-8">

<title>ClipForge Local V2</title>

<style>

*{
    box-sizing:border-box;
}

body{
    font-family:Arial,sans-serif;
    background:#101114;
    color:#eee;
    margin:0;
}

header{
    padding:18px 24px;
    background:#181a20;
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:15px;
}

.wrap{
    max-width:1200px;
    margin:auto;
    padding:20px;
}

.card{
    background:#191b21;
    border:1px solid #2b2e36;
    border-radius:12px;
    padding:16px;
    margin-bottom:16px;
}

.grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:16px;
}

button,
input,
select,
textarea{
    background:#101216;
    color:#eee;
    border:1px solid #353944;
    border-radius:8px;
    padding:10px;
    font:inherit;
}

button{
    cursor:pointer;
}

button:hover{
    border-color:#668cff;
}

button.primary{
    background:#315dcc;
    border-color:#315dcc;
}

button.danger{
    background:#542727;
    border-color:#7d3b3b;
}

button.warning{
    background:#59471d;
    border-color:#80652a;
}

.row{
    display:flex;
    gap:8px;
    align-items:center;
}

.row>*{
    flex:1;
}

.small{
    font-size:12px;
    color:#a8acb7;
}

#calendar{
    display:grid;
    grid-template-columns:repeat(7,1fr);
    gap:5px;
}

.day{
    min-height:120px;
    background:#121419;
    border:1px solid #2a2d34;
    border-radius:7px;
    padding:7px;
}

.event{
    margin-top:6px;
    padding:7px;
    background:#303745;
    border-radius:6px;
    font-size:12px;
    cursor:pointer;
}

.event.failed{
    background:#5a2929;
}

.event.published{
    background:#294a37;
}

.event.uploading{
    background:#574b28;
}

.clip{
    border-top:1px solid #292c34;
    padding:13px 10px;
}

.clip-title{
    font-size:15px;
    font-weight:bold;
    margin-bottom:5px;
}

.clip-description{
    color:#c4c7d0;
    font-size:13px;
    line-height:1.4;
    margin:5px 0;
}

.clip-tags{
    color:#8fa7ff;
    font-size:12px;
    margin:5px 0 10px;
}

.modal-backdrop{
    position:fixed;
    inset:0;
    background:rgba(0,0,0,.72);
    display:flex;
    align-items:center;
    justify-content:center;
    z-index:1000;
    padding:20px;
}

.modal{
    width:min(560px,100%);
    background:#191b21;
    border:1px solid #353944;
    border-radius:14px;
    box-shadow:0 20px 60px rgba(0,0,0,.5);
    padding:20px;
}

.modal h2{
    margin-top:0;
}

.form-group{
    margin:14px 0;
}

.form-group label{
    display:block;
    margin-bottom:7px;
    font-size:13px;
    color:#bfc3ce;
}

.form-group input,
.form-group textarea,
.form-group select{
    width:100%;
}

.datetime-row{
    display:grid;
    grid-template-columns:1.4fr 1fr;
    gap:10px;
}

.quick-times{
    display:flex;
    flex-wrap:wrap;
    gap:6px;
    margin-top:8px;
}

.quick-times button{
    padding:7px 10px;
    font-size:12px;
}

.modal-actions{
    display:flex;
    gap:8px;
    justify-content:flex-end;
    margin-top:20px;
    flex-wrap:wrap;
}

.status-pill{
    display:inline-block;
    padding:4px 8px;
    border-radius:20px;
    background:#30343e;
    font-size:11px;
}

.hidden{
    display:none !important;
}

@media(max-width:800px){

    .grid{
        grid-template-columns:1fr;
    }

    #calendar{
        grid-template-columns:repeat(2,1fr);
    }

    header{
        flex-direction:column;
        align-items:flex-start;
    }

    .datetime-row{
        grid-template-columns:1fr;
    }
}

</style>

</head>

<body>

<header>

<div>
    <b>ClipForge Local V2</b>
    <div class="small">
        3 posts/day · Asia/Kolkata
    </div>
</div>

<div class="row">

<button onclick="autoSchedule()">
    Auto-schedule
</button>

<button onclick="load()">
    Refresh
</button>

</div>

</header>


<div class="wrap">


<div class="grid">


<div class="card">

<h3>Process YouTube URL</h3>

<div class="row">

<input
    id="url"
    placeholder="YouTube URL"
/>

<button
    class="primary"
    onclick="job()"
>
    Process
</button>

</div>

<p class="small">
Local CPU processing. Use content you own or are authorized to use.
</p>

</div>


<div class="card">

<h3>Automation</h3>

<div class="row">

<select
    id="mode"
    onchange="setMode()"
>

<option value="manual">
Manual
</option>

<option value="review">
Review
</option>

<option value="full">
Full Automation
</option>

</select>

<button onclick="toggle()">
    Auto-schedule:
    <span id="aut">off</span>
</button>

</div>

<p class="small">
Default slots: 10:00, 14:00, 19:30.
Cross-posting counts as one content slot.
</p>

<div id="prov" class="small"></div>

</div>


</div>


<div class="card">

<h3>

Calendar

<button onclick="pm()">
    ‹
</button>

<span id="mon"></span>

<button onclick="nm()">
    ›
</button>

</h3>

<div id="calendar"></div>

</div>


<div class="card">

<h3>Clips</h3>

<div id="clips"></div>

</div>


<div class="card">

<h3>Jobs</h3>

<div id="jobs"></div>

</div>


</div>


<!-- SCHEDULER MODAL -->

<div
    id="scheduleModal"
    class="modal-backdrop hidden"
>

<div class="modal">

<h2 id="modalTitle">
    Schedule Clip
</h2>

<div class="small" id="modalClipInfo"></div>


<div class="form-group">

<label>
    Date
</label>

<input
    id="scheduleDate"
    type="date"
    required
>

</div>


<div class="form-group">

<label>
    Time
</label>

<input
    id="scheduleTime"
    type="time"
    step="300"
    required
>

<div class="quick-times">

<button
    type="button"
    onclick="setQuickTime('10:00')"
>
    10:00
</button>

<button
    type="button"
    onclick="setQuickTime('14:00')"
>
    14:00
</button>

<button
    type="button"
    onclick="setQuickTime('19:30')"
>
    19:30
</button>

<button
    type="button"
    onclick="setQuickTime('09:00')"
>
    09:00
</button>

<button
    type="button"
    onclick="setQuickTime('12:00')"
>
    12:00
</button>

<button
    type="button"
    onclick="setQuickTime('18:00')"
>
    18:00
</button>

</div>

</div>


<div class="form-group">

<label>
    Title
</label>

<input
    id="scheduleTitle"
    placeholder="AI-generated title"
/>

</div>


<div class="form-group">

<label>
    Description
</label>

<textarea
    id="scheduleDescription"
    rows="3"
></textarea>

</div>


<div class="form-group">

<label>
    Hashtags
</label>

<input
    id="scheduleHashtags"
    placeholder="#shorts #history"
/>

</div>


<div class="form-group">

<label>
    Platforms
</label>

<div class="row">

<label>
    <input
        id="platformYoutube"
        type="checkbox"
    >
    YouTube
</label>

<label>
    <input
        id="platformInstagram"
        type="checkbox"
    >
    Instagram
</label>

<label>
    <input
        id="platformFacebook"
        type="checkbox"
    >
    Facebook
</label>

</div>

</div>


<div
    id="modalStatus"
    class="small"
></div>


<div class="modal-actions">

<button
    id="unscheduleBtn"
    class="warning"
    onclick="unscheduleCurrent()"
>
    Unschedule
</button>

<button
    id="deleteBtn"
    class="danger"
    onclick="deleteCurrent()"
>
    Delete
</button>

<button
    onclick="closeModal()"
>
    Cancel
</button>

<button
    class="primary"
    onclick="saveSchedule()"
>
    Save
</button>

</div>

</div>

</div>


<script>

let S = [];
let C = [];

let M = new Date();
M.setDate(1);

let editingScheduleId = null;
let selectedClipId = null;

const APP_TIMEZONE = "Asia/Kolkata";

const $ = x =>
    document.getElementById(x);


async function api(u,o){

    let r = await fetch(u,o);

    let data = {};

    try{
        data = await r.json();
    }catch(e){}

    if(!r.ok){
        throw new Error(
            data.detail ||
            "Request failed"
        );
    }

    return data;
}


function escapeHtml(value){

    return String(value ?? "")
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}


function dateInAppTimezone(){

    let parts =
        new Intl.DateTimeFormat(
            "en-CA",
            {
                timeZone:APP_TIMEZONE,
                year:"numeric",
                month:"2-digit",
                day:"2-digit"
            }
        ).formatToParts(new Date());

    let get =
        type =>
            parts.find(
                x => x.type === type
            ).value;

    return (
        get("year") +
        "-" +
        get("month") +
        "-" +
        get("day")
    );
}


function timeInAppTimezone(){

    let parts =
        new Intl.DateTimeFormat(
            "en-GB",
            {
                timeZone:APP_TIMEZONE,
                hour:"2-digit",
                minute:"2-digit",
                hourCycle:"h23"
            }
        ).formatToParts(new Date());

    let get =
        type =>
            parts.find(
                x => x.type === type
            ).value;

    return (
        get("hour") +
        ":" +
        get("minute")
    );
}


function nextHourInAppTimezone(){

    let now = new Date();

    now.setMinutes(
        now.getMinutes() + 60
    );

    let parts =
        new Intl.DateTimeFormat(
            "en-CA",
            {
                timeZone:APP_TIMEZONE,
                year:"numeric",
                month:"2-digit",
                day:"2-digit",
                hour:"2-digit",
                minute:"2-digit",
                hourCycle:"h23"
            }
        ).formatToParts(now);

    let get =
        type =>
            parts.find(
                x => x.type === type
            ).value;

    return {
        date:
            get("year") +
            "-" +
            get("month") +
            "-" +
            get("day"),

        time:
            get("hour") +
            ":" +
            get("minute")
    };
}


function setQuickTime(value){

    $("scheduleTime").value =
        value;

}


function openNewSchedule(clipId){

    let clip =
        C.find(
            x => Number(x.id) === Number(clipId)
        );

    if(!clip) return;

    editingScheduleId = null;
    selectedClipId = Number(clipId);

    $("modalTitle").textContent =
        "Schedule Clip";

    $("modalClipInfo").textContent =
        `Clip ${clip.id} · ${clip.title}`;

    let next =
        nextHourInAppTimezone();

    $("scheduleDate").value =
        next.date;

    $("scheduleTime").value =
        next.time;

    $("scheduleTitle").value =
        clip.title || "";

    $("scheduleDescription").value =
        clip.description || "";

    let tags = "";

    try{

        let parsed =
            JSON.parse(
                clip.hashtags || "[]"
            );

        if(Array.isArray(parsed)){
            tags = parsed.join(" ");
        }else{
            tags = clip.hashtags || "";
        }

    }catch(e){

        tags =
            clip.hashtags || "";

    }

    $("scheduleHashtags").value =
        tags;

    $("platformYoutube").checked =
        true;

    $("platformInstagram").checked =
        false;

    $("platformFacebook").checked =
        false;

    $("modalStatus").innerHTML =
        "Choose a date and time using the calendar/clock.";

    $("unscheduleBtn").classList.add(
        "hidden"
    );

    $("deleteBtn").classList.add(
        "hidden"
    );

    $("scheduleModal").classList.remove(
        "hidden"
    );

}


function openEditSchedule(id){

    let s =
        S.find(
            x => Number(x.id) === Number(id)
        );

    if(!s) return;

    editingScheduleId =
        Number(id);

    selectedClipId =
        Number(s.clip_id);

    $("modalTitle").textContent =
        "Edit Scheduled Clip";

    $("modalClipInfo").textContent =
        `Clip ${s.clip_id} · ${s.title || "Untitled"}`;

    $("scheduleDate").value =
        s.run_at.slice(0,10);

    $("scheduleTime").value =
        s.run_at.slice(11,16);

    $("scheduleTitle").value =
        s.title || "";

    $("scheduleDescription").value =
        s.description || "";

    $("scheduleHashtags").value =
        s.hashtags || "";

    let platforms = [];

    try{
        platforms =
            JSON.parse(
                s.platforms || "[]"
            );
    }catch(e){}

    $("platformYoutube").checked =
        platforms.includes("youtube");

    $("platformInstagram").checked =
        platforms.includes("instagram");

    $("platformFacebook").checked =
        platforms.includes("facebook");

    $("modalStatus").innerHTML =
        `<span class="status-pill">${escapeHtml(s.status)}</span>`;

    let canManage =
        s.status !== "published";

    $("unscheduleBtn").classList.toggle(
        "hidden",
        !canManage ||
        s.status === "cancelled"
    );

    $("deleteBtn").classList.toggle(
        "hidden",
        !canManage
    );

    $("scheduleModal").classList.remove(
        "hidden"
    );

}


function closeModal(){

    $("scheduleModal").classList.add(
        "hidden"
    );

    editingScheduleId = null;
    selectedClipId = null;
}


async function saveSchedule(){

    let date =
        $("scheduleDate").value;

    let time =
        $("scheduleTime").value;

    if(!date || !time){

        alert(
            "Please choose a date and time."
        );

        return;
    }

    let platforms = [];

    if($("platformYoutube").checked)
        platforms.push("youtube");

    if($("platformInstagram").checked)
        platforms.push("instagram");

    if($("platformFacebook").checked)
        platforms.push("facebook");

    if(platforms.length === 0){

        alert(
            "Select at least one platform."
        );

        return;
    }

    let body = {

        clip_id:selectedClipId,

        run_at:
            date +
            "T" +
            time,

        title:
            $("scheduleTitle").value.trim(),

        description:
            $("scheduleDescription").value.trim(),

        hashtags:
            $("scheduleHashtags").value.trim(),

        platforms:platforms
    };

    try{

        if(editingScheduleId){

            await api(
                "/api/schedules/" +
                editingScheduleId,
                {
                    method:"PUT",
                    headers:{
                        "Content-Type":
                        "application/json"
                    },
                    body:JSON.stringify(body)
                }
            );

        }else{

            await api(
                "/api/schedules",
                {
                    method:"POST",
                    headers:{
                        "Content-Type":
                        "application/json"
                    },
                    body:JSON.stringify(body)
                }
            );

        }

        closeModal();

        await load();

    }catch(e){

        alert(
            e.message
        );

    }

}


async function unscheduleCurrent(){

    if(!editingScheduleId)
        return;

if(
    !confirm(
        "Unschedule this video?\\n\\nIt will stay in ClipForge but will no longer be published at its scheduled time."
        )
){
        return;
    }

    try{

        await api(
            "/api/schedules/" +
            editingScheduleId +
            "/unschedule",
            {
                method:"POST"
            }
        );

        closeModal();

        await load();

    }catch(e){

        alert(
            e.message
        );

    }

}


async function deleteCurrent(){

    if(!editingScheduleId)
        return;

    if(
        !confirm(
            "Delete this schedule permanently?\\n\\nThis removes the schedule from ClipForge. It does not delete anything already published on social media."
        )
    ){
        return;
    }

    try{

        await api(
            "/api/schedules/" +
            editingScheduleId,
            {
                method:"DELETE"
            }
        );

        closeModal();

        await load();

    }catch(e){

        alert(
            e.message
        );

    }

}


async function load(){

    try{

        [S,C] =
            await Promise.all([
                api("/api/schedules"),
                api("/api/clips")
            ]);

        let j =
            await api("/api/jobs");

        let p =
            await api("/api/providers");

        let s =
            await api("/api/settings");


        $("jobs").innerHTML =
            j.map(
                x =>
                `<div>
                    <b>
                        ${escapeHtml(
                            x.title ||
                            x.source
                        )}
                    </b>
                    — ${escapeHtml(x.status)}
                    ${x.progress}%
                    <span class="small">
                        ${escapeHtml(
                            x.message || ""
                        )}
                    </span>
                </div>`
            ).join("")
            ||
            '<span class="small">No jobs yet.</span>';


        $("clips").innerHTML =
            C.map(
                x => {

                    let tags = "";

                    try{

                        let parsed =
                            JSON.parse(
                                x.hashtags ||
                                "[]"
                            );

                        if(Array.isArray(parsed)){
                            tags =
                                parsed.join(" ");
                        }else{
                            tags =
                                x.hashtags || "";
                        }

                    }catch(e){

                        tags =
                            x.hashtags || "";

                    }

                    return `
                    <div class="clip">

                        <div class="clip-title">
                            ${escapeHtml(
                                x.title
                            )}
                        </div>

                        <div class="small">
                            ${Math.round(
                                x.duration || 0
                            )}s
                            · ID ${x.id}
                        </div>

                        ${
                            x.description
                            ?
                            `<div class="clip-description">
                                ${escapeHtml(
                                    x.description
                                )}
                            </div>`
                            :
                            ""
                        }

                        ${
                            tags
                            ?
                            `<div class="clip-tags">
                                ${escapeHtml(tags)}
                            </div>`
                            :
                            ""
                        }

                        <button
                            class="primary"
                            onclick="openNewSchedule(${x.id})"
                        >
                            Schedule
                        </button>

                    </div>
                    `;

                }
            ).join("")
            ||
            '<span class="small">No clips yet.</span>';


        $("mode").value =
            s.automation_mode ||
            "review";

        $("aut").textContent =
            s.auto_schedule_enabled === "1"
                ? "on"
                : "off";

        $("prov").textContent =
            "Configured: " +
            Object.keys(p)
                .filter(
                    k => p[k]
                )
                .join(", ");


        cal();

    }catch(e){

        console.error(e);

    }

}


async function job(){

    let u =
        $("url").value.trim();

    if(!u) return;

    try{

        await api(
            "/api/jobs",
            {
                method:"POST",
                headers:{
                    "Content-Type":
                    "application/json"
                },
                body:JSON.stringify({
                    url:u
                })
            }
        );

        $("url").value = "";

        load();

    }catch(e){

        alert(
            e.message
        );

    }

}


async function autoSchedule(){

    try{

        let r =
            await api(
                "/api/auto-schedule",
                {
                    method:"POST"
                }
            );

        alert(
            "Scheduled " +
            r.scheduled +
            " clips into " +
            r.slots_per_day +
            " daily slots."
        );

        load();

    }catch(e){

        alert(
            e.message
        );

    }

}


async function setMode(){

    try{

        await api(
            "/api/settings/mode",
            {
                method:"POST",
                headers:{
                    "Content-Type":
                    "application/json"
                },
                body:JSON.stringify({
                    mode:
                        $("mode").value
                })
            }
        );

        load();

    }catch(e){

        alert(
            e.message
        );

    }

}


async function toggle(){

    let s =
        await api(
            "/api/settings"
        );

    await api(
        "/api/settings/auto",
        {
            method:"POST",
            headers:{
                "Content-Type":
                "application/json"
            },
            body:JSON.stringify({
                enabled:
                    s.auto_schedule_enabled !== "1"
            })
        }
    );

    load();

}


function pm(){

    M.setMonth(
        M.getMonth() - 1
    );

    cal();

}


function nm(){

    M.setMonth(
        M.getMonth() + 1
    );

    cal();

}


function cal(){

    let y =
        M.getFullYear();

    let m =
        M.getMonth();

    let first =
        new Date(
            y,
            m,
            1
        );

    let start =
        first.getDay();

    let days =
        new Date(
            y,
            m + 1,
            0
        ).getDate();

    $("mon").textContent =
        M.toLocaleString(
            undefined,
            {
                month:"long",
                year:"numeric"
            }
        );


    let h =
        [
            "Sun",
            "Mon",
            "Tue",
            "Wed",
            "Thu",
            "Fri",
            "Sat"
        ]
        .map(
            x =>
            `<div
                class="day"
                style="min-height:30px"
            >
                <b>${x}</b>
            </div>`
        )
        .join("");


    for(
        let i = 0;
        i < start;
        i++
    ){

        h +=
            '<div class="day"></div>';

    }


    for(
        let d = 1;
        d <= days;
        d++
    ){

        let k =
            `${y}-${String(m+1).padStart(2,"0")}-${String(d).padStart(2,"0")}`;

        h +=
            `<div
                class="day"
            >
                <b>${d}</b>`;


        S.filter(
            s =>
                s.status !== "cancelled" &&
                s.run_at.slice(0,10) === k
        ).forEach(
            s => {

                h +=
                    `<div
                        class="event ${escapeHtml(
                            s.status
                        )}"
                        onclick="openEditSchedule(${s.id})"
                    >
                        <b>
                            ${escapeHtml(
                                s.run_at.slice(
                                    11,
                                    16
                                )
                            )}
                        </b>
                        ·
                        ${escapeHtml(
                            s.title ||
                            "Clip " +
                            s.clip_id
                        )}

                        <div class="small">
                            ${escapeHtml(
                                s.status
                            )}
                        </div>

                    </div>`;

            }
        );


        h +=
            "</div>";

    }


    $("calendar").innerHTML =
        h;

}


load();


setInterval(
    load,
    10000
);


/* Close modal when clicking outside it. */

$("scheduleModal")
    .addEventListener(
        "click",
        function(e){

            if(
                e.target ===
                $("scheduleModal")
            ){
                closeModal();
            }

        }
    );


/*
Allow Escape key to close the scheduler.
*/

document.addEventListener(
    "keydown",
    function(e){

        if(
            e.key === "Escape"
        ){
            closeModal();
        }

    }
);

</script>

</body>

</html>
"""


@app.get("/api/media")
def media(path: str):

    p = Path(path).resolve()

    if OUTPUT.resolve() not in p.parents:
        raise HTTPException(
            403
        )

    from fastapi.responses import FileResponse

    return FileResponse(p)


if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host=HOST,
        port=PORT
    )