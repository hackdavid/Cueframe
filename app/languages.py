"""Narration languages. Ask in chat ("… in Hindi", "hindi mein …", or write in Devanagari)
and the narration and captions are in that language; maths, code and on-screen text stay
as they are. ElevenLabs' multilingual model speaks all of these with the same voice;
ELEVENLABS_VOICE_ID_<CODE> (e.g. ELEVENLABS_VOICE_ID_HI) picks another voice."""

import os
import re

LANGS = {
    "hi": ("Hindi", "हिन्दी"), "es": ("Spanish", "Español"), "ar": ("Arabic", "العربية"),
    "fr": ("French", "Français"), "de": ("German", "Deutsch"), "pt": ("Portuguese", "Português"),
    "bn": ("Bengali", "বাংলা"), "ur": ("Urdu", "اردو"), "ta": ("Tamil", "தமிழ்"),
    "te": ("Telugu", "తెలుగు"), "mr": ("Marathi", "मराठी"), "id": ("Indonesian", "Bahasa Indonesia"),
    "ja": ("Japanese", "日本語"), "zh": ("Chinese", "中文"), "ru": ("Russian", "Русский"),
    "it": ("Italian", "Italiano"), "tr": ("Turkish", "Türkçe"),
}  # fmt: skip
_BY_NAME = {name.lower(): code for code, (name, _) in LANGS.items()} | {"hinglish": "hi"}
_NAMES = "|".join(_BY_NAME)
_ASK = re.compile(
    rf"\b(?:in|into|using|narrat\w*|speak\w*|voice\w*|explain\w*)\s+(?:the\s+)?({_NAMES})\b"
    rf"|\b({_NAMES})\s+(?:me(?:i)?n|language|narration|voice)\b",
    re.I,
)
_SCRIPTS = [("hi", r"[\u0900-\u097F]"), ("bn", r"[\u0980-\u09FF]"), ("ta", r"[\u0B80-\u0BFF]"),
            ("te", r"[\u0C00-\u0C7F]"), ("ar", r"[\u0600-\u06FF]"), ("ru", r"[\u0400-\u04FF]"),
            ("ja", r"[\u3040-\u30FF]"), ("zh", r"[\u4E00-\u9FFF]")]  # fmt: skip


def detect(text: str | None) -> str | None:
    """Language code asked for in `text`, or None for English."""
    if not text:
        return None
    if m := _ASK.search(text):
        code = _BY_NAME[(m.group(1) or m.group(2)).lower()]
        return None if code == "en" else code
    for code, pat in _SCRIPTS:
        if len(re.findall(pat, text)) >= 6:
            return code
    return None


def name(code: str | None) -> str:
    return LANGS[code][0] if code in LANGS else "English"


def instruction(code: str | None) -> str:
    """Planner instruction for narration in `code` ('' for English)."""
    if code not in LANGS:
        return ""
    lang, native = LANGS[code]
    return (
        f"\n\nLANGUAGE: write every spoken line (narration, intro, outro, 'say' and 'after' "
        f"fields) in {lang} ({native}), as a native {lang}-speaking teacher would say it: "
        f"natural, warm and simple, in {lang} script. Keep maths notation, LaTeX, code, "
        "commands, file names, URLs and anything typed or written on screen exactly as it "
        f"would be in English. Numbers and symbols may be read out in {lang}."
    )


def voice_id(code: str | None) -> str | None:
    return os.environ.get(f"ELEVENLABS_VOICE_ID_{code.upper()}") if code else None
