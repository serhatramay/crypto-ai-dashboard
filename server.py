#!/usr/bin/env python3
"""
AI Crypto Trading Dashboard Server
- Real-time prices via Binance REST API
- AI-powered automated trading with LLM
- Paper trading with virtual balance
"""

import json
import math
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib import request
import socketserver
import urllib.parse

# ============================================================
# DATABASE - PostgreSQL (Neon) with SQLite fallback
# ============================================================

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

def get_conn():
    """Veritabanı bağlantısı al"""
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        return conn
    else:
        import sqlite3
        conn = sqlite3.connect(str(Path(__file__).parent / "trading.db"))
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    if DATABASE_URL:
        cur.execute("""CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            time TEXT, date TEXT, symbol TEXT, side TEXT,
            entry DOUBLE PRECISION, exit_price DOUBLE PRECISION,
            pnl DOUBLE PRECISION, leverage INTEGER DEFAULT 1, model TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, entry DOUBLE PRECISION, size DOUBLE PRECISION,
            leverage INTEGER, margin DOUBLE PRECISION, open_time TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY, value DOUBLE PRECISION
        )""")
        print("[DB] PostgreSQL (Neon) initialized")
    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT, date TEXT, symbol TEXT, side TEXT,
            entry REAL, exit_price REAL, pnl REAL, leverage INTEGER DEFAULT 1, model TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, side TEXT, entry REAL, size REAL,
            leverage INTEGER, margin REAL, open_time TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY, value REAL
        )""")
        conn.commit()
        print("[DB] SQLite initialized (local mode)")
    cur.close()
    conn.close()

def db_save_trade(trade):
    try:
        conn = get_conn()
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute(
                "INSERT INTO trades (time, date, symbol, side, entry, exit_price, pnl, leverage, model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (trade['time'], trade.get('date', datetime.now(TR_TZ).strftime("%Y-%m-%d")),
                 trade['symbol'], trade['side'], trade['entry'], trade['exit'], trade['pnl'],
                 trade.get('leverage', 1), trade['model'])
            )
        else:
            cur.execute(
                "INSERT INTO trades (time, date, symbol, side, entry, exit_price, pnl, leverage, model) VALUES (?,?,?,?,?,?,?,?,?)",
                (trade['time'], trade.get('date', datetime.now(TR_TZ).strftime("%Y-%m-%d")),
                 trade['symbol'], trade['side'], trade['entry'], trade['exit'], trade['pnl'],
                 trade.get('leverage', 1), trade['model'])
            )
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Error saving trade: {e}")

def db_save_position(pos):
    try:
        conn = get_conn()
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute(
                "INSERT INTO positions (id, symbol, side, entry, size, leverage, margin, open_time) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (pos['id'], pos['symbol'], pos['side'], pos['entry'], pos['size'], pos['leverage'], pos['margin'], pos['open_time'])
            )
        else:
            cur.execute(
                "INSERT INTO positions (id, symbol, side, entry, size, leverage, margin, open_time) VALUES (?,?,?,?,?,?,?,?)",
                (pos['id'], pos['symbol'], pos['side'], pos['entry'], pos['size'], pos['leverage'], pos['margin'], pos['open_time'])
            )
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Error saving position: {e}")

def db_remove_position(pos_id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute("DELETE FROM positions WHERE id = %s", (pos_id,))
        else:
            cur.execute("DELETE FROM positions WHERE id = ?", (pos_id,))
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Error removing position: {e}")

def db_save_balance(balance):
    try:
        conn = get_conn()
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute("INSERT INTO state (key, value) VALUES ('balance', %s) ON CONFLICT (key) DO UPDATE SET value = %s", (balance, balance))
        else:
            cur.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('balance', ?)", (balance,))
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Error saving balance: {e}")

def db_reset():
    """Tüm verileri sıfırla - temiz başlangıç"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM trades")
        cur.execute("DELETE FROM positions")
        cur.execute("DELETE FROM state")
        if not DATABASE_URL:
            conn.commit()
        cur.close()
        conn.close()
        print("[DB] Database reset complete")
    except Exception as e:
        print(f"[DB] Error resetting: {e}")

def db_load_all():
    """Sunucu başlangıcında tüm verileri yükle"""
    conn = get_conn()
    cur = conn.cursor()

    trades = []
    if DATABASE_URL:
        cur.execute("SELECT time, date, symbol, side, entry, exit_price, pnl, leverage, model FROM trades ORDER BY id DESC")
        for row in cur.fetchall():
            trades.append({
                "time": row[0], "date": row[1], "symbol": row[2],
                "side": row[3], "entry": row[4], "exit": row[5],
                "pnl": row[6], "leverage": row[7] or 1, "model": row[8]
            })
    else:
        for row in conn.execute("SELECT * FROM trades ORDER BY id DESC"):
            trades.append({
                "time": row['time'], "date": row['date'], "symbol": row['symbol'],
                "side": row['side'], "entry": row['entry'], "exit": row['exit_price'],
                "pnl": row['pnl'], "leverage": row['leverage'] if 'leverage' in row.keys() else 1,
                "model": row['model']
            })

    positions = []
    if DATABASE_URL:
        cur.execute("SELECT id, symbol, side, entry, size, leverage, margin, open_time FROM positions")
        for row in cur.fetchall():
            positions.append({
                "id": row[0], "symbol": row[1], "side": row[2],
                "entry": row[3], "current_price": row[3], "size": row[4],
                "leverage": row[5], "margin": row[6], "pnl": 0.0,
                "open_time": row[7]
            })
    else:
        for row in conn.execute("SELECT * FROM positions"):
            positions.append({
                "id": row['id'], "symbol": row['symbol'], "side": row['side'],
                "entry": row['entry'], "current_price": row['entry'], "size": row['size'],
                "leverage": row['leverage'], "margin": row['margin'], "pnl": 0.0,
                "open_time": row['open_time']
            })

    balance = None
    if DATABASE_URL:
        cur.execute("SELECT value FROM state WHERE key = 'balance'")
        balance_row = cur.fetchone()
        if balance_row:
            balance = balance_row[0]
    else:
        balance_row = conn.execute("SELECT value FROM state WHERE key = 'balance'").fetchone()
        if balance_row:
            balance = balance_row['value']

    cur.close()
    conn.close()
    return trades, positions, balance

# Turkey timezone (UTC+3)
TR_TZ = timezone(timedelta(hours=3))

# ============================================================
# TELEGRAM BİLDİRİM
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8626429617:AAEhSi3CbMPAo1kryWn7uv-SIp40muSO_-k")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1176599927")

def send_telegram(message):
    """Telegram bildirim gönder"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }).encode()
        req = request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[Telegram] Error: {e}")

# AI Trading Configuration
AI_CONFIG = {
    "max_positions": 10,
    "max_same_direction": 5,  # Aynı yönde max 5 pozisyon
    "trade_amount": 300,
    "check_interval": 60,  # 60 saniye (analiz aralığı)
    "stop_loss_pct": 5,
    "take_profit_pct": 25,
    "daily_loss_limit_pct": 10,  # Günlük max kayıp: bakiyenin %10'u
    "min_volume_ratio": 0.5,  # Hacim filtresi daha gevşek
    "candle_period": 300,  # 5 dakikalık mumlar (saniye)
    "min_hold_time": 1800,  # Minimum tutma süresi: 30 dakika (saniye)
    "cooldown": 300,  # Cooldown: 5 dakika (saniye)
    "min_candles": 20,  # Minimum mum sayısı (analiz için)
}

# ============================================================
# PROFESYONEL TEKNİK ANALİZ MOTORU
# ============================================================

class TechnicalAnalyzer:
    """Tüm teknik gösterge hesaplamaları"""

    @staticmethod
    def ema_series(prices, period):
        """EMA serisi hesapla"""
        if len(prices) < period:
            return []
        k = 2 / (period + 1)
        ema = [sum(prices[:period]) / period]
        for p in prices[period:]:
            ema.append(p * k + ema[-1] * (1 - k))
        return ema

    @staticmethod
    def sma(prices, period):
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    @staticmethod
    def rsi(prices, period=14):
        """RSI - Relative Strength Index"""
        if len(prices) < period + 1:
            return 50
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = deltas[-period:]
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0.0001
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(prices, fast=12, slow=26, signal_period=9):
        """MACD - Moving Average Convergence Divergence"""
        if len(prices) < slow + signal_period:
            return 0, 0, 0
        ema_f = TechnicalAnalyzer.ema_series(prices, fast)
        ema_s = TechnicalAnalyzer.ema_series(prices, slow)
        # EMA serilerini hizala
        offset = len(ema_f) - len(ema_s)
        ema_f = ema_f[offset:]
        macd_line = [f - s for f, s in zip(ema_f, ema_s)]
        if len(macd_line) < signal_period:
            return macd_line[-1] if macd_line else 0, 0, 0
        signal_ema = TechnicalAnalyzer.ema_series(macd_line, signal_period)
        offset2 = len(macd_line) - len(signal_ema)
        histogram = macd_line[-1] - signal_ema[-1] if signal_ema else 0
        return macd_line[-1], signal_ema[-1] if signal_ema else 0, histogram

    @staticmethod
    def bollinger_bands(prices, period=20, num_std=2):
        """Bollinger Bands"""
        if len(prices) < period:
            return None, None, None
        sma = sum(prices[-period:]) / period
        variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
        std = math.sqrt(variance)
        return sma + num_std * std, sma, sma - num_std * std

    @staticmethod
    def ema_crossover(prices, short=9, long=21):
        """EMA Crossover - Golden/Death Cross"""
        if len(prices) < long + 2:
            return "neutral"
        ema_s_now = TechnicalAnalyzer.ema_series(prices, short)
        ema_l_now = TechnicalAnalyzer.ema_series(prices, long)
        ema_s_prev = TechnicalAnalyzer.ema_series(prices[:-1], short)
        ema_l_prev = TechnicalAnalyzer.ema_series(prices[:-1], long)
        if not all([ema_s_now, ema_l_now, ema_s_prev, ema_l_prev]):
            return "neutral"
        s_now, l_now = ema_s_now[-1], ema_l_now[-1]
        s_prev, l_prev = ema_s_prev[-1], ema_l_prev[-1]
        if s_prev < l_prev and s_now > l_now:
            return "golden_cross"
        elif s_prev > l_prev and s_now < l_now:
            return "death_cross"
        elif s_now > l_now:
            return "bullish"
        else:
            return "bearish"

    @staticmethod
    def support_resistance(prices, lookback=50):
        """Destek ve direnç seviyeleri"""
        if len(prices) < lookback:
            return [], []
        recent = prices[-lookback:]
        supports, resistances = [], []
        for i in range(2, len(recent) - 2):
            if recent[i] <= min(recent[i - 1], recent[i - 2], recent[i + 1], recent[i + 2]):
                supports.append(recent[i])
            if recent[i] >= max(recent[i - 1], recent[i - 2], recent[i + 1], recent[i + 2]):
                resistances.append(recent[i])
        return sorted(supports)[-3:] if supports else [], sorted(resistances)[:3] if resistances else []

    @staticmethod
    def atr(prices, period=14):
        """ATR - Average True Range (volatilite ölçümü)"""
        if len(prices) < period + 1:
            return None
        true_ranges = []
        for i in range(1, len(prices)):
            high_low = abs(prices[i] - prices[i-1])  # Basitleştirilmiş (close-to-close)
            true_ranges.append(high_low)
        if len(true_ranges) < period:
            return None
        return sum(true_ranges[-period:]) / period

    @staticmethod
    def fair_value_gaps(candles_ohlc, current_price, lookback=30):
        """FVG (Fair Value Gap) tespiti - Adil Fiyat Boşluğu
        3 ardışık mumdan oluşur:
        - Bullish FVG: Mum1 high < Mum3 low (yukarı boşluk)
        - Bearish FVG: Mum1 low > Mum3 high (aşağı boşluk)
        Fiyat genellikle bu boşlukları doldurmak için geri döner.
        """
        if len(candles_ohlc) < 3 or current_price <= 0:
            return [], 0

        fvgs = []
        start = max(0, len(candles_ohlc) - lookback)
        for i in range(start + 2, len(candles_ohlc)):
            c1 = candles_ohlc[i - 2]
            c3 = candles_ohlc[i]

            # Bullish FVG: Mum1'in high'ı < Mum3'ün low'u
            if c1['high'] < c3['low']:
                gap_top = c3['low']
                gap_bottom = c1['high']
                gap_pct = (gap_top - gap_bottom) / current_price * 100
                if gap_pct >= 0.05:  # Çok küçük boşlukları atla
                    age = len(candles_ohlc) - 1 - i
                    fvgs.append({
                        'type': 'bullish',
                        'top': gap_top,
                        'bottom': gap_bottom,
                        'mid': (gap_top + gap_bottom) / 2,
                        'gap_pct': gap_pct,
                        'age': age,
                        'filled': current_price <= gap_top and current_price >= gap_bottom
                    })

            # Bearish FVG: Mum1'in low'u > Mum3'ün high'ı
            if c1['low'] > c3['high']:
                gap_top = c1['low']
                gap_bottom = c3['high']
                gap_pct = (gap_top - gap_bottom) / current_price * 100
                if gap_pct >= 0.05:
                    age = len(candles_ohlc) - 1 - i
                    fvgs.append({
                        'type': 'bearish',
                        'top': gap_top,
                        'bottom': gap_bottom,
                        'mid': (gap_top + gap_bottom) / 2,
                        'gap_pct': gap_pct,
                        'age': age,
                        'filled': current_price >= gap_bottom and current_price <= gap_top
                    })

        # FVG skoru hesapla
        fvg_score = 0
        for fvg in fvgs:
            age_decay = max(0.3, 1 - fvg['age'] * 0.04)  # Yeni FVG'ler daha önemli
            gap_strength = min(2.0, fvg['gap_pct'] / 0.2)  # Büyük boşluk = güçlü sinyal

            if fvg['type'] == 'bullish':
                # Fiyat bullish FVG'nin içinde = güçlü alım (boşluk dolduruluyor)
                if fvg['filled']:
                    fvg_score = max(fvg_score, 85 * age_decay * gap_strength)
                # Fiyat boşluğa yakın (üstten %0.5 mesafede)
                elif current_price <= fvg['top'] * 1.005 and current_price > fvg['top']:
                    fvg_score = max(fvg_score, 50 * age_decay * gap_strength)
                # Fiyat boşluğun altında (doldurdu ve geçti - tamamlanmış)
                elif current_price < fvg['bottom']:
                    pass  # Boşluk aşıldı, sinyal yok

            elif fvg['type'] == 'bearish':
                if fvg['filled']:
                    fvg_score = min(fvg_score, -85 * age_decay * gap_strength)
                elif current_price >= fvg['bottom'] * 0.995 and current_price < fvg['bottom']:
                    fvg_score = min(fvg_score, -50 * age_decay * gap_strength)
                elif current_price > fvg['top']:
                    pass

        fvg_score = max(-100, min(100, fvg_score))
        return fvgs, fvg_score

    @staticmethod
    def volume_signal(volumes, period=20):
        """Hacim analizi"""
        if len(volumes) < period or not volumes[-1]:
            return "normal", 1.0
        avg = sum(volumes[-period:]) / period
        ratio = volumes[-1] / avg if avg > 0 else 1
        if ratio > 2:
            return "very_high", ratio
        elif ratio > 1.3:
            return "high", ratio
        elif ratio < 0.5:
            return "very_low", ratio
        elif ratio < 0.7:
            return "low", ratio
        return "normal", ratio

    @staticmethod
    def divergence(prices, period=14):
        """RSI Divergence tespiti
        Bullish Divergence: Fiyat düşük dip, RSI yüksek dip → yakında yükseliş
        Bearish Divergence: Fiyat yüksek zirve, RSI düşük zirve → yakında düşüş
        """
        if len(prices) < period * 3:
            return 0, "none"

        # Son N periyotta RSI hesapla
        window = min(len(prices), period * 4)
        recent_prices = prices[-window:]
        rsi_values = []
        for i in range(period + 1, len(recent_prices) + 1):
            rsi_values.append(TechnicalAnalyzer.rsi(recent_prices[:i], period))

        if len(rsi_values) < period * 2:
            return 0, "none"

        # Son iki dip ve zirveyi bul
        price_seg = recent_prices[-(len(rsi_values)):]
        half = len(price_seg) // 2

        # Dip noktaları (bullish divergence için)
        price_low1 = min(price_seg[:half])
        price_low2 = min(price_seg[half:])
        rsi_low1 = min(rsi_values[:half])
        rsi_low2 = min(rsi_values[half:])

        # Zirve noktaları (bearish divergence için)
        price_high1 = max(price_seg[:half])
        price_high2 = max(price_seg[half:])
        rsi_high1 = max(rsi_values[:half])
        rsi_high2 = max(rsi_values[half:])

        # Bullish Divergence: Fiyat daha düşük dip yapmış ama RSI daha yüksek dip yapmış
        if price_low2 < price_low1 and rsi_low2 > rsi_low1 + 3:
            strength = min(90, (rsi_low2 - rsi_low1) * 5)
            return strength, "bullish"

        # Bearish Divergence: Fiyat daha yüksek zirve yapmış ama RSI daha düşük zirve yapmış
        if price_high2 > price_high1 and rsi_high2 < rsi_high1 - 3:
            strength = min(90, (rsi_high1 - rsi_high2) * 5)
            return -strength, "bearish"

        return 0, "none"

    @staticmethod
    def order_blocks(candles_ohlc, current_price, lookback=30):
        """Order Block tespiti - Kurumsal pozisyon bölgeleri
        Güçlü bir hareketin öncesindeki son ters mum = order block
        Bullish OB: Düşüş sonrası son kırmızı mum (ardından güçlü yükseliş)
        Bearish OB: Yükseliş sonrası son yeşil mum (ardından güçlü düşüş)
        """
        if len(candles_ohlc) < 4 or current_price <= 0:
            return 0

        score = 0
        start = max(0, len(candles_ohlc) - lookback)

        for i in range(start + 1, len(candles_ohlc) - 2):
            c_prev = candles_ohlc[i]
            c_impulse = candles_ohlc[i + 1]
            c_confirm = candles_ohlc[i + 2]

            impulse_size = abs(c_impulse['close'] - c_impulse['open'])
            avg_size = sum(abs(c['close'] - c['open']) for c in candles_ohlc[max(0, i-5):i+1]) / min(6, i+1)

            if avg_size == 0:
                continue

            # Güçlü hareket mi? (ortalama mum boyutunun 2 katından büyük)
            if impulse_size < avg_size * 2:
                continue

            age = len(candles_ohlc) - 1 - i
            age_decay = max(0.3, 1 - age * 0.04)

            # Bullish Order Block: Kırmızı mum + ardından güçlü yeşil hareket
            if c_prev['close'] < c_prev['open'] and c_impulse['close'] > c_impulse['open']:
                ob_top = c_prev['open']
                ob_bottom = c_prev['low']
                # Fiyat OB bölgesinde mi?
                if current_price >= ob_bottom * 0.998 and current_price <= ob_top * 1.002:
                    score = max(score, 75 * age_decay)
                elif current_price >= ob_bottom * 0.995 and current_price < ob_bottom:
                    score = max(score, 40 * age_decay)

            # Bearish Order Block: Yeşil mum + ardından güçlü kırmızı hareket
            elif c_prev['close'] > c_prev['open'] and c_impulse['close'] < c_impulse['open']:
                ob_top = c_prev['high']
                ob_bottom = c_prev['close']
                if current_price >= ob_bottom * 0.998 and current_price <= ob_top * 1.002:
                    score = min(score, -75 * age_decay)
                elif current_price <= ob_top * 1.005 and current_price > ob_top:
                    score = min(score, -40 * age_decay)

        return max(-100, min(100, score))

    @staticmethod
    def liquidity_sweep(candles_ohlc, current_price, lookback=40):
        """Liquidity Sweep tespiti - Stop Hunt / Likidite Avı
        Fiyat önceki bir tepe/dibi kısa süreliğine kırar, sonra geri döner.
        Büyük oyuncuların stop-loss'ları tetikleyip likidite topladığı an.
        """
        if len(candles_ohlc) < 10 or current_price <= 0:
            return 0

        score = 0
        start = max(0, len(candles_ohlc) - lookback)
        recent = candles_ohlc[start:]

        # Son birkaç mumdaki swing high/low'ları bul
        swing_highs = []
        swing_lows = []
        for i in range(2, len(recent) - 2):
            if recent[i]['high'] >= max(recent[i-1]['high'], recent[i-2]['high'], recent[i+1]['high'], recent[i+2]['high']):
                swing_highs.append({'price': recent[i]['high'], 'idx': i})
            if recent[i]['low'] <= min(recent[i-1]['low'], recent[i-2]['low'], recent[i+1]['low'], recent[i+2]['low']):
                swing_lows.append({'price': recent[i]['low'], 'idx': i})

        if not swing_highs and not swing_lows:
            return 0

        # Son 3 muma bak - sweep oldu mu?
        last_candles = recent[-3:] if len(recent) >= 3 else recent
        last_high = max(c['high'] for c in last_candles)
        last_low = min(c['low'] for c in last_candles)
        last_close = recent[-1]['close']

        # Bullish Sweep: Fiyat swing low'un altına düştü ama geri kapandı
        for sl in swing_lows:
            if sl['idx'] >= len(recent) - 3:
                continue  # Son mumların kendisi değil, önceki swing'ler
            if last_low < sl['price'] and last_close > sl['price']:
                # Sweep oldu ve geri döndü → bullish
                sweep_depth = (sl['price'] - last_low) / current_price * 100
                if sweep_depth > 0.05:  # En az %0.05 sweep
                    score = max(score, min(85, sweep_depth * 100))

        # Bearish Sweep: Fiyat swing high'ın üstüne çıktı ama geri kapandı
        for sh in swing_highs:
            if sh['idx'] >= len(recent) - 3:
                continue
            if last_high > sh['price'] and last_close < sh['price']:
                sweep_depth = (last_high - sh['price']) / current_price * 100
                if sweep_depth > 0.05:
                    score = min(score, max(-85, -sweep_depth * 100))

        return max(-100, min(100, score))


class AITradingBot:
    """Profesyonel seviye AI trading bot - çoklu gösterge analizi"""
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "POLUSDT"]
    SYMBOL_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "XRPUSDT": "XRP", "DOGEUSDT": "DOGE", "ADAUSDT": "ADA", "AVAXUSDT": "AVAX", "LINKUSDT": "LINK", "DOTUSDT": "DOT", "POLUSDT": "POL"}
    COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple", "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche-2", "LINK": "chainlink", "DOT": "polkadot", "POL": "polygon-ecosystem-token"}

    def __init__(self, state):
        self.state = state
        self.running = False
        self.thread = None
        self.last_analysis = {}
        # Mum verileri (OHLC + close listesi)
        self.candle_closes = {s: [] for s in self.SYMBOLS}
        self.candle_volumes = {s: [] for s in self.SYMBOLS}
        self.candles_ohlc = {s: [] for s in self.SYMBOLS}  # [{open, high, low, close}, ...]
        self.current_candle = {s: None for s in self.SYMBOLS}  # Oluşmakta olan mum
        # Kısa vadeli fiyat (5sn aralıklarla)
        self.price_history = {s: [] for s in self.SYMBOLS}
        # Fear & Greed Index
        self.fear_greed = {"value": 50, "label": "Neutral"}
        self.data_ready = False
        # Soğuma süresi: coin başına son kapanış zamanı
        self.cooldown_until = {s: 0 for s in self.SYMBOLS}
        # Trailing stop: pozisyon ID → en yüksek kâr %
        self.peak_pnl_pct = {}
        # Günlük kayıp takibi
        self.daily_loss = 0.0
        self.daily_loss_date = datetime.now(TR_TZ).strftime("%Y-%m-%d")

    def feed_price(self, symbol, price):
        """Price updater'dan fiyat geçmişini besle"""
        if price and price > 0:
            self.price_history[symbol].append(price)
            if len(self.price_history[symbol]) > 200:
                self.price_history[symbol].pop(0)

    def fetch_historical_candles(self):
        """Başlangıçta 200 saatlik mum verisi çek (CryptoCompare)"""
        print("[AI Bot] Fetching historical candle data...")
        for symbol in self.SYMBOLS:
            coin = self.SYMBOL_MAP[symbol]
            # Birden fazla API dene
            coin_id = self.COINGECKO_IDS.get(coin, coin.lower())
            apis = [
                ("cryptocompare", f'https://min-api.cryptocompare.com/data/v2/histohour?fsym={coin}&tsym=USD&limit=200'),
                ("coingecko", f'https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=8'),
                ("binance", f'https://api.binance.us/api/v3/klines?symbol={symbol}&interval=1h&limit=200'),
            ]
            for api_name, api_url in apis:
                try:
                    req = request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'})
                    with request.urlopen(req, timeout=15) as resp:
                        raw = json.loads(resp.read().decode())
                        if api_name == "cryptocompare" and 'Data' in raw:
                            candles = raw.get('Data', {}).get('Data', [])
                            self.candle_closes[symbol] = [c['close'] for c in candles if c.get('close')]
                            self.candle_volumes[symbol] = [c.get('volumeto', 0) for c in candles]
                            self.candles_ohlc[symbol] = [
                                {'open': c['open'], 'high': c['high'], 'low': c['low'], 'close': c['close']}
                                for c in candles if c.get('close') and c.get('high')
                            ]
                        elif api_name == "coingecko" and 'prices' in raw:
                            self.candle_closes[symbol] = [p[1] for p in raw['prices']]
                            vols = raw.get('total_volumes', [])
                            self.candle_volumes[symbol] = [v[1] for v in vols] if vols else []
                            # CoinGecko sadece close veriyor, OHLC oluştur (close=open=high=low)
                            self.candles_ohlc[symbol] = [
                                {'open': p[1], 'high': p[1], 'low': p[1], 'close': p[1]}
                                for p in raw['prices']
                            ]
                        elif api_name == "binance" and isinstance(raw, list) and len(raw) > 0:
                            self.candle_closes[symbol] = [float(c[4]) for c in raw]
                            self.candle_volumes[symbol] = [float(c[5]) for c in raw]
                            self.candles_ohlc[symbol] = [
                                {'open': float(c[1]), 'high': float(c[2]), 'low': float(c[3]), 'close': float(c[4])}
                                for c in raw
                            ]
                        if self.candle_closes.get(symbol):
                            print(f"  [OK] {symbol} via {api_name}: {len(self.candle_closes[symbol])} candles")
                            break
                except Exception as e:
                    print(f"  [RETRY] {symbol} via {api_name}: {e}")
                    continue
            if not self.candle_closes.get(symbol):
                print(f"  [FAIL] {symbol}: No historical data available")
        self.data_ready = any(len(v) > 50 for v in self.candle_closes.values())
        print(f"[AI Bot] Historical data ready: {self.data_ready}")

    def fetch_fear_greed(self):
        """Fear & Greed Index çek"""
        try:
            url = 'https://api.alternative.me/fng/?limit=1'
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                entry = data['data'][0]
                self.fear_greed = {
                    "value": int(entry['value']),
                    "label": entry['value_classification']
                }
                print(f"[AI Bot] Fear & Greed: {self.fear_greed['value']} ({self.fear_greed['label']})")
        except Exception as e:
            print(f"[AI Bot] Fear & Greed fetch failed: {e}")

    def get_prices(self, symbol):
        """Saatlik + kısa vadeli fiyatları birleştir"""
        candles = list(self.candle_closes.get(symbol, []))
        live = list(self.price_history.get(symbol, []))
        if live:
            candles.extend(live[-20:])
        return candles

    def analyze_market(self, symbol, price_data):
        """Çoklu gösterge analizi + skor sistemi"""
        prices = self.get_prices(symbol)
        current_price = price_data.get('price', 0)
        change_24h = price_data.get('change', 0)
        volumes = self.candle_volumes.get(symbol, [])

        # Yeterli veri yoksa kısa vadeli fiyatlarla basit analiz yap
        if len(prices) < 30:
            live = self.price_history.get(symbol, [])
            if len(live) < 5:
                return "hold", "Veri bekleniyor...", 0, {}
            # Basit analiz - iki strateji: trend takip + dip alım
            rsi_simple = TechnicalAnalyzer.rsi(live, min(14, len(live) - 1)) if len(live) > 3 else 50
            fg = self.fear_greed.get('value', 50)

            # Kısa vadeli momentum (son fiyatlar yükseliyor mu düşüyor mu)
            momentum = 0
            if len(live) >= 6:
                recent_avg = sum(live[-3:]) / 3
                older_avg = sum(live[-6:-3]) / 3
                if older_avg > 0:
                    momentum = (recent_avg - older_avg) / older_avg * 100

            score = 0
            reasons = []

            # Trend takip: güçlü düşüş + negatif momentum = SHORT
            if change_24h < -4 and momentum < -0.05:
                score -= 50
                reasons.append(f"Güçlü düşüş:{change_24h:.1f}%")
            # Dip alım: düşüş ama momentum toparlanıyor = LONG
            elif change_24h < -2 and momentum >= 0:
                score += 40
                reasons.append(f"Dip alım:{change_24h:.1f}%")
            # Düşüş + düşüş momentum = SHORT devam
            elif change_24h < -2 and momentum < 0:
                score -= 30
                reasons.append(f"Trend düşüş:{change_24h:.1f}%")
            # Güçlü yükseliş + pozitif momentum = LONG
            elif change_24h > 4 and momentum > 0.05:
                score += 50
                reasons.append(f"Güçlü yükseliş:{change_24h:.1f}%")
            # Yükseliş ama momentum zayıflıyor = SHORT
            elif change_24h > 2 and momentum <= 0:
                score -= 40
                reasons.append(f"Zirve:{change_24h:.1f}%")
            # Yükseliş + pozitif momentum = LONG devam
            elif change_24h > 2 and momentum > 0:
                score += 30
                reasons.append(f"Trend yükseliş:{change_24h:.1f}%")

            if rsi_simple < 30:
                score += 25
                reasons.append(f"RSI:{rsi_simple:.0f}")
            elif rsi_simple > 70:
                score -= 25
                reasons.append(f"RSI:{rsi_simple:.0f}")

            # F&G aşırılıklarda sinyali güçlendirir
            if fg < 20 and score > 0:
                score += 15
                reasons.append(f"F&G:{fg}")
            elif fg > 80 and score < 0:
                score -= 15
                reasons.append(f"F&G:{fg}")

            if momentum != 0:
                reasons.append(f"Mom:{'↑' if momentum > 0 else '↓'}{abs(momentum):.2f}%")

            # Basit modda da FVG kontrol et
            ohlc_data = self.candles_ohlc.get(symbol, [])
            if len(ohlc_data) >= 3:
                _, basic_fvg_score = TechnicalAnalyzer.fair_value_gaps(ohlc_data, current_price, lookback=20)
                if abs(basic_fvg_score) > 20:
                    score += basic_fvg_score * 0.3
                    fvg_dir = "alım" if basic_fvg_score > 0 else "satım"
                    reasons.append(f"FVG:{fvg_dir}")

            if score > 20:
                signal, lev = "buy", 15
            elif score < -20:
                signal, lev = "sell", 15
            else:
                signal, lev = "hold", 0
            reason_text = f"Skor:{score:.0f} (basit) | " + " + ".join(reasons) if reasons else f"Skor:{score:.0f} | Nötr"
            return signal, reason_text, lev, {"total_score": score, "mode": "basic", "rsi": round(rsi_simple, 1), "fear_greed": self.fear_greed}

        # === TÜM GÖSTERGELERİ HESAPLA ===
        rsi = TechnicalAnalyzer.rsi(prices, 14)
        macd_line, macd_signal, macd_hist = TechnicalAnalyzer.macd(prices)
        bb_upper, bb_mid, bb_lower = TechnicalAnalyzer.bollinger_bands(prices)
        ema_cross = TechnicalAnalyzer.ema_crossover(prices, 9, 21)
        atr_value = TechnicalAnalyzer.atr(prices, 14)
        ema9 = TechnicalAnalyzer.ema_series(prices, 9)
        ema21 = TechnicalAnalyzer.ema_series(prices, 21)
        ema50 = TechnicalAnalyzer.ema_series(prices, 50)
        supports, resistances = TechnicalAnalyzer.support_resistance(prices)
        vol_signal, vol_ratio = TechnicalAnalyzer.volume_signal(volumes)
        fg_value = self.fear_greed.get('value', 50)
        # FVG (Fair Value Gap) hesapla
        ohlc_data = self.candles_ohlc.get(symbol, [])
        fvgs, fvg_score = TechnicalAnalyzer.fair_value_gaps(ohlc_data, current_price, lookback=30)
        # Divergence (RSI uyumsuzluğu)
        div_score, div_type = TechnicalAnalyzer.divergence(prices, 14)
        # Order Block
        ob_score = TechnicalAnalyzer.order_blocks(ohlc_data, current_price, lookback=30)
        # Liquidity Sweep
        liq_score = TechnicalAnalyzer.liquidity_sweep(ohlc_data, current_price, lookback=40)

        # === SKOR SİSTEMİ (-100 ile +100 arası) ===
        scores = {}

        # 1. RSI (ağırlık: %15)
        if rsi < 25:
            scores['rsi'] = 90
        elif rsi < 35:
            scores['rsi'] = 60
        elif rsi < 45:
            scores['rsi'] = 25
        elif rsi > 75:
            scores['rsi'] = -90
        elif rsi > 65:
            scores['rsi'] = -60
        elif rsi > 55:
            scores['rsi'] = -25
        else:
            scores['rsi'] = 0

        # 2. MACD (ağırlık: %20)
        if macd_hist > 0 and macd_line > macd_signal:
            scores['macd'] = min(80, macd_hist / (abs(current_price) * 0.001 + 0.01) * 40)
        elif macd_hist < 0 and macd_line < macd_signal:
            scores['macd'] = max(-80, macd_hist / (abs(current_price) * 0.001 + 0.01) * 40)
        else:
            scores['macd'] = 0

        # 3. Bollinger Bands (ağırlık: %15)
        if bb_lower and bb_upper and current_price:
            bb_width = bb_upper - bb_lower
            if bb_width > 0:
                bb_pos = (current_price - bb_lower) / bb_width  # 0-1 arası
                if bb_pos < 0.1:
                    scores['bollinger'] = 80  # Alt bandın altında - güçlü alım
                elif bb_pos < 0.3:
                    scores['bollinger'] = 40
                elif bb_pos > 0.9:
                    scores['bollinger'] = -80  # Üst bandın üstünde - güçlü satım
                elif bb_pos > 0.7:
                    scores['bollinger'] = -40
                else:
                    scores['bollinger'] = 0
            else:
                scores['bollinger'] = 0
        else:
            scores['bollinger'] = 0

        # 4. EMA Crossover (ağırlık: %15)
        cross_scores = {
            "golden_cross": 85, "bullish": 30,
            "death_cross": -85, "bearish": -30,
            "neutral": 0
        }
        scores['ema_cross'] = cross_scores.get(ema_cross, 0)

        # 5. Trend - EMA50 yönü (ağırlık: %10)
        if ema50 and len(ema50) > 5:
            trend_pct = (ema50[-1] - ema50[-5]) / ema50[-5] * 100 if ema50[-5] else 0
            scores['trend'] = max(-70, min(70, trend_pct * 30))
        else:
            scores['trend'] = 0

        # 6. Support/Resistance (ağırlık: %10)
        scores['sr'] = 0
        if supports and current_price:
            nearest_support = min(supports, key=lambda s: abs(s - current_price))
            dist_pct = (current_price - nearest_support) / current_price * 100
            if dist_pct < 1:  # Desteğe çok yakın
                scores['sr'] = 60
            elif dist_pct < 2:
                scores['sr'] = 30
        if resistances and current_price:
            nearest_resist = min(resistances, key=lambda r: abs(r - current_price))
            dist_pct = (nearest_resist - current_price) / current_price * 100
            if dist_pct < 1:  # Dirence çok yakın
                scores['sr'] = -60
            elif dist_pct < 2:
                scores['sr'] = -30

        # 7. Fear & Greed Index (trend doğrulayıcı - aşırı korku=SHORT güçlenir, aşırı açgözlülük=LONG güçlenir)
        if fg_value < 20:
            scores['fear_greed'] = -70  # Aşırı korku = piyasa çöküyor, SHORT güçlen
        elif fg_value < 35:
            scores['fear_greed'] = -35
        elif fg_value > 80:
            scores['fear_greed'] = 70  # Aşırı açgözlülük = piyasa coşmuş, LONG güçlen
        elif fg_value > 65:
            scores['fear_greed'] = 35
        else:
            scores['fear_greed'] = 0

        # 8. Volume (ağırlık: %5) - sinyali güçlendirir veya zayıflatır
        vol_multiplier = 1.0
        if vol_signal in ["very_high", "high"]:
            vol_multiplier = 1.3
        elif vol_signal in ["very_low", "low"]:
            vol_multiplier = 0.7
        scores['volume'] = 0  # Hacim tek başına sinyal vermez, çarpan olarak kullanılır

        # 9. FVG - Fair Value Gap (ağırlık: %14)
        scores['fvg'] = fvg_score

        # 10. Divergence - RSI Uyumsuzluğu (ağırlık: %10)
        scores['divergence'] = div_score

        # 11. Order Block - Kurumsal Bölge (ağırlık: %10)
        scores['order_block'] = ob_score

        # 12. Liquidity Sweep - Likidite Avı (ağırlık: %8)
        scores['liquidity'] = liq_score

        # 13. 24h Momentum - Günlük fiyat değişimi (ağırlık: %10)
        if change_24h <= -5:
            scores['momentum_24h'] = -80  # Sert düşüş = güçlü SHORT
        elif change_24h <= -3:
            scores['momentum_24h'] = -50
        elif change_24h <= -1:
            scores['momentum_24h'] = -20
        elif change_24h >= 5:
            scores['momentum_24h'] = 80  # Sert yükseliş = güçlü LONG
        elif change_24h >= 3:
            scores['momentum_24h'] = 50
        elif change_24h >= 1:
            scores['momentum_24h'] = 20
        else:
            scores['momentum_24h'] = 0

        # === AĞIRLIKLI TOPLAM SKOR ===
        weights = {
            'rsi': 0.08, 'macd': 0.08, 'bollinger': 0.06,
            'ema_cross': 0.10, 'trend': 0.10, 'sr': 0.06,
            'fear_greed': 0.08, 'volume': 0.04, 'fvg': 0.06,
            'divergence': 0.06, 'order_block': 0.06, 'liquidity': 0.06,
            'momentum_24h': 0.16
        }
        total_score = sum(scores.get(k, 0) * w for k, w in weights.items())
        total_score *= vol_multiplier  # Hacim çarpanı uygula
        total_score = max(-100, min(100, total_score))

        # === GÖSTERGE UYUMU KONTROLÜ ===
        # En az 3 gösterge aynı yönü göstermeli
        bullish_count = sum(1 for k, v in scores.items() if v > 10 and k != 'volume')
        bearish_count = sum(1 for k, v in scores.items() if v < -10 and k != 'volume')

        # === MULTI-TIMEFRAME FİLTRE ===
        # Saatlik mumlar (tarihsel veri) = üst zaman dilimi
        # 5dk mumlar (canlı) = alt zaman dilimi
        hourly_closes = list(self.candle_closes.get(symbol, []))
        trend_conflict = False
        htf_trend = "neutral"
        if len(hourly_closes) >= 50:
            htf_ema20 = TechnicalAnalyzer.ema_series(hourly_closes, 20)
            htf_ema50 = TechnicalAnalyzer.ema_series(hourly_closes, 50)
            if htf_ema20 and htf_ema50:
                if htf_ema20[-1] > htf_ema50[-1]:
                    htf_trend = "bullish"
                else:
                    htf_trend = "bearish"
                # Üst zaman dilimi ile çakışma kontrolü
                if htf_trend == "bearish" and total_score > 0:
                    total_score *= 0.6  # Sinyali zayıflat ama tamamen iptal etme
                    trend_conflict = total_score < 20  # Zayıflatıldıktan sonra eşik altında mı?
                elif htf_trend == "bullish" and total_score < 0:
                    total_score *= 0.6
                    trend_conflict = total_score > -20
                elif htf_trend == "bullish" and total_score > 0:
                    total_score *= 1.2  # Uyumlu trend = sinyali güçlendir
                    total_score = min(100, total_score)
                elif htf_trend == "bearish" and total_score < 0:
                    total_score *= 1.2
                    total_score = max(-100, total_score)
        else:
            # Yeterli veri yoksa EMA50 trend filtresi kullan
            trend_dir = scores.get('trend', 0)
            if trend_dir < -20 and total_score > 0:
                trend_conflict = True
            elif trend_dir > 20 and total_score < 0:
                trend_conflict = True

        # === KARAR ===
        min_agreement = 3  # En az 3 gösterge uyumu
        if total_score > 20 and bullish_count >= min_agreement and not trend_conflict:
            signal = "buy"
        elif total_score < -20 and bearish_count >= min_agreement and not trend_conflict:
            signal = "sell"
        else:
            signal = "hold"

        # === KALDIRAÇ (agresif) ===
        abs_score = abs(total_score)
        if abs_score > 75:
            leverage = 30
        elif abs_score > 60:
            leverage = 25
        elif abs_score > 45:
            leverage = 20
        elif abs_score > 30:
            leverage = 15
        else:
            leverage = 10

        # === ANALİZ DETAYLARI ===
        indicators = {
            "rsi": round(rsi, 1),
            "macd": {"line": round(macd_line, 4), "signal": round(macd_signal, 4), "hist": round(macd_hist, 4)},
            "bollinger": {"upper": round(bb_upper, 2) if bb_upper else 0, "mid": round(bb_mid, 2) if bb_mid else 0, "lower": round(bb_lower, 2) if bb_lower else 0},
            "ema_cross": ema_cross,
            "ema9": round(ema9[-1], 2) if ema9 else 0,
            "ema21": round(ema21[-1], 2) if ema21 else 0,
            "ema50": round(ema50[-1], 2) if ema50 else 0,
            "support": [round(s, 2) for s in supports],
            "resistance": [round(r, 2) for r in resistances],
            "volume": {"signal": vol_signal, "ratio": round(vol_ratio, 2)},
            "fear_greed": self.fear_greed,
            "atr": round(atr_value, 4) if atr_value else 0,
            "change_24h": round(change_24h, 2),
            "fvg": {
                "score": round(fvg_score, 1),
                "count": len(fvgs),
                "active": [{"type": f['type'], "top": round(f['top'], 2), "bottom": round(f['bottom'], 2), "filled": f['filled']} for f in fvgs[-3:]]
            },
            "divergence": {"score": round(div_score, 1), "type": div_type},
            "order_block": round(ob_score, 1),
            "liquidity_sweep": round(liq_score, 1),
            "htf_trend": htf_trend,
            "scores": {k: round(v, 1) for k, v in scores.items()},
            "total_score": round(total_score, 1)
        }

        # Açıklama oluştur
        reasons = []
        if abs(scores.get('rsi', 0)) > 30:
            reasons.append(f"RSI:{rsi:.0f}")
        if abs(scores.get('macd', 0)) > 20:
            reasons.append(f"MACD:{'↑' if macd_hist > 0 else '↓'}")
        if abs(scores.get('bollinger', 0)) > 20:
            reasons.append(f"BB:{'alt' if scores['bollinger'] > 0 else 'üst'}")
        if abs(scores.get('ema_cross', 0)) > 20:
            reasons.append(f"EMA:{ema_cross}")
        if abs(scores.get('fear_greed', 0)) > 20:
            reasons.append(f"F&G:{fg_value}")
        if abs(scores.get('sr', 0)) > 20:
            reasons.append("S/R yakın")
        if abs(scores.get('fvg', 0)) > 20:
            fvg_dir = "alım" if scores['fvg'] > 0 else "satım"
            reasons.append(f"FVG:{fvg_dir}")
        if abs(scores.get('divergence', 0)) > 20:
            reasons.append(f"DIV:{div_type}")
        if abs(scores.get('order_block', 0)) > 20:
            ob_dir = "destek" if scores['order_block'] > 0 else "direnç"
            reasons.append(f"OB:{ob_dir}")
        if abs(scores.get('liquidity', 0)) > 20:
            liq_dir = "sweep↑" if scores['liquidity'] > 0 else "sweep↓"
            reasons.append(f"LIQ:{liq_dir}")

        reason_text = f"Skor:{total_score:.0f} | " + " + ".join(reasons) if reasons else f"Skor:{total_score:.0f} | Nötr"

        return signal, reason_text, leverage, indicators

    def should_open_position(self, symbol, signal):
        if signal not in ["buy", "sell"]:
            return False
        if len(self.state.positions) >= AI_CONFIG["max_positions"]:
            return False
        for pos in self.state.positions:
            if pos['symbol'] == symbol:
                return False
        # Soğuma süresi kontrolü (5 dakika)
        if time.time() < self.cooldown_until.get(symbol, 0):
            return False
        # Aynı yönde max pozisyon kontrolü
        same_dir = sum(1 for p in self.state.positions if p['side'] == signal)
        if same_dir >= AI_CONFIG["max_same_direction"]:
            return False
        # Günlük kayıp limiti kontrolü
        today = datetime.now(TR_TZ).strftime("%Y-%m-%d")
        if today != self.daily_loss_date:
            self.daily_loss = 0.0
            self.daily_loss_date = today
        daily_limit = self.state.initial_balance * AI_CONFIG["daily_loss_limit_pct"] / 100
        if self.daily_loss >= daily_limit:
            return False
        # Hacim kontrolü - düşük hacimde işlem açma
        volumes = self.candle_volumes.get(symbol, [])
        if len(volumes) >= 20:
            avg_vol = sum(volumes[-20:]) / 20
            current_vol = volumes[-1] if volumes else 0
            if avg_vol > 0 and current_vol / avg_vol < AI_CONFIG["min_volume_ratio"]:
                return False
        return True

    def should_close_position(self, position):
        """Akıllı çıkış - Trailing SL + dinamik TP/SL + acil SL + ters sinyal"""
        pnl_pct = (position['pnl'] / position['margin']) * 100
        pos_id = position['id']

        # Dinamik TP/SL (ATR bazlı, yoksa sabit)
        sl_pct = position.get('dynamic_sl_pct', AI_CONFIG["stop_loss_pct"])
        tp_pct = position.get('dynamic_tp_pct', AI_CONFIG["take_profit_pct"])

        # Minimum tutma süresi kontrolü
        open_ts = position.get('open_timestamp', 0)
        hold_time = time.time() - open_ts if open_ts else float('inf')
        is_mature = hold_time >= AI_CONFIG["min_hold_time"]  # 30 dakika

        # Acil stop loss - HER ZAMAN aktif (kayıp %12'yi geçerse hemen kapat)
        if pnl_pct <= -12:
            return True, f"Acil Stop Loss (-%{abs(pnl_pct):.1f})"

        # Normal stop loss - HER ZAMAN aktif (dinamik)
        if pnl_pct <= -sl_pct:
            return True, f"Stop Loss (-%{abs(pnl_pct):.1f})"

        # Take profit - HER ZAMAN aktif (dinamik)
        if pnl_pct >= tp_pct:
            return True, f"Take Profit (+%{pnl_pct:.1f})"

        # Peak tracking - HER ZAMAN çalışır (zirveyi yakala)
        peak = self.peak_pnl_pct.get(pos_id, 0)
        if pnl_pct > peak:
            self.peak_pnl_pct[pos_id] = pnl_pct
            peak = pnl_pct

        # === Aşağıdaki kurallar sadece minimum tutma süresi geçtikten sonra aktif ===
        if not is_mature:
            return False, None

        # Trailing Stop mekanizması
        if peak >= 5:
            trailing_sl = peak * 0.6
            if pnl_pct <= trailing_sl:
                return True, f"Trailing Stop (+%{pnl_pct:.1f}, zirve: +%{peak:.1f})"
        elif peak >= 3:
            if pnl_pct <= 0:
                return True, f"Breakeven Stop (+%{pnl_pct:.1f}, zirve: +%{peak:.1f})"

        # Ters sinyal kontrolü - pozisyon yönüne ters güçlü sinyal varsa kapat
        symbol = position['symbol']
        price_data = self.state.prices.get(symbol)
        if price_data and self.data_ready:
            signal, _, _, indicators = self.analyze_market(symbol, price_data)
            score = indicators.get('total_score', 0)
            if position['side'] == 'buy' and score < -70:
                return True, f"Ters sinyal (skor:{score:.0f})"
            elif position['side'] == 'sell' and score > 70:
                return True, f"Ters sinyal (skor:{score:.0f})"

        return False, None

    def run(self):
        print("[AI Bot] Starting professional trading engine...")
        self.running = True

        # İlk başta tarihsel veri çek
        self.fetch_historical_candles()
        self.fetch_fear_greed()

        fg_timer = 0
        candle_timer = 0
        analysis_timer = 0
        retry_history = 0
        loop_interval = 30  # Ana döngü 30 saniyede bir çalışır
        candle_ticks = AI_CONFIG["candle_period"] // loop_interval  # 5dk mum = 10 tick
        analysis_ticks = max(1, AI_CONFIG["check_interval"] // loop_interval)  # 60sn analiz = 2 tick

        while self.running:
            try:
                self.state.update_prices()

                # Fear & Greed her 30 dakikada güncelle (60 * 30sn = 30dk)
                fg_timer += 1
                if fg_timer >= 60:
                    self.fetch_fear_greed()
                    fg_timer = 0

                # Tarihsel veri yoksa 5 dakikada bir tekrar dene
                if not self.data_ready:
                    retry_history += 1
                    if retry_history >= 10:
                        print("[AI Bot] Retrying historical data fetch...")
                        self.fetch_historical_candles()
                        retry_history = 0

                # Canlı fiyatlardan OHLC mum oluştur (her tick'te güncelle)
                for sym in self.SYMBOLS:
                    price = self.state.prices.get(sym, {}).get('price', 0)
                    if price > 0:
                        if self.current_candle[sym] is None:
                            self.current_candle[sym] = {'open': price, 'high': price, 'low': price, 'close': price}
                        else:
                            self.current_candle[sym]['high'] = max(self.current_candle[sym]['high'], price)
                            self.current_candle[sym]['low'] = min(self.current_candle[sym]['low'], price)
                            self.current_candle[sym]['close'] = price

                # 5 dakikalık mum tamamlandığında kaydet
                candle_timer += 1
                if candle_timer >= candle_ticks:
                    for sym in self.SYMBOLS:
                        if self.current_candle[sym]:
                            candle = self.current_candle[sym]
                            self.candle_closes[sym].append(candle['close'])
                            self.candles_ohlc[sym].append(candle)
                            if len(self.candle_closes[sym]) > 500:
                                self.candle_closes[sym].pop(0)
                            if len(self.candles_ohlc[sym]) > 500:
                                self.candles_ohlc[sym].pop(0)
                            self.current_candle[sym] = None
                    candle_timer = 0
                    ready_count = sum(1 for v in self.candle_closes.values() if len(v) >= 30)
                    if ready_count >= len(self.SYMBOLS) // 2 and not self.data_ready:
                        self.data_ready = True
                        candle_info = {s: len(v) for s, v in self.candle_closes.items()}
                        print(f"[AI Bot] FULL analysis mode active! Candles: {candle_info}")

                # Stop loss / take profit kontrolü - HER 30 SANİYEDE
                positions_to_close = []
                for pos in self.state.positions:
                    should_close, reason = self.should_close_position(pos)
                    if should_close:
                        positions_to_close.append((pos['id'], reason))

                for pos_id, reason in positions_to_close:
                    result = self.state.close_position(pos_id)
                    if result.get('status') == 'ok':
                        trade = result['trade']
                        self.cooldown_until[trade['symbol']] = time.time() + AI_CONFIG["cooldown"]
                        self.peak_pnl_pct.pop(pos_id, None)
                        if trade['pnl'] < 0:
                            self.daily_loss += abs(trade['pnl'])
                        print(f"[AI Bot] CLOSED: {trade['symbol']} | PnL: ${trade['pnl']:.2f} | {reason}")

                        pnl = trade['pnl']
                        pnl_emoji = "✅" if pnl >= 0 else "❌"
                        side_text = "LONG" if trade['side'] == 'buy' else "SHORT"
                        coin = trade['symbol'].replace('USDT', '')
                        lev = trade.get('leverage', 1)
                        pnl_pct = (pnl / AI_CONFIG["trade_amount"]) * 100
                        equity = self.state.get_equity()
                        msg = (
                            f"{pnl_emoji} <b>{side_text} Kapandı</b>\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"🪙 Coin: <b>{coin}/USDT</b>\n"
                            f"💰 Giriş: <b>${trade['entry']:,.2f}</b>\n"
                            f"💰 Çıkış: <b>${trade['exit']:,.2f}</b>\n"
                            f"⚡ Kaldıraç: <b>{lev}x</b>\n"
                            f"{'📈' if pnl >= 0 else '📉'} Kar/Zarar: <b>{'+' if pnl >= 0 else '-'}${abs(pnl):,.2f} ({pnl_pct:+.2f}%)</b>\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"💼 Bakiye: ${equity:,.2f}\n"
                            f"📝 Sebep: {reason}"
                        )
                        send_telegram(msg)

                # Yeni pozisyon analizi - her check_interval'de
                analysis_timer += 1
                if analysis_timer >= analysis_ticks:
                    analysis_timer = 0
                    for symbol in self.SYMBOLS:
                        price_data = self.state.prices.get(symbol)
                        if not price_data:
                            continue

                        # Yetersiz veri kontrolü - minimum mum sayısı olmadan işlem açma
                        candle_count = len(self.candle_closes.get(symbol, []))
                        if candle_count < AI_CONFIG["min_candles"]:
                            continue

                        signal, reason, leverage, indicators = self.analyze_market(symbol, price_data)
                        self.last_analysis[symbol] = {
                            "signal": signal,
                            "reason": reason,
                            "leverage": leverage,
                            "indicators": indicators,
                            "time": datetime.now(TR_TZ).strftime("%H:%M:%S")
                        }

                        if self.should_open_position(symbol, signal):
                            atr_val = indicators.get('atr', 0)
                            result = self.state.open_position(
                                symbol=symbol, side=signal,
                                amount=AI_CONFIG["trade_amount"], leverage=leverage,
                                atr=atr_val
                            )
                            if result.get('status') == 'ok':
                                pos = result['position']
                                print(f"[AI Bot] OPENED: {pos['symbol']} {pos['side'].upper()} {leverage}x | Entry: ${pos['entry']:.2f} | {reason}")

                                self.state.update_position_pnl()
                                tp = pos.get('tp_price', 0)
                                sl = pos.get('sl_price', 0)
                                emoji = "🟢" if signal == "buy" else "🔴"
                                side_text = "LONG" if signal == "buy" else "SHORT"
                                coin = pos['symbol'].replace('USDT', '')
                                ind = indicators or {}
                                rsi_val = ind.get('rsi', '-')
                                score_val = ind.get('total_score', '-')
                                msg = (
                                    f"{emoji} <b>{side_text} Açıldı</b>\n"
                                    f"━━━━━━━━━━━━━━━\n"
                                    f"🪙 Coin: <b>{coin}/USDT</b>\n"
                                    f"💰 Giriş: <b>${pos['entry']:,.2f}</b>\n"
                                    f"⚡ Kaldıraç: <b>{leverage}x</b>\n"
                                    f"🎯 TP: <b>${tp:,.2f}</b>\n"
                                    f"🛑 SL: <b>${sl:,.2f}</b>\n"
                                    f"━━━━━━━━━━━━━━━\n"
                                    f"📊 Skor: {score_val} | RSI: {rsi_val}\n"
                                    f"📝 {reason}"
                                )
                                send_telegram(msg)

                time.sleep(loop_interval)

            except Exception as e:
                print(f"[AI Bot] Error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)

    def start(self):
        if not self.running:
            self.state.ai_status = "running"
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            return True
        return False

    def stop(self):
        self.running = False
        self.state.ai_status = "idle"
        return True

class PaperTradingState:
    def __init__(self):
        self.initial_balance = 10000.0
        self.prices = {
            "BTCUSDT": {"price": 84230.50, "change": 2.34},
            "ETHUSDT": {"price": 2456.80, "change": -1.23},
            "SOLUSDT": {"price": 198.45, "change": 5.67},
            "XRPUSDT": {"price": 2.45, "change": -0.89}
        }
        self.ai_status = "idle"
        self.selected_model = "gpt-4"
        self.lock = threading.Lock()
        self.ai_bot = AITradingBot(self)

        # DB'den yükle
        init_db()
        saved_trades, saved_positions, saved_balance = db_load_all()
        self.trades = saved_trades
        self.positions = saved_positions
        self.balance = saved_balance if saved_balance is not None else self.initial_balance
        print(f"[DB] Loaded: {len(self.trades)} trades, {len(self.positions)} positions, balance=${self.balance:.2f}")
        
    def update_prices(self):
        """Çoklu API desteği - biri çalışmazsa diğeri devreye girer"""
        
        # API 1: Binance.US (ABD versiyonu, farklı IP blokları)
        try:
            symbols_json = json.dumps(list(AITradingBot.SYMBOLS))
            url = f'https://api.binance.us/api/v3/ticker/24hr?symbols={symbols_json}'
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with request.urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode())
                with self.lock:
                    for item in data:
                        symbol = item['symbol']
                        if symbol in AITradingBot.SYMBOL_MAP:
                            self.prices[symbol] = {
                                "price": float(item['lastPrice']),
                                "change": float(item['priceChangePercent'])
                            }
                    self.update_position_pnl()
                    print("[Price] Binance.US OK")
                    return
        except Exception as e:
            print(f"[Price] Binance.US failed: {e}")
        
        # API 2: KuCoin
        try:
            url = 'https://api.kucoin.com/api/v1/market/allTickers'
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with request.urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode())
                tickers = {t['symbol']: t for t in data['data']['ticker']}
                with self.lock:
                    for symbol in AITradingBot.SYMBOLS:
                        coin = AITradingBot.SYMBOL_MAP[symbol]
                        kucoin_sym = f"{coin}-USDT"
                        if kucoin_sym in tickers:
                            self.prices[symbol] = {"price": float(tickers[kucoin_sym]['last']), "change": float(tickers[kucoin_sym]['changeRate']) * 100}
                    self.update_position_pnl()
                    print("[Price] KuCoin OK")
                    return
        except Exception as e:
            print(f"[Price] KuCoin failed: {e}")
        
        # API 3: CoinGecko (proxy üzerinden)
        try:
            ids_str = ",".join(AITradingBot.COINGECKO_IDS[AITradingBot.SYMBOL_MAP[s]] for s in AITradingBot.SYMBOLS if AITradingBot.SYMBOL_MAP[s] in AITradingBot.COINGECKO_IDS)
            url = f'https://api.coingecko.com/api/v3/simple/price?ids={ids_str}&vs_currencies=usd&include_24hr_change=true'
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            with request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                with self.lock:
                    for symbol in AITradingBot.SYMBOLS:
                        coin = AITradingBot.SYMBOL_MAP[symbol]
                        coin_id = AITradingBot.COINGECKO_IDS.get(coin)
                        if coin_id and coin_id in data:
                            self.prices[symbol] = {"price": float(data[coin_id]['usd']), "change": float(data[coin_id].get('usd_24h_change', 0))}
                    self.update_position_pnl()
                    print("[Price] CoinGecko OK")
                    return
        except Exception as e:
            print(f"[Price] CoinGecko failed: {e}")
        
        # API 4: CryptoCompare
        try:
            coins_str = ",".join(AITradingBot.SYMBOL_MAP[s] for s in AITradingBot.SYMBOLS)
            url = f'https://min-api.cryptocompare.com/data/pricemultifull?fsyms={coins_str}&tsyms=USD'
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with request.urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode())
                raw = data['RAW']
                with self.lock:
                    for symbol in AITradingBot.SYMBOLS:
                        coin = AITradingBot.SYMBOL_MAP[symbol]
                        if coin in raw and 'USD' in raw[coin]:
                            self.prices[symbol] = {"price": float(raw[coin]['USD']['PRICE']), "change": float(raw[coin]['USD'].get('CHANGEPCT24HOUR', 0))}
                    self.update_position_pnl()
                    print("[Price] CryptoCompare OK")
                    return
        except Exception as e:
            print(f"[Price] CryptoCompare failed: {e}")
        
        print("[Price] All APIs failed, using cached prices")
    
    def update_position_pnl(self):
        for pos in self.positions:
            symbol = pos['symbol']
            current_price = self.prices.get(symbol, {}).get('price', pos['entry'])

            if pos['side'] == 'buy':
                price_diff = current_price - pos['entry']
            else:
                price_diff = pos['entry'] - current_price

            pos['pnl'] = price_diff * pos['size']
            pos['current_price'] = current_price

            # Hedef fiyatları hesapla (dinamik TP/SL)
            entry = pos['entry']
            size = pos['size']
            margin = pos['margin']
            tp_pct = pos.get('dynamic_tp_pct', AI_CONFIG["take_profit_pct"])
            sl_pct = pos.get('dynamic_sl_pct', AI_CONFIG["stop_loss_pct"])
            tp_delta = (margin * tp_pct / 100) / size
            sl_delta = (margin * sl_pct / 100) / size

            if pos['side'] == 'buy':
                pos['tp_price'] = entry + tp_delta
                pos['sl_price'] = entry - sl_delta
            else:
                pos['tp_price'] = entry - tp_delta
                pos['sl_price'] = entry + sl_delta
    
    def open_position(self, symbol, side, amount, leverage=1, atr=0):
        with self.lock:
            price = self.prices.get(symbol, {}).get('price', 0)
            if price == 0:
                return {"error": "Price not available"}

            size = (amount * leverage) / price

            if amount > self.balance:
                return {"error": "Insufficient balance"}

            self.balance -= amount

            # Benzersiz ID için DB'deki max + mevcut pozisyonlardan büyük olan
            max_id = max([p['id'] for p in self.positions], default=0)
            new_id = max_id + 1

            # ATR tabanlı dinamik TP/SL (coin volatilitesine göre)
            if atr and atr > 0 and price > 0:
                atr_pct = (atr / price) * 100  # ATR'nin fiyata oranı (%)
                # SL: 3x ATR (nefes alanı), TP: 5:1 risk/reward
                dynamic_sl_pct = max(5, min(10, atr_pct * 3 * leverage))
                dynamic_tp_pct = max(25, min(50, dynamic_sl_pct * 5))
            else:
                dynamic_tp_pct = AI_CONFIG["take_profit_pct"]
                dynamic_sl_pct = AI_CONFIG["stop_loss_pct"]

            position = {
                "id": new_id,
                "symbol": symbol,
                "side": side,
                "entry": price,
                "current_price": price,
                "size": size,
                "leverage": leverage,
                "margin": amount,
                "pnl": 0.0,
                "open_time": datetime.now(TR_TZ).strftime("%H:%M:%S"),
                "open_timestamp": time.time(),
                "dynamic_tp_pct": round(dynamic_tp_pct, 1),
                "dynamic_sl_pct": round(dynamic_sl_pct, 1),
            }

            self.positions.append(position)
            db_save_position(position)
            db_save_balance(self.balance)

            return {
                "status": "ok",
                "position": position,
                "balance": self.balance
            }
    
    def close_position(self, position_id):
        with self.lock:
            pos = None
            for p in self.positions:
                if p['id'] == position_id:
                    pos = p
                    break
            
            if not pos:
                return {"error": "Position not found"}
            
            self.update_position_pnl()
            final_pnl = pos['pnl']
            
            self.balance += pos['margin'] + final_pnl
            
            trade = {
                "time": datetime.now(TR_TZ).strftime("%H:%M:%S"),
                "date": datetime.now(TR_TZ).strftime("%Y-%m-%d"),
                "symbol": pos['symbol'],
                "side": pos['side'],
                "entry": pos['entry'],
                "exit": pos['current_price'],
                "leverage": pos['leverage'],
                "pnl": final_pnl,
                "model": self.selected_model
            }
            self.trades.insert(0, trade)
            self.positions.remove(pos)

            db_save_trade(trade)
            db_remove_position(pos['id'])
            db_save_balance(self.balance)

            return {
                "status": "ok",
                "trade": trade,
                "balance": self.balance
            }
    
    def get_equity(self):
        self.update_position_pnl()
        # Equity = balance + açık pozisyonların (margin + pnl) toplamı
        positions_value = sum(p['margin'] + p['pnl'] for p in self.positions)
        return self.balance + positions_value
    
    def get_state(self):
        with self.lock:
            self.update_position_pnl()
            equity = self.get_equity()
            total_pnl = equity - self.initial_balance
            total_pnl_pct = (total_pnl / self.initial_balance) * 100 if self.initial_balance else 0
            
            winning_trades = len([t for t in self.trades if t['pnl'] > 0])
            total_trades = len(self.trades)
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            return {
                "prices": self.prices,
                "positions": self.positions,
                "trades": self.trades[:50],
                "balance": self.balance,
                "equity": equity,
                "initial_balance": self.initial_balance,
                "pnl": total_pnl,
                "pnl_pct": round(total_pnl_pct, 2),
                "win_rate": round(win_rate, 1),
                "total_trades": total_trades,
                "ai_status": self.ai_status,
                "selected_model": self.selected_model,
                "ai_analysis": self.ai_bot.last_analysis,
                "fear_greed": self.ai_bot.fear_greed,
                "timestamp": datetime.now(TR_TZ).isoformat()
            }

state = PaperTradingState()

def price_updater():
    while True:
        state.update_prices()
        # Bot'un fiyat geçmişini besle
        for symbol in AITradingBot.SYMBOLS:
            price = state.prices.get(symbol, {}).get('price', 0)
            state.ai_bot.feed_price(symbol, price)
        time.sleep(5)

class DashboardHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == "/":
            self.serve_file("index.html", "text/html")
        elif path == "/api/state":
            self.serve_json(state.get_state())
        elif path == "/api/prices":
            self.serve_json(state.prices)
        elif path.endswith(".css"):
            self.serve_file(path[1:], "text/css")
        elif path.endswith(".js"):
            self.serve_file(path[1:], "application/javascript")
        else:
            self.send_error(404)
    
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == "/api/trade":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            
            result = state.open_position(
                symbol=data.get('symbol'),
                side=data.get('side'),
                amount=float(data.get('amount', 0)),
                leverage=int(data.get('leverage', 1))
            )
            self.serve_json(result)
            
        elif path == "/api/close":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            
            result = state.close_position(data.get('position_id'))
            self.serve_json(result)
            
        elif path == "/api/bot/start":
            success = state.ai_bot.start()
            self.serve_json({"status": "ok" if success else "already_running"})
            
        elif path == "/api/bot/stop":
            success = state.ai_bot.stop()
            self.serve_json({"status": "ok" if success else "not_running"})
            
        elif path == "/api/model":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            state.selected_model = data.get("model", "gpt-4")
            self.serve_json({"status": "ok", "model": state.selected_model})

        elif path == "/api/reset":
            db_reset()
            state.balance = state.initial_balance
            state.positions = []
            state.trades = []
            db_save_balance(state.balance)
            self.serve_json({"status": "ok", "message": "All data reset to $10,000"})

        else:
            self.send_error(404)
    
    def serve_file(self, filepath, content_type):
        try:
            full_path = Path(__file__).parent / filepath
            with open(full_path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404)
    
    def serve_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def log_message(self, format, *args):
        pass

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    allow_reuse_address = True

def main():
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")
    
    print("[Server] Starting price updater...")
    updater_thread = threading.Thread(target=price_updater, daemon=True)
    updater_thread.start()

    # Bot'u otomatik başlat (10 saniye sonra, fiyatlar yüklensin)
    def auto_start_bot():
        time.sleep(10)
        print("[Server] Auto-starting AI bot...")
        state.ai_bot.start()

    bot_starter = threading.Thread(target=auto_start_bot, daemon=True)
    bot_starter.start()

    server = ThreadedHTTPServer((host, port), DashboardHandler)
    print(f"[Server] Dashboard running at http://{host}:{port}")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
