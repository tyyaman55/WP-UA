<p align="center">
  <img src="wpualogo.jpg" alt="WPUA-SM-Bot açık logo" width="140">
</p>

<h1 align="center">wpua-sm-bot<br><sub><a href="https://en.wikipedia.org/wiki/Wikipedia:Unusual_articles">Wikipedia:Unusual articles</a> Social Media Bot</sub></h1>

<p align="center">
  Vikipedi'nin "sıra dışı maddeler" listelerinden her gün bir madde seçip
  <b>Bluesky</b> (İngilizce, Türkçe) ve <b>X</b> (İngilizce) hesaplarında otomatik paylaşan bot.
</p>

<p align="center">
  <img src="wpuabanner.jpg" alt="Bluesky İngilizce hesap başlık fotoğrafı" width="49%">
  <img src="wpuabannerX.jpg" alt="X  İngilizce hesap başlık fotoğrafı" width="49%">
</p>

---

## Ne yapıyor?

1. **Kaynak tarama** — Vikipedi'nin doğrulanmış "sıra dışı maddeler" sayfalarını tarar: Önce İngilizce (`WP:UA`), tükenirse Almanca/İspanyolca/Fransızca sürümlere geçer.
2. **Tekrar önleme** — Daha önce paylaşılan maddeler, repodaki `posted_articles.txt` dosyasında tutulur, her taramada bu listeye bakılıp aynı madde bir daha seçilmez.
3. **İçerik üretimi** — Seçilen maddenin özeti ve görseli Wikipedia API'lerinden çekilir, **Gemini** (öncelikli) veya **DeepSeek** (yedek) ile İngilizce ve Türkçe olarak 2-3 cümlelik merak uyandıran bir anlatı üretilir.
4. **Çoklu platform paylaşımı:**
   - 🦋 **Bluesky (EN)** — görsel + madde bağlantılı başlık + İngilizce anlatı
   - 🦋 **Bluesky (TR)** — görsel + madde bağlantılı başlık + Türkçe anlatı
   - ✖️ **X (EN)** — görsel + başlık + İngilizce anlatı (maliyet nedeniyle **linksiz**)
5. **Arşivleme** — paylaşım başarılı olduğunda madde başlığı GitHub'daki arşiv dosyasına otomatik işlenir.

## Nasıl çalışıyor?

Bot, `wiki_bot.py` içinde tek bir Python betiği olarak çalışır ve GitHub Actions üzerinde (`.github/workflows/`) tetiklenir. Zamanlama GitHub'ın `cron` tetiklemesinin gecikmeli çalışması nedeniyle **[cron-job.org](https://cron-job.org)** üzerinden GitHub'ın `workflow_dispatch` uç noktasına atılan zamanlanmış bir API isteğiyle yapılıyor.

```
cron-job.org  ──(POST, zamanlanmış)──▶  GitHub Actions (workflow_dispatch)  ──▶  wiki_bot.py çalışır
```

## Gerekli GitHub Secrets

| Secret | Açıklama |
|---|---|
| `BSKY_HANDLE`, `BSKY_APP_PASSWORD` | İngilizce Bluesky hesabı |
| `BSKY_TR_HANDLE`, `BSKY_TR_APP_PASSWORD` | Türkçe Bluesky hesabı |
| `X_API_KEY`, `X_API_SECRET` | X Developer App kimlik bilgileri (bir **proje**ye bağlı olmalı) |
| `X_ACCESS_TOKEN_EN`, `X_ACCESS_SECRET_EN` | X hesabına ait erişim belirteçleri (okuma-yazma izinli) |
| `GEMINI_API_KEY` | Google Gemini API anahtarı (birincil içerik üretici) |
| `DEEPSEEK_API_KEY` | DeepSeek API anahtarı (yedek içerik üretici) |

`GITHUB_TOKEN` ve `GITHUB_REPOSITORY` GitHub Actions tarafından otomatik sağlanır, elle tanımlamaya gerek yoktur.

## Maliyet notu

- X API kullandıkça öde (kredi) modelinde çalışıyor. Dış bağlantı içermeyen bir gönderi **$0.015**, bağlantı içeren bir gönderi **$0.200** olarak fiyatlanıyor. Bu yüzden Bluesky gönderilerinin aksine X gönderilerinde bilinçli olarak Vikipedi maddesine giden bağlantı **eklenmiyor**.
- Gemini/DeepSeek çağrısı günde yalnızca **1 kez** yapılır, üretilen aynı metin hem Bluesky hem X'e dağıtılır.

## Dosyalar

| Dosya | Açıklama |
|---|---|
| `wiki_bot.py` | Botun tüm mantığı (kaynak tarama, içerik üretimi, paylaşım) |
| `.github/workflows/` | GitHub Actions workflow tanımı (`workflow_dispatch` ile tetiklenir) |
| `posted_articles.txt` | Daha önce paylaşılan maddelerin arşivi |
| `wpualogo.jpg`, `wpuabanner.jpg` | Bluesky İngilizce hesap görselleri |
| `wpuatrlogo.jpg`, `wpuatrbanner.jpg` | Bluesky Türkçe hesap görselleri |
| `wpualogoX.jpg`, `wpuabannerX.jpg` | X İngilizce hesap görselleri |

## Elle çalıştırma

Repo → **Actions** sekmesi → ilgili workflow → **Run workflow** ile tetiklenebilir. Zamanlanmış tetikleme cron-job.org üzerinden yapılır.
