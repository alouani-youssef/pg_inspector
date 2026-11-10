-- =============================================================================
-- pg_inspector seed database
--
-- Loaded automatically by the postgres image on FIRST start of an empty volume
-- (mounted into /docker-entrypoint-initdb.d). To re-seed: `docker compose down -v`.
--
-- The data is fully DETERMINISTIC (no random()) so tests can assert exact
-- counts and values. If you change anything here, update the expectations
-- documented at the bottom of this file.
--
-- Covers the objects an inspector must understand:
--   the public schema, enums, domains, sequences/identity, PK / FK / UNIQUE / CHECK,
--   composite PKs, self-referencing FK, btree / partial / expression / GIN
--   indexes, jsonb, arrays, numeric, timestamptz, generated columns, views,
--   materialized views, functions, triggers, comments, and an empty table.
-- =============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- -----------------------------------------------------------------------------
-- Schema: everything lives in "public" (created by default in every database)
-- -----------------------------------------------------------------------------
COMMENT ON SCHEMA public IS 'pg_inspector seed: e-commerce tables, reporting views and audit log';

-- -----------------------------------------------------------------------------
-- Types
-- -----------------------------------------------------------------------------
CREATE TYPE public.order_status AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled');
COMMENT ON TYPE public.order_status IS 'Lifecycle of an order';

CREATE DOMAIN public.email AS text
    CHECK (VALUE ~* '^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$');

CREATE DOMAIN public.positive_money AS numeric(12, 2)
    CHECK (VALUE >= 0);

-- -----------------------------------------------------------------------------
-- public.countries  (natural text PK)
-- -----------------------------------------------------------------------------
CREATE TABLE public.countries (
    code char(2) PRIMARY KEY,
    name text    NOT NULL UNIQUE
);
COMMENT ON TABLE public.countries IS 'ISO-3166 alpha-2 countries';

INSERT INTO public.countries (code, name) VALUES
    ('MA', 'Morocco'),
    ('FR', 'France'),
    ('US', 'United States'),
    ('DE', 'Germany'),
    ('ES', 'Spain');

-- -----------------------------------------------------------------------------
-- public.customers
-- -----------------------------------------------------------------------------
CREATE TABLE public.customers (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email        public.email  NOT NULL,
    first_name   text        NOT NULL,
    last_name    text        NOT NULL,
    full_name    text GENERATED ALWAYS AS (first_name || ' ' || last_name) STORED,
    country_code char(2)     NOT NULL REFERENCES public.countries (code),
    is_active    boolean     NOT NULL DEFAULT true,
    preferences  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT customers_email_key UNIQUE (email)
);
COMMENT ON TABLE  public.customers IS 'Registered shoppers';
COMMENT ON COLUMN public.customers.email IS 'Login email, unique per customer';
COMMENT ON COLUMN public.customers.preferences IS 'Free-form UI/marketing preferences';

CREATE INDEX customers_country_idx       ON public.customers (country_code);
CREATE INDEX customers_lower_last_idx    ON public.customers (lower(last_name));
CREATE INDEX customers_preferences_gin   ON public.customers USING gin (preferences);

-- 200 customers. Every 10th is inactive. Country cycles MA, FR, US, DE, ES.
INSERT INTO public.customers (email, first_name, last_name, country_code, is_active, preferences, created_at)
SELECT
    format('customer%s@example.com', g),
    (ARRAY['Youssef','Sara','Liam','Emma','Omar','Lea','Noah','Mia','Adam','Ines'])[1 + (g % 10)],
    (ARRAY['Alaoui','Martin','Smith','Muller','Garcia','Bennani','Dubois','Johnson','Schmidt','Lopez'])[1 + ((g / 10) % 10)],
    (ARRAY['MA','FR','US','DE','ES'])[1 + (g % 5)],
    (g % 10) <> 0,
    jsonb_build_object(
        'newsletter', (g % 2 = 0),
        'theme',      CASE WHEN g % 3 = 0 THEN 'dark' ELSE 'light' END,
        'language',   (ARRAY['ar','fr','en','de','es'])[1 + (g % 5)]
    ),
    timestamptz '2025-01-01 00:00:00+00' + (g * interval '1 day')
FROM generate_series(1, 200) AS g;

-- -----------------------------------------------------------------------------
-- public.categories  (self-referencing FK -> tree)
-- -----------------------------------------------------------------------------
CREATE TABLE public.categories (
    id        integer PRIMARY KEY,
    parent_id integer REFERENCES public.categories (id) ON DELETE SET NULL,
    name      text    NOT NULL,
    slug      text    NOT NULL UNIQUE
);
COMMENT ON TABLE public.categories IS 'Hierarchical product categories';

INSERT INTO public.categories (id, parent_id, name, slug) VALUES
    (1, NULL, 'Electronics',  'electronics'),
    (2, 1,    'Laptops',      'laptops'),
    (3, 1,    'Phones',       'phones'),
    (4, 1,    'Accessories',  'accessories'),
    (5, NULL, 'Home',         'home'),
    (6, 5,    'Kitchen',      'kitchen'),
    (7, 5,    'Furniture',    'furniture'),
    (8, NULL, 'Books',        'books');

-- -----------------------------------------------------------------------------
-- public.products
-- -----------------------------------------------------------------------------
CREATE TABLE public.products (
    id          integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    sku         varchar(32)          NOT NULL UNIQUE,
    name        text                 NOT NULL,
    category_id integer              NOT NULL REFERENCES public.categories (id),
    price       public.positive_money  NOT NULL,
    stock       integer              NOT NULL DEFAULT 0 CHECK (stock >= 0),
    tags        text[]               NOT NULL DEFAULT '{}',
    attributes  jsonb,
    discontinued boolean             NOT NULL DEFAULT false,
    created_at  timestamptz          NOT NULL DEFAULT now(),
    updated_at  timestamptz          NOT NULL DEFAULT now()
);
COMMENT ON TABLE  public.products IS 'Sellable catalogue items';
COMMENT ON COLUMN public.products.price IS 'Unit price in EUR';

CREATE INDEX products_category_idx ON public.products (category_id);
CREATE INDEX products_tags_gin     ON public.products USING gin (tags);
CREATE INDEX products_active_idx   ON public.products (name) WHERE NOT discontinued;  -- partial

-- 50 products. Categories 2,3,4,6,7,8 (leaf categories). Every 7th discontinued.
INSERT INTO public.products (sku, name, category_id, price, stock, tags, attributes, discontinued, created_at, updated_at)
SELECT
    format('SKU-%s', lpad(g::text, 4, '0')),
    format('Product %s', g),
    (ARRAY[2,3,4,6,7,8])[1 + (g % 6)],
    (10 + (g * 7.5))::numeric(12,2),
    (g * 3) % 100,
    CASE g % 4
        WHEN 0 THEN ARRAY['sale','new']
        WHEN 1 THEN ARRAY['bestseller']
        WHEN 2 THEN ARRAY['new']
        ELSE        ARRAY[]::text[]
    END,
    CASE WHEN g % 5 = 0 THEN NULL
         ELSE jsonb_build_object('color', (ARRAY['black','white','red','blue'])[1 + (g % 4)], 'weight_g', g * 100)
    END,
    (g % 7 = 0),
    timestamptz '2024-06-01 00:00:00+00' + (g * interval '1 day'),
    timestamptz '2024-06-01 00:00:00+00' + (g * interval '1 day')
FROM generate_series(1, 50) AS g;

-- -----------------------------------------------------------------------------
-- public.orders
-- -----------------------------------------------------------------------------
CREATE TABLE public.orders (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id      bigint            NOT NULL REFERENCES public.customers (id) ON DELETE CASCADE,
    status           public.order_status NOT NULL DEFAULT 'pending',
    shipping_country char(2)           NOT NULL REFERENCES public.countries (code),
    placed_at        timestamptz       NOT NULL,
    shipped_at       timestamptz,
    notes            text,
    CONSTRAINT orders_shipped_after_placed CHECK (shipped_at IS NULL OR shipped_at >= placed_at)
);
COMMENT ON TABLE public.orders IS 'Customer orders (header)';

CREATE INDEX orders_customer_idx       ON public.orders (customer_id);
CREATE INDEX orders_placed_at_idx      ON public.orders (placed_at);
CREATE INDEX orders_open_idx           ON public.orders (placed_at) WHERE status IN ('pending', 'paid'); -- partial

-- 2000 orders, enough for EXPLAIN to prefer indexes.
-- status cycles: pending, paid, shipped, delivered, cancelled (400 each).
INSERT INTO public.orders (customer_id, status, shipping_country, placed_at, shipped_at, notes)
SELECT
    1 + (g % 200),
    (ARRAY['pending','paid','shipped','delivered','cancelled']::public.order_status[])[1 + (g % 5)],
    (ARRAY['MA','FR','US','DE','ES'])[1 + ((g + 1) % 5)],
    timestamptz '2025-01-01 00:00:00+00' + (g * interval '4 hours'),
    CASE WHEN g % 5 IN (2, 3)
         THEN timestamptz '2025-01-01 00:00:00+00' + (g * interval '4 hours') + interval '2 days'
    END,
    CASE WHEN g % 50 = 0 THEN 'Gift wrap please' END
FROM generate_series(1, 2000) AS g;

-- -----------------------------------------------------------------------------
-- public.order_items  (composite PK, two FKs)
-- -----------------------------------------------------------------------------
CREATE TABLE public.order_items (
    order_id   bigint              NOT NULL REFERENCES public.orders (id) ON DELETE CASCADE,
    line_no    smallint            NOT NULL,
    product_id integer             NOT NULL REFERENCES public.products (id),
    quantity   integer             NOT NULL CHECK (quantity > 0),
    unit_price public.positive_money NOT NULL,
    line_total numeric(12, 2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    PRIMARY KEY (order_id, line_no)
);
COMMENT ON TABLE public.order_items IS 'Order lines';

CREATE INDEX order_items_product_idx ON public.order_items (product_id);

-- Every order has 1..3 lines (order_id % 3 + 1) => 4001 lines total.
INSERT INTO public.order_items (order_id, line_no, product_id, quantity, unit_price)
SELECT
    o.id,
    l.line_no,
    p.id,
    1 + ((o.id + l.line_no) % 4),
    p.price
FROM public.orders o
CROSS JOIN LATERAL generate_series(1, (o.id % 3)::int + 1) AS l(line_no)
JOIN public.products p ON p.id = 1 + ((o.id * 7 + l.line_no) % 50);

-- -----------------------------------------------------------------------------
-- public.payments  (1:1 with non-pending orders)
-- -----------------------------------------------------------------------------
CREATE TABLE public.payments (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id  bigint              NOT NULL UNIQUE REFERENCES public.orders (id) ON DELETE CASCADE,
    amount    public.positive_money NOT NULL,
    method    text                NOT NULL CHECK (method IN ('card', 'paypal', 'bank_transfer')),
    paid_at   timestamptz         NOT NULL
);
COMMENT ON TABLE public.payments IS 'One payment per paid order';

INSERT INTO public.payments (id, order_id, amount, method, paid_at)
SELECT
    -- deterministic uuid derived from the order id
    md5('payment-' || o.id)::uuid,
    o.id,
    (SELECT sum(oi.line_total) FROM public.order_items oi WHERE oi.order_id = o.id),
    (ARRAY['card','paypal','bank_transfer'])[1 + (o.id % 3)],
    o.placed_at + interval '1 hour'
FROM public.orders o
WHERE o.status IN ('paid', 'shipped', 'delivered');

-- -----------------------------------------------------------------------------
-- public.reviews
-- -----------------------------------------------------------------------------
CREATE TABLE public.reviews (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id  integer  NOT NULL REFERENCES public.products (id) ON DELETE CASCADE,
    customer_id bigint   NOT NULL REFERENCES public.customers (id) ON DELETE CASCADE,
    rating      smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body        text,
    created_at  timestamptz NOT NULL,
    UNIQUE (product_id, customer_id)
);

-- 300 reviews, rating cycles 1..5.
INSERT INTO public.reviews (product_id, customer_id, rating, body, created_at)
SELECT
    1 + ((g + (g / 200) * 7) % 50),
    1 + (g % 200),
    1 + (g % 5)::smallint,
    CASE WHEN g % 4 = 0 THEN NULL ELSE format('Review number %s', g) END,
    timestamptz '2025-03-01 00:00:00+00' + (g * interval '6 hours')
FROM generate_series(1, 300) AS g;

-- -----------------------------------------------------------------------------
-- public.coupons  (intentionally EMPTY table: inspectors must handle 0 rows)
-- -----------------------------------------------------------------------------
CREATE TABLE public.coupons (
    code        text PRIMARY KEY,
    percent_off smallint NOT NULL CHECK (percent_off BETWEEN 1 AND 100),
    valid_until date
);
COMMENT ON TABLE public.coupons IS 'Intentionally empty in the seed data';

-- -----------------------------------------------------------------------------
-- public.product_price_changes + trigger
-- -----------------------------------------------------------------------------
CREATE TABLE public.product_price_changes (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id integer        NOT NULL,
    old_price  numeric(12, 2) NOT NULL,
    new_price  numeric(12, 2) NOT NULL,
    changed_at timestamptz    NOT NULL DEFAULT now()
);

CREATE FUNCTION public.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
COMMENT ON FUNCTION public.touch_updated_at() IS 'Sets updated_at on every UPDATE';

CREATE FUNCTION public.log_price_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.price IS DISTINCT FROM OLD.price THEN
        INSERT INTO public.product_price_changes (product_id, old_price, new_price)
        VALUES (OLD.id, OLD.price, NEW.price);
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER products_touch_updated_at
    BEFORE UPDATE ON public.products
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

CREATE TRIGGER products_price_audit
    AFTER UPDATE OF price ON public.products
    FOR EACH ROW EXECUTE FUNCTION public.log_price_change();

-- -----------------------------------------------------------------------------
-- Functions
-- -----------------------------------------------------------------------------
CREATE FUNCTION public.order_total(p_order_id bigint) RETURNS numeric
LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(line_total), 0) FROM public.order_items WHERE order_id = p_order_id;
$$;
COMMENT ON FUNCTION public.order_total(bigint) IS 'Sum of line totals for an order';

-- -----------------------------------------------------------------------------
-- Views
-- -----------------------------------------------------------------------------
CREATE VIEW public.active_products AS
SELECT p.id, p.sku, p.name, c.name AS category, p.price, p.stock
FROM public.products p
JOIN public.categories c ON c.id = p.category_id
WHERE NOT p.discontinued;
COMMENT ON VIEW public.active_products IS 'Products still for sale';

CREATE VIEW public.customer_order_summary AS
SELECT
    c.id            AS customer_id,
    c.full_name,
    c.country_code,
    count(o.id)     AS order_count,
    max(o.placed_at) AS last_order_at
FROM public.customers c
LEFT JOIN public.orders o ON o.customer_id = c.id
GROUP BY c.id, c.full_name, c.country_code;

CREATE MATERIALIZED VIEW public.monthly_revenue AS
SELECT
    date_trunc('month', p.paid_at) AS month,
    count(*)                       AS payments,
    sum(p.amount)                  AS revenue
FROM public.payments p
GROUP BY 1
ORDER BY 1;
CREATE UNIQUE INDEX monthly_revenue_month_idx ON public.monthly_revenue (month);
COMMENT ON MATERIALIZED VIEW public.monthly_revenue IS 'Revenue per calendar month (refresh manually)';

COMMIT;

-- Fresh planner statistics so EXPLAIN output is realistic right after boot.
ANALYZE;

-- =============================================================================
-- Expected values for tests
-- -----------------------------------------------------------------------------
--   schemas (user)                 : public (all objects)
--   public.countries                 : 5
--   public.customers                 : 200   (180 active, 20 inactive)
--   public.categories                : 8     (3 roots)
--   public.products                  : 50    (7 discontinued -> 43 in public.active_products)
--   public.orders                    : 2000  (400 per status)
--   public.order_items               : 4001
--   public.payments                  : 1200  (paid + shipped + delivered)
--   public.reviews                   : 300
--   public.coupons                   : 0
--   public.product_price_changes    : 0
--   public.customer_order_summary : 200 rows, 10 orders per customer
--   views                          : public.active_products, public.customer_order_summary
--   materialized views             : public.monthly_revenue (11 rows)
--   enum public.order_status         : pending, paid, shipped, delivered, cancelled
-- =============================================================================
