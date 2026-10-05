"use strict";

const countEl = id => document.getElementById(id);
let countProducts = [], countItems = [], countCorridors = [], countPositions = [], countRows = [];
let countSchemaReady = false, countBaseReady = false, countHistoryLoaded = false;
let countSaving = false, countLoading = false, countApplying = false, countUncertain = false;
let countPage = 1, countHistoryVersion = 0, countApplyTarget = null;
const COUNT_SITUATION = {
    PENDENTE: { label: "Pendente", badge: "warning" },
    APLICADA: { label: "Aplicada", badge: "success" },
    SEM_DIVERGENCIA: { label: "Sem divergência", badge: "" }
};

function countMessage(id, message, error = false) {
    countEl(id).textContent = message;
    countEl(id).dataset.error = String(error);
}

const formatDivergence = value => (Number(value) > 0 ? `+${value}` : String(value));
const divergenceClass = value => (value > 0 ? "divergence-positive" : value < 0 ? "divergence-negative" : "");
function formatInstant(value) {
    if (!value) return "—";
    const time = new Date(value);
    return Number.isNaN(time.getTime()) ? "—" : time.toLocaleString("pt-BR");
}
function infoList(pairs) {
    return pairs.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
}

// Disponibilidade vem da resposta do backend (503), nunca de flag do cliente.
function setSchemaState(ready, message) {
    countSchemaReady = ready;
    countEl("countSchemaNotice").hidden = ready;
    if (!ready) countEl("countSchemaNotice").textContent = `${message || "Contagens indisponíveis."} Registro e aplicação ` +
        "permanecem bloqueados até a instalação manual do SQL da Etapa 2D; as demais telas continuam disponíveis.";
}

function updateRegisterState() {
    const ready = countSchemaReady && countBaseReady && !countUncertain && canPerform("counts:create");
    countEl("countFields").disabled = !ready || countSaving || countLoading || !countProducts.length;
}

function renderItemInfo() {
    const item = countItems.find(i => i.id_item_estoque === Number(countEl("countItem").value));
    countEl("countItemInfo").innerHTML = item ? infoList([["SKU", item.produto_codigo], ["Corredor", item.corredor],
        ["Posição", item.codigo_posicao], ["Lote", item.lote], ["Status", item.status],
        ["Saldo atual (referência)", `${item.quantidade} un.`]]) : infoList([["Item", "Selecione produto e lote."]]);
}

function renderItemOptions() {
    const product = Number(countEl("countProduct").value);
    const selected = countEl("countItem").value;
    const rows = countItems.filter(i => i.id_produto === product);
    countEl("countItem").innerHTML = `<option value="">${!product ? "Selecione o produto" : rows.length ? "Selecione lote e posição" :
        "Produto sem lotes cadastrados (use Entrada)"}</option>` + rows.map(i =>
        `<option value="${i.id_item_estoque}">${escapeHtml(i.corredor)} / ${escapeHtml(i.codigo_posicao)} — lote ${escapeHtml(i.lote)}</option>`).join("");
    countEl("countItem").value = rows.some(i => String(i.id_item_estoque) === selected) ? selected : "";
    renderItemInfo();
}

function renderPositionFilter() {
    const corridor = Number(countEl("filterCorridor").value);
    const selected = countEl("filterPosition").value;
    const rows = countPositions.filter(p => !corridor || p.id_corredor === corridor);
    countEl("filterPosition").innerHTML = '<option value="">Todas</option>' +
        rows.map(p => `<option value="${p.id_posicao}">${escapeHtml(p.codigo_posicao)}</option>`).join("");
    countEl("filterPosition").value = rows.some(p => String(p.id_posicao) === selected) ? selected : "";
}

function historyParams() {
    const params = {};
    for (const [id, key] of [["filterProduct", "id_produto"], ["filterCorridor", "id_corredor"], ["filterPosition", "id_posicao"],
        ["filterDivergence", "divergencia"], ["filterSituation", "situacao"]]) {
        if (countEl(id).value) params[key] = countEl(id).value;
    }
    const lot = countEl("filterLot").value.trim();
    if (lot) params.lote = lot;
    return params;
}

function renderHistory() {
    const body = countEl("countHistory");
    const pager = countEl("countPagination");
    pager.replaceChildren();
    if (!countHistoryLoaded) {
        body.innerHTML = `<tr><td colspan="11" class="empty">${countSchemaReady ? "Histórico indisponível. Tente atualizar." :
            "Contagens indisponíveis até a instalação do schema da Etapa 2D."}</td></tr>`;
        return;
    }
    const pages = Math.max(1, Math.ceil(countRows.length / 10));
    countPage = Math.min(countPage, pages);
    const canApply = canPerform("counts:apply") && countSchemaReady && !countUncertain;
    body.innerHTML = countRows.slice((countPage - 1) * 10, countPage * 10).map(row => {
        const meta = COUNT_SITUATION[row.situacao] || { label: row.situacao, badge: "" };
        const applied = row.situacao === "APLICADA" ? `<br><small>Ajuste #${escapeHtml(row.id_movimentacao_ajuste)} por ` +
            `${escapeHtml(row.aplicada_por || "—")} em ${escapeHtml(formatInstant(row.aplicada_em))}</small>` : "";
        const action = row.situacao === "PENDENTE" && canApply ?
            `<button class="btn secondary" type="button" data-apply-count="${Number(row.id_contagem)}">Aplicar ajuste</button>` : "—";
        return `<tr><td>${escapeHtml(formatInstant(row.data_hora))}</td>
            <td>${escapeHtml(row.produto_nome)}<br><small>${escapeHtml(row.produto_codigo)}</small></td>
            <td>${escapeHtml(row.corredor)} / ${escapeHtml(row.codigo_posicao)}<br><small>Lote ${escapeHtml(row.lote)}</small></td>
            <td>${escapeHtml(row.status_item)}</td><td>${escapeHtml(row.quantidade_sistema)}</td><td>${escapeHtml(row.quantidade_fisica)}</td>
            <td class="${divergenceClass(row.divergencia)}">${escapeHtml(formatDivergence(row.divergencia))}</td>
            <td>${escapeHtml(row.usuario_nome)}</td><td>${escapeHtml(row.observacao || "—")}</td>
            <td><span class="badge ${meta.badge}">${escapeHtml(meta.label)}</span>${applied}</td><td>${action}</td></tr>`;
    }).join("") || '<tr><td colspan="11" class="empty">Nenhuma contagem encontrada.</td></tr>';
    for (const [label, next] of [["Anterior", countPage - 1], ["Próxima", countPage + 1]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.disabled = next < 1 || next > pages;
        button.onclick = () => { countPage = next; renderHistory(); };
        pager.append(button);
    }
}

async function loadHistory() {
    const version = ++countHistoryVersion;
    countEl("countHistory").setAttribute("aria-busy", "true");
    const result = await apiContagem.listar(historyParams());
    if (version !== countHistoryVersion) return countHistoryLoaded;  // resposta obsoleta de filtro anterior
    countEl("countHistory").setAttribute("aria-busy", "false");
    if (result.status === 503) setSchemaState(false, result.data?.message);
    else if (result.ok) setSchemaState(true);
    countHistoryLoaded = result.ok && Array.isArray(result.data);
    countRows = countHistoryLoaded ? result.data : [];
    if (countHistoryLoaded) countMessage("historyFeedback", countRows.length ? `${countRows.length} contagem(ns) encontrada(s).` : "");
    else if (result.status !== 503) countMessage("historyFeedback", result.data?.message || "Não foi possível carregar o histórico. Tente atualizar.", true);
    else countMessage("historyFeedback", "");
    renderHistory();
    updateRegisterState();
    return countHistoryLoaded;
}

async function loadCounts() {
    if (countLoading) return false;
    countLoading = true;
    countEl("refreshCounts").disabled = true;
    updateRegisterState();
    try {
        const results = await Promise.all([apiProduto.listar(), apiLocalizacao.itens(), apiLocalizacao.corredores(), apiLocalizacao.posicoes()]);
        const [products, items, corridors, positions] = results.map(r => (r.ok && Array.isArray(r.data) ? r.data : null));
        countBaseReady = results.every(r => r.ok && Array.isArray(r.data));
        countProducts = products || []; countItems = items || []; countCorridors = corridors || []; countPositions = positions || [];
        const selected = countEl("countProduct").value || new URLSearchParams(location.search).get("produto") || "";
        const filtered = countEl("filterProduct").value, corridor = countEl("filterCorridor").value;
        const options = countProducts.map(p => `<option value="${p.id_produto}">${escapeHtml(p.nome)} — ${escapeHtml(p.codigo)}</option>`).join("");
        countEl("countProduct").innerHTML = `<option value="">${countProducts.length ? "Selecione um produto" : products ? "Nenhum produto cadastrado" : "Produtos indisponíveis"}</option>` + options;
        countEl("countProduct").value = countProducts.some(p => String(p.id_produto) === selected) ? selected : "";
        countEl("filterProduct").innerHTML = '<option value="">Todos</option>' + options;
        countEl("filterProduct").value = filtered;
        countEl("filterCorridor").innerHTML = '<option value="">Todos</option>' +
            countCorridors.map(c => `<option value="${c.id_corredor}">${escapeHtml(c.identificacao)}</option>`).join("");
        countEl("filterCorridor").value = corridor;
        renderPositionFilter();
        renderItemOptions();
        const historyOk = await loadHistory();
        if (!countBaseReady) {
            const failed = results.find(r => !r.ok);
            countMessage("countFeedback", failed?.data?.message || "Não foi possível carregar produtos e localizações. Atualize antes de registrar.", true);
        }
        // Só uma atualização completa libera novas escritas após resposta incerta.
        if (countBaseReady && historyOk) countUncertain = false;
        return countBaseReady && historyOk;
    } finally {
        countLoading = false;
        countEl("refreshCounts").disabled = false;
        updateRegisterState();
        renderHistory();
    }
}

function showResult(count) {
    const meta = COUNT_SITUATION[count.situacao] || { label: count.situacao };
    countEl("countResult").innerHTML = infoList([["Contagem", `#${count.id_contagem}`], ["Sistêmico (capturado)", count.quantidade_sistema],
        ["Físico", count.quantidade_fisica], ["Divergência", formatDivergence(count.divergencia)], ["Situação", meta.label],
        ["Responsável", count.usuario_nome], ["Data/hora", formatInstant(count.data_hora)]]);
    countEl("countResult").hidden = false;
}

async function submitCount(event) {
    event.preventDefault();
    if (countSaving || countLoading || countUncertain || !countSchemaReady || !canPerform("counts:create") ||
        !event.currentTarget.reportValidity()) return;
    const productId = Number(countEl("countProduct").value);
    const item = countItems.find(i => i.id_item_estoque === Number(countEl("countItem").value) && i.id_produto === productId);
    const raw = countEl("countPhysical").value.trim();
    const physical = Number(raw);
    if (!item) { countMessage("countFeedback", "Selecione um lote e posição do produto.", true); return; }
    if (!raw || !Number.isSafeInteger(physical) || physical < 0 || physical > 2147483647) {
        countMessage("countFeedback", "Informe uma quantidade física inteira entre 0 e 2147483647.", true);
        return;
    }
    const payload = { id_item_estoque: item.id_item_estoque, quantidade_fisica: physical };
    const note = countEl("countNote").value.trim();
    if (note) payload.observacao = note;
    countSaving = true;
    updateRegisterState();
    countEl("countForm").setAttribute("aria-busy", "true");
    countEl("countResult").hidden = true;
    try {
        const result = await apiContagem.registrar(payload);
        if (!result.ok) {
            if (result.status === 503) {
                setSchemaState(false, result.data?.message);
                countMessage("countFeedback", result.data?.message || "Contagens indisponíveis.", true);
            } else if (result.status <= 0 || result.status >= 500) {
                // A gravação pode ter ocorrido antes da falha: não repetir automaticamente.
                countUncertain = true;
                countMessage("countFeedback", "O servidor não confirmou o registro. A contagem pode ter sido gravada: atualize o histórico antes de registrar novamente.", true);
                renderHistory();
            } else {
                countMessage("countFeedback", mutationError(result, "Não foi possível registrar a contagem."), true);
            }
            return;
        }
        showResult(result.data);
        countEl("countPhysical").value = "";
        countEl("countNote").value = "";
        showToast("Contagem registrada.");
        countPage = 1;
        const refreshed = await loadCounts();
        countMessage("countFeedback", refreshed ? "Contagem registrada. Os valores abaixo foram capturados pelo servidor; o estoque não foi alterado." :
            "Contagem registrada. Atualize para consultar o histórico.", !refreshed);
    } finally {
        countSaving = false;
        countEl("countForm").setAttribute("aria-busy", "false");
        updateRegisterState();
    }
}

function openApply(id) {
    const row = countRows.find(r => r.id_contagem === id);
    if (!row || row.situacao !== "PENDENTE" || !canPerform("counts:apply") || !countSchemaReady || countUncertain) return;
    countApplyTarget = row;
    countEl("applySummary").innerHTML = infoList([["Contagem", `#${row.id_contagem}`], ["Produto", `${row.produto_nome} (${row.produto_codigo})`],
        ["Local / lote", `${row.corredor} / ${row.codigo_posicao} — lote ${row.lote}`], ["Sistêmico registrado", row.quantidade_sistema],
        ["Físico contado", row.quantidade_fisica], ["Divergência", formatDivergence(row.divergencia)]]);
    countEl("applyEffect").textContent = `Efeito: será criada uma movimentação AJUSTE auditável que define este lote nesta posição em ` +
        `${row.quantidade_fisica} unidade(s) (${formatDivergence(row.divergencia)}) e altera o estoque global do produto pela mesma diferença. ` +
        "Outros lotes não mudam. Se o item tiver sido movimentado após a contagem, o ajuste será recusado e uma nova contagem será necessária.";
    countEl("applyReason").value = "";
    countMessage("applyFeedback", "");
    countEl("confirmApply").disabled = false;
    countEl("applyModal").classList.add("active");
    countEl("applyReason").focus();
}

function closeApply() {
    if (countApplying) return;
    countEl("applyModal").classList.remove("active");
    countApplyTarget = null;
}

async function submitApply(event) {
    event.preventDefault();
    const row = countApplyTarget;
    if (!row || countApplying || countUncertain || !event.currentTarget.reportValidity()) return;
    const motivo = countEl("applyReason").value.trim();
    if (!motivo) { countMessage("applyFeedback", "Informe o motivo obrigatório do ajuste.", true); return; }
    countApplying = true;
    for (const id of ["confirmApply", "cancelApply", "closeApply"]) countEl(id).disabled = true;
    let done = false, blocked = false;
    try {
        const result = await apiContagem.aplicar(row.id_contagem, motivo);
        if (result.ok) {
            done = true;
            const move = result.data.movimentacao;
            countApplying = false;
            closeApply();
            showToast("Ajuste aplicado.");
            const refreshed = await loadCounts();
            countMessage("historyFeedback", `Ajuste #${move.id_movimentacao} aplicado: lote ${move.quantidade_item_anterior} → ` +
                `${move.quantidade_item_posterior}; estoque global ${move.estoque_anterior} → ${move.estoque_posterior}.` +
                (refreshed ? "" : " Atualize para consultar o histórico."), !refreshed);
            return;
        }
        blocked = true;
        if (result.status === 503) {
            setSchemaState(false, result.data?.message);
            countMessage("applyFeedback", result.data?.message || "Contagens indisponíveis.", true);
        } else if (result.status <= 0 || result.status >= 500) {
            countUncertain = true;
            countMessage("applyFeedback", "O servidor não confirmou o ajuste. Ele pode ter sido aplicado: feche e atualize o histórico antes de qualquer nova tentativa.", true);
            renderHistory();
        } else if (result.status === 409 || result.status === 404) {
            // Já aplicada, desatualizada, sem divergência ou capacidade: estado mudou no servidor.
            countMessage("applyFeedback", mutationError(result, "A contagem não pode ser aplicada."), true);
            await loadHistory();
        } else {
            blocked = false;
            countMessage("applyFeedback", mutationError(result, "Não foi possível aplicar a contagem."), true);
        }
    } finally {
        countApplying = false;
        countEl("cancelApply").disabled = countEl("closeApply").disabled = false;
        if (!done) countEl("confirmApply").disabled = blocked;
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    if (!await window.sessionReady) return;
    // Etapa 2E: permissões relidas após 403 (revogação) reaplicam as ações desta tela.
    document.addEventListener("inventaire:permissions", () => { updateRegisterState(); renderHistory(); });
    countEl("countProduct").onchange = renderItemOptions;
    countEl("countItem").onchange = renderItemInfo;
    countEl("countForm").onsubmit = submitCount;
    countEl("refreshCounts").onclick = loadCounts;
    countEl("countFilters").onsubmit = event => { event.preventDefault(); countPage = 1; loadHistory(); };
    for (const id of ["filterProduct", "filterCorridor", "filterPosition", "filterLot", "filterDivergence", "filterSituation"]) {
        countEl(id).addEventListener("change", () => {
            if (id === "filterCorridor") renderPositionFilter();
            countPage = 1;
            loadHistory();
        });
    }
    countEl("countHistory").onclick = event => {
        const button = event.target.closest("[data-apply-count]");
        if (button) openApply(Number(button.dataset.applyCount));
    };
    countEl("applyForm").onsubmit = submitApply;
    countEl("cancelApply").onclick = countEl("closeApply").onclick = closeApply;
    countEl("applyModal").onclick = event => { if (event.target === countEl("applyModal")) closeApply(); };
    document.addEventListener("keydown", event => { if (event.key === "Escape") closeApply(); });
    await loadCounts();
});
