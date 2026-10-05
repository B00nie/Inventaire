"use strict";
const loc = id => document.getElementById(id);
let locationCorridors = [], locationPositions = [];
let locationLoading = false, locationSaving = false, locationReady = false, searchVersion = 0;

function locationFeedback(message, error = false) {
    loc('locationFeedback').textContent = message;
    loc('locationFeedback').dataset.error = String(error);
}

async function searchLocations() {
    const version = ++searchVersion;
    const params = {q: loc('locationSearch').value.trim()};
    if (loc('locationCorridor').value) params.id_corredor = loc('locationCorridor').value;
    if (loc('locationPosition').value) params.id_posicao = loc('locationPosition').value;
    const product = new URLSearchParams(location.search).get('produto');
    if (product) params.id_produto = product;
    loc('locationItems').setAttribute('aria-busy', 'true');
    const result = await apiLocalizacao.itens(params);
    if (version !== searchVersion) return;
    loc('locationItems').setAttribute('aria-busy', 'false');
    loc('locationItems').innerHTML = result.ok && Array.isArray(result.data) ? result.data.map(i =>
        `<tr><td>${escapeHtml(i.produto_nome)}</td><td>${escapeHtml(i.produto_codigo)}</td><td>${escapeHtml(i.corredor)}</td>
         <td>${escapeHtml(i.codigo_posicao)}</td><td>${escapeHtml(i.lote)}</td><td>${i.quantidade}</td><td>${escapeHtml(i.status)}</td>
         <td><a href="movimentacoes.html?produto=${i.id_produto}">Movimentar</a></td></tr>`).join('') ||
        '<tr><td colspan="8">Nenhum lote encontrado.</td></tr>' : '<tr><td colspan="8">Rastreamento indisponível. Atualize para tentar novamente.</td></tr>';
    if (!result.ok) locationFeedback(result.data?.message || 'Não foi possível consultar os lotes.', true);
}

function filterLocationPositions() {
    const corridor = Number(loc('locationCorridor').value);
    loc('locationPosition').innerHTML = '<option value="">Todas as posições</option>' + locationPositions
        .filter(p => !corridor || p.id_corredor === corridor)
        .map(p => `<option value="${p.id_posicao}">${escapeHtml(p.codigo_posicao)}</option>`).join('');
}

async function loadLocations() {
    if (locationLoading) return;
    locationLoading = true;
    loc('refreshLocations').disabled = true;
    try {
        const [corridors, positions] = await Promise.all([apiLocalizacao.corredores(), apiLocalizacao.posicoes()]);
        locationReady = corridors.ok && positions.ok && Array.isArray(corridors.data) && Array.isArray(positions.data);
        loc('corridorFields').disabled = loc('positionFields').disabled = !locationReady || locationSaving;
        if (!locationReady) {
            locationFeedback(corridors.data?.message || positions.data?.message || 'Localizações indisponíveis.', true);
            loc('corridorsBody').innerHTML = '<tr><td colspan="5">Dados indisponíveis.</td></tr>';
            loc('positionsBody').innerHTML = '<tr><td colspan="4">Dados indisponíveis.</td></tr>';
            loc('locationItems').innerHTML = '<tr><td colspan="8">Rastreamento indisponível.</td></tr>';
            return;
        }
        locationFeedback('Localizações atualizadas.');
        locationCorridors = corridors.data; locationPositions = positions.data;
        const manage = canPerform('locations:manage');
        loc('corridorsBody').innerHTML = locationCorridors.map(c => `<tr><td>${escapeHtml(c.identificacao)}</td>
            <td>${c.ocupacao} / ${c.capacidade_maxima} unidades</td><td>${c.ativo ? 'Ativo' : 'Inativo'}</td>
            <td><button class="btn secondary" type="button" data-browse-corridor="${c.id_corredor}">Ver posições e lotes</button></td>
            <td>${manage ? `<button class="btn secondary" type="button" data-edit-corridor="${c.id_corredor}">Editar</button>` : '—'}</td></tr>`).join('') || '<tr><td colspan="5">Nenhum corredor cadastrado.</td></tr>';
        loc('positionsBody').innerHTML = locationPositions.map(p => `<tr><td>${escapeHtml(p.codigo_posicao)}</td><td>${escapeHtml(p.corredor)}</td>
            <td>${p.ativo && p.corredor_ativo ? 'Ativa' : 'Inativa / corredor inativo'}</td><td>${manage ? `<button class="btn secondary" type="button" data-edit-position="${p.id_posicao}">Editar</button>` : '—'}</td></tr>`).join('') || '<tr><td colspan="4">Nenhuma posição cadastrada.</td></tr>';
        const options = locationCorridors.map(c => `<option value="${c.id_corredor}">${escapeHtml(c.identificacao)}${c.ativo ? '' : ' (inativo)'}</option>`).join('');
        const selected = loc('positionCorridor').value, corridorFilter = loc('locationCorridor').value, positionFilter = loc('locationPosition').value;
        loc('positionCorridor').innerHTML = '<option value="">Selecione</option>' + options;
        loc('positionCorridor').value = selected;
        loc('locationCorridor').innerHTML = '<option value="">Todos os corredores</option>' + options;
        loc('locationCorridor').value = corridorFilter;
        filterLocationPositions();
        loc('locationPosition').value = positionFilter;
        await searchLocations();
    } finally { locationLoading = false; loc('refreshLocations').disabled = false; }
}

function editCorridor(id) {
    const c = locationCorridors.find(c => c.id_corredor === id);
    loc('corridorId').value = id; loc('corridorName').value = c.identificacao;
    loc('corridorCapacity').value = c.capacidade_maxima; loc('corridorActive').checked = c.ativo;
    loc('corridorName').focus();
}
function editPosition(id) {
    const p = locationPositions.find(p => p.id_posicao === id);
    loc('positionId').value = id; loc('positionCode').value = p.codigo_posicao;
    loc('positionCorridor').value = p.id_corredor; loc('positionCorridor').disabled = true;
    loc('positionActive').checked = p.ativo; loc('positionCode').focus();
}
async function saveLocation(event, corridor) {
    event.preventDefault();
    if (!canPerform('locations:manage') || locationSaving || locationLoading || !locationReady || !event.currentTarget.reportValidity()) return;
    const id = Number(loc(corridor ? 'corridorId' : 'positionId').value);
    const data = corridor ? {identificacao: loc('corridorName').value.trim(), capacidade_maxima: Number(loc('corridorCapacity').value), ativo: loc('corridorActive').checked} :
        {codigo_posicao: loc('positionCode').value.trim(), id_corredor: Number(loc('positionCorridor').value), ativo: loc('positionActive').checked};
    locationSaving = true;
    loc('corridorFields').disabled = loc('positionFields').disabled = true;
    try {
        const result = await (corridor ? apiLocalizacao.salvarCorredor(id, data) : apiLocalizacao.salvarPosicao(id, data));
        if (!result.ok) {
            locationFeedback(mutationError(result, 'Falha ao salvar localização.'), true);
            if (result.status <= 0 || result.status >= 500) locationReady = false;
            return;
        }
        event.target.reset(); loc(corridor ? 'corridorId' : 'positionId').value = '';
        loc('positionCorridor').disabled = false;
        await loadLocations();
        if (locationReady) locationFeedback('Localização salva.');
    } finally {
        locationSaving = false;
        loc('corridorFields').disabled = loc('positionFields').disabled = !locationReady;
    }
}
document.addEventListener('DOMContentLoaded', async () => {
    if (!await window.sessionReady) return;
    // Etapa 2E: permissões relidas após 403 (revogação) reaplicam as ações desta tela.
    document.addEventListener('inventaire:permissions', loadLocations);
    const product = new URLSearchParams(location.search).get('produto');
    loc('productFilterNote').hidden = !product;
    loc('corridorForm').onsubmit = e => saveLocation(e, true);
    loc('positionForm').onsubmit = e => saveLocation(e, false);
    loc('positionForm').onreset = () => { loc('positionId').value = ''; loc('positionCorridor').disabled = false; };
    loc('corridorForm').onreset = () => { loc('corridorId').value = ''; };
    loc('refreshLocations').onclick = loadLocations;
    loc('searchLocationsForm').onsubmit = e => { e.preventDefault(); searchLocations(); };
    loc('locationCorridor').onchange = () => { filterLocationPositions(); searchLocations(); };
    loc('locationPosition').onchange = searchLocations;
    loc('corridorsBody').onclick = e => {
        const button = e.target.closest('button'); if (!button) return;
        if (button.dataset.editCorridor) editCorridor(Number(button.dataset.editCorridor));
        if (button.dataset.browseCorridor) {
            loc('locationCorridor').value = button.dataset.browseCorridor;
            filterLocationPositions(); searchLocations();
            loc('searchLocationsForm').scrollIntoView({behavior: 'smooth'});
        }
    };
    loc('positionsBody').onclick = e => { const b = e.target.closest('[data-edit-position]'); if (b) editPosition(Number(b.dataset.editPosition)); };
    await loadLocations();
});
