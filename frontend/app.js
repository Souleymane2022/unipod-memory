const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const TYPE_LABEL = { chat: "Messages", transcript: "Réunion", document: "Document" };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Erreur HTTP ${res.status}`);
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

async function loadStatus() {
  try {
    const h = await api("/api/health");
    $("#status").textContent = `${h.chunks} passages indexés · LLM : ${h.llm}`;
  } catch { $("#status").textContent = "API injoignable"; }
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
        <span class="badge">${esc(TYPE_LABEL[s.doc_type] || s.doc_type)}</span>
        ${s.cited_author ? ` · ${esc(s.cited_author)}` : ""}
        ${s.cited_date ? ` · ${esc(s.cited_date)}` : ""}${s.timestamp ? ` ${esc(s.timestamp)}` : ""}
        · passage ${esc(s.chunk)}</div>
      <div class="excerpt">« ${esc(s.excerpt)} »</div>
    </div>`).join("");
  const mode = r.mode === "extractive" ? "Mode extractif (sans LLM) : citations directes des sources." :
               r.mode && r.mode.startsWith("llm") ? `Réponse rédigée par ${esc(r.mode.slice(4))} à partir des sources.` : "";
  return `${esc(r.answer).replace(/\n/g, "<br>")}${sources ? `<div class="sources">${sources}</div>` : ""}
    ${r.warning ? `<div class="mode err">${esc(r.warning)}</div>` : ""}<div class="mode">${mode}</div>`;
}

async function ask(question) {
  addMsg(esc(question), "user");
  const pending = addMsg("Recherche dans la mémoire du groupe…", "bot");
  const btn = $("#ask-form button");
  btn.disabled = true;
  try {
    const r = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
    pending.innerHTML = renderAnswer(r);
    if (!r.found) pending.classList.add("notfound");
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
document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => ask(c.textContent)));

// ---- ingestion
$("#ingest-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  for (const f of $("#files").files) fd.append("files", f);
  fd.append("doc_type", $("#doc_type").value);
  if ($("#author").value) fd.append("author", $("#author").value);
  if ($("#date").value) fd.append("date", $("#date").value);
  const btn = e.submitter; btn.disabled = true;
  $("#ingest-result").innerHTML = "Indexation en cours…";
  try {
    const r = await api("/api/ingest", { method: "POST", body: fd });
    $("#ingest-result").innerHTML = r.ingested.map((d) =>
      `<p class="ok">✔ ${esc(d.source)} — ${esc(TYPE_LABEL[d.doc_type])}, ${d.chunks} passage(s) (${d.chunk_words.join(", ")} mots)</p>`).join("");
    $("#ingest-form").reset();
    loadDocuments(); loadStatus();
  } catch (err) {
    $("#ingest-result").innerHTML = `<p class="err">${esc(err.message)}</p>`;
  } finally { btn.disabled = false; }
});

async function loadDocuments() {
  const { documents } = await api("/api/documents");
  $("#docs tbody").innerHTML = documents.map((d) => `
    <tr><td>${esc(d.source)}${d.title ? `<br><small>${esc(d.title)}</small>` : ""}</td>
      <td>${esc(TYPE_LABEL[d.doc_type] || d.doc_type)}</td>
      <td>${esc(d.date_start)}${d.date_end && d.date_end !== d.date_start ? " → " + esc(d.date_end) : ""}</td>
      <td>${esc(d.authors.slice(0, 4).join(", "))}${d.authors.length > 4 ? "…" : ""}</td>
      <td>${d.chunks}</td>
      <td><button data-del="${esc(d.source)}" title="Retirer de la mémoire">Supprimer</button></td></tr>`).join("")
    || `<tr><td colspan="6">Aucun document. Ajoutez-en ci-dessus.</td></tr>`;
  document.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Retirer ${b.dataset.del} de la mémoire ?`)) return;
    await api(`/api/documents/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
    loadDocuments(); loadStatus();
  }));
  $("#summary-source").innerHTML = documents.map((d) =>
    `<option value="${esc(d.source)}">${esc(d.source)} (${esc(TYPE_LABEL[d.doc_type] || d.doc_type)})</option>`).join("");
}

// ---- résumé
$("#summary-btn").addEventListener("click", async () => {
  const source = $("#summary-source").value;
  if (!source) return;
  const btn = $("#summary-btn"); btn.disabled = true;
  $("#summary-result").innerHTML = "Analyse en cours…";
  try {
    const r = await api("/api/summarize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source }) });
    const tasks = (r.tasks || []).map((t) => `<li>${esc(t.task)}${t.owner ? ` <span class="badge">${esc(t.owner)}</span>` : ""}${t.deadline ? ` <span class="badge">${esc(t.deadline)}</span>` : ""}</li>`).join("");
    $("#summary-result").innerHTML = `<div class="summary">
      ${r.participants?.length ? `<p class="hint">Participants : ${esc(r.participants.join(", "))}</p>` : ""}
      <h3>Résumé</h3><p>${esc(r.summary)}</p>
      <h3>Décisions (${(r.decisions || []).length})</h3><ul>${(r.decisions || []).map((d) => `<li>${esc(d)}</li>`).join("") || "<li>Aucune décision détectée.</li>"}</ul>
      <h3>Tâches / actions (${(r.tasks || []).length})</h3><ul>${tasks || "<li>Aucune tâche détectée.</li>"}</ul>
      <p class="mode">${r.mode === "extractive" ? "Mode extractif (heuristiques, sans LLM)." : esc(r.mode)}</p></div>`;
  } catch (e) {
    $("#summary-result").innerHTML = `<p class="err">${esc(e.message)}</p>`;
  } finally { btn.disabled = false; }
});

loadStatus();
loadDocuments();
