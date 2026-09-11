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
    "User-Agent": "BlueskyUnusualWikiBot/5.7 (https://bsky.app/; dual-language curated bot)"
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

        # NOT: Bu sayfaların çoğu artık düz <li> madde listesi değil, wikitable
        # (<tr><td>...) satırları olarak biçimlendiriliyor (bölge/ülke | madde | not
        # şeklinde 3 sütun). Sadece <li> aranırsa maddelerin büyük çoğunluğu (yüzlerce)
        # atlanır ve sadece hâlâ düz liste olan birkaç bölümden (~20 madde) sonuç gelir.
        # Bu yüzden hem <li> hem <tr> blokları aynı anda yakalanıyor.
        blocks = re.findall(r'<(?P<tag>li|tr)\b[^>]*>(?P<content>.*?)</(?P=tag)>', html_text, flags=re.DOTALL | re.IGNORECASE)

        skip_prefixes = (
            "wikipedia:", "wikipédia:", "file:", "fichier:", "datei:", "archivo:",
            "help:", "aide:", "hilfe:", "ayuda:", "category:", "catégorie:",
            "kategorie:", "categoría:", "special:", "spezial:", "spécial:", "especial:",
            "talk:", "diskussion:", "discussion:", "discusión:", "template:", "modèle:",
            "vorlage:", "plantilla:", "portal:", "user:", "utilisateur:", "benutzer:", "usuario:",
            "mediawiki:"
        )

        def pick_title(block_html):
            # Tablo satırlarında ilk hücre genelde bölge/ülke adı (ör. "Illinois"),
            # asıl ilginç madde ise Vikipedi kuralı gereği KALIN (<b>) yazılan link
            # olur (ör. "'''[[Bubbly Creek]]'''"). Bu yüzden önce kalın linki dene;
            # bulunamazsa (düz <li> listelerinde olduğu gibi) ilk uygun linke düş.
            bold_match = re.search(
                r'<b>\s*<a\s+[^>]*href=["\']/wiki/([^"#?:]+)["\'][^>]*>(.*?)</a>\s*</b>',
                block_html, flags=re.DOTALL | re.IGNORECASE
            )
            if bold_match:
                decoded = urllib.parse.unquote(bold_match.group(1)).replace('_', ' ').strip()
                if not any(decoded.lower().startswith(p) for p in skip_prefixes):
                    return decoded

            links = re.findall(r'<a\s+[^>]*href=["\']/wiki/([^"#?:]+)["\'][^>]*>(.*?)</a>', block_html, flags=re.DOTALL | re.IGNORECASE)
            for raw_slug, _ in links:
                decoded = urllib.parse.unquote(raw_slug).replace('_', ' ').strip()
                d_lower = decoded.lower()

                if any(d_lower.startswith(p) for p in skip_prefixes):
                    continue
                if d_lower.startswith(("list of", "liste de", "liste von", "lista de", "chronologie", "liste des")):
                    continue

                return decoded
            return None

        candidates = []
        seen_titles = set()
        for _tag, block in blocks:
            target_title = pick_title(block)
            if not target_title:
                continue

            clean_note = re.sub(r'<[^>]+>', ' ', block)
            clean_note = re.sub(r'\s+', ' ', clean_note).strip()
            clean_note = html.unescape(clean_note)

            if len(clean_note) < 25:
                continue

            # <tr> bazen iç içe başka bloklarla (ör. iç tablo/resim) çakışıp aynı
            # maddeyi birden fazla üretebilir; başlığa göre yinelenenleri ele.
            dedup_key = target_title.lower()
            if dedup_key in seen_titles:
                continue
            seen_titles.add(dedup_key)

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
        print("[Gemini] API anahtarı (GEMINI_API_KEY) tanımlı değil! Atlanıyor.")
        return None

    url = clean_url(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "maxOutputTokens": 1200,
            "temperature": 0.85,
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }
    }

    try:
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
            print(f"[Gemini] Yanıt alındı ancak beklenen JSON formatı çözülemedi.")
        else:
            print(f"[Gemini] HTTP {resp.status_code} Hatası: {resp.text[:300]}")
    except Exception as e:
        print(f"[Gemini] Bağlantı/Zaman aşımı hatası: {e}")
    return None

def request_groq(prompt):
    if not GROQ_API_KEY:
        print("[Groq] API anahtarı (GROQ_API_KEY) tanımlı değil! Atlanıyor.")
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
                "content": "You always respond with a single valid JSON object and nothing else — no markdown fences, no commentary."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.8,
        "reasoning_effort": "none",
        "max_tokens": 1200
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = parse_json_safely(content)
            if parsed:
                return parsed
            print(f"[Groq] Yanıt alındı ancak JSON çözülemedi: {content[:200]}")
        else:
            print(f"[Groq] HTTP {resp.status_code} Hatası: {resp.text[:300]}")
    except Exception as e:
        print(f"[Groq] Bağlantı hatası: {e}")
    return None

def validate_candidate_output(data, budget_en, budget_tr, target_min_en, target_min_tr):
    """Çıktıyı uzunluk, noktalama ve dil kurallarına göre değerlendirir."""
    if not data:
        return False, None, None, None, "Yanıt boş veya parse edilemedi"

    emoji = data.get("emoji", "").strip() or random.choice(FALLBACK_EMOJIS)
    n_en = data.get("narrative_en", "").strip()
    n_tr = data.get("narrative_tr", "").strip()

    if not n_en or not n_tr:
        return False, None, None, None, "Metin alanları eksik (narrative_en veya narrative_tr boş)"

    if len(n_en) > budget_en:
        n_en = fit_complete_sentences(n_en, budget_en)
    if len(n_tr) > budget_tr:
        n_tr = fit_complete_sentences(n_tr, budget_tr)

    if not is_valid_turkish(n_tr):
        return False, None, None, None, "Türkçe metin doğrulaması başarısız (İngilizce saptandı)"

    if not validate_internal_punctuation(n_tr, max_internal=2):
        return False, None, None, None, "Türkçe cümlede 2'den fazla iç noktalama işareti var"

    if len(n_en) < target_min_en or len(n_tr) < target_min_tr:
        return False, None, None, None, f"Bütçe yetersiz (EN: {len(n_en)}/{target_min_en}, TR: {len(n_tr)}/{target_min_tr})"

    return True, emoji, n_en, n_tr, "Kusursuz"

def generate_dual_language_posts(cand, extract, budget_en, budget_tr):
    # Eskiden bu eşik "budget - 25" idi (ör. 258/300) ve model o son 25 karakterlik
    # dar aralığı sık sık tutturamadığı için "Bütçe yetersiz" diyerek geçerli, iyi
    # yazılmış metinleri bile reddediyordu. Aralık genişletildi (budget'ın ~%75'i,
    # taban 150) ki gereksiz reddedilme/retry olmasın; buna karşın aşağıdaki prompt
    # modeli yine de üst sınıra olabildiğince yaklaşmaya zorluyor.
    target_min_en = max(150, int(budget_en * 0.75))
    target_min_tr = max(150, int(budget_tr * 0.75))

    base_prompt = (
        "You are the curator of a popular Bluesky feed dedicated to reality's strangest oddities.\n"
        f"This subject is officially listed on Wikipedia's curated unusual articles list ({cand['domain']}).\n\n"
        f"Article Title: {cand['title']}\n"
        f"Curator Note (WHY IT IS UNUSUAL): {cand['curation_note']}\n"
        f"Article Extract ({cand['lang'].upper()} Wikipedia): {extract}\n\n"
        "GOAL:\n"
        "1. Craft a compelling 2 to 3-sentence micro-narrative in ENGLISH ('narrative_en') that hooks the reader with the sheer bizarre irony of this story.\n"
        "2. Craft a TURKISH version ('narrative_tr') of the same story. CRITICAL LANGUAGE RULE: 'narrative_tr' MUST be written 100% in natural, fluent, native TURKISH (TÜRKÇE). Under NO circumstances write English in narrative_tr! Never use aorist tense (-r, -ar, -er, -maz, -mez; 'yapılır', 'bilinir'); use past (-dı/-miş) or present continuous (-ıyor).\n\n"
        "TONE & STYLE (CRITICAL):\n"
        "- Write with an intriguing, curious narrative voice with a subtle touch of dry, intelligent mischief (playful curiosity without being disrespectful or silly).\n"
        "- Do NOT write a dry textbook summary. Avoid formal encyclopedic passive phrasing (e.g. 'It is known as...', 'This article describes...').\n"
        "- Focus on the concrete paradox: the specific odd rule, historical accident, absurd number, or improbable turn of events.\n"
        "- No cheesy clickbait hooks like 'Imagine this', 'Picture this', 'Meet the', 'What if', or 'You won't believe'. Dive straight into the bizarre action or fact.\n"
        "- Avoid excessive punctuation: do NOT use more than 2 mid-sentence punctuation marks (commas/dashes) in a single sentence; split into shorter sentences if needed.\n\n"
        "LENGTH REQUIREMENTS (STRICT - MAXIMIZE LENGTH):\n"
        f"- You have a hard budget of {budget_en} characters for narrative_en and {budget_tr} characters for narrative_tr. Your goal is to get as close as possible to this exact number, ideally within the last 10-15 characters of it. Treat the budget as a target to fill, not a ceiling to avoid.\n"
        f"- Absolute minimum (will be REJECTED if shorter): {target_min_en} characters for narrative_en, {target_min_tr} characters for narrative_tr.\n"
        "- Do NOT stop early at 150-180 characters. If your first draft is short, add another concrete detail, a number, a date, a consequence, or a sensory specific from the extract/curator note to use the remaining space - never pad with filler words or repetition.\n"
        f"- Hard Limit: Under NO condition exceed {budget_en} characters for EN and {budget_tr} characters for TR.\n"
        "- End on a finished, grammatically complete sentence (punctuated with . ! or ?).\n"
        "- Do not repeat or start with the article title.\n"
        "- No hashtags, no markdown formatting.\n"
        "- Select ONE matching emoji.\n"
        "- Return strictly a single JSON: {\"emoji\": \"...\", \"narrative_en\": \"...\", \"narrative_tr\": \"...\"}."
    )

    last_valid_fallback = None

    print(f"\n--- AI Üretim Süreci Başlıyor ---")
    print(f"API Durumu: GEMINI={'Tanımlı' if GEMINI_API_KEY else 'YOK'}, GROQ={'Tanımlı' if GROQ_API_KEY else 'YOK'}")

    for attempt in range(1, 4):
        prompt = base_prompt
        if attempt > 1:
            prompt += (
                "\n\nCRITICAL RETRY NOTICE: Either 'narrative_tr' was NOT written in Turkish, or a sentence had excess punctuation, "
                "or text was too short. You MUST write 'narrative_tr' purely in TURKISH, fill the character budget, and avoid aorist tense."
            )

        # 1. DENEME: ÖNCE GEMINI
        print(f"\n[Deneme {attempt}/3] [1. Öncelik: Gemini 2.5 Flash] çağrılıyor...")
        data_gemini = request_gemini(prompt)
        ok, emoji, n_en, n_tr, reason = validate_candidate_output(
            data_gemini, budget_en, budget_tr, target_min_en, target_min_tr
        )

        if ok:
            print(f"===> Başarılı! Metin GEMINI tarafından üretildi (EN: {len(n_en)} kr, TR: {len(n_tr)} kr).")
            return emoji, n_en, n_tr
        else:
            print(f"[Gemini] Çıktı uygun bulunmadı ({reason}).")
            if data_gemini and is_valid_turkish(data_gemini.get("narrative_tr", "")):
                last_valid_fallback = (
                    data_gemini.get("emoji") or random.choice(FALLBACK_EMOJIS),
                    data_gemini.get("narrative_en", ""),
                    data_gemini.get("narrative_tr", ""),
                    "Gemini (Kısmi Bütçe)"
                )

        # 2. DENEME: GEMINI BAŞARISIZ OLURSA DOĞRUDAN GROQ'A GEÇ
        print(f"[Deneme {attempt}/3] [2. Öncelik: Groq Qwen3.6] devreye giriyor...")
        data_groq = request_groq(prompt)
        ok, emoji, n_en, n_tr, reason = validate_candidate_output(
            data_groq, budget_en, budget_tr, target_min_en, target_min_tr
        )

        if ok:
            print(f"===> Başarılı! Metin GROQ tarafından üretildi (EN: {len(n_en)} kr, TR: {len(n_tr)} kr).")
            return emoji, n_en, n_tr
        else:
            print(f"[Groq] Çıktı uygun bulunmadı ({reason}).")
            if data_groq and is_valid_turkish(data_groq.get("narrative_tr", "")):
                last_valid_fallback = (
                    data_groq.get("emoji") or random.choice(FALLBACK_EMOJIS),
                    data_groq.get("narrative_en", ""),
                    data_groq.get("narrative_tr", ""),
                    "Groq (Kısmi Bütçe)"
                )

    # 3 Deneme sonunda tam bütçe tutturulamadıysa fakat geçerli Türkçe üretildiyse onu kurtar
    if last_valid_fallback:
        em, en_cand, tr_cand, provider = last_valid_fallback
        print(f"\n===> Tam bütçeye ulaşılamadı fakat geçerli Türkçe metin kurtarıldı [{provider}] (EN: {len(en_cand)} kr, TR: {len(tr_cand)} kr).")
        return em, fit_complete_sentences(en_cand, budget_en), fit_complete_sentences(tr_cand, budget_tr)

    print("\n[UYARI] Hem Gemini hem Groq başarısız oldu. Çift dilli AI metni üretilemedi!")
    return random.choice(FALLBACK_EMOJIS), fit_complete_sentences(extract, budget_en), None

def build_post(display_title, narrative, emoji, page_url):
    builder = client_utils.TextBuilder()
    builder.text(f"{emoji} ")
    builder.link(display_title.upper(), page_url)
    builder.text("\n\n")
    builder.text(narrative)
    return builder

def main():
    if not BSKY_HANDLE_EN or not BSKY_APP_PASSWORD_EN:
        print("İngilizce Bluesky hesap bilgileri eksik.")
        sys.exit(1)

    posted = get_posted_titles()
    posted_lower = {line.lower() for line in posted}

    en_source = next(s for s in UNUSUAL_SOURCES if s["lang"] == "en")
    other_sources = [s for s in UNUSUAL_SOURCES if s["lang"] != "en"]

    chosen_candidate = None
    target_data = None

    # 1. ÖNCELİK: ENWIKI
    print("Öncelik kontrolü: ENWIKI taranıyor...")
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
                break

    # 2. ÖNCELİK: ENWIKI BİTERSE DİĞERLERİ
    if not chosen_candidate:
        print("ENWIKI tükendi veya erişilemedi, diğer diller rastgele taranıyor...")
        random.shuffle(other_sources)
        for src in other_sources:
            candidates = extract_candidates_from_source(src)
            unposted = [c for c in candidates if not is_already_posted(c, posted_lower)]
            random.shuffle(unposted)
            for cand in unposted[:40]:
                data = fetch_summary(cand["domain"], cand["title"])
                if data and data.get("type") == "standard" and data.get("extract"):
                    chosen_candidate = cand
                    target_data = data
                    print(f"Yedek Dilden Seçilen Madde: {cand['title']} ({cand['domain']})")
                    break
            if chosen_candidate:
                break

    if not chosen_candidate or not target_data:
        print("Uygun sıra dışı madde bulunamadı.")
        return

    title_en = chosen_candidate["title"]
    domain = chosen_candidate["domain"]
    extract = target_data.get("extract", "").strip()

    page_url_en = target_data.get("content_urls", {}).get("desktop", {}).get("page", "")

    tr_url, tr_title = get_turkish_wiki_page(domain, title_en)
    if tr_url and tr_title:
        page_url_tr = tr_url
        title_tr = tr_title
    else:
        page_url_tr = page_url_en
        title_tr = title_en

    img_url = (
        target_data.get("originalimage", {}).get("source") or 
        target_data.get("thumbnail", {}).get("source")
    )
    image_bytes = None
    alt_text_en = f"{title_en} Wikipedia image"
    alt_text_tr = f"{title_tr} Vikipedi görseli"

    if img_url:
        try:
            caption = fetch_image_caption(domain, title_en, img_url)
            if caption:
                alt_text_en = f"{title_en}: {caption}"[:495]
                alt_text_tr = f"{title_tr}: {caption}"[:495]

            r = requests.get(clean_url(img_url), headers=HEADERS, timeout=20)
            if r.status_code == 200:
                image_bytes = optimize_image(r.content)
        except Exception as e:
            print(f"Görsel indirilemedi: {e}")

    header_len_en = len(title_en) + 5
    budget_en = TOTAL_BLUESKY_BUDGET - header_len_en - 2

    header_len_tr = len(title_tr) + 5
    budget_tr = TOTAL_BLUESKY_BUDGET - header_len_tr - 2

    emoji, narrative_en, narrative_tr = generate_dual_language_posts(
        chosen_candidate, extract, budget_en, budget_tr
    )

    post_en = build_post(title_en, narrative_en, emoji, page_url_en)

    # 1. HESAP: İNGİLİZCE PAYLAŞIM
    try:
        client_en = Client()
        client_en.login(BSKY_HANDLE_EN, BSKY_APP_PASSWORD_EN)
        if image_bytes:
            client_en.send_image(text=post_en, image=image_bytes, image_alt=alt_text_en)
        else:
            client_en.send_post(text=post_en)
        print(f"[EN Hesap] Başarıyla paylaşıldı: {title_en}")
    except Exception as e:
        print(f"[EN Hesap] Paylaşım hatası: {e}")

    # 2. HESAP: TÜRKÇE PAYLAŞIM
    if BSKY_HANDLE_TR and BSKY_APP_PASSWORD_TR:
        if not narrative_tr or not is_valid_turkish(narrative_tr):
            print("[TR Hesap] GÜVENLİK ENGELİ: Geçerli Türkçe metin üretilemediği için İngilizce paylaşım engellendi!")
        else:
            try:
                post_tr = build_post(title_tr, narrative_tr, emoji, page_url_tr)
                client_tr = Client()
                client_tr.login(BSKY_HANDLE_TR, BSKY_APP_PASSWORD_TR)
                if image_bytes:
                    client_tr.send_image(text=post_tr, image=image_bytes, image_alt=alt_text_tr)
                else:
                    client_tr.send_post(text=post_tr)
                print(f"[TR Hesap] Başarıyla paylaşıldı: {title_tr}")
            except Exception as e:
                print(f"[TR Hesap] Paylaşım hatası: {e}")
    else:
        print("Türkçe hesap kimlik bilgileri tanımlı değil, sadece İngilizce paylaşıldı.")

    save_posted_title(f"{chosen_candidate['lang']}:{title_en}")

if __name__ == "__main__":
    main()
