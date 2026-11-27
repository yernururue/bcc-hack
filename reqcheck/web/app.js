// ReqCheck UI: один экран, состояния S0–S4 из docs/05-design.md.
import { DemoApi, STAGE_NAMES, estimateRun } from './demo.js';
import { AuthRequired, HttpApi } from './http.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
// По умолчанию — сервер /api/v1. `?demo` — фиктивные данные без сервера (`?demo&fail` — сценарий ошибки).
const api = params.has('demo') ? new DemoApi({ failAtStage: params.has('fail') ? 3 : null }) : new HttpApi();
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');

const ACCEPT = { pdf: 'PDF', md: 'Markdown', markdown: 'Markdown', txt: 'Текст', docx: 'Word' };
const OUT_FORMAT = { pdf: 'PDF', md: 'MD', markdown: 'MD', txt: 'MD', docx: 'DOCX' };

const el = {
  drop: $('drop'), file: $('file'), chip: $('chip'), chipName: $('chip-name'), chipMeta: $('chip-meta'),
  chipRemove: $('chip-remove'), dropError: $('drop-error'), example: $('example'),
  model: $('model'),
  run: $('run'), start: $('start'), stage: $('run-stage'), count: $('run-count'), progress: $('progress'),
  stages: $('stages'), stageList: $('stage-list'), stagesBody: $('stages-body'),
  result: $('result'), verdict: $('verdict'), verdictSub: $('verdict-sub'), stats: $('stats'), fixes: $('fixes'),
  downloadMain: $('download-main'), downloadJson: $('download-json'), downloadAnnotated: $('download-annotated'), downloadHint: $('download-hint'), reset: $('reset'),
  failure: $('failure'), failureText: $('failure-text'), retry: $('retry'),
  compare: $('compare'), compareResult: $('compare-result'), compareRows: $('compare-rows'),
  compareOverlap: $('compare-overlap'), compareList: $('compare-list'),
  tokenForm: $('token-form'), token: $('token'), tokenError: $('token-error'),
};

const state = { file: null, models: [], running: false };

// ---------- утилиты ----------

const fmtMoney = (v) => (v < 0.01 ? '< $0,01' : '≈ $' + v.toFixed(2).replace('.', ','));
const fmtSize = (b) => (b < 1024 * 1024 ? Math.max(1, Math.round(b / 1024)) + ' КБ' : (b / 1048576).toFixed(1).replace('.', ',') + ' МБ');
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
};
const wait = (ms) => new Promise((r) => setTimeout(r, reduceMotion.matches ? Math.min(ms, 50) : ms));

function withTransition(update) {
  if (document.startViewTransition && !reduceMotion.matches) document.startViewTransition(update);
  else update();
}

function restartAnimation(node, cls) {
  node.classList.remove(cls);
  void node.offsetWidth;
  node.classList.add(cls);
}

// ---------- модели ----------

async function loadModels() {
  try {
    state.models = await api.listModels();
  } catch (err) {
    if (err instanceof AuthRequired) askToken(err);
    else setError('Не удалось получить список моделей: ' + err.message);
    return;
  }
  el.model.replaceChildren();
  for (const m of state.models) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = `${m.id} · ${fmtMoney(estimateRun(m))}`;
    if (m.default) opt.selected = true;
    el.model.append(opt);
  }
}

// ---------- токен доступа (REQCHECK_API_TOKEN) ----------

function askToken(err) {
  // первый 401 без токена — просто просим; с токеном — он не подошёл
  el.tokenError.textContent = api.token ? err.message : '';
  el.tokenForm.hidden = false;
  el.token.focus();
}

el.tokenForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  api.setToken(el.token.value);
  el.token.value = '';
  el.tokenError.textContent = '';
  el.tokenForm.hidden = true;
  await loadModels();
});

function currentModel() {
  return state.models.find((m) => m.id === el.model.value);
}

// ---------- файл ----------

function extOf(name) {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i + 1).toLowerCase();
}

function setError(msg) {
  el.dropError.textContent = msg;
  if (msg) restartAnimation(el.drop, 'is-shake');
}

function acceptFile(f) {
  // f — File из выбора/перетаскивания или {name, size, blob} из примера
  const ext = extOf(f.name);
  if (!ACCEPT[ext]) {
    setError('Нужен PDF, Markdown, TXT или DOCX');
    return;
  }
  if (f.size > 20 * 1024 * 1024) {
    setError('Файл больше 20 МБ');
    return;
  }
  setError('');
  state.file = { name: f.name, size: f.size, ext, blob: f.blob === undefined ? f : f.blob };
  withTransition(() => {
    el.chipName.textContent = f.name;
    el.chipMeta.textContent = `${ACCEPT[ext]} · ${fmtSize(f.size)}`;
    el.drop.hidden = true;
    el.chip.hidden = false;
    el.example.hidden = true;
  });
  el.start.disabled = false;
}

function clearFile() {
  state.file = null;
  el.file.value = '';
  withTransition(() => {
    el.chip.hidden = true;
    el.drop.hidden = false;
    el.example.hidden = false;
  });
  el.start.disabled = true;
}

el.file.addEventListener('change', () => el.file.files[0] && acceptFile(el.file.files[0]));
el.chipRemove.addEventListener('click', () => {
  clearFile();
  el.file.focus();
});
el.example.addEventListener('click', async () => {
  try {
    acceptFile(await api.loadExample());
  } catch (err) {
    setError('Не удалось загрузить пример: ' + err.message);
  }
});

let dragDepth = 0;
window.addEventListener('dragenter', (e) => {
  e.preventDefault();
  if (state.running || el.drop.hidden) return;
  dragDepth++;
  el.drop.classList.add('is-drag');
});
window.addEventListener('dragleave', () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) el.drop.classList.remove('is-drag');
});
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  el.drop.classList.remove('is-drag');
  const f = e.dataTransfer?.files?.[0];
  if (f && !state.running && !el.drop.hidden) acceptFile(f);
});

// ---------- этапы ----------

function renderStages() {
  el.stageList.replaceChildren();
  STAGE_NAMES.forEach((name, i) => {
    const li = $('tpl-stage').content.firstElementChild.cloneNode(true);
    li.style.setProperty('--i', i);
    li.querySelector('.stage-name').textContent = name;
    el.stageList.append(li);
  });
}

function onEvent(ev) {
  if (ev.type === 'compare') {
    el.stage.textContent = 'Сравнение с одним промптом';
    el.count.textContent = '';
    return;
  }
  if (ev.type !== 'stage') return;
  const li = el.stageList.children[ev.index];
  li.dataset.state = ev.state;
  // Пояснение у этапа показываем только при ошибке: в обычном прогоне это лишний текст.
  if (ev.state === 'error' && ev.note) li.querySelector('.stage-note').textContent = ev.note;
  if (ev.state === 'active') {
    el.stage.textContent = STAGE_NAMES[ev.index];
    el.count.textContent = `${ev.index + 1} / ${STAGE_NAMES.length}`;
  }
  if (ev.state === 'done' || ev.state === 'error') setProgress((ev.index + 1) / STAGE_NAMES.length);
}

function setProgress(p) {
  el.start.style.setProperty('--p', p);
  el.progress.setAttribute('aria-valuenow', Math.round(p * 100));
}

// После прогона этапы больше не нужны: сворачиваем и убираем, на экране остаётся только результат.
async function dismissStages() {
  el.stagesBody.classList.add('is-collapsed');
  await wait(200);
  el.stages.hidden = true;
}

// ---------- запуск ----------

// Синхронно: принудительный reflow фиксирует стартовую ширину, и переход идёт без ожидания кадра
// (requestAnimationFrame не срабатывает во фоновой вкладке, а прогон не должен от этого зависеть).
function morphToProgress() {
  const btn = el.start;
  btn.style.width = btn.offsetWidth + 'px';
  void btn.offsetWidth;
  btn.classList.add('is-progress');
  btn.style.width = el.run.clientWidth + 'px';
  el.run.classList.add('is-running');
}

function lockInputs(locked) {
  el.model.disabled = locked;
  el.chipRemove.disabled = locked;
}

async function start() {
  if (!state.file || state.running) return;
  state.running = true;
  lockInputs(true);
  el.failure.hidden = true;
  el.result.hidden = true;
  el.result.classList.remove('is-in');
  el.start.classList.remove('is-error', 'is-collapsed');
  setProgress(0);
  renderStages();
  el.stagesBody.classList.remove('is-collapsed');
  el.stages.hidden = false;

  el.stage.textContent = 'Подготовка';
  el.count.textContent = '';

  morphToProgress();

  try {
    const view = await api.run(state.file, currentModel(), onEvent, { compare: el.compare.checked });
    await finish(view);
  } catch (err) {
    fail(err);
    if (err instanceof AuthRequired) askToken(err);
  } finally {
    state.running = false;
  }
}

async function finish(view) {
  setProgress(1);
  el.stage.textContent = 'Готово';
  await wait(400);

  el.start.classList.add('is-collapsed');
  await Promise.all([wait(200), dismissStages()]);
  el.run.classList.add('is-gone');

  const { stats } = view;
  el.verdict.textContent = view.headline;
  el.verdict.classList.toggle('is-blocked', !view.can_start_development);
  el.verdictSub.textContent = view.sub;

  renderStats([
    { label: 'требований', value: stats.requirements },
    { label: plural(stats.violations, 'нарушение', 'нарушения', 'нарушений'), value: stats.violations },
    { label: plural(stats.blockers, 'блокирующий', 'блокирующих', 'блокирующих'), value: stats.blockers, danger: stats.blockers > 0 },
  ]);
  renderFixes(view.fixes);
  renderComparison(view.comparison);

  const fmt = OUT_FORMAT[state.file.ext];
  el.downloadMain.textContent = `Скачать отчёт ${fmt}`;
  el.downloadAnnotated.hidden = !view.annotated;
  const notes = [];
  if (!view.complete) notes.push(`Отчёт неполный: ${view.incomplete_reason}.`);
  if (api.demo && fmt === 'PDF') notes.push('В демо отчёт выгружается в Markdown. PDF отдаёт сервер.');
  el.downloadHint.textContent = notes.join(' ');

  lockInputs(false);
  el.result.hidden = false;
  restartAnimation(el.result, 'is-in');
  countUp();
}

function fail(err) {
  el.start.classList.add('is-error');
  el.stage.textContent = 'Проверка остановлена';
  el.failureText.textContent = err.message;
  el.failure.hidden = false;
  lockInputs(false);
}

// ---------- результат ----------

function renderStats(items) {
  el.stats.replaceChildren();
  for (const it of items) {
    const div = document.createElement('div');
    div.className = 'stat' + (it.danger ? ' is-danger' : '');
    const dt = document.createElement('dt');
    dt.textContent = it.label;
    const dd = document.createElement('dd');
    dd.dataset.value = it.value;
    dd.textContent = '0';
    div.append(dt, dd);
    el.stats.append(div);
  }
}

function countUp() {
  const dds = [...el.stats.querySelectorAll('dd')];
  dds.forEach((dd, i) => {
    const target = +dd.dataset.value;
    if (reduceMotion.matches) {
      dd.textContent = target;
      return;
    }
    const delay = 200 + i * 60, dur = 400;
    const t0 = performance.now() + delay;
    const tick = (now) => {
      const t = Math.min(1, Math.max(0, (now - t0) / dur));
      dd.textContent = Math.round(target * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

function renderFixes(findings) {
  el.fixes.replaceChildren();
  for (const f of findings.slice(0, 5)) {
    const li = $('tpl-fix').content.firstElementChild.cloneNode(true);
    li.querySelector('.fix-id').textContent = f.id;
    const sev = li.querySelector('.fix-sev');
    sev.textContent = f.severity_name;
    sev.dataset.sev = f.severity;
    li.querySelector('.fix-title').textContent = f.title;
    li.querySelector('.fix-anchor').textContent = f.anchor;
    const quoteTpl = li.querySelector('.fix-quote');
    let lastQuote = quoteTpl;
    f.quotes.forEach((q, i) => {
      const node = i === 0 ? quoteTpl : quoteTpl.cloneNode();
      node.textContent = q;
      if (i > 0) lastQuote.after(node);
      lastQuote = node;
    });
    li.querySelector('.fix-text').textContent = f.explanation;
    if (f.suggestion) li.querySelector('.fix-suggestion-text').textContent = f.suggestion;
    else li.querySelector('.fix-suggestion').hidden = true;

    const head = li.querySelector('.fix-head');
    const body = li.querySelector('.collapse');
    body.id = 'fix-' + f.id;
    head.setAttribute('aria-controls', body.id);
    let sourceLoaded = false;
    head.addEventListener('click', () => {
      const open = head.getAttribute('aria-expanded') !== 'true';
      head.setAttribute('aria-expanded', String(open));
      body.classList.toggle('is-collapsed', !open);
      if (open && f.snippet && !sourceLoaded) {
        sourceLoaded = true;
        showSource(li.querySelector('.fix-source'), f);
      }
    });
    el.fixes.append(li);
  }
}

// Сравнение с одним промптом (демо): цифры считает сервер, здесь только раскладка.
function renderComparison(cmp) {
  el.compareResult.hidden = !cmp;
  if (!cmp) return;
  el.compareRows.replaceChildren();
  el.compareList.replaceChildren();
  const caption = el.compareResult.querySelector('.compare-caption');
  caption.hidden = Boolean(cmp.error) || !cmp.baseline_top.length;
  el.compareResult.querySelector('.compare-table').hidden = Boolean(cmp.error);
  if (cmp.error) {
    el.compareOverlap.textContent = cmp.error;
    return;
  }
  const money = (v) => '$' + v.toFixed(4).replace('.', ',');
  const secs = (v) => (v == null ? '—' : `${Math.round(v)} с`);
  const rows = [
    ['Находок в отчёте', cmp.pipeline.findings, cmp.baseline.findings],
    ['Блокирующих', cmp.pipeline.blockers, cmp.baseline.blockers],
    ['Существенных', cmp.pipeline.major, cmp.baseline.major],
    ['Отброшено: цитаты нет в тексте', cmp.pipeline.dropped, cmp.baseline.dropped],
    ['Стоимость', money(cmp.pipeline.cost_usd), money(cmp.baseline.cost_usd)],
    ['Время', secs(cmp.pipeline.seconds), secs(cmp.baseline.seconds)],
  ];
  for (const [label, a, b] of rows) {
    const tr = document.createElement('tr');
    const th = Object.assign(document.createElement('th'), { scope: 'row', textContent: label });
    tr.append(th, ...[a, b].map((v) => Object.assign(document.createElement('td'), { textContent: v })));
    el.compareRows.append(tr);
  }
  const o = cmp.overlap;
  el.compareOverlap.textContent =
    `Одно и то же место нашли оба: ${o.both}. Только конвейер: ${o.only_pipeline}. Только один промпт: ${o.only_baseline}.`;
  for (const f of cmp.baseline_top) {
    const li = document.createElement('li');
    li.className = 'compare-item';
    const head = document.createElement('p');
    head.className = 'compare-item-head';
    const sev = Object.assign(document.createElement('span'), { className: 'fix-sev', textContent: f.severity_name });
    sev.dataset.sev = f.severity;
    const cat = Object.assign(document.createElement('span'), { textContent: f.category });
    const mark = Object.assign(document.createElement('span'), {
      className: 'compare-mark', textContent: f.in_pipeline ? 'есть и у конвейера' : 'только у одного промпта',
    });
    head.append(sev, cat, mark);
    const q = Object.assign(document.createElement('blockquote'), { className: 'fix-quote', textContent: f.quote });
    li.append(head, q);
    el.compareList.append(li);
  }
}

// Фрагмент оригинала (ADR-009): PNG области страницы для PDF, абзацы для Markdown/TXT.
// Грузится при первом раскрытии строки; не загрузился — блока просто нет.
async function showSource(fig, f) {
  try {
    const snip = await api.snippet(f.id);
    if (snip.kind === 'image') {
      const img = Object.assign(document.createElement('img'), {
        src: snip.url, alt: 'Фрагмент страницы с цитатой: ' + f.quotes[0], loading: 'lazy',
      });
      fig.append(img);
    } else {
      for (const para of snip.paragraphs) {
        const p = document.createElement('p');
        p.className = 'fix-source-para' + (para.highlight ? ' is-hit' : '');
        p.textContent = para.text;
        fig.append(p);
      }
    }
    fig.hidden = false;
  } catch {
    // фрагмент — дополнение: без него находка остаётся полной
  }
}

async function download(kind) {
  try {
    const { filename, blob } = await api.download(kind);
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: filename });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    el.downloadHint.textContent = 'Не удалось скачать отчёт: ' + err.message;
  }
}

el.downloadMain.addEventListener('click', () => download('main'));
el.downloadJson.addEventListener('click', () => download('json'));
el.downloadAnnotated.addEventListener('click', () => download('annotated'));

function resetRun() {
  el.result.hidden = true;
  el.failure.hidden = true;
  el.stages.hidden = true;
  el.run.classList.remove('is-running', 'is-gone');
  el.start.classList.remove('is-progress', 'is-collapsed', 'is-error');
  el.start.style.width = '';
  setProgress(0);
}

el.reset.addEventListener('click', () => {
  api.dispose();
  resetRun();
  clearFile();
  window.scrollTo({ top: 0, behavior: reduceMotion.matches ? 'auto' : 'smooth' });
});

el.retry.addEventListener('click', () => {
  if (api.demo) api.failAtStage = null; // в демо повтор проходит успешно
  resetRun();
  start();
});

el.start.addEventListener('click', start);

loadModels();
