#!/usr/bin/env python3
"""
AI Crypto Trading Dashboard Server
- Real-time prices via Binance WebSocket
- AI trading bot integration
- Live PnL tracking
"""

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver
import urllib.parse

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Global state
class DashboardState:
    def __init__(self):
        self.prices = {}
        self.positions = []
        self.trades = []
        self.pnl = 0.0
        self.equity = 10000.0
        self.ai_status = "idle"
        self.selected_model = "gpt-4"
        self.lock = threading.Lock()
        
    def update_price(self, symbol, price):
        with self.lock:
            self.prices[symbol] = {
                "price": price,
                "timestamp": datetime.now().isoformat()
            }
    
    def get_state(self):
        with self.lock:
            return {
                "prices": self.prices,
                "positions": self.positions,
                "trades": self.trades[-20:],  # Last 20 trades
                "pnl": self.pnl,
                "equity": self.equity,
                "ai_status": self.ai_status,
                "selected_model": self.selected_model,
                "timestamp": datetime.now().isoformat()
            }

state = DashboardState()

# Binance WebSocket for real-time prices
class BinancePriceFeed:
    def __init__(self):
        self.symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
        self.running = False
        
    async def connect(self):
        import websockets
        streams = "/".join([f"{s.lower()}@ticker" for s in self.symbols])
        uri = f"wss://stream.binance.com:9443/ws/{streams}"
        
        while self.running:
            try:
                async with websockets.connect(uri) as ws:
                    print(f"[Binance] Connected to price feed")
                    while self.running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        symbol = data.get("s", "")
                        price = float(data.get("c", 0))
                        if symbol and price:
                            state.update_price(symbol, price)
            except Exception as e:
                print(f"[Binance] Error: {e}, reconnecting...")
                await asyncio.sleep(5)
    
    def start(self):
        self.running = True
        asyncio.run(self.connect())

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
            # Process trade
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
        pass  # Suppress logs

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    allow_reuse_address = True

def start_price_feed():
    feed = BinancePriceFeed()
    thread = threading.Thread(target=feed.start, daemon=True)
    thread.start()
    return feed

def main():
    port = int(os.environ.get("PORT", 8765))
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Start price feed
    print("[Server] Starting Binance price feed...")
    start_price_feed()
    
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
