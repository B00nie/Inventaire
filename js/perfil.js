"use strict";

/**
 * perfil.js — Meu Perfil: dados pessoais da PRÓPRIA conta (PATCH /me/perfil).
 *
 * Fonte única dos dados: o usuário validado por /session (common.js) e a resposta do próprio
 * endpoint. Nunca /users nem ID fixo. Editáveis: nome, sobrenome, e-mail e telefone; perfil de
 * acesso e unidade são somente leitura. O id exibido acompanha o envio: se a sessão do navegador
 * trocou de conta, o servidor recusa (409) e esta página não aplica a resposta a outra identidade.
 */

const PROFILE_FIELDS = ["nome", "sobrenome", "email", "telefone"];
let profileUser = null;      // último estado confirmado (sessão ou resposta do servidor)
let profileUserId = null;    // identidade sob a qual o formulário foi preenchido
let profileSaving = false;
let profileCanEdit = false;

const profileEl = id => document.getElementById(id);

function profileStatus(message, type = "") {
    const status = profileEl("profileStatus");
    status.textContent = message;
    status.dataset.type = type;
}

function profileValues() {
    const form = profileEl("profileForm");
    return Object.fromEntries(PROFILE_FIELDS.map(field => [field, form.elements[field].value.trim()]));
}

// Só os campos alterados; telefone vazio vira null (sem telefone). E-mail comparado normalizado.
function profileChanges() {
    if (!profileUser) return {};
    const values = profileValues();
    values.email = values.email.toLowerCase();
    const changes = {};
    PROFILE_FIELDS.forEach(field => {
        const atual = profileUser[field] ?? "";
        if (values[field] !== atual) changes[field] = field === "telefone" && !values[field] ? null : values[field];
    });
    return changes;
}

function updateProfileButtons() {
    const dirty = Object.keys(profileChanges()).length > 0;
    profileEl("saveProfile").disabled = !profileCanEdit || profileSaving || !dirty;
    profileEl("cancelProfile").disabled = profileSaving || !dirty;
}

function setProfileFieldsDisabled(disabled) {
    const form = profileEl("profileForm");
    PROFILE_FIELDS.forEach(field => { form.elements[field].disabled = disabled; });
}

// textContent/value: nenhum dado do servidor é interpretado como HTML.
function renderProfile(user) {
    profileUser = { ...user };
    profileUserId = user.id;
    const form = profileEl("profileForm");
    PROFILE_FIELDS.forEach(field => { form.elements[field].value = user[field] ?? ""; });
    const recognized = user.perfil_reconhecido !== false;
    const name = [user.nome, user.sobrenome].filter(Boolean).join(" ") || "—";
    profileEl("profilePerfil").value = roleLabel(user.perfil, recognized);
    profileEl("profileUnidade").value = user.unidade || "—";
    profileEl("profileAvatar").textContent = initials(name);
    profileEl("profileNameDisplay").textContent = name;
    profileEl("profileEmailDisplay").textContent = user.email || "—";
    profileEl("profileRoleDisplay").textContent = roleLabel(user.perfil, recognized);
    form.setAttribute("aria-busy", "false");
    updateProfileButtons();
}

function validateProfile() {
    const values = profileValues();
    if (!values.nome || !values.sobrenome || !values.email) return "Nome, sobrenome e e-mail são obrigatórios.";
    const limits = { nome: 40, sobrenome: 90, email: 60, telefone: 45 };
    const longo = PROFILE_FIELDS.find(field => values[field].length > limits[field]);
    if (longo) return `O campo ${longo} aceita no máximo ${limits[longo]} caracteres.`;
    if (!profileEl("profileEmail").checkValidity()) return "Informe um e-mail válido.";
    return "";
}

async function saveProfile(event) {
    event.preventDefault();
    if (profileSaving || !profileCanEdit || !profileUser) return;
    const erro = validateProfile();
    if (erro) { profileStatus(erro, "danger"); return; }
    const changes = profileChanges();
    if (!Object.keys(changes).length) { profileStatus("Nenhuma alteração para salvar."); return; }

    const formUserId = profileUserId;
    profileSaving = true;
    setProfileFieldsDisabled(true);
    updateProfileButtons();
    profileStatus("Salvando...");
    let result;
    try {
        result = await apiPerfil.atualizar(formUserId, changes);
    } finally {
        profileSaving = false;
    }

    // Resposta atrasada: a identidade da página mudou durante o envio → nada é aplicado aqui.
    if (getSession()?.userId !== formUserId || profileUserId !== formUserId) return;
    setProfileFieldsDisabled(false);

    if (result.ok && result.data?.user?.id === formUserId) {
        if (!applySessionUser(result.data.user)) { identityChanged(); return; }
        renderProfile(result.data.user);
        profileStatus("Dados atualizados. Use o novo e-mail no próximo login; a senha não mudou.", "success");
        showToast("Perfil atualizado.");
        return;
    }
    if (result.status === 409 && /sessão deste navegador mudou/i.test(result.data?.message || "")) {
        identityChanged();
        return;
    }
    if (result.status === 400 || result.status === 409) {
        profileStatus(result.data?.message || "Verifique os dados informados.", "danger");
    } else if (result.status === 0 || result.status === -1 || result.status >= 500 || result.ok) {
        // Resultado incerto: não repete automaticamente; o formulário é preservado.
        profileStatus("O servidor não confirmou a alteração. Recarregue a página para conferir seus dados antes de tentar de novo.", "danger");
    } else {
        profileStatus(mutationError(result, "Não foi possível salvar."), "danger");
    }
    updateProfileButtons();
}

function cancelProfile() {
    if (profileSaving || !profileUser) return;
    renderProfile(profileUser);
    profileStatus("Alterações descartadas.");
}

document.addEventListener("DOMContentLoaded", async () => {
    if (!await window.sessionReady) return;
    const user = getSessionUser();
    if (!user) return;
    profileCanEdit = canPerform("profile:edit");
    renderProfile(user);
    setProfileFieldsDisabled(!profileCanEdit);
    profileStatus(profileCanEdit ? "" : "Seu perfil de acesso não permite editar dados pessoais.");

    const form = profileEl("profileForm");
    form.addEventListener("submit", saveProfile);
    form.addEventListener("input", updateProfileButtons);
    profileEl("cancelProfile").addEventListener("click", cancelProfile);
    // Troca de conta em outra aba: o formulário antigo nunca é salvo (common.js bloqueia a página).
    document.addEventListener("inventaire:identity-changed", () => { profileUser = null; profileUserId = null; profileCanEdit = false; });
});
