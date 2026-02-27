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
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib import request
import socketserver
import urllib.parse

# Database
DB_PATH = Path(__file__).parent / "trading.db"

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT, date TEXT, symbol TEXT, side TEXT,
        entry REAL, exit_price REAL, pnl REAL, leverage INTEGER DEFAULT 1, model TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, side TEXT, entry REAL, size REAL,
        leverage INTEGER, margin REAL, open_time TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY, value REAL
    )""")
    conn.commit()
    conn.close()
    print("[DB] SQLite initialized")

def db_save_trade(trade):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO trades (time, date, symbol, side, entry, exit_price, pnl, leverage, model) VALUES (?,?,?,?,?,?,?,?,?)",
        (trade['time'], trade.get('date', datetime.now(TR_TZ).strftime("%Y-%m-%d")),
         trade['symbol'], trade['side'], trade['entry'], trade['exit'], trade['pnl'],
         trade.get('leverage', 1), trade['model'])
    )
    conn.commit()
    conn.close()

def db_save_position(pos):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO positions (id, symbol, side, entry, size, leverage, margin, open_time) VALUES (?,?,?,?,?,?,?,?)",
        (pos['id'], pos['symbol'], pos['side'], pos['entry'], pos['size'], pos['leverage'], pos['margin'], pos['open_time'])
    )
    conn.commit()
    conn.close()

def db_remove_position(pos_id):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM positions WHERE id = ?", (pos_id,))
    conn.commit()
    conn.close()

def db_save_balance(balance):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('balance', ?)", (balance,))
    conn.commit()
    conn.close()

def db_load_all():
    """Sunucu başlangıcında tüm verileri yükle"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    trades = []
    for row in conn.execute("SELECT * FROM trades ORDER BY id DESC"):
        trades.append({
            "time": row['time'], "date": row['date'], "symbol": row['symbol'],
            "side": row['side'], "entry": row['entry'], "exit": row['exit_price'],
            "pnl": row['pnl'], "leverage": row['leverage'] if 'leverage' in row.keys() else 1,
            "model": row['model']
        })

    positions = []
    for row in conn.execute("SELECT * FROM positions"):
        positions.append({
            "id": row['id'], "symbol": row['symbol'], "side": row['side'],
            "entry": row['entry'], "current_price": row['entry'], "size": row['size'],
            "leverage": row['leverage'], "margin": row['margin'], "pnl": 0.0,
            "open_time": row['open_time']
        })

    balance_row = conn.execute("SELECT value FROM state WHERE key = 'balance'").fetchone()
    balance = balance_row['value'] if balance_row else None

    conn.close()
    return trades, positions, balance

# Turkey timezone (UTC+3)
TR_TZ = timezone(timedelta(hours=3))

# AI Trading Configuration
AI_CONFIG = {
    "max_positions": 5,
    "trade_amount": 100,
    "check_interval": 30,  # 30 saniye
    "stop_loss_pct": 5,
    "take_profit_pct": 10,
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


class AITradingBot:
    """Profesyonel seviye AI trading bot - çoklu gösterge analizi"""
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    SYMBOL_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "XRPUSDT": "XRP"}

    def __init__(self, state):
        self.state = state
        self.running = False
        self.thread = None
        self.last_analysis = {}
        # Saatlik mum verileri (200 mum)
        self.candle_closes = {s: [] for s in self.SYMBOLS}
        self.candle_volumes = {s: [] for s in self.SYMBOLS}
        # Kısa vadeli fiyat (5sn aralıklarla)
        self.price_history = {s: [] for s in self.SYMBOLS}
        # Fear & Greed Index
        self.fear_greed = {"value": 50, "label": "Neutral"}
        self.data_ready = False

    def feed_price(self, symbol, price):
        """Price updater'dan fiyat geçmişini besle"""
        if price and price > 0:
            self.price_history[symbol].append(price)
            if len(self.price_history[symbol]) > 100:
                self.price_history[symbol].pop(0)

    def fetch_historical_candles(self):
        """Başlangıçta 200 saatlik mum verisi çek (CryptoCompare)"""
        print("[AI Bot] Fetching historical candle data...")
        for symbol in self.SYMBOLS:
            coin = self.SYMBOL_MAP[symbol]
            try:
                url = f'https://min-api.cryptocompare.com/data/v2/histohour?fsym={coin}&tsym=USD&limit=200'
                req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                    candles = data.get('Data', {}).get('Data', [])
                    self.candle_closes[symbol] = [c['close'] for c in candles if c.get('close')]
                    self.candle_volumes[symbol] = [c.get('volumeto', 0) for c in candles]
                    print(f"  [OK] {symbol}: {len(self.candle_closes[symbol])} candles loaded")
            except Exception as e:
                print(f"  [FAIL] {symbol}: {e}")
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

        if len(prices) < 30:
            return "hold", "Yetersiz veri", 0, {}

        # === TÜM GÖSTERGELERİ HESAPLA ===
        rsi = TechnicalAnalyzer.rsi(prices, 14)
        macd_line, macd_signal, macd_hist = TechnicalAnalyzer.macd(prices)
        bb_upper, bb_mid, bb_lower = TechnicalAnalyzer.bollinger_bands(prices)
        ema_cross = TechnicalAnalyzer.ema_crossover(prices, 9, 21)
        ema9 = TechnicalAnalyzer.ema_series(prices, 9)
        ema21 = TechnicalAnalyzer.ema_series(prices, 21)
        ema50 = TechnicalAnalyzer.ema_series(prices, 50)
        supports, resistances = TechnicalAnalyzer.support_resistance(prices)
        vol_signal, vol_ratio = TechnicalAnalyzer.volume_signal(volumes)
        fg_value = self.fear_greed.get('value', 50)

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

        # 7. Fear & Greed Index (ağırlık: %10)
        if fg_value < 20:
            scores['fear_greed'] = 70  # Aşırı korku = alım fırsatı
        elif fg_value < 35:
            scores['fear_greed'] = 35
        elif fg_value > 80:
            scores['fear_greed'] = -70  # Aşırı açgözlülük = satım
        elif fg_value > 65:
            scores['fear_greed'] = -35
        else:
            scores['fear_greed'] = 0

        # 8. Volume (ağırlık: %5) - sinyali güçlendirir veya zayıflatır
        vol_multiplier = 1.0
        if vol_signal in ["very_high", "high"]:
            vol_multiplier = 1.3
        elif vol_signal in ["very_low", "low"]:
            vol_multiplier = 0.7
        scores['volume'] = 0  # Hacim tek başına sinyal vermez, çarpan olarak kullanılır

        # === AĞIRLIKLI TOPLAM SKOR ===
        weights = {
            'rsi': 0.15, 'macd': 0.20, 'bollinger': 0.15,
            'ema_cross': 0.15, 'trend': 0.10, 'sr': 0.10,
            'fear_greed': 0.10, 'volume': 0.05
        }
        total_score = sum(scores.get(k, 0) * w for k, w in weights.items())
        total_score *= vol_multiplier  # Hacim çarpanı uygula
        total_score = max(-100, min(100, total_score))

        # === KARAR ===
        if total_score > 10:
            signal = "buy"
        elif total_score < -10:
            signal = "sell"
        else:
            signal = "hold"

        # === KALDIRAÇ (sinyal gücüne göre) ===
        abs_score = abs(total_score)
        if abs_score > 70:
            leverage = 30
        elif abs_score > 55:
            leverage = 25
        elif abs_score > 45:
            leverage = 20
        elif abs_score > 35:
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
            "change_24h": round(change_24h, 2),
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

        reason_text = f"Skor:{total_score:.0f} | " + " + ".join(reasons) if reasons else f"Skor:{total_score:.0f} | Nötr"

        return signal, reason_text, leverage, indicators

    def should_open_position(self, symbol, signal):
        if len(self.state.positions) >= AI_CONFIG["max_positions"]:
            return False
        for pos in self.state.positions:
            if pos['symbol'] == symbol:
                return False
        return signal in ["buy", "sell"]

    def should_close_position(self, position):
        """Akıllı çıkış - TP/SL + ters sinyal kontrolü"""
        pnl_pct = (position['pnl'] / position['margin']) * 100
        if pnl_pct <= -AI_CONFIG["stop_loss_pct"]:
            return True, f"Stop Loss (-%{abs(pnl_pct):.1f})"
        if pnl_pct >= AI_CONFIG["take_profit_pct"]:
            return True, f"Take Profit (+%{pnl_pct:.1f})"

        # Ters sinyal kontrolü - pozisyon yönüne ters güçlü sinyal varsa kapat
        symbol = position['symbol']
        price_data = self.state.prices.get(symbol)
        if price_data and self.data_ready:
            signal, _, _, indicators = self.analyze_market(symbol, price_data)
            score = indicators.get('total_score', 0)
            if position['side'] == 'buy' and score < -50:
                return True, f"Ters sinyal (skor:{score:.0f})"
            elif position['side'] == 'sell' and score > 50:
                return True, f"Ters sinyal (skor:{score:.0f})"

        return False, None

    def run(self):
        print("[AI Bot] Starting professional trading engine...")
        self.running = True

        # İlk başta tarihsel veri çek
        self.fetch_historical_candles()
        self.fetch_fear_greed()

        fg_timer = 0

        while self.running:
            try:
                self.state.update_prices()

                # Fear & Greed her 30 dakikada güncelle
                fg_timer += 1
                if fg_timer >= 60:  # 60 * 30sn = 30dk
                    self.fetch_fear_greed()
                    fg_timer = 0

                # Stop loss / take profit / ters sinyal kontrolü
                positions_to_close = []
                for pos in self.state.positions:
                    should_close, reason = self.should_close_position(pos)
                    if should_close:
                        positions_to_close.append((pos['id'], reason))

                for pos_id, reason in positions_to_close:
                    result = self.state.close_position(pos_id)
                    if result.get('status') == 'ok':
                        trade = result['trade']
                        print(f"[AI Bot] CLOSED: {trade['symbol']} | PnL: ${trade['pnl']:.2f} | {reason}")

                # Yeni pozisyon analizi
                for symbol in self.SYMBOLS:
                    price_data = self.state.prices.get(symbol)
                    if not price_data:
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
                        result = self.state.open_position(
                            symbol=symbol, side=signal,
                            amount=AI_CONFIG["trade_amount"], leverage=leverage
                        )
                        if result.get('status') == 'ok':
                            pos = result['position']
                            print(f"[AI Bot] OPENED: {pos['symbol']} {pos['side'].upper()} {leverage}x | Entry: ${pos['entry']:.2f} | {reason}")

                time.sleep(AI_CONFIG["check_interval"])

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
            url = 'https://api.binance.us/api/v3/ticker/24hr?symbols=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]'
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with request.urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode())
                with self.lock:
                    for item in data:
                        symbol = item['symbol']
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
                    if 'BTC-USDT' in tickers:
                        self.prices["BTCUSDT"] = {"price": float(tickers['BTC-USDT']['last']), "change": float(tickers['BTC-USDT']['changeRate']) * 100}
                    if 'ETH-USDT' in tickers:
                        self.prices["ETHUSDT"] = {"price": float(tickers['ETH-USDT']['last']), "change": float(tickers['ETH-USDT']['changeRate']) * 100}
                    if 'SOL-USDT' in tickers:
                        self.prices["SOLUSDT"] = {"price": float(tickers['SOL-USDT']['last']), "change": float(tickers['SOL-USDT']['changeRate']) * 100}
                    if 'XRP-USDT' in tickers:
                        self.prices["XRPUSDT"] = {"price": float(tickers['XRP-USDT']['last']), "change": float(tickers['XRP-USDT']['changeRate']) * 100}
                    self.update_position_pnl()
                    print("[Price] KuCoin OK")
                    return
        except Exception as e:
            print(f"[Price] KuCoin failed: {e}")
        
        # API 3: CoinGecko (proxy üzerinden)
        try:
            url = 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,ripple&vs_currencies=usd&include_24hr_change=true'
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            with request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                with self.lock:
                    self.prices["BTCUSDT"] = {"price": float(data['bitcoin']['usd']), "change": float(data['bitcoin'].get('usd_24h_change', 0))}
                    self.prices["ETHUSDT"] = {"price": float(data['ethereum']['usd']), "change": float(data['ethereum'].get('usd_24h_change', 0))}
                    self.prices["SOLUSDT"] = {"price": float(data['solana']['usd']), "change": float(data['solana'].get('usd_24h_change', 0))}
                    self.prices["XRPUSDT"] = {"price": float(data['ripple']['usd']), "change": float(data['ripple'].get('usd_24h_change', 0))}
                    self.update_position_pnl()
                    print("[Price] CoinGecko OK")
                    return
        except Exception as e:
            print(f"[Price] CoinGecko failed: {e}")
        
        # API 4: CryptoCompare
        try:
            url = 'https://min-api.cryptocompare.com/data/pricemultifull?fsyms=BTC,ETH,SOL,XRP&tsyms=USD'
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with request.urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode())
                raw = data['RAW']
                with self.lock:
                    self.prices["BTCUSDT"] = {"price": float(raw['BTC']['USD']['PRICE']), "change": float(raw['BTC']['USD'].get('CHANGEPCT24HOUR', 0))}
                    self.prices["ETHUSDT"] = {"price": float(raw['ETH']['USD']['PRICE']), "change": float(raw['ETH']['USD'].get('CHANGEPCT24HOUR', 0))}
                    self.prices["SOLUSDT"] = {"price": float(raw['SOL']['USD']['PRICE']), "change": float(raw['SOL']['USD'].get('CHANGEPCT24HOUR', 0))}
                    self.prices["XRPUSDT"] = {"price": float(raw['XRP']['USD']['PRICE']), "change": float(raw['XRP']['USD'].get('CHANGEPCT24HOUR', 0))}
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

            pos['pnl'] = price_diff * pos['size'] * pos['leverage']
            pos['current_price'] = current_price

            # Hedef fiyatları hesapla (take profit / stop loss)
            entry = pos['entry']
            size = pos['size']
            leverage = pos['leverage']
            margin = pos['margin']
            tp_delta = (margin * AI_CONFIG["take_profit_pct"] / 100) / (size * leverage)
            sl_delta = (margin * AI_CONFIG["stop_loss_pct"] / 100) / (size * leverage)

            if pos['side'] == 'buy':
                pos['tp_price'] = entry + tp_delta
                pos['sl_price'] = entry - sl_delta
            else:
                pos['tp_price'] = entry - tp_delta
                pos['sl_price'] = entry + sl_delta
    
    def open_position(self, symbol, side, amount, leverage=1):
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
                "open_time": datetime.now(TR_TZ).strftime("%H:%M:%S")
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
        total_pnl = sum(p['pnl'] for p in self.positions)
        return self.balance + total_pnl
    
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
