import os
import sys
import random
import re
import urllib.parse
import requests
from io import BytesIO
from PIL import Image
from atproto import Client, client_utils

BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
STATE_FILE = "posted_articles.txt"

HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/2.4 (https://bsky.app/; personal automation bot)"
}

FALLBACK_EMOJIS = ["📜", "🧐", "💡", "🔍", "✨"]

def get_ai_context_emoji(title, extract):
    """Gemini API kullanarak içerikle en uyumlu tek bir emojiyi belirler."""
    if not GEMINI_API_KEY:
        return random.choice(FALLBACK_EMOJIS)

    prompt = (
        "Given the Wikipedia title and short summary below, return ONLY ONE single emoji "
        "that best captures the essence, humor, absurdity, or subject of the story. "
        "Do NOT write any words, explanations, or quotes. Output ONLY the emoji character itself.\n\n"
        f"Title: {title}\n"
        f"Summary: {extract[:300]}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            emoji_text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            # Olası boşlukları temizle ve sadece ilk emojiyi al
            if emoji_text:
                selected_emoji = emoji_text.split()[0]
                print(f"Yapay zeka tarafından seçilen emoji: {selected_emoji}")
                return selected_emoji
        else:
            print(f"Gemini API yanıt kodu: {resp.status_code}")
    except Exception as e:
        print(f"Yapay zeka emoji seçim hatası: {e}")

    return random.choice(FALLBACK_EMOJIS)

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
        print(f"Madde listesi alınırken hata oluştu: {e}")
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

def build_post(title, extract, page_url):
    emoji = get_ai_context_emoji(title, extract)
    builder = client_utils.TextBuilder()

    builder.text(f"{emoji} ")
    builder.link(title.upper(), page_url)
    builder.text("\n\n")

    header_len = 2 + len(title) + 2
    available_budget = 295 - header_len
    
    body = fit_complete_sentences(extract, available_budget)
    builder.text(body)

    return builder

def main():
    if not BSKY_HANDLE or not BSKY_APP_PASSWORD:
        print("Kimlik bilgileri eksik.")
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
        print("Uygun içerik bulunamadı.")
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
                image_alt=f"{title} Wikipedia görseli"
            )
        else:
            client.send_post(text=rich_text)

        print(f"Başarıyla paylaşıldı: {title}")
        save_posted_title(title)
    except Exception as e:
        print(f"Paylaşım başarısız: {e}")

if __name__ == "__main__":
    main()
