/**
 * AI Crypto Trading Dashboard - Main Application
 */

// Global state
const state = {
    prices: {},
    selectedSymbol: 'BTCUSDT',
    activeTab: 'dashboard',
    aiStatus: 'idle',
    selectedModel: 'gpt-4'
};

// DOM Elements
const elements = {
    pricesList: document.getElementById('prices-list'),
    tradesTbody: document.getElementById('trades-tbody'),
    equity: document.getElementById('equity'),
    pnl: document.getElementById('pnl'),
    positions: document.getElementById('positions'),
    winRate: document.getElementById('win-rate'),
    chartSymbol: document.getElementById('chart-symbol'),
    tradingviewChart: document.getElementById('tradingview-chart'),
    startBotBtn: document.getElementById('start-bot-btn'),
    paperModeBtn: document.getElementById('paper-mode-btn')
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initPriceFeed();
    initChart();
    initTrading();
    initModels();
    loadInitialData();
});

// Navigation
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const tabContents = document.querySelectorAll('.tab-content');
    
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const tab = item.dataset.tab;
            
            // Update nav
            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');
            
            // Update content
            tabContents.forEach(t => t.classList.remove('active'));
            document.getElementById(`${tab}-tab`).classList.add('active');
            
            state.activeTab = tab;
        });
    });
}

// Price Feed
function initPriceFeed() {
    // Initial render
    renderPrices();
    
    // Poll for updates every 2 seconds
    setInterval(fetchPrices, 2000);
}

async function fetchPrices() {
    try {
        const response = await fetch('/api/prices');
        const prices = await response.json();
        state.prices = prices;
        renderPrices();
        updateStats();
    } catch (err) {
        console.error('Failed to fetch prices:', err);
    }
}

function renderPrices() {
    const symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT'];
    const icons = {
        'BTCUSDT': '₿',
        'ETHUSDT': 'Ξ',
        'SOLUSDT': '◎',
        'XRPUSDT': '✕'
    };
    const names = {
        'BTCUSDT': 'Bitcoin',
        'ETHUSDT': 'Ethereum',
        'SOLUSDT': 'Solana',
        'XRPUSDT': 'XRP'
    };
    
    let html = '';
    symbols.forEach(symbol => {
        const data = state.prices[symbol];
        const price = data ? data.price : 0;
        const change = data ? data.change : 0;
        const changeClass = change >= 0 ? 'up' : 'down';
        const changeSign = change >= 0 ? '+' : '';
        
        html += `
            <div class="price-item" data-symbol="${symbol}">
                <div class="price-symbol">
                    <div class="price-icon">${icons[symbol]}</div>
                    <div>
                        <div class="price-name">${names[symbol]}</div>
                        <div style="font-size: 11px; color: var(--text-muted);">${symbol.replace('USDT', '/USDT')}</div>
                    </div>
                </div>
                <div class="price-value">
                    <div class="price-current">$${price ? price.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '---'}</div>
                    <div class="price-change ${changeClass}">${changeSign}${change ? change.toFixed(2) : '0.00'}%</div>
                </div>
            </div>
        `;
    });
    
    if (elements.pricesList) {
        elements.pricesList.innerHTML = html;
    }
}

// Chart
function initChart() {
    if (elements.chartSymbol) {
        elements.chartSymbol.addEventListener('change', (e) => {
            state.selectedSymbol = e.target.value;
            updateChart();
        });
    }
}

function updateChart() {
    if (elements.tradingviewChart) {
        const symbol = state.selectedSymbol;
        elements.tradingviewChart.src = `https://www.tradingview.com/widgetembed/?frameElementId=tradingview_chart&symbol=BINANCE:${symbol}&interval=15&theme=dark&style=1&locale=en&toolbar_bg=f1f3f6&enable_publishing=false&hide_top_toolbar=false&hide_legend=false&save_image=false&calendar=false&hide_volume=false`;
    }
}

// Trading
function initTrading() {
    // Order form
    const orderForm = document.getElementById('order-form');
    if (orderForm) {
        orderForm.addEventListener('submit', handleOrderSubmit);
    }
    
    // Side buttons
    const sideButtons = document.querySelectorAll('[data-side]');
    sideButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            sideButtons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });
    
    // Start bot button
    if (elements.startBotBtn) {
        elements.startBotBtn.addEventListener('click', toggleBot);
    }
}

async function handleOrderSubmit(e) {
    e.preventDefault();
    
    const symbol = document.getElementById('order-symbol').value;
    const side = document.querySelector('[data-side].active').dataset.side;
    const amount = document.getElementById('order-amount').value;
    const leverage = document.getElementById('order-leverage').value;
    
    const order = {
        symbol,
        side,
        amount: parseFloat(amount),
        leverage: parseInt(leverage),
        timestamp: new Date().toISOString()
    };
    
    try {
        const response = await fetch('/api/trade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(order)
        });
        
        const result = await response.json();
        
        if (result.status === 'ok') {
            showNotification('Order placed successfully!', 'success');
            addTradeToTable(order);
        }
    } catch (err) {
        showNotification('Failed to place order', 'error');
    }
}

function toggleBot() {
    const btn = elements.startBotBtn;
    if (state.aiStatus === 'idle') {
        // Start bot
        fetch('/api/bot/start', { method: 'POST' })
            .then(r => r.json())
            .then(result => {
                if (result.status === 'ok') {
                    state.aiStatus = 'running';
                    btn.innerHTML = '⏸ Stop Bot';
                    btn.classList.remove('btn-primary');
                    btn.classList.add('btn-secondary');
                    showNotification('AI Bot started - Automated trading active!', 'success');
                }
            });
    } else {
        // Stop bot
        fetch('/api/bot/stop', { method: 'POST' })
            .then(r => r.json())
            .then(result => {
                if (result.status === 'ok') {
                    state.aiStatus = 'idle';
                    btn.innerHTML = '▶ Start Bot';
                    btn.classList.remove('btn-secondary');
                    btn.classList.add('btn-primary');
                    showNotification('AI Bot stopped', 'info');
                }
            });
    }
}

// Models
function initModels() {
    const selectButtons = document.querySelectorAll('.select-model-btn');
    selectButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const model = btn.dataset.model;
            selectModel(model);
        });
    });
}

async function selectModel(model) {
    try {
        const response = await fetch('/api/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        
        const result = await response.json();
        
        if (result.status === 'ok') {
            state.selectedModel = model;
            
            // Update UI
            document.querySelectorAll('.model-card').forEach(card => {
                card.classList.remove('active');
                const status = card.querySelector('.model-status');
                const btn = card.querySelector('.select-model-btn');
                
                if (card.dataset.model === model) {
                    status.textContent = 'Active';
                    status.classList.add('active');
                    btn.classList.remove('btn-secondary');
                    btn.classList.add('btn-primary');
                    btn.textContent = 'Selected';
                } else {
                    status.textContent = 'Available';
                    status.classList.remove('active');
                    btn.classList.remove('btn-primary');
                    btn.classList.add('btn-secondary');
                    btn.textContent = 'Select Model';
                }
            });
            
            showNotification(`AI Model changed to ${model}`, 'success');
        }
    } catch (err) {
        showNotification('Failed to change model', 'error');
    }
}

// Close position function
async function closePosition(positionId) {
    try {
        const response = await fetch('/api/close', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ position_id: positionId })
        });
        
        const result = await response.json();
        
        if (result.status === 'ok') {
            showNotification(`Position closed! P&L: ${result.trade.pnl >= 0 ? '+' : ''}$${Math.abs(result.trade.pnl).toFixed(2)}`, 'success');
            // Refresh data
            loadInitialData();
        } else {
            showNotification('Failed to close position: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showNotification('Failed to close position', 'error');
    }
}

// Stats & Data
function updateStats() {
    // Calculate stats from state
    const equity = state.equity || 10000;
    const pnl = state.pnl || 0;
    const pnlPct = state.pnl_pct || 0;
    
    if (elements.equity) {
        elements.equity.textContent = `$${equity.toFixed(2)}`;
    }
    
    if (elements.pnl) {
        elements.pnl.textContent = `${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toFixed(2)}`;
        elements.pnl.className = 'stat-value ' + (pnl >= 0 ? 'positive' : 'negative');
    }
    
    // Update P&L percentage
    const pnlChangeEl = document.getElementById('pnl-change');
    if (pnlChangeEl) {
        pnlChangeEl.textContent = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
        pnlChangeEl.className = 'stat-change ' + (pnlPct >= 0 ? 'positive' : 'negative');
    }
}

function addTradeToTable(trade) {
    const row = document.createElement('tr');
    row.innerHTML = `
        <td>${new Date().toLocaleTimeString()}</td>
        <td>${trade.symbol}</td>
        <td><span class="trade-type ${trade.side === 'buy' ? 'long' : 'short'}">${trade.side.toUpperCase()}</span></td>
        <td>$${(Math.random() * 50000 + 30000).toFixed(2)}</td>
        <td>--</td>
        <td class="trade-pnl">--</td>
        <td>${state.selectedModel}</td>
    `;
    
    if (elements.tradesTbody) {
        elements.tradesTbody.insertBefore(row, elements.tradesTbody.firstChild);
        
        // Keep only last 20 rows
        while (elements.tradesTbody.children.length > 20) {
            elements.tradesTbody.removeChild(elements.tradesTbody.lastChild);
        }
    }
}

function loadInitialData() {
    // Load initial trades from server state
    fetch('/api/state')
        .then(r => r.json())
        .then(data => {
            const trades = data.trades || [];
            
            if (elements.tradesTbody && trades.length > 0) {
                elements.tradesTbody.innerHTML = trades.map(t => `
                    <tr>
                        <td>${t.time}</td>
                        <td>${t.symbol}</td>
                        <td><span class="trade-type ${t.side === 'buy' ? 'long' : 'short'}">${t.side.toUpperCase()}</span></td>
                        <td>$${t.entry.toFixed(2)}</td>
                        <td>$${t.exit.toFixed(2)}</td>
                        <td class="trade-pnl ${t.pnl >= 0 ? 'positive' : 'negative'}">${t.pnl >= 0 ? '+' : ''}$${Math.abs(t.pnl).toFixed(2)}</td>
                        <td>${t.model}</td>
                    </tr>
                `).join('');
                
                // Calculate real win rate
                const winningTrades = trades.filter(t => t.pnl > 0).length;
                const winRate = trades.length > 0 ? (winningTrades / trades.length * 100).toFixed(1) : 0;
                
                if (elements.winRate) {
                    elements.winRate.textContent = winRate + '%';
                }
                
                const tradesCountEl = document.getElementById('trades-count');
                if (tradesCountEl) {
                    tradesCountEl.textContent = trades.length + ' trades';
                }
            }
            
            // Update positions count and render positions
            if (data.positions) {
                const positionsCountEl = document.getElementById('positions');
                if (positionsCountEl) {
                    positionsCountEl.textContent = data.positions.length;
                }
                
                // Render positions in Trading tab
                const positionsList = document.getElementById('positions-list');
                if (positionsList) {
                    if (data.positions && data.positions.length > 0) {
                        positionsList.innerHTML = data.positions.map(p => `
                            <div class="position-item">
                                <div class="position-header">
                                    <span class="position-symbol">${p.symbol}</span>
                                    <span class="position-side ${p.side}">${p.side.toUpperCase()}</span>
                                </div>
                                <div class="position-details">
                                    <div class="position-detail">
                                        <span class="position-detail-label">Entry</span>
                                        <span class="position-detail-value">$${p.entry.toFixed(2)}</span>
                                    </div>
                                    <div class="position-detail">
                                        <span class="position-detail-label">Current</span>
                                        <span class="position-detail-value">$${p.current_price ? p.current_price.toFixed(2) : p.entry.toFixed(2)}</span>
                                    </div>
                                    <div class="position-detail">
                                        <span class="position-detail-label">Size</span>
                                        <span class="position-detail-value">${p.size.toFixed(4)}</span>
                                    </div>
                                    <div class="position-detail">
                                        <span class="position-detail-label">Leverage</span>
                                        <span class="position-detail-value">${p.leverage}x</span>
                                    </div>
                                    <div class="position-detail">
                                        <span class="position-detail-label">P&L</span>
                                        <span class="position-detail-value" style="color: ${p.pnl >= 0 ? 'var(--accent-success)' : 'var(--accent-danger)'}">${p.pnl >= 0 ? '+' : ''}$${Math.abs(p.pnl).toFixed(2)}</span>
                                    </div>
                                </div>
                                <button class="btn btn-secondary btn-sm btn-block" onclick="closePosition(${p.id})" style="margin-top: 12px;">Close Position</button>
                            </div>
                        `).join('');
                    } else {
                        positionsList.innerHTML = '<div class="empty-state">No open positions</div>';
                    }
                }
            }
        })
        .catch(err => console.error('Failed to load initial data:', err));
}

// Notifications
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 16px 24px;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 500;
        z-index: 10000;
        animation: slideIn 0.3s ease;
        ${type === 'success' ? 'background: rgba(16, 185, 129, 0.9);' : 
          type === 'error' ? 'background: rgba(239, 68, 68, 0.9);' : 
          'background: rgba(59, 130, 246, 0.9);'}
        color: white;
    `;
    notification.textContent = message;
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// Add animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from { transform: translateX(100%); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOut {
        from { transform: translateX(0); opacity: 1; }
        to { transform: translateX(100%); opacity: 0; }
    }
`;
document.head.appendChild(style);
