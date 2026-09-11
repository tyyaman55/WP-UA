import os
import sys
import time
import random
import re
import json
import base64
import urllib.parse
import requests
from io import BytesIO
from PIL import Image
from atproto import Client, client_utils

BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY")  # "owner/repo"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
STATE_FILE = "posted_articles.txt"

HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/3.8 (https://bsky.app/; personal curation bot)"
}

GITHUB_API_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

TOTAL_BLUESKY_BUDGET = 300
MAX_BLOB_IMAGE_SIZE = 950_000
FALLBACK_EMOJIS = ["📜", "🧐", "💡", "🔍", "✨", "🛸", "🧩"]

def clean_url(raw_url):
    if not raw_url:
        return raw_url
    match = re.search(r'https?://[^\s)\]"\']+', str(raw_url))
    return match.group(0) if match else raw_url

def get_posted_titles():
    """Kayıt dosyasını GitHub API üzerinden repodan okur (yarış durumlarından etkilenmez)."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GITHUB_TOKEN veya GITHUB_REPOSITORY tanımlı değil, boş liste ile devam ediliyor.")
        return set()

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}"
    try:
        resp = requests.get(url, headers=GITHUB_API_HEADERS, params={"ref": GITHUB_BRANCH}, timeout=15)
        if resp.status_code == 200:
            content_b64 = resp.json().get("content", "")
            text = base64.b64decode(content_b64).decode("utf-8")
            return set(line.strip() for line in text.splitlines() if line.strip())
        elif resp.status_code == 404:
            return set()
        else:
            print(f"Kayıt dosyası okunamadı (HTTP {resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"Kayıt dosyası okunurken hata: {e}")
    return set()

def save_posted_title(title, max_retries=5):
    """Kayıt dosyasını GitHub Contents API ile günceller.

    Her denemede güncel içerik + sha çekilir, satır eklenir ve geri yazılır.
    SHA çakışması (başka bir çalıştırma araya girdiyse) olursa yeniden dener.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("GITHUB_TOKEN veya GITHUB_REPOSITORY tanımlı değil, kayıt atlanıyor.")
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
        elif resp.status_code != 404:
            print(f"Kayıt dosyası okunamadı (HTTP {resp.status_code}): {resp.text[:200]}")

        if title in set(line.strip() for line in current_text.splitlines() if line.strip()):
            print(f"'{title}' zaten kayıtlı, tekrar yazılmıyor.")
            return

        new_text = current_text
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += f"{title}\n"

        payload = {
            "message": "chore: update posted wiki archive [skip ci]",
            "content": base64.b64encode(new_text.encode("utf-8")).decode("utf-8"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(url, headers=GITHUB_API_HEADERS, json=payload, timeout=15)
        if put_resp.status_code in (200, 201):
            print(f"'{title}' başarıyla kaydedildi.")
            return
        elif put_resp.status_code in (409, 422):
            print(f"SHA çakışması, tekrar deneniyor ({attempt}/{max_retries})...")
            time.sleep(1.5)
            continue
        else:
            print(f"Kayıt hatası (HTTP {put_resp.status_code}): {put_resp.text[:200]}")
            return

    print("Maksimum deneme sayısına ulaşıldı, kayıt başarısız oldu.")

def get_unusual_articles():
    url = clean_url("https://en.wikipedia.org/w/api.php")
    params = {
        "action": "parse",
        "page": "Wikipedia:Unusual_articles",
        "prop": "links",
        "format": "json"
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        data = resp.json()
        links = data.get("parse", {}).get("links", [])
        articles = [
            l["*"] for l in links 
            if l.get("ns") == 0 and "exists" in l and not l["*"].startswith("List of")
        ]
        print(f"Toplam sıra dışı madde sayısı: {len(articles)}")
        return articles
    except Exception as e:
        print(f"Madde listesi çekilirken hata: {e}")
        return []

def fetch_summary(title):
    safe_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = clean_url(f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe_title}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

def fetch_image_caption(title, img_url):
    """Maddedeki görsele ait orijinal altyazıyı (caption) çeker."""
    if not img_url:
        return None
        
    try:
        # URL'den dosya adını ayıkla
        raw_filename = img_url.split('/')[-1]
        if re.match(r'^\d+px-', raw_filename):
            raw_filename = re.sub(r'^\d+px-', '', raw_filename)
        target_filename = urllib.parse.unquote(raw_filename).replace(' ', '_').lower()

        safe_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
        media_url = clean_url(f"https://en.wikipedia.org/api/rest_v1/page/media-list/{safe_title}")
        
        resp = requests.get(media_url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            for item in items:
                item_file = item.get("title", "").replace("File:", "").replace(" ", "_").lower()
                # Dosya ismi eşleşirse veya lead görsel ise altyazıyı al
                if item_file and (item_file == target_filename or target_filename in item_file):
                    caption = item.get("caption", {}).get("text", "").strip()
                    if caption:
                        clean_caption = re.sub(r'<[^>]+>', '', caption)
                        return re.sub(r'\s+', ' ', clean_caption).strip()

            # Birebir eşleşmezse ilk maddenin altyazısını dene
            if items and items[0].get("caption", {}).get("text"):
                first_caption = items[0]["caption"]["text"].strip()
                clean_first = re.sub(r'<[^>]+>', '', first_caption)
                clean_first = re.sub(r'\s+', ' ', clean_first).strip()
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

def parse_json_safely(raw_str):
    if not raw_str:
        return None
    clean = re.sub(r'^```(?:json)?\s*', '', raw_str.strip(), flags=re.MULTILINE)
    clean = re.sub(r'\s*```$', '', clean, flags=re.MULTILINE).strip()
    try:
        return json.loads(clean)
    except Exception:
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return None

def request_gemini(prompt):
    if not GEMINI_API_KEY:
        return None

    url = clean_url(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "maxOutputTokens": 800,
            "temperature": 0.75,
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }
    }

    try:
        print("Gemini 2.5 Flash çağrılıyor...")
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            result = resp.json()
            candidates = result.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part and part["text"].strip():
                        parsed = parse_json_safely(part["text"])
                        if parsed:
                            return parsed
            print(f"Gemini geçerli bir JSON döndürmedi: {resp.text[:200]}")
        else:
            print(f"Gemini API Hatası (HTTP {resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"Gemini bağlantı hatası: {e}")

    return None

def request_groq(prompt):
    if not GROQ_API_KEY:
        print("GROQ_API_KEY tanımlı değil, Groq yedek adımı atlanıyor.")
        return None

    url = clean_url("https://api.groq.com/openai/v1/chat/completions")
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.75,
        "max_tokens": 400
    }

    try:
        print("Groq API devreye giriyor (OpenAI GPT-OSS-120b)...")
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            result = resp.json()
            content = result["choices"][0]["message"]["content"]
            parsed = parse_json_safely(content)
            if parsed:
                return parsed
            print(f"Groq JSON parse edilemedi: {content[:200]}")
        else:
            print(f"Groq API Hatası (HTTP {resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"Groq bağlantı hatası: {e}")

    return None

def generate_ai_curated_post(title, extract, available_budget):
    target_min = max(200, available_budget - 20)

    prompt = (
        "You are the curator of a top Bluesky account uncovering bizarre, absurd, and fascinating Wikipedia rabbit holes.\n\n"
        f"Article Title: {title}\n"
        f"Article Background Details: {extract}\n\n"
        "Task:\n"
        "1. Select ONE single emoji capturing the core absurdity of this subject.\n"
        "2. Write a highly engaging, richly detailed, and witty narrative explaining WHY this topic is so strange or incredible.\n\n"
        "CRITICAL CHARACTER BUDGET REQUIREMENTS (DO NOT IGNORE):\n"
        f"- Target Length: You MUST write between {target_min} and {available_budget} characters. Do NOT write brief summaries.\n"
        f"- Absolute Maximum: Under NO circumstances exceed {available_budget} characters (hard platform cutoff).\n"
        "- Sentence Structure: You must end with a full, grammatically complete sentence (ending with . ! or ?).\n"
        "- Language: English.\n"
        "- Do not start with or repeat the article title.\n"
        "- Do not include hashtags, markdown bolding, or links.\n"
        "- Output strictly valid JSON format with keys: \"emoji\" and \"narrative\"."
    )

    data = None
    if GEMINI_API_KEY:
        data = request_gemini(prompt)

    if not data and GROQ_API_KEY:
        data = request_groq(prompt)

    if data:
        emoji = data.get("emoji", "").strip() or random.choice(FALLBACK_EMOJIS)
        narrative = data.get("narrative", "").strip()
        if narrative:
            if len(narrative) > available_budget:
                narrative = fit_complete_sentences(narrative, available_budget)
            return emoji, narrative

    print("Yapay zeka yanıt vermedi, acil durum ham metin formatına geçiliyor.")
    return random.choice(FALLBACK_EMOJIS), fit_complete_sentences(extract, available_budget)

def build_post(title, extract, page_url):
    builder = client_utils.TextBuilder()

    header_len_approx = len(title) + 5
    available_narrative_budget = TOTAL_BLUESKY_BUDGET - header_len_approx - 2

    emoji, narrative = generate_ai_curated_post(title, extract, available_narrative_budget)

    # 1. Başlık ve Tıklanabilir Link
    builder.text(f"{emoji} ")
    builder.link(title.upper(), page_url)
    builder.text("\n\n")

    # 2. Gövde Metni
    builder.text(narrative)

    total_post_len = len(f"{emoji} {title.upper()}\n\n{narrative}")
    print(f"Toplam Gönderi Hacmi: {total_post_len} / 300 grafem (Özet: {len(narrative)} kr)")

    return builder

def main():
    if not BSKY_HANDLE or not BSKY_APP_PASSWORD:
        print("Bluesky kimlik değişkenleri eksik.")
        sys.exit(1)

    posted = get_posted_titles()
    candidates = get_unusual_articles()
    
    if not candidates:
        print("Aday listesi boş.")
        return

    unposted = [c for c in candidates if c not in posted]
    random.shuffle(unposted)

    target_data = None
    for cand in unposted[:50]:
        data = fetch_summary(cand)
        if data and data.get("type") == "standard" and data.get("extract"):
            target_data = data
            print(f"Seçilen madde: {cand}")
            break

    if not target_data:
        print("50 aday tarandı ancak uygun içerik bulunamadı.")
        return

    title = target_data.get("title")
    extract = target_data.get("extract", "").strip()
    page_url = target_data.get("content_urls", {}).get("desktop", {}).get("page", "")
    
    img_url = (
        target_data.get("originalimage", {}).get("source") or 
        target_data.get("thumbnail", {}).get("source")
    )

    image_bytes = None
    alt_text = f"{title} Wikipedia image"

    if img_url:
        try:
            # Varsa maddedeki orijinal görsel açıklamasını çek
            caption = fetch_image_caption(title, img_url)
            if caption:
                alt_text = f"{title}: {caption}"
                if len(alt_text) > 500:
                    alt_text = alt_text[:497] + "..."
            print(f"Görsel Alt Metni: {alt_text}")

            r = requests.get(clean_url(img_url), headers=HEADERS, timeout=20)
            if r.status_code == 200:
                image_bytes = optimize_image(r.content)
        except Exception as e:
            print(f"Görsel indirilemedi: {e}")

    client = Client()
    client.login(BSKY_HANDLE, BSKY_APP_PASSWORD)

    rich_text = build_post(title, extract, page_url)

    try:
        if image_bytes:
            client.send_image(
                text=rich_text,
                image=image_bytes,
                image_alt=alt_text
            )
        else:
            client.send_post(text=rich_text)

        print(f"Başarıyla paylaşıldı: {title}")
        save_posted_title(title)
    except Exception as e:
        print(f"Bluesky paylaşım hatası: {e}")

if __name__ == "__main__":
    main()
