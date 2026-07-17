"""
Endpoint:
  GET  /          → Halaman web monitoring
  POST /update    → Kirim data dari ESP32
  GET  /data      → Ambil data JSON terkini

python app.py
"""

from flask import Flask, request, jsonify, Response
from datetime import datetime

app = Flask(__name__)

# Data sensor (nilai default untuk demo)
sensor_data = {
    "organic": 45.0,
    "inorganic": 72.0,
    "last_updated": None
}

#  HTML TEMPLATE
HTML = r"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>SmartBin Monitor</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#060b06;--card:#0a1309;
  --gc:#39d353;--gd:#0f3b1a;--gg:rgba(57,211,83,.28);
  --bc:#38bdf8;--bd:#073b54;--bg2:rgba(56,189,248,.28);
  --txt:#c8e6c8;--txt2:#547754;
  --warn:#f59e0b;--danger:#ef4444;--orange:#f97316;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:var(--bg);color:var(--txt);font-family:'Rajdhani',sans-serif;overflow-x:hidden;min-height:100%}

/* ── ANIMATED BG ── */
.bg-grid{
  position:fixed;inset:0;z-index:0;pointer-events:none;
  background-image:
    linear-gradient(rgba(57,211,83,.045) 1px,transparent 1px),
    linear-gradient(90deg,rgba(57,211,83,.045) 1px,transparent 1px);
  background-size:36px 36px;
  animation:grid-pan 24s linear infinite;
}
@keyframes grid-pan{to{background-position:36px 36px}}
.scanlines{
  position:fixed;inset:0;z-index:1;pointer-events:none;
  background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,.04) 3px,rgba(0,0,0,.04) 4px);
}

/* ── LAYOUT ── */
.app{position:relative;z-index:2;max-width:430px;margin:0 auto;padding:0 14px 28px}

/* ── BINS GRID (mobile default: stacked) ── */
.bins-grid{display:flex;flex-direction:column}

/* ── DESKTOP LAYOUT ── */
@media (min-width:900px){
  .app{max-width:1180px;padding:0 32px 40px}

  .header{margin-bottom:26px}
  .h-title{font-size:16px}
  .h-sub{font-size:11px}

  .bins-grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:24px;
    align-items:stretch;
  }
  .bin-card{margin-bottom:0}

  .card-body{gap:26px;align-items:center}

  .card-body svg{width:150px;height:280px}

  .big-num{font-size:60px}
  .big-unit{font-size:20px}

  .sbar{padding:13px 18px}
  .last-upd{font-size:9px}
}

/* ── HEADER ── */
.header{
  padding:14px 0 12px;
  display:flex;align-items:center;justify-content:space-between;
  border-bottom:1px solid rgba(57,211,83,.16);
  margin-bottom:18px;
}
.h-left{display:flex;align-items:center;gap:10px}
.h-icon{
  width:42px;height:42px;
  background:rgba(57,211,83,.09);border:1px solid rgba(57,211,83,.3);
  border-radius:11px;display:flex;align-items:center;justify-content:center;
  font-size:20px;position:relative;overflow:hidden;
}
.h-icon::after{
  content:'';position:absolute;inset:0;
  background:conic-gradient(transparent 0deg,rgba(57,211,83,.18) 90deg,transparent 90deg);
  animation:spin 5s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}
.h-title{font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;color:var(--gc);letter-spacing:2.5px}
.h-sub{font-size:10px;color:var(--txt2);letter-spacing:1.5px;margin-top:1px}
.live-badge{
  display:flex;align-items:center;gap:7px;
  background:rgba(57,211,83,.07);border:1px solid rgba(57,211,83,.22);
  border-radius:20px;padding:5px 11px;
}
.led{
  width:7px;height:7px;border-radius:50%;
  background:var(--gc);box-shadow:0 0 8px var(--gc);
  animation:blink 1.6s ease-in-out infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
.live-txt{font-family:'Orbitron',sans-serif;font-size:9px;font-weight:700;color:var(--gc);letter-spacing:2px}

/* ── BIN CARD ── */
.bin-card{
  background:var(--card);
  border-radius:18px;border:1px solid rgba(57,211,83,.18);
  padding:15px;margin-bottom:16px;position:relative;overflow:hidden;
}
.bin-card::before{
  content:'';position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(ellipse 60% 40% at 20% 10%,rgba(57,211,83,.07),transparent);
}
.bin-card.inorganic{border-color:rgba(56,189,248,.18)}
.bin-card.inorganic::before{
  background:radial-gradient(ellipse 60% 40% at 20% 10%,rgba(56,189,248,.07),transparent);
}
/* corner brackets */
.brkt{position:absolute;width:20px;height:20px;pointer-events:none}
.brkt.tl{top:9px;left:9px;border-top:2px solid rgba(57,211,83,.32);border-left:2px solid rgba(57,211,83,.32)}
.brkt.br{bottom:9px;right:9px;border-bottom:2px solid rgba(57,211,83,.32);border-right:2px solid rgba(57,211,83,.32)}
.bin-card.inorganic .brkt{border-color:rgba(56,189,248,.32)}

.card-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}
.card-lbl{font-family:'Orbitron',sans-serif;font-size:10px;font-weight:700;letter-spacing:3px;color:var(--gc)}
.bin-card.inorganic .card-lbl{color:var(--bc)}

.status-pill{
  font-family:'Orbitron',sans-serif;font-size:8px;font-weight:700;
  letter-spacing:2px;padding:4px 11px;border-radius:30px;
  transition:all .5s;
}

.card-body{display:flex;align-items:flex-start;gap:15px}

/* ── STATS PANEL ── */
.stats{flex:1;display:flex;flex-direction:column;gap:13px;padding-top:5px}
.big-lbl{font-size:9px;color:var(--txt2);letter-spacing:2px;text-transform:uppercase;margin-bottom:5px}
.big-num{
  font-family:'Orbitron',sans-serif;font-size:50px;font-weight:900;
  color:var(--gc);line-height:1;transition:color .5s,text-shadow .5s;
  text-shadow:0 0 28px rgba(57,211,83,.45);
}
.bin-card.inorganic .big-num{color:var(--bc);text-shadow:0 0 28px rgba(56,189,248,.45)}
.big-unit{font-family:'Orbitron',sans-serif;font-size:18px;font-weight:400;color:var(--txt2)}

.prog-labels{display:flex;justify-content:space-between;font-size:9px;color:var(--txt2);letter-spacing:1px;margin-bottom:5px}
.prog-track{height:5px;background:rgba(255,255,255,.07);border-radius:3px;overflow:hidden}
.prog-fill{
  height:100%;border-radius:3px;width:0%;
  transition:width 1.3s cubic-bezier(.25,.46,.45,.94),background .5s;
}
.prog-fill.org{background:linear-gradient(90deg,var(--gd),var(--gc))}
.prog-fill.inorg{background:linear-gradient(90deg,var(--bd),var(--bc))}

.chips{display:flex;gap:8px}
.chip{
  flex:1;background:rgba(255,255,255,.03);
  border:1px solid rgba(255,255,255,.06);border-radius:10px;
  padding:8px 6px;text-align:center;
}
.chip-lbl{display:block;font-size:8px;color:var(--txt2);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:3px}
.chip-val{font-family:'Orbitron',sans-serif;font-size:11px;font-weight:600;color:var(--txt);transition:color .5s}

/* ── SVG BIN ANIMATIONS ── */
.wave-path{animation:wave-go 2.6s linear infinite}
.wave-path-b{animation:wave-go 3.8s linear infinite reverse}
@keyframes wave-go{
  from{transform:translateX(0px)}
  to{transform:translateX(-160px)}
}
.wg{transition:transform 1.4s cubic-bezier(.25,.46,.45,.94)}

.bubble{animation:rise linear infinite}
@keyframes rise{
  0%  {transform:translateY(0);opacity:0}
  8%  {opacity:.7}
  92% {opacity:.25}
  100%{transform:translateY(-185px);opacity:0}
}

/* ── STATUS BAR ── */
.sbar{
  background:rgba(6,11,6,.85);border:1px solid rgba(57,211,83,.1);
  border-radius:14px;padding:11px 14px;
  display:flex;align-items:center;gap:10px;margin-bottom:10px;
}
.sbar-txt{font-family:'Orbitron',sans-serif;font-size:9px;color:var(--txt2);letter-spacing:1.5px;white-space:nowrap}
.cbar{flex:1;height:3px;background:rgba(255,255,255,.06);border-radius:2px;overflow:hidden}
.cbar-fill{height:100%;background:var(--gc);border-radius:2px;width:100%;transition:width 1s linear}
.last-upd{font-family:'Orbitron',sans-serif;font-size:8px;color:var(--txt2);letter-spacing:1px;text-align:center;padding:0 4px}

/* ── FLASH ON UPDATE ── */
@keyframes flash-g{
  0%{box-shadow:none}
  45%{box-shadow:0 0 0 1px rgba(57,211,83,.5),0 0 30px rgba(57,211,83,.25)}
  100%{box-shadow:none}
}
@keyframes flash-b{
  0%{box-shadow:none}
  45%{box-shadow:0 0 0 1px rgba(56,189,248,.5),0 0 30px rgba(56,189,248,.25)}
  100%{box-shadow:none}
}
.flash-g{animation:flash-g .65s ease}
.flash-b{animation:flash-b .65s ease}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="scanlines"></div>
<div class="app">

  <!-- ── HEADER ── -->
  <header class="header">
    <div class="h-left">
      <div class="h-icon">🗑</div>
      <div>
        <div class="h-title">SmartBin</div>
        <div class="h-sub">Monitor Volume</div>
      </div>
    </div>
    <div class="live-badge">
      <div class="led" id="conn-led"></div>
      <span class="live-txt">LIVE</span>
    </div>
  </header>

  <div class="bins-grid">

  <!-- ══════════════════════════════════════════
       TONG ORGANIK
  ══════════════════════════════════════════ -->
  <div class="bin-card" id="card-org">
    <div class="brkt tl"></div><div class="brkt br"></div>
    <div class="card-top">
      <span class="card-lbl">♻ ORGANIK</span>
      <span class="status-pill" id="pill-org">—</span>
    </div>
    <div class="card-body">

      <!-- SVG BIN — ORGANIK -->
      <div>
        <svg width="126" height="236" viewBox="0 0 126 236" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <clipPath id="clip-org">
              <!-- Interior trapezoid of bin -->
              <polygon points="17,42 109,42 99,213 27,213"/>
            </clipPath>
          </defs>

          <!-- Clipped liquid area -->
          <g clip-path="url(#clip-org)">
            <!-- Dark bin interior bg -->
            <rect width="126" height="236" fill="#061206"/>
            <!-- Liquid wave group — positioned via JS -->
            <g class="wg" id="wg-org" style="transform:translateY(213px)">
              <!-- Primary wave fill -->
              <path class="wave-path"
                d="M-160,0 C-140,-9 -120,9 -80,0 C-60,-9 -40,9 0,0
                   C20,-9 40,9 80,0 C100,-9 120,9 160,0
                   C180,-9 200,9 240,0 C260,-9 280,9 320,0
                   L320,230 L-160,230 Z"
                fill="#22c55e" opacity="0.82"/>
              <!-- Secondary wave (depth) -->
              <path class="wave-path-b"
                d="M-160,4 C-140,-5 -120,13 -80,4 C-60,-5 -40,13 0,4
                   C20,-5 40,13 80,4 C100,-5 120,13 160,4
                   C180,-5 200,13 240,4 C260,-5 280,13 320,4
                   L320,230 L-160,230 Z"
                fill="#15803d" opacity="0.55"/>
            </g>
            <!-- Bubbles (hidden when empty) -->
            <circle class="bubble" id="ob1" cx="40"  cy="190" r="3.2" fill="rgba(74,222,128,.65)" style="animation-duration:3.3s;animation-delay:0.0s"/>
            <circle class="bubble" id="ob2" cx="67"  cy="197" r="2.0" fill="rgba(74,222,128,.5)"  style="animation-duration:4.1s;animation-delay:1.3s"/>
            <circle class="bubble" id="ob3" cx="82"  cy="202" r="1.5" fill="rgba(74,222,128,.4)"  style="animation-duration:2.9s;animation-delay:0.6s"/>
            <circle class="bubble" id="ob4" cx="51"  cy="207" r="2.7" fill="rgba(74,222,128,.55)" style="animation-duration:4.7s;animation-delay:2.2s"/>
          </g>

          <!-- 50% dashed guide line (inside, behind outline) -->
          <line x1="22" y1="127" x2="104" y2="127" stroke="#39d353" stroke-width="0.6" stroke-dasharray="3,5" opacity="0.18"/>

          <!-- Graduation marks (outside clip, always visible) -->
          <line x1="107" y1="84"  x2="114" y2="84"  stroke="#39d353" stroke-width="1.2" opacity="0.4"/>
          <text x="116" y="88"  font-family="Rajdhani" font-size="8" font-weight="600" fill="#39d353" opacity="0.4">75</text>
          <line x1="104" y1="127" x2="111" y2="127" stroke="#39d353" stroke-width="1.2" opacity="0.4"/>
          <text x="113" y="131" font-family="Rajdhani" font-size="8" font-weight="600" fill="#39d353" opacity="0.4">50</text>
          <line x1="101" y1="171" x2="108" y2="171" stroke="#39d353" stroke-width="1.2" opacity="0.4"/>
          <text x="110" y="175" font-family="Rajdhani" font-size="8" font-weight="600" fill="#39d353" opacity="0.4">25</text>

          <!-- Bin body outline -->
          <polygon points="17,42 109,42 99,213 27,213"
            fill="none" stroke="#39d353" stroke-width="1.8" opacity="0.85"/>

          <!-- Lid -->
          <rect x="9" y="24" width="108" height="20" rx="10"
            fill="#061806" stroke="#39d353" stroke-width="1.5" opacity="0.92"/>

          <!-- Handle -->
          <rect x="38" y="10" width="50" height="16" rx="8"
            fill="#030e03" stroke="#39d353" stroke-width="1.5"/>

          <!-- Lid icon -->
          <text x="63" y="37" text-anchor="middle" font-size="12" fill="#39d353" opacity="0.6">♻</text>

          <!-- Corner dots -->
          <circle cx="17"  cy="42"  r="2.5" fill="#39d353" opacity="0.7"/>
          <circle cx="109" cy="42"  r="2.5" fill="#39d353" opacity="0.7"/>
          <circle cx="27"  cy="213" r="2.5" fill="#39d353" opacity="0.7"/>
          <circle cx="99"  cy="213" r="2.5" fill="#39d353" opacity="0.7"/>

          <!-- Percentage overlay (fixed center position) -->
          <rect x="22" y="109" width="82" height="30" rx="6" fill="rgba(0,0,0,.58)"/>
          <text id="org-pct" x="63" y="129" text-anchor="middle"
            font-family="Orbitron" font-size="18" font-weight="700" fill="white">—</text>
        </svg>
      </div>

      <!-- Stats -->
      <div class="stats">
        <div>
          <div class="big-lbl">Volume Terisi</div>
          <div><span class="big-num" id="num-org">—</span><span class="big-unit">%</span></div>
        </div>
        <div>
          <div class="prog-labels"><span>Kosong</span><span>Penuh</span></div>
          <div class="prog-track"><div class="prog-fill org" id="bar-org"></div></div>
        </div>
        <div class="chips">
          <div class="chip">
            <span class="chip-lbl">Sisa</span>
            <span class="chip-val" id="sisa-org">—</span>
          </div>
          <div class="chip">
            <span class="chip-lbl">Status</span>
            <span class="chip-val" id="stat-org">—</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ══════════════════════════════════════════
       TONG ANORGANIK
  ══════════════════════════════════════════ -->
  <div class="bin-card inorganic" id="card-inorg">
    <div class="brkt tl"></div><div class="brkt br"></div>
    <div class="card-top">
      <span class="card-lbl">⬡ ANORGANIK</span>
      <span class="status-pill" id="pill-inorg">—</span>
    </div>
    <div class="card-body">

      <!-- SVG BIN — ANORGANIK -->
      <div>
        <svg width="126" height="236" viewBox="0 0 126 236" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <clipPath id="clip-inorg">
              <polygon points="17,42 109,42 99,213 27,213"/>
            </clipPath>
          </defs>

          <g clip-path="url(#clip-inorg)">
            <rect width="126" height="236" fill="#030c15"/>
            <g class="wg" id="wg-inorg" style="transform:translateY(213px)">
              <path class="wave-path"
                d="M-160,0 C-140,-9 -120,9 -80,0 C-60,-9 -40,9 0,0
                   C20,-9 40,9 80,0 C100,-9 120,9 160,0
                   C180,-9 200,9 240,0 C260,-9 280,9 320,0
                   L320,230 L-160,230 Z"
                fill="#38bdf8" opacity="0.82"/>
              <path class="wave-path-b"
                d="M-160,4 C-140,-5 -120,13 -80,4 C-60,-5 -40,13 0,4
                   C20,-5 40,13 80,4 C100,-5 120,13 160,4
                   C180,-5 200,13 240,4 C260,-5 280,13 320,4
                   L320,230 L-160,230 Z"
                fill="#0284c7" opacity="0.55"/>
            </g>
            <circle class="bubble" id="ib1" cx="40"  cy="190" r="3.2" fill="rgba(56,189,248,.65)" style="animation-duration:3.0s;animation-delay:0.4s"/>
            <circle class="bubble" id="ib2" cx="70"  cy="196" r="2.0" fill="rgba(56,189,248,.5)"  style="animation-duration:4.4s;animation-delay:0.9s"/>
            <circle class="bubble" id="ib3" cx="55"  cy="202" r="1.5" fill="rgba(56,189,248,.4)"  style="animation-duration:3.5s;animation-delay:1.8s"/>
            <circle class="bubble" id="ib4" cx="80"  cy="206" r="2.7" fill="rgba(56,189,248,.55)" style="animation-duration:4.9s;animation-delay:2.6s"/>
          </g>

          <line x1="22" y1="127" x2="104" y2="127" stroke="#38bdf8" stroke-width="0.6" stroke-dasharray="3,5" opacity="0.18"/>

          <line x1="107" y1="84"  x2="114" y2="84"  stroke="#38bdf8" stroke-width="1.2" opacity="0.4"/>
          <text x="116" y="88"  font-family="Rajdhani" font-size="8" font-weight="600" fill="#38bdf8" opacity="0.4">75</text>
          <line x1="104" y1="127" x2="111" y2="127" stroke="#38bdf8" stroke-width="1.2" opacity="0.4"/>
          <text x="113" y="131" font-family="Rajdhani" font-size="8" font-weight="600" fill="#38bdf8" opacity="0.4">50</text>
          <line x1="101" y1="171" x2="108" y2="171" stroke="#38bdf8" stroke-width="1.2" opacity="0.4"/>
          <text x="110" y="175" font-family="Rajdhani" font-size="8" font-weight="600" fill="#38bdf8" opacity="0.4">25</text>

          <polygon points="17,42 109,42 99,213 27,213"
            fill="none" stroke="#38bdf8" stroke-width="1.8" opacity="0.85"/>

          <rect x="9" y="24" width="108" height="20" rx="10"
            fill="#030d18" stroke="#38bdf8" stroke-width="1.5" opacity="0.92"/>
          <rect x="38" y="10" width="50" height="16" rx="8"
            fill="#020810" stroke="#38bdf8" stroke-width="1.5"/>
          <text x="63" y="37" text-anchor="middle" font-size="12" fill="#38bdf8" opacity="0.6">⚙</text>

          <circle cx="17"  cy="42"  r="2.5" fill="#38bdf8" opacity="0.7"/>
          <circle cx="109" cy="42"  r="2.5" fill="#38bdf8" opacity="0.7"/>
          <circle cx="27"  cy="213" r="2.5" fill="#38bdf8" opacity="0.7"/>
          <circle cx="99"  cy="213" r="2.5" fill="#38bdf8" opacity="0.7"/>

          <rect x="22" y="109" width="82" height="30" rx="6" fill="rgba(0,0,0,.58)"/>
          <text id="inorg-pct" x="63" y="129" text-anchor="middle"
            font-family="Orbitron" font-size="18" font-weight="700" fill="white">—</text>
        </svg>
      </div>

      <!-- Stats -->
      <div class="stats">
        <div>
          <div class="big-lbl">Volume Terisi</div>
          <div><span class="big-num" id="num-inorg">—</span><span class="big-unit">%</span></div>
        </div>
        <div>
          <div class="prog-labels"><span>Kosong</span><span>Penuh</span></div>
          <div class="prog-track"><div class="prog-fill inorg" id="bar-inorg"></div></div>
        </div>
        <div class="chips">
          <div class="chip">
            <span class="chip-lbl">Sisa</span>
            <span class="chip-val" id="sisa-inorg">—</span>
          </div>
          <div class="chip">
            <span class="chip-lbl">Status</span>
            <span class="chip-val" id="stat-inorg">—</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  </div><!-- /bins-grid -->

  <!-- ── STATUS BAR ── -->
  <div class="sbar">
    <span class="sbar-txt" id="cd-lbl">REFRESH: 5s</span>
    <div class="cbar"><div class="cbar-fill" id="cbar-fill"></div></div>
    <div class="led"></div>
  </div>
  <div class="last-upd" id="last-upd">Menunggu data dari sensor ESP32...</div>

</div><!-- /app -->

<script>
// CONSTANTS
const REFRESH = 5;          // detik
const TOP_Y   = 42;         // y atas interior tong
const BOT_Y   = 213;        // y bawah interior tong
const BIN_H   = BOT_Y - TOP_Y; // 171

// HELPERS
function liquidY(pct) {
  return BOT_Y - (Math.min(100, Math.max(0, pct)) / 100) * BIN_H;
}

function statusOf(pct) {
  if (pct < 50) return { lbl:'AMAN',    col:'#39d353' };
  if (pct < 70) return { lbl:'SEDANG',  col:'#f59e0b' };
  if (pct < 90) return { lbl:'PENUH',   col:'#f97316' };
  return              { lbl:'KRITIS',  col:'#ef4444' };
}

// UPDATE ONE BIN
function updateBin(type, pct) {
  const org = (type === 'organic');
  const p   = org ? 'org' : 'inorg';

  // Move wave group (CSS transition handles smooth animation)
  document.getElementById('wg-'   + p).style.transform = `translateY(${liquidY(pct)}px)`;

  // SVG % text
  document.getElementById((org ? 'org' : 'inorg') + '-pct').textContent = pct.toFixed(1) + '%';

  // Large number
  const bigEl = document.getElementById('num-' + p);
  bigEl.textContent = pct.toFixed(1);

  // Progress bar
  document.getElementById('bar-' + p).style.width = pct + '%';

  // Status
  const s = statusOf(pct);
  bigEl.style.color      = s.col;
  bigEl.style.textShadow = `0 0 28px ${s.col}70`;

  const pill = document.getElementById('pill-' + p);
  pill.textContent       = s.lbl;
  pill.style.background  = s.col + '20';
  pill.style.color       = s.col;
  pill.style.border      = `1px solid ${s.col}55`;

  document.getElementById('sisa-' + p).textContent = (100 - pct).toFixed(1) + '%';
  const statEl = document.getElementById('stat-' + p);
  statEl.textContent = s.lbl;
  statEl.style.color = s.col;

  // Bubbles: show when > 12%
  const show = pct > 12;
  const pfx  = org ? 'ob' : 'ib';
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(pfx + i);
    if (el) el.style.visibility = show ? 'visible' : 'hidden';
  }
}

// FETCH DATA
async function fetchData() {
  try {
    const r = await fetch('/data');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();

    updateBin('organic',   d.organic);
    updateBin('inorganic', d.inorganic);

    document.getElementById('last-upd').textContent =
      d.last_updated
        ? 'Update terakhir: ' + d.last_updated
        : 'Terhubung — menunggu data ESP32...';

    // Flash cards
    const org   = document.getElementById('card-org');
    const inorg = document.getElementById('card-inorg');
    org.classList.remove('flash-g');   inorg.classList.remove('flash-b');
    void org.offsetWidth;              void inorg.offsetWidth;
    org.classList.add('flash-g');      inorg.classList.add('flash-b');
    setTimeout(() => { org.classList.remove('flash-g'); inorg.classList.remove('flash-b'); }, 700);

    setLED(true);
  } catch (e) {
    console.error('Fetch error:', e);
    setLED(false);
  }
}

function setLED(ok) {
  const led = document.getElementById('conn-led');
  const col = ok ? '#39d353' : '#ef4444';
  led.style.background = col;
  led.style.boxShadow  = `0 0 8px ${col}`;
}

// COUNTDOWN
let timer = null, left = REFRESH;

function startCountdown() {
  left = REFRESH;
  clearInterval(timer);

  const fill  = document.getElementById('cbar-fill');
  const label = document.getElementById('cd-lbl');

  fill.style.transition = 'none';
  fill.style.width = '100%';

  timer = setInterval(() => {
    left--;
    fill.style.transition = 'width 1s linear';
    fill.style.width = Math.max(0, left / REFRESH * 100) + '%';
    label.textContent = 'REFRESH: ' + left + 's';

    if (left <= 0) {
      clearInterval(timer);
      fetchData().finally(startCountdown);
    }
  }, 1000);
}

// BOOT
fetchData().finally(startCountdown);
</script>
</body>
</html>
"""

#  FLASK ROUTES
@app.route("/")
def index():
    """Halaman utama monitoring."""
    return Response(HTML, content_type="text/html; charset=utf-8")

@app.route("/update", methods=["POST"])
def update():
    """
    Endpoint untuk ESP32 mengirim data volume.

    Body JSON:
        { "organic": 45.5, "inorganic": 72.3 }
        Nilai dalam persen (0.0 – 100.0).
    """
    global sensor_data
    try:
        body = request.get_json(force=True, silent=True)
        if not body:
            return jsonify({"status": "error", "message": "Body JSON tidak valid"}), 400

        changed = False
        if "organic" in body:
            sensor_data["organic"] = max(0.0, min(100.0, float(body["organic"])))
            changed = True
        if "inorganic" in body:
            sensor_data["inorganic"] = max(0.0, min(100.0, float(body["inorganic"])))
            changed = True

        if changed:
            sensor_data["last_updated"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        return jsonify({"status": "ok", "data": sensor_data})

    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/data")
def get_data():
    """Endpoint polling untuk web (auto-refresh)."""
    return jsonify(sensor_data)

if __name__ == "__main__":
    print("\n" + "=" * 52)
    print("  🗑️  SmartBin Monitor — Flask Web Server")
    print("=" * 52)
    print("  Web UI   : http://localhost:5000")
    print("  Data API : http://0.0.0.0:5000/data")
    print("  ESP32    : POST http://<IP_PC>:5000/update")
    print('             Body: {"organic":45.5,"inorganic":72.3}')
    print("=" * 52 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=True)