async function fetchData(url) {
    // Revalidate mutable data, not map tiles or other cacheable assets.
    const response = await fetch(url, { cache: 'no-cache' });
    if (!response.ok) {
        throw new Error(`${url}: HTTP ${response.status}`);
    }
    return response;
}

function setDataStatus(text, state = 'error') {
    const status = document.getElementById('data-status');
    status.textContent = text;
    status.dataset.state = state;
}

async function loadDataStatus(hasFillRates = true) {
    if (!hasFillRates) {
        setDataStatus('Alleen containerlocaties beschikbaar; vulgraaddata kon niet geladen worden.');
        return;
    }

    try {
        const response = await fetchData('data/meta.json');
        const meta = await response.json();
        const updated = typeof meta.updated === 'string' ? Date.parse(meta.updated) : NaN;
        if (!Number.isFinite(updated)) {
            throw new Error('Ongeldige ophaaltijd in data/meta.json');
        }

        const stale = Date.now() - updated >= 48 * 60 * 60 * 1000;
        const timestamp = new Date(updated).toLocaleString('nl-NL', {
            timeZone: 'Europe/Amsterdam',
            day: 'numeric', month: 'short', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
        setDataStatus(
            `${stale ? 'Verouderde data (48 uur of ouder). ' : ''}Laatst opgehaald: ${timestamp} (Amsterdam). Dagelijkse metingen.`,
            stale ? 'stale' : 'current'
        );
    } catch (error) {
        console.error('Ophaaltijd kon niet geladen worden:', error);
        setDataStatus('Vulgraaddata geladen; laatste ophaaltijd onbekend.');
    }
}
