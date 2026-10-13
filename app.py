import re
import subprocess
import threading
import uuid
from pathlib import Path

import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent

tasks = {}


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()


def pick_audio_format(formats):
    """Best audio-only format from the list."""
    audio = [
        f for f in formats
        if f.get("vcodec") == "none"
        and f.get("acodec") not in (None, "none")
        and f.get("url")
    ]
    if not audio:
        return None
    audio.sort(key=lambda f: (f.get("abr") or 0), reverse=True)
    return audio[0]


def pick_video_format(formats, quality):
    """Best video-only format matching quality."""
    video = [
        f for f in formats
        if f.get("vcodec") not in (None, "none")
        and f.get("acodec") in (None, "none")
        and f.get("url")
    ]
    if not video:
        # Fallback: any video stream
        video = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("url")]
    if not video:
        return None

    if quality == "best":
        video.sort(key=lambda f: (f.get("height") or 0), reverse=True)
    elif quality == "worst":
        video.sort(key=lambda f: (f.get("height") or 0))
    else:
        try:
            h = int(quality)
        except ValueError:
            h = 720
        filtered = [f for f in video if (f.get("height") or 0) <= h]
        if filtered:
            video = filtered
        video.sort(key=lambda f: (f.get("height") or 0), reverse=True)

    return video[0]


def extract_links(url: str, mode: str, quality: str):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "ignoreerrors": True,
        # Uncomment if YouTube requires sign-in:
        # "cookiefile": str(BASE_DIR / "cookies.txt"),
        # "cookiesfrombrowser": ("firefox",),
    }

    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if info is None:
            return results

        entries = info.get("entries") if info.get("_type") == "playlist" else [info]
        entries = [e for e in (entries or []) if e]

        for idx, entry in enumerate(entries, start=1):
            try:
                title = entry.get("title") or f"video_{idx}"
                formats = entry.get("formats") or []
                video_id = entry.get("id")
                webpage_url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"

                safe_title = sanitize_filename(title)

                if mode == "mp3":
                    fmt = pick_audio_format(formats)
                    if not fmt:
                        results.append({
                            "index": idx,
                            "title": title,
                            "error": "No audio stream available",
                        })
                        continue
                    results.append({
                        "index": idx,
                        "title": title,
                        "filename": f"{idx:02d} - {safe_title}.m4a",
                        "url": fmt["url"],
                        "ext": fmt.get("ext") or "m4a",
                        "quality": f"{fmt.get('abr', '?')} kbps",
                        "filesize": fmt.get("filesize") or fmt.get("filesize_approx"),
                        "mode": "direct",
                    })
                else:
                    # MP4 mode: return a proxy URL that merges on the fly
                    results.append({
                        "index": idx,
                        "title": title,
                        "filename": f"{idx:02d} - {safe_title}.mp4",
                        "video_id": video_id,
                        "webpage_url": webpage_url,
                        "mode": "stream",  # client will call /api/stream
                        "quality": f"{quality}p" if quality.isdigit() else quality,
                    })
            except Exception as e:
                results.append({
                    "index": idx,
                    "title": entry.get("title") or f"video_{idx}",
                    "error": str(e),
                })

    return results


def run_extract(task_id: str, url: str, mode: str, quality: str):
    try:
        tasks[task_id]["status"] = "running"
        links = extract_links(url, mode, quality)
        tasks[task_id]["links"] = links
        tasks[task_id]["status"] = "done"
    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/extract", methods=["POST"])
def extract():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    mode = data.get("mode", "mp4")
    quality = data.get("quality", "720")

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if mode not in ("mp4", "mp3"):
        return jsonify({"error": "Invalid mode"}), 400

    task_id = uuid.uuid4().hex
    tasks[task_id] = {
        "status": "queued",
        "url": url,
        "mode": mode,
        "quality": quality,
    }

    threading.Thread(
        target=run_extract,
        args=(task_id, url, mode, quality),
        daemon=True,
    ).start()

    return jsonify({"task_id": task_id})


@app.route("/api/status/<task_id>")
def status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@app.route("/api/stream")
def stream():
    """
    Stream a merged MP4 (video+audio) directly to the browser.
    Nothing is saved to disk — yt-dlp pipes to stdout, FFmpeg muxes,
    and we stream the result to the client.
    """
    video_url = request.args.get("video_url")
    quality = request.args.get("quality", "720")
    filename = request.args.get("filename", "video.mp4")

    if not video_url:
        return jsonify({"error": "video_url required"}), 400

    # Resolve direct stream URLs for the specific video
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        # Uncomment if YouTube requires sign-in:
        # "cookiefile": str(BASE_DIR / "cookies.txt"),
        # "cookiesfrombrowser": ("firefox",),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            formats = info.get("formats") or []
            vfmt = pick_video_format(formats, quality)
            afmt = pick_audio_format(formats)
            if not vfmt or not afmt:
                return jsonify({"error": "Could not find compatible streams"}), 400
            v_url = vfmt["url"]
            a_url = afmt["url"]
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Use FFmpeg to merge video + audio and pipe to stdout
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-headers", "Referer: https://www.youtube.com/\r\n",
        "-i", v_url,
        "-headers", "Referer: https://www.youtube.com/\r\n",
        "-i", a_url,
        "-c:v", "copy",
        "-c:a", "aac",
        "-movflags", "frag_keyframe+empty_moov+faststart",
        "-f", "mp4",
        "pipe:1",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=10**7,
    )

    def generate():
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            proc.wait()

    return Response(
        stream_with_context(generate()),
        mimetype="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)