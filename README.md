# MCP-Based Autonomous Data Lakehouse Copilot

Doğal dilde sorulan analitik soruları anlayıp, **MCP (Model Context Protocol)** üzerinden
DuckDB (Parquet/CSV) ve PostgreSQL veri kaynaklarına erişen; SQL üretip çalıştıran, hata
alırsa **LangGraph self-correction loop** ile kendini düzelten ve sonucu iş diliyle
özetleyen otonom bir veri analiz ajanı.

```
Kullanıcı Sorusu
      │
      ▼
[LangGraph Agent] ──► schema_discovery ──► sql_generation
      ▲                                          │
      │                                          ▼
      └────── error_analysis ◄── validate ── mcp_tool_execution
                  (max N retry)                  │
                                                 ▼
                                            summarize ──► Cevap
```

Detaylı mimari sözleşme için: [CLAUDE.md](./CLAUDE.md).

---

## Kurulum

Gereken: **Python 3.11+**, Node.js (yalnız MCP Inspector için, opsiyonel).

```bash
# 1. Sanal ortam
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Bağımlılıklar
pip install -r requirements.txt

# 3. Ortam değişkenleri
cp .env.example .env
# .env'i aç, en azından OPENAI_API_KEY'i doldur

# 4. Örnek veri (Parquet: customers, products, orders)
python -m scripts.seed_data
```

---

## Çalıştırma

### CLI — tek soru
```bash
python main.py --question "En çok kazanan 3 kategori nedir?"
```

### CLI — interaktif REPL
```bash
python main.py
soru › İstanbul'da kaç müşteri var?
soru › En pahalı 5 ürün nedir?
soru › exit
```

### MCP sunucusunu bağımsız test etmek (Inspector)
```bash
npx @modelcontextprotocol/inspector python -m src.mcp_servers.duckdb_server
```
Tarayıcıda açılan panelde tool'ları elle çağırabilirsin.

---

## Mimari — Katmanlar

| Katman | Dizin | Sorumluluk |
|---|---|---|
| Presentation | `main.py` | Rich tabanlı CLI + REPL |
| Orchestration | `src/agent/` | LangGraph state machine + node'lar + prompt'lar |
| Client | `src/clients/` | Async MCP stdio client |
| Server | `src/mcp_servers/` | FastMCP DuckDB sunucusu (LLM bilmez) |
| Cross-cutting | `src/core/` | Config, logging, exception, guardrail |
| Data | `data/processed/` | Parquet dosyaları (gitignore) |

**Kritik sınır kuralı:** Ajan doğrudan `duckdb.connect()` çağıramaz — veriye yalnızca MCP
tool'ları üzerinden erişir. MCP sunucuları LLM veya LangChain import etmez.

---

## Güvenlik — SQL Guardrail

- Yalnızca `SELECT` / `WITH ... SELECT` sorguları çalıştırılır.
- Yasak keyword'ler (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `CREATE`, `ATTACH`,
  `COPY`, `INSTALL`, `LOAD`, `TRUNCATE`, `GRANT`, `REVOKE`, `VACUUM`, `SET`) engellenir.
- Her sorguya otomatik `LIMIT` (varsayılan 1000) eklenir.
- Dosya erişimi `DATA_DIR` altına kısıtlıdır (path traversal koruması).
- String literal içindeki yasak keyword'ler false-positive üretmez.

---

## Kalite

```bash
ruff format . && ruff check .    # stil + lint
mypy                              # tip denetimi (--strict)
pytest --cov=src                  # test + coverage
```

Mevcut durum: **46/46 test yeşil, %92 coverage, ruff temiz, mypy --strict temiz.**

---

## Konfigürasyon (`.env`)

Tam listesi ve varsayılanları için `.env.example`'a bak. Öne çıkanlar:

| Değişken | Ne için |
|---|---|
| `OPENAI_API_KEY` | Zorunlu, gpt-4o çağrıları için |
| `OPENAI_MODEL` | Varsayılan `gpt-4o`, alternatif: `gpt-4o-mini` |
| `MAX_RETRIES` | Self-correction loop üst sınırı (varsayılan 3) |
| `MAX_ROWS_RETURNED` | Auto-LIMIT değeri (varsayılan 1000) |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `DATA_DIR` | Parquet dosyalarının konumu (varsayılan `./data/processed`) |

---

## Ekran Görüntüleri

### 1. Basit agregasyon — DuckDB kaynağı
```bash
python main.py --question "En çok kazanan 3 kategori nedir?"
```
![Basit agregasyon](docs/screenshots/01_top_categories.png)

Agent DuckDB'yi seçer, JOIN + GROUP BY üretir, Türkçe iş diliyle özetler.

### 2. Karmaşık sorgu + zaman filtresi
```bash
python main.py --question "2024'ün ikinci yarısında kategori bazlı sipariş sayısı nedir?"
```
![Karmaşık sorgu](docs/screenshots/04_complex_query.png)

WHERE + GROUP BY birlikte kullanılır.

### 3. İnteraktif REPL
```bash
python main.py
```
![REPL](docs/screenshots/05_repl.png)

Arka arkaya soru sorulabilir; `exit`/`quit`/`Ctrl+C` ile çıkılır.


### 7. Guardrail bloğu (developer test)
```bash
python -c "
import asyncio
from src.clients.mcp_client import duckdb_client

async def main():
    async with duckdb_client() as c:
        r = await c.call_tool('query_sql', {'sql': 'DELETE FROM orders'})
        print(r)

asyncio.run(main())
"
```
![Guardrail](docs/screenshots/07_guardrail_block.png)

DELETE denenirse `GuardrailViolation` döner; process çökmez, structured error envelope.

---

## Örnek Sorular

Seed data ile denenmiş, çalıştığı doğrulanmış sorgular:

- *"En çok kazanan 3 kategori nedir?"*
- *"İstanbul'da kaç müşteri var?"*
- *"Her müşterinin toplam siparişi ve kayıt tarihi, ilk 5."*
- *"KOBİ segmentindeki müşterilerin toplam harcaması?"*
- *"En pahalı 10 ürünün kategorileri?"*
- *"Postgres'teki müşterilerden Ankara'da yaşayanlar?"*
- *"Parquet'teki en çok satan 5 ürün?"*

---

## Log Akışı

CLI çalışırken:
- **Konsol:** Rich formatlı çıktı (Soru, SQL, Sonuç tablosu, Cevap paneli)
- **stderr:** structlog INFO satırları (agent akışını izlemek için)
- **`logs/copilot.log`:** JSON satırları (grep/jq/aggregator için)

Sessiz konsol için: `python main.py 2>/dev/null` (log dosyaya yine yazılır).

---

## Lisans / Katkı

Learning/portfolio projesi.