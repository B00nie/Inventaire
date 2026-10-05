
"use strict";

// Etapa 2E: as permissões efetivas vêm do servidor (GET /session → user.permissoes),
// calculadas a partir do perfil persistido. Este arquivo NÃO contém a matriz de perfis:
// apenas traduz ações/páginas da interface para nomes de permissão do backend. Sem
// permissões válidas do servidor, nada é concedido. O backend revalida toda operação.
const UI_ACTIONS = Object.freeze({
    "inventory:create": "produtos:gerenciar",
    "inventory:edit": "produtos:gerenciar",
    "inventory:delete": "produtos:gerenciar",
    "inventory:export": "produtos:consultar",
    "locations:manage": "localizacoes:gerenciar",
    "movements:create": ["movimentacoes:entrada", "movimentacoes:saida", "movimentacoes:ajuste"],
    "counts:create": "contagens:registrar",
    "counts:apply": "contagens:aplicar",
    "suppliers:manage": "fornecedores:gerenciar",
    "profile:edit": "perfil:editar_proprio",
    "users:list": "usuarios:gerenciar",
    "users:create": "usuarios:gerenciar",
    "users:manage": "usuarios:gerenciar"
});
// null = qualquer usuário autenticado e ativo (preferências e dados da própria conta).
// Lista = todas exigidas (Etapa 2F: páginas que combinam dados de vários recursos, como no backend).
// Correção de acessos: Relatórios exige também relatorios:consultar (OPERADOR não tem); Fornecedores
// continua exigindo fornecedores:consultar, que o OPERADOR deixou de ter.
const PAGE_PERMISSIONS = Object.freeze({
    dashboard: ["produtos:consultar", "movimentacoes:consultar", "contagens:consultar"],
    inventory: "produtos:consultar", movements: "movimentacoes:consultar",
    locations: "localizacoes:consultar", counts: "contagens:consultar", suppliers: "fornecedores:consultar",
    reports: ["relatorios:consultar", "produtos:consultar", "localizacoes:consultar", "movimentacoes:consultar", "contagens:consultar"],
    admin: "usuarios:gerenciar", settings: null, profile: null
});
const ROLE_LABELS = Object.freeze({ OPERADOR: "Operador", GESTOR: "Gestor", AUDITOR: "Auditor", ADMINISTRADOR: "Administrador" });
let verifiedSession = null;
let verifiedProfile = null;
let verifiedUser = null;  // cópia da última resposta validada de /session (ou de PATCH /me/perfil)
let resolveSessionReady;
window.sessionReady = new Promise(resolve => { resolveSessionReady = resolve; });

function getSession() { return verifiedSession; }
function getProfile() {
    return verifiedProfile || { name: "—", email: "—", role: "—", unit: "", phone: "" };
}
function getCurrentRole() { return verifiedSession?.role || ""; }
function getSessionUser() { return verifiedUser ? { ...verifiedUser } : null; }
function profileFromUser(user, session) {
    return {
        name: [user.nome, user.sobrenome].filter(Boolean).join(" ") || "—",
        email: user.email || "—", role: roleLabel(user.perfil, session.recognized), unit: user.unidade || "", phone: user.telefone || ""
    };
}
function roleLabel(role, recognized = true) {
    if (!recognized) return `${role || "—"} (perfil não reconhecido)`;
    return ROLE_LABELS[role] || role || "—";
}
function hasPermission(permission) {
    return Boolean(verifiedSession?.permissions?.has(permission));
}

function canAccessPage(page){
    if (!verifiedSession || !(page in PAGE_PERMISSIONS)) return false;
    const required = PAGE_PERMISSIONS[page];
    return required === null || [].concat(required).every(hasPermission);
}

// Aceita ação de interface (ex.: "counts:apply") ou permissão do backend (ex.: "movimentacoes:ajuste").
function canPerform(action){
    const required = UI_ACTIONS[action] ?? action;
    return [].concat(required).some(hasPermission);
}

// Converte a resposta de /session em estado verificado; formato inesperado não concede nada.
function sessionFromUser(user) {
    if (!user?.id || user.ativo === false || !Array.isArray(user.permissoes)
        || !user.permissoes.every(item => typeof item === "string")) return null;
    return {
        authenticated: true, userId: user.id, role: user.perfil, admin: user.admin === true,
        recognized: user.perfil_reconhecido !== false, permissions: new Set(user.permissoes)
    };
}

// ─────────────────────────────────────────────
// Datas vindas do backend
// ─────────────────────────────────────────────
//
// O repositório atual formata alertas como "AAAA-MM-DD HH:mm:ss", sem fuso.
// Mantemos também suporte a HTTP-date para respostas legadas e campos datetime
// serializados pelo Flask. Preservamos os componentes literais: o contrato de
// alertas não declara UTC. Não inferir fuso a partir de um relatório histórico.
const BACKEND_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const HTTP_DATE_PATTERN = /^[A-Za-z]{3},\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/;

/**
 * Carimbo ordenável "AAAA-MM-DDTHH:mm:ss" a partir de uma data do backend,
 * aceitando tanto HTTP-date quanto ISO 8601. Devolve "" quando não reconhece o
 * valor — um formato desconhecido nunca vira uma data plausível.
 * @param {unknown} value
 * @returns {string}
 */
function backendTimestampKey(value) {
    if (typeof value !== "string") return "";

    const httpDate = HTTP_DATE_PATTERN.exec(value.trim());
    if (httpDate) {
        const [, day, month, year, hour, minute, second] = httpDate;
        const monthIndex = BACKEND_MONTHS.indexOf(month);
        if (monthIndex === -1) return "";
        return `${year}-${String(monthIndex + 1).padStart(2, "0")}-${day.padStart(2, "0")}T${hour}:${minute}:${second}`;
    }

    const iso = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/.exec(value.trim());
    if (iso) return `${iso[1]}T${iso[2]}`;

    const isoDay = /^(\d{4}-\d{2}-\d{2})$/.exec(value.trim());
    return isoDay ? `${isoDay[1]}T00:00:00` : "";
}

/**
 * Dia "AAAA-MM-DD" de uma data do backend, ou "" quando indeterminado.
 * @param {unknown} value
 * @returns {string}
 */
function backendDayKey(value) {
    return backendTimestampKey(value).slice(0, 10);
}

/**
 * Dia de hoje no fuso local, no mesmo formato de backendDayKey().
 * @returns {string}
 */
function localDayKey(date = new Date()) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function mutationError(result, fallback) {
    if (result.status === 401) return "Sessão expirada. Entre novamente.";
    if (result.status === 403) return "Seu perfil não possui permissão para esta ação.";
    if (result.status === 404) return "Registro não encontrado. Atualize a lista.";
    if (result.status === 409) return result.data?.message || result.data?.error || "A alteração conflita com um registro existente.";
    if (result.status === 0 || result.status === -1) return "Não foi possível conectar ao servidor. Tente novamente.";
    if (result.status >= 500) return "O servidor não confirmou a operação. Atualize a lista antes de tentar novamente.";
    return result.data?.error || result.data?.message || fallback;
}

// Estado explícito para gráficos sem dados ou sem biblioteca disponível.
function showChartState(id, message) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    canvas.hidden = true;
    canvas.style.display = "none";
    let state = document.getElementById(`${id}State`);
    if (!state) {
        state = document.createElement("p");
        state.id = `${id}State`;
        state.className = "empty";
        canvas.parentElement.appendChild(state);
    }
    state.textContent = message;
}

// Etapa 2G: exibição do CNPJ normalizado (14 caracteres). Apenas formato; não é validação fiscal.
function formatCnpj(value){
    const cnpj = String(value ?? "");
    return /^[0-9A-Z]{12}[0-9]{2}$/.test(cnpj)
        ? `${cnpj.slice(0, 2)}.${cnpj.slice(2, 5)}.${cnpj.slice(5, 8)}/${cnpj.slice(8, 12)}-${cnpj.slice(12)}` : (cnpj || "—");
}

function escapeHtml(value){
    return String(value??"")
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}

// Aspas CSV escapam delimitadores, mas não impedem interpretação como fórmula
// pela planilha. Campos textuais potencialmente executáveis recebem apóstrofo.
function csvEscape(value) {
    let text = String(value ?? "");
    if (typeof value === "string" && (/^\s*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text))) {
        text = "'" + text;
    }
    return `"${text.replaceAll('"', '""')}"`;
}

function initials(name){
    return String(name||"U")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0,2)
        .map(part=>part[0].toUpperCase())
        .join("");
}

async function logout() {
    const result = await apiPost("/logout", {});
    if (!result.ok) {
        showToast("Não foi possível encerrar a sessão. Tente novamente.", "danger");
        return;
    }
    verifiedSession = null;
    verifiedProfile = null;
    clearApiSession();
    window.location.href = "login.html";
}

function showToast(message,type="success"){
    const container=document.getElementById("toastContainer");
    if(!container)return;

    const toast=document.createElement("div");
    toast.className="toast";
    toast.style.borderLeftColor=
        type==="danger"?"var(--danger)":
        type==="warning"?"var(--warning)":
        "var(--success)";
    toast.textContent=message;

    container.appendChild(toast);
    setTimeout(()=>toast.remove(),3000);
}

function showAccessDenied(){
    const main=document.querySelector(".main");

    if(!main)return;

    main.innerHTML=`
        <div
            class="card"
            style="
                max-width:680px;
                margin:80px auto;
                text-align:center;
                padding:42px;
            "
        >
            <i
                class="fa-solid fa-lock"
                style="
                    font-size:54px;
                    color:var(--danger);
                    margin-bottom:20px;
                "
            ></i>

            <h1>Acesso não autorizado</h1>

            <p
                class="text-muted"
                style="
                    margin:14px 0 24px;
                    line-height:1.7;
                "
            >
                Seu perfil não possui permissão para acessar esta página.
                Entre em contato com um administrador caso precise de acesso.
            </p>

            <a class="btn" href="${canAccessPage("dashboard") ? "dashboard.html" : "perfil.html"}">
                <i class="fa-solid fa-arrow-left"></i>
                ${canAccessPage("dashboard") ? "Voltar ao Dashboard" : "Ir para Meu Perfil"}
            </a>

            <button class="btn secondary" type="button" onclick="logout()">
                <i class="fa-solid fa-right-from-bracket"></i>
                Sair
            </button>
        </div>
    `;
}

function applyRolePermissions(){
    const currentPage=document.body.dataset.page;

    document.querySelectorAll(".nav a[data-page]").forEach(link=>{
        const allowed=canAccessPage(link.dataset.page);
        const listItem=link.closest("li");

        if(listItem){
            listItem.style.display=allowed?"":"none";
        }
    });

    document.querySelectorAll("[data-permission]").forEach(element=>{
        element.style.display=canPerform(element.dataset.permission)?"":"none";
    });
    // Até aqui o CSS mantém ações protegidas ocultas (sem "piscar" ações indevidas).
    document.body.classList.add("permissions-ready");

    if(currentPage&&!canAccessPage(currentPage)){
        showAccessDenied();
        return false;
    }

    return true;
}

// ─────────────────────────────────────────────
// Identidade compartilhada entre abas
// ─────────────────────────────────────────────
// Abas e janelas do mesmo contexto do navegador compartilham UM cookie de sessão: um login ou
// logout em outra aba troca a identidade efetiva de todas. Esta aba nunca continua exibindo (nem
// operando como) a identidade antiga: ao detectar a troca, bloqueia a página e pede recarga.
// Contas simultâneas exigem contextos independentes (outro perfil do navegador ou janela anônima).
// O marcador guarda só o id exibido; a autorização continua exclusivamente no backend.
let identityLost = false;
function publishIdentity(userId) {
    try { if (localStorage.getItem(IDENTITY_KEY) !== String(userId)) localStorage.setItem(IDENTITY_KEY, String(userId)); }
    catch { /* storage indisponível: a verificação ao voltar à aba continua valendo */ }
}
function identityChanged() {
    if (identityLost) return;
    identityLost = true;
    verifiedSession = null;
    verifiedProfile = null;
    verifiedUser = null;
    document.dispatchEvent(new CustomEvent("inventaire:identity-changed"));
    document.querySelector(".sidebar")?.setAttribute("hidden", "");
    const main = document.querySelector(".main");
    if (main) main.innerHTML = `<div class="card" role="alert" style="max-width:680px;margin:80px auto;text-align:center;padding:42px">
        <i class="fa-solid fa-user-lock" style="font-size:48px;color:var(--warning);margin-bottom:18px"></i>
        <h1>A sessão deste navegador mudou</h1>
        <p class="text-muted" style="margin:14px 0 24px;line-height:1.7">Houve login ou logout em outra aba ou janela.
        Esta página foi bloqueada para não exibir nem alterar dados da conta anterior.
        Para usar duas contas ao mesmo tempo, use outro perfil do navegador ou uma janela anônima.</p>
        <button class="btn" type="button" onclick="window.location.reload()"><i class="fa-solid fa-rotate"></i>Recarregar</button></div>`;
}
// Mesma origem do frontend: evento de storage vindo de outra aba.
window.addEventListener("storage", event => {
    if (event.key === IDENTITY_KEY && verifiedSession && event.newValue !== String(verifiedSession.userId)) identityChanged();
});
// Origem diferente (ex.: localhost × 127.0.0.1) não recebe o evento: confere /session ao voltar à aba.
async function checkIdentity() {
    if (!verifiedSession || identityLost) return;
    const expected = verifiedSession.userId;
    const result = await apiGet("/session");
    if (result.status === 401 || result.status === 403) { identityChanged(); return; }
    const session = result.ok ? sessionFromUser(result.data?.user) : null;
    if (session && session.userId !== expected) identityChanged();
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") checkIdentity(); });
// Página restaurada do cache de voltar/avançar: nunca reapresenta a identidade antiga sem revalidar.
window.addEventListener("pageshow", event => { if (event.persisted) window.location.reload(); });

// Aplica dados atualizados do PRÓPRIO usuário (resposta de PATCH /me/perfil). Recusa outra identidade.
function applySessionUser(user) {
    const session = sessionFromUser(user);
    if (!session || !verifiedSession || identityLost || session.userId !== verifiedSession.userId) return false;
    verifiedSession = session;
    verifiedUser = { ...user };
    verifiedProfile = profileFromUser(user, session);
    createUserChip(getProfile());
    applyRolePermissions();
    return true;
}

// 403 de uma ação (ex.: perfil rebaixado em outra sessão): relê /session e reaplica a
// interface. Inativo/removido → login. Falha de leitura → nenhuma ação fica liberada.
let permissionsRefresh = null;
function refreshPermissions(){
    permissionsRefresh ??= (async () => {
        const result = await apiGet("/session");
        const session = result.ok ? sessionFromUser(result.data?.user) : null;
        if (result.status === 403 || result.status === 401) {
            clearApiSession();
            window.location.href = "login.html";
            return;
        }
        // Resposta de outra identidade (troca de conta em outra aba): nunca é misturada a esta página.
        if (session && verifiedSession && session.userId !== verifiedSession.userId) { identityChanged(); return; }
        if (identityLost) return;
        verifiedSession = session;
        if (session) {
            verifiedUser = { ...result.data.user };
            verifiedProfile = profileFromUser(result.data.user, session);
        }
        applyRolePermissions();
        document.dispatchEvent(new CustomEvent("inventaire:permissions"));
    })().finally(() => { permissionsRefresh = null; });
    return permissionsRefresh;
}
document.addEventListener("inventaire:forbidden", () => { if (verifiedSession) refreshPermissions(); });

function createUserChip(profile){
    const container=document.getElementById("userChip");
    if(!container)return;

    container.innerHTML=`
        <button id="userChipButton" type="button">
            <span class="avatar">${escapeHtml(initials(profile.name))}</span>

            <span class="user-copy">
                <strong>${escapeHtml(profile.name)}</strong><br>
                <small>${escapeHtml(profile.role)}</small>
            </span>

            <i class="fa-solid fa-chevron-down"></i>
        </button>

        <div class="user-menu" id="userMenu">
            <a href="perfil.html">
                <i class="fa-solid fa-user"></i>
                Meu perfil
            </a>

            ${
                canAccessPage("settings")
                    ? `
                        <a href="configuracao.html">
                            <i class="fa-solid fa-gear"></i>
                            Configurações
                        </a>
                    `
                    : ""
            }

            <button type="button" onclick="logout()">
                <i class="fa-solid fa-right-from-bracket"></i>
                Sair
            </button>
        </div>
    `;

    const button=document.getElementById("userChipButton");
    const menu=document.getElementById("userMenu");

    button.addEventListener("click",event=>{
        event.stopPropagation();
        menu.classList.toggle("active");
    });

    document.addEventListener("click",()=>menu.classList.remove("active"));
}

const NOTIFICATION_SEVERITY_META={
    3:{badge:"danger",icon:"fa-triangle-exclamation",color:"var(--danger)",label:"Crítico"},
    2:{badge:"warning",icon:"fa-triangle-exclamation",color:"var(--warning)",label:"Médio"},
    1:{badge:"success",icon:"fa-shield-halved",color:"var(--success)",label:"Baixo"}
};

function notificationSeverityMeta(severidade){
    return NOTIFICATION_SEVERITY_META[severidade]||{
        badge:"",icon:"fa-circle-info",color:"var(--text-muted)",label:"Não informada"
    };
}


function ensureNotificationClearButton(){
    const panel=document.getElementById("notificationPanel");
    if(!panel)return null;

    let button=document.getElementById("clearNotificationsButton");

    if(button)return button;

    const title=panel.querySelector(".section-title");
    if(!title)return null;

    title.style.alignItems="center";

    button=document.createElement("button");
    button.id="clearNotificationsButton";
    button.type="button";
    button.className="notification-clear-btn";
    button.innerHTML='<i class="fa-solid fa-broom"></i><span>Limpar</span>';
    button.title="Limpar notificações recentes";

    title.appendChild(button);

    button.addEventListener("click",event=>{
        event.stopPropagation();

        if(typeof clearRecentAlerts==="function"){
            clearRecentAlerts();
        }

        renderNotificationPanel();

        if(typeof showToast==="function"){
            showToast("Notificações recentes limpas.");
        }
    });

    return button;
}

function renderNotificationPanel(){
    const list=document.getElementById("notificationList");
    const count=document.getElementById("notificationCount");

    if(!list||!count)return;

    const alerts=typeof getRecentAlerts==="function"?getRecentAlerts():[];

    count.textContent=String(alerts.length);

    if(!alerts.length){
        list.innerHTML=`<div class="notification-empty text-muted" style="padding:12px 0">Nenhuma notificação recente.</div>`;
        return;
    }

    list.innerHTML=alerts.map(alerta=>{
        const meta=notificationSeverityMeta(alerta.severidade);
        const evento=formatAlertNotification(alerta);
        return `
            <div class="notification-item">
                <i class="fa-solid ${meta.icon}" style="color:${meta.color}"></i>
                <div>
                    <strong>${escapeHtml(meta.label)}</strong>
                    <p class="text-muted">${escapeHtml(evento)}</p>
                </div>
            </div>
        `;
    }).join("");
}

function configureNotifications(){
    const button=document.getElementById("notificationButton");
    const panel=document.getElementById("notificationPanel");

    if(!button||!panel)return;

    button.addEventListener("click",event=>{
        event.stopPropagation();
        panel.classList.toggle("active");
    });

    document.addEventListener("click",()=>panel.classList.remove("active"));

    // Adiciona o botão de limpar e renderiza o estado inicial.
    ensureNotificationClearButton();
    renderNotificationPanel();

    if(typeof onAlert==="function"){
        onAlert(alerta=>{
            renderNotificationPanel();

            if(typeof showToast==="function"){
                showToast(
                    formatAlertNotification(alerta),
                    alerta.severidade>=3?"danger":alerta.severidade===2?"warning":"success"
                );
            }
        });
    }

    if(typeof initNotifications==="function"){
        initNotifications();
    }
}

const GLOBAL_SEARCH_ITEMS = [
    { page: "locations", title: "Localizações", description: "Corredores, posições e lotes armazenados",
      href: "localizacoes.html", icon: "fa-location-dot", keywords: ["lote", "posição", "corredor", "rastreamento"] },
    {
        page: "movements", title: "Movimentações", description: "Entradas, saídas, ajustes e histórico de estoque",
        href: "movimentacoes.html", icon: "fa-right-left", keywords: ["movimentações", "entrada", "saída", "ajuste", "histórico"]
    },
    { page: "counts", title: "Contagens", description: "Contagem física, divergências e ajustes de inventário",
      href: "contagens.html", icon: "fa-clipboard-check", keywords: ["contagem", "inventário cíclico", "divergência", "conferência", "auditoria"] },
    { page: "suppliers", title: "Fornecedores", description: "Cadastro de fornecedores e origem dos recebimentos",
      href: "fornecedores.html", icon: "fa-truck-field", keywords: ["fornecedor", "fornecedores", "cnpj", "recebimento", "origem"] },
    {
        page: "dashboard",
        title: "Dashboard",
        description: "Indicadores reais de estoque, reposição e divergências",
        href: "dashboard.html",
        icon: "fa-chart-line",
        keywords: ["dashboard", "visão geral", "inventário", "depósito", "indicadores"]
    },
    { page: "reports", title: "Relatórios", description: "Posição de estoque, reposição, movimentações, divergências e saídas",
      href: "relatorios.html", icon: "fa-file-lines",
      keywords: ["relatórios", "reposição", "estoque baixo", "estoque mínimo", "posição de estoque", "saídas no período", "csv"] },
    {
        page: "inventory",
        title: "Inventário",
        description: "Estoque, validade e cadastro de produtos",
        href: "inventario.html",
        icon: "fa-boxes-stacked",
        keywords: ["inventário", "estoque", "validade", "produto", "produtos"]
    },
    {
        page: "admin",
        title: "Administração",
        description: "Usuários, permissões e histórico de acessos",
        href: "administracao.html",
        icon: "fa-users-gear",
        keywords: ["administração", "usuários", "permissões", "acessos"]
    },
    {
        page: "settings",
        title: "Configurações",
        description: "Aparência e tema neste navegador",
        href: "configuracao.html",
        icon: "fa-gear",
        keywords: ["configurações", "tema", "aparência", "claro", "escuro"]
    },
    {
        page: "profile",
        title: "Meu Perfil",
        description: "Dados pessoais e preferências",
        href: "perfil.html",
        icon: "fa-user",
        keywords: ["perfil", "conta", "senha", "dados pessoais"]
    },
];

function normalizeSearchText(value){
    return String(value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .trim();
}

function configureGlobalSearch(){
    const input = document.getElementById("globalSearch");

    if(!input){
        return;
    }

    const wrapper = input.closest(".search-global");

    if(!wrapper){
        return;
    }

    let results = wrapper.querySelector(".search-results");

    if(!results){
        results = document.createElement("div");
        results.className = "search-results";
        results.setAttribute("role", "listbox");
        wrapper.appendChild(results);
    }

    let visibleItems = [];
    let selectedIndex = -1;

    function closeResults(){
        results.classList.remove("active");
        selectedIndex = -1;
    }

    function openPage(index){
        const item = visibleItems[index];

        if(item){
            window.location.href = item.href;
        }
    }

    function updateSelection(){
        results.querySelectorAll(".search-result-item").forEach((button, index)=>{
            button.classList.toggle("selected", index === selectedIndex);
        });
    }

    function renderResults(){
        const term = normalizeSearchText(input.value);

        if(!term){
            closeResults();
            results.innerHTML = "";
            return;
        }

        visibleItems = GLOBAL_SEARCH_ITEMS.filter(item=>{
            const allowed = typeof canAccessPage === "function"
                ? canAccessPage(item.page)
                : true;

            const searchable = normalizeSearchText([
                item.title,
                item.description,
                ...(item.keywords || [])
            ].join(" "));

            return allowed && searchable.includes(term);
        });

        selectedIndex = visibleItems.length ? 0 : -1;

        if(!visibleItems.length){
            results.innerHTML = `
                <div class="search-empty">
                    Nenhum resultado encontrado.
                </div>
            `;
            results.classList.add("active");
            return;
        }

        results.innerHTML = visibleItems.map((item, index)=>`
            <button
                type="button"
                class="search-result-item ${index === selectedIndex ? "selected" : ""}"
                data-search-index="${index}"
            >
                <i class="fa-solid ${item.icon}"></i>

                <span class="search-result-copy">
                    <strong>${escapeHtml(item.title)}</strong>
                    <small>${escapeHtml(item.description)}</small>
                </span>
            </button>
        `).join("");

        results.classList.add("active");

        results.querySelectorAll(".search-result-item").forEach(button=>{
            button.addEventListener("click", ()=>{
                openPage(Number(button.dataset.searchIndex));
            });
        });
    }

    input.addEventListener("input", renderResults);

    input.addEventListener("focus", ()=>{
        if(input.value.trim()){
            renderResults();
        }
    });

    input.addEventListener("keydown", event=>{
        if(!results.classList.contains("active")){
            if(event.key === "Enter"){
                renderResults();
            }
            return;
        }

        if(event.key === "ArrowDown"){
            event.preventDefault();

            if(visibleItems.length){
                selectedIndex = (selectedIndex + 1) % visibleItems.length;
                updateSelection();
            }
        }

        if(event.key === "ArrowUp"){
            event.preventDefault();

            if(visibleItems.length){
                selectedIndex =
                    (selectedIndex - 1 + visibleItems.length) %
                    visibleItems.length;

                updateSelection();
            }
        }

        if(event.key === "Enter"){
            event.preventDefault();

            if(selectedIndex >= 0){
                openPage(selectedIndex);
            }
        }

        if(event.key === "Escape"){
            closeResults();
        }
    });

    document.addEventListener("click", event=>{
        if(!wrapper.contains(event.target)){
            closeResults();
        }
    });
}


document.addEventListener("DOMContentLoaded", async () => {
    if (window.location.pathname.endsWith("login.html")) { resolveSessionReady(true); return; }
    const result = await apiGet("/session");
    const user = result.data?.user;
    if (result.status === 401) {
        clearApiSession();
        resolveSessionReady(false);
        window.location.href = "login.html";
        return;
    }
    const session = result.ok && result.data?.authenticated === true ? sessionFromUser(user) : null;
    if (!session) {
        // Sem permissões verificadas (falha, formato inesperado ou usuário desativado): nada é liberado.
        if (result.status === 403) clearApiSession();
        resolveSessionReady(false);
        document.querySelector(".main").innerHTML = '<div class="card"><h1>Sessão indisponível</h1><p>Não foi possível validar sua sessão e suas permissões. Tente recarregar a página.</p><a href="login.html">Voltar ao login</a></div>';
        return;
    }
    verifiedSession = session;
    verifiedUser = { ...user };
    verifiedProfile = profileFromUser(user, session);
    publishIdentity(session.userId);
    const allowed = applyRolePermissions();
    resolveSessionReady(allowed);
    createUserChip(getProfile());  // logout sempre disponível, mesmo em página negada
    if (!allowed) return;
    configureNotifications();
    configureGlobalSearch();
    const currentPage = document.body.dataset.page;
    document.querySelectorAll(".nav a[data-page]").forEach(link => {
        link.classList.toggle("active", link.dataset.page === currentPage);
    });
});

window.logout=logout;
window.showToast=showToast;
window.canAccessPage=canAccessPage;
window.canPerform=canPerform;
window.getCurrentRole=getCurrentRole;
window.applyRolePermissions=applyRolePermissions;
window.refreshPermissions=refreshPermissions;
window.getSessionUser=getSessionUser;
window.applySessionUser=applySessionUser;
window.identityChanged=identityChanged;
window.roleLabel=roleLabel;
window.backendTimestampKey=backendTimestampKey;
window.backendDayKey=backendDayKey;
window.localDayKey=localDayKey;
