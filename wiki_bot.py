import os
import sys
import time
import random
import re
import json
import base64
import html
import urllib.parse
import requests
from io import BytesIO
from PIL import Image
from atproto import Client, client_utils

# İngilizce Hesap (Birincil)
BSKY_HANDLE_EN = os.environ.get("BSKY_HANDLE_EN") or os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD_EN = os.environ.get("BSKY_APP_PASSWORD_EN") or os.environ.get("BSKY_APP_PASSWORD")

# Türkçe Hesap (İkincil)
BSKY_HANDLE_TR = os.environ.get("BSKY_TR_HANDLE")
BSKY_APP_PASSWORD_TR = os.environ.get("BSKY_TR_APP_PASSWORD")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
STATE_FILE = "posted_articles.txt"

HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/5.6 (https://bsky.app/; dual-language curated bot)"
}

GITHUB_API_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

TOTAL_BLUESKY_BUDGET = 300
FALLBACK_EMOJIS = ["📜", "🧐", "💡", "🔍", "✨", "🛸", "🧩"]

UNUSUAL_SOURCES = [
    {
        "lang": "en",
        "domain": "en.wikipedia.org",
        "page": "Wikipedia:Unusual_articles"
    },
    {
        "lang": "de",
        "domain": "de.wikipedia.org",
        "page": "Wikipedia:Kuriositätenkabinett"
    },
    {
        "lang": "es",
        "domain": "es.wikipedia.org",
        "page": "Wikipedia:Artículos_peculiares"
    },
    {
        "lang": "fr",
        "domain": "fr.wikipedia.org",
        "page": "Wikipédia:Articles_insolites"
    }
]

def clean_url(raw_url):
    if not raw_url:
        return raw_url
    match = re.search(r'https?://[^\s)\]"\']+', str(raw_url))
    return match.group(0) if match else raw_url

def get_posted_titles():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return set()

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}"
    try:
        resp = requests.get(url, headers=GITHUB_API_HEADERS, params={"ref": GITHUB_BRANCH}, timeout=15)
        if resp.status_code == 200:
            content_b64 = resp.json().get("content", "")
            text = base64.b64decode(content_b64).decode("utf-8")
            return set(line.strip() for line in text.splitlines() if line.strip())
    except Exception as e:
        print(f"Kayıt dosyası okunurken hata: {e}")
    return set()

def is_already_posted(cand, posted_lower_set):
    raw_title_lower = cand["title"].lower()
    prefixed_key_lower = f"{cand['lang']}:{cand['title']}".lower()
    return (raw_title_lower in posted_lower_set or prefixed_key_lower in posted_lower_set)

def save_posted_title(record_key, max_retries=5):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}"

    for attempt in range(1, max_retries + 1):
        current_text = ""
        sha = None

        resp = requests.get(url, headers=GITHUB_API_HEADERS, params={"ref": GITHUB_BRANCH}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            sha = data["sha"]
            current_text = base64.b64decode(data["content"]).decode("utf-8")

        if record_key in set(line.strip() for line in current_text.splitlines() if line.strip()):
            return

        new_text = current_text
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += f"{record_key}\n"

        payload = {
            "message": "chore: update posted wiki archive [skip ci]",
            "content": base64.b64encode(new_text.encode("utf-8")).decode("utf-8"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(url, headers=GITHUB_API_HEADERS, json=payload, timeout=15)
        if put_resp.status_code in (200, 201):
            print(f"'{record_key}' başarıyla arşive işlendi.")
            return
        elif put_resp.status_code in (409, 422):
            time.sleep(1.5)
            continue
        else:
            return

def extract_candidates_from_source(source):
    domain = source["domain"]
    page = source["page"]
    lang = source["lang"]

    api_url = clean_url(f"https://{domain}/w/api.php")
    params = {
        "action": "parse",
        "page": page,
        "prop": "text",
        "redirects": 1,
        "format": "json"
    }

    try:
        resp = requests.get(api_url, params=params, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return []

        html_text = resp.json().get("parse", {}).get("text", {}).get("*", "")
        li_blocks = re.findall(r'<li\b[^>]*>(.*?)</li>', html_text, flags=re.DOTALL | re.IGNORECASE)

        skip_prefixes = (
            "wikipedia:", "wikipédia:", "file:", "fichier:", "datei:", "archivo:",
            "help:", "aide:", "hilfe:", "ayuda:", "category:", "catégorie:",
            "kategorie:", "categoría:", "special:", "spezial:", "spécial:", "especial:",
            "talk:", "diskussion:", "discussion:", "discusión:", "template:", "modèle:",
            "vorlage:", "plantilla:", "portal:", "user:", "utilisateur:", "benutzer:", "usuario:",
            "mediawiki:"
        )

        candidates = []
        for li in li_blocks:
            links = re.findall(r'<a\s+[^>]*href=["\']/wiki/([^"#?:]+)["\'][^>]*>(.*?)</a>', li, flags=re.DOTALL | re.IGNORECASE)
            if not links:
                continue

            target_title = None
            for raw_slug, _ in links:
                decoded = urllib.parse.unquote(raw_slug).replace('_', ' ').strip()
                d_lower = decoded.lower()

                if any(d_lower.startswith(p) for p in skip_prefixes):
                    continue
                if d_lower.startswith(("list of", "liste de", "liste von", "lista de", "chronologie", "liste des")):
                    continue

                target_title = decoded
                break

            if not target_title:
                continue

            clean_note = re.sub(r'<[^>]+>', ' ', li)
            clean_note = re.sub(r'\s+', ' ', clean_note).strip()
            clean_note = html.unescape(clean_note)

            if len(clean_note) < 25:
                continue

            candidates.append({
                "lang": lang,
                "domain": domain,
                "title": target_title,
                "curation_note": clean_note
            })

        print(f"[{domain}] Listeden {len(candidates)} adet sıra dışı madde ayıklandı.")
        return candidates
    except Exception as e:
        print(f"Kaynak okuma hatası ({domain}): {e}")
        return []

def fetch_summary(domain, title):
    safe_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = clean_url(f"https://{domain}/api/rest_v1/page/summary/{safe_title}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

def get_turkish_wiki_page(domain, title):
    safe_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
    lang_code = domain.split(".")[0]

    if lang_code == "tr":
        return f"https://tr.wikipedia.org/wiki/{safe_title}", title

    api_url = clean_url(f"https://{domain}/w/api.php")
    params = {
        "action": "query",
        "titles": title,
        "prop": "langlinks",
        "lllang": "tr",
        "format": "json"
    }

    try:
        resp = requests.get(api_url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            pages = resp.json().get("query", {}).get("pages", {})
            for page_id, page_info in pages.items():
                langlinks = page_info.get("langlinks", [])
                if langlinks:
                    tr_title = langlinks[0].get("*")
                    if tr_title:
                        safe_tr = urllib.parse.quote(tr_title.replace(" ", "_"), safe="")
                        print(f"Türkçe karşılığı bulundu: {tr_title}")
                        return f"https://tr.wikipedia.org/wiki/{safe_tr}", tr_title
    except Exception as e:
        print(f"Türkçe dil bağlantısı sorgulama hatası: {e}")

    return None, None

def fetch_image_caption(domain, title, img_url):
    if not img_url:
        return None
    try:
        raw_filename = img_url.split('/')[-1]
        if re.match(r'^\d+px-', raw_filename):
            raw_filename = re.sub(r'^\d+px-', '', raw_filename)
        target_filename = urllib.parse.unquote(raw_filename).replace(' ', '_').lower()

        safe_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
        media_url = clean_url(f"https://{domain}/api/rest_v1/page/media-list/{safe_title}")

        resp = requests.get(media_url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            for item in items:
                item_file = item.get("title", "").replace("File:", "").replace(" ", "_").lower()
                if item_file and (item_file == target_filename or target_filename in item_file):
                    caption = item.get("caption", {}).get("text", "").strip()
                    if caption:
                        clean_caption = re.sub(r'<[^>]+>', '', caption)
                        return re.sub(r'\s+', ' ', clean_caption).strip()

            if items and items[0].get("caption", {}).get("text"):
                clean_first = re.sub(r'<[^>]+>', '', items[0]["caption"]["text"]).strip()
                if clean_first:
                    return clean_first
    except Exception as e:
        print(f"Görsel açıklaması alınamadı: {e}")
    return None

def optimize_image(img_bytes):
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)

        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue()
    except Exception:
        return img_bytes

def fit_complete_sentences(text, max_len):
    raw_sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    collected = []
    current_len = 0

    for s in raw_sentences:
        s = s.strip()
        if not s:
            continue
        added_len = len(s) if not collected else len(s) + 1
        if current_len + added_len <= max_len:
            collected.append(s)
            current_len += added_len
        else:
            break

    if collected:
        return " ".join(collected)

    first = raw_sentences[0]
    truncated = first[:max_len - 1]
    last_space = truncated.rfind(' ')
    return (truncated[:last_space] if last_space > 0 else truncated).rstrip() + "..."

def validate_internal_punctuation(text, max_internal=2):
    if not text:
        return True
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    for s in sentences:
        internal_marks = re.findall(r'[,;:\-—–()[\]]', s)
        if len(internal_marks) > max_internal:
            return False
    return True

def is_valid_turkish(text):
    if not text:
        return False
    if any(c in "çğıöşüÇĞİÖŞÜ" for c in text):
        return True
    tr_stopwords = {
        "ve", "bir", "bu", "da", "de", "için", "ile", "gibi", "çok", "ama",
        "ancak", "sonra", "kadar", "olan", "diye", "yok", "var", "oldu",
        "etti", "yaptı", "çıktı", "geldi", "gitti", "başladı", "tarihinde"
    }
    words = set(re.findall(r'\b[a-zA-ZçğıöşüÇĞİÖŞÜ]+\b', text.lower()))
    return len(words.intersection(tr_stopwords)) >= 2

def parse_json_safely(raw_str):
    if not raw_str:
        return None
    clean = re.sub(r'^```(?:json)?\s*', '', raw_str.strip(), flags=re.MULTILINE)
    clean = re.sub(r'\s*
