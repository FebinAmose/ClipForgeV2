import os

import yt_dlp

from .config import DOWNLOADS


def download_url(url):
    """
    Download a single authorized video source for ClipForge.

    YouTube JS challenges are handled through:
      - yt-dlp-ejs
      - Deno

    The downloader prefers the highest-quality video available up to
    1440p, then merges it with the best available audio.
    """

    out = str(
        DOWNLOADS / "%(title).160s [%(id)s].%(ext)s"
    )

    opts = {
        'outtmpl': out,

        # Prefer high-quality video up to 1440p.
        # Fall back to 1080p if necessary, then best available.
        'format': (
            'bv*[height<=1440]+ba/'
            'bv*[height<=1080]+ba/'
            'b'
        ),

        'merge_output_format': 'mp4',

        # Never download playlists when a URL points to one.
        'noplaylist': True,

        # Retry transient network/YouTube failures.
        'retries': 5,
        'fragment_retries': 5,

        # Keep CPU/network usage reasonable on the local PC.
        'concurrent_fragment_downloads': 2,

        # YouTube JS challenge solving.
        #
        # yt-dlp's Python API expects js_runtimes as a dictionary.
        'js_runtimes': {
            'deno': {}
        },

        # Allow yt-dlp to retrieve updated EJS components if required.
        'remote_components': {
            'ejs:github'
        },

        # Remux the final file to MP4 after downloading.
        'postprocessors': [
            {
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4'
            }
        ],
    }

    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(
            url,
            download=True
        )

        p = y.prepare_filename(info)

        # yt-dlp may return the pre-remux extension.
        # Check whether the MP4 produced by FFmpeg exists.
        if not p.lower().endswith('.mp4'):
            candidate = (
                os.path.splitext(p)[0]
                + '.mp4'
            )

            if os.path.exists(candidate):
                p = candidate

        if not os.path.exists(p):
            raise FileNotFoundError(
                f'Download completed but output file was not found: {p}'
            )

        return (
            p,
            info.get('title')
            or info.get('id')
            or 'source'
        )