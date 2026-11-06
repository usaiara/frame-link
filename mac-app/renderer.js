const $ = id => document.getElementById(id);
let currentStatus = null;
let applicationList = [];
let selectedApp = null;
let refreshing = false;
let toastTimer;
let configLoaded = false;
let configDirty = false;

document.querySelector('#overview .page-heading').insertAdjacentHTML('afterend', `
  <div class="connection-gate" id="connectionGate">
    <div class="gate-art"><span class="gate-ring ring-one"></span><span class="gate-ring ring-two"></span><span class="gate-phone">▥</span><span class="gate-cable">⌁</span></div>
    <div class="eyebrow">CONFIGURAÇÃO INICIAL · ETAPA 1 DE 3</div>
    <h2>Conecte seu iPhone para começar.</h2>
    <p>Ligue o cabo USB ao Mac, desbloqueie o aparelho e confirme a opção “Confiar neste computador” se ela aparecer.</p>
    <div class="gate-steps"><span><i>1</i> Conectar USB</span><span><i>2</i> Ativar jailbreak e Frida</span><span><i>3</i> Preparar a transmissão</span></div>
    <div class="gate-wait"><span class="gate-spinner"></span><strong>Procurando dispositivo...</strong><button class="button secondary" id="gateRefresh">Verificar agora</button></div>
    <small>O painel reconhecerá o iPhone automaticamente. Você pode configurar o OBS na aba Controle enquanto isso.</small>
  </div>`);

document.querySelector('.settings-grid').insertAdjacentHTML('beforeend', `
  <div class="card setting-card remote-card">
    <span class="setting-icon">⌁</span>
    <h2>Servidor OBS</h2>
    <p>Use o OBS deste Mac, o WebSocket de um Windows ou um fluxo RTMP na mesma rede.</p>
    <label class="input-label" for="inputMode">FONTE DE VÍDEO</label>
    <div class="select-wrap mode-select"><select id="inputMode"><option value="rtmp_push">Receber RTMP do Windows neste Mac</option><option value="websocket">OBS WebSocket · cenas e controle</option><option value="rtmp">Ler fluxo RTMP de outro servidor</option></select><span>⌄</span></div>
    <div id="websocketFields"><div class="remote-grid">
      <label><span class="input-label">IP OU NOME DO COMPUTADOR</span><input id="obsHost" placeholder="192.168.1.20" spellcheck="false"></label>
      <label><span class="input-label">PORTA</span><input id="obsPort" type="number" min="1" max="65535" value="4455"></label>
    </div>
    <label class="input-label" for="obsPassword">SENHA DO WEBSOCKET</label>
    <input id="obsPassword" type="password" placeholder="Senha do OBS remoto; local pode ficar vazio" autocomplete="off"></div>
    <div id="rtmpFields" hidden><label class="input-label" for="rtmpUrl">URL DO FLUXO RTMP</label><input id="rtmpUrl" placeholder="rtmp://192.168.1.20:1935/camswap" spellcheck="false"><small>Use o IP do Windows. 127.0.0.1 aponta para este Mac.</small></div>
    <div id="rtmpPushFields" hidden><div class="input-label">NO OBS DO WINDOWS → CONFIGURAÇÕES → TRANSMISSÃO</div><label class="input-label" for="publishServer">SERVIDOR</label><div class="copy-row"><input id="publishServer" readonly><button class="button secondary" id="copyServer">Copiar</button></div><label class="input-label" for="publishKey">CHAVE DE TRANSMISSÃO</label><div class="copy-row"><input id="publishKey" readonly value="live"><button class="button secondary" id="copyKey">Copiar</button></div><small>Selecione Serviço: Personalizado e depois clique em Iniciar transmissão no OBS.</small></div>
    <button class="button primary wide" id="saveObs">Salvar e conectar</button>
    <div class="setting-footer"><span>O Mac continua entregando a imagem ao iPhone.</span><span class="status-dot on"></span></div>
  </div>`);

function toast(message, error = false) {
  const item = $('toast');
  item.textContent = message;
  item.classList.toggle('error', error);
  item.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => item.classList.remove('show'), 5000);
}

function dot(id, active) { $(id).classList.toggle('on', !!active); }

async function api(route, method = 'GET', data) {
  return window.studio.api(route, method, data);
}

async function action(route, message, data) {
  const buttons = [...document.querySelectorAll('button')];
  const previousDisabled = buttons.map(button => button.disabled);
  buttons.forEach(button => button.disabled = true);
  try {
    const result = await api(route, 'POST', data);
    if (route.startsWith('/tweak/') && currentStatus?.phone) {
      renderStatus({
        ...currentStatus,
        phone: {
          ...currentStatus.phone,
          tweakInstalled: result.installed,
          tweakVersion: result.version || null,
          tweakError: null,
        },
      });
    }
    toast(message);
    await refresh();
    return result;
  } catch (error) {
    toast(error.message || String(error), true);
  } finally {
    buttons.forEach((button, index) => button.disabled = previousDisabled[index]);
    renderTweakStatus(currentStatus);
  }
}

function renderTweakStatus(state) {
  const phone = state?.phone;
  const installed = phone?.tweakInstalled;
  const canInstall = !!phone && installed === false;
  const canRemove = !!phone && installed === true;
  for (const id of ['installTop', 'installQuick', 'installSetting']) $(id).disabled = !canInstall;
  $('removeSetting').disabled = !canRemove;
  $('installTop').textContent = canRemove ? '✓  Tweak ativado' : '↧  Ativar tweak';
  $('installQuick').textContent = canRemove ? 'Ativado' : 'Ativar';
  $('installSetting').textContent = canRemove ? 'Ativado' : 'Ativar';

  let title, detail, footer;
  if (!phone) {
    title = 'iPhone desconectado';
    detail = 'Conecte o cabo USB para consultar o tweak.';
    footer = 'USB desconectado';
  } else if (installed === true) {
    title = `Tweak instalado${phone.tweakVersion ? ` · v${phone.tweakVersion}` : ''}`;
    detail = 'Pacote encontrado no iPhone. A substituição da câmera está disponível.';
    footer = 'Instalado · iOS 15 · rootless';
  } else if (installed === false) {
    title = 'Tweak removido';
    detail = 'O pacote não está instalado. Use Ativar para restaurar a câmera virtual.';
    footer = 'Não instalado no iPhone';
  } else {
    title = 'Estado não verificado';
    detail = phone.tweakError || 'Aguardando resposta do iPhone.';
    footer = 'Verificação indisponível';
  }
  $('tweakStateTitle').textContent = title;
  $('tweakStateDetail').textContent = detail;
  $('tweakQuickState').textContent = title;
  $('tweakFooterText').textContent = footer;
  dot('tweakStateDot', installed === true);
  dot('tweakFooterDot', installed === true);
  $('tweakState').classList.toggle('installed', installed === true);
  $('tweakState').classList.toggle('removed', installed === false);
}

function populateScenes(scenes, active, mode) {
  for (const id of ['scene', 'sceneSettings']) {
    const select = $(id);
    const previous = select.value;
    select.replaceChildren();
    if (!scenes.length) {
      const option = document.createElement('option');
      option.textContent = mode === 'rtmp_push' ? 'Recebendo RTMP do Windows' : mode === 'rtmp' ? 'Fluxo RTMP ativo' : 'OBS indisponível';
      option.value = '';
      select.append(option);
      select.disabled = true;
      continue;
    }
    select.disabled = false;
    for (const name of scenes) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.append(option);
    }
    select.value = scenes.includes(active) ? active : (scenes.includes(previous) ? previous : scenes[0]);
  }
}

function renderStatus(state) {
  const hadPhone = !!currentStatus?.phone;
  currentStatus = state;
  const connected = !!state.phone;
  const tweakInstalled = state.phone?.tweakInstalled === true;
  const obs = !!state.obs.connected;
  const bridge = !!state.bridgeRunning;
  const live = !!state.frameReady;
  $('deviceName').textContent = connected ? state.phone.name : 'Desconectado';
  $('phoneDetail').textContent = connected ? `iOS ${state.phone.os} · USB conectado` : 'Conecte via USB e ative o jailbreak';
  $('connectionGate').classList.toggle('visible', !connected);
  $('overview').classList.toggle('waiting-phone', !connected);
  if (!connected) {
    $('appCount').textContent = '—';
    applicationList = [];
    renderApps();
    $('appsSubtitle').textContent = 'Conecte o iPhone pelo USB para listar os aplicativos.';
  } else if (!hadPhone) {
    loadApps();
  }
  renderTweakStatus(state);
  dot('phoneDot', connected);
  $('obsName').textContent = state.obsMode === 'rtmp_push' ? (obs ? 'Windows ao vivo' : 'Aguardando Windows') : state.obsMode === 'rtmp' ? (obs ? 'RTMP ativo' : 'RTMP sem sinal') : (obs ? 'Conectado' : 'Desconectado');
  $('obsDetail').textContent = state.obsMode === 'rtmp_push' ? state.rtmpPublishServer : state.obsMode === 'rtmp' ? (state.sourceError || state.rtmpUrl || 'Configure a URL RTMP') : (obs ? `${state.obsHost} · ${state.obs.active || 'Sem cena'}` : `Servidor ${state.obsHost}:${state.obsPort}`);
  dot('obsDot', obs);
  $('bridgeName').textContent = live ? 'Ao vivo' : bridge ? 'Sem quadros' : 'Desligada';
  $('bridgeDetail').textContent = bridge ? 'Porta 8080 ativa' : 'Pronta para iniciar';
  dot('bridgeDot', live);
  $('topStatus').textContent = !connected ? 'iPhone desconectado' : state.phone.tweakInstalled === false ? 'Tweak desativado' : tweakInstalled && obs && live ? 'Tudo conectado' : 'Aguardando conexões';
  document.querySelector('.clock-dot').classList.toggle('on', tweakInstalled && obs && live);
  $('macAddress').textContent = state.macIp;
  $('endpoint').textContent = `http://${state.macIp}:8080/frame.jpg`;
  $('bridgeSwitch').classList.toggle('on', bridge);
  $('bridgeSwitch').setAttribute('aria-checked', String(bridge));
  $('bridgeAction').textContent = bridge ? 'Parar ponte' : 'Iniciar ponte';
  $('bridgeSettingsState').textContent = live ? 'Quadros recebidos do OBS' : bridge ? 'Ponte ativa, sem sinal' : 'Ponte desligada';
  document.querySelector('.live-badge').classList.toggle('waiting', !live);
  document.querySelector('.live-badge').innerHTML = live ? '<i></i> LIVE' : '<i></i> AGUARDANDO';
  document.querySelector('.preview-empty small').textContent = state.obsMode === 'rtmp_push' ? 'No OBS do Windows, clique em Iniciar transmissão' : 'Inicie a ponte e confirme a fonte de vídeo';
  document.querySelector('.control-card p').textContent = state.obsMode === 'rtmp_push' ? 'O Mac está pronto para receber o OBS do Windows.' : 'Escolha a cena e controle o envio para o iPhone.';
  document.querySelector('label[for="scene"]').textContent = state.obsMode === 'rtmp_push' ? 'FONTE ATIVA' : 'CENA ATIVA NO OBS';
  document.querySelector('.route-check').classList.toggle('off', !(live && tweakInstalled));
  document.querySelector('.route-check').textContent = live && tweakInstalled ? '✓' : '…';
  dot('bridgeSettingsDot', live);
  $('obsSettingsState').textContent = state.obsMode === 'rtmp_push' ? (obs ? 'Vídeo chegando do Windows' : 'Aguardando Iniciar transmissão no Windows') : state.obsMode === 'rtmp' ? (obs ? 'Fluxo RTMP recebendo vídeo' : (state.sourceError || 'Aguardando fluxo RTMP')) : (obs ? `WebSocket conectado · ${state.obsHost}:${state.obsPort}` : `Sem conexão com ${state.obsHost}:${state.obsPort}`);
  dot('obsSettingsDot', obs);
  populateScenes(state.obs.scenes || [], state.obs.active, state.obsMode);
  $('publishServer').value = state.rtmpPublishServer;
  $('publishKey').value = state.rtmpPublishKey;
  if (!configDirty && (!configLoaded || $('inputMode').value !== state.obsMode)) {
    $('inputMode').value = state.obsMode || 'websocket';
    showInputMode();
    $('obsHost').value = state.obsHost;
    $('obsPort').value = state.obsPort;
    $('rtmpUrl').value = state.rtmpUrl || '';
    $('obsPassword').placeholder = state.obsPasswordSaved ? 'Senha salva · digite para alterar' : 'Senha do OBS remoto; local pode ficar vazio';
    configLoaded = true;
  }
  const ipWarning = state.macIp !== state.configuredIp && state.macIp !== 'indisponível';
  if (ipWarning && !window.ipWarned) {
    window.ipWarned = true;
    toast(`O IP do Mac mudou para ${state.macIp}. O tweak ainda usa ${state.configuredIp}; recompile antes do teste.`, true);
  }
  $('previewDot').classList.toggle('on', live);
  $('previewText').textContent = live ? (tweakInstalled ? 'Sinal sincronizado com o OBS' : 'OBS ao vivo; ative o tweak no iPhone') : state.obsMode === 'rtmp_push' ? 'Aguardando transmissão do Windows' : 'Aguardando quadros do OBS';
}

function showInputMode() {
  const rtmp = $('inputMode').value === 'rtmp';
  const push = $('inputMode').value === 'rtmp_push';
  $('websocketFields').hidden = rtmp || push;
  $('rtmpFields').hidden = !rtmp;
  $('rtmpPushFields').hidden = !push;
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    renderStatus(await api('/status'));
  } catch (error) {
    $('topStatus').textContent = 'Painel desconectado';
    toast(`Serviço local indisponível: ${error.message}`, true);
  } finally {
    refreshing = false;
  }
}

async function loadApps() {
  try {
    const data = await api('/apps');
    applicationList = data.apps;
    $('appCount').textContent = String(applicationList.length);
    renderApps();
  } catch (error) {
    $('appsSubtitle').textContent = 'Conecte o iPhone via USB para listar os aplicativos.';
    $('appList').replaceChildren();
  }
}

function renderApps() {
  const query = $('appSearch').value.trim().toLocaleLowerCase();
  const filtered = applicationList.filter(item =>
    `${item.name} ${item.id}`.toLocaleLowerCase().includes(query));
  $('appsSubtitle').textContent = `${filtered.length} aplicativos encontrados · ${selectedApp ? `selecionado: ${selectedApp.name}` : 'selecione um para o teste'}`;
  const list = $('appList');
  list.replaceChildren();
  for (const app of filtered) {
    const row = document.createElement('div');
    row.className = `app-row${selectedApp?.id === app.id ? ' selected' : ''}`;
    const title = document.createElement('span');
    title.className = 'app-name';
    const avatar = document.createElement('span');
    avatar.className = 'app-avatar';
    if (app.icon) {
      const icon = document.createElement('img');
      icon.src = `http://127.0.0.1:18765/icons/${encodeURIComponent(app.id)}`;
      icon.alt = '';
      icon.onerror = () => { avatar.replaceChildren(document.createTextNode((app.name || '?').slice(0, 1).toUpperCase())); };
      avatar.append(icon);
    } else {
      avatar.textContent = (app.name || '?').slice(0, 1).toUpperCase();
    }
    title.append(avatar, document.createTextNode(app.name));
    const id = document.createElement('span');
    id.className = 'app-id';
    id.textContent = app.id;
    const choose = document.createElement('span');
    choose.className = 'app-select';
    choose.textContent = selectedApp?.id === app.id ? '✓ Selecionado' : 'Selecionar';
    row.append(title, id, choose);
    row.addEventListener('click', () => { selectedApp = app; renderApps(); toast(`${app.name} selecionado para o teste. Abra o app no iPhone.`); });
    list.append(row);
  }
}

async function updateScene(event) {
  const name = event.target.value;
  if (!name) return;
  await action('/scene', `Cena “${name}” ativada no OBS.`, { name });
}

function switchView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active-view', v.id === id));
  document.querySelectorAll('.nav').forEach(v => v.classList.toggle('active', v.dataset.view === id));
  $('crumb').textContent = { overview: 'Visão geral', applications: 'Aplicativos', control: 'Controle' }[id];
  if (id === 'applications') loadApps();
}

function updatePreview() {
  if (!currentStatus?.frameReady) {
    $('preview').style.display = 'none';
    $('previewEmpty').style.display = 'flex';
    return;
  }
  const img = $('preview');
  img.onload = () => { img.style.display = 'block'; $('previewEmpty').style.display = 'none'; $('previewTime').textContent = new Date().toLocaleTimeString('pt-BR'); };
  img.onerror = () => { img.style.display = 'none'; $('previewEmpty').style.display = 'flex'; };
  img.src = `http://127.0.0.1:8080/frame.jpg?t=${Date.now()}`;
}

document.querySelectorAll('.nav').forEach(item => item.addEventListener('click', () => switchView(item.dataset.view)));
for (const id of ['scene', 'sceneSettings']) $(id).addEventListener('change', updateScene);
for (const id of ['installTop', 'installQuick', 'installSetting']) $(id).addEventListener('click', () => action('/tweak/install', 'Tweak instalado no iPhone via USB.'));
for (const id of ['shotTop', 'screenshotSetting']) $(id).addEventListener('click', () => action('/screenshot', 'Captura aberta no Mac.'));
$('removeSetting').addEventListener('click', () => action('/tweak/uninstall', 'Tweak removido do iPhone.'));
$('saveObs').addEventListener('click', async () => {
  const password = $('obsPassword').value;
  const data = { mode: $('inputMode').value, host: $('obsHost').value.trim(), port: Number($('obsPort').value), password, rtmpUrl: $('rtmpUrl').value.trim() };
  const label = data.mode === 'rtmp_push' ? 'Mac pronto para receber o OBS do Windows.' : data.mode === 'rtmp' ? 'Fluxo RTMP configurado.' : `Servidor OBS configurado em ${data.host}:${data.port}.`;
  const result = await action('/obs/config', label, data);
  if (result) {
    configDirty = false;
    $('obsPassword').value = '';
    $('obsPassword').placeholder = password ? 'Senha salva · digite para alterar' : 'Sem senha configurada';
  }
});
$('inputMode').addEventListener('change', () => { configDirty = true; showInputMode(); });
for (const id of ['obsHost', 'obsPort', 'obsPassword', 'rtmpUrl']) $(id).addEventListener('input', () => { configDirty = true; });
$('copyServer').addEventListener('click', async () => { await window.studio.copy($('publishServer').value); toast('Endereço do servidor copiado.'); });
$('copyKey').addEventListener('click', async () => { await window.studio.copy($('publishKey').value); toast('Chave copiada.'); });
$('gateRefresh').addEventListener('click', refresh);
$('bridgeSwitch').addEventListener('click', () => action(currentStatus?.bridgeRunning ? '/bridge/stop' : '/bridge/start', currentStatus?.bridgeRunning ? 'Ponte desligada.' : 'Ponte iniciada.'));
$('bridgeAction').addEventListener('click', () => action(currentStatus?.bridgeRunning ? '/bridge/stop' : '/bridge/start', currentStatus?.bridgeRunning ? 'Ponte desligada.' : 'Ponte iniciada.'));
$('refresh').addEventListener('click', async () => { await refresh(); await loadApps(); toast('Painel atualizado.'); });
$('refreshApps').addEventListener('click', loadApps);
$('appSearch').addEventListener('input', renderApps);

setTimeout(refresh, 500);
setInterval(refresh, 5000);
setInterval(updatePreview, 500);
