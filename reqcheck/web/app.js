// ReqCheck UI: один экран, состояния S0–S4 из docs/05-design.md.
import { DemoApi, STAGE_NAMES, estimateRun, toMarkdown } from './demo.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const api = new DemoApi({ failAtStage: params.has('fail') ? 3 : null });
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');

const ACCEPT = { pdf: 'PDF', md: 'Markdown', markdown: 'Markdown', txt: 'Текст' };
const OUT_FORMAT = { pdf: 'PDF', md: 'MD', markdown: 'MD', txt: 'MD' };
const SEV = { blocker: 'блокирующий', major: 'существенный', minor: 'незначительный' };

const el = {
  drop: $('drop'), file: $('file'), chip: $('chip'), chipName: $('chip-name'), chipMeta: $('chip-meta'),
  chipRemove: $('chip-remove'), dropError: $('drop-error'), example: $('example'),
  model: $('model'), modelHint: $('model-hint'),
  run: $('run'), start: $('start'), stage: $('run-stage'), clock: $('run-clock'), progress: $('progress'),
  stages: $('stages'), stageList: $('stage-list'), stagesBody: $('stages-body'),
  stagesSummary: $('stages-summary'), stagesSummaryText: $('stages-summary-text'),
  result: $('result'), verdict: $('verdict'), verdictSub: $('verdict-sub'), stats: $('stats'), fixes: $('fixes'),
  downloadMain: $('download-main'), downloadJson: $('download-json'), downloadHint: $('download-hint'), reset: $('reset'),
  failure: $('failure'), failureText: $('failure-text'), retry: $('retry'),
};

const state = { file: null, models: [], report: null, running: false, timer: null, t0: 0 };

$('build').textContent = 'v0.1 · демо-данные';

// ---------- утилиты ----------

const fmtMoney = (v) => (v < 0.01 ? '< $0,01' : '≈ $' + v.toFixed(2).replace('.', ','));
const fmtPrice = (v) => '$' + String(+v.toFixed(3)).replace('.', ',');
const fmtSize = (b) => (b < 1024 * 1024 ? Math.max(1, Math.round(b / 1024)) + ' КБ' : (b / 1048576).toFixed(1).replace('.', ',') + ' МБ');
const fmtClock = (ms) => {
  const s = Math.floor(ms / 1000);
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
};
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
  state.models = await api.listModels();
  for (const m of state.models) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = `${m.id} · ${fmtMoney(estimateRun(m))}`;
    if (m.default) opt.selected = true;
    el.model.append(opt);
  }
  updateModelHint();
}

function currentModel() {
  return state.models.find((m) => m.id === el.model.value);
}

function updateModelHint() {
  const m = currentModel();
  el.modelHint.textContent = m ? `${fmtPrice(m.in)} / ${fmtPrice(m.out)} за 1M токенов · ${fmtMoney(estimateRun(m))} за прогон` : '';
}

el.model.addEventListener('change', updateModelHint);

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
  const ext = extOf(f.name);
  if (!ACCEPT[ext]) {
    setError('Нужен PDF, Markdown или TXT');
    return;
  }
  if (f.size > 20 * 1024 * 1024) {
    setError('Файл больше 20 МБ');
    return;
  }
  setError('');
  state.file = { name: f.name, size: f.size, ext };
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
el.example.addEventListener('click', () => acceptFile({ name: 'requirements.md', size: 7_912 }));

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
  if (ev.type !== 'stage') return;
  const li = el.stageList.children[ev.index];
  li.dataset.state = ev.state;
  if (ev.note) li.querySelector('.stage-note').textContent = ev.note;
  if (ev.ms != null) li.querySelector('.stage-time').textContent = (ev.ms / 1000).toFixed(1).replace('.', ',') + ' с';
  if (ev.state === 'active') {
    el.stage.textContent = `Этап ${ev.index + 1} из ${STAGE_NAMES.length} · ${STAGE_NAMES[ev.index]}`;
  }
  if (ev.state === 'done') setProgress((ev.index + 1) / STAGE_NAMES.length);
}

function setProgress(p) {
  el.start.style.setProperty('--p', p);
  el.progress.setAttribute('aria-valuenow', Math.round(p * 100));
}

function setStagesCollapsed(collapsed) {
  el.stagesBody.classList.toggle('is-collapsed', collapsed);
  el.stagesSummary.setAttribute('aria-expanded', String(!collapsed));
}

el.stagesSummary.addEventListener('click', () => {
  setStagesCollapsed(el.stagesSummary.getAttribute('aria-expanded') === 'true');
});

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
  setStagesCollapsed(false);
  el.stages.classList.remove('is-done');
  el.stages.hidden = false;

  el.stage.textContent = 'Подготовка';
  state.t0 = performance.now();
  el.clock.textContent = '00:00';
  state.timer = setInterval(() => (el.clock.textContent = fmtClock(performance.now() - state.t0)), 250);

  morphToProgress();

  try {
    const model = currentModel();
    const report = await api.run(state.file, model, onEvent);
    state.report = report;
    await finish(report, performance.now() - state.t0);
  } catch (err) {
    fail(err);
  } finally {
    clearInterval(state.timer);
    state.running = false;
  }
}

async function finish(report, elapsed) {
  setProgress(1);
  el.stage.textContent = 'Готово';
  await wait(400);

  el.start.classList.add('is-collapsed');
  await wait(200);
  el.run.classList.add('is-gone');

  const s = report.summary;
  el.stagesSummaryText.textContent =
    `${STAGE_NAMES.length} этапов · ${fmtClock(elapsed)} · ${report.run.llm.calls} вызовов LLM · ${fmtMoney(report.run.llm.cost_usd)}`;
  el.stages.classList.add('is-done');
  setStagesCollapsed(true);

  const blockers = s.by_severity.blocker;
  const can = report.verdict.can_start_development;
  el.verdict.textContent = can ? 'Можно начинать разработку' : 'Нельзя начинать разработку';
  el.verdict.classList.toggle('is-blocked', !can);
  el.verdictSub.textContent = can
    ? 'Блокирующих дефектов нет.'
    : `${blockers} ${plural(blockers, 'блокирующий дефект', 'блокирующих дефекта', 'блокирующих дефектов')}. ` +
      (report.verdict.can_start_testing ? '' : 'Тест-дизайн тоже рано: 2 требования непроверяемы.');

  renderStats([
    { label: 'требований', value: s.requirements_total },
    { label: plural(s.violations_total, 'нарушение', 'нарушения', 'нарушений'), value: s.violations_total },
    { label: plural(blockers, 'блокирующий', 'блокирующих', 'блокирующих'), value: blockers, danger: blockers > 0 },
  ]);
  renderFixes(report.findings);

  const fmt = OUT_FORMAT[state.file.ext];
  el.downloadMain.textContent = `Скачать отчёт ${fmt}`;
  el.downloadHint.textContent = fmt === 'PDF' ? 'В демо отчёт выгружается в Markdown. PDF появится вместе с сервером.' : '';

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
    sev.textContent = SEV[f.severity];
    sev.dataset.sev = f.severity;
    li.querySelector('.fix-title').textContent = f.title;
    li.querySelector('.fix-anchor').textContent = f.anchor;
    const quoteTpl = li.querySelector('.fix-quote');
    f.quotes.forEach((q, i) => {
      const node = i === 0 ? quoteTpl : quoteTpl.cloneNode();
      node.textContent = q;
      if (i > 0) li.querySelector('.fix-quote:last-of-type').after(node);
    });
    li.querySelector('.fix-text').textContent = f.explanation;
    li.querySelector('.fix-suggestion-text').textContent = f.suggestion;

    const head = li.querySelector('.fix-head');
    const body = li.querySelector('.collapse');
    body.id = 'fix-' + f.id;
    head.setAttribute('aria-controls', body.id);
    head.addEventListener('click', () => {
      const open = head.getAttribute('aria-expanded') !== 'true';
      head.setAttribute('aria-expanded', String(open));
      body.classList.toggle('is-collapsed', !open);
    });
    el.fixes.append(li);
  }
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const baseName = () => state.file.name.replace(/\.[^.]+$/, '');

el.downloadMain.addEventListener('click', () =>
  download(`${baseName()}-review.md`, toMarkdown(state.report), 'text/markdown;charset=utf-8'));
el.downloadJson.addEventListener('click', () =>
  download(`${baseName()}-review.json`, JSON.stringify(state.report, null, 2), 'application/json'));

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
  resetRun();
  clearFile();
  window.scrollTo({ top: 0, behavior: reduceMotion.matches ? 'auto' : 'smooth' });
});

el.retry.addEventListener('click', () => {
  api.failAtStage = null; // в демо повтор проходит успешно
  resetRun();
  start();
});

el.start.addEventListener('click', start);

loadModels();
