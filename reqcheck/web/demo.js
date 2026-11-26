// Демо-адаптер: имитирует API проверки на фиктивных данных, пока нет сервера.
// Интерфейс тот же, что будет у HTTP-адаптера: listModels() и run(file, model, onEvent).
// Цитаты в находках взяты дословно из docs/requirements.md репозитория ТЗ, остальное придумано для показа.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Цены OpenRouter на 2026-11-25, $ за 1M токенов (вход / выход).
export const MODELS = [
  { id: 'deepseek/deepseek-v4.1-flash', in: 0.051, out: 0.203 },
  { id: 'openai/gpt-6-luna', in: 0.10, out: 0.50, default: true },
  { id: 'anthropic/claude-haiku-5.5', in: 0.10, out: 0.50 },
  { id: 'qwen/qwen3.8-flash', in: 0.15, out: 0.47 },
  { id: 'google/gemini-3.8-flash', in: 0.825, out: 4.125 },
  { id: 'openai/gpt-6.1-terra', in: 1.00, out: 5.00 },
  { id: 'anthropic/claude-sonnet-5.5', in: 2.00, out: 10.00 },
];

// Оценка прогона документа ~10 страниц: ~40k токенов на вход, ~30k на выход.
export const estimateRun = (m) => (40_000 * m.in + 30_000 * m.out) / 1e6;

const STAGES = [
  { name: 'Чтение документа', ms: 500 },
  { name: 'Выделение требований', ms: 1400 },
  { name: 'Проверка каждого требования', ms: 2600 },
  { name: 'Проверка набора', ms: 1900 },
  { name: 'Проверка цитат', ms: 600 },
  { name: 'Вопросы к автору', ms: 1000 },
  { name: 'Вердикт', ms: 250 },
  { name: 'Отчёт', ms: 450 },
];
export const STAGE_NAMES = STAGES.map((s) => s.name);

const FINDINGS = [
  {
    id: 'F-012', severity: 'blocker', category: 'contradiction',
    title: 'Ф-2 противоречит критерию приёмки 2',
    anchor: 'Часть A › Функциональные требования › Ф-2 ↔ Критерии приёмки, строка 2',
    quotes: ['Месяц определяется по дате совершения покупки в Алматы.', 'Февральский счётчик вырос на 4 000 ₸'],
    explanation: 'По Ф-2 покупка 31 января относится к январю, а критерий приёмки ожидает рост февральского счётчика.',
    suggestion: 'Выбрать одно правило (дата совершения или дата проведения) и привести к нему Ф-2 и критерий приёмки.',
  },
  {
    id: 'F-013', severity: 'blocker', category: 'contradiction',
    title: 'Ф-3 и Ф-4 по-разному ограничивают push',
    anchor: 'Часть A › Функциональные требования › Ф-3 ↔ Ф-4',
    quotes: ['На карту приходится максимум один такой push за календарный месяц.', 'следующее достижение вызывает push независимо от истории предыдущей настройки'],
    explanation: 'После смены порога Ф-4 разрешает второй push в том же месяце, а Ф-3 его запрещает.',
    suggestion: 'Указать, сбрасывает ли изменение порога месячный лимит push.',
  },
  {
    id: 'F-015', severity: 'blocker', category: 'contradiction',
    title: 'Метод назван и сохранением, и чтением порога',
    anchor: 'Часть B › Общие параметры › Description, REST type',
    quotes: ['Сохранение порога. Функциональные требования: чтение порога'],
    explanation: 'Назначение метода описано двояко, а тип запроса GET не подходит для изменения данных.',
    suggestion: 'Разделить чтение и сохранение на два метода или описать единственное назначение и подходящий тип запроса.',
  },
  {
    id: 'F-016', severity: 'blocker', category: 'contradiction',
    title: 'Раздел статуса 403 описывает ответ 404',
    anchor: 'Часть B › Статусы › 403',
    quotes: ['Для чужой карты возвращается 404, как и для отсутствующей карты.'],
    explanation: 'Не сказано, когда возвращается 403, а код 404 не входит в перечень статусов.',
    suggestion: 'Описать условия ответа 403 и добавить 404 в перечень статусов с примером ответа.',
  },
  {
    id: 'F-007', severity: 'major', category: 'incomplete',
    title: 'Не задано, как измерять «в течение минуты»',
    anchor: 'Часть A › Нефункциональные требования',
    quotes: ['Сервис передаёт push на отправку в течение минуты после получения события.'],
    explanation: 'Нет доли событий, нагрузки и точки измерения, поэтому требование нельзя однозначно проверить.',
    suggestion: 'Для 99 % событий при нагрузке до N событий/с push передаётся на отправку не позднее 60 с после получения события.',
  },
];

function buildReport(file, model) {
  return {
    schema_version: '1.0',
    demo: true,
    run: {
      document: { name: file.name, format: file.ext },
      started_at: new Date().toISOString(),
      model: model.id,
      agent_version: 'v0.1-demo',
      llm: { calls: 7, prompt_tokens: 41200, completion_tokens: 18900, cost_usd: +estimateRun(model).toFixed(4) },
      complete: true,
    },
    summary: {
      requirements_total: 24, split_composites: 3, violations_total: 17, unclear_total: 4,
      by_severity: { blocker: 4, major: 8, minor: 5 },
    },
    verdict: {
      can_start_development: false,
      can_start_testing: false,
      reason: '4 блокирующих дефекта; 2 требования непроверяемы',
      rule: 'blocker >= 1',
      top_fixes: FINDINGS.map((f) => f.id),
    },
    findings: FINDINGS,
  };
}

export function toMarkdown(r) {
  const sev = { blocker: 'блокирующий', major: 'существенный', minor: 'незначительный' };
  const lines = [
    `# Отчёт о проверке требований: ${r.run.document.name}`,
    '',
    '> Демонстрационный отчёт на фиктивных данных. Настоящий отчёт формирует агент.',
    '',
    '## 1. Паспорт прогона',
    `- Документ: ${r.run.document.name}`,
    `- Дата: ${r.run.started_at}`,
    `- Модель: ${r.run.model}`,
    `- Версия агента: ${r.run.agent_version}`,
    `- Вызовы LLM: ${r.run.llm.calls}, стоимость: $${r.run.llm.cost_usd}`,
    '',
    '## 2–4. Требования и проверки',
    '(в демо опущено)',
    '',
    '## 5. Сводка',
    `- Требований: ${r.summary.requirements_total}`,
    `- Нарушений: ${r.summary.violations_total} (блокирующих ${r.summary.by_severity.blocker})`,
    '',
    '## 7. Итог',
    `- Разработка: ${r.verdict.can_start_development ? 'можно начинать' : 'нельзя начинать'}`,
    `- Причина: ${r.verdict.reason} (правило: ${r.verdict.rule})`,
    '',
    '### Исправить в первую очередь',
  ];
  r.findings.forEach((f, i) => {
    lines.push(`${i + 1}. **${f.id} · ${sev[f.severity]} · ${f.title}**`);
    lines.push(`   - Место: ${f.anchor}`);
    f.quotes.forEach((q) => lines.push(`   - Цитата: «${q}»`));
    lines.push(`   - ${f.explanation}`);
    lines.push(`   - Предложение: ${f.suggestion}`);
  });
  return lines.join('\n') + '\n';
}

const SEV_NAMES = { blocker: 'блокирующий', major: 'существенный', minor: 'незначительный' };

// Тот же вид, что отдаёт сервер в GET /checks/{id}/summary (reqcheck/server/view.py).
function toView(r) {
  const blockers = r.summary.by_severity.blocker;
  return {
    can_start_development: r.verdict.can_start_development,
    can_start_testing: r.verdict.can_start_testing,
    headline: `Нельзя начинать: ${blockers} блокирующих дефекта`,
    sub: 'Тест-дизайн тоже рано: 2 требования непроверяемы.',
    stats: {
      requirements: r.summary.requirements_total,
      violations: r.summary.violations_total,
      blockers,
    },
    fixes: r.findings.map((f) => ({ ...f, severity_name: SEV_NAMES[f.severity] })),
    complete: true,
    incomplete_reason: null,
  };
}

export class DemoApi {
  demo = true;

  constructor({ failAtStage = null } = {}) {
    this.failAtStage = failAtStage;
  }

  async loadExample() {
    return { name: 'requirements.md', size: 7_912, blob: null };
  }

  async download(kind) {
    const base = this.file.name.replace(/\.[^.]+$/, '');
    if (kind === 'json') {
      return { filename: `${base}-review.json`, blob: new Blob([JSON.stringify(this.report, null, 2)], { type: 'application/json' }) };
    }
    return { filename: `${base}-review.md`, blob: new Blob([toMarkdown(this.report)], { type: 'text/markdown;charset=utf-8' }) };
  }

  async dispose() {}

  async listModels() {
    return MODELS;
  }

  async run(file, model, onEvent) {
    for (let i = 0; i < STAGES.length; i++) {
      const s = STAGES[i];
      onEvent({ type: 'stage', index: i, state: 'active' });
      await sleep(s.ms);
      if (this.failAtStage === i) {
        onEvent({ type: 'stage', index: i, state: 'error' });
        throw new Error(`Модель дважды вернула ответ не по схеме. Этап «${s.name}» не выполнен.`);
      }
      onEvent({ type: 'stage', index: i, state: 'done' });
    }
    this.file = file;
    this.report = buildReport(file, model);
    return toView(this.report);
  }
}
