# Eval Report

**Tarih:** 2026-08-10 15:08  
**Model:** gpt-5.4-mini  
**Mod:** live (hibrit: exact + llm_judge)  

## Özet

| Metrik | Değer |
|--------|-------|
| Accuracy | **17/25 (68.0%)** |
| — exact match | 12 |
| — llm_judge kurtardı | 5 |
| Ort. retry | 0.0 |
| p95 latency | 12.8s |

## Soru Bazında Sonuçlar

| ID | Zorluk | Doğru | Yöntem | Retry | Süre (s) | Skills |
|----|--------|-------|--------|-------|----------|--------|
| q01 | medium | ✅ | exact | 0 | 6.21 | LEFT JOIN, IS NULL |
| q02 | hard | ✅ | exact | 0 | 6.16 | INNER JOIN x3, COALESCE, date filter, GROUP BY, ORDER BY, LIMIT |
| q03 | medium | ✅ | 🤖judge | 0 | 5.11 | self-join, LEFT JOIN, COALESCE |
| q04 | hard | ✅ | exact | 0 | 12.8 | INNER JOIN, LEFT JOIN, COUNT DISTINCT, ratio |
| q05 | medium | ✅ | exact | 0 | 6.32 | COALESCE, filter, GROUP BY |
| q06 | hard | ✅ | exact | 0 | 5.38 | LEFT JOIN, anti-join, IS NULL |
| q07 | very_hard | ❌ | 🤖judge | 0 | 6.76 | multi-join, COALESCE, aggregate math, cost |
| q08 | hard | ❌ | 🤖judge | 0 | 5.7 | NULL semantics, date_trunc, GROUP BY |
| q09 | very_hard | ✅ | 🤖judge | 0 | 7.41 | window function, RANK/ROW_NUMBER, PARTITION BY, multi-join |
| q10 | medium | ✅ | exact | 0 | 5.5 | GROUP BY, aggregate |
| q11 | easy | ✅ | exact | 0 | 4.33 | COUNT |
| q12 | easy | ✅ | exact | 0 | 6.57 | filter, COUNT |
| q13 | easy | ✅ | exact | 0 | 7.15 | ORDER BY, LIMIT |
| q14 | medium | ✅ | 🤖judge | 0 | 5.24 | DATE_TRUNC, date filter, GROUP BY |
| q15 | medium | ❌ | 🤖judge | 0 | 5.43 | INNER JOIN, GROUP BY, ORDER BY, LIMIT |
| q16 | medium | ✅ | exact | 0 | 4.82 | INNER JOIN, AVG, COUNT, GROUP BY |
| q17 | medium | ✅ | exact | 0 | 5.44 | NULL semantics, DATEDIFF, AVG, filter |
| q18 | hard | ✅ | 🤖judge | 0 | 5.31 | CASE WHEN, ratio, GROUP BY |
| q19 | hard | ✅ | exact | 0 | 4.92 | INNER JOIN, COALESCE, filter, SUM |
| q20 | hard | ✅ | 🤖judge | 0 | 5.91 | INNER JOIN, GROUP BY, SUM |
| q21 | hard | ❌ | 🤖judge | 0 | 5.93 | subquery, AVG, COALESCE, GROUP BY |
| q22 | hard | ❌ | 🤖judge | 0 | 5.44 | filter, computed column, ORDER BY, LIMIT |
| q23 | very_hard | ❌ | 🤖judge | 0 | 7.35 | multi-join, COALESCE, GROUP BY, EXTRACT |
| q24 | very_hard | ❌ | 🤖judge | 0 | 5.96 | CTE, window function, LAG, DATE_TRUNC |
| q25 | very_hard | ❌ | 🤖judge | 0 | 21.06 | multi-join, COALESCE, filter NOT IN, aggregate, ORDER BY |

## Agent SQL Çıktıları

### q01 — Ödeme kaydı hiç olmayan siparişleri durumlarına göre say.
```sql
SELECT
  o.status,
  COUNT(*) AS order_count
FROM orders o
LEFT JOIN payments p
  ON o.order_id = p.order_id
WHERE p.order_id IS NULL
GROUP BY o.status
ORDER BY o.status
```

### q02 — 2024 yılında kategoriye göre net ciroyu (indirimler düşülmüş) bul, en yüksek 5 kategori.
```sql
WITH category_net_revenue AS (
  SELECT
    c.category_name,
    SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0)) AS net_revenue
  FROM order_items oi
  JOIN orders o
    ON oi.order_id = o.order_id
  JOIN products p
    ON oi.product_id = p.product_id
  JOIN categories c
    ON p.category_id = c.category_id
  WHERE o.order_date >= DATE '2024-01-01'
    AND o.order_date < DATE '2025-01-01'
  GROUP BY c.category_name
)
SELECT
  category_name,
  ROUND(net_revenue, 2) AS net_revenue
FROM category_net_revenue
ORDER BY net_revenue DESC
LIMIT 5;
```

### q03 — Her kategoriyi üst kategorisiyle birlikte listele; kök kategoriler için '(root)' yaz.
> **Judge:** Agent's output correctly lists each category with its parent category, including marking root categories with '(root)', matching the reference output in terms of structure and content.

```sql
SELECT c.category_name AS category_name, COALESCE(p.category_name, '(root)') AS parent_category_name FROM categories c LEFT JOIN categories p ON c.parent_category_id = p.category_id ORDER BY c.category_name;
```

### q04 — Ülkeye göre iade oranını yüzde olarak hesapla ve azalan sırala.
```sql
WITH order_counts AS (
  SELECT
    c.country,
    COUNT(DISTINCT o.order_id) AS total_orders,
    COUNT(DISTINCT r.order_id) AS refunded_orders
  FROM customers c
  LEFT JOIN orders o
    ON c.customer_id = o.customer_id
  LEFT JOIN refunds r
    ON o.order_id = r.order_id
  GROUP BY c.country
)
SELECT
  country,
  ROUND(100.0 * refunded_orders / NULLIF(total_orders, 0), 2) AS refund_rate_percentage
FROM order_counts
ORDER BY refund_rate_percentage DESC;
```

### q05 — Tahsil edilen (captured) ödemelerde ödeme yöntemine göre toplam tutarı bul; tutarı NULL olanları 0 say.
```sql
SELECT p.payment_method, ROUND(SUM(COALESCE(p.amount, 0)), 2) AS total_amount
FROM payments p
WHERE p.payment_status = 'captured'
GROUP BY p.payment_method
ORDER BY p.payment_method
```

### q06 — Hiç sipariş vermemiş müşteri sayısını bul.
```sql
SELECT COUNT(*) AS customer_count
FROM customers c
LEFT JOIN orders o
  ON c.customer_id = o.customer_id
WHERE o.order_id IS NULL;
```

### q07 — Her müşteri segmenti için ortalama sipariş değerini (AOV) ve brüt kar marjını hesapla.
> **Judge:** Temel sayısal değerler farklı. Agent'ın AOV ve brüt kar marjı değerleri, referans sonuçlarla uyuşmuyor.

```sql
WITH order_revenue AS (
  SELECT
    o.customer_id,
    SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0)) AS gross_revenue,
    SUM(oi.quantity * p.cost) AS total_cost
  FROM orders o
  JOIN order_items oi
    ON o.order_id = oi.order_id
  JOIN products p
    ON oi.product_id = p.product_id
  GROUP BY o.customer_id
), customer_orders AS (
  SELECT
    o.customer_id,
    COUNT(DISTINCT o.order_id) AS order_count
  FROM orders o
  GROUP BY o.customer_id
)
SELECT
  c.segment,
  ROUND(AVG(orv.gross_revenue), 2) AS aov,
  ROUND((SUM(orv.gross_revenue) - SUM(orv.total_cost)) / NULLIF(SUM(orv.gross_revenue), 0) * 100, 2) AS gross_profit_margin_pct
FROM customers c
JOIN order_revenue orv
  ON c.customer_id = orv.customer_id
JOIN customer_orders co
  ON c.customer_id = co.customer_id
GROUP BY c.segment
ORDER BY c.segment;
```

### q08 — Aylara göre kargolanmış ama teslim edilmemiş sipariş sayısını bul (shipped_at dolu, delivered_at boş).
> **Judge:** Temel sayısal değerler farklı. Agent'ın yanıtında 2023-03 için 27, referans sonuçta ise 25 var. Ayrıca, 2023-04 için 21, referans sonuçta ise 22 var. Bu nedenle, agent'ın cevabı doğru değil.

```sql
SELECT strftime(order_date, '%Y-%m') AS month, COUNT(*) AS shipped_not_delivered_order_count
FROM orders
WHERE shipped_at IS NOT NULL
  AND delivered_at IS NULL
GROUP BY 1
ORDER BY 1;
```

### q09 — Her kategoride net ciroya göre ilk 3 ürünü sırala (kategori içi sıralama).
> **Judge:** Agent's output matches the reference output in terms of product rankings and net revenue values, despite minor differences in column names.

```sql
WITH product_revenue AS (
    SELECT
        p.category_id,
        p.product_id,
        p.product_name,
        ROUND(SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0)), 2) AS net_revenue
    FROM order_items oi
    JOIN products p ON oi.product_id = p.product_id
    GROUP BY p.category_id, p.product_id, p.product_name
), ranked_products AS (
    SELECT
        pr.category_id,
        c.category_name,
        pr.product_id,
        pr.product_name,
        pr.net_revenue,
        ROW_NUMBER() OVER (PARTITION BY pr.category_id ORDER BY pr.net_revenue DESC, pr.product_id) AS rn
    FROM product_revenue pr
    JOIN categories c ON pr.category_id = c.category_id
)
SELECT
    category_name,
    product_id,
    product_name,
    net_revenue
FROM ranked_products
WHERE rn <= 3
ORDER BY category_name, rn;
```

### q10 — İade nedenlerine göre toplam iade tutarını ve adet sayısını bul.
```sql
SELECT reason, ROUND(SUM(amount), 2) AS total_refund_amount, COUNT(*) AS refund_count FROM refunds GROUP BY reason ORDER BY total_refund_amount DESC, refund_count DESC;
```

### q11 — Toplam kaç sipariş var?
```sql
SELECT COUNT(*) AS total_orders FROM orders
```

### q12 — Aktif olmayan ürün sayısı kaçtır?
```sql
SELECT COUNT(*) AS inactive_product_count FROM products WHERE is_active = FALSE
```

### q13 — En pahalı 5 ürünü fiyatlarıyla listele.
```sql
SELECT p.product_name, ROUND(p.unit_price, 2) AS unit_price
FROM products p
ORDER BY p.unit_price DESC
LIMIT 5
```

### q14 — 2024 yılında aylık sipariş sayısı nasıl değişmiş?
> **Judge:** Agent's response provides the correct monthly order counts for 2024, despite the difference in date format. The order counts match the reference data.

```sql
SELECT strftime(order_date, '%Y-%m') AS month, COUNT(*) AS order_count
FROM orders
WHERE order_date >= DATE '2024-01-01'
  AND order_date < DATE '2025-01-01'
GROUP BY 1
ORDER BY 1
```

### q15 — En çok sipariş veren 10 müşteriyi bul.
> **Judge:** Agent'ın döndürdüğü sonuçta, müşteri sıralaması ve sipariş sayıları referans sonuçla uyuşmuyor. Özellikle, müşteri ID'leri ve sipariş sayıları arasında tutarsızlık var. Örneğin, referans sonuçta 440, 297 ve 466 müşteri ID'leri var, ancak agent sonuçta bu ID'ler yok. Bu nedenle, agent doğru yanıt vermemiştir.

```sql
SELECT c.customer_id, c.full_name, COUNT(o.order_id) AS order_count
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
GROUP BY c.customer_id, c.full_name
ORDER BY order_count DESC, c.customer_id
LIMIT 10
```

### q16 — Kategori bazında ortalama ürün fiyatı ve ürün sayısı nedir?
```sql
SELECT c.category_name, ROUND(AVG(p.unit_price), 2) AS average_product_price, COUNT(p.product_id) AS product_count FROM products p JOIN categories c ON p.category_id = c.category_id GROUP BY c.category_name ORDER BY c.category_name
```

### q17 — Teslim edilen siparişlerde ortalama teslimat süresi kaç gündür?
```sql
SELECT ROUND(AVG(date_diff('day', o.order_date, CAST(o.delivered_at AS DATE))), 2) AS average_delivery_days FROM orders o WHERE o.status = 'delivered' AND o.delivered_at IS NOT NULL
```

### q18 — Ödeme yöntemine göre başarılı ödeme (captured) oranını yüzde olarak bul.
> **Judge:** Agent'ın cevabı, ödeme yöntemine göre başarılı ödeme oranlarını doğru bir şekilde hesaplamış ve referans sonuçlarla aynı sıralamayı korumuş. Yüzde değerleri de doğru verilmiş.

```sql
SELECT payment_method, ROUND(100.0 * COUNT(*) FILTER (WHERE payment_status = 'captured') / NULLIF(COUNT(*), 0), 2) AS captured_rate_percent FROM payments GROUP BY payment_method ORDER BY payment_method
```

### q19 — İptal edilen siparişlerin toplam ciro kaybı ne kadardır?
```sql
SELECT ROUND(SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0)), 2) AS total_revenue_loss
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.status = 'cancelled';
```

### q20 — Sipariş durumuna göre iade sayısı ve toplam iade tutarını bul.
> **Judge:** Agent's result provides the correct refund count and total refund amount for the 'refunded' status, which matches the reference result. The additional statuses with zero refunds do not affect the correctness of the answer.

```sql
WITH refund_stats AS (
  SELECT
    o.status,
    COUNT(r.refund_id) AS refund_count,
    ROUND(COALESCE(SUM(r.amount), 0), 2) AS total_refund_amount
  FROM orders o
  LEFT JOIN refunds r
    ON o.order_id = r.order_id
  GROUP BY o.status
)
SELECT
  status,
  refund_count,
  total_refund_amount
FROM refund_stats
ORDER BY status;
```

### q21 — Sipariş başına ortalama kalem sayısı ve ortalama sipariş tutarı nedir?
> **Judge:** Temel sayısal değerler farklı. Agent'ın yanıtı 10.07 ve 3128.68 iken, referans sonuç 2.87 ve 6634.72. Bu, yanlış hesaplama olduğunu gösteriyor.

```sql
WITH order_metrics AS (
  SELECT
    oi.order_id,
    SUM(oi.quantity) AS total_item_count,
    SUM(oi.quantity * oi.unit_price - oi.discount_amount) AS order_amount
  FROM order_items oi
  GROUP BY oi.order_id
)
SELECT
  ROUND(AVG(total_item_count), 2) AS avg_item_count_per_order,
  ROUND(AVG(order_amount), 2) AS avg_order_amount
FROM order_metrics;
```

### q22 — Aktif ürünlerde kar marjı oranı en yüksek 10 ürünü bul.
> **Judge:** Agent's results show profit margins of 0.58 (58%) for multiple products, while the reference results show different profit margins for the same products, indicating a discrepancy in the calculations. The agent's output does not match the expected profit margins.

```sql
WITH product_profit AS (
    SELECT
        p.product_id,
        p.product_name,
        p.unit_price,
        p.cost,
        (p.unit_price - p.cost) / NULLIF(p.unit_price, 0) AS profit_margin_rate
    FROM products p
    WHERE p.is_active = TRUE
)
SELECT
    product_id,
    product_name,
    ROUND(profit_margin_rate, 2) AS profit_margin_rate
FROM product_profit
ORDER BY profit_margin_rate DESC, product_id
LIMIT 10;
```

### q23 — Müşteri segmentine ve yıla göre net ciro ve sipariş sayısı trendi.
> **Judge:** Temel sayısal değerler farklı. Agent'ın döndürdüğü net ciro ve sipariş sayısı, referans sonuçlarla karşılaştırıldığında önemli farklılıklar göstermektedir.

```sql
WITH yearly_orders AS (
  SELECT
    c.segment,
    EXTRACT(YEAR FROM o.order_date) AS order_year,
    COUNT(DISTINCT o.order_id) AS order_count,
    SUM(COALESCE(p.amount, 0) - COALESCE(r.amount, 0)) AS net_revenue
  FROM orders o
  JOIN customers c
    ON o.customer_id = c.customer_id
  LEFT JOIN payments p
    ON o.order_id = p.order_id
   AND p.payment_status = 'captured'
  LEFT JOIN refunds r
    ON o.order_id = r.order_id
  GROUP BY c.segment, EXTRACT(YEAR FROM o.order_date)
)
SELECT
  segment,
  order_year,
  ROUND(net_revenue, 2) AS net_revenue,
  order_count
FROM yearly_orders
ORDER BY segment, order_year;
```

### q24 — Aylık sipariş sayısındaki bir önceki aya göre değişimi bul (LAG).
> **Judge:** Temel sayısal değerler farklı. Agent'ın yanıtında, 'month_over_month_change' değerleri ile referans sonuçtaki 'change_pct' değerleri arasında tutarsızlık var. Örneğin, 2023-02-01 için agent -15, referans -10.0; 2023-03-01 için agent 10, referans 7.41 gibi. Bu nedenle, agent doğru yanıt vermemiştir.

```sql
WITH monthly_orders AS (
  SELECT
    DATE_TRUNC('month', order_date) AS month,
    COUNT(*) AS order_count
  FROM orders
  GROUP BY 1
)
SELECT
  month,
  order_count,
  LAG(order_count) OVER (ORDER BY month) AS previous_month_order_count,
  order_count - LAG(order_count) OVER (ORDER BY month) AS month_over_month_change
FROM monthly_orders
ORDER BY month;
```

### q25 — İptal ve iade edilmemiş siparişlerde en yüksek yaşam boyu değere (CLV) sahip 10 müşteriyi bul.
> **Judge:** Agent's results do not match the reference results in terms of customer IDs and CLV values. For example, the reference shows customer_id 439 with a CLV of 94117.37, while the agent shows the same customer_id with a CLV of 81609.93. This indicates a fundamental error in the calculations or data retrieval.

```sql
WITH customer_clv AS (
    SELECT
        c.customer_id,
        c.full_name,
        ROUND(COALESCE(SUM(p.amount), 0), 2) AS clv
    FROM customers c
    JOIN orders o
        ON c.customer_id = o.customer_id
    LEFT JOIN payments p
        ON o.order_id = p.order_id
       AND p.payment_status = 'captured'
    LEFT JOIN refunds r
        ON o.order_id = r.order_id
    WHERE o.status NOT IN ('cancelled', 'refunded')
      AND r.order_id IS NULL
    GROUP BY c.customer_id, c.full_name
)
SELECT
    customer_id,
    full_name,
    clv
FROM customer_clv
ORDER BY clv DESC, customer_id
LIMIT 10;
```
