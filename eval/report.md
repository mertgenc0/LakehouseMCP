# Eval Report

**Tarih:** 2026-08-10 14:11  
**Model:** gpt-4o  
**Mod:** live (hibrit: exact + llm_judge)  

## Özet

| Metrik | Değer |
|--------|-------|
| Accuracy | **15/25 (60.0%)** |
| — exact match | 12 |
| — llm_judge kurtardı | 3 |
| Ort. retry | 0.12 |
| p95 latency | 7.27s |

## Soru Bazında Sonuçlar

| ID | Zorluk | Doğru | Yöntem | Retry | Süre (s) | Skills |
|----|--------|-------|--------|-------|----------|--------|
| q01 | medium | ✅ | exact | 0 | 6.71 | LEFT JOIN, IS NULL |
| q02 | hard | ❌ | 🤖judge | 0 | 6.17 | INNER JOIN x3, COALESCE, date filter, GROUP BY, ORDER BY, LIMIT |
| q03 | medium | ✅ | exact | 0 | 5.57 | self-join, LEFT JOIN, COALESCE |
| q04 | hard | ✅ | exact | 0 | 6.51 | INNER JOIN, LEFT JOIN, COUNT DISTINCT, ratio |
| q05 | medium | ✅ | exact | 0 | 4.29 | COALESCE, filter, GROUP BY |
| q06 | hard | ✅ | exact | 0 | 4.07 | LEFT JOIN, anti-join, IS NULL |
| q07 | very_hard | ❌ | 🤖judge | 0 | 5.21 | multi-join, COALESCE, aggregate math, cost |
| q08 | hard | ✅ | exact | 0 | 5.97 | NULL semantics, date_trunc, GROUP BY |
| q09 | very_hard | ❌ | 🤖judge | 0 | 8.02 | window function, RANK/ROW_NUMBER, PARTITION BY, multi-join |
| q10 | medium | ✅ | exact | 0 | 6.65 | GROUP BY, aggregate |
| q11 | easy | ✅ | exact | 0 | 3.73 | COUNT |
| q12 | easy | ✅ | exact | 0 | 4.21 | filter, COUNT |
| q13 | easy | ✅ | exact | 0 | 4.99 | ORDER BY, LIMIT |
| q14 | medium | ✅ | 🤖judge | 0 | 5.2 | DATE_TRUNC, date filter, GROUP BY |
| q15 | medium | ❌ | 🤖judge | 0 | 5.23 | INNER JOIN, GROUP BY, ORDER BY, LIMIT |
| q16 | medium | ✅ | exact | 0 | 5.63 | INNER JOIN, AVG, COUNT, GROUP BY |
| q17 | medium | ❌ | 🤖judge | 1 | 6.23 | NULL semantics, DATEDIFF, AVG, filter |
| q18 | hard | ✅ | 🤖judge | 0 | 5.22 | CASE WHEN, ratio, GROUP BY |
| q19 | hard | ❌ | exact | 2 | 7.27 | INNER JOIN, COALESCE, filter, SUM |
| q20 | hard | ✅ | exact | 0 | 4.16 | INNER JOIN, GROUP BY, SUM |
| q21 | hard | ❌ | 🤖judge | 0 | 4.66 | subquery, AVG, COALESCE, GROUP BY |
| q22 | hard | ❌ | 🤖judge | 0 | 5.06 | filter, computed column, ORDER BY, LIMIT |
| q23 | very_hard | ❌ | 🤖judge | 0 | 6.88 | multi-join, COALESCE, GROUP BY, EXTRACT |
| q24 | very_hard | ✅ | 🤖judge | 0 | 6.14 | CTE, window function, LAG, DATE_TRUNC |
| q25 | very_hard | ❌ | 🤖judge | 0 | 5.57 | multi-join, COALESCE, filter NOT IN, aggregate, ORDER BY |

## Agent SQL Çıktıları

### q01 — Ödeme kaydı hiç olmayan siparişleri durumlarına göre say.
```sql
SELECT o.status, COUNT(*) AS order_count
FROM orders o
LEFT JOIN payments p ON o.order_id = p.order_id
WHERE p.order_id IS NULL
GROUP BY o.status;
```

### q02 — 2024 yılında kategoriye göre net ciroyu (indirimler düşülmüş) bul, en yüksek 5 kategori.
> **Judge:** Temel sayısal değerler farklı. Agent'ın sonuçları, referans sonuçlarla karşılaştırıldığında net ciro değerleri açısından tutarsızlık göstermektedir.

```sql
SELECT c.category_name, ROUND(SUM(oi.quantity * (oi.unit_price - oi.discount_amount)), 2) AS net_revenue
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p ON oi.product_id = p.product_id
JOIN categories c ON p.category_id = c.category_id
WHERE o.order_date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY c.category_name
ORDER BY net_revenue DESC
LIMIT 5;
```

### q03 — Her kategoriyi üst kategorisiyle birlikte listele; kök kategoriler için '(root)' yaz.
```sql
SELECT c1.category_id, c1.category_name, COALESCE(c2.category_name, '(root)') AS parent_category_name
FROM categories c1
LEFT JOIN categories c2 ON c1.parent_category_id = c2.category_id;
```

### q04 — Ülkeye göre iade oranını yüzde olarak hesapla ve azalan sırala.
```sql
SELECT c.country, ROUND(SUM(CASE WHEN o.status = 'refunded' THEN 1 ELSE 0 END) * 100.0 / COUNT(o.order_id), 2) AS refund_rate
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.country
ORDER BY refund_rate DESC;
```

### q05 — Tahsil edilen (captured) ödemelerde ödeme yöntemine göre toplam tutarı bul; tutarı NULL olanları 0 say.
```sql
SELECT payment_method, COALESCE(SUM(amount), 0) AS total_amount
FROM payments
WHERE payment_status = 'captured'
GROUP BY payment_method
```

### q06 — Hiç sipariş vermemiş müşteri sayısını bul.
```sql
SELECT COUNT(*) AS customer_count FROM customers WHERE customer_id NOT IN (SELECT DISTINCT customer_id FROM orders)
```

### q07 — Her müşteri segmenti için ortalama sipariş değerini (AOV) ve brüt kar marjını hesapla.
> **Judge:** Temel sayısal değerler farklı. Agent'ın hesapladığı ortalama sipariş değerleri (AOV) ve brüt kar marjları, referans sonuçlarla uyuşmuyor. Örneğin, 'consumer' segmenti için agent 597.58 AOV ve -0.36 brüt kar marjı verirken, referans sonuç 6894.65 AOV ve 8275147.95 brüt kar marjı veriyor.

```sql
SELECT c.segment, 
       ROUND(AVG(oi.quantity * (oi.unit_price - oi.discount_amount)), 2) AS average_order_value, 
       ROUND(AVG((oi.quantity * (oi.unit_price - oi.discount_amount) - oi.quantity * p.cost) / (oi.quantity * (oi.unit_price - oi.discount_amount))), 2) AS gross_margin
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p ON oi.product_id = p.product_id
GROUP BY c.segment;
```

### q08 — Aylara göre kargolanmış ama teslim edilmemiş sipariş sayısını bul (shipped_at dolu, delivered_at boş).
```sql
SELECT DATE_TRUNC('month', shipped_at) AS month, COUNT(order_id) AS undelivered_shipped_orders
FROM orders
WHERE shipped_at IS NOT NULL AND delivered_at IS NULL
GROUP BY month
ORDER BY month;
```

### q09 — Her kategoride net ciroya göre ilk 3 ürünü sırala (kategori içi sıralama).
> **Judge:** Agent's results do not match the reference results in terms of product names and net revenues. The products listed by the agent do not correspond to the top products in the reference data for the respective categories.

```sql
WITH ProductRevenue AS (
    SELECT 
        p.product_id, 
        p.product_name, 
        p.category_id, 
        SUM((oi.quantity * oi.unit_price) - oi.discount_amount) AS net_revenue
    FROM 
        order_items oi
    JOIN 
        products p ON oi.product_id = p.product_id
    GROUP BY 
        p.product_id, p.product_name, p.category_id
), RankedProducts AS (
    SELECT 
        pr.product_id, 
        pr.product_name, 
        pr.category_id, 
        pr.net_revenue,
        ROW_NUMBER() OVER (PARTITION BY pr.category_id ORDER BY pr.net_revenue DESC) as rank
    FROM 
        ProductRevenue pr
)
SELECT 
    rp.category_id, 
    rp.product_id, 
    rp.product_name, 
    ROUND(rp.net_revenue, 2) as net_revenue
FROM 
    RankedProducts rp
WHERE 
    rp.rank <= 3
ORDER BY 
    rp.category_id, rp.rank;
```

### q10 — İade nedenlerine göre toplam iade tutarını ve adet sayısını bul.
```sql
SELECT reason, ROUND(SUM(amount), 2) AS total_refund_amount, COUNT(refund_id) AS refund_count FROM refunds GROUP BY reason
```

### q11 — Toplam kaç sipariş var?
```sql
SELECT COUNT(order_id) AS total_orders FROM orders
```

### q12 — Aktif olmayan ürün sayısı kaçtır?
```sql
SELECT COUNT(*) AS inactive_product_count FROM products WHERE is_active = FALSE;
```

### q13 — En pahalı 5 ürünü fiyatlarıyla listele.
```sql
SELECT product_name, unit_price FROM products ORDER BY unit_price DESC LIMIT 5
```

### q14 — 2024 yılında aylık sipariş sayısı nasıl değişmiş?
> **Judge:** Agent'ın cevabı, referans sonuçla aynı verileri içeriyor. Sadece tarih formatı farklı, ancak her iki sonuç da 2024 yılına ait aylık sipariş sayılarını doğru bir şekilde yansıtıyor. Bu nedenle, agent'ın cevabı doğru kabul edilebilir.

```sql
SELECT EXTRACT(MONTH FROM order_date) AS month, COUNT(order_id) AS order_count
FROM orders
WHERE EXTRACT(YEAR FROM order_date) = 2024
GROUP BY month
ORDER BY month;
```

### q15 — En çok sipariş veren 10 müşteriyi bul.
> **Judge:** Agent's result includes different customers compared to the reference result. Specifically, the agent's output has customer_id 440 and customer_id 208 is missing, which indicates a discrepancy in the top 10 customers by total orders.

```sql
SELECT c.customer_id, c.full_name, COUNT(o.order_id) AS total_orders
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
GROUP BY c.customer_id, c.full_name
ORDER BY total_orders DESC
LIMIT 10;
```

### q16 — Kategori bazında ortalama ürün fiyatı ve ürün sayısı nedir?
```sql
SELECT c.category_name, ROUND(AVG(p.unit_price), 2) AS average_price, COUNT(p.product_id) AS product_count
FROM products p
JOIN categories c ON p.category_id = c.category_id
GROUP BY c.category_name
```

### q17 — Teslim edilen siparişlerde ortalama teslimat süresi kaç gündür?
> **Judge:** Temel sayısal değerler farklı: agent'ın cevabı 3.52 gün, referans sonucu 6.03 gün. Bu, doğru bir hesaplama yapılmadığını gösteriyor.

```sql
SELECT ROUND(AVG(julian(delivered_at) - julian(shipped_at)), 2) AS average_delivery_days FROM orders WHERE status = 'delivered';
```

### q18 — Ödeme yöntemine göre başarılı ödeme (captured) oranını yüzde olarak bul.
> **Judge:** Agent's response provides the correct captured percentages for each payment method, matching the reference results in terms of values and order, despite minor formatting differences.

```sql
SELECT payment_method, ROUND(100.0 * SUM(CASE WHEN payment_status = 'captured' THEN 1 ELSE 0 END) / COUNT(*), 2) AS captured_percentage
FROM payments
GROUP BY payment_method;
```

### q19 — İptal edilen siparişlerin toplam ciro kaybı ne kadardır?
```sql
SELECT ROUND(SUM(revenue), 2) AS total_revenue_loss FROM orders WHERE status = 'cancelled';
```

### q20 — Sipariş durumuna göre iade sayısı ve toplam iade tutarını bul.
```sql
SELECT o.status, COUNT(r.refund_id) AS refund_count, ROUND(SUM(r.amount), 2) AS total_refund_amount
FROM orders o
JOIN refunds r ON o.order_id = r.order_id
GROUP BY o.status
```

### q21 — Sipariş başına ortalama kalem sayısı ve ortalama sipariş tutarı nedir?
> **Judge:** Temel sayısal değerler farklı: agent'ın yanıtında ortalama sipariş tutarı 3128.68 iken referans sonuçta 6634.72. Bu, yanlış hesaplama olduğu anlamına geliyor.

```sql
SELECT ROUND(AVG(item_count), 2) AS avg_items_per_order, ROUND(AVG(order_total), 2) AS avg_order_total
FROM (
  SELECT o.order_id, 
         COUNT(oi.order_item_id) AS item_count, 
         SUM(oi.quantity * oi.unit_price - oi.discount_amount) AS order_total
  FROM orders o
  JOIN order_items oi ON o.order_id = oi.order_id
  GROUP BY o.order_id
) subquery;
```

### q22 — Aktif ürünlerde kar marjı oranı en yüksek 10 ürünü bul.
> **Judge:** Agent's results show profit margins of 0.58 for multiple products, while the reference results show different profit margins (e.g., 58.28, 58.25, etc.). This indicates a fundamental discrepancy in the calculated profit margins, which are not just a matter of scale but represent incorrect values.

```sql
SELECT p.product_id, p.product_name, ROUND((p.unit_price - p.cost) / p.unit_price, 2) AS profit_margin
FROM products p
WHERE p.is_active = TRUE
ORDER BY profit_margin DESC
LIMIT 10;
```

### q23 — Müşteri segmentine ve yıla göre net ciro ve sipariş sayısı trendi.
> **Judge:** Temel sayısal değerler farklı. Agent'ın döndürdüğü net ciro ve sipariş sayısı, referans sonuçlarla karşılaştırıldığında önemli farklılıklar göstermektedir. Örneğin, 2023 yılı için 'consumer' segmentinde agent 577174.24 net ciro ve 987 sipariş sayısı verirken, referans sonuç 7322918.86 net ciro ve 1086 sipariş sayısı vermektedir. Bu nedenle agent'ın cevabı doğru değildir.

```sql
SELECT c.segment, YEAR(o.order_date) AS year, ROUND(SUM(oi.quantity * (oi.unit_price - oi.discount_amount)), 2) AS net_revenue, COUNT(DISTINCT o.order_id) AS order_count
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.status IN ('delivered', 'paid', 'shipped')
GROUP BY c.segment, YEAR(o.order_date)
ORDER BY c.segment, year;
```

### q24 — Aylık sipariş sayısındaki bir önceki aya göre değişimi bul (LAG).
> **Judge:** Agent's output correctly calculates the monthly order count, previous month order count, and percentage change using the LAG function. The values match the expected results despite minor differences in column names and formats.

```sql
WITH monthly_orders AS (
  SELECT DATE_TRUNC('month', order_date) AS month, COUNT(order_id) AS order_count
  FROM orders
  GROUP BY DATE_TRUNC('month', order_date)
),
monthly_change AS (
  SELECT 
    month, 
    order_count, 
    LAG(order_count) OVER (ORDER BY month) AS previous_month_order_count
  FROM monthly_orders
)
SELECT 
  month, 
  order_count, 
  previous_month_order_count, 
  ROUND((order_count - previous_month_order_count) * 100.0 / previous_month_order_count, 2) AS percentage_change
FROM monthly_change
WHERE previous_month_order_count IS NOT NULL;
```

### q25 — İptal ve iade edilmemiş siparişlerde en yüksek yaşam boyu değere (CLV) sahip 10 müşteriyi bul.
> **Judge:** Agent's result does not match the reference results in terms of customer IDs and their corresponding CLV values. The agent's output includes customers with significantly lower CLV values than those in the reference output, indicating a fundamental error in the calculation or selection of customers.

```sql
SELECT c.customer_id, c.full_name, ROUND(SUM(oi.quantity * (oi.unit_price - oi.discount_amount)), 2) AS clv
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
LEFT JOIN refunds r ON o.order_id = r.order_id
WHERE o.status NOT IN ('cancelled', 'refunded') AND r.order_id IS NULL
GROUP BY c.customer_id, c.full_name
ORDER BY clv DESC
LIMIT 10;
```
