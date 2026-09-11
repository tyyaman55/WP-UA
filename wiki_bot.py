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

BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
STATE_FILE = "posted_articles.txt"

HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/4.1 (https://bsky.app/; curated unusual articles bot)"
}

GITHUB_API_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

TOTAL_BLUESKY_BUDGET = 300
FALLBACK_EMOJIS = ["📜", "🧐", "💡", "🔍", "✨", "🛸", "🧩"]

# YALNIZCA BU 4 RESMİ LİSTE KULLANILIR
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
    """Kayıt dosyasını doğrudan GitHub API üzerinden okur."""
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
    """Maddenin daha önce paylaşılıp paylaşılmadığını teyit eder."""
    raw_title_lower = cand["title"].lower()
    prefixed_key_lower = f"{cand['lang']}:{cand['title']}".lower()
    return (raw_title_lower in posted_lower_set or prefixed_key_lower in posted_lower_set)

def save_posted_title(record_key, max_retries=5):
    """Kayıt dosyasına yeni maddeyi GitHub Contents API ile ekler."""
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
            print(f"'{record_key}' başarıyla kaydedildi.")
            return
        elif put_resp.status_code in (409, 422):
            time.sleep(1.5)
            continue
        else:
            return

def extract_candidates_from_source(source):
    """Listeden hem madde başlığını hem de küratörün 'unusual' açıklama notunu çeker."""
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
            candidates = resp.json().get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part and part["text"].strip():
                        parsed = parse_json_safely(part["text"])
                        if parsed:
                            return parsed
    except Exception as e:
        print(f"Gemini bağlantı hatası: {e}")
    return None

def request_groq(prompt):
    if not GROQ_API_KEY:
        return None

    url = clean_url("https://api.groq.com/openai/v1/chat/completions")
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "qwen/qwen3.6-27b",
        "messages": [
            {
                "role": "system",
                "content": "You always respond with a single valid JSON object and nothing else — no markdown fences, no commentary before or after it."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.75,
        "reasoning_effort": "none",
        "max_tokens": 900
    }

    try:
        print("Groq API devreye giriyor (Qwen3.6 27B)...")
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = parse_json_safely(content)
            if parsed:
                return parsed
    except Exception as e:
        print(f"Groq bağlantı hatası: {e}")
    return None

def generate_ai_curated_post(cand, extract, available_budget):
    target_min = max(available_budget - 18, int(available_budget * 0.92))

    prompt = (
        "You are the curator of a popular Bluesky account that uncovers extraordinary, bizarre, and fascinating Wikipedia rabbit holes.\n"
        f"This subject is officially listed on Wikipedia's curated unusual articles list ({cand['domain']}).\n\n"
        f"Article Title: {cand['title']}\n"
        f"Curator's List Annotation (EXPLAINS WHY IT IS UNUSUAL): {cand['curation_note']}\n"
        f"Article Lead Extract ({cand['lang'].upper()} Wikipedia): {extract}\n\n"
        "TASK:\n"
        "1. Identify specifically WHAT makes this subject bizarre, unusual, or remarkable using both the Curator's Annotation and the Article Extract.\n"
        "2. Write an intriguing, curiosity-provoking micro-narrative in ENGLISH built strictly around that unusual aspect.\n\n"
        "TONE & STYLE GUIDELINES:\n"
        "- The output narrative MUST BE 100% IN ENGLISH, regardless of the source language (German, French, Spanish, or English).\n"
        "- Intriguing and captivating, never dry or boring. Spark deep curiosity in the reader.\n"
        "- Respectful and grounded storytelling: NOT flippant, NOT disrespectful, and NOT slangy ('laubali olmadan').\n"
        "- Avoid cheap hype or formulaic hooks like 'Imagine', 'Picture this', 'Meet', 'You won't believe', or 'Insane'. Present the strange reality directly with authentic punch.\n"
        "- Weave in concrete details (names, dates, numbers, odd legal rules, or peculiar events).\n\n"
        "STRICT LENGTH CONSTRAINTS:\n"
        f"- Target Length: MUST be between {target_min} and {available_budget} characters. Maximize the character budget!\n"
        f"- Absolute Maximum: Under NO circumstances exceed {available_budget} characters.\n"
        "- MUST end with a complete, fully punctuated sentence (. ! or ?). Never cut off mid-thought.\n"
        "- Do not repeat or begin with the article title.\n"
        "- No hashtags, no markdown, no links.\n"
        "- Select ONE fitting emoji for the story.\n"
        "- Output strictly a single JSON object: {\"emoji\": \"...\", \"narrative\": \"...\"}."
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

    print("Yapay zeka yanıt vermedi, acil durum metin kesimine geçiliyor.")
    return random.choice(FALLBACK_EMOJIS), fit_complete_sentences(extract, available_budget)

def build_post(cand, extract, page_url):
    builder = client_utils.TextBuilder()

    header_text_without_emoji = f" {cand['title'].upper()}\n\n"
    header_cost = 2 + len(header_text_without_emoji)
    available_narrative_budget = TOTAL_BLUESKY_BUDGET - header_cost - 2

    emoji, narrative = generate_ai_curated_post(cand, extract, available_narrative_budget)

    # 1. Emoji ve Tıklanabilir Başlık
    builder.text(f"{emoji} ")
    builder.link(cand['title'].upper(), page_url)
    builder.text("\n\n")

    # 2. Üretilen Yoğun İngilizce Metin
    builder.text(narrative)

    total_post_len = len(f"{emoji} {cand['title'].upper()}\n\n{narrative}")
    print(f"Toplam Gönderi Hacmi: {total_post_len} / 300 grafem (Özet: {len(narrative)} kr)")

    return builder

def main():
    if not BSKY_HANDLE or not BSKY_APP_PASSWORD:
        print("Bluesky kimlik değişkenleri eksik.")
        sys.exit(1)

    posted = get_posted_titles()
    posted_lower = {line.lower() for line in posted}

    en_source = next(s for s in UNUSUAL_SOURCES if s["lang"] == "en")
    other_sources = [s for s in UNUSUAL_SOURCES if s["lang"] != "en"]

    chosen_candidate = None
    target_data = None

    # 1. ÖNCELİK: ENWIKI KONTROLÜ
    print("Öncelik kontrolü: ENWIKI maddeleri taranıyor...")
    en_candidates = extract_candidates_from_source(en_source)
    en_unposted = [c for c in en_candidates if not is_already_posted(c, posted_lower)]
    print(f"[en.wikipedia.org] Henüz paylaşılmamış ENWIKI aday sayısı: {len(en_unposted)}")

    if en_unposted:
        random.shuffle(en_unposted)
        for cand in en_unposted[:50]:
            data = fetch_summary(cand["domain"], cand["title"])
            if data and data.get("type") == "standard" and data.get("extract"):
                chosen_candidate = cand
                target_data = data
                print(f"ENWIKI'den Seçilen Madde: {cand['title']}")
                print(f"Küratör Notu: {cand['curation_note'][:120]}...")
                break

    # 2. ÖNCELİK: ENWIKI BİTMİŞSE DİĞER DİLLER RASTGELE DEVREYE GİRER
    if not chosen_candidate:
        if not en_unposted:
            print("ENWIKI sıra dışı maddeleri tükendi! Diğer diller (DE, ES, FR) rastgele taranıyor...")
        else:
            print("ENWIKI adaylarından veri alınamadı, yedek dillere geçiliyor...")

        random.shuffle(other_sources)
        for src in other_sources:
            candidates = extract_candidates_from_source(src)
            unposted = [c for c in candidates if not is_already_posted(c, posted_lower)]
            print(f"[{src['domain']}] Henüz paylaşılmamış aday sayısı: {len(unposted)}")

            random.shuffle(unposted)
            for cand in unposted[:40]:
                data = fetch_summary(cand["domain"], cand["title"])
                if data and data.get("type") == "standard" and data.get("extract"):
                    chosen_candidate = cand
                    target_data = data
                    print(f"Yedek Dilden Seçilen Madde: {cand['title']} ({cand['domain']})")
                    print(f"Küratör Notu: {cand['curation_note'][:120]}...")
                    break

            if chosen_candidate:
                break

    if not chosen_candidate or not target_data:
        print("Uygun içerikli sıra dışı madde bulunamadı.")
        return

    title = chosen_candidate["title"]
    domain = chosen_candidate["domain"]
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
            caption = fetch_image_caption(domain, title, img_url)
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

    rich_text = build_post(chosen_candidate, extract, page_url)

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
        save_posted_title(f"{chosen_candidate['lang']}:{title}")
    except Exception as e:
        print(f"Bluesky paylaşım hatası: {e}")

if __name__ == "__main__":
    main()
