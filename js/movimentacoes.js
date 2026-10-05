"use strict";

let movementProducts = [];
let movementRows = [];
let movementsLoaded = false;
let movementProductsLoaded = false;
let movementSaving = false;
let movementLoading = false;
let movementPositions = [], movementItems = [], movementPhysicalLoaded = false;
let movementPage = 1;
let movementSuppliers = [], movementSuppliersState = 'loading';
const movementElement = id => document.getElementById(id);
const movementMeta = {
    ENTRADA: { label: "Entrada", badge: "success" },
    SAIDA: { label: "Saída", badge: "danger" },
    AJUSTE: { label: "Ajuste", badge: "warning" }
};

function movementFeedback(message, error = false) {
    const element = movementElement("movementFeedback");
    element.textContent = message;
    element.dataset.error = String(error);
}

function updateMovementType() {
    const type = movementElement("movementType").value;
    const adjustment = type === "AJUSTE";
    movementElement("movementAmountLabel").textContent = adjustment ? "Novo saldo do lote na posição" : "Quantidade";
    movementElement("movementAmount").min = adjustment ? "0" : "1";
    movementElement("movementReason").required = adjustment;
    movementElement("movementReasonLabel").textContent = adjustment ? "Motivo (obrigatório)" : "Motivo (opcional)";
    movementElement("movementAmountHelp").textContent = adjustment ? "Saldo final deste lote nesta posição, inclusive zero. Os outros lotes não mudam." :
        type === "SAIDA" ? "Unidades a retirar do saldo." : "Unidades a adicionar ao saldo.";
    updateMovementDestination();
}

function updateMovementDestination() {
    const entry = movementElement('movementType').value === 'ENTRADA';
    movementElement('entryDestination').hidden = !entry;
    movementElement('exitDestination').hidden = entry;
    movementElement('movementPosition').required = movementElement('movementLot').required = entry;
    movementElement('movementItem').required = !entry;
    const selected = movementElement('movementItem').value;
    const product = Number(movementElement('movementProduct').value);
    const rows = movementItems.filter(i => i.id_produto === product && (movementElement('movementType').value === 'AJUSTE' || i.status === 'DISPONIVEL'));
    movementElement('movementItem').innerHTML = '<option value="">Selecione lote e posição</option>' + rows.map(i =>
        `<option value="${i.id_item_estoque}">${escapeHtml(i.corredor)} / ${escapeHtml(i.codigo_posicao)} — lote ${escapeHtml(i.lote)} — ${i.quantidade} un. (${escapeHtml(i.status)})</option>`).join('');
    movementElement('movementItem').value = selected;
}

function updateMovementStock() {
    const product = movementProducts.find(p => p.id_produto === Number(movementElement("movementProduct").value));
    movementElement("movementStock").textContent = product ?
        `${product.nome} • SKU: ${product.codigo} • Estoque atual: ${product.estoque}` : "Selecione um produto para consultar SKU e estoque atual.";
    updateMovementDestination();
}

function renderMovementHistory() {
    const body = movementElement("movementHistory");
    if (!movementsLoaded) {
        body.innerHTML = '<tr><td colspan="11" class="empty">Histórico indisponível. Tente atualizar.</td></tr>';
        movementElement("movementPagination").replaceChildren();
        return;
    }
    const product = Number(movementElement("historyProduct").value);
    const type = movementElement("historyType").value;
    const term = normalizeSearchText(movementElement("historySearch").value);
    const rows = movementRows.filter(row => (!product || row.id_produto === product) && (!type || row.tipo === type) &&
        (!term || normalizeSearchText(`${row.produto_nome} ${row.produto_codigo} ${row.lote || ''} ${row.codigo_posicao || ''} ${row.corredor || ''} ${row.fornecedor_razao_social || ''} ${row.fornecedor_cnpj || ''}`).includes(term)));
    const pages = Math.max(1, Math.ceil(rows.length / 10));
    movementPage = Math.min(movementPage, pages);
    body.innerHTML = rows.slice((movementPage - 1) * 10, movementPage * 10).map(row => {
        const meta = movementMeta[row.tipo];
        const time = new Date(row.data_hora);
        const date = Number.isNaN(time.getTime()) ? "—" : time.toLocaleString("pt-BR");
        return `<tr><td>${escapeHtml(date)}</td><td>${escapeHtml(row.produto_nome)}</td><td>${escapeHtml(row.produto_codigo)}</td>
            <td><span class="badge ${meta?.badge || ""}">${escapeHtml(meta?.label || row.tipo)}</span></td>
            <td>${row.tipo === "AJUSTE" ? "Saldo alvo: " : ""}${escapeHtml(row.quantidade)}</td>
            <td>${escapeHtml(row.estoque_anterior)}</td><td>${escapeHtml(row.estoque_posterior)}</td>
            <td>${escapeHtml(row.usuario_nome)}</td><td>${escapeHtml(row.motivo || "—")}</td>
            <td>${row.id_item_estoque ? `${escapeHtml(row.corredor)} / ${escapeHtml(row.codigo_posicao)} · ${escapeHtml(row.lote)}<br>Lote: ${row.quantidade_item_anterior} → ${row.quantidade_item_posterior}` : 'Legado sem contexto físico'}</td>
            <td>${row.id_fornecedor ? `${escapeHtml(row.fornecedor_razao_social)}<br><small>${escapeHtml(formatCnpj(row.fornecedor_cnpj))}</small>` : '—'}</td></tr>`;
    }).join("") || '<tr><td colspan="11" class="empty">Nenhuma movimentação encontrada.</td></tr>';
    const pager = movementElement("movementPagination");
    pager.replaceChildren();
    for (const [label, next] of [["Anterior", movementPage - 1], ["Próxima", movementPage + 1]]) {
        const button = document.createElement("button");
        button.textContent = label;
        button.disabled = next < 1 || next > pages;
        button.onclick = () => { movementPage = next; renderMovementHistory(); };
        pager.append(button);
    }
}

async function loadMovements() {
    if (movementLoading) return false;
    movementLoading = true;
    movementElement("refreshMovements").disabled = true;
    movementElement("movementFields").disabled = true;
    movementElement("movementHistory").setAttribute("aria-busy", "true");
    try {
        const [products, history, positions, items] = await Promise.all([apiProduto.listar(), apiMovimentacao.listar(), apiLocalizacao.posicoes(), apiLocalizacao.itens(), loadMovementSuppliers()]);
        movementPhysicalLoaded = positions.ok && items.ok && Array.isArray(positions.data) && Array.isArray(items.data);
        movementPositions = movementPhysicalLoaded ? positions.data.filter(p => p.ativo && p.corredor_ativo) : [];
        movementItems = movementPhysicalLoaded ? items.data : [];
        const position = movementElement('movementPosition').value;
        movementElement('movementPosition').innerHTML = '<option value="">Selecione o destino</option>' + movementPositions.map(p =>
            `<option value="${p.id_posicao}">${escapeHtml(p.corredor)} / ${escapeHtml(p.codigo_posicao)}</option>`).join('');
        movementElement('movementPosition').value = position;
        const selected = movementElement("movementProduct").value || new URLSearchParams(location.search).get("produto") || "";
        const filtered = movementElement("historyProduct").value;
        movementProductsLoaded = products.ok && Array.isArray(products.data);
        movementProducts = movementProductsLoaded ? products.data : [];
        movementsLoaded = history.ok && Array.isArray(history.data);
        movementRows = movementsLoaded ? history.data.slice().sort((a, b) =>
            new Date(b.data_hora) - new Date(a.data_hora) || b.id_movimentacao - a.id_movimentacao) : [];
        const options = movementProducts.map(p => `<option value="${p.id_produto}">${escapeHtml(p.nome)} — ${escapeHtml(p.codigo)} (saldo ${p.estoque})</option>`).join("");
        movementElement("movementProduct").innerHTML = `<option value="">${movementProducts.length ? "Selecione um produto" : products.ok ? "Nenhum produto cadastrado" : "Produtos indisponíveis"}</option>` + options;
        movementElement("historyProduct").innerHTML = '<option value="">Todos</option>' + options;
        movementElement("movementProduct").value = selected;
        movementElement("historyProduct").value = filtered;
        updateMovementStock();
        renderMovementHistory();
        const ready = movementProductsLoaded && movementsLoaded && movementPhysicalLoaded;
        movementElement("movementFields").disabled = !ready || !movementProducts.length || movementSaving;
        if (!ready) movementFeedback(positions.data?.message || items.data?.message || "Não foi possível carregar produtos, localizações ou histórico. Atualize antes de registrar.", true);
        else movementFeedback('Produtos, localizações e histórico atualizados.');
        return ready;
    } finally {
        movementLoading = false;
        movementElement("refreshMovements").disabled = movementSaving;
        movementElement("movementHistory").setAttribute("aria-busy", "false");
    }
}

// Etapa 2G: fornecedores ATIVOS para a origem opcional da Entrada. Indisponibilidade (ex.: SQL 2G
// pendente → 503) não bloqueia Entradas sem fornecedor; o seletor fica desabilitado e explicado.
// Opção B aprovada: lista mínima /fornecedores/selecao-entrada (fornecedores:selecionar), também para o
// OPERADOR, que não acessa a página nem as APIs de cadastro/recebimentos. Sem a permissão, não consulta.
async function loadMovementSuppliers() {
    const select = movementElement('movementSupplier');
    if (!canPerform('fornecedores:selecionar')) { movementSuppliers = []; select.disabled = true; return; }
    const selected = select.value;
    movementSuppliersState = 'loading';
    const result = await apiFornecedor.selecaoEntrada();
    const valid = result.ok && Array.isArray(result.data?.itens);
    movementSuppliers = valid ? result.data.itens : [];
    movementSuppliersState = valid ? 'ready' : result.status === 503 ? 'pending' : 'error';
    select.innerHTML = '<option value="">Sem fornecedor</option>' + movementSuppliers.map(f =>
        `<option value="${f.id_fornecedor}">${escapeHtml(f.razao_social)} — ${escapeHtml(f.cnpj_formatado)}</option>`).join('');
    select.value = movementSuppliers.some(f => String(f.id_fornecedor) === selected) ? selected : '';
    select.disabled = !valid;
    movementElement('movementSupplierNote').textContent = valid
        ? (result.data.total > movementSuppliers.length ? `Mostrando ${movementSuppliers.length} de ${result.data.total} fornecedores ativos.` :
           'Somente fornecedores ativos. A razão social e o CNPJ ficam registrados no histórico desta Entrada.')
        : movementSuppliersState === 'pending' ? 'Fornecedores indisponíveis: o SQL da Etapa 2G ainda não foi aplicado. Registre a Entrada sem fornecedor.'
        : 'Não foi possível carregar os fornecedores. Atualize para tentar novamente; a Entrada sem fornecedor continua disponível.';
}

// Etapa 2E: só os tipos permitidos pelo servidor ficam selecionáveis (o backend revalida o tipo).
function applyMovementTypePermissions() {
    const select = movementElement("movementType");
    let first = null;
    [...select.options].forEach(option => {
        const allowed = canPerform(`movimentacoes:${option.value.toLowerCase()}`);
        option.hidden = option.disabled = !allowed;
        if (allowed && first === null) first = option.value;
    });
    if (select.selectedOptions[0]?.disabled) select.value = first ?? "";
    updateMovementType();
}

async function submitMovement(event) {
    event.preventDefault();
    if (movementSaving || movementLoading || !movementsLoaded || !movementPhysicalLoaded || !canPerform("movements:create") || !event.currentTarget.reportValidity()) return;
    const productId = Number(movementElement("movementProduct").value);
    const type = movementElement("movementType").value;
    if (!canPerform(`movimentacoes:${type.toLowerCase()}`)) {
        movementFeedback("Seu perfil não possui permissão para este tipo de movimentação.", true);
        return;
    }
    const amount = Number(movementElement("movementAmount").value);
    if (!Number.isSafeInteger(productId) || productId <= 0 || !Number.isSafeInteger(amount) ||
        amount < (type === "AJUSTE" ? 0 : 1) || amount > 2147483647) {
        movementFeedback("Informe um produto e uma quantidade inteira válida.", true);
        return;
    }
    const payload = { tipo: type, motivo: movementElement("movementReason").value.trim() };
    if (type === 'ENTRADA') {
        payload.id_posicao = Number(movementElement('movementPosition').value);
        payload.lote = movementElement('movementLot').value.trim();
        // Origem opcional: só ID de fornecedor ativo carregado; snapshots são capturados pelo servidor.
        const supplier = movementElement('movementSupplier').value;
        if (supplier) {
            if (!movementSuppliers.some(f => String(f.id_fornecedor) === supplier)) { movementFeedback('Selecione um fornecedor ativo válido.', true); return; }
            payload.id_fornecedor = Number(supplier);
        }
    } else {
        const item = movementItems.find(i => i.id_item_estoque === Number(movementElement('movementItem').value) && i.id_produto === productId);
        if (!item) { movementFeedback('Selecione um lote e posição do produto.', true); return; }
        payload.id_posicao = item.id_posicao; payload.lote = item.lote;
    }
    if (!payload.id_posicao || !payload.lote) { movementFeedback('Informe posição e lote.', true); return; }
    payload[type === "AJUSTE" ? "novo_saldo_item" : "quantidade"] = amount;
    if (type === "AJUSTE" && !payload.motivo) {
        movementFeedback("Informe o motivo obrigatório do ajuste.", true);
        return;
    }
    movementSaving = true;
    movementElement("movementFields").disabled = true;
    movementElement("refreshMovements").disabled = true;
    movementElement("movementForm").setAttribute("aria-busy", "true");
    try {
        const result = await apiMovimentacao.registrar(productId, payload);
        if (!result.ok) {
            movementFeedback(result.status === 503 ? (result.data?.message || "Funcionalidade indisponível: schema pendente.")
                : mutationError(result, "Não foi possível registrar a movimentação."), true);
            // Falha de rede/servidor pode ocorrer depois do commit. Não repetir automaticamente.
            // 503 é recusa explícita (schema pendente, com rollback), não resultado incerto.
            if (result.status <= 0 || (result.status >= 500 && result.status !== 503)) movementsLoaded = false;
            if (result.status === 409 && payload.id_fornecedor) await loadMovementSuppliers();
            return;
        }
        movementElement("movementAmount").value = "";
        movementElement("movementReason").value = "";
        movementElement("movementSupplier").value = "";
        const refreshed = await loadMovements();
        movementFeedback(refreshed ? "Movimentação registrada com sucesso." : "Movimentação registrada. Atualize para consultar o saldo e o histórico.", !refreshed);
        showToast("Movimentação registrada.");
    } finally {
        movementSaving = false;
        movementElement("movementFields").disabled = !movementsLoaded || !movementProductsLoaded || !movementPhysicalLoaded || !movementProducts.length;
        movementElement("refreshMovements").disabled = false;
        movementElement("movementForm").setAttribute("aria-busy", "false");
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    if (!await window.sessionReady) return;
    movementElement("movementProduct").onchange = updateMovementStock;
    movementElement("movementType").onchange = updateMovementType;
    movementElement("movementForm").onsubmit = submitMovement;
    for (const id of ["historyProduct", "historyType", "historySearch"]) {
        movementElement(id).addEventListener(id === "historySearch" ? "input" : "change", () => {
            movementPage = 1; renderMovementHistory();
        });
    }
    movementElement("refreshMovements").onclick = async () => {
        if (await loadMovements()) movementFeedback("Produtos e histórico atualizados.");
    };
    applyMovementTypePermissions();
    document.addEventListener("inventaire:permissions", applyMovementTypePermissions);
    await loadMovements();
});
