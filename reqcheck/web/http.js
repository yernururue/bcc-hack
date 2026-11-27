// HTTP-адаптер к /api/v1 (docs/04-integration.md). Интерфейс тот же, что у DemoApi в demo.js:
// listModels(), loadExample(), run(file, model, onEvent) → вид результата, download(kind),
// snippet(findingId), setToken(token), dispose().
//
// Токен (REQCHECK_API_TOKEN на сервере) уходит заголовком Authorization во всех запросах. Хранится в
// sessionStorage вкладки: закрыли вкладку — токен забыт. EventSource заголовков не умеет, поэтому с
// токеном поток событий читается через fetch.

const BASE = '/api/v1';
const TOKEN_KEY = 'reqcheck.token';

export class AuthRequired extends Error {
  constructor(message) {
    super(message || 'Нужен токен доступа');
    this.name = 'AuthRequired';
  }
}

async function fail(res) {
  let detail = '';
  try {
    detail = (await res.json()).detail;
  } catch {
    // тело не JSON: оставляем код
  }
  if (res.status === 401) throw new AuthRequired();
  throw new Error(detail || `Сервер ответил ${res.status}`);
}

async function ok(res) {
  if (!res.ok) await fail(res);
  return res;
}

function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return ''; // хранилище недоступно (приватный режим): токен живёт до перезагрузки
  }
}

// Разбор text/event-stream из fetch: события разделены пустой строкой, поля event: и data:.
async function readEvents(res, onEvent) {
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return false;
    buf += value.replace(/\r\n/g, '\n');
    let cut;
    while ((cut = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let type = 'message';
      let data = '';
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) type = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (data && onEvent(type, JSON.parse(data))) {
        await reader.cancel().catch(() => {});
        return true;
      }
    }
  }
}

export class HttpApi {
  demo = false;
  jobId = null;
  urls = [];
  token = readToken();

  setToken(token) {
    this.token = token.trim();
    try {
      if (this.token) sessionStorage.setItem(TOKEN_KEY, this.token);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      // см. readToken
    }
  }

  async fetch(path, init = {}) {
    const headers = new Headers(init.headers);
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`);
    const res = await fetch(path.startsWith('/') ? path : `${BASE}/${path}`, { ...init, headers });
    if (res.status === 401 && this.token) {
      // токен был, но сервер его не принял: отличаем от «токен не задан»
      throw new AuthRequired('Токен не подошёл');
    }
    return ok(res);
  }

  async listModels() {
    const rows = await (await this.fetch('models')).json();
    return rows.map((m) => ({ id: m.id, tier: m.tier, in: m.price_in, out: m.price_out, default: m.default }));
  }

  async loadExample() {
    const res = await ok(await fetch('example.pdf'));
    const blob = await res.blob();
    return { name: 'example.pdf', size: blob.size, blob };
  }

  async run(file, model, onEvent, { compare = false } = {}) {
    const form = new FormData();
    form.append('file', file.blob, file.name);
    form.append('model', model.id);
    if (compare) form.append('compare', 'true');
    const created = await (await this.fetch('checks', { method: 'POST', body: form })).json();
    this.jobId = created.id;
    this.file = file;
    const handle = (type, ev) => {
      if (type === 'stage') onEvent({ type: 'stage', index: ev.index, state: ev.state, note: ev.note });
      else if (type === 'compare') onEvent({ type: 'compare' });
    };
    if (this.token) await this.#streamWithFetch(created.id, handle);
    else await this.#streamWithEventSource(created.id, handle);
    return (await this.fetch(`checks/${created.id}/summary`)).json();
  }

  async #streamWithEventSource(id, handle) {
    await new Promise((resolve, reject) => {
      const es = new EventSource(`${BASE}/checks/${id}/events`);
      es.addEventListener('stage', (e) => handle('stage', JSON.parse(e.data)));
      es.addEventListener('compare', (e) => handle('compare', JSON.parse(e.data)));
      es.addEventListener('done', () => {
        es.close();
        resolve();
      });
      es.addEventListener('error', (e) => {
        if (e.data) {
          // событие error от сервера: проверка не удалась
          es.close();
          reject(new Error(JSON.parse(e.data).message));
        } else if (es.readyState === EventSource.CLOSED) {
          reject(new Error('Потеряна связь с сервером'));
        }
        // иначе EventSource сам переподключится и получит события заново
      });
    });
  }

  // Как EventSource, но с заголовком Authorization. Обрыв без итогового события — переподключение:
  // сервер отдаёт события заново с начала, повтор этапов интерфейсу не вредит.
  async #streamWithFetch(id, handle) {
    let error = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      const res = await this.fetch(`checks/${id}/events`, { headers: { Accept: 'text/event-stream' } });
      const finished = await readEvents(res, (type, data) => {
        if (type === 'done') return true;
        if (type === 'error') {
          error = new Error(data.message);
          return true;
        }
        handle(type, data);
        return false;
      }).catch(() => false);
      if (error) throw error;
      if (finished) return;
      await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
    }
    throw new Error('Потеряна связь с сервером');
  }

  async download(kind) {
    const path = kind === 'annotated' ? 'annotated' : kind === 'json' ? 'report?format=json' : 'report';
    const res = await this.fetch(`checks/${this.jobId}/${path}`);
    const m = /filename\*=UTF-8''([^;]+)/.exec(res.headers.get('content-disposition') || '');
    return { filename: m ? decodeURIComponent(m[1]) : 'report', blob: await res.blob() };
  }

  // Фрагмент оригинала для находки: { kind: 'image', url } или { kind: 'text', paragraphs }.
  async snippet(findingId) {
    const res = await this.fetch(`checks/${this.jobId}/findings/${encodeURIComponent(findingId)}/snippet`);
    if ((res.headers.get('content-type') || '').startsWith('image/')) {
      const url = URL.createObjectURL(await res.blob());
      this.urls.push(url);
      return { kind: 'image', url };
    }
    return { kind: 'text', paragraphs: (await res.json()).paragraphs };
  }

  async dispose() {
    this.urls.forEach((u) => URL.revokeObjectURL(u));
    this.urls = [];
    if (!this.jobId) return;
    const id = this.jobId;
    this.jobId = null;
    await this.fetch(`checks/${id}`, { method: 'DELETE' }).catch(() => {});
  }
}
