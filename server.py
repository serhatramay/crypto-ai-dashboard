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

# AI Trading Configuration
AI_CONFIG = {
    "max_positions": 3,  # Max concurrent positions
    "trade_amount": 100,  # USDT per trade
    "leverage": 2,
    "check_interval": 60,  # Check every 60 seconds
    "stop_loss_pct": 5,
    "take_profit_pct": 10,
}

class AITradingBot:
    def __init__(self, state):
        self.state = state
        self.running = False
        self.thread = None
        self.last_analysis = {}
        
    def analyze_market(self, symbol, price_data):
        """Simple technical analysis"""
        change = price_data.get('change', 0)
        price = price_data.get('price', 0)
        
        # Simple strategy based on price change
        if change > 2:
            return "buy", f"Strong upward momentum (+{change:.2f}%)"
        elif change < -2:
            return "sell", f"Strong downward momentum ({change:.2f}%)"
        elif change > 0.5:
            return "buy", f"Positive momentum (+{change:.2f}%)"
        elif change < -0.5:
            return "sell", f"Negative momentum ({change:.2f}%)"
        else:
            return "hold", f"Sideways movement ({change:.2f}%)"
    
    def should_open_position(self, symbol, signal):
        """Check if we should open a position"""
        # Check max positions
        if len(self.state.positions) >= AI_CONFIG["max_positions"]:
            return False
        
        # Check if already have position for this symbol
        for pos in self.state.positions:
            if pos['symbol'] == symbol:
                return False
        
        # Check signal
        return signal in ["buy", "sell"]
    
    def should_close_position(self, position):
        """Check if we should close a position"""
        pnl_pct = (position['pnl'] / position['margin']) * 100
        
        # Stop loss
        if pnl_pct <= -AI_CONFIG["stop_loss_pct"]:
            return True, "Stop loss triggered"
        
        # Take profit
        if pnl_pct >= AI_CONFIG["take_profit_pct"]:
            return True, "Take profit triggered"
        
        return False, None
    
    def run(self):
        """Main bot loop"""
        print("[AI Bot] Starting automated trading...")
        self.running = True
        
        while self.running:
            try:
                # Update prices first
                self.state.update_prices()
                
                # Check existing positions for close signals
                positions_to_close = []
                for pos in self.state.positions:
                    should_close, reason = self.should_close_position(pos)
                    if should_close:
                        positions_to_close.append((pos['id'], reason))
                
                # Close positions
                for pos_id, reason in positions_to_close:
                    result = self.state.close_position(pos_id)
                    if result.get('status') == 'ok':
                        trade = result['trade']
                        print(f"[AI Bot] Closed position: {trade['symbol']} {trade['side']} | PnL: ${trade['pnl']:.2f} | Reason: {reason}")
                
                # Analyze each symbol for new positions
                symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
                for symbol in symbols:
                    price_data = self.state.prices.get(symbol)
                    if not price_data:
                        continue
                    
                    signal, reason = self.analyze_market(symbol, price_data)
                    self.last_analysis[symbol] = {
                        "signal": signal,
                        "reason": reason,
                        "time": datetime.now(TR_TZ).strftime("%H:%M:%S")
                    }
                    
                    # Open position if signal is strong
                    if self.should_open_position(symbol, signal):
                        side = signal  # buy or sell
                        result = self.state.open_position(
                            symbol=symbol,
                            side=side,
                            amount=AI_CONFIG["trade_amount"],
                            leverage=AI_CONFIG["leverage"]
                        )
                        
                        if result.get('status') == 'ok':
                            pos = result['position']
                            print(f"[AI Bot] Opened position: {pos['symbol']} {pos['side']} | Entry: ${pos['entry']:.2f} | Reason: {reason}")
                
                # Wait before next check
                time.sleep(AI_CONFIG["check_interval"])
                
            except Exception as e:
                print(f"[AI Bot] Error: {e}")
                time.sleep(10)
    
    def start(self):
        """Start the bot in a separate thread"""
        if not self.running:
            self.state.ai_status = "running"
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            return True
        return False
    
    def stop(self):
        """Stop the bot"""
        self.running = False
        self.state.ai_status = "idle"
        return True

# Global state
class PaperTradingState:
    def __init__(self):
        self.balance = 10000.0
        self.initial_balance = 10000.0
        self.prices = {
            "BTCUSDT": {"price": 84230.50, "change": 2.34},
            "ETHUSDT": {"price": 2456.80, "change": -1.23},
            "SOLUSDT": {"price": 198.45, "change": 5.67},
            "XRPUSDT": {"price": 2.45, "change": -0.89}
        }
        self.positions = []
        self.trades = []
        self.ai_status = "idle"
        self.selected_model = "gpt-4"
        self.lock = threading.Lock()
        self.ai_bot = AITradingBot(self)
        
    def update_prices(self):
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
                    self.update_position_pnl()
        except Exception as e:
            print(f"[Price Update] Error: {e}")
    
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
    
    def open_position(self, symbol, side, amount, leverage=1):
        with self.lock:
            price = self.prices.get(symbol, {}).get('price', 0)
            if price == 0:
                return {"error": "Price not available"}
            
            size = (amount * leverage) / price
            
            if amount > self.balance:
                return {"error": "Insufficient balance"}
            
            self.balance -= amount
            
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
                "symbol": pos['symbol'],
                "side": pos['side'],
                "entry": pos['entry'],
                "exit": pos['current_price'],
                "pnl": final_pnl,
                "model": self.selected_model
            }
            self.trades.insert(0, trade)
            
            self.positions.remove(pos)
            
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
                "trades": self.trades[:20],
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
        time.sleep(5)

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
    
    print("[Server] Starting price updater...")
    updater_thread = threading.Thread(target=price_updater, daemon=True)
    updater_thread.start()
    
    server = ThreadedHTTPServer((host, port), DashboardHandler)
    print(f"[Server] Dashboard running at http://{host}:{port}")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
