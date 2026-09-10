import os
import sys
import time
import random
import re
import json
import urllib.parse
import requests
from io import BytesIO
from PIL import Image
from atproto import Client, client_utils

BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
STATE_FILE = "posted_articles.txt"

HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/3.2 (https://bsky.app/; personal curation bot)"
}

TOTAL_BLUESKY_BUDGET = 300
MAX_BLOB_IMAGE_SIZE = 950_000
FALLBACK_EMOJIS = ["📜", "🧐", "💡", "🔍", "✨", "🛸", "🧩"]

def get_posted_titles():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_posted_title(title):
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{title}\n")

def get_unusual_articles():
    url = "https://en.wikipedia.org/w/api.php"
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
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe_title}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
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

def request_gemini(prompt):
    """Birincil sağlayıcı: Google Gemini 2.5 Flash"""
    if not GEMINI_API_KEY:
        return None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "maxOutputTokens": 200,
            "temperature": 0.8
        }
    }
    
    try:
        print("Gemini API çağrılıyor...")
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            result = resp.json()
            raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(raw_text)
        print(f"Gemini yanıt vermedi (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"Gemini bağlantı/zaman aşımı hatası: {e}")
    return None

def request_groq(prompt):
    """Yedek sağlayıcı: Groq (Llama 3.3 70B Versatile)"""
    if not GROQ_API_KEY:
        print("GROQ_API_KEY tanımlı değil, Groq yedek adımı atlanıyor.")
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "max_tokens": 200
    }

    try:
        print("Groq API devreye giriyor (Llama 3.3 70B)...")
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            result = resp.json()
            raw_text = result["choices"][0]["message"]["content"]
            return json.loads(raw_text)
        print(f"Groq API hatası (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"Groq bağlantı hatası: {e}")
    return None

def generate_ai_curated_post(title, extract, available_budget):
    """Gemini ve Groq fallback zinciriyle metin ve emoji üretir."""
    prompt = (
        "You are the curator of a popular Bluesky account uncovering bizarre, absurd, and fascinating Wikipedia rabbit holes.\n\n"
        f"Article Title: {title}\n"
        f"Article Background Details: {extract}\n\n"
        "Task:\n"
        "1. Select ONE single emoji that captures the essence, absurdity, or subject of this story.\n"
        "2. Do NOT copy the Wikipedia sentences. Instead, write an ORIGINAL, engaging, and witty 1-2 sentence micro-narrative "
        "explaining WHY this topic is so strange, unbelievable, or hilarious.\n\n"
        "Strict Constraints:\n"
        f"- The narrative MUST NOT exceed {available_budget} characters.\n"
        "- Must end with a complete sentence.\n"
        "- Write strictly in English.\n"
        "- Do not start with or repeat the article title.\n"
        "- Do not include hashtags or URLs.\n"
        "- Output strictly valid JSON format with keys: \"emoji\" and \"narrative\"."
    )

    data = None
    # 1. Öncelik: Gemini
    if GEMINI_API_KEY:
        data = request_gemini(prompt)

    # 2. Öncelik (Fallback): Groq
    if not data and GROQ_API_KEY:
        data = request_groq(prompt)

    # Başarılı JSON yanıtı kontrolü
    if data:
        emoji = data.get("emoji", "").strip() or random.choice(FALLBACK_EMOJIS)
        narrative = data.get("narrative", "").strip()
        if narrative:
            if len(narrative) > available_budget:
                narrative = fit_complete_sentences(narrative, available_budget)
            print(f"Yapay zeka metni başarıyla üretildi ({len(narrative)} kr):\n{narrative}")
            return emoji, narrative

    print("Yapay zeka sağlayıcıları yanıt vermedi, acil durum metin formatına geçiliyor.")
    return random.choice(FALLBACK_EMOJIS), fit_complete_sentences(extract, available_budget)

def build_post(title, extract, page_url):
    builder = client_utils.TextBuilder()

    header_text_without_emoji = f" {title.upper()}\n\n"
    header_cost = 2 + len(header_text_without_emoji)
    available_narrative_budget = TOTAL_BLUESKY_BUDGET - header_cost - 3

    emoji, narrative = generate_ai_curated_post(title, extract, available_narrative_budget)

    builder.text(f"{emoji} ")
    builder.link(title.upper(), page_url)
    builder.text("\n\n")
    builder.text(narrative)

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
    if img_url:
        try:
            r = requests.get(img_url, headers=HEADERS, timeout=20)
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
                image_alt=f"{title} konulu Wikipedia arşiv görseli"
            )
        else:
            client.send_post(text=rich_text)

        print(f"Başarıyla paylaşıldı: {title}")
        save_posted_title(title)
    except Exception as e:
        print(f"Bluesky paylaşım hatası: {e}")

if __name__ == "__main__":
    main()
