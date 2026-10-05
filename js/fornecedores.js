"use strict";
// Etapa 2G (RF12). Permissões vêm de /session (common.js); o backend revalida tudo.
const sup = id => document.getElementById(id);
const SUPPLIER_PAGE_SIZE = 20, RECEIPT_PAGE_SIZE = 20;
let supplierRows = [], supplierPage = 1, supplierReady = false, supplierSaving = false;
let supplierVersion = 0, receiptSupplier = null, receiptVersion = 0;

function supplierFeedback(message, error = false) {
    sup('supplierFeedback').textContent = message;
    sup('supplierFeedback').dataset.error = String(error);
}

function supplierUnavailable(result) {
    if (result.status === 503) return result.data?.message || 'Fornecedores indisponíveis: o SQL da Etapa 2G ainda não foi aplicado.';
    if (result.status === 403) return 'Seu perfil não possui permissão para consultar fornecedores.';
    if (result.status === 400) return result.data?.message || 'Filtro inválido.';
    if (result.status <= 0) return 'Não foi possível conectar ao servidor. Tente novamente.';
    return 'Não foi possível consultar os fornecedores. Atualize para tentar novamente.';
}

function renderPager(element, page, pages, onPage) {
    element.replaceChildren();
    if (pages <= 1) return;
    for (const [label, next] of [['Anterior', page - 1], ['Próxima', page + 1]]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.disabled = next < 1 || next > pages;
        button.onclick = () => onPage(next);
        element.append(button);
    }
    const info = document.createElement('span');
    info.textContent = `Página ${page} de ${pages}`;
    element.append(info);
}

function validSupplierPage(data) {
    return data && Array.isArray(data.itens) && Number.isSafeInteger(data.total) && Number.isSafeInteger(data.paginas);
}

async function loadSuppliers(page = supplierPage) {
    const version = ++supplierVersion;
    sup('refreshSuppliers').disabled = true;
    sup('suppliersBody').setAttribute('aria-busy', 'true');
    sup('suppliersBody').innerHTML = '<tr><td colspan="5">Carregando...</td></tr>';
    const params = { page, page_size: SUPPLIER_PAGE_SIZE };
    const q = sup('supplierSearch').value.trim();
    if (q) params.q = q;
    if (sup('supplierStatus').value) params.ativo = sup('supplierStatus').value;
    try {
        const result = await apiFornecedor.listar(params);
        if (version !== supplierVersion) return;
        supplierReady = result.ok && validSupplierPage(result.data);
        sup('supplierFields').disabled = !supplierReady || supplierSaving;
        if (!supplierReady) {
            supplierRows = [];
            const message = result.ok ? 'Resposta inesperada do servidor.' : supplierUnavailable(result);
            sup('suppliersBody').innerHTML = `<tr><td colspan="5" class="empty">${escapeHtml(message)}</td></tr>`;
            sup('supplierPagination').replaceChildren();
            supplierFeedback(message, true);
            return;
        }
        supplierRows = result.data.itens;
        supplierPage = result.data.page;
        renderSuppliers(result.data);
        supplierFeedback(`${result.data.total} fornecedor(es) encontrado(s).`);
    } finally {
        if (version === supplierVersion) {
            sup('refreshSuppliers').disabled = false;
            sup('suppliersBody').setAttribute('aria-busy', 'false');
        }
    }
}

function renderSuppliers(data) {
    const manage = canPerform('suppliers:manage');
    const receipts = canPerform('movimentacoes:consultar');
    sup('suppliersBody').innerHTML = supplierRows.map(f => `<tr>
        <td>${escapeHtml(f.razao_social)}</td><td>${escapeHtml(f.cnpj_formatado)}</td><td>${escapeHtml(f.contato || '—')}</td>
        <td><span class="badge ${f.ativo ? 'success' : 'warning'}">${f.ativo ? 'Ativo' : 'Inativo'}</span></td>
        <td class="supplier-actions">${receipts ? `<button class="btn secondary" type="button" data-receipts="${f.id_fornecedor}">Recebimentos</button>` : ''}
        ${manage ? `<button class="btn secondary" type="button" data-edit="${f.id_fornecedor}">Editar</button>
        <button class="btn secondary" type="button" data-toggle="${f.id_fornecedor}">${f.ativo ? 'Inativar' : 'Reativar'}</button>
        <button class="btn secondary" type="button" data-delete="${f.id_fornecedor}">Excluir</button>` : ''}${!manage && !receipts ? '—' : ''}</td></tr>`).join('')
        || '<tr><td colspan="5" class="empty">Nenhum fornecedor encontrado.</td></tr>';
    renderPager(sup('supplierPagination'), data.page, data.paginas, next => loadSuppliers(next));
}

function supplierPayload(base) {
    return { razao_social: base.razao_social, cnpj: base.cnpj, contato: base.contato || null, ativo: base.ativo };
}

async function writeSupplier(action, success) {
    if (supplierSaving || !supplierReady || !canPerform('suppliers:manage')) return false;
    supplierSaving = true;
    sup('supplierFields').disabled = true;
    try {
        const result = await action();
        if (!result.ok) {
            const message = result.status === 503 || result.status === 400 ? (result.data?.message || 'Dados inválidos.')
                : mutationError(result, 'Não foi possível salvar o fornecedor.');
            supplierFeedback(message, true);
            // Resultado incerto (rede/5xx exceto 503): bloqueia novas escritas até atualizar.
            if (result.status <= 0 || (result.status >= 500 && result.status !== 503)) supplierReady = false;
            return false;
        }
        await loadSuppliers();
        if (supplierReady) { supplierFeedback(success); showToast(success); }
        return true;
    } finally {
        supplierSaving = false;
        sup('supplierFields').disabled = !supplierReady;
    }
}

// O campo oculto não volta ao vazio com reset(): limpeza explícita no evento reset.
function clearSupplierEdit() {
    sup('supplierId').value = '';
    sup('supplierFormTitle').textContent = 'Cadastrar fornecedor';
}

function resetSupplierForm() {
    sup('supplierForm').reset();
}

async function submitSupplier(event) {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const id = Number(sup('supplierId').value);
    const data = supplierPayload({ razao_social: sup('supplierName').value.trim(), cnpj: sup('supplierCnpj').value.trim(),
        contato: sup('supplierContact').value.trim(), ativo: sup('supplierActive').checked });
    const ok = await writeSupplier(() => apiFornecedor.salvar(id || null, data), id ? 'Fornecedor atualizado.' : 'Fornecedor cadastrado.');
    if (ok) resetSupplierForm();
}

function editSupplier(id) {
    const f = supplierRows.find(row => row.id_fornecedor === id);
    if (!f) return;
    sup('supplierId').value = id;
    sup('supplierName').value = f.razao_social;
    sup('supplierCnpj').value = f.cnpj_formatado;
    sup('supplierContact').value = f.contato || '';
    sup('supplierActive').checked = f.ativo;
    sup('supplierFormTitle').textContent = 'Editar fornecedor';
    sup('supplierName').focus();
}

async function toggleSupplier(id) {
    const f = supplierRows.find(row => row.id_fornecedor === id);
    if (!f) return;
    const message = f.ativo ? `Inativar ${f.razao_social}? Ele não poderá ser escolhido em novos recebimentos; o histórico é preservado.`
        : `Reativar ${f.razao_social}?`;
    if (!window.confirm(message)) return;
    await writeSupplier(() => apiFornecedor.salvar(id, supplierPayload({ ...f, ativo: !f.ativo })),
        f.ativo ? 'Fornecedor inativado.' : 'Fornecedor reativado.');
}

async function deleteSupplier(id) {
    const f = supplierRows.find(row => row.id_fornecedor === id);
    if (!f || !window.confirm(`Excluir ${f.razao_social}? Só é possível sem recebimentos vinculados; caso contrário, inative-o.`)) return;
    await writeSupplier(() => apiFornecedor.excluir(id), 'Fornecedor excluído.');
}

async function loadReceipts(id, page = 1) {
    const f = supplierRows.find(row => row.id_fornecedor === id) || receiptSupplier;
    if (!f) return;
    receiptSupplier = f;
    const version = ++receiptVersion;
    sup('receiptsCard').hidden = false;
    sup('receiptsTitle').textContent = `Recebimentos — ${f.razao_social}`;
    sup('receiptsBody').setAttribute('aria-busy', 'true');
    sup('receiptsBody').innerHTML = '<tr><td colspan="7">Carregando...</td></tr>';
    const result = await apiFornecedor.recebimentos(f.id_fornecedor, { page, page_size: RECEIPT_PAGE_SIZE });
    if (version !== receiptVersion) return;
    sup('receiptsBody').setAttribute('aria-busy', 'false');
    if (!result.ok || !validSupplierPage(result.data)) {
        const message = result.ok ? 'Resposta inesperada do servidor.' : supplierUnavailable(result);
        sup('receiptsBody').innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(message)}</td></tr>`;
        sup('receiptsPagination').replaceChildren();
        return;
    }
    sup('receiptsBody').innerHTML = result.data.itens.map(m => {
        const time = new Date(m.data_hora);
        return `<tr><td>${escapeHtml(Number.isNaN(time.getTime()) ? '—' : time.toLocaleString('pt-BR'))}</td>
        <td>${escapeHtml(m.produto_nome)}</td><td>${escapeHtml(m.produto_codigo)}</td><td>${escapeHtml(m.quantidade)}</td>
        <td>${m.id_item_estoque ? `${escapeHtml(m.corredor)} / ${escapeHtml(m.codigo_posicao)} · ${escapeHtml(m.lote)}` : '—'}</td>
        <td>${escapeHtml(m.usuario_nome)}</td><td>${escapeHtml(m.fornecedor_razao_social)}<br><small>${escapeHtml(formatCnpj(m.fornecedor_cnpj))}</small></td></tr>`;
    }).join('') || '<tr><td colspan="7" class="empty">Nenhum recebimento vinculado a este fornecedor.</td></tr>';
    renderPager(sup('receiptsPagination'), result.data.page, result.data.paginas, next => loadReceipts(f.id_fornecedor, next));
}

document.addEventListener('DOMContentLoaded', async () => {
    if (!await window.sessionReady) return;
    document.addEventListener('inventaire:permissions', () => loadSuppliers());
    sup('supplierForm').onsubmit = submitSupplier;
    sup('supplierForm').onreset = clearSupplierEdit;
    sup('refreshSuppliers').onclick = () => loadSuppliers();
    sup('supplierSearchForm').onsubmit = e => { e.preventDefault(); loadSuppliers(1); };
    sup('supplierStatus').onchange = () => loadSuppliers(1);
    sup('closeReceipts').onclick = () => { sup('receiptsCard').hidden = true; receiptSupplier = null; };
    sup('suppliersBody').onclick = e => {
        const button = e.target.closest('button');
        if (!button) return;
        if (button.dataset.edit) editSupplier(Number(button.dataset.edit));
        if (button.dataset.toggle) toggleSupplier(Number(button.dataset.toggle));
        if (button.dataset.delete) deleteSupplier(Number(button.dataset.delete));
        if (button.dataset.receipts) loadReceipts(Number(button.dataset.receipts));
    };
    await loadSuppliers(1);
});
