# ---- Stage 1: Builder ----
# python slim modelini indiriyoruz 'builder' diyerek isim veriyoruz.
FROM python:3.11-slim AS builder

# İki Sistem Paketi -> gcc ve libpq-dev ile sistem paketi kuruluyor.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

#/build dizisi oluşur
WORKDIR /build
#requiremtns.txt dosyasını kopyalar.
COPY requirements.txt .
#--user → paketleri /root/.local'e kurar (sistem Python'ına değil), runtime'da appuser'a taşınacak
#--no-cache-dir → pip cache'i kaydetme, image boyutunu küçült
RUN pip install --user --no-cache-dir -r requirements.txt

# --- Stage 2: Runtime ---
# Temiz, sıfır bir Python image'ı açar. Builder'daki gcc, libpq-dev gibi derleme araçları buraya gelmiyor.
FROM python:3.11-slim AS runtime

#Sadece libpq5 kurar — PostgreSQL'in runtime kütüphanesi.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Güvenlik: root olmayan kullanıcı (appuser adında şifresiz, login kabuğu olmayan bir kullanıcı oluşturur.w)
RUN adduser --disabled-password --gecos "" appuser
WORKDIR /app

# Bağımlılıkları builder'dan kopyala
COPY --from=builder /root/.local /home/appuser/.local

# Uygulama dosyalarını container'a kopyalar
COPY src/       src/
COPY app.py     app.py
COPY main.py    main.py
COPY scripts/   scripts/
COPY eval/      eval/

# Veri ve log dizinleri (volume mount noktaları)
RUN mkdir -p data/processed logs && chown -R appuser:appuser /app

USER appuser
# appuser'ın pip ile kurduğu streamlit binary'si bulunabilsin
ENV PATH="/home/appuser/.local/bin:$PATH"
# Python çıktısını buffer'lamadan direkt yazar — CloudWatch logları gerçek zamanlı görünür
ENV PYTHONUNBUFFERED=1
# .pyc dosyaları oluşturma — container'da anlamsız, disk israfı
ENV PYTHONDONTWRITEBYTECODE=1
# from src.agent import ... importları /app/src'yi bulur
ENV PYTHONPATH=/app
# Bu container 8501 portunu dinliyor
EXPOSE 8501

# Streamlit ayarları — browser otomatik açılmasın, CORS izin ver
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]