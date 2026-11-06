const { app, BrowserWindow, clipboard, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, execFileSync } = require('child_process');

let window;
let backend;
const port = 18765;
const apiBase = `http://127.0.0.1:${port}`;

function findPython() {
  const candidates = [
    '/usr/local/bin/python3',
    '/opt/homebrew/bin/python3',
    '/usr/bin/python3',
  ];
  for (const item of candidates) {
    if (!fs.existsSync(item)) continue;
    try {
      execFileSync(item, ['-c', 'import frida, websocket'], { stdio: 'ignore' });
      return item;
    } catch (_) {}
  }
  throw new Error('Python com frida e websocket-client não encontrado. Veja o README.');
}

function resource(name) {
  return app.isPackaged
    ? path.join(process.resourcesPath, name)
    : path.join(__dirname, name);
}

function startBackend() {
  const script = resource('backend.py');
  const root = app.isPackaged ? process.resourcesPath : path.dirname(__dirname);
  try {
    backend = spawn(findPython(), [script, '--port', String(port), '--root', root], {
      cwd: root,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    backend.stdout.on('data', data => console.log(`[backend] ${data}`));
    backend.stderr.on('data', data => console.error(`[backend] ${data}`));
    backend.on('exit', code => console.error(`Backend finalizado: ${code}`));
  } catch (error) {
    console.error(error);
  }
}

async function api(route, method = 'GET', data) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch(`${apiBase}${route}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: data === undefined ? undefined : JSON.stringify(data),
      signal: controller.signal,
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  } finally {
    clearTimeout(timer);
  }
}

function createWindow() {
  window = new BrowserWindow({
    width: 1450,
    height: 920,
    minWidth: 1120,
    minHeight: 700,
    title: 'Frame Link',
    backgroundColor: '#080d17',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.loadFile(path.join(__dirname, 'index.html'));
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  startBackend();
  ipcMain.handle('api', (_event, route, method, data) => api(route, method, data));
  ipcMain.handle('copy', (_event, value) => { clipboard.writeText(String(value).slice(0, 512)); });
  createWindow();
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('before-quit', () => {
  if (backend && !backend.killed) backend.kill('SIGTERM');
});
