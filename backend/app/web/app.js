// Клиент веб-интерфейса «Ищейки»: авторизация, запуск миссии,
// поток событий по WebSocket, история и скачивание отчётов.

let token = localStorage.getItem('sleuth_token') || '';
let currentMission = null;

const $ = (id) => document.getElementById(id);

function headers(json = true) {
  const h = {};
  if (json) h['Content-Type'] = 'application/json';
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}

function logLine(text) {
  const box = $('log');
  const stamp = new Date().toLocaleTimeString('ru-RU');
  box.textContent += `\n[${stamp}] ${text}`;
  box.scrollTop = box.scrollHeight;
}

async function auth(action) {
  const body = { username: $('username').value.trim(), password: $('password').value };
  try {
    const response = await fetch(`/api/v1/auth/${action}`, {
      method: 'POST', headers: headers(), body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Ошибка авторизации');

    token = data.access_token;
    localStorage.setItem('sleuth_token', token);
    showAuthorized(data.username, data.role);
  } catch (error) {
    alert(error.message);
  }
}

async function loadExamples() {
  // Готовые формулировки по направлениям заказчика: аналитику проще
  // начать с образца, чем с чистого листа
  try {
    const response = await fetch('/api/v1/domains');
    if (!response.ok) return;
    const domains = await response.json();

    const select = $('examples');
    for (const domain of domains) {
      if (!domain.examples.length) continue;
      const group = document.createElement('optgroup');
      group.label = domain.title;
      for (const example of domain.examples) {
        const option = document.createElement('option');
        option.value = example;
        option.textContent = example.length > 78 ? example.slice(0, 78) + '…' : example;
        group.appendChild(option);
      }
      select.appendChild(group);
    }
  } catch (error) {
    // список образцов не критичен
  }
}

function useExample() {
  const value = $('examples').value;
  if (value) $('query').value = value;
}

function showAuthorized(username, role) {
  $('whoami').textContent = `${username} (${role})`;
  $('auth-card').classList.add('hidden');
  $('mission-card').classList.remove('hidden');
  $('history-card').classList.remove('hidden');
  loadExamples();
  loadHistory();
}

async function uploadDocuments() {
  // Документы загружаются до создания миссии: их идентификаторы уходят
  // в запрос, и агенты видят текст наравне с найденными источниками
  const input = $('docs');
  const files = [...(input.files || [])];
  if (!files.length) return [];

  const ids = [];
  for (const file of files) {
    const form = new FormData();
    form.append('file', file);
    logLine(`Загружаю документ: ${file.name}...`);
    const response = await fetch('/api/v1/documents/upload', {
      method: 'POST', headers: headers(false), body: form,
    });
    const data = await response.json();
    if (!response.ok) {
      logLine(`  не удалось: ${data.detail || response.status}`);
      continue;
    }
    ids.push(data.id);
    const note = data.chars
      ? `${data.pages || '?'} стр., ${data.chars} символов`
      : 'текстовый слой не найден — вероятно скан, нужен OCR';
    logLine(`  ${data.filename}: ${note}`);
  }
  $('docList').textContent = ids.length ? `Прикреплено документов: ${ids.length}` : '';
  return ids;
}

async function createMission() {
  const horizons = $('horizons').value.split(',')
    .map((h) => parseInt(h.trim(), 10)).filter((h) => !Number.isNaN(h));

  $('runBtn').disabled = true;
  $('log').textContent = 'Миссия создаётся...';
  $('result').innerHTML = '';
  $('progress').style.width = '0';

  try {
    const documentIds = await uploadDocuments();
    const payload = {
      query: $('query').value.trim(),
      mode: $('mode').value,
      horizons: horizons.length ? horizons : [1, 3, 5, 10],
      max_sources: parseInt($('maxSources').value, 10) || 20,
      user_document_ids: documentIds,
    };
    const response = await fetch('/api/v1/missions', {
      method: 'POST', headers: headers(), body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Не удалось создать миссию');

    currentMission = data.mission_id;
    logLine(`Миссия ${currentMission} принята к выполнению.`);
    listen(currentMission);
  } catch (error) {
    logLine('ОШИБКА: ' + error.message);
    $('runBtn').disabled = false;
  }
}

function listen(missionId) {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${scheme}://${location.host}/ws/missions/${missionId}`);

  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    const data = message.data || {};

    switch (message.type) {
      case 'PLAN_READY':
        logLine(`План готов. Тип: ${data.task_type}. Агенты: ${(data.agents || []).join(', ')}`);
        logLine(`Поисковые запросы: ${(data.search_queries || []).join(' | ')}`);
        break;
      case 'MEMORY_HIT':
        logLine(`Память: найдено ${data.count} релевантных записей прошлых миссий.`);
        break;
      case 'TASK_STARTED':
        logLine(`  ▸ ${data.agent_id} ${data.name} — запущен`);
        break;
      case 'TASK_COMPLETED':
        logLine(`  ✓ ${data.agent_id} — готово (уверенность ${data.confidence}) ${data.summary || ''}`);
        break;
      case 'TASK_FAILED':
        logLine(`  ✗ ${data.agent_id} — сбой: ${data.error}`);
        break;
      case 'CONFLICT_FOUND':
        logLine(`  ! Противоречие: ${data.description}`);
        break;
      case 'PROGRESS':
        $('progress').style.width = (data.percent || 0) + '%';
        break;
      case 'FINISHED':
        logLine(`ГОТОВО. Итоговая уверенность: ${(data.confidence * 100).toFixed(1)}%`);
        showResult(missionId, data);
        $('runBtn').disabled = false;
        loadHistory();
        loadClaims(missionId);
        socket.close();
        break;
      case 'ERROR':
        logLine('ОШИБКА МИССИИ: ' + data.error);
        $('runBtn').disabled = false;
        loadHistory();
        socket.close();
        break;
    }
  };

  socket.onerror = () => logLine('Соединение с потоком событий потеряно.');
}

function showResult(missionId, data) {
  const links = Object.entries(data.files || {})
    .map(([fmt, url]) => `<a href="${url}" target="_blank">${fmt.toUpperCase()}</a>`)
    .join(' &nbsp;·&nbsp; ') || 'файлы отчёта не сформированы';
  $('result').innerHTML = `<b>${data.summary || ''}</b><br>Отчёт: ${links}`;
}

async function loadClaims(missionId) {
  // ТЗ 3.6: аналитик подтверждает или отклоняет конкретные выводы
  try {
    const response = await fetch(`/api/v1/missions/${missionId}/claims`, { headers: headers(false) });
    if (!response.ok) return;
    const claims = await response.json();
    if (!claims.length) return;

    $('claims-card').classList.remove('hidden');
    $('claims').innerHTML = claims.map((c) => `
      <tr id="claim-${c.id}">
        <td>${c.agent_id || '—'}</td>
        <td>${escapeHtml(c.claim_text)}</td>
        <td>${(c.confidence * 100).toFixed(0)}%</td>
        <td>
          <span class="pill cl-${c.review_status}" id="status-${c.id}">${claimLabel(c.review_status)}</span><br>
          <button class="ghost mini" onclick="reviewClaim('${missionId}','${c.id}','accepted')">Принять</button>
          <button class="ghost mini" onclick="reviewClaim('${missionId}','${c.id}','rejected')">Отклонить</button>
        </td>
      </tr>`).join('');
  } catch (error) {
    // проверка выводов не критична для работы: молча пропускаем
  }
}

function claimLabel(status) {
  return { accepted: 'принято', rejected: 'отклонено', pending: 'не проверено' }[status] || status;
}

async function reviewClaim(missionId, claimId, status) {
  const note = status === 'rejected'
    ? prompt('Почему вывод отклонён? (необязательно)') || null
    : null;
  try {
    const response = await fetch(`/api/v1/missions/${missionId}/claims/${claimId}/review`, {
      method: 'POST', headers: headers(), body: JSON.stringify({ status, note }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'не удалось сохранить решение');

    const badge = $(`status-${claimId}`);
    badge.textContent = claimLabel(data.review_status);
    badge.className = `pill cl-${data.review_status}`;
    if (status === 'rejected') logLine(`Вывод отклонён и удалён из памяти системы.`);
  } catch (error) {
    alert(error.message);
  }
}

async function loadHistory() {
  try {
    const response = await fetch('/api/v1/history?limit=25', { headers: headers(false) });
    if (!response.ok) return;
    const missions = await response.json();

    $('history').innerHTML = missions.map((m) => `
      <tr>
        <td title="${escapeHtml(m.query)}">${escapeHtml(m.query.slice(0, 70))}${m.query.length > 70 ? '…' : ''}</td>
        <td>${m.mode}</td>
        <td><span class="pill st-${m.status}">${m.status}</span></td>
        <td>${m.confidence !== null ? (m.confidence * 100).toFixed(0) + '%' : '—'}</td>
        <td>${m.status === 'completed'
          ? `<a href="/reports/${m.id}.html" target="_blank">HTML</a> ·
             <a href="/reports/${m.id}.docx">DOCX</a> ·
             <a href="/reports/${m.id}.pdf">PDF</a>`
          : '—'}</td>
        <td><button class="ghost" style="padding:3px 8px;font-size:11px"
              onclick="rerunMission('${m.id}')">Повторить</button></td>
      </tr>`).join('');
  } catch (error) {
    // история не критична для работы: молча пропускаем
  }
}

async function rerunMission(missionId) {
  // ТЗ 7.8: история миссий с возможностью повторного запуска.
  // Параметры берутся из исходной миссии - переспрашивать их у пользователя
  // незачем, он уже их задал
  try {
    const response = await fetch(`/api/v1/missions/${missionId}/rerun`, {
      method: 'POST', headers: headers(),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'не удалось повторить');

    currentMission = data.mission_id;
    $('log').textContent = `Повторный запуск миссии ${missionId}`;
    $('result').innerHTML = '';
    $('progress').style.width = '0';
    $('runBtn').disabled = true;
    listen(currentMission);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (error) {
    alert(error.message);
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Восстановление сессии при перезагрузке страницы
if (token) {
  fetch('/api/v1/auth/me', { headers: headers(false) })
    .then((r) => (r.ok ? r.json() : Promise.reject()))
    .then((user) => showAuthorized(user.username, user.role))
    .catch(() => { token = ''; localStorage.removeItem('sleuth_token'); });
}
