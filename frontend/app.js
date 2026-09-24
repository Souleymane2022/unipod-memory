const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---- traductions (FR / EN)
const I18N = {
  fr: {
    tagline: "La mémoire collective du groupe : messages, réunions et documents.",
    tab_ask: "Poser une question", tab_ingest: "Ajouter des documents", tab_summary: "Résumé & décisions",
    wa_title: "UniPods Memory sur WhatsApp",
    wa_text: "Posez vos questions directement depuis WhatsApp : scannez le QR code ou appuyez sur le bouton, puis envoyez « aide ».",
    wa_button: "Ouvrir WhatsApp",
    wa_note: "Démo : numéro de test Meta, seuls les numéros autorisés reçoivent une réponse.",
    tg_title: "Aussi sur Telegram",
    tg_text: "Ouvert à tous : scannez le QR code ou appuyez sur le bouton, puis envoyez /aide ou posez directement votre question.",
    tg_button: "Ouvrir Telegram",
    credit: "UniPods Memory · Hackathon UniPod",
    welcome: "Bonjour ! Posez-moi une question sur ce qui s'est dit dans le groupe, en réunion ou dans les documents. Je réponds uniquement à partir des sources indexées et je les cite. Vous pouvez écrire en français ou en anglais.",
    examples: [
      "Quelle est la date limite de dépôt des projets pour le hackathon ?",
      "Qu'est-ce qui a été décidé pour l'imprimante 3D pendant la réunion ?",
      "Comment réserver une machine du fablab ?",
      "Quel est le salaire du directeur de l'UniPod ?",
    ],
    placeholder: "Ex. : Quand a lieu la prochaine réunion ?", ask_btn: "Demander",
    searching: "Recherche dans la mémoire du groupe…",
    mode_extractive: "Mode extractif (sans LLM) : citations directes des sources.",
    mode_llm: (m) => `Réponse rédigée par ${m} à partir des sources.`,
    passage: "passage",
    type: { chat: "Messages", transcript: "Réunion", document: "Document" },
    ingest_title: "Ajouter des fichiers",
    ingest_hint: "Formats : .txt, .md, .pdf, .docx (Word), .odt, .html. Exports de chat (<code>[2026-09-08 09:12] Nom: message</code> ou WhatsApp), transcriptions (<code>[00:04:05] Nom: texte</code>) et documents sont détectés automatiquement. Un fichier du même nom remplace l'ancienne version.",
    f_type: "Type", f_author: "Auteur (optionnel)", f_date: "Date (optionnel)",
    t_auto: "Détection automatique", t_chat: "Messages (chat)", t_transcript: "Transcription de réunion", t_document: "Document",
    index_btn: "Indexer", indexing: "Indexation en cours…",
    too_large: (n) => `${n} dépasse 4 Mo, la limite d'envoi de l'hébergement. Découpez-le ou exportez-le en .txt.`,
    ingested: (d, type) => `✔ ${d.source} — ${type}, ${d.chunks} passage(s) (${d.chunk_words.join(", ")} mots)`,
    docs_title: "Documents indexés",
    th: ["Source", "Type", "Dates", "Auteurs", "Passages", ""],
    delete: "Supprimer", delete_title: "Retirer de la mémoire", confirm_delete: (s) => `Retirer ${s} de la mémoire ?`,
    no_docs: "Aucun document. Ajoutez-en ci-dessus.",
    summary_title: "Résumé automatique",
    summary_hint: "Choisissez une conversation ou une réunion : résumé, décisions et tâches extraites.",
    summary_btn: "Résumer", analysing: "Analyse en cours…", participants: "Participants",
    summary: "Résumé", decisions: "Décisions", tasks: "Tâches / actions",
    no_decisions: "Aucune décision détectée.", no_tasks: "Aucune tâche détectée.",
    summary_extractive: "Mode extractif (heuristiques, sans LLM) : les phrases sont citées dans leur langue d'origine.",
    status_ok: (h) => `${h.chunks} passages indexés · LLM : ${h.llm === "none" ? "aucun (mode extractif)" : h.llm}` +
      (h.store === "postgres" ? " · base PostgreSQL" : "") + (h.ephemeral_storage ? " · stockage temporaire (démo)" : ""),
    status_ephemeral: "Hébergement serverless : les documents ajoutés peuvent disparaître au redémarrage. Le jeu de démo est réindexé automatiquement.",
    status_error: "Erreur serveur : ", status_starting: (n) => `Démarrage du serveur… (${n}/12)`,
    status_down: (m) => `API injoignable (${m}). Ouvrez /api/health pour le détail.`,
    http_error: (s) => `Erreur HTTP ${s}`,
  },
  en: {
    tagline: "The group's collective memory: messages, meetings and documents.",
    tab_ask: "Ask a question", tab_ingest: "Add documents", tab_summary: "Summary & decisions",
    wa_title: "UniPods Memory on WhatsApp",
    wa_text: "Ask your questions straight from WhatsApp: scan the QR code or tap the button, then send \"help\".",
    wa_button: "Open WhatsApp",
    wa_note: "Demo: Meta test number, only authorised numbers receive a reply.",
    tg_title: "Also on Telegram",
    tg_text: "Open to everyone: scan the QR code or tap the button, then send /help or simply ask your question.",
    tg_button: "Open Telegram",
    credit: "UniPods Memory · UniPod Hackathon",
    welcome: "Hello! Ask me anything about what was said in the group, in meetings or in documents. I only answer from the indexed sources and I cite them. You can write in English or French.",
    examples: [
      "What is the deadline to submit hackathon projects?",
      "What was decided about the 3D printer during the meeting?",
      "How do I book a fablab machine?",
      "What is the director's salary?",
    ],
    placeholder: "E.g.: When is the next meeting?", ask_btn: "Ask",
    searching: "Searching the group's memory…",
    mode_extractive: "Extractive mode (no LLM): direct quotes from the sources.",
    mode_llm: (m) => `Answer written by ${m} from the sources.`,
    passage: "passage",
    type: { chat: "Messages", transcript: "Meeting", document: "Document" },
    ingest_title: "Add files",
    ingest_hint: "Formats: .txt, .md, .pdf, .docx (Word), .odt, .html. Chat exports (<code>[2026-09-08 09:12] Name: message</code> or WhatsApp), transcripts (<code>[00:04:05] Name: text</code>) and documents are detected automatically. A file with the same name replaces the previous version.",
    f_type: "Type", f_author: "Author (optional)", f_date: "Date (optional)",
    t_auto: "Automatic detection", t_chat: "Messages (chat)", t_transcript: "Meeting transcript", t_document: "Document",
    index_btn: "Index", indexing: "Indexing…",
    too_large: (n) => `${n} is larger than 4 MB, the hosting upload limit. Split it or export it as .txt.`,
    ingested: (d, type) => `✔ ${d.source} — ${type}, ${d.chunks} passage(s) (${d.chunk_words.join(", ")} words)`,
    docs_title: "Indexed documents",
    th: ["Source", "Type", "Dates", "Authors", "Passages", ""],
    delete: "Delete", delete_title: "Remove from memory", confirm_delete: (s) => `Remove ${s} from memory?`,
    no_docs: "No documents yet. Add some above.",
    summary_title: "Automatic summary",
    summary_hint: "Pick a conversation or a meeting: summary, decisions and extracted tasks.",
    summary_btn: "Summarize", analysing: "Analysing…", participants: "Participants",
    summary: "Summary", decisions: "Decisions", tasks: "Tasks / actions",
    no_decisions: "No decision detected.", no_tasks: "No task detected.",
    summary_extractive: "Extractive mode (heuristics, no LLM): sentences are quoted in their original language.",
    status_ok: (h) => `${h.chunks} passages indexed · LLM: ${h.llm === "none" ? "none (extractive mode)" : h.llm}` +
      (h.store === "postgres" ? " · PostgreSQL database" : "") + (h.ephemeral_storage ? " · temporary storage (demo)" : ""),
    status_ephemeral: "Serverless hosting: uploaded documents may disappear on restart. The demo dataset is re-indexed automatically.",
    status_error: "Server error: ", status_starting: (n) => `Starting server… (${n}/12)`,
    status_down: (m) => `API unreachable (${m}). Open /api/health for details.`,
    http_error: (s) => `HTTP error ${s}`,
  },
};

function initialLang() {
  try {
    const saved = localStorage.getItem("lang");
    if (saved === "fr" || saved === "en") return saved;
  } catch { /* stockage indisponible */ }
  return (navigator.language || "fr").toLowerCase().startsWith("fr") ? "fr" : "en";
}
let lang = initialLang();
const t = (key, ...args) => { const v = I18N[lang][key]; return typeof v === "function" ? v(...args) : v; };
const typeLabel = (type) => I18N[lang].type[type] || type;

function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll("#docs thead th").forEach((th, i) => { th.textContent = t("th")[i]; });
  $("#examples").innerHTML = t("examples").map((q) => `<button class="chip" type="button">${esc(q)}</button>`).join("");
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => ask(c.textContent)));
  document.querySelectorAll(".lang-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
  if (lastHealth) renderStatus(lastHealth);
  loadDocuments();
}

document.querySelectorAll(".lang-btn").forEach((b) => b.addEventListener("click", async () => {
  if (b.dataset.lang === lang) return;
  lang = b.dataset.lang;
  try { localStorage.setItem("lang", lang); } catch { /* ignoré */ }
  applyLang();
  // Traduire la conversation : on repose les dernières questions dans la nouvelle langue.
  const previous = history.slice(-5);
  history.length = 0;
  document.querySelectorAll("#chat .msg:not(:first-child)").forEach((m) => m.remove());
  for (const q of previous) await ask(q);
}));

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || t("http_error", res.status));
  return data;
}

// ---- onglets
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
    if (btn.dataset.tab !== "ask") loadDocuments();
  })
);

// ---- statut
let lastHealth = null;
function renderWhatsApp(h) {
  const card = $("#wa-card");
  if (!h.whatsapp_number) { card.hidden = true; return; }
  const text = lang === "en" ? "help" : "aide";
  $("#wa-link").href = `https://wa.me/${h.whatsapp_number}?text=${text}`;
  $("#wa-number").textContent = "+" + h.whatsapp_number;
  $("#wa-qr").src = `/api/whatsapp/qr.svg?lang=${lang}`;
  card.hidden = false;
}

function renderTelegram(h) {
  const card = $("#tg-card");
  if (!h.telegram_username) { card.hidden = true; return; }
  $("#tg-link").href = `https://t.me/${h.telegram_username}`;
  $("#tg-username").textContent = "@" + h.telegram_username;
  $("#tg-qr").src = "/api/telegram/qr.svg";
  card.hidden = false;
}

function renderStatus(h) {
  renderWhatsApp(h);
  renderTelegram(h);
  const st = $("#status");
  st.textContent = t("status_ok", h) + (h.version ? ` · v${h.version}` : "");
  st.title = h.ephemeral_storage ? t("status_ephemeral") : "";
  st.classList.remove("err");
}

async function loadStatus(attempt = 0) {
  const st = $("#status");
  try {
    const res = await fetch("/api/health");
    const h = await res.json().catch(() => null);
    if (res.ok && h) {
      lastHealth = h;
      renderStatus(h);
      if (attempt > 0) loadDocuments();
      return;
    }
    if (h && h.detail) {  // erreur d'initialisation renvoyée par le serveur
      st.textContent = t("status_error") + h.detail;
      st.classList.add("err");
      return;
    }
    throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    // Démarrage à froid (serverless) : installation, téléchargement du modèle… on réessaie.
    if (attempt < 12) {
      st.textContent = t("status_starting", attempt + 1);
      setTimeout(() => loadStatus(attempt + 1), 5000);
    } else {
      st.textContent = t("status_down", e.message);
      st.classList.add("err");
    }
  }
}

// ---- Q/R
function addMsg(html, cls) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = html;
  $("#chat").appendChild(div);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return div;
}

function renderAnswer(r) {
  const sources = (r.sources || []).map((s) => `
    <div class="source">
      <div class="meta">[${s.ref}] <strong>${esc(s.source)}</strong>
        <span class="badge">${esc(typeLabel(s.doc_type))}</span>
        ${s.cited_author ? ` · ${esc(s.cited_author)}` : ""}
        ${s.cited_date ? ` · ${esc(s.cited_date)}` : ""}${s.timestamp ? ` ${esc(s.timestamp)}` : ""}
        · ${t("passage")} ${esc(s.chunk)}</div>
      <div class="excerpt">« ${esc(s.excerpt)} »</div>
    </div>`).join("");
  const mode = r.mode === "extractive" ? t("mode_extractive") :
               r.mode && r.mode.startsWith("llm") ? t("mode_llm", esc(r.mode.slice(4))) : "";
  return `${esc(r.answer).replace(/\n/g, "<br>")}${sources ? `<div class="sources">${sources}</div>` : ""}
    ${r.warning ? `<div class="mode err">${esc(r.warning)}</div>` : ""}<div class="mode">${mode}</div>`;
}

const history = [];

async function ask(question) {
  history.push(question);
  addMsg(esc(question), "user");
  const pending = addMsg(esc(t("searching")), "bot");
  const btn = $("#ask-form button");
  btn.disabled = true;
  try {
    const r = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, lang }) });
    pending.innerHTML = renderAnswer(r);
    if (!r.found && r.mode !== "chat") pending.classList.add("notfound");
  } catch (e) {
    pending.innerHTML = `<span class="err">${esc(e.message)}</span>`;
  } finally { btn.disabled = false; }
}

$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#question").value.trim();
  if (!q) return;
  $("#question").value = "";
  ask(q);
});

// ---- ingestion
$("#ingest-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  const big = [...$("#files").files].find((f) => f.size > 4 * 1024 * 1024);
  if (big) { $("#ingest-result").innerHTML = `<p class="err">${esc(t("too_large", big.name))}</p>`; return; }
  for (const f of $("#files").files) fd.append("files", f);
  fd.append("doc_type", $("#doc_type").value);
  fd.append("lang", lang);
  if ($("#author").value) fd.append("author", $("#author").value);
  if ($("#date").value) fd.append("date", $("#date").value);
  const btn = e.submitter; btn.disabled = true;
  $("#ingest-result").textContent = t("indexing");
  try {
    const r = await api("/api/ingest", { method: "POST", body: fd });
    $("#ingest-result").innerHTML = r.ingested.map((d) =>
      `<p class="ok">${esc(t("ingested", d, typeLabel(d.doc_type)))}</p>`).join("");
    $("#ingest-form").reset();
    loadDocuments(); loadStatus();
  } catch (err) {
    $("#ingest-result").innerHTML = `<p class="err">${esc(err.message)}</p>`;
  } finally { btn.disabled = false; }
});

async function loadDocuments() {
  let documents;
  try { ({ documents } = await api("/api/documents")); } catch { return; }
  $("#docs tbody").innerHTML = documents.map((d) => `
    <tr><td>${esc(d.source)}${d.title ? `<br><small>${esc(d.title)}</small>` : ""}</td>
      <td>${esc(typeLabel(d.doc_type))}</td>
      <td>${esc(d.date_start)}${d.date_end && d.date_end !== d.date_start ? " → " + esc(d.date_end) : ""}</td>
      <td>${esc(d.authors.slice(0, 4).join(", "))}${d.authors.length > 4 ? "…" : ""}</td>
      <td>${d.chunks}</td>
      <td><button data-del="${esc(d.source)}" title="${esc(t("delete_title"))}">${esc(t("delete"))}</button></td></tr>`).join("")
    || `<tr><td colspan="6">${esc(t("no_docs"))}</td></tr>`;
  document.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(t("confirm_delete", b.dataset.del))) return;
    await api(`/api/documents/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
    loadDocuments(); loadStatus();
  }));
  const selected = $("#summary-source").value;
  $("#summary-source").innerHTML = documents.map((d) =>
    `<option value="${esc(d.source)}">${esc(d.source)} (${esc(typeLabel(d.doc_type))})</option>`).join("");
  if (selected) $("#summary-source").value = selected;
}

// ---- résumé
$("#summary-btn").addEventListener("click", async () => {
  const source = $("#summary-source").value;
  if (!source) return;
  const btn = $("#summary-btn"); btn.disabled = true;
  $("#summary-result").textContent = t("analysing");
  try {
    const r = await api("/api/summarize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source, lang }) });
    const tasks = (r.tasks || []).map((x) => `<li>${esc(x.task)}${x.owner ? ` <span class="badge">${esc(x.owner)}</span>` : ""}${x.deadline ? ` <span class="badge">${esc(x.deadline)}</span>` : ""}</li>`).join("");
    $("#summary-result").innerHTML = `<div class="summary">
      ${r.participants?.length ? `<p class="hint">${esc(t("participants"))} : ${esc(r.participants.join(", "))}</p>` : ""}
      <h3>${esc(t("summary"))}</h3><p>${esc(r.summary)}</p>
      <h3>${esc(t("decisions"))} (${(r.decisions || []).length})</h3><ul>${(r.decisions || []).map((d) => `<li>${esc(d)}</li>`).join("") || `<li>${esc(t("no_decisions"))}</li>`}</ul>
      <h3>${esc(t("tasks"))} (${(r.tasks || []).length})</h3><ul>${tasks || `<li>${esc(t("no_tasks"))}</li>`}</ul>
      <p class="mode">${r.mode === "extractive" ? esc(t("summary_extractive")) : r.mode?.startsWith("llm:") ? t("mode_llm", esc(r.mode.slice(4))) : ""}</p></div>`;
  } catch (e) {
    $("#summary-result").innerHTML = `<p class="err">${esc(e.message)}</p>`;
  } finally { btn.disabled = false; }
});

applyLang();
loadStatus();
