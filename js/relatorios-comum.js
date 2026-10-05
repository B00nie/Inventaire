"use strict";

/**
 * relatorios-comum.js — Etapa 2F. Formatação compartilhada por Dashboard e Relatórios.
 * Não calcula indicadores: todos os números vêm do servidor (consultas agregadas no banco).
 * Aqui há apenas exibição, rótulos e escape. Falha nunca é exibida como zero.
 */

const REPOSICAO_META = Object.freeze({
    ABAIXO_DO_MINIMO: { label: "Abaixo do mínimo", badge: "danger" },
    NO_LIMITE: { label: "No limite mínimo", badge: "warning" },
    ACIMA_DO_MINIMO: { label: "Acima do mínimo", badge: "success" }
});
const TIPO_LABEL = Object.freeze({ ENTRADA: "Entrada", SAIDA: "Saída", AJUSTE: "Ajuste" });
const SITUACAO_CONTAGEM = Object.freeze({
    PENDENTE: { label: "Pendente", badge: "warning" },
    APLICADA: { label: "Aplicada", badge: "success" }
});

const isCount = value => Number.isSafeInteger(value) && value >= 0;

function formatNumber(value) {
    return Number.isFinite(value) ? value.toLocaleString("pt-BR") : "—";
}

function formatSigned(value) {
    return Number.isFinite(value) && value > 0 ? `+${formatNumber(value)}` : formatNumber(value);
}

// Instantes ISO com fuso: exibidos no fuso local do navegador.
function formatInstantLocal(value) {
    if (!value) return "—";
    const time = new Date(value);
    return Number.isNaN(time.getTime()) ? "—" : time.toLocaleString("pt-BR");
}

// Datas sem hora (validade): sem conversão de fuso, sem regra de vencimento.
function formatDateOnly(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    return match ? `${match[3]}/${match[2]}/${match[1]}` : "—";
}

function reposicaoBadges(item) {
    const meta = REPOSICAO_META[item.situacao_reposicao] || { label: "—", badge: "" };
    const semSaldo = item.sem_saldo ? ' <span class="badge danger">Sem saldo</span>' : "";
    return `<span class="badge ${meta.badge}">${escapeHtml(meta.label)}</span>${semSaldo}`;
}

// Sugestão objetiva = max(0, mínimo − saldo), calculada pelo servidor. Zero não vira "repor 0".
function sugestaoTexto(item) {
    if (item.quantidade_sugerida > 0) return `Repor ${formatNumber(item.quantidade_sugerida)} un. para atingir o mínimo`;
    return item.estoque_baixo ? "No limite mínimo: nada a repor para atingir o mínimo" : "—";
}

function readFailureMessage(result) {
    if (result.status === 403) return "Seu perfil não possui permissão para esta consulta.";
    if (result.status === 401) return "Sessão expirada. Entre novamente.";
    if (result.status === 0 || result.status === -1) return "Não foi possível conectar ao servidor.";
    if (result.status >= 500 && result.status !== 503) return "O servidor não respondeu a esta consulta.";
    return result.data?.message || "Dados indisponíveis.";
}

function movimentoVariacao(m) {
    return formatSigned(m.estoque_posterior - m.estoque_anterior);
}

// AJUSTE grava em "quantidade" o saldo alvo global (2B); não é quantidade movimentada.
function movimentoQuantidade(m) {
    return m.tipo === "AJUSTE" ? `alvo global ${formatNumber(m.quantidade)}` : formatNumber(m.quantidade);
}

function movimentoLocal(m) {
    if (!m.codigo_posicao) return "—";
    return `${escapeHtml(m.corredor)} / ${escapeHtml(m.codigo_posicao)}<br><small>Lote ${escapeHtml(m.lote)}</small>`;
}

window.REPOSICAO_META = REPOSICAO_META;
window.TIPO_LABEL = TIPO_LABEL;
window.SITUACAO_CONTAGEM = SITUACAO_CONTAGEM;
