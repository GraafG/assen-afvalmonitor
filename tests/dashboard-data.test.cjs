const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, '..', 'scripts', 'dashboard-data.js'), 'utf8');
const NOW = Date.parse('2026-10-15T00:00:00Z');

function setup(meta = { updated: '2026-10-14T10:18:25.750163+02:00' }, responseStatus = 200) {
    const status = { textContent: '', dataset: {} };
    const requests = [];
    const errors = [];
    const context = vm.createContext({
        Date: class extends Date {
            static now() { return NOW; }
        },
        document: {
            getElementById(id) {
                assert.equal(id, 'data-status');
                return status;
            },
        },
        console: { error: (...args) => errors.push(args) },
        fetch: async (url, options) => {
            requests.push({ url, options });
            return {
                ok: responseStatus >= 200 && responseStatus < 300,
                status: responseStatus,
                json: async () => meta,
            };
        },
    });
    vm.runInContext(source, context);
    return { context, status, requests, errors };
}

test('data requests revalidate the cache, including history and event logs', async () => {
    const { context, requests } = setup();
    for (const file of ['containers_fillrates.json', 'containers.json', 'history.json', 'events.ndjson']) {
        const response = await context.fetchData(`data/${file}`);
        assert.equal(response.ok, true);
    }
    assert.equal(requests.length, 4);
    for (const request of requests) {
        assert.equal(request.options.cache, 'no-cache');
    }
});

test('HTTP errors reject instead of being consumed as data', async () => {
    const { context } = setup({}, 503);
    await assert.rejects(context.fetchData('data/history.json'), /data\/history\.json: HTTP 503/);
});

test('current data shows the collection date, not the page load date', async () => {
    const { context, status, requests, errors } = setup();
    await context.loadDataStatus();
    assert.equal(status.dataset.state, 'current');
    assert.match(status.textContent, /14 okt/);
    assert.match(status.textContent, /10:18/);
    assert.match(status.textContent, /Amsterdam/);
    assert.match(status.textContent, /Dagelijkse metingen/);
    assert.equal(requests[0].url, 'data/meta.json');
    assert.equal(requests[0].options.cache, 'no-cache');
    assert.equal(errors.length, 0);
});

test('Amsterdam timestamp handles winter time independently of the browser timezone', async () => {
    const { context, status } = setup({ updated: '2026-01-14T08:18:00Z' });
    await context.loadDataStatus();
    assert.match(status.textContent, /09:18/);
});

for (const [age, expected] of [
    [48 * 60 * 60 * 1000 - 1, 'current'],
    [48 * 60 * 60 * 1000, 'stale'],
    [72 * 60 * 60 * 1000, 'stale'],
]) {
    test(`freshness threshold at ${age} ms is ${expected}`, async () => {
        const { context, status } = setup({ updated: new Date(NOW - age).toISOString() });
        await context.loadDataStatus();
        assert.equal(status.dataset.state, expected);
        assert.equal(status.textContent.includes('Verouderde data'), expected === 'stale');
    });
}

test('location-only fallback never claims that fill data is current', async () => {
    const { context, status, requests } = setup();
    await context.loadDataStatus(false);
    assert.equal(status.dataset.state, 'error');
    assert.match(status.textContent, /Alleen containerlocaties/);
    assert.equal(requests.length, 0);
});

for (const meta of [{}, { updated: null }, { updated: 'invalid' }, { updated: 0 }, null]) {
    test(`invalid metadata ${JSON.stringify(meta)} is reported as unknown`, async () => {
        const { context, status, errors } = setup(meta);
        await context.loadDataStatus();
        assert.equal(status.dataset.state, 'error');
        assert.match(status.textContent, /ophaaltijd onbekend/);
        assert.equal(errors.length, 1);
    });
}

test('missing metadata does not present loaded fill data as fresh', async () => {
    const { context, status, errors } = setup({}, 404);
    await context.loadDataStatus();
    assert.equal(status.dataset.state, 'error');
    assert.match(status.textContent, /ophaaltijd onbekend/);
    assert.equal(errors.length, 1);
});

test('network failure is reported explicitly', async () => {
    const { context, status, errors } = setup();
    context.fetch = async () => { throw new Error('offline'); };
    await context.loadDataStatus();
    assert.equal(status.dataset.state, 'error');
    assert.equal(errors.length, 1);
    assert.match(errors[0][1].message, /offline/);
});

test('a successful retry clears an earlier error state', async () => {
    const { context, status } = setup();
    await context.loadDataStatus(false);
    assert.equal(status.dataset.state, 'error');
    await context.loadDataStatus();
    assert.equal(status.dataset.state, 'current');
    assert.doesNotMatch(status.textContent, /Alleen containerlocaties/);
});
