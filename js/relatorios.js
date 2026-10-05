"use strict";

/**
 * relatorios.js — Etapa 2F. Cinco relatórios somente leitura, paginados no servidor.
 *
 * Período: o usuário escolhe dias locais [início, fim] (fim inclusive). São enviados instantes
 * ISO 8601 com o fuso do navegador: data_inicio = meia-noite local do dia inicial e
 * data_fim = meia-noite local do dia SEGUINTE ao final (limite exclusivo). O servidor valida
 * e aplica [data_inicio, data_fim); nenhuma comparação é feita com datas em texto.
 *
 * CSV (2F.2): gerado somente no navegador a partir de UMA resposta de /relatorios/<nome>/exportacao,
 * obtida pelo servidor numa única transação somente leitura (até 1.000 linhas, total do mesmo
 * snapshot). Nada é gravado no servidor. Falha ou resposta inválida cancela o arquivo inteiro.
 */

const repEl = id => document.getElementById(id);
const CSV_LIMIT = 1000;
const MAX_DAYS = 366;
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const reportState = {};
const reportOptions = { produtos: [], corredores: [], posicoes: [] };

const pad = n => String(n).padStart(2, "0");
const localDay = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const dayNumber = day => { const [y, m, d] = day.split("-").map(Number); return Date.UTC(y, m - 1, d) / 86400000; };
const value = id => repEl(id).value.trim();
const compact = params => Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined));
const emptyRow = (columns, message) => `<tr><td colspan="${columns}" class="empty">${escapeHtml(message)}</td></tr>`;
const productCell = (nome, codigo) => `${escapeHtml(nome)}<br><small>${escapeHtml(codigo)}</small>`;
const locationLink = id => `<a href="localizacoes.html?produto=${Number(id)}">Ver localização</a>`;

// Meia-noite local de um dia (mais N dias), como instante ISO com o deslocamento do navegador.
function instantFromDay(day, addDays = 0) {
    const [y, m, d] = day.split("-").map(Number);
    const time = new Date(y, m - 1, d + addDays, 0, 0, 0, 0);
    const offset = -time.getTimezoneOffset();
    const sign = offset >= 0 ? "+" : "-";
    const abs = Math.abs(offset);
    return `${localDay(time)}T${pad(time.getHours())}:${pad(time.getMinutes())}:${pad(time.getSeconds())}` +
        `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

function periodParams(key, required) {
    const start = repEl(`${key}Inicio`).value;
    const end = repEl(`${key}Fim`).value;
    if (!start && !end && !required) return { params: {} };
    if (!DAY_PATTERN.test(start) || !DAY_PATTERN.test(end)) {
        return { error: required ? "Informe a data inicial e a data final." : "Informe as duas datas ou deixe ambas vazias." };
    }
    const days = dayNumber(end) - dayNumber(start) + 1;
    if (days < 1) return { error: "A data final deve ser igual ou posterior à data inicial." };
    if (days > MAX_DAYS) return { error: `O período máximo é de ${MAX_DAYS} dias.` };
    return { params: { data_inicio: instantFromDay(start), data_fim: instantFromDay(end, 1) } };
}

function withPeriod(key, required, filters) {
    const period = periodParams(key, required);
    return period.error ? period : { params: { ...period.params, ...compact(filters) } };
}

function periodText(data) {
    const p = data.filtros?.periodo;
    return p ? ` Período: ${formatInstantLocal(p.data_inicio)} até antes de ${formatInstantLocal(p.data_fim_exclusivo)}.` : "";
}

function distribution(item) {
    const d = item.distribuicao || {};
    const mismatch = d.confere_com_saldo === false
        ? `<br><span class="badge danger">Soma física ${escapeHtml(formatNumber(d.unidades_fisicas))} ≠ saldo global</span>` : "";
    return `${escapeHtml(formatNumber(d.lotes))} lote(s) · ${escapeHtml(formatNumber(d.posicoes))} posição(ões) · ` +
        `${escapeHtml(formatNumber(d.corredores))} corredor(es)<br><small>Disponível ${escapeHtml(formatNumber(d.unidades_disponiveis))} · ` +
        `Reservado ${escapeHtml(formatNumber(d.unidades_reservadas))} · Bloqueado ${escapeHtml(formatNumber(d.unidades_bloqueadas))}</small>` +
        `${mismatch}<br>${locationLink(item.id_produto)}`;
}

const situationLabel = item => REPOSICAO_META[item.situacao_reposicao]?.label || item.situacao_reposicao;
const yesNo = flag => (flag ? "sim" : "não");

const REPORTS = {
    posicao: {
        endpoint: "posicao-estoque",
        fetch: params => apiRelatorio.posicao(params),
        params: () => ({ params: compact({ id_produto: value("posicaoProduto"), categoria: value("posicaoCategoria"),
            estoque_baixo: repEl("posicaoBaixo").checked ? "sim" : "" }) }),
        columns: 8,
        empty: "Nenhum produto encontrado com estes filtros.",
        row: item => `<tr><td>${productCell(item.nome, item.codigo)}</td><td>${escapeHtml(item.categoria)}</td>
            <td>${escapeHtml(formatNumber(item.estoque))}</td><td>${escapeHtml(formatNumber(item.quantidade_min))}</td>
            <td>${reposicaoBadges(item)}</td><td>${distribution(item)}</td><td>${escapeHtml(formatDateOnly(item.validade))}</td>
            <td class="legacy-location">${escapeHtml(item.localizacao_legada)}</td></tr>`,
        summary: data => [["Produtos no filtro", formatNumber(data.total)]],
        csv: {
            header: ["ID", "Produto", "SKU", "Categoria", "Saldo global", "Estoque mínimo", "Situação", "Sem saldo",
                "Sugestão (un.)", "Lotes", "Posições", "Corredores", "Unidades físicas", "Disponível", "Reservado",
                "Bloqueado", "Soma física confere", "Validade", "Localização (legada)"],
            row: i => [i.id_produto, i.nome, i.codigo, i.categoria, i.estoque, i.quantidade_min, situationLabel(i),
                yesNo(i.sem_saldo), i.quantidade_sugerida, i.distribuicao?.lotes, i.distribuicao?.posicoes,
                i.distribuicao?.corredores, i.distribuicao?.unidades_fisicas, i.distribuicao?.unidades_disponiveis,
                i.distribuicao?.unidades_reservadas, i.distribuicao?.unidades_bloqueadas,
                yesNo(i.distribuicao?.confere_com_saldo), i.validade || "", i.localizacao_legada]
        }
    },
    reposicao: {
        endpoint: "estoque-baixo",
        fetch: params => apiRelatorio.reposicao(params),
        params: () => ({ params: compact({ id_produto: value("reposicaoProduto"), categoria: value("reposicaoCategoria") }) }),
        columns: 7,
        empty: "Nenhum produto no limite ou abaixo do estoque mínimo.",
        row: item => `<tr><td>${productCell(item.nome, item.codigo)}</td><td>${escapeHtml(item.categoria)}</td>
            <td>${escapeHtml(formatNumber(item.estoque))}</td><td>${escapeHtml(formatNumber(item.quantidade_min))}</td>
            <td>${reposicaoBadges(item)}</td><td>${escapeHtml(sugestaoTexto(item))}</td><td>${locationLink(item.id_produto)}</td></tr>`,
        summary: data => {
            const r = data.resumo || {};
            return [["No limite ou abaixo", formatNumber(r.estoque_baixo)], ["Abaixo do mínimo", formatNumber(r.abaixo_do_minimo)],
                ["No limite mínimo", formatNumber(r.no_limite)], ["Sem saldo", formatNumber(r.sem_saldo)],
                ["Soma das sugestões (un.)", formatNumber(r.reposicao_sugerida_total)],
                ["Produtos considerados", formatNumber(r.total_produtos)]];
        },
        csv: {
            header: ["ID", "Produto", "SKU", "Categoria", "Saldo global", "Estoque mínimo", "Situação", "Sem saldo",
                "Sugestão (un.)"],
            row: i => [i.id_produto, i.nome, i.codigo, i.categoria, i.estoque, i.quantidade_min, situationLabel(i),
                yesNo(i.sem_saldo), i.quantidade_sugerida]
        }
    },
    movimentacoes: {
        endpoint: "movimentacoes",
        fetch: params => apiRelatorio.movimentacoes(params),
        params: () => withPeriod("movimentacoes", true, { tipo: value("movimentacoesTipo"),
            id_produto: value("movimentacoesProduto"), id_corredor: value("movimentacoesCorredor"),
            id_posicao: value("movimentacoesPosicao"), lote: value("movimentacoesLote") }),
        columns: 9,
        empty: "Nenhuma movimentação no período e filtros informados.",
        row: m => `<tr><td>${escapeHtml(formatInstantLocal(m.data_hora))}</td><td>${productCell(m.produto_nome, m.produto_codigo)}</td>
            <td>${escapeHtml(TIPO_LABEL[m.tipo] || m.tipo)}</td><td>${escapeHtml(movimentoQuantidade(m))}</td>
            <td>${escapeHtml(formatNumber(m.estoque_anterior))} → ${escapeHtml(formatNumber(m.estoque_posterior))}</td>
            <td>${escapeHtml(movimentoVariacao(m))}</td><td>${movimentoLocal(m)}</td><td>${escapeHtml(m.usuario_nome)}</td>
            <td>${escapeHtml(m.motivo || "—")}</td></tr>`,
        summary: data => [["Eventos no recorte", formatNumber(data.total)], ...(data.resumo || []).map(t => [
            TIPO_LABEL[t.tipo] || t.tipo, t.unidades === null
                ? `${formatNumber(t.eventos)} evento(s) · variação ${formatSigned(t.variacao_liquida)}`
                : `${formatNumber(t.eventos)} evento(s) · ${formatNumber(t.unidades)} un.`])],
        csv: {
            header: ["Data/hora (ISO)", "ID", "Produto", "SKU", "Tipo", "Quantidade registrada", "Saldo anterior",
                "Saldo posterior", "Variação", "Corredor", "Posição", "Lote", "Qtd. item anterior", "Qtd. item posterior",
                "Responsável", "Motivo"],
            row: m => [m.data_hora, m.id_movimentacao, m.produto_nome, m.produto_codigo, m.tipo, m.quantidade,
                m.estoque_anterior, m.estoque_posterior, m.estoque_posterior - m.estoque_anterior, m.corredor || "",
                m.codigo_posicao || "", m.lote || "", m.quantidade_item_anterior ?? "", m.quantidade_item_posterior ?? "",
                m.usuario_nome, m.motivo || ""]
        }
    },
    divergencias: {
        endpoint: "divergencias",
        fetch: params => apiRelatorio.divergencias(params),
        params: () => withPeriod("divergencias", false, { situacao: value("divergenciasSituacao"),
            sinal: value("divergenciasSinal"), id_produto: value("divergenciasProduto"),
            id_corredor: value("divergenciasCorredor"), id_posicao: value("divergenciasPosicao"),
            lote: value("divergenciasLote") }),
        columns: 10,
        empty: "Nenhuma divergência com estes filtros.",
        row: c => {
            const meta = SITUACAO_CONTAGEM[c.situacao] || { label: c.situacao, badge: "" };
            const applied = c.situacao === "APLICADA" ? `<br><small>Ajuste #${escapeHtml(c.id_movimentacao_ajuste)} por ` +
                `${escapeHtml(c.aplicada_por || "—")} em ${escapeHtml(formatInstantLocal(c.aplicada_em))}</small>` : "";
            return `<tr><td>${escapeHtml(formatInstantLocal(c.data_hora))}</td><td>${productCell(c.produto_nome, c.produto_codigo)}</td>
                <td>${escapeHtml(c.corredor)} / ${escapeHtml(c.codigo_posicao)}<br><small>Lote ${escapeHtml(c.lote)}</small></td>
                <td>${escapeHtml(c.status_item)}</td><td>${escapeHtml(formatNumber(c.quantidade_sistema))}</td>
                <td>${escapeHtml(formatNumber(c.quantidade_fisica))}</td>
                <td class="${c.divergencia > 0 ? "divergence-positive" : "divergence-negative"}">${escapeHtml(formatSigned(c.divergencia))}</td>
                <td><span class="badge ${meta.badge}">${escapeHtml(meta.label)}</span>${applied}</td>
                <td>${escapeHtml(c.usuario_nome)}</td><td>${escapeHtml(c.observacao || "—")}</td></tr>`;
        },
        summary: data => {
            const r = data.resumo || {};
            return [["Com divergência", formatNumber(r.com_divergencia)], ["Pendentes", formatNumber(r.pendentes)],
                ["Aplicadas", formatNumber(r.aplicadas)], ["Positivas (sobra)", formatNumber(r.positivas)],
                ["Negativas (falta)", formatNumber(r.negativas)], ["Soma positiva (un.)", formatSigned(r.soma_positiva)],
                ["Soma negativa (un.)", formatSigned(r.soma_negativa)],
                ["Contagens sem divergência no recorte", formatNumber(r.sem_divergencia)]];
        },
        csv: {
            header: ["Data/hora (ISO)", "ID contagem", "Produto", "SKU", "Corredor", "Posição", "Lote", "Status do item",
                "Sistêmico", "Físico", "Divergência", "Sinal", "Situação", "Ajuste", "Aplicada em", "Aplicada por",
                "Responsável", "Observação"],
            row: c => [c.data_hora, c.id_contagem, c.produto_nome, c.produto_codigo, c.corredor, c.codigo_posicao, c.lote,
                c.status_item, c.quantidade_sistema, c.quantidade_fisica, c.divergencia, c.sinal, c.situacao,
                c.id_movimentacao_ajuste ?? "", c.aplicada_em || "", c.aplicada_por || "", c.usuario_nome, c.observacao || ""]
        }
    },
    saidas: {
        endpoint: "saidas-periodo",
        fetch: params => apiRelatorio.saidas(params),
        params: () => withPeriod("saidas", true, { id_produto: value("saidasProduto"), categoria: value("saidasCategoria") }),
        columns: 4,
        empty: "Nenhuma saída registrada no período e filtros informados.",
        row: s => `<tr><td>${productCell(s.nome, s.codigo)}</td><td>${escapeHtml(s.categoria)}</td>
            <td>${escapeHtml(formatNumber(s.eventos))}</td><td>${escapeHtml(formatNumber(s.unidades))}</td></tr>`,
        summary: data => {
            const t = data.totais || {};
            return [["Produtos com saída", formatNumber(t.produtos)], ["Eventos de saída", formatNumber(t.eventos)],
                ["Unidades saídas", formatNumber(t.unidades)]];
        },
        csv: {
            header: ["ID", "Produto", "SKU", "Categoria", "Eventos de saída", "Unidades saídas"],
            row: s => [s.id_produto, s.nome, s.codigo, s.categoria, s.eventos, s.unidades]
        }
    }
};

const validPage = data => Array.isArray(data?.itens) && isCount(data.total) && isCount(data.page) && isCount(data.paginas);
// Contrato da exportação: itens, total e truncamento da MESMA resposta (mesma transação no servidor).
const validExport = data => Array.isArray(data?.itens) && isCount(data.total) && isCount(data.quantidade)
    && data.quantidade === data.itens.length && data.quantidade <= CSV_LIMIT && data.total >= data.quantidade
    && data.truncado === (data.total > data.quantidade);
const exportButton = key => document.querySelector(`[data-export="${key}"]`);

function setFeedback(key, message, error) {
    const element = repEl(`${key}Feedback`);
    element.textContent = message;
    element.dataset.error = String(Boolean(error));
}

function renderPagination(key, data) {
    const pager = repEl(`${key}Pagination`);
    if (data.paginas <= 1) {
        pager.replaceChildren();
        return;
    }
    pager.innerHTML = `<button type="button" data-goto="${data.page - 1}" ${data.page <= 1 ? "disabled" : ""}>Anterior</button>
        <span>Página ${escapeHtml(formatNumber(data.page))} de ${escapeHtml(formatNumber(data.paginas))}</span>
        <button type="button" data-goto="${data.page + 1}" ${data.page >= data.paginas ? "disabled" : ""}>Próxima</button>`;
}

function renderReport(key, data) {
    const config = REPORTS[key];
    repEl(`${key}Body`).innerHTML = data.itens.length ? data.itens.map(config.row).join("")
        : emptyRow(config.columns, data.total ? "Página sem itens. Volte para a primeira página." : config.empty);
    repEl(`${key}Summary`).innerHTML = config.summary(data)
        .map(([label, text]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(text)}</dd></div>`).join("");
    repEl(`${key}Meta`).textContent = `Dados do servidor em ${formatInstantLocal(data.gerado_em)}. ` +
        `${formatNumber(data.total)} registro(s) no total.${periodText(data)}`;
    renderPagination(key, data);
    exportButton(key).disabled = data.total === 0;
}

function renderFailure(key, message) {
    const state = reportState[key];
    state.data = null;
    state.params = null;
    repEl(`${key}Body`).innerHTML = emptyRow(REPORTS[key].columns, "Relatório indisponível.");
    repEl(`${key}Summary`).replaceChildren();
    repEl(`${key}Meta`).textContent = "";
    repEl(`${key}Pagination`).replaceChildren();
    exportButton(key).disabled = true;
    setFeedback(key, message, true);
}

async function loadReport(key, page = 1) {
    const config = REPORTS[key];
    const state = reportState[key] ??= { seq: 0 };
    const built = config.params();
    if (built.error) {
        setFeedback(key, `${built.error} Os filtros não foram aplicados.`, true);
        return;
    }
    const params = { ...built.params, page_size: repEl(`${key}PageSize`).value };
    const seq = ++state.seq;
    state.loaded = true;
    repEl(`${key}Table`).setAttribute("aria-busy", "true");
    setFeedback(key, "Carregando...", false);
    const result = await config.fetch({ ...params, page });
    if (seq !== state.seq) return;  // resposta antiga de filtros já substituídos
    repEl(`${key}Table`).setAttribute("aria-busy", "false");
    if (!result.ok || !validPage(result.data)) {
        renderFailure(key, result.ok ? "Resposta do servidor fora do formato esperado." : readFailureMessage(result));
        return;
    }
    state.data = result.data;
    state.params = params;
    renderReport(key, result.data);
    setFeedback(key, "", false);
}

async function exportReport(key) {
    const config = REPORTS[key];
    const state = reportState[key];
    if (!state?.params || state.exporting) return;  // bloqueio de duplo envio
    state.exporting = true;
    const button = exportButton(key);
    button.disabled = true;
    setFeedback(key, "Gerando CSV...", false);
    try {
        // Mesmos filtros da tela; a exportação não pagina (page/page_size são recusados pelo servidor).
        const { page_size: _pageSize, ...filters } = state.params;
        const result = await apiRelatorio.exportar(config.endpoint, filters);
        if (!result.ok || !validExport(result.data)) {
            setFeedback(key, `Exportação cancelada: ${result.ok ? "resposta fora do formato esperado." :
                readFailureMessage(result)} Nenhum arquivo foi gerado.`, true);
            return;
        }
        const { itens: exported, total, truncado } = result.data;
        const csv = [config.csv.header, ...exported.map(config.csv.row)]
            .map(row => row.map(csvEscape).join(";")).join("\r\n");
        const url = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = `relatorio-${key}-${localDay(new Date())}.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        setFeedback(key, truncado
            ? `CSV gerado com as primeiras ${formatNumber(exported.length)} de ${formatNumber(total)} linhas. Refine os filtros para exportar o restante.`
            : `CSV gerado com ${formatNumber(exported.length)} linha(s).`, false);
    } finally {
        state.exporting = false;
        button.disabled = !(reportState[key]?.data?.total > 0);
    }
}

function fillSelect(select, options) {
    const first = select.options[0]?.outerHTML || '<option value="">Todos</option>';
    const selected = select.value;
    select.innerHTML = first + options.map(([id, label]) => `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`).join("");
    select.value = options.some(([id]) => String(id) === selected) ? selected : "";
}

function renderPositionOptions(select) {
    const corridor = Number(repEl(select.dataset.positionsOf)?.value || 0);
    fillSelect(select, reportOptions.posicoes.filter(p => !corridor || p.id_corredor === corridor)
        .map(p => [p.id_posicao, p.codigo_posicao]));
}

async function loadOptions() {
    const [produtos, corredores, posicoes] = await Promise.all([apiProduto.listar(), apiLocalizacao.corredores(),
        apiLocalizacao.posicoes()]);
    const unavailable = [];
    if (produtos.ok && Array.isArray(produtos.data)) {
        reportOptions.produtos = [...produtos.data].sort((a, b) => a.nome.localeCompare(b.nome, "pt-BR") || a.id_produto - b.id_produto);
    } else unavailable.push("produtos");
    if (corredores.ok && Array.isArray(corredores.data)) reportOptions.corredores = corredores.data;
    else unavailable.push("corredores");
    if (posicoes.ok && Array.isArray(posicoes.data)) reportOptions.posicoes = posicoes.data;
    else unavailable.push("posições");
    const categorias = [...new Set(reportOptions.produtos.map(p => p.categoria))].sort((a, b) => a.localeCompare(b, "pt-BR"));
    document.querySelectorAll('select[data-options="produtos"]').forEach(select => fillSelect(select,
        reportOptions.produtos.map(p => [p.id_produto, `${p.nome} (${p.codigo})`])));
    document.querySelectorAll('select[data-options="categorias"]').forEach(select => fillSelect(select,
        categorias.map(c => [c, c])));
    document.querySelectorAll('select[data-options="corredores"]').forEach(select => fillSelect(select,
        reportOptions.corredores.map(c => [c.id_corredor, c.identificacao])));
    document.querySelectorAll('select[data-options="posicoes"]').forEach(renderPositionOptions);
    if (unavailable.length) showToast(`Listas de filtro indisponíveis: ${unavailable.join(", ")}. Os relatórios continuam disponíveis.`, "warning");
}

function activate(key, focus = false) {
    document.querySelectorAll('.report-tabs [role="tab"]').forEach(tab => {
        const active = tab.dataset.report === key;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        repEl(`panel-${tab.dataset.report}`).hidden = !active;
        if (active && focus) tab.focus();
    });
    if (location.hash !== `#${key}`) history.replaceState(null, "", `${location.pathname}${location.search}#${key}`);
    if (!reportState[key]?.loaded) loadReport(key);
}

document.addEventListener("DOMContentLoaded", async () => {
    if (!await window.sessionReady) return;
    const today = new Date();
    const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 29);
    for (const key of ["movimentacoes", "saidas"]) {
        repEl(`${key}Inicio`).value = localDay(start);
        repEl(`${key}Fim`).value = localDay(today);
    }
    const situacao = new URLSearchParams(location.search).get("situacao");
    if (situacao === "PENDENTE" || situacao === "APLICADA") repEl("divergenciasSituacao").value = situacao;

    document.querySelectorAll("[data-positions]").forEach(select => {
        repEl(select.dataset.positions).dataset.positionsOf = select.id;
        select.addEventListener("change", () => renderPositionOptions(repEl(select.dataset.positions)));
    });
    document.querySelectorAll("[data-apply]").forEach(button =>
        button.addEventListener("click", () => loadReport(button.dataset.apply, 1)));
    document.querySelectorAll("[data-export]").forEach(button =>
        button.addEventListener("click", () => exportReport(button.dataset.export)));
    document.querySelectorAll("[data-report-form]").forEach(form => form.addEventListener("submit", event => {
        event.preventDefault();
        loadReport(form.dataset.reportForm, 1);
    }));
    Object.keys(REPORTS).forEach(key => repEl(`${key}Pagination`).addEventListener("click", event => {
        const button = event.target.closest("button[data-goto]");
        if (button && !button.disabled) loadReport(key, Number(button.dataset.goto));
    }));
    const tabs = [...document.querySelectorAll('.report-tabs [role="tab"]')];
    tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => activate(tab.dataset.report));
        tab.addEventListener("keydown", event => {
            const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
            if (step) activate(tabs[(index + step + tabs.length) % tabs.length].dataset.report, true);
        });
    });
    window.addEventListener("hashchange", () => {
        const key = location.hash.slice(1);
        if (key in REPORTS) activate(key);
    });

    loadOptions();
    const initial = location.hash.slice(1);
    activate(initial in REPORTS ? initial : "posicao");
});
