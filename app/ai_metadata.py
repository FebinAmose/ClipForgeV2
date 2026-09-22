import json
import re
import requests

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "llama3.2:3b"

METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string"
        },
        "description": {
            "type": "string"
        },
        "hashtags": {
            "type": "array",
            "items": {
                "type": "string"
            }
        }
    },
    "required": [
        "title",
        "description",
        "hashtags"
    ]
}


def _clean_hashtags(values):
    """Normalize and validate AI-generated hashtags."""

    result = []

    for value in values:
        if not isinstance(value, str):
            continue

        value = value.strip()

        if not value:
            continue

        if not value.startswith("#"):
            value = "#" + value

        value = re.sub(r"[^#A-Za-z0-9_]", "", value)

        if len(value) < 2:
            continue

        if value.lower() not in [x.lower() for x in result]:
            result.append(value)

    return result[:5]


def _validate_metadata(data):
    """Validate structured Ollama output."""

    if not isinstance(data, dict):
        raise ValueError("AI response is not an object")

    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    hashtags = data.get("hashtags", [])

    if not title:
        raise ValueError("AI returned an empty title")

    if not description:
        raise ValueError("AI returned an empty description")

    if not isinstance(hashtags, list):
        raise ValueError("AI hashtags are not a list")

    hashtags = _clean_hashtags(hashtags)

    if len(hashtags) < 5:
        raise ValueError(
            f"AI returned only {len(hashtags)} valid hashtags"
        )

    # Clean title
    title = re.sub(r"\s+", " ", title).strip()
    title = title.strip('"').strip("'")

    # Keep titles short enough for social platforms
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0].rstrip(".,!?- ")

    # Clean description
    description = re.sub(r"\s+", " ", description).strip()
    description = description.strip('"').strip("'")

    if not title:
        raise ValueError("Title became empty after cleaning")

    if not description:
        raise ValueError("Description became empty after cleaning")

    return {
        "title": title,
        "description": description,
        "hashtags": hashtags,
    }


def _ask_ollama(transcript):
    prompt = f"""
Create social media metadata for this short video clip.

Requirements:

TITLE
- Interesting and natural.
- Accurate to the transcript.
- Maximum 60 characters.
- Do not use clickbait that contradicts the transcript.

DESCRIPTION
- One or two natural sentences.
- Explain what the clip is about.
- Do not invent facts.
- Do not mention that AI created it.

HASHTAGS
- Exactly 5 relevant hashtags.
- No generic spam hashtags such as #viral or #fyp unless genuinely relevant.

TRANSCRIPT:
{transcript}
""".strip()

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "format": METADATA_SCHEMA,
            "options": {
                "temperature": 0,
            },
        },
        timeout=180,
    )

    response.raise_for_status()

    payload = response.json()

    raw = payload.get("response", "").strip()

    if not raw:
        raise ValueError("Ollama returned an empty response")

    data = json.loads(raw)

    return _validate_metadata(data)


def _fallback_metadata(transcript):
    """Safe fallback when local AI fails."""

    words = transcript.strip().split()

    if words:
        title = " ".join(words[:10]).strip(" ,.!?")

        if len(title) > 57:
            title = title[:57].rsplit(" ", 1)[0]

        title = title + "..."
    else:
        title = "Interesting Moment You Should See"

    return {
        "title": title[:60],
        "description": (
            "A short clip highlighting an interesting moment "
            "from the original video."
        ),
        "hashtags": [
            "#shorts",
            "#interesting",
            "#story",
            "#insights",
            "#video",
        ],
    }


def generate_metadata(transcript):
    """
    Generate metadata with structured Ollama output.

    One retry is used for transient Ollama failures.
    A safe fallback is returned if both attempts fail.
    """

    transcript = (transcript or "").strip()

    if not transcript:
        print("[AI] Empty transcript. Using fallback metadata.")
        return _fallback_metadata(transcript)

    for attempt in range(1, 3):

        try:
            result = _ask_ollama(transcript)

            print(
                f"[AI] Metadata generated successfully "
                f"(attempt {attempt})"
            )

            return result

        except Exception as error:

            print(
                f"[AI] Metadata attempt {attempt} failed: {error}"
            )

            if attempt == 1:
                print("[AI] Retrying once...")

    print("[AI] Both AI attempts failed.")
    print("[AI] Using fallback metadata.")

    return _fallback_metadata(transcript)
