-- Cursuri valutare din API-ul Frankfurter (Etapa 4b).
-- fx_rates_raw: răspunsul JSON brut, câte un rând per fereastră de cerere.
-- fx_rates: cursurile aplatizate, o valoare per (zi, bază, monedă cotată).

CREATE TABLE IF NOT EXISTS raw.fx_rates_raw (
    id bigserial PRIMARY KEY,
    request_url text NOT NULL,
    window_start date NOT NULL,
    window_end date NOT NULL,
    payload jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (window_start, window_end, request_url)
);

CREATE TABLE IF NOT EXISTS raw.fx_rates (
    rate_date date NOT NULL,
    base char(3) NOT NULL,
    quote char(3) NOT NULL,
    rate numeric(18, 8) NOT NULL,
    _loaded_at timestamptz NOT NULL DEFAULT now(),
    _source_file text,
    PRIMARY KEY (rate_date, base, quote)
);
