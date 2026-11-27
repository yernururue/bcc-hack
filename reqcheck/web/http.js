// HTTP-адаптер к /api/v1 (docs/04-integration.md). Интерфейс тот же, что у DemoApi в demo.js:
// listModels(), loadExample(), run(file, model, onEvent) → вид результата, download(kind), dispose().

const BASE = '/api/v1';

async function fail(res) {
  let detail = '';
  try {
    detail = (await res.json()).detail;
  } catch {
    // тело не JSON: оставляем код
  }
  throw new Error(detail || `Сервер ответил ${res.status}`);
}

async function ok(res) {
  if (!res.ok) await fail(res);
  return res;
}

export class HttpApi {
  demo = false;
  jobId = null;
  urls = [];

  async listModels() {
    const rows = await (await ok(await fetch(`${BASE}/models`))).json();
    return rows.map((m) => ({ id: m.id, tier: m.tier, in: m.price_in, out: m.price_out, default: m.default }));
  }

  async loadExample() {
    const res = await ok(await fetch('example.pdf'));
    const blob = await res.blob();
    return { name: 'example.pdf', size: blob.size, blob };
  }

  async run(file, model, onEvent) {
    const form = new FormData();
    form.append('file', file.blob, file.name);
    form.append('model', model.id);
    const created = await (await ok(await fetch(`${BASE}/checks`, { method: 'POST', body: form }))).json();
    this.jobId = created.id;
    this.file = file;

    await new Promise((resolve, reject) => {
      const es = new EventSource(`${BASE}/checks/${created.id}/events`);
      es.addEventListener('stage', (e) => {
        const ev = JSON.parse(e.data);
        onEvent({ type: 'stage', index: ev.index, state: ev.state, note: ev.note });
      });
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
    return (await ok(await fetch(`${BASE}/checks/${created.id}/summary`))).json();
  }

  async download(kind) {
    const path = kind === 'annotated' ? 'annotated' : kind === 'json' ? 'report?format=json' : 'report';
    const res = await ok(await fetch(`${BASE}/checks/${this.jobId}/${path}`));
    const m = /filename\*=UTF-8''([^;]+)/.exec(res.headers.get('content-disposition') || '');
    return { filename: m ? decodeURIComponent(m[1]) : 'report', blob: await res.blob() };
  }

  // Фрагмент оригинала для находки: { kind: 'image', url } или { kind: 'text', paragraphs }.
  async snippet(findingId) {
    const res = await ok(await fetch(`${BASE}/checks/${this.jobId}/findings/${encodeURIComponent(findingId)}/snippet`));
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
    await fetch(`${BASE}/checks/${id}`, { method: 'DELETE' }).catch(() => {});
  }
}
