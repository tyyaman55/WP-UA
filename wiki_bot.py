import os
import sys
import random
import re
import requests
from io import BytesIO
from PIL import Image
from atproto import Client, client_utils

BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")
STATE_FILE = "posted_articles.txt"

HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/2.1 (contact@example.com)"
}

# Konu ve bağlama göre emoji eşleştirme havuzu
CONTEXT_EMOJIS = [
    ("⚔️", ["war", "battle", "military", "army", "soldier", "weapon", "conflict", "siege", "savaş", "asker", "ordu", "silah"]),
    ("🐾", ["animal", "dog", "cat", "bird", "emu", "mammal", "fish", "insect", "species", "creature", "hayvan", "kuş", "tür"]),
    ("🚀", ["space", "astronomy", "planet", "orbit", "moon", "star", "satellite", "nasa", "uzay", "gezegen", "yıldız"]),
    ("🎨", ["art", "painting", "sculpture", "museum", "artist", "exhibition", "sanat", "tablo", "ressam", "heykel"]),
    ("🎵", ["music", "song", "album", "band", "singer", "opera", "orchestra", "müzik", "şarkı", "albüm"]),
    ("🕵️", ["crime", "murder", "theft", "hoax", "conspiracy", "mystery", "investigation", "cinayet", "suç", "gizem", "hırsızlık"]),
    ("🍲", ["food", "dish", "cuisine", "recipe", "beer", "wine", "bread", "cheese", "fruit", "yemek", "mutfak", "içecek"]),
    ("⚽", ["sport", "football", "match", "player", "olympic", "race", "tournament", "spor", "futbol", "maç", "yarış"]),
    ("👑", ["king", "queen", "emperor", "monarch", "royal", "empire", "kral", "kraliçe", "imparator", "hanedan"]),
    ("🧪", ["science", "chemical", "physics", "experiment", "laboratory", "element", "kimya", "fizik", "deney", "laboratuvar"]),
    ("💀", ["death", "corpse", "cemetery", "grave", "funeral", "skeleton", "ölüm", "mezar", "iskelet"]),
    ("✈️", ["aircraft", "airplane", "flight", "pilot", "aviation", "crash", "uçak", "havacılık", "uçuş"]),
    ("🚢", ["ship", "boat", "submarine", "naval", "ocean", "sea", "sailor", "gemi", "denizaltı", "deniz"]),
    ("🏛️", ["politics", "government", "parliament", "president", "law", "court", "hükümet", "yasa", "mahkeme"]),
    ("💰", ["money", "currency", "bank", "gold", "economy", "millionaire", "para", "ekonomi", "banka", "altın"]),
    ("🌍", ["island", "country", "mountain", "river", "volcano", "city", "geography", "ada", "ülke", "dağ", "nehir", "şehir"]),
    ("👻", ["ghost", "curse", "myth", "monster", "legend", "folklore", "canavar", "efsane", "hayalet", "lanet"]),
]

FALLBACK_EMOJIS = ["📜", "🧐", "💡", "🔍", "✨"]

def detect_context_emoji(text):
    """Metindeki anahtar kelimeleri analiz ederek en uygun emojiyi seçer."""
    clean_text = text.lower()
    for emoji, keywords in CONTEXT_EMOJIS:
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", clean_text):
                return emoji
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
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        links = resp.json().get("parse", {}).get("links", [])
        return [l["*"] for l in links if l.get("ns") == 0 and "exists" in l]
    except Exception:
        return []

def fetch_summary(title):
    safe_title = title.replace(" ", "_")
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

def smart_truncate(text, max_len):
    """Metni cümle bütünlüğünü gözeterek 300 grafeme yaklaştırır."""
    if len(text) <= max_len:
        return text

    truncated = text[:max_len - 1]
    last_punct = max(truncated.rfind('. '), truncated.rfind('! '), truncated.rfind('? '))
    if last_punct > max_len * 0.70:
        return truncated[:last_punct + 1]

    last_space = truncated.rfind(' ')
    if last_space > 0:
        truncated = truncated[:last_space]
        
    return truncated.rstrip() + "…"

def build_post(title, extract, page_url):
    """Başlığı tıklanabilir link yapar, kalan ~260-280 karakteri özetle doldurur."""
    emoji = detect_context_emoji(f"{title} {extract}")
    builder = client_utils.TextBuilder()

    # 1. Emoji
    builder.text(f"{emoji} ")
    
    # 2. Tıklanabilir Başlık (Ekstra satır/footer harcamadan doğrudan linklenir)
    builder.link(title.upper(), page_url)
    builder.text("\n\n")

    # 3. Kalan karakter bütçesinin tamamını metne ver (300 sınırına göre)
    header_len = 2 + len(title) + 2  # emoji + boşluk + başlık + 2 newline
    available_budget = 295 - header_len
    
    body = smart_truncate(extract, available_budget)
    builder.text(body)

    return builder

def main():
    if not BSKY_HANDLE or not BSKY_APP_PASSWORD:
        sys.exit(1)

    posted = get_posted_titles()
    candidates = get_unusual_articles()
    unposted = [c for c in candidates if c not in posted]
    random.shuffle(unposted)

    target_data = None
    for cand in unposted[:15]:
        data = fetch_summary(cand)
        if data and data.get("type") == "standard" and data.get("extract"):
            target_data = data
            break

    if not target_data:
        print("Uygun içerikli madde bulunamadı.")
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
            print(f"Görsel alınamadı: {e}")

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

        print(f"Paylaşıldı: {title}")
        save_posted_title(title)
    except Exception as e:
        print(f"Paylaşım başarısız: {e}")

if __name__ == "__main__":
    main()
