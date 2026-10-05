"use strict";

/**
 * dashboard.js — Etapa 2F. Fonte única: GET /dashboard/resumo (somente leitura; o servidor
 * calcula tudo em um único snapshot). Cards e tabelas:
 *   Produtos cadastrados        → estoque.total_produtos
 *   Unidades em estoque         → estoque.unidades_em_estoque (soma de produtos.estoque)
 *   No limite/abaixo do mínimo  → estoque.estoque_baixo (estoque <= quantidade_min)
 *   Divergências pendentes      → contagens.pendentes (divergência ≠ 0, sem AJUSTE vinculado)
 *   Movimentações (30 dias)     → movimentacoes_30_dias.eventos (relógio do servidor)
 * Carregando, zero real, vazio e falha são estados distintos: falha nunca vira zero.
 */

const dashEl = id => document.getElementById(id);
let dashboardLoading = false;

function setKpi(id, value, state, failed = false) {
    const card = dashEl(id);
    card.querySelector("[data-kpi-value]").textContent = value;
    card.querySelector("[data-kpi-state]").textContent = state;
    card.dataset.state = failed ? "error" : "ok";
}

function emptyRow(columns, message) {
    return `<tr><td colspan="${columns}" class="empty">${escapeHtml(message)}</td></tr>`;
}

// Contrato mínimo da resposta; qualquer desvio é tratado como falha, nunca como zero.
function validSummary(data) {
    const e = data?.estoque, c = data?.contagens, j = data?.movimentacoes_30_dias;
    return Boolean(e && c && j) && [e.total_produtos, e.unidades_em_estoque, e.estoque_baixo, e.abaixo_do_minimo,
        e.no_limite, e.sem_saldo, c.pendentes, c.com_divergencia, c.aplicadas, j.eventos].every(isCount)
        && ["reposicao", "movimentacoes_recentes", "divergencias_pendentes"].every(key => Array.isArray(data[key]))
        && Array.isArray(j.por_tipo);
}

function renderFailure(message) {
    for (const id of ["kpiProdutos", "kpiUnidades", "kpiBaixo", "kpiDivergencias", "kpiMovimentacoes"]) {
        setKpi(id, "—", message, true);
    }
    dashEl("dashboardReposicao").innerHTML = emptyRow(5, "Não foi possível carregar a reposição.");
    dashEl("dashboardDivergencias").innerHTML = emptyRow(6, "Não foi possível carregar as divergências.");
    dashEl("dashboardMovimentacoes").innerHTML = emptyRow(7, "Não foi possível carregar as movimentações.");
    dashEl("dashboardJanela").innerHTML = emptyRow(4, "Não foi possível carregar o resumo.");
}

function renderSummary(data) {
    const e = data.estoque, c = data.contagens, j = data.movimentacoes_30_dias;
    setKpi("kpiProdutos", formatNumber(e.total_produtos),
        e.total_produtos ? `${formatNumber(e.sem_saldo)} sem saldo` : "Nenhum produto cadastrado");
    setKpi("kpiUnidades", formatNumber(e.unidades_em_estoque), "Soma dos saldos globais atuais");
    setKpi("kpiBaixo", formatNumber(e.estoque_baixo),
        `${formatNumber(e.abaixo_do_minimo)} abaixo · ${formatNumber(e.no_limite)} no limite`);
    setKpi("kpiDivergencias", formatNumber(c.pendentes),
        `${formatNumber(c.com_divergencia)} com divergência · ${formatNumber(c.aplicadas)} aplicadas`);
    setKpi("kpiMovimentacoes", formatNumber(j.eventos), j.eventos ? "Eventos registrados" : "Nenhum evento no período");

    dashEl("dashboardReposicao").innerHTML = data.reposicao.length ? data.reposicao.map(item => `<tr>
        <td>${escapeHtml(item.nome)}<br><small>${escapeHtml(item.codigo)}</small></td>
        <td>${escapeHtml(formatNumber(item.estoque))}</td><td>${escapeHtml(formatNumber(item.quantidade_min))}</td>
        <td>${reposicaoBadges(item)}</td><td>${escapeHtml(sugestaoTexto(item))}</td></tr>`).join("")
        : emptyRow(5, "Nenhum produto no limite ou abaixo do estoque mínimo.");

    dashEl("dashboardDivergencias").innerHTML = data.divergencias_pendentes.length ? data.divergencias_pendentes.map(row => `<tr>
        <td>${escapeHtml(formatInstantLocal(row.data_hora))}</td>
        <td>${escapeHtml(row.produto_nome)}<br><small>${escapeHtml(row.produto_codigo)}</small></td>
        <td>${escapeHtml(row.corredor)} / ${escapeHtml(row.codigo_posicao)}<br><small>Lote ${escapeHtml(row.lote)}</small></td>
        <td>${escapeHtml(formatNumber(row.quantidade_sistema))}</td><td>${escapeHtml(formatNumber(row.quantidade_fisica))}</td>
        <td class="${row.divergencia > 0 ? "divergence-positive" : "divergence-negative"}">${escapeHtml(formatSigned(row.divergencia))}</td>
        </tr>`).join("") : emptyRow(6, "Nenhuma divergência pendente.");

    dashEl("dashboardMovimentacoes").innerHTML = data.movimentacoes_recentes.length ? data.movimentacoes_recentes.map(m => `<tr>
        <td>${escapeHtml(formatInstantLocal(m.data_hora))}</td>
        <td>${escapeHtml(m.produto_nome)}<br><small>${escapeHtml(m.produto_codigo)}</small></td>
        <td>${escapeHtml(TIPO_LABEL[m.tipo] || m.tipo)}</td><td>${escapeHtml(movimentoQuantidade(m))}</td>
        <td>${escapeHtml(formatNumber(m.estoque_anterior))} → ${escapeHtml(formatNumber(m.estoque_posterior))}</td>
        <td>${movimentoLocal(m)}</td><td>${escapeHtml(m.usuario_nome)}</td></tr>`).join("")
        : emptyRow(7, "Nenhuma movimentação registrada.");

    dashEl("dashboardJanela").innerHTML = j.por_tipo.map(t => `<tr><td>${escapeHtml(TIPO_LABEL[t.tipo] || t.tipo)}</td>
        <td>${escapeHtml(formatNumber(t.eventos))}</td>
        <td>${escapeHtml(t.unidades === null ? "não se aplica" : formatNumber(t.unidades))}</td>
        <td>${escapeHtml(formatSigned(t.variacao_liquida))}</td></tr>`).join("");
}

async function loadDashboard() {
    if (dashboardLoading) return;
    dashboardLoading = true;
    dashEl("dashboardRefresh").disabled = true;
    dashEl("dashboardKpis").setAttribute("aria-busy", "true");
    dashEl("dashboardStatus").textContent = "Carregando indicadores...";
    dashEl("dashboardStatus").dataset.error = "false";
    try {
        const result = await apiRelatorio.dashboard();
        if (!result.ok || !validSummary(result.data)) {
            const message = result.ok ? "Resposta do servidor fora do formato esperado." : readFailureMessage(result);
            renderFailure(message);
            dashEl("dashboardStatus").textContent = `Indicadores indisponíveis: ${message}`;
            dashEl("dashboardStatus").dataset.error = "true";
            return;
        }
        renderSummary(result.data);
        dashEl("dashboardStatus").textContent = `Dados do servidor em ${formatInstantLocal(result.data.gerado_em)}.`;
    } finally {
        dashboardLoading = false;
        dashEl("dashboardRefresh").disabled = false;
        dashEl("dashboardKpis").setAttribute("aria-busy", "false");
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    if (!await window.sessionReady) return;
    // Sem acesso a Relatórios (OPERADOR): cards continuam informativos, sem links para a página negada.
    if (!canAccessPage("reports")) {
        document.querySelectorAll('a[href^="relatorios.html"]').forEach(link => {
            if (link.classList.contains("btn")) link.remove();
            else { link.removeAttribute("href"); link.classList.add("dashboard-kpi-static"); }
        });
    }
    dashEl("dashboardRefresh").addEventListener("click", loadDashboard);
    loadDashboard();
});
