CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    session_version INTEGER NOT NULL DEFAULT 0,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    remember_token_hash TEXT,
    remember_token_expires_at TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    group_name TEXT NOT NULL DEFAULT '默认',
    UNIQUE(user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL REFERENCES users(id),
    setting_key TEXT NOT NULL,
    setting_value TEXT,
    PRIMARY KEY (user_id, setting_key)
);

CREATE TABLE IF NOT EXISTS stock_scores (
    id SERIAL PRIMARY KEY,
    stock_code TEXT,
    score REAL,
    level TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS score_validation (
    score_id INTEGER PRIMARY KEY,
    stock_code TEXT NOT NULL,
    score REAL NOT NULL,
    level TEXT NOT NULL,
    score_date TEXT NOT NULL,
    base_close REAL,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_10d REAL,
    evaluated_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_candidates (
    id SERIAL PRIMARY KEY,
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    score REAL NOT NULL,
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'rule',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(trade_date, stock_code)
);

CREATE TABLE IF NOT EXISTS dynamic_pool (
    id SERIAL PRIMARY KEY,
    ts_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close REAL,
    pct_chg REAL,
    amount REAL,
    volume_ratio REAL,
    ma20 REAL,
    ai_score REAL,
    ai_grade TEXT,
    filter_pass_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(trade_date, ts_code)
);

CREATE INDEX IF NOT EXISTS idx_dynamic_pool_trade_date_score
ON dynamic_pool(trade_date, ai_score DESC);

CREATE TABLE IF NOT EXISTS dynamic_pool_scan_runs (
    id SERIAL PRIMARY KEY,
    trade_date TEXT NOT NULL UNIQUE,
    scanned_count INTEGER NOT NULL DEFAULT 0,
    static_pass_count INTEGER NOT NULL DEFAULT 0,
    daily_pass_count INTEGER NOT NULL DEFAULT 0,
    scored_count INTEGER NOT NULL DEFAULT 0,
    selected_count INTEGER NOT NULL DEFAULT 0,
    max_ai_score REAL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_news_sentiment (
    id SERIAL PRIMARY KEY,
    stock_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    news_count INTEGER NOT NULL DEFAULT 0,
    sentiment TEXT NOT NULL,
    sentiment_score REAL NOT NULL DEFAULT 0,
    news_titles TEXT,
    analysis_reason TEXT,
    created_at TEXT NOT NULL,
    macro_sentiment_cn REAL NOT NULL DEFAULT 0,
    macro_sentiment_global REAL NOT NULL DEFAULT 0,
    industry_sentiment REAL NOT NULL DEFAULT 0,
    final_sentiment_score REAL NOT NULL DEFAULT 0,
    score_breakdown TEXT,
    UNIQUE(stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS news_raw (
    id SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    stock_code TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    published_time TEXT NOT NULL,
    url TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(title, published_time)
);

CREATE INDEX IF NOT EXISTS idx_news_raw_type_time
ON news_raw(source_type, published_time);

CREATE INDEX IF NOT EXISTS idx_news_raw_stock_time
ON news_raw(stock_code, published_time);

CREATE TABLE IF NOT EXISTS news_fetch_state (
    id SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    UNIQUE(source_type, cache_key)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    stock_code TEXT NOT NULL,
    params TEXT,
    metrics TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_results_user_created
ON backtest_results(user_id, created_at DESC);
