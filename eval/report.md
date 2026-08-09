# Eval Report

**Tarih:** 2026-08-09 22:47  
**Model:** gpt-4o  
**Mod:** live (hibrit: exact + llm_judge)  

## Özet

| Metrik | Değer |
|--------|-------|
| Accuracy | **15/25 (60.0%)** |
| — exact match | 12 |
| — llm_judge kurtardı | 3 |
| Ort. retry | 0.04 |
| p95 latency | 7.26s |

## Soru Bazında Sonuçlar

| ID | Zorluk | Doğru | Yöntem | Retry | Süre (s) | Skills |
|----|--------|-------|--------|-------|----------|--------|
| q01 | medium | ✅ | exact | 0 | 3.89 | LEFT JOIN, IS NULL |
| q02 | hard | ❌ | 🤖judge | 0 | 5.52 | INNER JOIN x3, COALESCE, date filter, GROUP BY, ORDER BY, LIMIT |
| q03 | medium | ✅ | exact | 0 | 3.79 | self-join, LEFT JOIN, COALESCE |
| q04 | hard | ❌ | 🤖judge | 1 | 7.26 | INNER JOIN, LEFT JOIN, COUNT DISTINCT, ratio |
| q05 | medium | ✅ | exact | 0 | 3.99 | COALESCE, filter, GROUP BY |
| q06 | hard | ✅ | exact | 0 | 4.19 | LEFT JOIN, anti-join, IS NULL |
| q07 | very_hard | ❌ | 🤖judge | 0 | 4.7 | multi-join, COALESCE, aggregate math, cost |
| q08 | hard | ✅ | exact | 0 | 3.83 | NULL semantics, date_trunc, GROUP BY |
| q09 | very_hard | ❌ | 🤖judge | 0 | 8.37 | window function, RANK/ROW_NUMBER, PARTITION BY, multi-join |
| q10 | medium | ✅ | exact | 0 | 4.77 | GROUP BY, aggregate |
| q11 | easy | ✅ | exact | 0 | 3.07 | COUNT |
| q12 | easy | ✅ | exact | 0 | 3.52 | filter, COUNT |
| q13 | easy | ✅ | exact | 0 | 3.88 | ORDER BY, LIMIT |
| q14 | medium | ✅ | 🤖judge | 0 | 4.94 | DATE_TRUNC, date filter, GROUP BY |
| q15 | medium | ❌ | 🤖judge | 0 | 3.94 | INNER JOIN, GROUP BY, ORDER BY, LIMIT |
| q16 | medium | ✅ | exact | 0 | 3.96 | INNER JOIN, AVG, COUNT, GROUP BY |
| q17 | medium | ❌ | 🤖judge | 0 | 3.94 | NULL semantics, DATEDIFF, AVG, filter |
| q18 | hard | ✅ | 🤖judge | 0 | 3.89 | CASE WHEN, ratio, GROUP BY |
| q19 | hard | ❌ | 🤖judge | 0 | 4.43 | INNER JOIN, COALESCE, filter, SUM |
| q20 | hard | ❌ | 🤖judge | 0 | 4.64 | INNER JOIN, GROUP BY, SUM |
| q21 | hard | ✅ | exact | 0 | 4.27 | subquery, AVG, COALESCE, GROUP BY |
| q22 | hard | ✅ | exact | 0 | 3.94 | filter, computed column, ORDER BY, LIMIT |
| q23 | very_hard | ❌ | 🤖judge | 0 | 4.43 | multi-join, COALESCE, GROUP BY, EXTRACT |
| q24 | very_hard | ✅ | 🤖judge | 0 | 5.12 | CTE, window function, LAG, DATE_TRUNC |
| q25 | very_hard | ❌ | 🤖judge | 0 | 5.59 | multi-join, COALESCE, filter NOT IN, aggregate, ORDER BY |

## Agent SQL Çıktıları

### q01 — Ödeme kaydı hiç olmayan siparişleri durumlarına göre say.
```sql
SELECT o.status, COUNT(o.order_id) AS order_count
FROM orders o
LEFT JOIN payments p ON o.order_id = p.order_id
WHERE p.order_id IS NULL
GROUP BY o.status;
```

### q02 — 2024 yılında kategoriye göre net ciroyu (indirimler düşülmüş) bul, en yüksek 5 kategori.
> **Judge:** Agent's results do not match the reference results in terms of both the category names and the net revenue values. The agent's output includes categories and values that are different from those in the reference output.

```sql
SELECT c.category_name, ROUND(SUM(oi.quantity * (oi.unit_price - COALESCE(oi.discount_amount, 0))), 2) AS net_revenue
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
JOIN products p ON oi.product_id = p.product_id
JOIN categories c ON p.category_id = c.category_id
WHERE o.order_date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY c.category_name
ORDER BY net_revenue DESC
LIMIT 5;
```

### q03 — Her kategoriyi üst kategorisiyle birlikte listele; kök kategoriler için '(root)' yaz.
```sql
SELECT c.category_id, c.category_name, COALESCE(pc.category_name, '(root)') AS parent_category_name
FROM categories c
LEFT JOIN categories pc ON c.parent_category_id = pc.category_id;
```

### q04 — Ülkeye göre iade oranını yüzde olarak hesapla ve azalan sırala.
> **Judge:** Agent'ın döndürdüğü iade oranı yüzdesi (refund_rate) yanlış. Agent, iade oranlarını 100'den fazla değerler olarak hesaplamış, bu da mantıksız. Referans sonuçta ise iade oranları yüzde olarak doğru bir şekilde hesaplanmış ve daha makul değerler içeriyor.

```sql
SELECT c.country, ROUND(COALESCE(SUM(r.amount), 0) / NULLIF(SUM(oi.unit_price * oi.quantity - oi.discount_amount), 0) * 100, 2) AS refund_rate
FROM customers c
LEFT JOIN orders o ON c.customer_id = o.customer_id
LEFT JOIN order_items oi ON o.order_id = oi.order_id
LEFT JOIN refunds r ON o.order_id = r.order_id
GROUP BY c.country
ORDER BY refund_rate DESC;
```

### q05 — Tahsil edilen (captured) ödemelerde ödeme yöntemine göre toplam tutarı bul; tutarı NULL olanları 0 say.
```sql
SELECT payment_method, COALESCE(ROUND(SUM(amount), 2), 0) AS total_amount
FROM payments
WHERE payment_status = 'captured'
GROUP BY payment_method
```

### q06 — Hiç sipariş vermemiş müşteri sayısını bul.
```sql
SELECT COUNT(c.customer_id) AS non_ordering_customers_count
FROM customers c
LEFT JOIN orders o ON c.customer_id = o.customer_id
WHERE o.order_id IS NULL;
```

### q07 — Her müşteri segmenti için ortalama sipariş değerini (AOV) ve brüt kar marjını hesapla.
> **Judge:** Agent'ın sonuçları, referans sonuçlarla hem segment isimleri hem de sayısal değerler açısından uyuşmuyor. Agent'ın AOV değerleri çok daha düşük ve brüt kar marjı da yanlış. Bu nedenle doğru bir yanıt vermemiştir.

```sql
SELECT c.segment, 
       ROUND(AVG(oi.quantity * (oi.unit_price - COALESCE(oi.discount_amount, 0))), 2) AS average_order_value,
       ROUND(AVG((oi.unit_price - p.cost) / NULLIF(oi.unit_price, 0)), 2) AS gross_margin
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p ON oi.product_id = p.product_id
GROUP BY c.segment;
```

### q08 — Aylara göre kargolanmış ama teslim edilmemiş sipariş sayısını bul (shipped_at dolu, delivered_at boş).
```sql
SELECT DATE_TRUNC('month', shipped_at) AS month, COUNT(order_id) AS undelivered_orders_count
FROM orders
WHERE shipped_at IS NOT NULL AND delivered_at IS NULL
GROUP BY DATE_TRUNC('month', shipped_at)
ORDER BY month;
```

### q09 — Her kategoride net ciroya göre ilk 3 ürünü sırala (kategori içi sıralama).
> **Judge:** Agent's results do not match the reference results in terms of product names and net revenues. The net revenues provided by the agent are also incorrect compared to the reference values.

```sql
WITH product_revenue AS (
  SELECT 
    p.product_id, 
    p.product_name, 
    c.category_name, 
    SUM(oi.quantity * (oi.unit_price - COALESCE(oi.discount_amount, 0))) AS net_revenue
  FROM 
    products p
  JOIN 
    order_items oi ON p.product_id = oi.product_id
  JOIN 
    categories c ON p.category_id = c.category_id
  GROUP BY 
    p.product_id, p.product_name, c.category_name
), ranked_products AS (
  SELECT 
    product_id, 
    product_name, 
    category_name, 
    net_revenue,
    ROW_NUMBER() OVER (PARTITION BY category_name ORDER BY net_revenue DESC) as rank
  FROM 
    product_revenue
)
SELECT 
  product_id, 
  product_name, 
  category_name, 
  ROUND(net_revenue, 2) as net_revenue
FROM 
  ranked_products
WHERE 
  rank <= 3
ORDER BY 
  category_name, rank;
```

### q10 — İade nedenlerine göre toplam iade tutarını ve adet sayısını bul.
```sql
SELECT reason, ROUND(SUM(amount), 2) AS total_refund_amount, COUNT(refund_id) AS refund_count
FROM refunds
GROUP BY reason
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
> **Judge:** Agent's output matches the reference output in terms of order counts for each month, despite differences in date formatting. The values are identical, and the number of rows is the same.

```sql
SELECT EXTRACT(MONTH FROM order_date) AS month, COUNT(order_id) AS order_count
FROM orders
WHERE EXTRACT(YEAR FROM order_date) = 2024
GROUP BY month
ORDER BY month;
```

### q15 — En çok sipariş veren 10 müşteriyi bul.
> **Judge:** Agent's result includes different customers and counts compared to the reference result. Specifically, the agent's output has 'Customer 00557' and 'Customer 00466' which are not present in the agent's output, and the counts for some customers do not match.

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
FROM categories c
JOIN products p ON c.category_id = p.category_id
GROUP BY c.category_name
```

### q17 — Teslim edilen siparişlerde ortalama teslimat süresi kaç gündür?
> **Judge:** Agent sonucu 3.52 gün olarak hesaplamışken, referans sonuç 6.0 gün olarak verilmiş. Bu nedenle, değerler uyuşmuyor.

```sql
SELECT ROUND(AVG(CAST(delivered_at AS DATE) - CAST(shipped_at AS DATE)), 2) AS average_delivery_days
FROM orders
WHERE status = 'delivered' AND delivered_at IS NOT NULL AND shipped_at IS NOT NULL;
```

### q18 — Ödeme yöntemine göre başarılı ödeme (captured) oranını yüzde olarak bul.
> **Judge:** Agent'ın verdiği sonuçlar, referans sonuçlarla aynı satır sayısına ve aynı sayısal değerlere sahip. Kolon adları farklı olsa da, değerler doğru ve tutarlı.

```sql
SELECT payment_method, ROUND(COALESCE(SUM(CASE WHEN payment_status = 'captured' THEN 1 ELSE 0 END) * 100.0 / COUNT(payment_id), 0), 2) AS success_rate_percentage
FROM payments
GROUP BY payment_method;
```

### q19 — İptal edilen siparişlerin toplam ciro kaybı ne kadardır?
> **Judge:** Agent'ın döndürdüğü toplam ciro kaybı (273734.42) ile referans sonuçtaki kayıp ciro (874715.45) arasında büyük bir fark var. Bu nedenle, agent doğru bir yanıt vermemiştir.

```sql
SELECT ROUND(SUM(COALESCE(oi.unit_price * oi.quantity - oi.discount_amount, 0)), 2) AS total_revenue_loss
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.status = 'cancelled'
```

### q20 — Sipariş durumuna göre iade sayısı ve toplam iade tutarını bul.
> **Judge:** Agent sonucu, referans sonuçtaki 'total_refunded' kolonunu 'total_refund_amount' olarak adlandırmış. Bu nedenle kolon adları farklı olduğu için doğru kabul edilemez. Ayrıca, referans sonuçta sadece 'refunded' durumu var, ancak agent sonucu tüm durumları içeriyor. Bu da fazladan satır döndürdüğü anlamına geliyor.

```sql
SELECT o.status, COUNT(r.refund_id) AS refund_count, ROUND(SUM(COALESCE(r.amount, 0)), 2) AS total_refund_amount
FROM orders o
LEFT JOIN refunds r ON o.order_id = r.order_id
GROUP BY o.status
```

### q21 — Sipariş başına ortalama kalem sayısı ve ortalama sipariş tutarı nedir?
```sql
SELECT ROUND(AVG(item_count), 2) AS avg_items_per_order, ROUND(AVG(order_total), 2) AS avg_order_total
FROM (
  SELECT o.order_id, 
         COUNT(oi.order_item_id) AS item_count, 
         SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0)) AS order_total
  FROM orders o
  LEFT JOIN order_items oi ON o.order_id = oi.order_id
  GROUP BY o.order_id
) subquery;
```

### q22 — Aktif ürünlerde kar marjı oranı en yüksek 10 ürünü bul.
```sql
SELECT p.product_name, ROUND((p.unit_price - p.cost) / p.unit_price, 2) AS profit_margin
FROM products p
WHERE p.is_active = TRUE
ORDER BY profit_margin DESC
LIMIT 10;
```

### q23 — Müşteri segmentine ve yıla göre net ciro ve sipariş sayısı trendi.
> **Judge:** Agent returned 0 rows while the reference result contains 9 rows with specific data.

```sql
SELECT c.segment, EXTRACT(YEAR FROM o.order_date) AS year, ROUND(SUM(oi.quantity * (oi.unit_price - COALESCE(oi.discount_amount, 0))), 2) AS net_revenue, COUNT(DISTINCT o.order_id) AS order_count
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.status = 'completed'
GROUP BY c.segment, year
ORDER BY c.segment, year;
```

### q24 — Aylık sipariş sayısındaki bir önceki aya göre değişimi bul (LAG).
> **Judge:** Agent's output matches the reference output in terms of order counts and changes from the previous month, despite differences in column names and date formats.

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
    LAG(order_count) OVER (ORDER BY month) AS previous_month_count 
  FROM monthly_orders 
) 
SELECT 
  month, 
  order_count, 
  COALESCE(order_count - previous_month_count, 0) AS change_from_previous_month 
FROM monthly_change;
```

### q25 — İptal ve iade edilmemiş siparişlerde en yüksek yaşam boyu değere (CLV) sahip 10 müşteriyi bul.
> **Judge:** Agent's results do not match the reference results in terms of customer IDs and CLV values. The agent's CLV values are also significantly higher than those in the reference results.

```sql
WITH customer_revenue AS (
  SELECT 
    o.customer_id, 
    SUM(oi.quantity * (oi.unit_price - COALESCE(oi.discount_amount, 0))) AS total_revenue
  FROM 
    orders o
  JOIN 
    order_items oi ON o.order_id = oi.order_id
  LEFT JOIN 
    refunds r ON o.order_id = r.order_id
  WHERE 
    o.status != 'cancelled' AND r.order_id IS NULL
  GROUP BY 
    o.customer_id
)
SELECT 
  c.customer_id, 
  c.full_name, 
  ROUND(cr.total_revenue, 2) AS clv
FROM 
  customer_revenue cr
JOIN 
  customers c ON cr.customer_id = c.customer_id
ORDER BY 
  clv DESC
LIMIT 10;
```
