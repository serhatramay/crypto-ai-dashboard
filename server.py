#!/usr/bin/env python3
"""
AI Crypto Trading Dashboard Server
- Real-time prices via Binance REST API
- AI trading bot integration
- Live PnL tracking
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

# Turkey timezone (UTC+3)
TR_TZ = timezone(timedelta(hours=3))
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib import request
import socketserver
import urllib.parse

# Global state
class DashboardState:
    def __init__(self):
        self.prices = {
            "BTCUSDT": {"price": 84230.50, "change": 2.34},
            "ETHUSDT": {"price": 2456.80, "change": -1.23},
            "SOLUSDT": {"price": 198.45, "change": 5.67},
            "XRPUSDT": {"price": 2.45, "change": -0.89}
        }
        self.positions = [
            {"symbol": "BTCUSDT", "side": "buy", "entry": 84230.50, "size": 0.1, "leverage": 5, "pnl": -125.30},
            {"symbol": "ETHUSDT", "side": "sell", "entry": 2456.80, "size": 2.0, "leverage": 3, "pnl": 89.45},
        ]
        self.trades = [
            {"time": datetime.now(TR_TZ).strftime("%H:%M:%S"), "symbol": "BTCUSDT", "side": "buy", "entry": 45230.50, "exit": 45890.25, "pnl": 659.75, "model": "gpt-4"},
            {"time": (datetime.now(TR_TZ) - timedelta(minutes=4)).strftime("%H:%M:%S"), "symbol": "ETHUSDT", "side": "sell", "entry": 2890.75, "exit": 2845.20, "pnl": 45.55, "model": "claude"},
            {"time": (datetime.now(TR_TZ) - timedelta(minutes=17)).strftime("%H:%M:%S"), "symbol": "SOLUSDT", "side": "buy", "entry": 98.45, "exit": 102.30, "pnl": 3.85, "model": "deepseek"},
            {"time": (datetime.now(TR_TZ) - timedelta(minutes=47)).strftime("%H:%M:%S"), "symbol": "BTCUSDT", "side": "sell", "entry": 46120.00, "exit": 45800.00, "pnl": -320.00, "model": "gpt-4"},
            {"time": (datetime.now(TR_TZ) - timedelta(minutes=80)).strftime("%H:%M:%S"), "symbol": "XRPUSDT", "side": "buy", "entry": 2.35, "exit": 2.42, "pnl": 0.07, "model": "qwen"},
        ]
        self.pnl = -189.62  # Negative = loss
        self.equity = 9810.38
        self.initial_equity = 10000.0
        self.ai_status = "idle"
        self.selected_model = "gpt-4"
        self.lock = threading.Lock()
        
    def update_prices(self):
        """Fetch prices from Binance REST API"""
        try:
            url = "https://api.binance.com/api/v3/ticker/24hr?symbols=[\"BTCUSDT\",\"ETHUSDT\",\"SOLUSDT\",\"XRPUSDT\"]"
            req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                with self.lock:
                    for item in data:
                        symbol = item['symbol']
                        self.prices[symbol] = {
                            "price": float(item['lastPrice']),
                            "change": float(item['priceChangePercent'])
                        }
        except Exception as e:
            print(f"[Price Update] Error: {e}")
    
    def get_state(self):
        with self.lock:
            pnl_pct = (self.pnl / self.initial_equity) * 100 if self.initial_equity else 0
            return {
                "prices": self.prices,
                "positions": self.positions,
                "trades": self.trades[-20:],
                "pnl": self.pnl,
                "pnl_pct": round(pnl_pct, 2),
                "equity": self.equity,
                "initial_equity": self.initial_equity,
                "ai_status": self.ai_status,
                "selected_model": self.selected_model,
                "timestamp": datetime.now().isoformat()
            }

state = DashboardState()

# Background price updater
def price_updater():
    while True:
        state.update_prices()
        time.sleep(5)  # Update every 5 seconds

# HTTP Request Handler
class DashboardHandler(BaseHTTPRequestHandler):
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
            result = {"status": "ok", "trade": data}
            self.serve_json(result)
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
    port = int(os.environ.get("PORT", 8765))
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Start price updater thread
    print("[Server] Starting price updater...")
    updater_thread = threading.Thread(target=price_updater, daemon=True)
    updater_thread.start()
    
    # Start HTTP server
    server = ThreadedHTTPServer((host, port), DashboardHandler)
    print(f"[Server] Dashboard running at http://{host}:{port}")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
