const $ = (id) => document.getElementById(id);
const fmt = (n) => n == null ? "—" : Number(n).toLocaleString("zh-CN", {maximumSignificantDigits: 7});
const price = (n) => n == null ? "—" : Number(n).toLocaleString("en-US", {maximumSignificantDigits: 7});
let interval = "15m";

async function getJSON(url) {
  const response = await fetch(url, {cache: "no-store"});
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

function ageText(seconds) {
  if (seconds == null) return "尚无扫描记录";
  if (seconds < 60) return `${seconds} 秒前更新`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前更新`;
  return `${Math.floor(seconds / 3600)} 小时前更新`;
}

function renderDashboard(data) {
  const sys = data.system, metrics = data.metrics;
  $("statusDot").className = sys.status === "running" ? "online" : "stale";
  $("systemStatus").textContent = sys.status === "running" ? "策略运行中" : sys.status === "stale" ? "数据待刷新" : "等待首次扫描";
  $("scanTime").textContent = `${ageText(sys.age_seconds)} · ${sys.last_scan || "—"} · 本地纸面研究`;
  $("universe").textContent = fmt(sys.universe);
  $("directions").textContent = `${fmt(sys.active_directions)} 个趋势方向`;
  $("signalCount").textContent = fmt(metrics.new_signals);
  $("positionCount").textContent = fmt(metrics.open_positions);
  $("expectancy").textContent = metrics.expectancy_r == null ? "待积累" : `${metrics.expectancy_r > 0 ? "+" : ""}${metrics.expectancy_r}R`;
  $("sampleSize").textContent = `扣除成本 · ${metrics.closed_trades} 笔历史样本`;
  $("winRate").textContent = metrics.win_rate == null ? "待积累" : `${metrics.win_rate}%`;
  renderSignals(data.signals);
  renderPositions(data.positions);
  renderTelegram(data.telegram);
  renderModules(data.modules);
  $("sourceLabel").textContent = `SOURCE · ${data.source}`;
}

function renderSignals(signals) {
  $("signalsBody").innerHTML = signals.length ? signals.map(s => { const p=s.score_parts; const tip=`确认 ${p.confirmation}/30 · 盈亏比 ${p.reward_risk}/30 · 风控 ${p.risk_control}/30 · 成本 ${p.cost_integrity}/10`; return `<tr><td><div class="symbol-cell"><span class="grade ${s.grade.toLowerCase()}">${s.grade}</span>${s.symbol}</div></td><td><div class="score" title="${tip}"><strong>${s.score}</strong><span><i style="width:${s.score}%"></i></span></div></td><td class="${s.side === "做多" ? "long" : "short"}">${s.side}</td><td>${s.strategy === "enhanced" ? "增强版" : "基准版"}</td><td>${price(s.entry)}</td><td>${price(s.stop)}</td><td>${price(s.target)}</td><td class="rr">${s.reward_risk || "—"}R</td><td>${s.risk_pct == null ? "—" : (s.risk_pct * 100).toFixed(2) + "%"}</td></tr>`}).join("") : `<tr><td colspan="9" class="empty">当前没有新信号。系统保持静默，不构造机会。</td></tr>`;
}

function renderPositions(positions) {
  $("positionBadge").textContent = positions.length;
  $("positionList").innerHTML = positions.length ? positions.slice(0, 16).map(p => `<div class="position-row"><div><strong>${p.symbol}</strong><small>${p.strategy === "enhanced" ? "增强版" : "基准版"} · ${p.grade}级 · ${p.score}分</small></div><div class="${p.side === "做多" ? "long" : "short"}">${p.side}<small>${p.reward_risk || "—"}R</small></div><div><span class="price-path">入场 → 止损</span><small>${price(p.entry)} → ${price(p.stop)}</small></div><div><span class="price-path">目标</span><small>${price(p.target)}</small></div></div>`).join("") : `<p class="empty">当前没有纸面仓位。</p>`;
}

function renderTelegram(tg) {
  $("telegramBadge").textContent = tg.enabled ? "已启用" : "关闭";
  $("telegramBadge").style.color = tg.enabled ? "var(--green)" : "var(--amber)";
  $("telegramStatus").textContent = tg.enabled ? "推送通道已配置" : "未启用外部推送";
  $("telegramPolicy").textContent = tg.policy;
}

function renderModules(modules) {
  const labels = {active:"运行中", reserved:"接口已预留", available:"可用", foundation:"基础已建立"};
  $("moduleGrid").innerHTML = modules.map((m, i) => `<div class="module"><span class="module-icon">${String(i + 1).padStart(2,"0")} / ${m.kind.toUpperCase()}</span><strong>${m.name}</strong><span class="module-status ${m.status}">${labels[m.status]}</span></div>`).join("");
}

async function loadDashboard() {
  try { renderDashboard(await getJSON("/api/v1/dashboard")); }
  catch { $("systemStatus").textContent = "控制台连接失败"; $("statusDot").className = "stale"; }
}

function drawChart(candles) {
  const canvas = $("chart"), box = canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  canvas.width = box.width * dpr; canvas.height = box.height * dpr;
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  const w = box.width, h = box.height, pad = {t:18,r:64,b:26,l:8};
  const hi = Math.max(...candles.map(x=>x.h)), lo = Math.min(...candles.map(x=>x.l)), range = hi-lo || 1;
  const y = v => pad.t + (hi-v)/range*(h-pad.t-pad.b), step=(w-pad.l-pad.r)/candles.length, body=Math.max(2,step*.58);
  ctx.strokeStyle="#172a25"; ctx.lineWidth=1; ctx.font="11px Segoe UI"; ctx.fillStyle="#708b81";
  for(let i=0;i<5;i++){const yy=pad.t+i*(h-pad.t-pad.b)/4;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();ctx.fillText(price(hi-i*range/4),w-pad.r+8,yy+4)}
  candles.forEach((c,i)=>{const x=pad.l+i*step+step/2, color=c.c>=c.o?"#1fd693":"#ff6b6b";ctx.strokeStyle=color;ctx.fillStyle=color;ctx.beginPath();ctx.moveTo(x,y(c.h));ctx.lineTo(x,y(c.l));ctx.stroke();ctx.fillRect(x-body/2,Math.min(y(c.o),y(c.c)),body,Math.max(1,Math.abs(y(c.o)-y(c.c))))});
}

async function loadCandles() {
  const symbol = $("symbolPicker").value; $("chartTitle").textContent = symbol; $("chartMessage").style.display="grid"; $("chartMessage").textContent="正在载入行情…";
  try { const data=await getJSON(`/api/v1/candles?symbol=${symbol}&interval=${interval}`); drawChart(data.candles); const last=data.candles.at(-1); $("lastPrice").textContent=price(last.c); $("lastPrice").className=`last-price ${last.c>=last.o?"long":"short"}`; $("chartMessage").style.display="none"; }
  catch { $("chartMessage").textContent="公开行情暂时不可用，控制台其他数据不受影响。"; }
}

document.querySelectorAll("[data-scroll]").forEach(b=>b.addEventListener("click",()=>$(b.dataset.scroll).scrollIntoView({behavior:"smooth"})));
document.querySelectorAll(".intervals button").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll(".intervals button").forEach(x=>x.classList.remove("active"));b.classList.add("active");interval=b.textContent;loadCandles()}));
$("symbolPicker").addEventListener("change",loadCandles);
addEventListener("resize",()=>loadCandles());
loadDashboard(); loadCandles(); setInterval(loadDashboard,30000); setInterval(loadCandles,60000);
