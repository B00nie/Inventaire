
"use strict";

/**
 * administracao.js — Listagem, cadastro e controle de acesso (perfil/atividade/unidade) de usuários.
 *
 * GET /users (permissão usuarios:gerenciar, revalidada no servidor) devolve id, nome,
 * sobrenome, email, perfil canônico, unidade, telefone, admin, ativo e acesso.
 * Etapa 2E: perfil e atividade de terceiros via PATCH /users/{id}/acesso. Autoalteração
 * e remoção do último ADMINISTRADOR ativo são recusadas pelo backend (409). Edição
 * cadastral completa e exclusão continuam fora desta tela (escopo mínimo da matriz).
 */
const $ = id => document.getElementById(id);
let usersRequestId = 0;
let signupPending = false;
// true depois que uma leitura real já pintou a tabela: uma falha de atualização
// posterior preserva essa lista em vez de esvaziar a tela.
let usersRendered = false;

function resetUserMetrics() {
    ["adminUsersCount", "adminAdminsCount", "adminProfilesCount", "adminLogsCount"].forEach(id => $(id).textContent = "—");
}

function showUsersState(message) {
    $("usersTable").innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(message)}</td></tr>`;
}

function setUsersFeedback(message) {
    const feedback = $("usersFeedback");
    if (!feedback) return;
    feedback.textContent = message;
    feedback.hidden = !message;
}

function renderUsers(users) {
    $("adminUsersCount").textContent = users.length;
    // `admin` é calculado pelo servidor a partir do perfil persistido.
    $("adminAdminsCount").textContent = users.filter(user => user.admin === true && user.ativo === true).length;
    $("adminProfilesCount").textContent = new Set(users.map(user => String(user.perfil || "")).filter(Boolean)).size;
    // `acesso` é o último login gravado; só contamos quem realmente tem um.
    $("adminLogsCount").textContent = users.filter(user => Boolean(user.acesso)).length;
    if (!users.length) {
        showUsersState("Nenhum usuário cadastrado.");
        return;
    }
    // Opções de perfil = as do formulário de cadastro (fonte única na página).
    const roleOptions = [...$("signupRole").options].map(option => [option.value, option.textContent]);
    const ownId = getSession()?.userId;
    $("usersTable").innerHTML = users.map(user => {
        const name = [user.nome, user.sobrenome].filter(Boolean).join(" ") || "—";
        const known = roleOptions.some(([value]) => value === user.perfil);
        const role = roleLabel(user.perfil, known);
        const status = user.ativo === true ? "Ativo" : user.ativo === false ? "Inativo" : "—";
        const id = Number(user.id);
        const own = id === ownId;
        const locked = own || !canPerform("users:manage") || !Number.isSafeInteger(id);
        const title = own ? ' title="Você não pode alterar o próprio perfil ou a própria atividade."' : "";
        const options = (known ? "" : '<option value="" selected disabled>Perfil não reconhecido</option>') +
            roleOptions.map(([value, label]) => `<option value="${escapeHtml(value)}"${value === user.perfil ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
        const access = `<div class="user-access">
            <label class="sr-only" for="userRole${id}">Perfil de ${escapeHtml(name)}</label>
            <select id="userRole${id}" data-user-role="${id}"${locked ? " disabled" : ""}${title}>${options}</select>
            <button class="btn secondary" type="button" data-user-active="${id}" data-next="${user.ativo === true ? "false" : "true"}"${locked ? " disabled" : ""}${title}>
                ${user.ativo === true ? "Inativar" : "Reativar"}</button></div>`;
        // Unidade: campo administrativo (PATCH /users/{id}/acesso). Salva ao confirmar (Enter ou sair do campo).
        const unitLocked = !canPerform("users:manage") || !Number.isSafeInteger(id);
        const unit = `<label class="sr-only" for="userUnit${id}">Unidade de ${escapeHtml(name)}</label>
            <input class="user-unit" id="userUnit${id}" data-user-unit="${id}" maxlength="45" placeholder="—"
                value="${escapeHtml(user.unidade || "")}" data-current="${escapeHtml(user.unidade || "")}"${unitLocked ? " disabled" : ""}>`;
        return `<tr><td>${escapeHtml(name)}</td><td>${escapeHtml(user.email || "—")}</td><td>${escapeHtml(role)}</td><td>${status}</td><td>${unit}</td><td>${access}</td></tr>`;
    }).join("");
}

let lastUsers = [];
let accessPending = false;
async function changeUserAccess(id, change, description) {
    if (accessPending || !canPerform("users:manage")) return;
    if (!window.confirm(`Confirmar: ${description}? A mudança vale na próxima requisição do usuário.`)) {
        renderUsers(lastUsers);
        return;
    }
    accessPending = true;
    $("usersTable").setAttribute("aria-busy", "true");
    try {
        const result = await apiUsuario.alterarAcesso(id, change);
        if (result.ok) showToast("Acesso atualizado.");
        else showToast(mutationError(result, "Não foi possível alterar o acesso."), "danger");
    } finally {
        accessPending = false;
        await loadUsers();  // sempre reflete o estado persistido
    }
}

/**
 * Recarrega a lista real. Uma falha nunca inventa usuários nem apaga a lista já
 * exibida: quando já há dados na tela, eles permanecem e o erro vai para o aviso.
 * @returns {Promise<boolean>} true somente quando o backend confirmou a leitura.
 */
async function loadUsers() {
    if (!canPerform("users:list")) return false;
    const requestId = ++usersRequestId;
    $("refreshUsers").disabled = true;
    $("usersTable").setAttribute("aria-busy", "true");
    setUsersFeedback(usersRendered ? "Atualizando lista..." : "");
    if (!usersRendered) {
        resetUserMetrics();
        showUsersState("Carregando usuários...");
    }
    try {
        const result = await apiGet("/users");
        if (requestId !== usersRequestId) return false;
        if (!result.ok || !Array.isArray(result.data) || !result.data.every(user => user && typeof user === "object" && !Array.isArray(user))) {
            const message = mutationError(result, "Não foi possível carregar os usuários.");
            if (usersRendered) {
                setUsersFeedback(`${message} Os dados abaixo são os da última leitura bem-sucedida.`);
            } else {
                resetUserMetrics();
                showUsersState("Não foi possível carregar os usuários.");
                setUsersFeedback("");
            }
            return false;
        }
        lastUsers = result.data;
        renderUsers(result.data);
        usersRendered = true;
        setUsersFeedback("");
        return true;
    } catch {
        if (requestId === usersRequestId) {
            if (usersRendered) {
                setUsersFeedback("Não foi possível atualizar a lista. Os dados abaixo são os da última leitura bem-sucedida.");
            } else {
                resetUserMetrics();
                showUsersState("Não foi possível carregar os usuários.");
            }
        }
        return false;
    } finally {
        if (requestId === usersRequestId) {
            $("refreshUsers").disabled = false;
            $("usersTable").setAttribute("aria-busy", "false");
        }
    }
}

function openUserModal() {
    if (!canPerform("users:create")) {
        showToast("Seu perfil não possui permissão para esta ação.", "danger");
        return;
    }
    $("userForm").reset();
    $("userModal").classList.add("active");
}

function closeUserModal() {
    $("userModal").classList.remove("active");
}

document.addEventListener("DOMContentLoaded", async () => {
    if (!await window.sessionReady) return;
    const openButton = $("openUserModal");

    if (!openButton) {
        console.error('Botão com id="openUserModal" não encontrado.');
        return;
    }

    openButton.addEventListener(
        "click",
        () => openUserModal()
    );

    $("closeUserModal").addEventListener(
        "click",
        closeUserModal
    );

    $("cancelUserModal").addEventListener(
        "click",
        closeUserModal
    );

    $("refreshUsers").addEventListener("click", loadUsers);

    $("usersTable").addEventListener("change", event => {
        const unit = event.target.closest("[data-user-unit]");
        if (unit) {
            const unidade = unit.value.trim();
            if (unidade === unit.dataset.current) { unit.value = unidade; return; }
            changeUserAccess(Number(unit.dataset.userUnit), { unidade: unidade || null },
                unidade ? `alterar a unidade para "${unidade}"` : "remover a unidade");
            return;
        }
        const select = event.target.closest("[data-user-role]");
        if (!select || !select.value) return;
        changeUserAccess(Number(select.dataset.userRole), { perfil: select.value },
            `alterar o perfil para ${select.selectedOptions[0].textContent.trim()}`);
    });
    $("usersTable").addEventListener("click", event => {
        const button = event.target.closest("[data-user-active]");
        if (!button || button.disabled) return;
        const ativo = button.dataset.next === "true";
        changeUserAccess(Number(button.dataset.userActive), { ativo }, ativo ? "reativar o usuário" : "inativar o usuário");
    });

    $("userModal").addEventListener("click", event => {
        if (event.target === $("userModal")) {
            closeUserModal();
        }
    });

    $("userForm").addEventListener("submit", async event => {
        event.preventDefault();

        const data = Object.fromEntries(
            new FormData(event.currentTarget)
        );

        if (!canPerform("users:create") || signupPending) return;
        const email = data.email.trim().toLowerCase();
        if (data.password.trim().length < 6) {
            showToast("A senha deve ter pelo menos 6 caracteres.", "danger");
            return;
        }
            const submitBtn = $("userForm").querySelector('[type="submit"]');
            signupPending = true;
            if (submitBtn) submitBtn.disabled = true;

            try {
                const [nome, ...sobrenomePartes] = data.name.trim().split(/\s+/);
                const sobrenome = sobrenomePartes.join(" ") || null;
                if (!nome || !email || ![...$("signupRole").options].some(option => option.value === data.role)) {
                    showToast("Preencha nome, e-mail e um perfil válido.", "danger");
                    return;
                }

                const result = await apiPost("/signup", {
                    email,
                    password: data.password,
                    nome,
                    sobrenome,
                    perfil: data.role,
                    unidade: data.unit.trim() || null
                });

                if (result.status === 0) {
                    showToast("Não foi possível conectar ao servidor. Usuário não cadastrado.", "danger");
                    return;
                }

                if (result.ok) {
                    closeUserModal();
                    showToast("Usuário cadastrado com sucesso.");
                    // Uma falha de leitura não desfaz o POST /signup bem-sucedido.
                    if (!await loadUsers()) {
                        showToast("Usuário cadastrado com sucesso, mas a listagem não pôde ser atualizada. Tente atualizar a lista.", "warning");
                    }
                } else if (result.status === 400 && result.data?.message?.includes("Email already exists")) {
                    showToast("E-mail já cadastrado no sistema.", "danger");
                } else {
                    showToast(result.data?.message || "Falha ao cadastrar usuário.", "danger");
                }
            } catch (e) {
                console.error("[Admin] Erro ao cadastrar usuário:", e);
                showToast("Erro inesperado ao cadastrar usuário.", "danger");
            } finally {
                signupPending = false;
                if (submitBtn) submitBtn.disabled = false;
            }

    });

    await loadUsers();
});
