#!/usr/bin/env python3
"""
AI Crypto Trading Dashboard Server
- Real-time prices via Binance REST API
- AI-powered automated trading with LLM
- Paper trading with virtual balance
"""

import json
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
    "max_positions": 3,
    "trade_amount": 100,
    "check_interval": 30,  # 30 saniye
    "stop_loss_pct": 5,
    "take_profit_pct": 10,
}

class AITradingBot:
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

    def __init__(self, state):
        self.state = state
        self.running = False
        self.thread = None
        self.last_analysis = {}
        self.price_history = {s: [] for s in self.SYMBOLS}

    def feed_price(self, symbol, price):
        """Price updater thread'inden fiyat geçmişini besle"""
        if price and price > 0:
            self.price_history[symbol].append(price)
            if len(self.price_history[symbol]) > 50:
                self.price_history[symbol].pop(0)
        
    def analyze_market(self, symbol, price_data):
        """Gelişmiş teknik analiz - RSI, trend, momentum"""
        price = price_data.get('price', 0)
        change = price_data.get('change', 0)
        
        # Basit RSI hesaplama (14 period)
        rsi = self._calculate_rsi(symbol)
        
        # Trend analizi
        trend = self._analyze_trend(symbol)
        
        # Karar mantığı - sinyal gücüne göre kaldıraç (10x-30x)
        # Güçlü sinyal = yüksek kaldıraç, zayıf sinyal = düşük kaldıraç
        if rsi < 40 and trend == "up":
            lev = 25 if rsi < 30 else 20
            return "buy", f"RSI düşük ({rsi:.1f}) + yükseliş trendi", lev
        elif rsi > 60 and trend == "down":
            lev = 25 if rsi > 70 else 20
            return "sell", f"RSI yüksek ({rsi:.1f}) + düşüş trendi", lev
        elif change < -2:
            lev = 30 if change < -4 else 20
            return "buy", f"Dip alım fırsatı (%{change:.2f} düşüş)", lev
        elif change > 2:
            lev = 30 if change > 4 else 20
            return "sell", f"Kar realizasyonu (%{change:.2f} yükseliş)", lev
        elif change > 1 and trend == "up":
            return "buy", f"Momentum pozitif (+{change:.2f}%)", 15
        elif change < -1 and trend == "down":
            return "sell", f"Momentum negatif ({change:.2f}%)", 15
        elif rsi < 45:
            return "buy", f"RSI alım bölgesi ({rsi:.1f})", 10
        elif rsi > 55:
            return "sell", f"RSI satım bölgesi ({rsi:.1f})", 10
        else:
            return "hold", f"Nötr - RSI:{rsi:.1f}, Trend:{trend}", 0
    
    def _calculate_rsi(self, symbol, period=14):
        """Basit RSI hesaplama"""
        prices = self.price_history[symbol]
        if len(prices) < period + 1:
            return 50  # Yetersiz veri
        
        gains = []
        losses = []
        
        for i in range(1, min(period + 1, len(prices))):
            change = prices[-i] - prices[-i-1]
            if change > 0:
                gains.append(change)
            else:
                losses.append(abs(change))
        
        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def _analyze_trend(self, symbol):
        """Basit trend analizi"""
        prices = self.price_history[symbol]
        if len(prices) < 5:
            return "neutral"
        
        # Son 5 fiyatın ortalaması vs önceki 5'in ortalaması
        recent = sum(prices[-5:]) / 5
        previous = sum(prices[-10:-5]) / 5 if len(prices) >= 10 else prices[0]
        
        if recent > previous * 1.02:
            return "up"
        elif recent < previous * 0.98:
            return "down"
        return "neutral"
    
    def should_open_position(self, symbol, signal):
        if len(self.state.positions) >= AI_CONFIG["max_positions"]:
            return False
        for pos in self.state.positions:
            if pos['symbol'] == symbol:
                return False
        return signal in ["buy", "sell"]
    
    def should_close_position(self, position):
        pnl_pct = (position['pnl'] / position['margin']) * 100
        if pnl_pct <= -AI_CONFIG["stop_loss_pct"]:
            return True, "Stop loss triggered"
        if pnl_pct >= AI_CONFIG["take_profit_pct"]:
            return True, "Take profit triggered"
        return False, None
    
    def run(self):
        print("[AI Bot] Starting automated trading...")
        self.running = True
        
        while self.running:
            try:
                self.state.update_prices()
                
                # Stop loss / take profit kontrolü
                positions_to_close = []
                for pos in self.state.positions:
                    should_close, reason = self.should_close_position(pos)
                    if should_close:
                        positions_to_close.append((pos['id'], reason))
                
                for pos_id, reason in positions_to_close:
                    result = self.state.close_position(pos_id)
                    if result.get('status') == 'ok':
                        trade = result['trade']
                        print(f"[AI Bot] Closed: {trade['symbol']} | PnL: ${trade['pnl']:.2f} | {reason}")
                
                # Yeni pozisyon aç (AI analizine göre)
                symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
                for symbol in symbols:
                    price_data = self.state.prices.get(symbol)
                    if not price_data:
                        continue
                    
                    signal, reason, leverage = self.analyze_market(symbol, price_data)
                    self.last_analysis[symbol] = {
                        "signal": signal,
                        "reason": reason,
                        "leverage": leverage,
                        "time": datetime.now(TR_TZ).strftime("%H:%M:%S")
                    }

                    if self.should_open_position(symbol, signal):
                        side = signal
                        result = self.state.open_position(
                            symbol=symbol,
                            side=side,
                            amount=AI_CONFIG["trade_amount"],
                            leverage=leverage
                        )
                        
                        if result.get('status') == 'ok':
                            pos = result['position']
                            print(f"[AI Bot] Opened: {pos['symbol']} {pos['side']} | Entry: ${pos['entry']:.2f}")
                
                time.sleep(AI_CONFIG["check_interval"])
                
            except Exception as e:
                print(f"[AI Bot] Error: {e}")
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
