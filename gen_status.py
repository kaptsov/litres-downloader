#!/usr/bin/env python3
"""Generates a static HTML status page + JSON data for AJAX updates."""
import json
import os
import subprocess
import datetime

STATUS_DIR = "/var/www/server-status"
DOWNLOADS_DIR = "/var/www/litres-bot/downloads"


def get_disk_usage():
    result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    lines = result.stdout.strip().split("\n")
    if len(lines) >= 2:
        parts = lines[1].split()
        return {"total": parts[1], "used": parts[2], "avail": parts[3], "percent": parts[4]}
    return {}


def get_ram_usage():
    result = subprocess.run(["free", "-h"], capture_output=True, text=True)
    lines = result.stdout.strip().split("\n")
    info = {}
    for line in lines:
        if line.startswith("Mem:"):
            parts = line.split()
            info["ram_total"] = parts[1]
            info["ram_used"] = parts[2]
        elif line.startswith("Swap:"):
            parts = line.split()
            info["swap_total"] = parts[1]
            info["swap_used"] = parts[2]
    return info


def get_downloads_files():
    files = []
    if not os.path.exists(DOWNLOADS_DIR):
        return files
    for entry in os.scandir(DOWNLOADS_DIR):
        if entry.is_file():
            size_mb = entry.stat().st_size / (1024 * 1024)
            files.append({
                "name": entry.name,
                "size_mb": round(size_mb, 1),
                "mtime": entry.stat().st_mtime,
            })
        elif entry.is_dir():
            total_size = 0
            count = 0
            for root, dirs, fnames in os.walk(entry.path):
                for f in fnames:
                    total_size += os.path.getsize(os.path.join(root, f))
                    count += 1
            files.append({
                "name": f"{entry.name}/ ({count} files)",
                "size_mb": round(total_size / (1024 * 1024), 1),
                "mtime": entry.stat().st_mtime,
            })
    return sorted(files, key=lambda x: x["mtime"], reverse=True)


def get_bot_status():
    result = subprocess.run(["systemctl", "is-active", "litres-bot"], capture_output=True, text=True)
    return result.stdout.strip()


def get_downloads_total_size():
    if not os.path.exists(DOWNLOADS_DIR):
        return 0
    total = 0
    for root, dirs, files in os.walk(DOWNLOADS_DIR):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return round(total / (1024 * 1024), 1)


def collect_data():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "updated": now,
        "disk": get_disk_usage(),
        "ram": get_ram_usage(),
        "bot": get_bot_status(),
        "files": get_downloads_files(),
        "dl_total": get_downloads_total_size(),
    }


def generate_json(data):
    os.makedirs(STATUS_DIR, exist_ok=True)
    with open(os.path.join(STATUS_DIR, "data.json"), "w") as f:
        json.dump(data, f, ensure_ascii=False)


def generate_html():
    os.makedirs(STATUS_DIR, exist_ok=True)
    html = r"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Server Status</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; background: #1a1a2e; color: #e0e0e0; }
  h1 { color: #fff; font-size: 1.4em; }
  .card { background: #16213e; border-radius: 8px; padding: 16px; margin: 12px 0; }
  .label { color: #888; font-size: 0.85em; }
  .value { font-size: 1.2em; font-weight: bold; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #2a2a4a; }
  th { color: #888; font-weight: normal; }
  .updated { color: #555; font-size: 0.8em; text-align: center; margin-top: 20px; }
  .bar { background: #2a2a4a; border-radius: 4px; height: 8px; margin-top: 4px; }
  .bar-fill { background: #4caf50; height: 8px; border-radius: 4px; transition: width 0.5s; }
  .active { color: #4caf50; }
  .inactive { color: #f44336; }
  .pulse { animation: pulse 1s ease-in-out; }
  @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.6; } 100% { opacity: 1; } }
</style>
</head><body>
<h1>Litres Bot — Server Status</h1>

<div class="grid">
  <div class="card">
    <div class="label">Диск</div>
    <div class="value" id="disk-value">—</div>
    <div class="bar"><div class="bar-fill" id="disk-bar" style="width:0%"></div></div>
    <div class="label" id="disk-detail">—</div>
  </div>
  <div class="card">
    <div class="label">RAM</div>
    <div class="value" id="ram-value">—</div>
    <div class="label" id="ram-swap">—</div>
  </div>
</div>

<div class="card">
  <div class="label">Бот</div>
  <div class="value" id="bot-status">—</div>
</div>

<div class="card">
  <div class="label" id="dl-label">Downloads</div>
  <table>
    <thead><tr><th>Файл / Папка</th><th>Размер</th><th>Изменён</th></tr></thead>
    <tbody id="dl-table"><tr><td colspan="3" style="text-align:center;color:#888">Загрузка...</td></tr></tbody>
  </table>
</div>

<div class="updated" id="updated">—</div>

<script>
function fmtTime(ts) {
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  return `${hh}:${mm}:${ss} ${dd}.${mo}`;
}

async function refresh() {
  try {
    const r = await fetch('data.json?' + Date.now());
    const d = await r.json();

    document.getElementById('disk-value').textContent = `${d.disk.used} / ${d.disk.total}`;
    document.getElementById('disk-bar').style.width = d.disk.percent;
    document.getElementById('disk-detail').textContent = `${d.disk.percent} занято, ${d.disk.avail} свободно`;

    document.getElementById('ram-value').textContent = `${d.ram.ram_used} / ${d.ram.ram_total}`;
    document.getElementById('ram-swap').textContent = `Swap: ${d.ram.swap_used} / ${d.ram.swap_total}`;

    const bs = document.getElementById('bot-status');
    bs.textContent = d.bot;
    bs.className = 'value ' + (d.bot === 'active' ? 'active' : 'inactive');

    document.getElementById('dl-label').textContent = `Downloads (${d.dl_total} MB total)`;

    const tbody = document.getElementById('dl-table');
    if (d.files.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:#888">Пусто</td></tr>';
    } else {
      tbody.innerHTML = d.files.map(f =>
        `<tr><td>${f.name}</td><td>${f.size_mb} MB</td><td>${fmtTime(f.mtime)}</td></tr>`
      ).join('');
    }

    document.getElementById('updated').textContent = `Обновлено: ${d.updated} · live каждые 10 сек`;
    document.getElementById('updated').classList.add('pulse');
    setTimeout(() => document.getElementById('updated').classList.remove('pulse'), 1000);
  } catch (e) {
    document.getElementById('updated').textContent = 'Ошибка обновления: ' + e.message;
  }
}

refresh();
setInterval(refresh, 10000);
</script>
</body></html>"""

    with open(os.path.join(STATUS_DIR, "index.html"), "w") as f:
        f.write(html)


if __name__ == "__main__":
    data = collect_data()
    generate_json(data)
    generate_html()
