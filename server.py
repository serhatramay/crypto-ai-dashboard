#!/usr/bin/env python3
"""
AI Crypto Trading Dashboard Server
- Real-time prices via Binance REST API
- Paper trading with virtual balance
- Real position tracking
"""

import json
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

# Turkey timezone (UTC+3)
TR_TZ = timezone(timedelta(hours=3))

# Global state
class PaperTradingState:
    def __init__(self):
        # Paper trading account
        self.balance = 10000.0  # USDT
        self.initial_balance = 10000.0
        
        # Prices
        self.prices = {
            "BTCUSDT": {"price": 84230.50, "change": 2.34},
            "ETHUSDT": {"price": 2456.80, "change": -1.23},
            "SOLUSDT": {"price": 198.45, "change": 5.67},
            "XRPUSDT": {"price": 2.45, "change": -0.89}
        }
        
        # Active positions (no initial positions - user will create them)
        self.positions = []
        
        # Trade history
        self.trades = []
        
        # Settings
        self.ai_status = "idle"
        self.selected_model = "gpt-4"
        self.lock = threading.Lock()
        
    def update_prices(self):
        """Fetch prices from Binance REST API"""
        try:
            url = 'https://api.binance.com/api/v3/ticker/24hr?symbols=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]'
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
                    # Update position PnL based on new prices
                    self.update_position_pnl()
        except Exception as e:
            print(f"[Price Update] Error: {e}")
    
    def update_position_pnl(self):
        """Update PnL for all open positions"""
        for pos in self.positions:
            symbol = pos['symbol']
            current_price = self.prices.get(symbol, {}).get('price', pos['entry'])
            
            if pos['side'] == 'buy':
                # Long position
                price_diff = current_price - pos['entry']
            else:
                # Short position
                price_diff = pos['entry'] - current_price
            
            # PnL = price_diff * size * leverage
            pos['pnl'] = price_diff * pos['size'] * pos['leverage']
            pos['current_price'] = current_price
    
    def open_position(self, symbol, side, amount, leverage=1):
        """Open a new position"""
        with self.lock:
            price = self.prices.get(symbol, {}).get('price', 0)
            if price == 0:
                return {"error": "Price not available"}
            
            # Calculate position size in coin
            size = (amount * leverage) / price
            
            # Check if enough balance
            if amount > self.balance:
                return {"error": "Insufficient balance"}
            
            # Deduct from balance
            self.balance -= amount
            
            # Create position
            position = {
                "id": len(self.positions) + 1,
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
            
            return {
                "status": "ok",
                "position": position,
                "balance": self.balance
            }
    
    def close_position(self, position_id):
        """Close a position"""
        with self.lock:
            pos = None
            for p in self.positions:
                if p['id'] == position_id:
                    pos = p
                    break
            
            if not pos:
                return {"error": "Position not found"}
            
            # Calculate final PnL
            self.update_position_pnl()
            final_pnl = pos['pnl']
            
            # Return margin + PnL to balance
            self.balance += pos['margin'] + final_pnl
            
            # Record trade
            trade = {
                "time": datetime.now(TR_TZ).strftime("%H:%M:%S"),
                "symbol": pos['symbol'],
                "side": pos['side'],
                "entry": pos['entry'],
                "exit": pos['current_price'],
                "pnl": final_pnl,
                "model": self.selected_model
            }
            self.trades.insert(0, trade)
            
            # Remove position
            self.positions.remove(pos)
            
            return {
                "status": "ok",
                "trade": trade,
                "balance": self.balance
            }
    
    def get_equity(self):
        """Calculate total equity (balance + position values)"""
        self.update_position_pnl()
        total_pnl = sum(p['pnl'] for p in self.positions)
        return self.balance + total_pnl
    
    def get_state(self):
        with self.lock:
            self.update_position_pnl()
            equity = self.get_equity()
            total_pnl = equity - self.initial_balance
            total_pnl_pct = (total_pnl / self.initial_balance) * 100 if self.initial_balance else 0
            
            # Calculate win rate from closed trades
            winning_trades = len([t for t in self.trades if t['pnl'] > 0])
            total_trades = len(self.trades)
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            return {
                "prices": self.prices,
                "positions": self.positions,
                "trades": self.trades[:20],  # Last 20 trades
                "balance": self.balance,
                "equity": equity,
                "initial_balance": self.initial_balance,
                "pnl": total_pnl,
                "pnl_pct": round(total_pnl_pct, 2),
                "win_rate": round(win_rate, 1),
                "total_trades": total_trades,
                "ai_status": self.ai_status,
                "selected_model": self.selected_model,
                "timestamp": datetime.now(TR_TZ).isoformat()
            }

state = PaperTradingState()

# Background price updater
def price_updater():
    while True:
        state.update_prices()
        time.sleep(5)

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
            
            # Open new position
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
            
            # Close position
            result = state.close_position(data.get('position_id'))
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
